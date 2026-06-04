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

    return GenerateIocResponse(
        ioc_filename=filename,
        download_url=f"/v1/download-ioc/{filename}",
        message=f".ioc 파일 생성 완료 ({len(pins)}핀)",
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


def _build_ioc_content(
    chip: str,
    vp: Dict[str, Any],
    pins: List[Dict[str, Any]],
) -> List[str]:
    """STM32CubeMX .ioc 포맷 생성."""
    lines = [
        f"# STM32G4 Motor Drive Agent — 자동 생성 .ioc",
        f"# chip: {chip}",
        "",
        f"Mcu.Family=STM32G4",
        f"Mcu.Name={chip.replace('STM32', 'STM32')}",
        f"Mcu.Package=LQFP64",
        f"ProjectManager.ProjectName={chip}_MotorDrive",
        f"ProjectManager.LibraryCopySrc=1",
        f"ProjectManager.ProjectBuildStruct=",
        f"ProjectManager.CodeGenerationMode=1",
        "",
        f"RCC.HSEState=RCC_HSE_ON",
        f"RCC.HSEFreq={vp.get('crystal_mhz', 24)}000000",
        f"RCC.SYSCLKSource=RCC_SYSCLKSOURCE_PLLCLK",
        f"RCC.PLLState=RCC_PLL_ON",
        f"RCC.PLLM=1",
        f"RCC.SYSCLKFreq_VALUE={vp.get('clock_mhz', 170)}000000",
        "",
    ]

    for i, p in enumerate(pins):
        pin = p.get("pin", "")
        func = p.get("function", "")
        label = p.get("label", "")
        if pin and func:
            lines.append(f"{pin}.Signal={func}")
            if label:
                lines.append(f"{pin}.GPIO_Label={label}")

    motor_count = vp.get("motor_count", 1)
    deadtime_ns = vp.get("deadtime_ns", 500)
    if motor_count >= 1:
        lines += [
            "",
            "TIM1.Channel-PWM Generation1 CH1=TIM_CHANNEL_1",
            "TIM1.Channel-PWM Generation2 CH2=TIM_CHANNEL_2",
            "TIM1.Channel-PWM Generation3 CH3=TIM_CHANNEL_3",
            "TIM1.CounterMode=TIM_COUNTERMODE_CENTERALIGNED1",
            f"TIM1.DeadTime={_ns_to_deadtime_reg(deadtime_ns)}",
            "TIM1.RepetitionCounter=1",
        ]

    comms = vp.get("comms", [])
    if "fdcan" in comms:
        fdcan_baud = vp.get("fdcan_baudrate", 1000000)
        lines += [
            "",
            "FDCAN1.FrameFormat=FDCAN_FRAME_CLASSIC",
            f"FDCAN1.NominalBaudRate={fdcan_baud}",
            "FDCAN1.NominalSamplePoint=87.5",
        ]

    if vp.get("spi_eeprom"):
        lines += [
            "",
            "SPI1.Mode=SPI_MODE_MASTER",
            "SPI1.Direction=SPI_DIRECTION_2LINES",
            "SPI1.DataSize=SPI_DATASIZE_8BIT",
            "SPI1.CLKPolarity=SPI_POLARITY_LOW",
            "SPI1.CLKPhase=SPI_PHASE_1EDGE",
        ]

    lines += ["", "# End of .ioc"]
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
