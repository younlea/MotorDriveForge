"""
Step 3 코드젠 에이전트
결정론적 Golden Module 선택 → LLM 핀맵 적응 → USER CODE 마커 주입
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

GOLDEN_DIR = Path(__file__).parent.parent / "golden_modules"

# ---------------------------------------------------------------------------
# 결정론적 모듈 선택 규칙
# ---------------------------------------------------------------------------

def select_modules(vp: Dict[str, Any]) -> List[str]:
    """validated_pins → 적용할 golden module 이름 목록 (LLM 없음)."""
    control = vp.get("control_type", "").upper()
    encoder = vp.get("encoder_type", "")
    comms   = [c.lower() for c in vp.get("comms", [])]
    n_motor = vp.get("motor_count", 1)

    selected: List[str] = []

    # 모터 제어 기반 모듈
    if control in ("BLDC_6STEP", "BLDC") and encoder == "hall":
        selected.append("bldc_6step_hall")
    else:
        # FOC / PMSM / DC 등 모두 dc_motor_pid 기반
        selected.append("dc_motor_pid")

    # 통신 모듈
    if "fdcan" in comms:
        selected.append("fdcan_motor_cmd")

    # 다축 동기화
    if n_motor > 1:
        selected.append("multi_axis_sync")

    return selected


def _read_module(name: str) -> Dict[str, str]:
    """golden module .c / .h 파일 읽기. 없으면 빈 dict."""
    result: Dict[str, str] = {}
    for ext in ("c", "h"):
        path = GOLDEN_DIR / f"{name}.{ext}"
        if path.exists():
            result[ext] = path.read_text(encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# 핀맵 요약 (LLM 컨텍스트용)
# ---------------------------------------------------------------------------

def _pinmap_summary(vp: Dict[str, Any]) -> str:
    pins = vp.get("pins", [])
    timers: Dict[str, List[str]] = {}
    opamps: List[str] = []

    for p in pins:
        func = p.get("function", "")
        pin  = p.get("pin", "")
        lbl  = p.get("label", "")
        if func.startswith("TIM"):
            tim = func.split("_")[0]
            timers.setdefault(tim, []).append(f"{pin}={func}({lbl})")
        if "OPAMP" in func:
            opamps.append(f"{pin}={func}")

    pwm_timer = next((t for t in timers if t in ("TIM1", "TIM8")), "TIM1")
    enc_timer = next(
        (t for t in timers if t not in ("TIM1", "TIM8") and t.startswith("TIM")), "TIM3"
    )

    lines = [
        f"칩: {vp.get('chip', '')}",
        f"시스템 클럭: {vp.get('clock_mhz', 170)} MHz",
        f"크리스탈: {vp.get('crystal_mhz', 24)} MHz",
        f"PWM 타이머: {pwm_timer} (데드타임 {vp.get('deadtime_ns', 500)} ns, "
        f"제어방식: {vp.get('control_type', 'FOC')})",
        f"엔코더 타이머: {enc_timer} (센서: {vp.get('encoder_type', 'incremental')})",
        f"전류 센싱: {vp.get('current_sense', 'internal_opamp')}",
        f"OPAMP 핀: {', '.join(opamps) if opamps else '없음'}",
        f"통신: {vp.get('comms', [])}",
        f"모터 수: {vp.get('motor_count', 1)}",
        "",
        "핀 배치:",
    ]
    for p in pins:
        lines.append(f"  {p.get('pin','')} = {p.get('function','')}  [{p.get('label','')}]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM 적응
# ---------------------------------------------------------------------------

def _parse_code_blocks(response: str) -> List[str]:
    """LLM 응답에서 ```c ... ``` 블록 순서대로 추출."""
    return [b.strip() for b in re.findall(r"```(?:c|cpp)?\s*(.*?)```", response, re.DOTALL)]


class Step3Agent:
    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url

    def _available_model(self) -> str:
        try:
            r = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if r.status_code == 200:
                names = [m["name"] for m in r.json().get("models", [])]
                for pref in ("gemma4:31b", "gemma4", "qwen2.5:32b", "qwen2.5:7b", "qwen2.5"):
                    for n in names:
                        if n.startswith(pref.split(":")[0]):
                            return n
        except Exception:
            pass
        return "gemma4:31b"

    def _llm_adapt(
        self,
        module_name: str,
        code_h: str,
        code_c: str,
        pinmap: str,
        model: str,
        user_prompt: str = "",
    ) -> Dict[str, str]:
        """Golden Module 코드를 핀맵에 맞게 LLM으로 적응.
        핵심 로직은 유지하고 하드웨어 종속 부분(핀/타이머/OPAMP/ADC)만 교체.
        """
        system = (
            "당신은 STM32G4 HAL 펌웨어 코드 적응 전문가입니다. "
            "제공된 Golden Module 코드를 사용자의 실제 핀맵·타이머·OPAMP 설정에 맞게 수정하세요. "
            "핵심 알고리즘 로직(PID, FOC, 6-step 등)은 절대 변경하지 마세요. "
            "하드웨어 종속 부분(핀 이름, 타이머 인스턴스, ADC/OPAMP 번호)만 교체하세요. "
            "수정된 .h 파일을 첫 번째 ```c 블록에, .c 파일을 두 번째 ```c 블록에 출력하세요."
        )

        user = f"""## 사용자 알고리즘 요구사항
{user_prompt if user_prompt.strip() else "(별도 요구사항 없음 — 핀맵 기반 적응만 수행)"}

## 사용자 핀맵 정보
{pinmap}

## Golden Module 원본: {module_name}.h
```c
{code_h}
```

## Golden Module 원본: {module_name}.c
```c
{code_c}
```

## 수행 지시사항
1. TIM 핸들(htim1, htim3 등)을 핀맵의 실제 PWM/엔코더 타이머로 교체
2. OPAMP 번호(hopamp1, hopamp2, hopamp3)를 실제 OPAMP 핀에 맞게 교체
3. ADC 채널 번호를 실제 핀에 맞게 교체
4. GPIO 포트/핀 상수를 실제 핀맵으로 교체
5. 로직(알고리즘, 수식, 상태머신)은 변경하지 않음

위 지시에 따라 수정된 {module_name}.h와 {module_name}.c를 순서대로 출력하세요."""

        payload = {
            "model": model,
            "prompt": user,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.05, "num_predict": 8192},
        }
        try:
            r = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=300,
            )
            r.raise_for_status()
            blocks = _parse_code_blocks(r.json().get("response", ""))
            return {
                "h": blocks[0] if len(blocks) >= 1 else code_h,
                "c": blocks[1] if len(blocks) >= 2 else code_c,
            }
        except Exception as e:
            logger.error("LLM adapt failed [%s]: %s", module_name, e)
            return {"h": code_h, "c": code_c}  # fallback: 원본 반환

    # ---------------------------------------------------------------------------
    # USER CODE 마커 주입
    # ---------------------------------------------------------------------------

    @staticmethod
    def inject_into_marker(hal_src: str, adapted_code: str, marker: str = "2") -> str:
        """HAL 소스의 USER CODE BEGIN {marker} 마커 사이에 코드 주입."""
        pattern = (
            rf"(/\* USER CODE BEGIN {re.escape(marker)} \*/)"
            rf"(.*?)"
            rf"(/\* USER CODE END {re.escape(marker)} \*/)"
        )
        replacement = rf"\1\n{adapted_code}\n\3"
        result = re.sub(pattern, replacement, hal_src, flags=re.DOTALL)
        return result

    # ---------------------------------------------------------------------------
    # 메인 파이프라인
    # ---------------------------------------------------------------------------

    def run(
        self,
        vp: Dict[str, Any],
        prompt: str = "",
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Step 3 전체 파이프라인.

        Args:
            vp: validated_pins dict
            progress_cb: (percent: int, message: str) → None

        Returns:
            {
                "selected": [module_name, ...],
                "modules": {
                    module_name: {"h": str, "c": str, "original_h": str, "original_c": str}
                },
                "pinmap_summary": str,
            }
        """
        def _cb(pct: int, msg: str) -> None:
            logger.info("[Step3 %3d%%] %s", pct, msg)
            if progress_cb:
                progress_cb(pct, msg)

        # ── 1. 모듈 선택 ─────────────────────────────────────────────────
        _cb(5, "Golden Module 선택 중...")
        selected = select_modules(vp)
        _cb(10, f"선택 완료: {', '.join(selected)}")

        # ── 2. 핀맵 요약 ─────────────────────────────────────────────────
        pinmap = _pinmap_summary(vp)
        model = self._available_model()
        _cb(15, f"LLM 모델: {model}")

        # ── 3. 각 모듈 LLM 적응 ──────────────────────────────────────────
        modules_out: Dict[str, Dict[str, str]] = {}
        n = max(len(selected), 1)
        step = 70 // n

        for i, name in enumerate(selected):
            base_pct = 15 + i * step
            _cb(base_pct, f"[{i+1}/{n}] {name} 적응 중...")

            raw = _read_module(name)
            if not raw:
                logger.warning("Golden module 파일 없음: %s", name)
                _cb(base_pct + step, f"{name} 건너뜀 (파일 없음)")
                continue

            adapted = self._llm_adapt(
                name,
                raw.get("h", ""),
                raw.get("c", ""),
                pinmap,
                model,
                user_prompt=prompt,
            )
            modules_out[name] = {
                "h": adapted.get("h", raw.get("h", "")),
                "c": adapted.get("c", raw.get("c", "")),
                "original_h": raw.get("h", ""),
                "original_c": raw.get("c", ""),
            }
            _cb(base_pct + step, f"{name} 완료")

        _cb(95, "결과 정리 완료")

        return {
            "selected": selected,
            "modules": modules_out,
            "pinmap_summary": pinmap,
        }
