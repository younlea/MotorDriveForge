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

@app.post("/v1/review", response_model=ReviewReport, tags=["Step 1"])
async def review(
    chip: str = Form(..., description="예: STM32G474RET6"),
    prompt: str = Form(..., description="자연어 요구사항 프롬프트"),
    schematic_image: Optional[UploadFile] = File(
        None,
        description="회로도 이미지 (JPEG/PNG). 제공 시 Gemma 4 31B가 핀맵을 자동 추출.",
    ),
    csv_file: Optional[UploadFile] = File(None, description="핀맵 CSV (선택 — 이미지가 없을 때 필수)"),
    pinmap_csv: Optional[str] = Form(None, description="CSV 문자열 직접 입력 (선택)"),
):
    """
    회로도 이미지 + 자연어 프롬프트 → 검증 리포트.

    입력 우선순위:
      1. schematic_image → Gemma 4 31B Vision이 핀맵 자동 추출
      2. csv_file 또는 pinmap_csv → 직접 입력 CSV 사용
      (이미지와 CSV 동시 제공 시: 직접 CSV 우선, Vision 분석은 LLM 컨텍스트에만 포함)

    - errors[] > 0 이면 HTTP 403 반환.
    - vision_analysis 필드에 이미지 분석 결과 포함.
    """
    # 이미지 → base64
    image_b64: Optional[str] = None
    if schematic_image is not None:
        image_bytes = await schematic_image.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        logger.info("이미지 수신: %s (%.1f KB)", schematic_image.filename, len(image_bytes) / 1024)

    # CSV 소스 결정
    csv_text = ""
    if csv_file is not None:
        raw_bytes = await csv_file.read()
        csv_text = raw_bytes.decode("utf-8-sig")
    elif pinmap_csv:
        csv_text = pinmap_csv

    # 이미지도 CSV도 없으면 거부
    if image_b64 is None and not csv_text.strip():
        raise HTTPException(
            status_code=422,
            detail="schematic_image(회로도 이미지) 또는 csv_file/pinmap_csv 중 하나를 제공해야 합니다.",
        )

    req = ReviewRequest(
        chip=chip,
        pinmap_csv=csv_text,
        prompt=prompt,
        schematic_image_b64=image_b64,
    )

    try:
        report = get_agent().run(req)
    except Exception as e:
        logger.exception("ReviewAgent.run() 오류")
        raise HTTPException(status_code=500, detail=f"검증 에이전트 오류: {e}")

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
