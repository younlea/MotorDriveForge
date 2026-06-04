"""
Step 1 Review Agent — STM32G4 핀 검증 + 요구사항 파싱
입력: ReviewRequest (chip, schematic_image_b64 OR pinmap_csv, prompt)
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
    schematic_image_b64: Optional[str] = Field(
        None,
        description="회로도 이미지 base64 인코딩 (JPEG/PNG). 제공 시 Vision 분석 수행.",
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
    control_type: str = "FOC"           # FOC | BLDC_6step | PMSM
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

GEMMA4_VISION_MODEL = "gemma4:31b"

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
        """Ollama에 로드된 모델 확인 — Gemma 4 31B 우선, 없으면 Qwen2.5 폴백."""
        try:
            r = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if r.status_code == 200:
                names = [m["name"] for m in r.json().get("models", [])]
                for candidate in [
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

    def _ollama_generate(self, system: str, user: str, model: str) -> str:
        # think는 명시하지 않음(기본=추론 ON). 리뷰 Q&A(chat)·LLM Debate는 분석형이라
        # 근거 있는 답을 위해 추론이 필요. Vision OCR만 _ollama_multimodal에서 think=False.
        payload = {
            "model": model,
            "prompt": user,
            "system": system,
            "keep_alive": -1,  # 모델 메모리 영구 상주 — evict 후 재로드(~20GB)로 인한 지연/변동 방지
            "options": {"temperature": 0.1, "num_predict": 2048},
        }
        try:
            # read_timeout: 첫 토큰까지(콜드 로드 포함) 최대 대기. 이후 토큰 간격은 짧음.
            return self._ollama_stream(payload, read_timeout=600)
        except Exception as e:
            logger.error("Ollama generate error: %s", e)
            return ""

    def _ollama_multimodal(self, prompt: str, image_b64: str, model: str) -> str:
        """Ollama multimodal — 이미지 + 텍스트 → 응답 (Gemma 4 31B).

        /api/chat 사용 (OpenWebUI와 동일 경로). /api/generate는 Gemma 4 비전에서
        이미지를 평가만 하고 생성 토큰을 0개 내는 경우가 있어 빈 응답이 나옴.
        핀맵 추출 전용: num_predict를 낮춰 생성 토큰을 제한 (속도 핵심).
        스트리밍으로 호출 — 이미지 인코딩이 오래 걸려도 첫 토큰만 read_timeout 안에 오면 OK.
        """
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": True,
            "think": False,   # 추론 비활성화 — 모든 토큰을 thinking이 아닌 content(CSV)로
            "keep_alive": -1,  # 모델 메모리 영구 상주 — evict 후 재로드(~20GB)로 인한 지연/변동 방지
            "options": {"temperature": 0.1, "num_predict": 2048},  # 핀맵 CSV는 핀 수만큼 길어질 수 있음
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
        self, image_b64: str, prompt: str, chip_hint: str = ""
    ) -> Tuple[str, str]:
        """Gemma 4 31B 멀티모달로 회로도 이미지 → (pinmap_csv, vision_analysis).

        pinmap_csv: chip,pin,function,label 형식 CSV 문자열
        vision_analysis: 이미지에서 추출한 초기 분석 텍스트 (한국어)
        """
        vision_prompt = (
            "You are an expert at reading STM32 motor driver schematics and extracting pin assignments.\n"
            f"Target chip: {chip_hint or 'STM32G4'}\n"
            f"User requirements: {prompt}\n\n"
            "Output EXACTLY in this format (no extra commentary):\n\n"
            "CHIP: <chip name, e.g. STM32G474RET6>\n\n"
            "CSV:\n"
            "chip,pin,function,label\n"
            "<chip>,<pin>,<function>,<label>\n"
            "... (all visible pins)\n\n"
            "SUMMARY:\n"
            "<2-3 sentences: key peripherals used (e.g. TIM1 6-ch complementary PWM, OPAMP current sense, FDCAN). "
            "No analysis or problem identification — that is done in a later stage.>\n\n"
            "Rules:\n"
            "- function must use STM32 HAL notation: TIM1_CH1, FDCAN1_TX, OPAMP1_VOUT, ADC1_IN1, etc.\n"
            "- Include only pins clearly visible in the image.\n"
            "- CSV section: header + data rows only, no prose."
        )

        logger.info("Vision extraction 시작 (model=%s)", GEMMA4_VISION_MODEL)
        raw = self._ollama_multimodal(vision_prompt, image_b64, GEMMA4_VISION_MODEL)

        if not raw or len(raw.strip()) < 30:
            logger.warning("Vision extraction 응답 없음 또는 너무 짧음 (len=%d)", len(raw or ""))
            return "", ""

        # CHIP 파싱
        chip_match = re.search(r"CHIP:\s*(STM32G\w+)", raw, re.IGNORECASE)
        extracted_chip = chip_match.group(1).upper() if chip_match else chip_hint

        # CSV 섹션 추출 — 가변 줄바꿈 허용, 대소문자 무관
        csv_match = re.search(
            r"CSV:?\s*\n(.*?)(?:\n+(?:SUMMARY|ANALYSIS):|\Z)",
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

        # SUMMARY(또는 구버전 ANALYSIS) 섹션 추출
        analysis_match = re.search(r"(?:SUMMARY|ANALYSIS):\s*\n(.*)", raw, re.DOTALL)
        vision_analysis = analysis_match.group(1).strip() if analysis_match else raw[:300]

        logger.info(
            "Vision extraction 완료 — chip=%s, csv_lines=%d",
            extracted_chip,
            len(pinmap_csv.splitlines()),
        )
        return pinmap_csv, vision_analysis

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

        # --- 제어 방식
        if "foc" in prompt.lower():
            req.control_type = "FOC"
        elif "6step" in prompt.lower() or "6 step" in prompt.lower():
            req.control_type = "BLDC_6step"

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

        # LLM 보완 파싱 시도
        try:
            req = self._llm_enhance_requirements(req)
        except Exception as e:
            logger.warning("LLM requirements enhance failed: %s", e)

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
        raw = self._ollama_generate(system, user, model)

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
        required_opamp = requirements.motor_count * 3 if requirements.current_sense == "internal_opamp" else 0
        if required_opamp > opamp_max:
            errors.append(
                f"OPAMP 초과: {family} 최대 {opamp_max}개, "
                f"FOC {requirements.motor_count}모터 × 3채널 = {required_opamp}개 필요. "
                "모터 수를 줄이거나 외부 OPAMP로 변경하세요."
            )
        elif required_opamp > 0 and opamp_count < required_opamp:
            warnings.append(
                f"OPAMP 부족 가능성: 핀맵에 OPAMP_VOUT {opamp_count}개 정의, "
                f"FOC {requirements.motor_count}모터에는 {required_opamp}개 필요."
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
                func = str(row["function"]).upper()
                if pin in self.pin_af_db:
                    valid_funcs = list(self.pin_af_db[pin].keys())
                    if func not in valid_funcs and func != "GPIO" and not func.startswith("ADC") and not func.startswith("DAC"):
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
        answer = self._ollama_generate(system_prompt, user_prompt, model)
        if not answer:
            answer = "Ollama 서버에 연결할 수 없거나 모델이 로드되지 않았습니다."

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

    def run(self, request: ReviewRequest) -> ReviewReport:
        """메인 실행 — ReviewRequest → ReviewReport.

        흐름:
          [Vision]  이미지 → pinmap CSV + 초기 분석  (이미지 제공 시)
          [A]       Rule Engine 결정론 검증
          [B]       Hybrid RAG (Rule Engine 키워드 + Vision 분석으로 쿼리 보강)
          [C]       LLM Persona Debate (Vision 분석 포함 전체 컨텍스트)
        """
        logger.info("ReviewAgent.run() chip=%s, has_image=%s", request.chip, bool(request.schematic_image_b64))

        vision_analysis = ""

        # ── [Vision] 이미지가 있으면 pinmap 추출 ──────────────────────────
        if request.schematic_image_b64:
            _t0 = self._stage_start("vision")
            csv_from_vision, vision_analysis = self._vision_extract_pinmap(
                request.schematic_image_b64,
                request.prompt,
                chip_hint=request.chip,
            )
            self._stage_done("vision", _t0)
            self._save_partial(vision_analysis=vision_analysis, extracted_csv=csv_from_vision)
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

        rag_query = (
            f"STM32G4 {request.chip} 핀 AF 검증 "
            f"FOC PWM TIM1 TIM8 OPAMP FDCAN 규칙"
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
            rag_context_preview=rag_context[:500],
        )

        # ── [C] LLM Persona Debate ─────────────────────────────────────────
        if self._is_cancelled():
            raise InterruptedError("cancelled")

        logger.info("[STAGE] llm:start")
        _t0 = self._stage_start("llm")
        llm_errors, llm_warnings, llm_suggestions = self._llm_validate(
            request, requirements, pinmap_df, rag_context, vision_analysis
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
    ) -> Tuple[List[str], List[str], List[str]]:
        """LLM 핀 검증 — errors, warnings, suggestions 반환."""
        model = self._available_model()

        system = """You are an expert STM32G4 hardware validation engineer.
Analyze the provided pinmap CSV and requirements, then output ONLY a JSON object with:
{
  "errors": ["..."],
  "warnings": ["..."],
  "suggestions": ["..."]
}
Rules:
- errors: critical issues that BLOCK firmware generation (wrong AF, pin conflict, resource exceeded)
- warnings: non-critical issues the developer should review
- suggestions: optimization recommendations
- Write in Korean.
- Be specific: include pin names and peripheral names.
- Do not repeat issues already listed in the rule engine output."""

        vision_section = ""
        if vision_analysis:
            vision_section = f"\nVision 초기 분석 (이미지에서 추출):\n{vision_analysis}\n"

        user = f"""Chip: {request.chip}
Requirements (parsed):
{requirements.model_dump_json(indent=2)}
{vision_section}
Pinmap CSV:
{pinmap_df.to_csv(index=False)}

Reference documents (RAG):
{rag_context}

Validate the pinmap and return JSON."""

        raw = self._ollama_generate(system, user, model)
        if not raw:
            return [], [], ["LLM 검증 연결 실패 — 규칙 엔진 결과만 사용됩니다."]

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
