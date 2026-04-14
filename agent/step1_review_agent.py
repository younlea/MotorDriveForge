"""
Step 1 Review Agent — STM32G4 핀 검증 + 요구사항 파싱
입력: ReviewRequest (chip, pinmap_csv, prompt)
출력: ReviewReport (errors, warnings, suggestions, validated_pins)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    chip: str = Field(..., description="예: STM32G474RET6")
    pinmap_csv: str = Field(..., description="CSV 문자열 (chip,pin,function,label 컬럼)")
    prompt: str = Field(..., description="자연어 요구사항 프롬프트")


class ReviewReport(BaseModel):
    chip: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    validated_pins: Dict[str, Any] = Field(default_factory=dict)


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
        """Ollama에 로드된 모델 확인 — 72b 우선, 없으면 7b 폴백."""
        try:
            r = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if r.status_code == 200:
                names = [m["name"] for m in r.json().get("models", [])]
                for candidate in ["qwen2.5:72b", "qwen2.5:32b", "qwen2.5:7b", "qwen2.5"]:
                    if any(candidate in n for n in names):
                        return candidate
        except Exception:
            pass
        return "qwen2.5:7b"

    def _ollama_generate(self, system: str, user: str, model: str) -> str:
        payload = {
            "model": model,
            "prompt": user,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 2048},
        }
        try:
            r = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=120,
            )
            r.raise_for_status()
            return r.json().get("response", "")
        except Exception as e:
            logger.error("Ollama generate error: %s", e)
            return ""

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
            # 기존 req에 LLM 결과 병합 (non-empty 값만)
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

        # 1. TIM1/TIM8 핀 충돌: PB0/PB1 은 TIM1_CH2N/CH3N 이면서 TIM8_CH2N/CH3N AF가 다름
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

        # 3. BRK 핀 공유 여부 (모터별 독립 보호 불가)
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

    def _embed(self, text: str) -> List[float]:
        """Ollama embedding API — BGE-M3 또는 기본 임베딩."""
        try:
            r = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": "bge-m3", "prompt": text},
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()["embedding"]
        except Exception:
            pass
        # 폴백: 0 벡터 (검색 불가, 오류 방지용)
        return [0.0] * 1024

    def run(self, request: ReviewRequest) -> ReviewReport:
        """메인 실행 — ReviewRequest → ReviewReport."""
        logger.info("ReviewAgent.run() chip=%s", request.chip)

        # 1. CSV 파싱
        try:
            from io import StringIO
            pinmap_df = pd.read_csv(StringIO(request.pinmap_csv))
            pinmap_df.columns = [c.strip().lower() for c in pinmap_df.columns]
        except Exception as e:
            return ReviewReport(
                chip=request.chip,
                errors=[f"CSV 파싱 오류: {e}"],
            )

        # 2. 요구사항 파싱
        requirements = self.parse_prompt(request.prompt)
        if not requirements.chip:
            requirements.chip = request.chip

        # 3. RAG 컨텍스트 수집
        rag_query = (
            f"STM32G4 {request.chip} 핀 AF 검증 "
            f"FOC PWM TIM1 TIM8 OPAMP FDCAN 규칙"
        )
        rag_docs = self.rag_query(rag_query, top_k=5)
        rag_context = "\n\n---\n\n".join(rag_docs[:5]) if rag_docs else "(RAG 없음)"

        # 4. 규칙 엔진 검증
        rule_errors, rule_warnings = self.validate_pins_rules(pinmap_df, requirements)

        # 5. LLM 검증
        llm_errors, llm_warnings, llm_suggestions = self._llm_validate(
            request, requirements, pinmap_df, rag_context
        )

        # 6. 결과 합산 (중복 제거)
        all_errors = list(dict.fromkeys(rule_errors + llm_errors))
        all_warnings = list(dict.fromkeys(rule_warnings + llm_warnings))
        all_suggestions = list(dict.fromkeys(llm_suggestions))

        # 7. 확정 핀 JSON 생성
        validated_pins = self._build_validated_pins(pinmap_df, requirements)

        return ReviewReport(
            chip=request.chip,
            errors=all_errors,
            warnings=all_warnings,
            suggestions=all_suggestions,
            validated_pins=validated_pins,
        )

    def _llm_validate(
        self,
        request: ReviewRequest,
        requirements: RequirementsDict,
        pinmap_df: pd.DataFrame,
        rag_context: str,
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

        user = f"""Chip: {request.chip}
Requirements (parsed):
{requirements.model_dump_json(indent=2)}

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
