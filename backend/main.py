"""
FastAPI 백엔드 — STM32G4 Motor Drive Agent
엔드포인트:
  POST /v1/review        핀맵 CSV + 프롬프트 → 리뷰 리포트
  GET  /v1/status        파이프라인 서비스 상태
  POST /v1/generate-ioc  핀 JSON → .ioc 파일 생성 (Step 2)
  GET  /v1/health        헬스체크
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# 프로젝트 루트 기준 agent 모듈 임포트
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent.step1_review_agent import ReviewAgent, ReviewReport, ReviewRequest

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "stm32g4_docs")
PIN_AF_DB_PATH = os.getenv("PIN_AF_DB_PATH", "")
IOC_OUTPUT_DIR = Path(os.getenv("IOC_OUTPUT_DIR", "/tmp/ioc_outputs"))
IOC_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI 앱
# ---------------------------------------------------------------------------

app = FastAPI(
    title="STM32G4 Motor Drive Agent API",
    description="3-Step 파이프라인: 핀 검증 → CubeMX 자동화 → 알고리즘 통합",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Streamlit / React 연동
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ReviewAgent 싱글턴
_agent: Optional[ReviewAgent] = None


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


# ---------------------------------------------------------------------------
# 요청/응답 로깅 미들웨어
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    rid = str(uuid.uuid4())[:8]
    logger.info("[%s] → %s %s", rid, request.method, request.url.path)
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


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------

@app.get("/v1/health", tags=["System"])
async def health():
    return {"status": "ok", "service": "stm32g4-agent-backend"}


# ---------------------------------------------------------------------------
# GET /v1/status — 서비스 연결 확인
# ---------------------------------------------------------------------------

@app.get("/v1/status", response_model=StatusResponse, tags=["System"])
async def get_status():
    # Ollama 확인
    ollama_ok = False
    ollama_models: List[str] = []
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            ollama_ok = True
            ollama_models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    # Qdrant 확인
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
# POST /v1/review — Step 1 핀 검증
# ---------------------------------------------------------------------------

@app.post("/v1/review", response_model=ReviewReport, tags=["Step 1"])
async def review(
    chip: str = Form(..., description="예: STM32G474RET6"),
    prompt: str = Form(..., description="자연어 요구사항 프롬프트"),
    csv_file: Optional[UploadFile] = File(None, description="핀맵 CSV 파일 (선택)"),
    pinmap_csv: Optional[str] = Form(None, description="CSV 문자열 직접 입력 (선택)"),
):
    """
    핀맵 CSV + 자연어 프롬프트 → 검증 리포트.

    - csv_file OR pinmap_csv 중 하나 필수.
    - errors[] > 0 이면 HTTP 403 반환 (펌웨어 생성 차단).
    - warnings는 표시 후 통과 허용.
    """
    # CSV 소스 결정
    if csv_file is not None:
        raw_bytes = await csv_file.read()
        csv_text = raw_bytes.decode("utf-8-sig")  # BOM 제거
    elif pinmap_csv:
        csv_text = pinmap_csv
    else:
        raise HTTPException(
            status_code=422,
            detail="csv_file 또는 pinmap_csv 중 하나를 제공해야 합니다.",
        )

    req = ReviewRequest(chip=chip, pinmap_csv=csv_text, prompt=prompt)

    try:
        report = get_agent().run(req)
    except Exception as e:
        logger.exception("ReviewAgent.run() 오류")
        raise HTTPException(status_code=500, detail=f"검증 에이전트 오류: {e}")

    # 검증 게이트: errors > 0 → HTTP 403
    if report.errors:
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
    """
    확정 핀 JSON → STM32CubeMX .ioc 파일 생성.
    현재 Python 스크립트로 .ioc 텍스트 생성 (CubeMX 헤드리스 연동 준비).
    """
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

    # 핀 할당
    for i, p in enumerate(pins):
        pin = p.get("pin", "")
        func = p.get("function", "")
        label = p.get("label", "")
        if pin and func:
            lines.append(f"{pin}.Signal={func}")
            if label:
                lines.append(f"{pin}.GPIO_Label={label}")

    # TIM1 설정 (FOC PWM)
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

    # FDCAN 설정
    comms = vp.get("comms", [])
    if "fdcan" in comms:
        fdcan_baud = vp.get("fdcan_baudrate", 1000000)
        lines += [
            "",
            "FDCAN1.FrameFormat=FDCAN_FRAME_CLASSIC",
            f"FDCAN1.NominalBaudRate={fdcan_baud}",
            "FDCAN1.NominalSamplePoint=87.5",
        ]

    # SPI EEPROM
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
    # DTG = ns / (1/170MHz * 1e9) 근사 (DTG[7:5]=000 범위, 1 step = ~5.9ns)
    step_ns = 1e9 / 170_000_000
    return min(int(ns / step_ns), 127)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
