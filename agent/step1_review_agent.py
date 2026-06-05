"""
Step 1 Review Agent — STM32G4 핀 검증 + 요구사항 파싱
입력: ReviewRequest (chip, schematic_images_b64[] OR pinmap_csv, prompt)
출력: ReviewReport (errors, warnings, suggestions, validated_pins, vision_analysis)

새 흐름:
  [Vision] Gemma 4 31B 멀티모달 → 이미지에서 pinmap 추출 + 초기 분석
      ↓
  [A] Rule Engine (결정론)
      ↓
  [B] Hybrid RAG (Rule Engine 키워드 + Vision 분석으로 쿼리 보강)
      ↓
  [C] LLM Persona Debate (Gemma 4 31B, Vision 분석 컨텍스트 포함)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from pydantic import BaseModel, Field

# 오프라인 운영: HF가 huggingface.co로 나가지 않고 로컬 캐시만 쓰도록 강제.
# (컨테이너 env로 명시 설정되면 그 값이 우선 — setdefault라 덮어쓰지 않음)
# 반드시 sentence_transformers/huggingface_hub import 전에 설정해야 함.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# sentence_transformers는 embed_and_index.py와 동일한 백엔드 — 벡터 호환 보장
try:
    from sentence_transformers import SentenceTransformer as _ST
    _bge_model: Optional[_ST] = None

    def _get_bge_model() -> _ST:
        global _bge_model
        if _bge_model is None:
            _bge_model = _ST("BAAI/bge-m3")
        return _bge_model
except ImportError:
    _get_bge_model = None  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    chip: str = Field(..., description="예: STM32G474RET6")
    pinmap_csv: str = Field(
        default="",
        description="CSV 문자열 (chip,pin,function,label). 이미지 제공 시 자동 생성.",
    )
    prompt: str = Field(..., description="자연어 요구사항 프롬프트")
    schematic_images_b64: List[str] = Field(
        default_factory=list,
        description="회로도 이미지 base64 목록 (여러 장 = 한 설계의 멀티 시트). 제공 시 Vision 분석 수행.",
    )
    vision_analysis: str = Field(
        default="",
        description="이미 추출한 Vision 분석(확정 단계에서 전달). 있으면 Vision 재실행 생략.",
    )
    peripherals: str = Field(
        default="",
        description="외부 부품/연결 설명(게이트드라이버·전류감지·보호·커넥터·전원). LLM 페리페럴 검토에 사용.",
    )
    mode: str = Field(
        default="full",
        description="'fast': Rule Engine만 실행 (CI용). 'full': Rule Engine + RAG + LLM (기본).",
    )


class ReviewReport(BaseModel):
    chip: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    validated_pins: Dict[str, Any] = Field(default_factory=dict)
    vision_analysis: str = Field(default="", description="Vision 모델이 이미지에서 추출한 초기 분석")


class RequirementsDict(BaseModel):
    chip: str = ""
    clock_mhz: int = 170
    crystal_mhz: int = 8
    motor_count: int = 1
    control_type: str = "FOC"           # FOC | BLDC_6step | PMSM | DC(브러시드)
    encoder_type: str = "incremental"   # incremental | hall | sensorless
    encoder_channels: List[str] = Field(default_factory=list)
    pwm_channels: int = 6
    deadtime_ns: int = 500
    current_sense: str = "internal_opamp"  # internal_opamp | shunt_external | hall
    comms: List[str] = Field(default_factory=list)   # fdcan, uart, spi, i2c, usb
    fdcan_baudrate: int = 1000000
    spi_eeprom: bool = False
    extra_timers: List[str] = Field(default_factory=list)
    raw_prompt: str = ""


# ---------------------------------------------------------------------------
# Default pin AF table (STM32G4 핵심 핀, pin_af_db.json 없을 때 폴백)
# ---------------------------------------------------------------------------

DEFAULT_PIN_AF: Dict[str, Dict[str, str]] = {
    # TIM1 (6채널 상보 출력)
    "PA8":  {"TIM1_CH1":  "AF6"},
    "PA9":  {"TIM1_CH2":  "AF6"},
    "PA10": {"TIM1_CH3":  "AF6"},
    "PB13": {"TIM1_CH1N": "AF6"},
    "PB14": {"TIM1_CH2N": "AF6"},
    "PB15": {"TIM1_CH3N": "AF6"},
    "PA7":  {"TIM1_CH1N": "AF6"},
    "PB0":  {"TIM1_CH2N": "AF6"},
    "PB1":  {"TIM1_CH3N": "AF6"},
    # TIM8 (6채널 상보 출력)
    "PC6":  {"TIM8_CH1":  "AF4"},
    "PC7":  {"TIM8_CH2":  "AF4"},
    "PC8":  {"TIM8_CH3":  "AF4"},
    "PA5":  {"TIM8_CH1N": "AF4"},
    "PC13": {"TIM8_CH1N": "AF4"},
    # TIM2 encoder
    "PA0":  {"TIM2_CH1_ETR": "AF1"},
    "PA1":  {"TIM2_CH2":  "AF1"},
    "PA5":  {"TIM2_CH1_ETR": "AF1"},
    # TIM3 encoder
    "PA6":  {"TIM3_CH1":  "AF2"},
    "PB4":  {"TIM3_CH1":  "AF2"},
    "PB5":  {"TIM3_CH2":  "AF2"},
    # FDCAN
    "PA11": {"FDCAN1_RX": "AF9"},
    "PA12": {"FDCAN1_TX": "AF9"},
    "PB8":  {"FDCAN1_RX": "AF9"},
    "PB9":  {"FDCAN1_TX": "AF9"},
    "PB5":  {"FDCAN2_RX": "AF9"},
    "PB6":  {"FDCAN2_TX": "AF9"},
    # SPI
    "PA5":  {"SPI1_SCK":  "AF5"},
    "PA6":  {"SPI1_MISO": "AF5"},
    "PA7":  {"SPI1_MOSI": "AF5"},
    "PB12": {"SPI2_NSS":  "AF5"},
    "PB13": {"SPI2_SCK":  "AF5"},
    "PB14": {"SPI2_MISO": "AF5"},
    "PB15": {"SPI2_MOSI": "AF5"},
    # OPAMP (G474 — 6개)
    "PA1":  {"OPAMP1_VINM": "analog"},
    "PA2":  {"OPAMP1_VOUT": "analog"},
    "PA3":  {"OPAMP1_VINP": "analog"},
    "PA5":  {"OPAMP2_VINM": "analog"},
    "PA6":  {"OPAMP2_VOUT": "analog"},
    "PA7":  {"OPAMP2_VINP": "analog"},
    "PB0":  {"OPAMP3_VINP": "analog"},
    "PB1":  {"OPAMP3_VOUT": "analog"},
    "PB2":  {"OPAMP3_VINM": "analog"},
    # ADC (대표)
    "PA0":  {"ADC1_IN1":  "analog"},
    "PA1":  {"ADC1_IN2":  "analog"},
    "PA2":  {"ADC1_IN3":  "analog"},
    "PC0":  {"ADC1_IN6":  "analog"},
    "PC1":  {"ADC1_IN7":  "analog"},
    "PC2":  {"ADC1_IN8":  "analog"},
    "PC3":  {"ADC1_IN9":  "analog"},
    # BRK
    "PA6":  {"TIM1_BKIN":  "AF12"},
    "PB12": {"TIM1_BKIN":  "AF6"},
    "PA9":  {"TIM8_BKIN2": "AF14"},
}

# G431은 OPAMP 3개, G474는 6개
OPAMP_MAX: Dict[str, int] = {
    "G431": 3, "G441": 3,
    "G474": 6, "G484": 6,
    "G491": 3, "G4A1": 3,
}
DMA_CH_MAX = 16

# STM32G4 다이 고정 특수기능 핀 (패키지 무관). 회로도/Vision 오인식 검출용 — 결정론 검증.
# 예) OSC_IN은 PF0 고정이라 PA11 같은 곳에 있을 수 없음.
FIXED_FUNCTION_PINS: Dict[str, str] = {
    "OSC_IN": "PF0", "OSC_OUT": "PF1",              # HSE 메인 크리스탈
    "RCC_OSC_IN": "PF0", "RCC_OSC_OUT": "PF1",
    "OSC32_IN": "PC14", "OSC32_OUT": "PC15",        # LSE 32.768kHz
    "RCC_OSC32_IN": "PC14", "RCC_OSC32_OUT": "PC15",
    "SWDIO": "PA13", "SWCLK": "PA14",               # SWD 디버그
}

# gemma4-fast = gemma4:31b 가중치 + Modelfile의 num_ctx(=웹UI와 공유하는 컨텍스트).
# webui와 같은 모델을 써서 단일 runner를 공유 → evict/콜드로드 원천 차단. 품질은 31b와 동일.
GEMMA4_VISION_MODEL = "gemma4-fast:latest"

# ---------------------------------------------------------------------------
# ReviewAgent
# ---------------------------------------------------------------------------

class ReviewAgent:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        qdrant_url: str = "http://localhost:6333",
        pin_af_db_path: Optional[str] = None,
        collection: str = "stm32g4_docs",
    ) -> None:
        self.ollama_url = ollama_url.rstrip("/")
        self.qdrant_url = qdrant_url.rstrip("/")
        self.collection = collection
        self.pin_af_db: Dict[str, Dict[str, str]] = self._load_pin_af_db(pin_af_db_path)
        self.cancel_event = None  # threading.Event, set by backend on cancel request
        self.partial: Dict[str, Any] = {}  # 단계별 중간 결과 저장

    def _is_cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _save_partial(self, **kwargs) -> None:
        self.partial.update(kwargs)

    def _stage_start(self, key: str) -> float:
        t = time.time()
        self.partial.setdefault("timing", {})[key] = {"start": t, "elapsed": None}
        return t

    def _stage_done(self, key: str, t0: float) -> None:
        elapsed = time.time() - t0
        self.partial.setdefault("timing", {})[key] = {"elapsed": round(elapsed, 1)}
        logger.info("[TIMING] %s=%.1fs", key, elapsed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_pin_af_db(self, path: Optional[str]) -> Dict[str, Dict[str, str]]:
        if path and Path(path).exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("pin_af_db loaded from %s (%d entries)", path, len(data))
                return data
            except Exception as e:
                logger.warning("pin_af_db load error: %s — using built-in table", e)
        return DEFAULT_PIN_AF

    def _chip_family(self, chip: str) -> str:
        """'STM32G474RET6' → 'G474'"""
        m = re.search(r"G4(\d{2,3})", chip.upper())
        return f"G4{m.group(1)}" if m else "G474"

    def _available_model(self) -> str:
        """Ollama에 로드된 모델 확인 — gemma4-fast(webui 공유 인스턴스) 우선, 없으면 폴백."""
        try:
            r = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if r.status_code == 200:
                names = [m["name"] for m in r.json().get("models", [])]
                for candidate in [
                    "gemma4-fast",  # webui와 동일 runner 공유 (충돌/콜드로드 방지)
                    "gemma4:31b", "gemma4:27b", "gemma4",
                    "qwen2.5:72b", "qwen2.5:32b", "qwen2.5:7b", "qwen2.5",
                ]:
                    if any(candidate in n for n in names):
                        return candidate
        except Exception:
            pass
        return GEMMA4_VISION_MODEL

    def _ollama_stream(self, payload: Dict[str, Any], read_timeout: int) -> str:
        """Ollama /api/generate 스트리밍 호출 — 토큰을 받는 즉시 누적.

        non-streaming은 전체 응답이 끝날 때까지 1바이트도 안 오므로,
        총 생성 시간 > timeout이면 무조건 read timeout. (OpenWebUI가 잘 되는 이유는 스트리밍)
        스트리밍이면 read_timeout은 '토큰 사이 간격'에만 적용되므로,
        첫 토큰까지의 지연(모델 로드 + 이미지 인코딩)만 커버하면 총 생성 시간은 무제한.
        """
        payload = {**payload, "stream": True}
        parts: List[str] = []
        with requests.post(
            f"{self.ollama_url}/api/generate",
            json=payload,
            stream=True,
            timeout=(10, read_timeout),  # (connect, read-between-chunks)
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                parts.append(obj.get("response", ""))
                if obj.get("done"):
                    break
        return "".join(parts)

    def _ollama_generate(
        self, system: str, user: str, model: str,
        think: bool = False, num_predict: int = 2048,
    ) -> str:
        """Ollama 텍스트 생성.

        think: gemma4-fast(=31b)는 추론 모델이라 think=True면 thinking에 토큰을 쏟다
        num_predict를 소진해 정작 답(response)을 못 내는 경우가 있음(빈 응답).
        - JSON 구조 출력(LLM Debate)·구조적 파싱(요구사항)은 think=False (안정성 우선).
        - 자유 서술형 답(채팅)만 think=True로 추론 사용 + num_predict 여유 확보.
        """
        payload = {
            "model": model,
            "prompt": user,
            "system": system,
            "think": think,
            "keep_alive": -1,  # 모델 메모리 영구 상주 — evict 후 재로드 방지
            # num_ctx는 일부러 지정 안 함 — gemma4-fast Modelfile의 기본 컨텍스트를 따라
            # webui와 같은 runner를 공유하기 위함. 여기서 다른 값을 주면 별도 인스턴스로 분리됨.
            "options": {"temperature": 0.1, "num_predict": num_predict},
        }
        try:
            # read_timeout: 첫 토큰까지(콜드 로드 포함) 최대 대기. 이후 토큰 간격은 짧음.
            return self._ollama_stream(payload, read_timeout=600)
        except Exception as e:
            logger.error("Ollama generate error: %s", e)
            return ""

    def _ollama_multimodal(self, prompt: str, images_b64: List[str], model: str) -> str:
        """Ollama multimodal — 이미지(1장 이상) + 텍스트 → 응답 (Gemma 4 31B).

        /api/chat 사용 (OpenWebUI와 동일 경로). /api/generate는 Gemma 4 비전에서
        이미지를 평가만 하고 생성 토큰을 0개 내는 경우가 있어 빈 응답이 나옴.
        여러 장은 한 메시지의 images 배열로 전달 → 모델이 한 설계로 종합.
        핀맵 추출 전용: num_predict를 낮춰 생성 토큰을 제한 (속도 핵심).
        스트리밍으로 호출 — 이미지 인코딩이 오래 걸려도 첫 토큰만 read_timeout 안에 오면 OK.
        """
        # think=False: 이 모델은 추론을 켜면 thinking에 토큰을 다 써(20k자, done_reason=length)
        # 정작 CSV를 못 내고 ~20분 걸림. 추론 끄면 답(CSV)을 바로 냄. 정확도는 프롬프트/온도로 보강.
        # num_predict: 핀 많은 CSV + PERIPHERALS + SUMMARY가 다 들어가게 넉넉히(추론 없으니 안전).
        # num_ctx는 Modelfile(32K) 따름.
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": images_b64}],
            "stream": True,
            "think": False,
            "keep_alive": -1,  # 모델 메모리 영구 상주 — evict 후 재로드 방지
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
        content_parts: List[str] = []
        thinking_parts: List[str] = []
        done_reason = ""
        try:
            with requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                stream=True,
                timeout=(10, 600),  # (connect, 첫 토큰까지 = 콜드 로드 + 이미지 인코딩)
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg = obj.get("message", {})
                    content_parts.append(msg.get("content", "") or "")
                    thinking_parts.append(msg.get("thinking", "") or "")
                    if obj.get("done"):
                        done_reason = obj.get("done_reason", "")
                        break
            content = "".join(content_parts)
            thinking = "".join(thinking_parts)
            # think:False가 무시되어 thinking으로만 출력된 경우 폴백
            if not content.strip() and thinking.strip():
                logger.warning(
                    "Vision content 비어있음 — thinking 폴백 사용 (thinking_len=%d, done_reason=%s)",
                    len(thinking), done_reason,
                )
                return thinking
            if not content.strip():
                logger.warning("Vision chat 빈 응답 (done_reason=%s, chunks=%d)", done_reason, len(content_parts))
            return content
        except Exception as e:
            logger.error("Ollama multimodal(chat) error: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Vision: 회로도 이미지 → pinmap CSV + 초기 분석
    # ------------------------------------------------------------------

    def _vision_extract_pinmap(
        self, images_b64: List[str], prompt: str, chip_hint: str = ""
    ) -> Tuple[str, str, str]:
        """Gemma 4 31B 멀티모달로 회로도 이미지(1장 이상) → (pinmap_csv, vision_analysis, peripherals).

        여러 장은 한 설계의 여러 시트로 보고 하나의 통합 핀맵으로 추출.
        pinmap_csv: chip,pin,function,label 형식 CSV 문자열
        vision_analysis: 이미지에서 추출한 초기 분석 텍스트 (한국어)
        peripherals: 외부 부품/연결(게이트드라이버·전류감지·보호·커넥터·전원) 사실 나열 (LLM 검토용)
        """
        multi = len(images_b64) > 1
        multi_note = (
            f"회로도가 {len(images_b64)}장입니다. 같은 설계의 여러 시트이니 모든 시트의 핀을 "
            "하나의 핀맵으로 합치고, 같은 핀이 여러 시트에 나오면 한 번만 적으세요.\n"
            if multi else ""
        )
        # OpenWebUI에서 단순 프롬프트 + 추론으로 ~90% 읽는 것을 재현 — 읽기(특히 신호 이름)에 집중.
        # 형식 과부하/영문 강제가 정확도를 떨어뜨리므로 한국어·단순·읽기중심으로.
        vision_prompt = (
            "당신은 STM32 회로도(스키매틱)를 정밀하게 읽는 전문가입니다.\n"
            "첨부된 회로도에서 STM32 칩셋과 각 핀의 연결을 하나씩 꼼꼼히 읽어 정리하세요.\n"
            f"타겟 칩 힌트: {chip_hint or 'STM32G4 계열'}\n"
            f"{multi_note}"
            "가장 중요: 각 핀 옆/배선에 적힌 신호 이름(net label, 예: VSENS_VM)을 글자 그대로 정확히 읽으세요.\n\n"
            "아래 형식으로만 출력하세요 (잡담·설명 금지):\n\n"
            "CHIP: <칩명, 예: STM32G431RBI6>\n\n"
            "CSV:\n"
            "chip,pin,function,label\n"
            "<칩>,<핀 예 PA0>,,<회로도 신호이름 예 VSENS_VM>\n"
            "... (보이는 모든 핀. function 칸은 위 예시처럼 비워두세요)\n\n"
            "PERIPHERALS:\n"
            "<외부 부품/연결을 보이는 대로만 한 줄씩 (게이트드라이버·전류감지·보호·커넥터·전원). 안 보이면 생략>\n\n"
            "SUMMARY:\n"
            "<핵심 구성 1~2문장. 분석·문제지적은 하지 말 것>\n\n"
            "원칙 (가장 중요):\n"
            "- 당신의 임무는 '핀 번호'와 '신호 이름(label)'을 정확히 읽는 것. 이 둘에만 집중하세요.\n"
            "- label(신호이름)은 회로도에 적힌 그대로. 절대 지어내지 말 것.\n"
            "- function 칸은 회로도에 STM32 HAL 기능명(예: TIM1_CH1)이 핀 옆에 '직접 인쇄'된 경우만 적고, "
            "그 외엔 반드시 비워두세요. 신호 이름을 보고 기능을 추측하지 마세요 (틀린 추측이 가장 나쁨).\n"
            "- 회로도에 실제로 보이는 핀만. 안 보이면 만들어내지 말 것.\n"
            "- STM32G4 고정핀(오인 금지): OSC_IN=PF0, OSC_OUT=PF1, OSC32_IN=PC14, OSC32_OUT=PC15, "
            "SWDIO=PA13, SWCLK=PA14, USB=PA11/PA12(오실레이터 아님)."
        )

        logger.info("Vision extraction 시작 (model=%s, sheets=%d)", GEMMA4_VISION_MODEL, len(images_b64))
        raw = self._ollama_multimodal(vision_prompt, images_b64, GEMMA4_VISION_MODEL)

        if not raw or len(raw.strip()) < 30:
            logger.warning("Vision extraction 응답 없음 또는 너무 짧음 (len=%d)", len(raw or ""))
            return "", "", ""

        # CHIP 파싱
        chip_match = re.search(r"CHIP:\s*(STM32G\w+)", raw, re.IGNORECASE)
        extracted_chip = chip_match.group(1).upper() if chip_match else chip_hint

        # CSV 섹션 추출 — 가변 줄바꿈 허용, 대소문자 무관 (PERIPHERALS/SUMMARY 앞에서 멈춤)
        csv_match = re.search(
            r"CSV:?\s*\n(.*?)(?:\n+(?:PERIPHERALS|SUMMARY|ANALYSIS):|\Z)",
            raw, re.DOTALL | re.IGNORECASE,
        )
        pinmap_csv = ""
        if csv_match:
            csv_block = csv_match.group(1).strip()
            # chip 컬럼이 없으면 보완
            lines = csv_block.splitlines()
            if lines and "chip" in lines[0].lower():
                # chip 컬럼 값이 비어있으면 extracted_chip으로 채움
                fixed = [lines[0]]
                for line in lines[1:]:
                    parts = line.split(",")
                    if parts and not parts[0].strip().startswith("STM32"):
                        parts[0] = extracted_chip
                        line = ",".join(parts)
                    fixed.append(line)
                pinmap_csv = "\n".join(fixed)
            else:
                pinmap_csv = csv_block

        # PERIPHERALS 섹션 추출 (외부 부품/연결 — LLM 검토용)
        periph_match = re.search(
            r"PERIPHERALS:\s*\n(.*?)(?:\n+(?:SUMMARY|ANALYSIS):|\Z)",
            raw, re.DOTALL | re.IGNORECASE,
        )
        peripherals = periph_match.group(1).strip() if periph_match else ""

        # SUMMARY(또는 구버전 ANALYSIS) 섹션 추출
        analysis_match = re.search(r"(?:SUMMARY|ANALYSIS):\s*\n(.*)", raw, re.DOTALL)
        vision_analysis = analysis_match.group(1).strip() if analysis_match else raw[:300]

        logger.info(
            "Vision extraction 완료 — chip=%s, csv_lines=%d, periph=%d자",
            extracted_chip,
            len(pinmap_csv.splitlines()),
            len(peripherals),
        )
        return pinmap_csv, vision_analysis, peripherals

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse_prompt(self, prompt: str) -> RequirementsDict:
        """자연어 프롬프트 → RequirementsDict (LLM + 정규식 폴백)."""
        req = RequirementsDict(raw_prompt=prompt)

        # --- 칩명
        m = re.search(r"(STM32G4\w+)", prompt, re.IGNORECASE)
        if m:
            req.chip = m.group(1).upper()

        # --- 클럭
        m = re.search(r"(\d+)\s*MHz.*?시스템|시스템.*?(\d+)\s*MHz", prompt)
        if m:
            req.clock_mhz = int(m.group(1) or m.group(2))
        m = re.search(r"크리스탈\s*(\d+)\s*MHz|외부.*?(\d+)\s*MHz", prompt)
        if m:
            req.crystal_mhz = int(m.group(1) or m.group(2))

        # --- 모터 수
        m = re.search(r"(\d+)\s*[개ea].*?모터|모터\s*(\d+)\s*[개ea]", prompt)
        if m:
            req.motor_count = int(m.group(1) or m.group(2))

        # --- 제어 방식 (구체적인 것부터 검사; "bldc"가 "dc"에 오인 매칭되지 않도록 순서 주의)
        pl = prompt.lower()
        if "foc" in pl:
            req.control_type = "FOC"
        elif "pmsm" in pl:
            req.control_type = "PMSM"
        elif "bldc" in pl or "6step" in pl or "6 step" in pl or "6스텝" in prompt:
            req.control_type = "BLDC_6step"
        elif ("브러시드" in prompt or "brushed" in pl or "dc모터" in pl
              or "dc 모터" in pl or re.search(r"\bdc\b", pl)):
            req.control_type = "DC"  # 브러시드 DC — 3상 FOC 아님

        # --- 인코더
        if "증분형" in prompt or "incremental" in prompt.lower():
            req.encoder_type = "incremental"
        elif "hall" in prompt.lower() or "홀" in prompt:
            req.encoder_type = "hall"
        elif "센서리스" in prompt or "sensorless" in prompt.lower():
            req.encoder_type = "sensorless"

        # --- PWM 채널
        m = re.search(r"(\d+)\s*채널\s*PWM|PWM\s*(\d+)\s*채널", prompt)
        if m:
            req.pwm_channels = int(m.group(1) or m.group(2))

        # --- 데드타임
        m = re.search(r"데드타임\s*(\d+)\s*ns|deadtime\s*(\d+)\s*ns", prompt, re.IGNORECASE)
        if m:
            req.deadtime_ns = int(m.group(1) or m.group(2))

        # --- 전류 센싱
        if "내부 opamp" in prompt.lower() or "internal opamp" in prompt.lower() or "내부 OPAMP" in prompt:
            req.current_sense = "internal_opamp"
        elif "외부 opamp" in prompt.lower() or "shunt" in prompt.lower():
            req.current_sense = "shunt_external"

        # --- 통신
        comms: List[str] = []
        if "fdcan" in prompt.lower() or "can" in prompt.lower():
            comms.append("fdcan")
        if "uart" in prompt.lower() or "usart" in prompt.lower():
            comms.append("uart")
        if "spi" in prompt.lower():
            comms.append("spi")
        if "i2c" in prompt.lower():
            comms.append("i2c")
        if "usb" in prompt.lower():
            comms.append("usb")
        req.comms = comms

        # --- FDCAN 속도
        m = re.search(r"(\d+)\s*[Mm]bps|(\d+)\s*kbps", prompt)
        if m:
            val = m.group(1) or m.group(2)
            if "M" in (m.group(0) or "") or "m" in (m.group(0) or ""):
                req.fdcan_baudrate = int(val) * 1_000_000
            else:
                req.fdcan_baudrate = int(val) * 1_000

        # --- SPI EEPROM
        req.spi_eeprom = bool(re.search(r"spi.*?eeprom|eeprom.*?spi", prompt, re.IGNORECASE))

        # LLM 보완 파싱 — 기본 OFF. 정규식이 이미 모든 필드를 파싱하므로 보통 불필요하고,
        # 여기서 LLM을 호출하면 Vision~rule_engine 사이를 수십 초~분 막아버림(stage 로그도 없어 '쉬는 것'처럼 보임).
        # 비정형 프롬프트라 정규식이 약할 때만 LLM_ENHANCE_REQUIREMENTS=1로 켜기.
        if os.getenv("LLM_ENHANCE_REQUIREMENTS", "0") == "1":
            logger.info("[STAGE] requirements_llm:start")
            _t0 = self._stage_start("requirements_llm")
            try:
                req = self._llm_enhance_requirements(req)
            except Exception as e:
                logger.warning("LLM requirements enhance failed: %s", e)
            self._stage_done("requirements_llm", _t0)

        return req

    def _llm_enhance_requirements(self, req: RequirementsDict) -> RequirementsDict:
        model = self._available_model()
        system = (
            "You are an STM32G4 expert. Parse the hardware requirements from the user prompt "
            "and output ONLY a JSON object with these keys: "
            "chip, clock_mhz, crystal_mhz, motor_count, control_type, encoder_type, "
            "pwm_channels, deadtime_ns, current_sense, comms (list), fdcan_baudrate, spi_eeprom (bool). "
            "Do not add any explanation."
        )
        user = f"Prompt:\n{req.raw_prompt}\n\nCurrent parsed (may be incomplete):\n{req.model_dump_json()}"
        # 작은 JSON 출력 — think=False(기본), num_predict 작게 잡아 빠르게.
        raw = self._ollama_generate(system, user, model, num_predict=512)

        # JSON 블록 추출
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            for k, v in data.items():
                if v is not None and v != "" and hasattr(req, k):
                    setattr(req, k, v)
        return req

    def validate_pins_rules(
        self,
        pinmap_df: pd.DataFrame,
        requirements: RequirementsDict,
    ) -> Tuple[List[str], List[str]]:
        """규칙 엔진 — (errors, warnings) 반환."""
        errors: List[str] = []
        warnings: List[str] = []
        family = self._chip_family(requirements.chip or "STM32G474")

        pins = set(pinmap_df["pin"].str.upper().tolist()) if "pin" in pinmap_df.columns else set()
        functions = set(pinmap_df["function"].str.upper().tolist()) if "function" in pinmap_df.columns else set()

        # 1. TIM1/TIM8 핀 충돌
        shared_brk_pins = {"PB0", "PB1"}
        if shared_brk_pins & pins:
            tim1_funcs = {f for f in functions if "TIM1" in f}
            tim8_funcs = {f for f in functions if "TIM8" in f}
            if tim1_funcs and tim8_funcs:
                errors.append(
                    "TIM1/TIM8 핀 충돌: PB0/PB1은 TIM1_CH2N/CH3N과 TIM8_CH2N/CH3N AF가 겹칩니다. "
                    "두 타이머를 동시에 상보 출력으로 사용하려면 별도 핀을 배정하세요."
                )

        # 2. OPAMP 수 초과
        opamp_max = OPAMP_MAX.get(family, 6)
        opamp_funcs = [f for f in functions if "OPAMP" in f and "VOUT" in f]
        opamp_count = len(opamp_funcs)
        # 3상 제어(FOC/PMSM/BLDC)만 모터당 3채널 전류감지 가정. 브러시드 DC는 해당 없음.
        three_phase = requirements.control_type in ("FOC", "PMSM", "BLDC_6step")
        ctrl = requirements.control_type
        required_opamp = (
            requirements.motor_count * 3
            if (requirements.current_sense == "internal_opamp" and three_phase) else 0
        )
        if required_opamp > opamp_max:
            errors.append(
                f"OPAMP 초과: {family} 최대 {opamp_max}개, "
                f"{ctrl} {requirements.motor_count}모터 × 3채널 = {required_opamp}개 필요. "
                "모터 수를 줄이거나 외부 OPAMP로 변경하세요."
            )
        elif required_opamp > 0 and opamp_count < required_opamp:
            warnings.append(
                f"OPAMP 부족 가능성: 핀맵에 OPAMP_VOUT {opamp_count}개 정의, "
                f"{ctrl} {requirements.motor_count}모터에는 {required_opamp}개 필요."
            )

        # 3. BRK 핀 공유 여부
        brk_funcs = [f for f in functions if "BKIN" in f]
        if requirements.motor_count > 1 and len(brk_funcs) < requirements.motor_count:
            warnings.append(
                f"BRK 핀 부족: {requirements.motor_count}모터 독립 보호에는 BRK 핀 {requirements.motor_count}개 필요, "
                f"현재 {len(brk_funcs)}개. 모터별 독립 fault 보호가 불가할 수 있습니다."
            )

        # 4. ADC 트리거 소스 중복
        adc_trig_funcs = [f for f in functions if "ADC" in f and "TRIG" in f]
        if len(adc_trig_funcs) != len(set(adc_trig_funcs)):
            errors.append("ADC 트리거 소스 중복: 동일 트리거가 여러 ADC에 할당되어 있습니다.")

        # 5. DMA 채널 초과
        dma_funcs = [f for f in functions if "DMA" in f]
        if len(dma_funcs) > DMA_CH_MAX:
            errors.append(
                f"DMA 채널 초과: STM32G4 최대 {DMA_CH_MAX}채널, 현재 {len(dma_funcs)}개 할당."
            )

        # 6. CPU 부하 추정 (20kHz FOC 기준)
        if requirements.control_type == "FOC" and requirements.motor_count > 2:
            errors.append(
                f"CPU 부하 초과 위험: STM32G474 170MHz에서 20kHz FOC 최대 권장 2모터, "
                f"요청 {requirements.motor_count}모터. 제어 주파수 낮추거나 모터 수 조정 필요."
            )
        elif requirements.control_type == "FOC" and requirements.motor_count == 2:
            warnings.append(
                "CPU 부하 주의: 20kHz FOC 2모터는 170MHz에서 ~85% CPU 부하. "
                "백그라운드 태스크를 최소화하세요."
            )

        # 7. 핀 AF 기본 검증
        if "pin" in pinmap_df.columns and "function" in pinmap_df.columns:
            for _, row in pinmap_df.iterrows():
                pin = str(row["pin"]).upper()
                func = str(row["function"]).upper().strip()
                if func in ("", "NAN", "NONE", "GPIO"):
                    continue  # function 미지정(Vision이 신호이름만 읽은 경우)은 AF 검증 생략
                if pin in self.pin_af_db:
                    valid_funcs = list(self.pin_af_db[pin].keys())
                    if func not in valid_funcs and not func.startswith("ADC") and not func.startswith("DAC"):
                        warnings.append(
                            f"핀 AF 미확인: {pin} — {func}. "
                            f"DB 등록 기능: {', '.join(valid_funcs)}"
                        )

        # 8. FDCAN 관련
        if "fdcan" in requirements.comms:
            fdcan_pins = [f for f in functions if "FDCAN" in f]
            if len(fdcan_pins) < 2:
                errors.append(
                    "FDCAN 핀 부족: FDCAN_TX, FDCAN_RX 최소 2핀 필요."
                )

        # 9. SPI EEPROM
        if requirements.spi_eeprom:
            spi_funcs = [f for f in functions if "SPI" in f]
            if not spi_funcs:
                warnings.append("SPI EEPROM 요청되었으나 핀맵에 SPI 핀이 없습니다.")

        # 10. 고정 특수기능 핀 위치 검증 (OSC/SWD는 다이 고정 — 회로도·Vision 오인식 검출)
        if "pin" in pinmap_df.columns and "function" in pinmap_df.columns:
            for _, row in pinmap_df.iterrows():
                pin = str(row["pin"]).upper().strip()
                func = str(row["function"]).upper().strip()
                expected = FIXED_FUNCTION_PINS.get(func)
                if expected and pin != expected:
                    errors.append(
                        f"고정 핀 불일치: {func}는 STM32G4에서 {expected}에 고정인데 {pin}에 할당됨. "
                        f"회로도 재확인 필요(특히 Vision 추출 오인식 — 예: PA11/PA13은 OSC 핀이 아님)."
                    )

        return errors, warnings

    def rag_query(self, query_text: str, top_k: int = 5) -> List[str]:
        """Qdrant hybrid search → 관련 문서 청크 목록."""
        try:
            payload = {
                "vector": self._embed(query_text),
                "limit": top_k,
                "with_payload": True,
            }
            r = requests.post(
                f"{self.qdrant_url}/collections/{self.collection}/points/search",
                json=payload,
                timeout=10,
            )
            if r.status_code == 200:
                results = r.json().get("result", [])
                return [
                    hit["payload"].get("text", hit["payload"].get("content", ""))
                    for hit in results
                    if hit.get("payload")
                ]
        except Exception as e:
            logger.warning("RAG query failed: %s", e)
        return []

    def _rag_with_meta(self, query: str, top_k: int = 3) -> List[dict]:
        """Qdrant 검색 — 텍스트 + 출처 메타데이터 반환."""
        try:
            payload = {
                "vector": self._embed(query),
                "limit": top_k,
                "with_payload": True,
            }
            r = requests.post(
                f"{self.qdrant_url}/collections/{self.collection}/points/search",
                json=payload,
                timeout=10,
            )
            if r.status_code == 200:
                results = r.json().get("result", [])
                hits = []
                for hit in results:
                    p = hit.get("payload", {})
                    hits.append({
                        "text": p.get("text", p.get("content", "")),
                        "doc_id": p.get("doc_id", ""),
                        "section": p.get("section", ""),
                        "page_start": p.get("page_start", ""),
                    })
                return hits
        except Exception as e:
            logger.warning("RAG with meta failed: %s", e)
        return []

    def chat(
        self,
        chip: str,
        question: str,
        history: List[dict],
        report_context: dict,
    ) -> dict:
        """멀티턴 채팅 — 검증 결과 컨텍스트 + RAG + 대화 이력 → 답변 + 출처.

        Args:
            history: [{"role": "user"|"assistant", "content": str}, ...]
            report_context: ReviewReport.model_dump()
        Returns:
            {"answer": str, "sources": [str, ...]}
        """
        errors = report_context.get("errors", [])
        warnings = report_context.get("warnings", [])
        suggestions = report_context.get("suggestions", [])
        vision = report_context.get("vision_analysis", "")

        # RAG
        rag_hits = self._rag_with_meta(f"{chip} {question}", top_k=3)
        rag_text = "\n\n".join(h["text"] for h in rag_hits if h["text"])
        sources = [
            f"{h['doc_id']} — {h['section']} (p.{h['page_start']})"
            for h in rag_hits
            if h.get("doc_id")
        ]

        # 대화 이력 (최근 6턴)
        history_text = "\n".join(
            f"{'사용자' if m['role'] == 'user' else '전문가'}: {m['content']}"
            for m in history[-6:]
        )

        system_prompt = (
            "당신은 STM32G4 모터 드라이브 회로 전문가입니다. "
            "이미 수행된 검증 결과와 참고 문서를 바탕으로 사용자 질문에 답하세요. "
            "근거가 있으면 문서명과 내용을 인용하세요. 한국어로 답변하세요."
        )

        user_prompt = f"""검증 결과 요약:
- 칩: {chip}
- 오류: {errors if errors else '없음'}
- 경고: {warnings if warnings else '없음'}
- 권장: {suggestions if suggestions else '없음'}
- Vision 분석: {vision[:300]}

참고 문서:
{rag_text if rag_text else '(RAG 결과 없음)'}

대화 이력:
{history_text if history_text else '(첫 번째 질문)'}

사용자 질문: {question}"""

        model = self._available_model()
        # 채팅은 자유 서술형이라 추론(think) 사용. thinking이 답을 잡아먹지 않게 num_predict 여유.
        answer = self._ollama_generate(system_prompt, user_prompt, model, think=True, num_predict=4096)
        if not answer:
            answer = "답변 생성에 실패했습니다(빈 응답). 다시 시도하거나 질문을 더 구체적으로 해주세요."

        return {"answer": answer, "sources": sources}

    def _embed(self, text: str) -> List[float]:
        """embed_and_index.py와 동일한 sentence_transformers BAAI/bge-m3 사용.
        Ollama bge-m3(llama.cpp)는 벡터가 다르게 나와 RAG 검색 품질 저하됨."""
        if _get_bge_model is not None:
            try:
                vec = _get_bge_model().encode(text, normalize_embeddings=True)
                return vec.tolist()
            except Exception as e:
                logger.warning("sentence_transformers embed failed: %s", e)
        return [0.0] * 1024

    def extract_pinmap_only(self, request: ReviewRequest) -> Dict[str, str]:
        """Vision만 실행 — 사용자 확정용 핀맵 추출 (Rule/RAG/LLM 안 함).

        반환: {"chip": 감지/입력 칩, "pinmap_csv": 추출 CSV, "vision_analysis": 요약}
        칩은 명시 입력값(request.chip)이 있으면 우선, 없으면 추출 CSV에서 감지.
        """
        if not request.schematic_images_b64:
            return {"chip": request.chip, "pinmap_csv": request.pinmap_csv, "vision_analysis": "", "peripherals": ""}

        logger.info("extract_pinmap_only — sheets=%d", len(request.schematic_images_b64))
        _t0 = self._stage_start("vision")
        csv_from_vision, vision_analysis, peripherals = self._vision_extract_pinmap(
            request.schematic_images_b64, request.prompt, chip_hint=request.chip,
        )
        self._stage_done("vision", _t0)
        self._save_partial(vision_analysis=vision_analysis, extracted_csv=csv_from_vision, peripherals=peripherals)

        # 칩 결정: 명시 입력이 있으면 그대로, 없으면 CSV 첫 데이터행에서 감지
        detected_chip = request.chip
        if not detected_chip:
            for line in csv_from_vision.splitlines()[1:]:
                first = line.split(",")[0].strip().upper()
                if first.startswith("STM32G4"):
                    detected_chip = first
                    break
        return {
            "chip": detected_chip,
            "pinmap_csv": csv_from_vision,
            "vision_analysis": vision_analysis,
            "peripherals": peripherals,
        }

    def run(self, request: ReviewRequest) -> ReviewReport:
        """메인 실행 — ReviewRequest → ReviewReport.

        흐름:
          [Vision]  이미지 → pinmap CSV + 초기 분석  (이미지 제공 시)
          [A]       Rule Engine 결정론 검증
          [B]       Hybrid RAG (Rule Engine 키워드 + Vision 분석으로 쿼리 보강)
          [C]       LLM Persona Debate (Vision 분석 포함 전체 컨텍스트)
        """
        logger.info("ReviewAgent.run() chip=%s, image_count=%d", request.chip, len(request.schematic_images_b64))

        # 확정 단계에서 전달된 Vision 분석/페리페럴이 있으면 그대로 사용 (이미지 없으면 Vision 재실행 안 함)
        vision_analysis = request.vision_analysis or ""
        peripherals = request.peripherals or ""

        # ── [Vision] 이미지가 있으면 pinmap 추출 (여러 장은 한 설계로 종합) ──
        if request.schematic_images_b64:
            _t0 = self._stage_start("vision")
            csv_from_vision, vision_analysis, peripherals = self._vision_extract_pinmap(
                request.schematic_images_b64,
                request.prompt,
                chip_hint=request.chip,
            )
            self._stage_done("vision", _t0)
            self._save_partial(vision_analysis=vision_analysis, extracted_csv=csv_from_vision, peripherals=peripherals)
            # 직접 입력 CSV가 없을 때만 Vision CSV 사용
            if not request.pinmap_csv.strip() and csv_from_vision:
                request.pinmap_csv = csv_from_vision
                logger.info("Vision CSV 사용 (%d chars)", len(csv_from_vision))
            elif csv_from_vision:
                logger.info("직접 입력 CSV 우선 사용 (Vision 분석은 컨텍스트에만 포함)")

        # ── CSV 파싱 ───────────────────────────────────────────────────────
        if not request.pinmap_csv.strip():
            return ReviewReport(
                chip=request.chip,
                errors=["핀맵 정보 없음: 회로도 이미지 또는 CSV를 입력해주세요."],
                vision_analysis=vision_analysis,
            )

        try:
            from io import StringIO
            pinmap_df = pd.read_csv(StringIO(request.pinmap_csv))
            pinmap_df.columns = [c.strip().lower() for c in pinmap_df.columns]
        except Exception as e:
            return ReviewReport(
                chip=request.chip,
                errors=[f"CSV 파싱 오류: {e}"],
                vision_analysis=vision_analysis,
            )

        # ── [A] 요구사항 파싱 + Rule Engine ───────────────────────────────
        requirements = self.parse_prompt(request.prompt)
        if not requirements.chip:
            requirements.chip = request.chip
        # 디버그: 프롬프트에서 인식된 요구사항 + Rule Engine 입력 핀맵 노출
        self._save_partial(
            requirements=requirements.model_dump(),
            pinmap_rows=pinmap_df.fillna("").to_dict(orient="records"),
        )

        if self._is_cancelled():
            raise InterruptedError("cancelled")

        logger.info("[STAGE] rule_engine:start")
        _t0 = self._stage_start("rule_engine")
        rule_errors, rule_warnings = self.validate_pins_rules(pinmap_df, requirements)
        self._stage_done("rule_engine", _t0)
        logger.info("[STAGE] rule_engine:done errors=%d warnings=%d", len(rule_errors), len(rule_warnings))
        self._save_partial(rule_errors=rule_errors, rule_warnings=rule_warnings)

        # fast 모드: Rule Engine 결과만 즉시 반환 (LLM/RAG 호출 없음)
        if request.mode == "fast":
            return ReviewReport(
                chip=request.chip,
                errors=rule_errors,
                warnings=rule_warnings,
                validated_pins=self._build_validated_pins(pinmap_df, requirements),
                vision_analysis=vision_analysis,
            )

        # ── [B] Hybrid RAG — Rule Engine 키워드 + Vision 분석으로 쿼리 보강 ──
        keyword_hints = ""
        if rule_errors or rule_warnings:
            # Rule Engine 결과에서 핵심 키워드 추출
            all_issues = " ".join(rule_errors + rule_warnings)
            kw_matches = re.findall(r"TIM\w+|OPAMP\w*|DMA|FDCAN\w*|ADC\w*|BRK|AF\d+|BDTR", all_issues)
            keyword_hints = " ".join(set(kw_matches))

        # 제어 방식별 RAG 키워드 (FOC 하드코딩 금지 — DC인데 FOC 문서 끌어오면 안 됨)
        ctrl_kw = {
            "FOC": "FOC 3상 PWM TIM1 상보출력 데드타임 OPAMP 전류감지",
            "PMSM": "PMSM FOC 3상 PWM TIM1 상보출력 OPAMP 전류감지",
            "BLDC_6step": "BLDC 6-step 사다리꼴 PWM TIM1 홀센서 전류감지",
            "DC": "브러시드 DC 모터 H-bridge PWM TIM 전류감지",
        }.get(requirements.control_type, "모터 제어 PWM TIM")
        rag_query = (
            f"STM32G4 {request.chip} 핀 AF 검증 {ctrl_kw} 규칙"
        )
        if keyword_hints:
            rag_query += f" {keyword_hints}"
        if vision_analysis:
            # Vision 초기 분석의 첫 200자를 RAG 쿼리에 반영
            rag_query += f" {vision_analysis[:200]}"

        if self._is_cancelled():
            raise InterruptedError("cancelled")

        logger.info("[STAGE] rag:start query_len=%d", len(rag_query))
        _t0 = self._stage_start("rag")
        rag_docs = self.rag_query(rag_query, top_k=5)
        rag_context = "\n\n---\n\n".join(rag_docs[:5]) if rag_docs else "(RAG 없음)"
        self._stage_done("rag", _t0)
        logger.info("[STAGE] rag:done docs=%d", len(rag_docs))
        self._save_partial(
            rag_query=rag_query,
            rag_docs_count=len(rag_docs),
            # 각 청크를 개별 저장 (디버그 패널에서 5개 모두 확인용). LLM엔 rag_context 전체가 들어감.
            rag_chunks=[d[:1500] for d in rag_docs[:5]],
        )

        # ── [C] LLM Persona Debate ─────────────────────────────────────────
        if self._is_cancelled():
            raise InterruptedError("cancelled")

        logger.info("[STAGE] llm:start")
        _t0 = self._stage_start("llm")
        llm_errors, llm_warnings, llm_suggestions = self._llm_validate(
            request, requirements, pinmap_df, rag_context, vision_analysis, peripherals
        )
        self._stage_done("llm", _t0)
        logger.info("[STAGE] llm:done")
        self._save_partial(llm_errors=llm_errors, llm_warnings=llm_warnings, llm_suggestions=llm_suggestions)

        # ── 결과 합산 (중복 제거) ──────────────────────────────────────────
        all_errors = list(dict.fromkeys(rule_errors + llm_errors))
        all_warnings = list(dict.fromkeys(rule_warnings + llm_warnings))
        all_suggestions = list(dict.fromkeys(llm_suggestions))

        validated_pins = self._build_validated_pins(pinmap_df, requirements)

        return ReviewReport(
            chip=request.chip,
            errors=all_errors,
            warnings=all_warnings,
            suggestions=all_suggestions,
            validated_pins=validated_pins,
            vision_analysis=vision_analysis,
        )

    def _llm_validate(
        self,
        request: ReviewRequest,
        requirements: RequirementsDict,
        pinmap_df: pd.DataFrame,
        rag_context: str,
        vision_analysis: str = "",
        peripherals: str = "",
    ) -> Tuple[List[str], List[str], List[str]]:
        """LLM 검증 — STM32 핀맵 + 외부 페리페럴(게이트드라이버·전류감지·보호·전원) 종합.
        errors, warnings, suggestions 반환."""
        model = self._available_model()

        system = """You are an expert STM32G4 motor-drive hardware reviewer.
Review BOTH the STM32 pin assignments AND the connected peripherals (gate driver, power stage,
current sensing, protection, connectors, power supply). Output ONLY a JSON object with:
{
  "errors": ["..."],
  "warnings": ["..."],
  "suggestions": ["..."]
}
Cover, where the data allows:
- MCU side: AF/pin conflicts, timer/ADC/DMA, complementary PWM + dead-time, clock.
- Power/EMI: gate driver bootstrap/supply, decoupling, current-sense resistor/OPAMP path, GND.
- Safety/Failsafe: brake/BKIN, OCP/OVP/UVLO, watchdog, fault feedback to MCU.
Rules:
- errors: critical issues that BLOCK firmware generation or are unsafe.
- warnings: issues the developer should review.
- suggestions: optimization/best-practice recommendations.
- Write in Korean. Be specific: name the pin/peripheral/component.
- Base peripheral findings on the PERIPHERALS section; if info is missing, ask via a suggestion
  rather than inventing components. Do not repeat rule-engine issues."""

        vision_section = ""
        if vision_analysis:
            vision_section = f"\nVision 초기 분석:\n{vision_analysis}\n"
        periph_section = (
            f"\n연결된 페리페럴/외부 부품 (사용자 확정):\n{peripherals}\n"
            if peripherals.strip() else
            "\n연결된 페리페럴 정보 없음 — 페리페럴 관련은 추정 금지, 필요한 정보를 suggestion으로 질문할 것.\n"
        )

        user = f"""Chip: {request.chip}
Requirements (parsed):
{requirements.model_dump_json(indent=2)}
{vision_section}{periph_section}
Pinmap CSV:
{pinmap_df.to_csv(index=False)}

Reference documents (RAG):
{rag_context}

Review the MCU pinmap AND the peripherals, then return JSON."""

        raw = self._ollama_generate(system, user, model)  # think=False (기본) — JSON 안정 출력
        if not raw:
            return [], [], ["LLM 검증 응답 없음 — 규칙 엔진 결과만 사용됩니다."]

        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return [], [], [f"LLM 응답 파싱 실패: {raw[:200]}"]

        try:
            data = json.loads(m.group(0))
            return (
                data.get("errors", []),
                data.get("warnings", []),
                data.get("suggestions", []),
            )
        except json.JSONDecodeError as e:
            logger.error("LLM JSON decode error: %s", e)
            return [], [], []

    def _build_validated_pins(
        self,
        pinmap_df: pd.DataFrame,
        requirements: RequirementsDict,
    ) -> Dict[str, Any]:
        """확정 핀 JSON 구조 생성."""
        pins_list = []
        if "pin" in pinmap_df.columns:
            for _, row in pinmap_df.iterrows():
                entry: Dict[str, Any] = {
                    "pin": str(row.get("pin", "")).upper(),
                    "function": str(row.get("function", "")),
                    "label": str(row.get("label", "")),
                }
                pin_upper = entry["pin"]
                if pin_upper in self.pin_af_db:
                    func_upper = entry["function"].upper()
                    af = self.pin_af_db[pin_upper].get(func_upper, "")
                    entry["af"] = af
                pins_list.append(entry)

        return {
            "chip": requirements.chip,
            "clock_mhz": requirements.clock_mhz,
            "crystal_mhz": requirements.crystal_mhz,
            "motor_count": requirements.motor_count,
            "control_type": requirements.control_type,
            "encoder_type": requirements.encoder_type,
            "pwm_channels": requirements.pwm_channels,
            "deadtime_ns": requirements.deadtime_ns,
            "current_sense": requirements.current_sense,
            "comms": requirements.comms,
            "spi_eeprom": requirements.spi_eeprom,
            "pins": pins_list,
        }


# ---------------------------------------------------------------------------
# CLI entry point (테스트용)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    sample_csv = """chip,pin,function,label
STM32G474RET6,PA8,TIM1_CH1,U_PWM_H
STM32G474RET6,PA9,TIM1_CH2,V_PWM_H
STM32G474RET6,PA10,TIM1_CH3,W_PWM_H
STM32G474RET6,PB13,TIM1_CH1N,U_PWM_L
STM32G474RET6,PB14,TIM1_CH2N,V_PWM_L
STM32G474RET6,PB15,TIM1_CH3N,W_PWM_L
STM32G474RET6,PA12,FDCAN1_TX,CAN_TX
STM32G474RET6,PA11,FDCAN1_RX,CAN_RX
STM32G474RET6,PA2,OPAMP1_VOUT,CURR_U
STM32G474RET6,PA6,OPAMP2_VOUT,CURR_V
STM32G474RET6,PB1,OPAMP3_VOUT,CURR_W
"""

    sample_prompt = (
        "STM32G474RET6 칩을 쓸 거고, 외부 크리스탈 24MHz / 시스템 170MHz야. "
        "BLDC 모터 1개를 FOC로 제어할 건데 증분형 엔코더(A/B/Z)로 각도 읽고, "
        "3상 6채널 PWM으로 인버터 구동해. 데드타임 500ns, 전류는 내부 OPAMP. "
        "통신은 FDCAN 1Mbps 쓰고, 파라미터 저장용으로 SPI EEPROM도 연결할 거야."
    )

    agent = ReviewAgent()
    req = ReviewRequest(chip="STM32G474RET6", pinmap_csv=sample_csv, prompt=sample_prompt)
    report = agent.run(req)
    print(report.model_dump_json(indent=2))
