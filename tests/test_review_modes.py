"""
Task 01 — fast/full 모드 분리 단위 테스트
Ollama/Qdrant 없이 Rule Engine 레벨만 검증.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.step1_review_agent import ReviewAgent, ReviewRequest


def _make_agent() -> ReviewAgent:
    return ReviewAgent(
        ollama_url="http://localhost:11434",
        qdrant_url="http://localhost:6333",
    )


INVALID_PINMAP_CSV = """chip,pin,function,label
STM32G474RET6,PA8,TIM1_CH1,U_PWM_H
STM32G474RET6,PC6,TIM8_CH1,U2_PWM_H
STM32G474RET6,PB0,TIM1_CH2N,TIM1_CH2N_PIN
STM32G474RET6,PB1,TIM1_CH3N,TIM1_CH3N_PIN
"""

VALID_PINMAP_CSV = """chip,pin,function,label
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

PROMPT = (
    "STM32G474RET6 FOC 1모터, 내부 OPAMP, FDCAN 1Mbps, 데드타임 500ns, 시스템 170MHz"
)


def test_fast_mode_skips_llm_on_error():
    """fast 모드: 오류 핀맵도 LLM 없이 Rule Engine 결과만 즉시 반환."""
    agent = _make_agent()
    req = ReviewRequest(
        chip="STM32G474RET6",
        pinmap_csv=INVALID_PINMAP_CSV,
        prompt=PROMPT,
        mode="fast",
    )
    report = agent.run(req)
    # fast 모드에선 errors가 있어야 함 (TIM1/TIM8 충돌)
    assert len(report.errors) > 0, "fast 모드에서 TIM1/TIM8 충돌 오류가 감지되어야 합니다"
    # LLM이 호출되지 않으므로 suggestions는 Rule Engine 결과만 (빈 리스트)
    assert report.chip == "STM32G474RET6"


def test_full_mode_returns_200_on_error():
    """full 모드: errors 있어도 report 반환 (HTTP 200 — LLM 설명 포함 가능)."""
    agent = _make_agent()
    req = ReviewRequest(
        chip="STM32G474RET6",
        pinmap_csv=INVALID_PINMAP_CSV,
        prompt=PROMPT,
        mode="full",
    )
    # full 모드에선 예외 없이 report 반환 (LLM/RAG 연결 실패 시 suggestion에 메시지)
    report = agent.run(req)
    assert report is not None
    assert report.chip == "STM32G474RET6"
    # errors는 Rule Engine이 감지한 것들 (LLM 추가도 가능)
    assert isinstance(report.errors, list)


def test_fast_mode_valid_pinmap_no_errors():
    """fast 모드: 올바른 핀맵은 errors 없이 통과."""
    agent = _make_agent()
    req = ReviewRequest(
        chip="STM32G474RET6",
        pinmap_csv=VALID_PINMAP_CSV,
        prompt=PROMPT,
        mode="fast",
    )
    report = agent.run(req)
    tim_errors = [e for e in report.errors if "TIM1/TIM8" in e or "OPAMP 초과" in e or "DMA" in e]
    assert len(tim_errors) == 0, f"유효 핀맵에서 예상치 못한 오류: {tim_errors}"


def test_mode_field_default():
    """ReviewRequest 기본 모드는 'full'."""
    req = ReviewRequest(chip="STM32G474RET6", prompt="test")
    assert req.mode == "full"
