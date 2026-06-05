"""
FastAPI 백엔드 — STM32G4 Motor Drive Agent
엔드포인트:
  POST /v1/review        회로도 이미지(선택) + 프롬프트 → 리뷰 리포트
  GET  /v1/status        파이프라인 서비스 상태
  POST /v1/generate-ioc  핀 JSON → .ioc 파일 생성 (Step 2)
  GET  /v1/health        헬스체크
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import threading
from collections import deque

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent.step1_review_agent import ReviewAgent, ReviewReport, ReviewRequest
from agent.step3_codegen_agent import Step3Agent

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "stm32g4_docs")
PIN_AF_DB_PATH = os.getenv("PIN_AF_DB_PATH", "")
IOC_OUTPUT_DIR = Path(os.getenv("IOC_OUTPUT_DIR", "/tmp/ioc_outputs"))
IOC_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CODE_OUTPUT_DIR = Path(os.getenv("CODE_OUTPUT_DIR", "/tmp/code_outputs"))
CODE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REVIEW_RESULTS_DIR = Path(os.getenv("REVIEW_RESULTS_DIR", "/app/review_results"))
REVIEW_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# CubeMX CLI 탐색 경로 (우선순위 순)
CUBEMX_SEARCH_PATHS = [
    os.getenv("CUBEMX_PATH", ""),
    "/opt/STM32CubeMX/STM32CubeMX",
    "/usr/local/STM32CubeMX/STM32CubeMX",
    os.path.expanduser("~/STM32CubeMX/STM32CubeMX"),
    os.path.expanduser("~/stm32cubemx/STM32CubeMX"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# 인메모리 로그 버퍼 (최근 200줄)
_log_buffer: deque[str] = deque(maxlen=500)

class _BufHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _log_buffer.append(self.format(record))

_buf_h = _BufHandler()
_buf_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s", "%H:%M:%S"))
_buf_h.setLevel(logging.INFO)
logging.getLogger().addHandler(_buf_h)

# ---------------------------------------------------------------------------
# FastAPI 앱
# ---------------------------------------------------------------------------

app = FastAPI(
    title="STM32G4 Motor Drive Agent API",
    description="3-Step 파이프라인: 회로도 이미지 → 핀 검증 → CubeMX 자동화 → 알고리즘 통합",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_agent: Optional[ReviewAgent] = None
_step3_agent: Optional[Step3Agent] = None

# Step 3 job store: job_id → {status, progress, message, result}
_step3_jobs: Dict[str, Dict] = {}

# 현재 실행 중인 review 취소 플래그
_review_cancel_event = threading.Event()

# 단계별 중간 결과 저장소
_review_partial: Dict[str, Any] = {}


def _save_review_to_disk(partial: Dict[str, Any], chip: str, status: str = "completed") -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_chip = chip.replace("/", "_")
    fname = REVIEW_RESULTS_DIR / f"review_{ts}_{safe_chip}_{status}.json"
    payload = {**partial, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "chip": chip, "status": status}
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("결과 저장: %s", fname.name)
    return fname


def get_agent() -> ReviewAgent:
    global _agent
    if _agent is None:
        _agent = ReviewAgent(
            ollama_url=OLLAMA_URL,
            qdrant_url=QDRANT_URL,
            pin_af_db_path=PIN_AF_DB_PATH or None,
            collection=QDRANT_COLLECTION,
        )
    return _agent


def get_step3_agent() -> Step3Agent:
    global _step3_agent
    if _step3_agent is None:
        _step3_agent = Step3Agent(ollama_url=OLLAMA_URL)
    return _step3_agent


def _run_step3_job(job_id: str, vp: Dict[str, Any], prompt: str = "") -> None:
    """백그라운드 스레드에서 Step 3 파이프라인 실행."""
    def _cb(pct: int, msg: str) -> None:
        _step3_jobs[job_id].update({"progress": pct, "message": msg})

    _step3_jobs[job_id] = {"status": "running", "progress": 0, "message": "시작 중...", "result": None}
    try:
        result = get_step3_agent().run(vp, prompt=prompt, progress_cb=_cb)
        _step3_jobs[job_id].update({
            "status": "complete",
            "progress": 100,
            "message": "Step 3 완료",
            "result": result,
        })
    except Exception as e:
        logger.exception("Step 3 job 오류: %s", job_id)
        _step3_jobs[job_id].update({
            "status": "error",
            "progress": 0,
            "message": str(e),
            "result": None,
        })


# ---------------------------------------------------------------------------
# 요청/응답 로깅 미들웨어
# ---------------------------------------------------------------------------

_NO_LOG_PATHS = {"/v1/logs", "/v1/health", "/v1/review/partial"}

@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    if path in _NO_LOG_PATHS:
        return await call_next(request)
    start = time.time()
    rid = str(uuid.uuid4())[:8]
    logger.info("[%s] → %s %s", rid, request.method, path)
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    logger.info("[%s] ← %d (%.1f ms)", rid, response.status_code, elapsed)
    return response


# ---------------------------------------------------------------------------
# Pydantic schemas (API 전용)
# ---------------------------------------------------------------------------

class GenerateIocRequest(BaseModel):
    validated_pins: Dict[str, Any]


class GenerateIocResponse(BaseModel):
    ioc_filename: str
    download_url: str
    message: str


class StatusResponse(BaseModel):
    ollama: bool
    qdrant: bool
    backend: bool
    ollama_models: List[str]
    qdrant_collections: List[str]


class ChatRequest(BaseModel):
    chip: str
    question: str
    history: List[Dict[str, str]] = []
    report_context: Dict[str, Any] = {}


class ChatResponse(BaseModel):
    answer: str
    sources: List[str] = []


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------

@app.get("/v1/health", tags=["System"])
async def health():
    return {"status": "ok", "service": "stm32g4-agent-backend"}


@app.get("/v1/logs", tags=["System"])
async def get_logs(n: int = 50):
    return {"logs": list(_log_buffer)[-n:]}


@app.post("/v1/review/cancel", tags=["Step 1"])
async def cancel_review():
    _review_cancel_event.set()
    logger.info("검증 취소 요청 수신")
    if _review_partial:
        chip = _review_partial.get("chip", "unknown")
        _save_review_to_disk(_review_partial, chip, status="cancelled")
    return {"status": "cancel_requested"}


@app.get("/v1/review/partial", tags=["Step 1"])
async def get_partial_results():
    return _review_partial


# ---------------------------------------------------------------------------
# GET /v1/status
# ---------------------------------------------------------------------------

@app.get("/v1/status", response_model=StatusResponse, tags=["System"])
async def get_status():
    ollama_ok = False
    ollama_models: List[str] = []
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            ollama_ok = True
            ollama_models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    qdrant_ok = False
    qdrant_collections: List[str] = []
    try:
        r = requests.get(f"{QDRANT_URL}/collections", timeout=5)
        if r.status_code == 200:
            qdrant_ok = True
            qdrant_collections = [
                c["name"] for c in r.json().get("result", {}).get("collections", [])
            ]
    except Exception:
        pass

    return StatusResponse(
        ollama=ollama_ok,
        qdrant=qdrant_ok,
        backend=True,
        ollama_models=ollama_models,
        qdrant_collections=qdrant_collections,
    )


# ---------------------------------------------------------------------------
# POST /v1/review — Step 1: Vision + 핀 검증
# ---------------------------------------------------------------------------

@app.post("/v1/extract-pinmap", tags=["Step 1"])
def extract_pinmap(
    prompt: str = Form("", description="자연어 요구사항 프롬프트 (Vision 보강용, 선택)"),
    chip: str = Form("", description="칩 힌트 (비우면 회로도에서 자동 감지)"),
    schematic_images: List[UploadFile] = File(
        default=[],
        description="회로도 이미지 (여러 장 = 한 설계의 멀티 시트).",
    ),
):
    """① Vision 핀맵 추출만 수행 (Rule/RAG/LLM 안 함).

    사용자가 추출 결과(칩 + 핀맵 CSV)를 검토·수정한 뒤 /v1/review로 확정 검증하기 위한 단계.
    Vision OCR 오인식을 사용자가 바로잡을 수 있게 함.
    반환: {chip, pinmap_csv, vision_analysis}
    """
    images_b64: List[str] = []
    for f in (schematic_images or []):
        if f is None:
            continue
        b = f.file.read()
        if b:
            images_b64.append(base64.b64encode(b).decode("utf-8"))
            logger.info("이미지 수신(extract): %s (%.1f KB)", f.filename, len(b) / 1024)
    if not images_b64:
        raise HTTPException(status_code=422, detail="schematic_images(회로도 이미지)가 필요합니다.")

    req = ReviewRequest(
        chip=chip,
        prompt=prompt or "회로도에서 핀맵을 추출하세요.",
        schematic_images_b64=images_b64,
    )
    _review_cancel_event.clear()
    _review_partial.clear()
    agent = get_agent()
    agent.cancel_event = _review_cancel_event
    agent.partial = _review_partial
    try:
        result = agent.extract_pinmap_only(req)
    except Exception as e:
        logger.exception("extract_pinmap_only 오류")
        raise HTTPException(status_code=500, detail=f"Vision 추출 오류: {e}")
    return result


@app.post("/v1/review", response_model=ReviewReport, tags=["Step 1"])
def review(
    chip: str = Form(..., description="예: STM32G474RET6"),
    prompt: str = Form(..., description="자연어 요구사항 프롬프트"),
    schematic_images: List[UploadFile] = File(
        default=[],
        description="회로도 이미지 (JPEG/PNG, 여러 장 가능 = 한 설계의 멀티 시트). Gemma 4 Vision이 종합해 핀맵 자동 추출.",
    ),
    schematic_image: Optional[UploadFile] = File(
        None,
        description="(하위 호환) 회로도 이미지 1장. schematic_images 사용 권장.",
    ),
    csv_file: Optional[UploadFile] = File(None, description="핀맵 CSV (선택 — 이미지가 없을 때 필수)"),
    pinmap_csv: Optional[str] = Form(None, description="CSV 문자열 직접 입력 (선택)"),
    vision_analysis: Optional[str] = Form(None, description="확정 단계에서 전달하는 기존 Vision 분석 (있으면 Vision 재실행 생략)"),
    peripherals: Optional[str] = Form(None, description="외부 부품/연결 설명 (확정 단계에서 전달, LLM 페리페럴 검토용)"),
    mode: Literal["fast", "full"] = Form("full", description="fast: Rule Engine만 (CI용). full: 전체 LLM 리뷰 (기본)."),
):
    """
    회로도 이미지 + 자연어 프롬프트 → 검증 리포트.

    입력 우선순위:
      1. schematic_image → Gemma 4 31B Vision이 핀맵 자동 추출
      2. csv_file 또는 pinmap_csv → 직접 입력 CSV 사용

    mode:
      - fast: Rule Engine만 실행. errors > 0 이면 HTTP 403.
      - full: Vision + Rule Engine + RAG + LLM. errors 있어도 HTTP 200 (LLM 설명 포함).
    """
    # 이미지 → base64 목록 (schematic_images 우선, 없으면 하위호환 schematic_image)
    uploads = [f for f in (schematic_images or []) if f is not None]
    if not uploads and schematic_image is not None:
        uploads = [schematic_image]

    images_b64: List[str] = []
    for f in uploads:
        image_bytes = f.file.read()
        if not image_bytes:
            continue
        images_b64.append(base64.b64encode(image_bytes).decode("utf-8"))
        logger.info("이미지 수신: %s (%.1f KB)", f.filename, len(image_bytes) / 1024)
    if len(images_b64) > 1:
        logger.info("회로도 %d장 수신 — 한 설계로 종합 분석", len(images_b64))

    # CSV 소스 결정
    csv_text = ""
    if csv_file is not None:
        raw_bytes = csv_file.file.read()
        csv_text = raw_bytes.decode("utf-8-sig")
    elif pinmap_csv:
        csv_text = pinmap_csv

    # 이미지도 CSV도 없으면 거부
    if not images_b64 and not csv_text.strip():
        raise HTTPException(
            status_code=422,
            detail="schematic_images(회로도 이미지) 또는 csv_file/pinmap_csv 중 하나를 제공해야 합니다.",
        )

    req = ReviewRequest(
        chip=chip,
        pinmap_csv=csv_text,
        prompt=prompt,
        schematic_images_b64=images_b64,
        vision_analysis=vision_analysis or "",
        peripherals=peripherals or "",
        mode=mode,
    )

    _review_cancel_event.clear()
    _review_partial.clear()
    agent = get_agent()
    agent.cancel_event = _review_cancel_event
    agent.partial = _review_partial

    try:
        report = get_agent().run(req)
    except Exception as e:
        if _review_partial:
            _save_review_to_disk(_review_partial, chip, status="error")
        if _review_cancel_event.is_set():
            logger.info("검증 취소됨")
            raise HTTPException(status_code=499, detail="검증이 취소되었습니다.")
        logger.exception("ReviewAgent.run() 오류")
        raise HTTPException(status_code=500, detail=f"검증 에이전트 오류: {e}")

    # fast 모드: errors 있으면 403 (CI 게이트)
    # full 모드: errors 있어도 200 (LLM 자연어 설명 포함)
    _save_review_to_disk(_review_partial, chip, status="completed")

    if report.errors and mode == "fast":
        return JSONResponse(
            status_code=403,
            content={
                "detail": "핀 검증 실패 — 회로도 수정 후 재시도하세요.",
                "report": report.model_dump(),
            },
        )

    return report


# ---------------------------------------------------------------------------
# POST /v1/generate-ioc — Step 2 .ioc 파일 생성
# ---------------------------------------------------------------------------

@app.post("/v1/generate-ioc", response_model=GenerateIocResponse, tags=["Step 2"])
async def generate_ioc(request: GenerateIocRequest):
    """확정 핀 JSON → STM32CubeMX .ioc 파일 생성."""
    vp = request.validated_pins
    chip = vp.get("chip", "STM32G474RETx")
    pins: List[Dict[str, Any]] = vp.get("pins", [])

    ioc_lines = _build_ioc_content(chip, vp, pins)
    ioc_text = "\n".join(ioc_lines)

    filename = f"{chip}_{uuid.uuid4().hex[:8]}.ioc"
    out_path = IOC_OUTPUT_DIR / filename
    out_path.write_text(ioc_text, encoding="utf-8")

    logger.info("IOC 파일 생성: %s (%d 핀)", filename, len(pins))

    # 미검증 서브패밀리 경고 (플래시 그룹이 실제 .ioc로 미확인된 경우)
    msg = f".ioc 파일 생성 완료 ({len(pins)}핀)"
    ident = _chip_identity(chip)
    if ident.get("verified") != "1":
        msg += (
            f" ⚠️ {ident['name']} 식별자는 미검증입니다(검증: G431/G474). "
            "CubeMX 로드 실패 시 실제 .ioc의 Mcu.Name/CPN으로 확인이 필요합니다."
        )

    return GenerateIocResponse(
        ioc_filename=filename,
        download_url=f"/v1/download-ioc/{filename}",
        message=msg,
    )


@app.get("/v1/download-ioc/{filename}", tags=["Step 2"])
async def download_ioc(filename: str):
    path = IOC_OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="파일이 존재하지 않습니다.")
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/octet-stream",
    )


# 핀별 function 옵션 — scripts/parse_cubemx_db.py 가 CubeMX DB에서 파생한 파일.
# (agent/ 는 컨테이너에 복사·마운트되므로 백엔드에서 읽을 수 있다.)
_PIN_OPTIONS_PATH = Path(__file__).resolve().parent.parent / "agent" / "pin_function_options.json"
_pin_options_cache: Optional[Dict[str, Any]] = None


def _load_pin_options() -> Dict[str, Any]:
    global _pin_options_cache
    if _pin_options_cache is None:
        try:
            _pin_options_cache = json.loads(_PIN_OPTIONS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("pin_function_options.json 로드 실패: %s", e)
            _pin_options_cache = {}
    return _pin_options_cache


@app.get("/v1/pin-options/{chip}", tags=["Step 2"])
async def pin_options(chip: str):
    """칩 부품번호 → 핀별 선택 가능한 CubeMX 신호 목록(드롭다운 소스).

    chip은 _chip_identity()로 Mcu.Name(=CubeMX DB 키)으로 정규화해 조회한다.
    """
    ident = _chip_identity(chip)
    entry = _load_pin_options().get(ident["name"], {})
    return {
        "chip": chip,
        "mcu_name": ident["name"],
        "package": entry.get("package"),
        "found": bool(entry),
        "pins": entry.get("pins", {}),
        # GPIO/아날로그는 Signal이 아니라 Mode로 설정되지만, 드롭다운 편의를 위해 제공
        "common": ["GPIO_Output", "GPIO_Input", "GPIO_Analog"],
    }


# ---------------------------------------------------------------------------
# CubeMX CLI 유틸리티
# ---------------------------------------------------------------------------

def _find_cubemx() -> Optional[str]:
    """시스템에서 STM32CubeMX 실행 파일 탐색."""
    for path in CUBEMX_SEARCH_PATHS:
        if path and Path(path).exists():
            return path
    result = subprocess.run(["which", "STM32CubeMX"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _run_cubemx_headless(ioc_path: Path, output_dir: Path) -> Tuple[bool, str]:
    """CubeMX CLI를 headless 모드로 실행해 .ioc → HAL 코드 생성.

    Returns:
        (success, message)
    """
    cubemx = _find_cubemx()
    if not cubemx:
        return False, "STM32CubeMX가 설치되지 않았습니다."

    output_dir.mkdir(parents=True, exist_ok=True)

    # CubeMX headless 스크립트
    script = (
        f"loadbmproject {ioc_path.resolve()}\n"
        f"setproperty project.output {output_dir.resolve()}\n"
        "generatecode\n"
        "exit\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".script", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            [cubemx, "-q", script_path],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            return True, "코드 생성 완료"
        return False, f"CubeMX 오류: {result.stderr[:300]}"
    except subprocess.TimeoutExpired:
        return False, "CubeMX 실행 시간 초과 (180초)"
    except Exception as e:
        return False, f"CubeMX 실행 실패: {e}"
    finally:
        os.unlink(script_path)


def _zip_directory(src_dir: Path, zip_name: str) -> Path:
    """디렉토리를 ZIP으로 묶어 CODE_OUTPUT_DIR에 저장."""
    zip_path = CODE_OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in src_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(src_dir.parent))
    return zip_path


# ---------------------------------------------------------------------------
# POST /v1/generate-code — .ioc → CubeMX CLI → HAL 코드 ZIP
# ---------------------------------------------------------------------------

@app.post("/v1/generate-code", tags=["Step 2"])
async def generate_code(request: GenerateIocRequest):
    """validated_pins → .ioc 생성 → CubeMX CLI → HAL 코드 ZIP 반환.

    - CubeMX 미설치: 503 + {status: cubemx_not_found}
    - CubeMX 설치됨: ZIP FileResponse
    """
    vp = request.validated_pins
    chip = vp.get("chip", "STM32G474RETx")
    pins: List[Dict[str, Any]] = vp.get("pins", [])

    # CubeMX 존재 여부 먼저 확인
    cubemx_path = _find_cubemx()
    if not cubemx_path:
        return JSONResponse(
            status_code=503,
            content={
                "status": "cubemx_not_found",
                "message": (
                    "STM32CubeMX가 설치되지 않았습니다.\n"
                    "설치 후 환경변수 CUBEMX_PATH에 실행 파일 경로를 지정하거나 "
                    "/opt/STM32CubeMX/에 설치해주세요."
                ),
                "cubemx_path": None,
            },
        )

    # .ioc 파일 생성
    ioc_lines = _build_ioc_content(chip, vp, pins)
    ioc_text = "\n".join(ioc_lines)
    run_id = uuid.uuid4().hex[:8]
    ioc_filename = f"{chip}_{run_id}.ioc"
    ioc_path = IOC_OUTPUT_DIR / ioc_filename
    ioc_path.write_text(ioc_text, encoding="utf-8")

    # CubeMX headless 실행
    output_dir = CODE_OUTPUT_DIR / f"{chip}_{run_id}"
    success, message = _run_cubemx_headless(ioc_path, output_dir)
    if not success:
        raise HTTPException(status_code=500, detail=message)

    # 생성된 코드 ZIP
    zip_name = f"{chip}_MotorDrive_{run_id}.zip"
    zip_path = _zip_directory(output_dir, zip_name)

    # 임시 디렉토리 정리
    shutil.rmtree(output_dir, ignore_errors=True)

    logger.info("코드 ZIP 생성: %s", zip_name)
    return FileResponse(
        path=str(zip_path),
        filename=zip_name,
        media_type="application/zip",
    )


@app.get("/v1/cubemx-status", tags=["Step 2"])
async def cubemx_status():
    """CubeMX 설치 여부 확인."""
    path = _find_cubemx()
    return {"installed": path is not None, "path": path}


# ---------------------------------------------------------------------------
# Step 3 — Golden Module 적응 코드젠
# ---------------------------------------------------------------------------

class Step3Request(BaseModel):
    validated_pins: Dict[str, Any]
    prompt: str = ""


@app.post("/v1/generate-step3", tags=["Step 3"])
async def generate_step3(request: Step3Request):
    """Golden Module 선택 → LLM 적응 → 결과 반환 (백그라운드 job).
    반환: {job_id, selected_modules}
    """
    from agent.step3_codegen_agent import select_modules
    vp = request.validated_pins
    job_id = uuid.uuid4().hex[:12]
    selected = select_modules(vp)
    _step3_jobs[job_id] = {
        "status": "pending",
        "progress": 0,
        "message": f"대기 중 — 선택 모듈: {', '.join(selected)}",
        "result": None,
    }
    t = threading.Thread(target=_run_step3_job, args=(job_id, vp, request.prompt), daemon=True)
    t.start()
    return {"job_id": job_id, "selected_modules": selected}


@app.get("/v1/step3-status/{job_id}", tags=["Step 3"])
async def step3_status(job_id: str):
    """Step 3 job 진행 상태 조회."""
    if job_id not in _step3_jobs:
        raise HTTPException(status_code=404, detail="job_id를 찾을 수 없습니다.")
    job = _step3_jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "result": job["result"] if job["status"] == "complete" else None,
    }


# ---------------------------------------------------------------------------
# POST /v1/chat — Step 1 결과 기반 멀티턴 채팅
# ---------------------------------------------------------------------------

@app.post("/v1/chat", response_model=ChatResponse, tags=["Step 1"])
def chat(request: ChatRequest):
    """검증 결과 컨텍스트 + RAG + 대화 이력 → 전문가 답변."""
    try:
        result = get_agent().chat(
            chip=request.chip,
            question=request.question,
            history=request.history,
            report_context=request.report_context,
        )
        return ChatResponse(**result)
    except Exception as e:
        logger.exception("chat() 오류")
        raise HTTPException(status_code=500, detail=f"채팅 에이전트 오류: {e}")


# 칩별 식별 문자열 — STM32CubeMX가 MCU DB를 조회하는 키. 잘못되면 로드시
# NumberFormatException(parseInt(""))으로 .ioc가 안 열린다. 식별자(Mcu.Name)는
# 설치된 DB의 mcu xml 파일명과 정확히 일치해야 하고, MxDb.Version도 그 툴에 맞춰야 한다.
#
# 칩별 하드코딩 금지: 부품번호(STM32G4 + 서브2 + 핀 + 플래시 + 패키지 + 온도)를 규칙으로
# 디코드한다. Mcu.Name의 플래시 그룹(괄호)만 서브패밀리당 상수라 표로 둔다(ST 카탈로그 사실).
# 예) STM32G431RBT6 → Name=STM32G431R(6-8-B)Tx, STM32G474CET6 → STM32G474C(B-C-E)Tx.
G4_FLASH_GROUP: Dict[str, str] = {
    "STM32G431": "6-8-B", "STM32G441": "B",
    "STM32G473": "8-B-C-E", "STM32G474": "B-C-E",
    "STM32G483": "C-E", "STM32G484": "C-E",
    "STM32G491": "C-E", "STM32G4A1": "C-E",
}
# 핀수 문자 / 패키지 문자 (STM32 부품번호 표준 표기)
G4_PIN_COUNT: Dict[str, str] = {"K": "32", "C": "48", "R": "64", "M": "80", "Q": "80", "V": "100"}
G4_PKG_TYPE: Dict[str, str] = {
    "T": "LQFP", "U": "UFQFPN", "I": "UFBGA", "H": "TFBGA", "Y": "WLCSP", "P": "TSSOP",
}
# 플래시 그룹이 실제 .ioc로 검증된 서브패밀리 (그 외는 응답에 ⚠️ 미검증 경고)
_VERIFIED_SUBFAMILY = {"STM32G431", "STM32G474"}
# 부품번호가 불완전(서브패밀리만 입력)할 때의 기본 변종
G4_DEFAULT_PART: Dict[str, str] = {
    "STM32G431": "STM32G431RBT6", "STM32G441": "STM32G441CBT6",
    "STM32G473": "STM32G473RET6", "STM32G474": "STM32G474RET6",
    "STM32G483": "STM32G483RET6", "STM32G484": "STM32G484RET6",
    "STM32G491": "STM32G491RET6", "STM32G4A1": "STM32G4A1RET6",
}


def _chip_identity(chip: str) -> Dict[str, str]:
    """부품번호 → CubeMX 식별자(cpn/name/user/pkg/verified). 하드코딩 테이블 없이 디코드.

    서브패밀리만 주어지면(예: 'STM32G474') 기본 변종으로 보완. 디코드 불가 시 G474 기본.
    """
    raw = re.sub(r"[^A-Z0-9]", "", chip.upper().strip())
    sub = raw[:9] if raw.startswith("STM32G4") and len(raw) >= 9 else ""
    # 핀/플래시/패키지 문자가 없으면(서브패밀리만) 기본 변종으로 치환
    m = re.match(r"(STM32G4[0-9A-Z]{2})([KCRMQV])([0-9BCE])([TUIHYP])([0-9A-Z]?)", raw)
    if not m:
        raw = G4_DEFAULT_PART.get(sub, "STM32G474RET6")
        m = re.match(r"(STM32G4[0-9A-Z]{2})([KCRMQV])([0-9BCE])([TUIHYP])([0-9A-Z]?)", raw)
    sub, pin, flash, pkg, temp = m.groups()
    group = G4_FLASH_GROUP.get(sub, flash)  # 미등록 서브패밀리는 단일 플래시 코드로 폴백
    return {
        "cpn": f"{sub}{pin}{flash}{pkg}{temp or '6'}",
        "name": f"{sub}{pin}({group}){pkg}x",
        "user": f"{sub}{pin}{flash}{pkg}x",
        "pkg": G4_PKG_TYPE.get(pkg, "LQFP") + G4_PIN_COUNT.get(pin, "64"),
        "verified": "1" if (sub in _VERIFIED_SUBFAMILY and sub in G4_FLASH_GROUP) else "0",
    }

# 칩 독립적인 깡통 스켈레톤 — dataset의 실제 CubeMX 출력
# (NUCLEO-G431RB/GPIO_IOToggle.ioc)에서 식별·핀열거·IP목록만 제거하고 그대로 보존.
# NVIC/RCC(170MHz 클럭트리)/ProjectManager/SYS는 검증된 값이라 손대지 않는다.
_STATIC_IOC = """\
#MicroXplorer Configuration settings - do not modify
CAD.formats=
CAD.pinconfig=
CAD.provider=
File.Version=6
KeepUserPlacement=true
Mcu.Family=STM32G4
Mcu.ThirdPartyNb=0
Mcu.UserConstants=
MxCube.Version=6.17.0
MxDb.Version=DB.6.0.170
NVIC.BusFault_IRQn=true\\:0\\:0\\:false\\:false\\:false\\:true\\:false\\:false
NVIC.DebugMonitor_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:false\\:false
NVIC.ForceEnableDMAVector=true
NVIC.HardFault_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:false\\:false
NVIC.MemoryManagement_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:false\\:false
NVIC.NonMaskableInt_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:false\\:false
NVIC.PendSV_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:false\\:false
NVIC.PriorityGroup=NVIC_PRIORITYGROUP_4
NVIC.SVCall_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:false\\:false
NVIC.SysTick_IRQn=true\\:0\\:0\\:true\\:false\\:true\\:true\\:true\\:false
NVIC.UsageFault_IRQn=true\\:0\\:0\\:false\\:false\\:false\\:true\\:false\\:false
PinOutPanel.RotationAngle=0
ProjectManager.AskForMigrate=true
ProjectManager.BackupPrevious=false
ProjectManager.CompilerOptimize=6
ProjectManager.ComputerToolchain=false
ProjectManager.CoupleFile=false
ProjectManager.DeletePrevious=true
ProjectManager.FreePins=false
ProjectManager.HalAssertFull=false
ProjectManager.HeapSize=0x200
ProjectManager.KeepUserCode=true
ProjectManager.LastFirmware=true
ProjectManager.LibraryCopy=2
ProjectManager.MainLocation=Src
ProjectManager.NoMain=false
ProjectManager.PreviousToolchain=
ProjectManager.ProjectBuild=false
ProjectManager.RegisterCallBack=
ProjectManager.StackSize=0x400
ProjectManager.TargetToolchain=STM32CubeIDE
ProjectManager.ToolChainLocation=
ProjectManager.UAScriptAfterPath=
ProjectManager.UAScriptBeforePath=
ProjectManager.UnderRoot=true
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false
RCC.ADC12Freq_Value=170000000
RCC.AHBFreq_Value=170000000
RCC.APB1Freq_Value=170000000
RCC.APB1TimFreq_Value=170000000
RCC.APB2Freq_Value=170000000
RCC.APB2TimFreq_Value=170000000
RCC.CRSFreq_Value=48000000
RCC.CortexFreq_Value=170000000
RCC.EXTERNAL_CLOCK_VALUE=12288000
RCC.FCLKCortexFreq_Value=170000000
RCC.FDCANFreq_Value=170000000
RCC.FamilyName=M
RCC.HCLKFreq_Value=170000000
RCC.HSE_VALUE=24000000
RCC.HSI48_VALUE=48000000
RCC.HSI_VALUE=16000000
RCC.I2C1Freq_Value=170000000
RCC.I2C2Freq_Value=170000000
RCC.I2C3Freq_Value=170000000
RCC.I2SFreq_Value=170000000
RCC.IPParameters=ADC12Freq_Value,AHBFreq_Value,APB1Freq_Value,APB1TimFreq_Value,APB2Freq_Value,APB2TimFreq_Value,CRSFreq_Value,CortexFreq_Value,EXTERNAL_CLOCK_VALUE,FCLKCortexFreq_Value,FDCANFreq_Value,FamilyName,HCLKFreq_Value,HSE_VALUE,HSI48_VALUE,HSI_VALUE,I2C1Freq_Value,I2C2Freq_Value,I2C3Freq_Value,I2SFreq_Value,LPTIM1Freq_Value,LPUART1Freq_Value,LSCOPinFreq_Value,LSE_VALUE,LSI_VALUE,MCO1PinFreq_Value,PLLM,PLLN,PLLPoutputFreq_Value,PLLQoutputFreq_Value,PLLRCLKFreq_Value,PWRFreq_Value,RNGFreq_Value,SAI1Freq_Value,SYSCLKFreq_VALUE,SYSCLKSource,UART4Freq_Value,USART1Freq_Value,USART2Freq_Value,USART3Freq_Value,USBFreq_Value,VCOInputFreq_Value,VCOOutputFreq_Value
RCC.LPTIM1Freq_Value=170000000
RCC.LPUART1Freq_Value=170000000
RCC.LSCOPinFreq_Value=32000
RCC.LSE_VALUE=32768
RCC.LSI_VALUE=32000
RCC.MCO1PinFreq_Value=16000000
RCC.PLLM=RCC_PLLM_DIV4
RCC.PLLN=85
RCC.PLLPoutputFreq_Value=170000000
RCC.PLLQoutputFreq_Value=170000000
RCC.PLLRCLKFreq_Value=170000000
RCC.PWRFreq_Value=170000000
RCC.RNGFreq_Value=170000000
RCC.SAI1Freq_Value=170000000
RCC.SYSCLKFreq_VALUE=170000000
RCC.SYSCLKSource=RCC_SYSCLKSOURCE_PLLCLK
RCC.UART4Freq_Value=170000000
RCC.USART1Freq_Value=170000000
RCC.USART2Freq_Value=170000000
RCC.USART3Freq_Value=170000000
RCC.USBFreq_Value=170000000
RCC.VCOInputFreq_Value=4000000
RCC.VCOOutputFreq_Value=340000000
VP_SYS_VS_DBSignals.Mode=DisableDeadBatterySignals
VP_SYS_VS_DBSignals.Signal=SYS_VS_DBSignals
VP_SYS_VS_Systick.Mode=SysTick
VP_SYS_VS_Systick.Signal=SYS_VS_Systick
board=custom
"""


def _peripheral_of(func: str) -> Optional[str]:
    """신호 함수명에서 주변장치 인스턴스 추출. 예: TIM1_CH1→TIM1, FDCAN1_TX→FDCAN1,
    SPI1_SCK→SPI1, ADC1_IN1→ADC1. 매칭 안 되면(GPIO 등) None."""
    m = re.match(
        r"^(TIM\d+|FDCAN\d+|SPI\d+|I2C\d+|USART\d+|UART\d+|LPUART\d+|"
        r"ADC\d+|DAC\d+|OPAMP\d+|COMP\d+)",
        func.upper(),
    )
    return m.group(1) if m else None


def _build_ioc_content(
    chip: str,
    vp: Dict[str, Any],
    pins: List[Dict[str, Any]],
) -> List[str]:
    """검증된 깡통 .ioc 템플릿에 핀맵을 주입해 유효한 STM32CubeMX .ioc 생성.

    설계(work/step2_workflow/01_pinmap_to_ioc.md): 처음부터 만들지 않고 실제 CubeMX
    출력 기반 스켈레톤(_STATIC_IOC)을 로드한 뒤 식별/핀열거/IP목록/신호만 주입한다.
    핸드롤 방식은 Mcu.PinsNb·Mcu.Name 등 필수 필드 누락으로 CubeMX 로드가 깨졌다.
    """
    ident = _chip_identity(chip)

    # 1) 스켈레톤을 key→value 딕셔너리로 로드 (verbatim 보존)
    props: Dict[str, str] = {}
    for ln in _STATIC_IOC.splitlines():
        if ln.startswith("#") or "=" not in ln:
            props.setdefault("__header__", ln)  # 첫 줄 주석 보존
            continue
        k, v = ln.split("=", 1)
        props[k] = v

    # 2) 칩 식별 필드 주입
    proj_name = f"{ident['user']}_MotorDrive"
    props["Mcu.CPN"] = ident["cpn"]
    props["Mcu.Name"] = ident["name"]
    props["Mcu.Package"] = ident["pkg"]
    props["Mcu.UserName"] = ident["user"]
    props["ProjectManager.DeviceId"] = ident["user"]
    props["ProjectManager.ProjectName"] = proj_name
    props["ProjectManager.ProjectFileName"] = f"{proj_name}.ioc"

    # HSE(크리스털) 주파수 반영
    crystal_hz = int(vp.get("crystal_mhz", 24)) * 1_000_000
    props["RCC.HSE_VALUE"] = str(crystal_hz)

    # 3) 핀 신호 주입 + Mcu.Pin 열거 재구성
    #    VP_SYS 가상핀(Systick/DBSignals)을 항상 먼저 둔다 (스켈레톤이 보유).
    mcu_pins: List[str] = ["VP_SYS_VS_Systick", "VP_SYS_VS_DBSignals"]
    used_ips: set = set()
    seen_pins: set = set()
    for p in pins:
        pin = str(p.get("pin", "")).strip()
        func = str(p.get("function", "")).strip()
        label = str(p.get("label", "")).strip()
        if not pin or pin in seen_pins:
            continue
        seen_pins.add(pin)
        if func and func.upper() not in ("NAN", "NONE", "GPIO"):
            props[f"{pin}.Signal"] = func
            props[f"{pin}.Locked"] = "true"
            ip = _peripheral_of(func)
            if ip:
                used_ips.add(ip)
        if label:
            props[f"{pin}.GPIO_Label"] = label
        mcu_pins.append(pin)

    for i, mp in enumerate(mcu_pins):
        props[f"Mcu.Pin{i}"] = mp
    props["Mcu.PinsNb"] = str(len(mcu_pins))

    # 4) IP 목록 재구성 — 기본 3종(NVIC/RCC/SYS) + 핀에서 추출한 주변장치
    ip_list = ["NVIC", "RCC", "SYS"] + sorted(used_ips)
    for i, ip in enumerate(ip_list):
        props[f"Mcu.IP{i}"] = ip
    props["Mcu.IPNb"] = str(len(ip_list))

    # 5) 주변장치 최소 설정 (모드/채널) — 핀이 실제로 배정된 경우에만
    deadtime_ns = vp.get("deadtime_ns", 500)
    if "TIM1" in used_ips:
        props["TIM1.IPParameters"] = (
            "Channel-PWM Generation1 CH1,Channel-PWM Generation2 CH2,"
            "Channel-PWM Generation3 CH3,CounterMode,DeadTime,RepetitionCounter"
        )
        props["TIM1.Channel-PWM Generation1 CH1"] = "TIM_CHANNEL_1"
        props["TIM1.Channel-PWM Generation2 CH2"] = "TIM_CHANNEL_2"
        props["TIM1.Channel-PWM Generation3 CH3"] = "TIM_CHANNEL_3"
        props["TIM1.CounterMode"] = "TIM_COUNTERMODE_CENTERALIGNED1"
        props["TIM1.DeadTime"] = str(_ns_to_deadtime_reg(deadtime_ns))
        props["TIM1.RepetitionCounter"] = "1"
    if "FDCAN1" in used_ips:
        fdcan_baud = vp.get("fdcan_baudrate", 1000000)
        props["FDCAN1.IPParameters"] = "FrameFormat,NominalBaudRate,NominalSamplePoint"
        props["FDCAN1.FrameFormat"] = "FDCAN_FRAME_CLASSIC"
        props["FDCAN1.NominalBaudRate"] = str(fdcan_baud)
        props["FDCAN1.NominalSamplePoint"] = "87.5"
    if "SPI1" in used_ips:
        props["SPI1.IPParameters"] = "Mode,Direction,DataSize,CLKPolarity,CLKPhase"
        props["SPI1.Mode"] = "SPI_MODE_MASTER"
        props["SPI1.Direction"] = "SPI_DIRECTION_2LINES"
        props["SPI1.DataSize"] = "SPI_DATASIZE_8BIT"
        props["SPI1.CLKPolarity"] = "SPI_POLARITY_LOW"
        props["SPI1.CLKPhase"] = "SPI_PHASE_1EDGE"

    # 6) 직렬화 — CubeMX 스타일(헤더 주석 + 키 알파벳 정렬)
    header = props.pop("__header__", "#MicroXplorer Configuration settings - do not modify")
    lines = [header]
    for k in sorted(props.keys()):
        lines.append(f"{k}={props[k]}")
    return lines


def _ns_to_deadtime_reg(ns: int) -> int:
    """데드타임 ns → TIM1 DTG 레지스터 근사값 (170MHz 기준)."""
    step_ns = 1e9 / 170_000_000
    return min(int(ns / step_ns), 127)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
