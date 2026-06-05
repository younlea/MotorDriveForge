"""
Streamlit MVP UI — STM32G4 Motor Drive Agent
HW 개발자용 3-Step 파이프라인 인터페이스

입력 흐름:
  회로도 이미지 + 자연어 프롬프트
      → Gemma 4 31B Vision 분석 (핀맵 자동 추출)
      → Rule Engine + Hybrid RAG
      → LLM Persona Debate
      → 검증 리포트
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from io import StringIO

_paste_comp = components.declare_component(
    "paste_image",
    path=str(Path(__file__).parent / "paste_component"),
)

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

AUTO_CHIP = "(자동 — 회로도에서 감지)"
CHIPS = [
    AUTO_CHIP,
    "STM32G431RBT6",
    "STM32G431RBI6",
    "STM32G474RET6",
    "STM32G491RET6",
    "STM32G484RET6",
    "STM32G441CBT6",
]

EXAMPLE_PROMPT = (
    "STM32G474RET6 칩을 쓸 거고, 외부 크리스탈 24MHz / 시스템 170MHz야.\n"
    "BLDC 모터 1개를 FOC로 제어할 건데 증분형 엔코더(A/B/Z)로 각도 읽고,\n"
    "3상 6채널 PWM으로 인버터 구동해. 데드타임 500ns, 전류는 내부 OPAMP.\n"
    "통신은 FDCAN 1Mbps 쓰고, 파라미터 저장용으로 SPI EEPROM도 연결할 거야."
)

STEP3_EXAMPLE_PROMPT = (
    "PMSM 모터 1개를 FOC로 제어할 거야. 증분형 엔코더(A/B/Z)로 위치 피드백.\n"
    "전류 센싱은 OPAMP 3개로 3상 샨트. 데드타임 500ns.\n"
    "FDCAN으로 외부 명령 수신하고 속도+위치 캐스케이드 제어 필요해.\n"
    "BRK 핀으로 하드웨어 OCP 처리하고 NTC 온도 모니터링도 추가해줘."
)

EXAMPLE_CSV = """chip,pin,function,label
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
STM32G474RET6,PB4,TIM3_CH1,ENC_A
STM32G474RET6,PB5,TIM3_CH2,ENC_B
STM32G474RET6,PA5,SPI1_SCK,SPI_SCK
STM32G474RET6,PA6,SPI1_MISO,SPI_MISO
STM32G474RET6,PA7,SPI1_MOSI,SPI_MOSI
"""

# ---------------------------------------------------------------------------
# 페이지 설정
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="STM32G4 Motor Drive Agent",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state 초기화
# ---------------------------------------------------------------------------

for key, default in [
    ("review_passed", False),
    ("validated_pins", {}),
    ("ioc_result", None),
    ("last_report", None),
    ("vision_analysis", ""),
    ("extracted_csv", ""),
    ("pasted_images_b64", []),     # Step1: Ctrl+V로 누적한 회로도 여러 장
    ("confirm_pinmap_csv", ""),    # Vision 추출 후 사용자 확정 대기 중인 핀맵 CSV
    ("confirm_chip", ""),          # Vision 감지/입력 칩 (확정 단계 편집용)
    ("confirm_vision_analysis", ""),
    ("confirm_peripherals", ""),   # Vision 추출 외부 부품/연결 (확정 단계 편집용)
    ("_last_pasted_s1", None),     # 직전에 누적한 붙여넣기 값(중복 append 방지)
    ("chat_history", []),
    ("go_to_step2", False),
    ("step2_pasted_image_b64", None),
    ("step2_extracted_pins", None),
    ("code_zip", None),
    ("step3_job_id", None),
    ("step3_result", None),
    ("step3_hal_zip", None),
    ("step3_prompt", ""),
    # 백그라운드 검증 태스크
    ("_rv_running", False),
    ("_rv_result", None),
    ("_rv_error", None),
    ("_rv_start", None),
    ("_rv_cancel", False),
    # 파이프라인 스테이지 (한번 올라간 상태는 내려가지 않음)
    ("_rv_stages", {"vision": "wait", "pinmap": "wait", "rule_engine": "wait", "rag": "wait", "llm": "wait"}),
    ("_rv_rag_warn", False),
    ("_rv_partial_timing", {}),
    # 디버그: 결과 표시 후에도 남길 중간 데이터/로그 스냅샷
    ("_rv_final_partial", {}),
    ("_rv_final_logs", []),
    ("_dev_debug", True),  # 개발 중 디버그 패널 표시 (완료 후 끄기)
]:
    if key not in st.session_state:
        st.session_state[key] = default


def _fetch_pin_options(chip: str) -> dict:
    """백엔드에서 칩의 핀별 function 옵션을 가져온다(세션 캐시)."""
    chip = (chip or "").strip()
    cache = st.session_state.setdefault("_pin_opts_cache", {})
    if chip and chip not in cache:
        try:
            r = requests.get(f"{BACKEND_URL}/v1/pin-options/{chip}", timeout=10)
            cache[chip] = r.json() if r.status_code == 200 else {}
        except Exception:
            cache[chip] = {}
    return cache.get(chip, {})


def _fn_choices(pin: str, cur: str, pin_opts: dict, common: list) -> list:
    """한 핀의 드롭다운 선택지: (미지정) + 핀 AF + GPIO + 기존값 보존."""
    opts = pin_opts.get(pin, [])
    choices = ["(미지정)"] + opts + [c for c in common if c not in opts]
    if cur and cur not in choices:
        choices.insert(1, cur)  # 기존값(SWDIO/FDCAN 등) 유지
    return choices


def _suggest_function(label: str, pin: str, pin_opts: dict) -> str:
    """라벨에서 function 추정 — 그 핀에서 실제 가능한 AF 중에서 고른다(추정값, 검토용).

    매칭 안 되면 "" 반환(미지정). 통신/디버그처럼 명확한 것부터, 모호하면 비움.
    """
    opts = pin_opts.get(pin, [])
    lab = (label or "").upper()
    if not lab:
        return ""

    def pick(pat: str) -> Optional[str]:
        rx = re.compile(pat)
        return next((o for o in opts if rx.match(o)), None)

    rules = [
        # (라벨 패턴, 핀 AF 패턴 또는 GPIO 상수)
        (r"SWDIO", r"SYS_JTMS"),
        (r"SWCLK|SWDCLK", r"SYS_JTCK"),
        (r"SWO|TRACE", r"SYS_JTDO"),
        (r"CAN.?TX|CANTX", r"FDCAN\d+_TX"),
        (r"CAN.?RX|CANRX", r"FDCAN\d+_RX"),
        (r"MISO", r"SPI\d+_MISO"),
        (r"MOSI", r"SPI\d+_MOSI"),
        (r"SCK|SPI.?CLK", r"SPI\d+_SCK"),
        (r"NSS|SPI.?CS|_CS\b|CS_", r"SPI\d+_NSS"),
        (r"SCL", r"I2C\d+_SCL"),
        (r"SDA", r"I2C\d+_SDA"),
        (r"U?ART.?TX|TXD|\bTX\b", r"(US|LPU)?ART\d+_TX"),
        (r"U?ART.?RX|RXD|\bRX\b", r"(US|LPU)?ART\d+_RX"),
        (r"XTAL.?IN|HSE.?IN|OSC.?IN|XTAL.?1|OSC.?1", r"RCC_OSC_IN"),
        (r"XTAL.?OUT|HSE.?OUT|OSC.?OUT|XTAL.?2|OSC.?2", r"RCC_OSC_OUT"),
        # 모터 제어
        (r"ENBL|ENABLE|PWM", r"TIM\d+_CH\d+$"),           # 속도제어 PWM
        (r"ENC|ENCODER|QEI|HALL", r"TIM\d+_CH\d+$"),       # 엔코더 입력
        # 아날로그 (전류/포텐셔미터/전압·온도 센스)
        (r"CURR|ISENS|I_SENS|SENSE|VSENS|V_SENS|POT|POTENTIO|\bAIN\b|ADC|VBUS|VMON|NTC|TEMP",
         r"ADC\d+_IN\d+"),
    ]
    for lab_pat, fn_pat in rules:
        if re.search(lab_pat, lab):
            if fn_pat.startswith("GPIO"):
                return fn_pat
            hit = pick(fn_pat)
            if hit:
                return hit
            # AF 후보가 없을 때의 합리적 폴백
            if "TIM" in fn_pat:
                return "GPIO_Output" if re.search(r"ENBL|ENABLE|PWM", lab) else ""
            if "ADC" in fn_pat:
                return "GPIO_Analog"
            return ""
    # 순수 디지털 IO
    if re.search(r"DIR|DIRECTION|RELAY|LED|\bEN_|_EN\b|STBY|SLEEP|RESET_DRV", lab):
        return "GPIO_Output"
    if re.search(r"FAULT|FLT|nINT|_INT\b|ALERT|RDY|READY|DIAG|\bPG\b|nOC", lab):
        return "GPIO_Input"
    return ""


# ---------------------------------------------------------------------------
# 사이드바 — 연결 상태
# ---------------------------------------------------------------------------

def check_service(url: str, path: str = "", timeout: int = 3) -> bool:
    try:
        r = requests.get(f"{url}{path}", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


@st.cache_data(ttl=15, show_spinner=False)
def _cached_check_services() -> dict:
    return {
        "backend": check_service(BACKEND_URL, "/v1/health", timeout=2),
        "ollama":  check_service(OLLAMA_URL,  "/api/tags",   timeout=2),
        "qdrant":  check_service(QDRANT_URL,  "/collections", timeout=2),
    }


@st.cache_data(ttl=30, show_spinner=False)
def _cached_status() -> dict:
    try:
        r = requests.get(f"{BACKEND_URL}/v1/status", timeout=4)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def status_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


REVIEW_TIMEOUT = 3600  # 사실상 무제한 (중단 버튼으로 수동 취소)


def _circular_progress_html(pct: int, elapsed: float, remaining: float) -> str:
    r = 54
    circ = 2 * 3.14159 * r
    offset = circ * (1 - pct / 100)
    me, se = divmod(int(elapsed), 60)
    mr, sr = divmod(int(remaining), 60)
    start_ts = time.strftime("%H:%M:%S", time.localtime(time.time() - elapsed))
    return f"""
<div style="display:flex;align-items:center;gap:28px;padding:14px 0 10px 0">
  <svg width="120" height="120" viewBox="0 0 120 120">
    <circle cx="60" cy="60" r="{r}" fill="none" stroke="#e8e8e8" stroke-width="9"/>
    <circle cx="60" cy="60" r="{r}" fill="none" stroke="#ff4b4b" stroke-width="9"
      stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.1f}"
      stroke-linecap="round" transform="rotate(-90 60 60)"/>
    <text x="60" y="55" text-anchor="middle" font-size="21" font-weight="bold" fill="#222">{pct}%</text>
    <text x="60" y="73" text-anchor="middle" font-size="10" fill="#999">진행중</text>
  </svg>
  <div style="line-height:1.9;font-family:monospace">
    <div style="font-size:13px;color:#555">시작　<b>{start_ts}</b></div>
    <div style="font-size:15px;color:#222">경과　<b>{me:02d}:{se:02d}</b></div>
    <div style="font-size:15px;color:#e07000">만료　<b>{mr:02d}:{sr:02d}</b></div>
    <div style="font-size:12px;color:#aaa;margin-top:6px">Gemma 4 31B 분석 중…</div>
  </div>
</div>"""


_STATE_RANK = {"wait": 0, "active": 1, "done": 2}


def _update_stages_from_partial(pr: dict) -> None:
    """현재 실행의 partial(매 실행마다 백엔드가 비움)로 스테이지 갱신.

    /v1/logs는 누적 버퍼라 이전 실행의 완료 마커가 남아 새 실행을 즉시 '완료'로
    오인함 → partial.timing(이번 실행 전용)을 근거로 사용해 그 문제를 회피.
    timing[key] = {elapsed:None} → 진행중(active), elapsed 값 있음 → 완료(done), 없음 → 대기.
    """
    st_ = st.session_state._rv_stages
    timing = pr.get("timing", {}) or {}

    def _state_of(key: str):
        t = timing.get(key)
        if not isinstance(t, dict):
            return None
        return "done" if t.get("elapsed") is not None else "active"

    def _upgrade(uikey: str, state):
        if state and _STATE_RANK[state] > _STATE_RANK[st_[uikey]]:
            st_[uikey] = state

    _upgrade("vision", _state_of("vision"))
    # 핀맵: vision 완료/CSV 추출되면 done, vision 진행중이면 active
    if pr.get("extracted_csv") or _state_of("vision") == "done":
        _upgrade("pinmap", "done")
    elif _state_of("vision") == "active":
        _upgrade("pinmap", "active")
    _upgrade("rule_engine", _state_of("rule_engine"))
    _upgrade("rag", _state_of("rag"))
    _upgrade("llm", _state_of("llm"))
    # RAG 검색 결과가 0이면 경고 표시
    if _state_of("rag") == "done" and pr.get("rag_docs_count") == 0:
        st.session_state._rv_rag_warn = True


def _pipeline_status_html() -> str:
    st_ = st.session_state._rv_stages
    rag_warn = st.session_state._rv_rag_warn

    timing = st.session_state.get("_rv_partial_timing", {})

    def _fmt_elapsed(key: str) -> str:
        t = timing.get(key, {})
        e = t.get("elapsed")
        if e is None:
            return ""
        m, s = divmod(int(e), 60)
        return f"{m}:{s:02d}" if m else f"{s:.0f}s"

    def _box(label: str, key: str, warn: bool = False) -> str:
        state = st_[key]
        colors = {
            "done":   ("#e8f5e9", "#2e7d32", "#43a047"),
            "active": ("#fff8e1", "#f57f17", "#ffa726"),
            "wait":   ("#f5f5f5", "#9e9e9e", "#bdbdbd"),
        }
        bg, text, border = colors[state]
        icon = {"done": "✅", "active": "⏳", "wait": "⬜"}[state]
        if warn and state == "done":
            bg, text, border = "#fff3e0", "#e65100", "#ff9800"
            icon = "⚠️"
        elapsed_str = _fmt_elapsed(key)
        time_html = (
            f'<div style="font-size:10px;color:{text};margin-top:2px">{elapsed_str}</div>'
            if elapsed_str else ""
        )
        return (
            f'<div style="background:{bg};border:2px solid {border};border-radius:10px;'
            f'padding:10px 14px;min-width:110px;text-align:center;'
            f'box-shadow:0 1px 4px rgba(0,0,0,.1)">'
            f'<div style="font-size:20px">{icon}</div>'
            f'<div style="font-size:12px;font-weight:bold;color:{text};margin-top:4px">{label}</div>'
            f'{time_html}'
            f'</div>'
        )

    arrow = '<div style="font-size:22px;color:#bdbdbd;align-self:center">→</div>'
    boxes = [
        _box("이미지 분석",  "vision"),
        arrow,
        _box("핀맵 추론",   "pinmap"),
        arrow,
        _box("Rule Engine", "rule_engine"),
        arrow,
        _box("RAG 검색",    "rag", warn=rag_warn),
        arrow,
        _box("LLM 리뷰",    "llm"),
    ]
    inner = "\n".join(boxes)
    return (
        f'<div style="display:flex;align-items:stretch;gap:8px;'
        f'padding:12px 0;flex-wrap:nowrap;overflow-x:auto">'
        f'{inner}</div>'
    )


def _run_review_bg(data: dict, files: dict) -> None:
    try:
        r = requests.post(
            f"{BACKEND_URL}/v1/review",
            data=data,
            files=files if files else None,
            timeout=None,
        )
        if not st.session_state._rv_cancel:
            st.session_state._rv_result = r
    except Exception as e:
        if not st.session_state._rv_cancel:
            st.session_state._rv_error = str(e)
    finally:
        st.session_state._rv_running = False


def _begin_review(data: dict, files: list) -> None:
    """검증 백그라운드 시작 + 이전 결과/상태 초기화 (직접 CSV 경로·확정 경로 공용)."""
    st.session_state._rv_running = True
    st.session_state._rv_result = None
    st.session_state._rv_error = None
    st.session_state._rv_cancel = False
    st.session_state._rv_start = time.time()
    st.session_state._rv_stages = {"vision": "wait", "pinmap": "wait", "rule_engine": "wait", "rag": "wait", "llm": "wait"}
    st.session_state._rv_rag_warn = False
    st.session_state["_rv_partial_timing"] = {}
    # 이전 검증 결과/화면 초기화
    st.session_state.last_report = None
    st.session_state.review_passed = False
    st.session_state.validated_pins = {}
    st.session_state.vision_analysis = ""
    st.session_state.extracted_csv = ""
    st.session_state._rv_final_partial = {}
    st.session_state._rv_final_logs = []
    st.session_state.chat_history = []
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
        t = threading.Thread(target=_run_review_bg, args=(data, files), daemon=True)
        add_script_run_ctx(t, ctx)
    except Exception:
        t = threading.Thread(target=_run_review_bg, args=(data, files), daemon=True)
    t.start()


with st.sidebar:
    st.title("STM32G4 Agent")
    st.caption("Motor Drive Firmware 자동화")
    st.divider()

    st.subheader("서비스 연결 상태")

    if st.button("새로고침", key="refresh_status"):
        st.rerun()

    _svc = _cached_check_services()
    backend_ok = _svc["backend"]
    ollama_ok  = _svc["ollama"]
    qdrant_ok  = _svc["qdrant"]

    st.write(f"{status_icon(backend_ok)} **Backend** (`{BACKEND_URL}`)")
    st.write(f"{status_icon(ollama_ok)} **Ollama** (`:{11434}`)")
    st.write(f"{status_icon(qdrant_ok)} **Qdrant** (`:{6333}`)")

    # 검증 중에는 status 조회 생략 (backend 부하 방지)
    if backend_ok and not st.session_state._rv_running:
        _st = _cached_status()
        if _st.get("ollama_models"):
            st.caption(f"모델: {', '.join(_st['ollama_models'][:2])}")
        if _st.get("qdrant_collections"):
            st.caption(f"컬렉션: {', '.join(_st['qdrant_collections'])}")

    st.divider()
    st.caption("3-Step 파이프라인")
    step1_badge = "🟢" if st.session_state.review_passed else "⚪"
    st.write(f"{step1_badge} Step 1: 핀 검증")
    step2_badge = "🟢" if st.session_state.ioc_result else ("🟡" if st.session_state.review_passed else "⚪")
    st.write(f"{step2_badge} Step 2: 코드 생성")
    st.write("⚪ Step 3: 알고리즘 통합")

    st.divider()
    st.session_state._dev_debug = st.checkbox(
        "🛠 개발자 디버그 패널",
        value=st.session_state._dev_debug,
        help="결과 표시 후에도 인식된 입력값·Rule Engine·RAG·서버 로그를 접이식으로 노출합니다. 운영 시 끄세요.",
    )
    st.caption("MotorDriveForge v2.0")


# ---------------------------------------------------------------------------
# 메인 영역
# ---------------------------------------------------------------------------

st.title("STM32G4 Motor Drive Agent")
st.caption("회로도 이미지 + 자연어 프롬프트 → Vision 분석 → 핀 검증 → HAL 코드 → 알고리즘 통합")

tab1, tab2, tab3 = st.tabs(["Step 1  핀 검증", "Step 2  코드 생성", "Step 3  알고리즘 통합"])


# ===========================================================================
# Tab 1 — Step 1 핀 검증
# ===========================================================================

with tab1:
    st.header("Step 1 — 핀 검증")
    st.caption(
        "회로도 이미지를 올리면 Gemma 4 31B가 핀맵을 자동 추출합니다. "
        "핀맵 CSV를 직접 넣을 수도 있습니다."
    )

    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        chip = st.selectbox(
            "칩 선택", CHIPS, index=0,
            help="명시 선택하지 않으면(자동) 회로도 Vision 추출 단계에서 감지된 칩을 확인·수정합니다.",
        )

        st.subheader("입력 방식")

        # ── 이미지 업로드 (주 입력, 여러 장 가능) ──────────────────────────
        st.markdown("**회로도 이미지** (권장 — Gemma 4 31B Vision 자동 분석, 여러 장 가능)")
        schematic_files = st.file_uploader(
            "회로도 이미지 업로드",
            type=["jpg", "jpeg", "png", "bmp", "webp"],
            help="JPEG/PNG 회로도. 여러 장 올리면 한 설계의 멀티 시트로 종합 분석합니다.",
            label_visibility="collapsed",
            accept_multiple_files=True,
        )
        schematic_files = schematic_files or []
        if schematic_files:
            st.caption(f"업로드된 회로도 {len(schematic_files)}장")
            for _img in schematic_files:
                st.image(_img, caption=_img.name, use_container_width=True)

        st.divider()

        # ── CSV 보조 입력 ──────────────────────────────────────────────────
        with st.expander("핀맵 CSV 직접 입력 (선택 — 이미지 없을 때 필수)", expanded=not bool(schematic_files)):
            csv_input_mode = st.radio(
                "CSV 입력 방식", ["파일 업로드", "직접 입력"], horizontal=True, key="csv_mode"
            )

            csv_text: Optional[str] = None
            csv_file = None

            if csv_input_mode == "파일 업로드":
                uploaded = st.file_uploader(
                    "핀맵 CSV 파일",
                    type=["csv"],
                    help="chip, pin, function, label 컬럼 필수",
                    key="csv_uploader",
                )
                if uploaded:
                    csv_file = uploaded
                    try:
                        df = pd.read_csv(uploaded)
                        uploaded.seek(0)
                        st.dataframe(df.head(10), use_container_width=True)
                        st.caption(f"총 {len(df)}개 핀")
                    except Exception as e:
                        st.error(f"CSV 파싱 오류: {e}")
            else:
                csv_text = st.text_area(
                    "CSV 직접 입력",
                    value=EXAMPLE_CSV.strip() if not schematic_files else "",
                    height=180,
                    help="chip,pin,function,label 헤더 포함",
                    placeholder="이미지를 업로드하면 자동 추출됩니다.",
                )
                if csv_text:
                    try:
                        df_preview = pd.read_csv(StringIO(csv_text))
                        st.dataframe(df_preview.head(10), use_container_width=True)
                        st.caption(f"총 {len(df_preview)}개 핀")
                    except Exception:
                        pass

    with col_right:
        st.markdown("**스크린샷 붙여넣기** (Ctrl+V — 여러 장 누적 가능)")
        pasted_val = _paste_comp(key="paste_zone", default=None)
        # 새 이미지를 붙여넣을 때만 누적 (rerun 시 같은 값 재append 방지)
        if pasted_val and pasted_val != st.session_state._last_pasted_s1:
            st.session_state.pasted_images_b64.append(pasted_val)
            st.session_state._last_pasted_s1 = pasted_val
        if st.session_state.pasted_images_b64:
            _n = len(st.session_state.pasted_images_b64)
            cap_col, clr_col = st.columns([3, 1])
            cap_col.caption(f"붙여넣은 회로도 {_n}장")
            if clr_col.button("초기화", key="clear_pasted"):
                st.session_state.pasted_images_b64 = []
                st.session_state._last_pasted_s1 = None
                st.rerun()
            for _pb in st.session_state.pasted_images_b64:
                st.image(_pb, use_container_width=True)

        st.divider()

        prompt = st.text_area(
            "자연어 요구사항 프롬프트",
            value=EXAMPLE_PROMPT,
            height=200,
            placeholder=EXAMPLE_PROMPT,
            help=(
                "포함 권장: 칩명·클럭 / 모터종류·제어방식 / 피드백센서 / "
                "PWM채널·데드타임 / 통신프로토콜 / 외부장치"
            ),
        )

        st.caption("프롬프트 체크리스트")
        checks = {
            "칩명": any(c in prompt for c in CHIPS),
            "클럭 정보": "MHz" in prompt,
            "모터 / 제어": any(w in prompt.lower() for w in ["모터", "foc", "bldc", "pmsm"]),
            "피드백 센서": any(w in prompt for w in ["엔코더", "encoder", "hall", "홀", "센서리스"]),
            "PWM / 데드타임": any(w in prompt.lower() for w in ["pwm", "데드타임", "deadtime"]),
            "통신": any(w in prompt.lower() for w in ["fdcan", "can", "uart", "spi", "i2c"]),
        }
        for label, ok in checks.items():
            icon = "✅" if ok else "⬜"
            st.caption(f"{icon} {label}")

        # Vision 이전 결과 표시
        if st.session_state.vision_analysis:
            with st.expander("Vision 분석 결과 (이전 실행)", expanded=False):
                st.markdown(st.session_state.vision_analysis)

        if st.session_state.extracted_csv:
            with st.expander("Vision 추출 핀맵 (이전 실행)", expanded=False):
                try:
                    df_v = pd.read_csv(StringIO(st.session_state.extracted_csv))
                    st.dataframe(df_v, use_container_width=True)
                    st.caption(f"Vision 추출 핀 수: {len(df_v)}")
                except Exception:
                    st.text(st.session_state.extracted_csv[:500])

    st.divider()

    # 입력 유효성 확인
    has_image = bool(schematic_files) or bool(st.session_state.pasted_images_b64)
    has_csv = bool(csv_text and csv_text.strip()) or (csv_file is not None)
    in_confirm = bool(st.session_state.confirm_pinmap_csv)  # Vision 추출 후 확정 대기 중
    can_submit = has_image or has_csv

    run_disabled = not backend_ok or not can_submit
    if not backend_ok:
        st.warning("Backend 서버에 연결할 수 없습니다. `uvicorn backend.main:app --port 8000` 실행 후 재시도하세요.")
    elif not can_submit and not in_confirm:
        st.info("회로도 이미지 또는 핀맵 CSV를 입력해주세요.")

    review_mode = st.radio(
        "검증 모드",
        ["🔍 Full review (기본 — LLM 자연어 설명 포함)", "⚡ Fast review (CI용 — Rule Engine만)"],
        horizontal=True,
        help="Fast: 핀 충돌·AF 오류만 즉시 검출 (LLM 없음). Full: Rule Engine + RAG + LLM 전체 분석.",
    )
    mode_value = "fast" if "Fast" in review_mode else "full"

    def _image_files() -> list:
        """업로드 + 붙여넣기 이미지를 multipart (필드, 파일) 튜플 리스트로."""
        fs: list = []
        for _f in (schematic_files or []):
            _f.seek(0)
            fs.append(("schematic_images", (_f.name, _f.read(), _f.type or "image/jpeg")))
        for _i, _pb in enumerate(st.session_state.pasted_images_b64):
            b64_data = _pb.split(",", 1)[-1]
            fs.append(("schematic_images", (f"pasted_{_i+1}.png", base64.b64decode(b64_data), "image/png")))
        return fs

    # ── 실행 중: 중단 버튼 ───────────────────────────────────────────────
    if st.session_state._rv_running:
        if st.button("⏹ 검증 중단", type="secondary", use_container_width=True, key="btn_stop"):
            st.session_state._rv_cancel = True
            st.session_state._rv_running = False
            try:
                requests.post(f"{BACKEND_URL}/v1/review/cancel", timeout=3)
            except Exception:
                pass
            st.warning("검증이 중단되었습니다.")
            st.rerun()

    # ── 확정 단계: Vision 추출 핀맵을 검토·수정 후 ② 검증 ────────────────
    elif in_confirm:
        st.markdown("### ✅ 추출된 핀맵 검토 · 수정")
        st.caption("Vision이 회로도에서 추출한 결과입니다. 오인식(예: OSC 핀)을 바로잡고 검증하세요.")
        st.session_state.confirm_chip = st.text_input(
            "칩 (Vision 감지값 — 필요시 수정)",
            value=st.session_state.confirm_chip or "",
            placeholder="예: STM32G431RBI6",
        )
        try:
            _base_df = pd.read_csv(StringIO(st.session_state.confirm_pinmap_csv))
        except Exception:
            _base_df = pd.DataFrame(columns=["chip", "pin", "function", "label"])
        for _c in ["chip", "pin", "function", "label"]:
            if _c not in _base_df.columns:
                _base_df[_c] = ""

        _chip_now = (st.session_state.confirm_chip or "").strip()
        _opts = _fetch_pin_options(_chip_now)
        _pin_opts = _opts.get("pins", {})
        _common = _opts.get("common", ["GPIO_Output", "GPIO_Input", "GPIO_Analog"])
        _io_map = _opts.get("io", {})

        def _io_badge(pin: str) -> str:
            """I/O structure 배지 — FT(5V)=초록, TT(3.6V)=주황."""
            code = _io_map.get(pin, "")
            if code.startswith("FT"):
                return ":green[**FT**] 5V"
            if code.startswith("TT"):
                return ":orange[**TT**] 3V3"
            return ":gray[—]"

        def _clean(v):
            v = "" if v is None else str(v).strip()
            return "" if v.lower() == "nan" else v

        st.markdown("**핀별 function 지정** — AF 드롭다운에서 선택 (ENBL→TIMx_CHy, "
                    "전류센스/포텐셔미터→ADCx_INy, 엔코더→TIMx_CHy, DIR/nFAULT→GPIO_Output)")
        if not _opts.get("found"):
            st.warning(f"이 칩({_opts.get('mcu_name') or _chip_now or '?'})의 AF 옵션을 "
                       "못 찾았습니다. 칩 부품번호를 확인하세요. (드롭다운은 GPIO만 표시)")

        def _pin_sort_key(p):
            # PA8 → (0,'A',8); 비표준 핀은 뒤로
            if len(p) >= 3 and p[0] == "P" and p[1].isalpha() and p[2:].isdigit():
                return (0, p[1], int(p[2:]))
            return (1, p, 0)

        # Vision이 판독한 핀 → (label, function)
        _vision = {}
        for _, _row in _base_df.iterrows():
            _p = _clean(_row.get("pin")).upper()
            if _p:
                _vision[_p] = (_clean(_row.get("label")), _clean(_row.get("function")))

        # 이 패키지의 전체 핀(AF 옵션=패키지 정확 핀목록 ∪ 판독핀)을 이름순으로.
        # io맵은 서브패밀리 전체(다른 패키지 핀 포함)라 universe엔 안 넣고 배지에만 쓴다.
        _universe = set(_pin_opts) | set(_vision)
        _cc = st.columns(2)
        _show_all = _cc[0].checkbox(
            "칩의 모든 핀 표시 (미판독 포함 — OCR 누락·밀림 보정용)",
            value=True, key="confirm_show_all",
        )
        _auto_fn = _cc[1].checkbox(
            "라벨로 function 자동 추정 (검토 후 수정)",
            value=True, key="confirm_auto_fn",
        )
        _pin_list = sorted(_universe if _show_all else set(_vision), key=_pin_sort_key)

        _hc = st.columns([0.9, 1, 1.6, 2.6])
        _hc[0].markdown("**IO**"); _hc[1].markdown("**핀**")
        _hc[2].markdown("**라벨**"); _hc[3].markdown("**function**")
        _rows = []
        _suggested = 0
        for pin in _pin_list:
            _lbl0, cur = _vision.get(pin, ("", ""))
            if not cur and _auto_fn:
                cur = _suggest_function(_lbl0, pin, _pin_opts)  # 라벨→추정(검토용)
                if cur:
                    _suggested += 1
            choices = _fn_choices(pin, cur, _pin_opts, _common)
            c0, c1, c2, c3 = st.columns([0.9, 1, 1.6, 2.6])
            c0.markdown(_io_badge(pin))
            c1.markdown(f"`{pin}`")
            _lbl = c2.text_input("l", value=_lbl0,
                                 key=f"clbl_{_chip_now}_{pin}", label_visibility="collapsed")
            _sel = c3.selectbox("f", choices,
                                index=(choices.index(cur) if cur in choices else 0),
                                key=f"cfn_{_chip_now}_{pin}", label_visibility="collapsed")
            _func = "" if _sel == "(미지정)" else _sel
            # function이나 라벨이 채워진 핀만 핀맵에 포함(빈 핀 80개로 오염 방지)
            if _func or _lbl:
                _rows.append({"chip": _chip_now, "pin": pin, "function": _func, "label": _lbl})
        edited_df = pd.DataFrame(_rows, columns=["chip", "pin", "function", "label"])
        _set = sum(1 for r in _rows if r["function"])
        st.caption(f"표시 {len(_pin_list)}핀 · 판독 {len(_vision)} · 자동추정 {_suggested} · "
                   f"function 지정 {_set} (⚠️ 추정값은 검토·수정하세요)")

        # 위 드롭다운 편집기가 단일 소스. 검증에 넘어갈 핀맵을 읽기전용으로 미리보기(항상 싱크).
        with st.expander("검증에 넘길 핀맵 미리보기 (CSV)", expanded=False):
            if len(edited_df):
                st.dataframe(edited_df, use_container_width=True, hide_index=True)
            else:
                st.caption("아직 채워진 핀이 없습니다. 위에서 라벨/function을 지정하세요.")

        st.markdown("**연결된 페리페럴 / 외부 부품** (Vision 추출 — 수정 가능)")
        st.session_state.confirm_peripherals = st.text_area(
            "페리페럴",
            value=st.session_state.confirm_peripherals or "",
            height=160,
            label_visibility="collapsed",
            help="게이트 드라이버·전류 감지·보호·커넥터·전원 등. LLM이 핀맵과 함께 검토합니다. 비워도 됩니다.",
            placeholder="- 게이트 드라이버: ...\n- 전류 감지: ...\n- 보호: ...\n- 전원: ...",
        )

        if st.session_state.confirm_vision_analysis:
            with st.expander("Vision 분석 요약", expanded=False):
                st.markdown(st.session_state.confirm_vision_analysis[:800])

        col_go, col_cancel = st.columns([3, 1])
        if col_go.button("② 검증 실행 (확정된 핀맵)", type="primary", use_container_width=True, key="btn_confirm_review"):
            _chip = (st.session_state.confirm_chip or "").strip()
            if not _chip:
                st.error("칩을 입력하세요 (자동 감지 실패 시 직접 입력).")
                st.stop()
            _data = {
                "chip": _chip,
                "prompt": prompt,
                "mode": mode_value,
                "pinmap_csv": edited_df.to_csv(index=False),
                "vision_analysis": st.session_state.confirm_vision_analysis,
                "peripherals": st.session_state.confirm_peripherals or "",
            }
            st.session_state.confirm_pinmap_csv = ""  # 확정 단계 종료
            _begin_review(_data, [])  # 이미지 없이 — Vision 재실행 안 함
            st.rerun()
        if col_cancel.button("✖ 취소", use_container_width=True, key="btn_confirm_cancel"):
            st.session_state.confirm_pinmap_csv = ""
            st.session_state.confirm_vision_analysis = ""
            st.session_state.confirm_peripherals = ""
            st.rerun()

    # ── 이미지 입력: ① Vision 추출 → 검토 ───────────────────────────────
    elif has_image:
        st.success("회로도 이미지 감지 — 먼저 Vision으로 핀맵을 추출해 검토합니다.")
        if st.button("① Vision으로 핀맵 추출 → 검토", type="primary", disabled=run_disabled,
                     use_container_width=True, key="btn_extract"):
            if not prompt.strip():
                st.error("프롬프트를 입력하세요.")
                st.stop()
            _ex_data = {"prompt": prompt, "chip": "" if chip == AUTO_CHIP else chip}
            with st.spinner("Vision으로 핀맵 추출 중... (회로도 장수에 따라 최대 5분)"):
                try:
                    _er = requests.post(
                        f"{BACKEND_URL}/v1/extract-pinmap",
                        data=_ex_data, files=_image_files(), timeout=600,
                    )
                    if _er.status_code == 200:
                        _res = _er.json()
                        st.session_state.confirm_pinmap_csv = _res.get("pinmap_csv") or "chip,pin,function,label\n"
                        st.session_state.confirm_chip = _res.get("chip") or ("" if chip == AUTO_CHIP else chip)
                        st.session_state.confirm_vision_analysis = _res.get("vision_analysis", "")
                        st.session_state.confirm_peripherals = _res.get("peripherals", "")
                    else:
                        st.error(f"Vision 추출 실패 {_er.status_code}: {_er.text[:200]}")
                except Exception as _e:
                    st.error(f"Vision 추출 요청 실패: {_e}")
            st.rerun()

    # ── 직접 CSV 입력: 바로 검증 ─────────────────────────────────────────
    else:
        btn_label = "검증 실행 (Rule Engine + RAG + LLM)" if mode_value == "full" else "검증 실행 (Rule Engine only — Fast)"
        if st.button(btn_label, type="primary", disabled=run_disabled, use_container_width=True, key="btn_review"):
            if not prompt.strip():
                st.error("프롬프트를 입력하세요.")
                st.stop()
            # 직접 CSV용 칩: AUTO면 CSV의 chip 컬럼에서 추출
            _chip = chip
            if chip == AUTO_CHIP:
                _chip = ""
                try:
                    _src = csv_text if (csv_text and csv_text.strip()) else (csv_file.getvalue().decode("utf-8-sig") if csv_file else "")
                    _dfc = pd.read_csv(StringIO(_src))
                    if "chip" in _dfc.columns and len(_dfc):
                        _chip = str(_dfc["chip"].iloc[0]).strip()
                except Exception:
                    pass
                if not _chip:
                    st.error("칩을 감지하지 못했습니다. 상단에서 칩을 직접 선택하거나 CSV의 chip 컬럼을 채워주세요.")
                    st.stop()
            _files: list = []
            _data: dict = {"chip": _chip, "prompt": prompt, "mode": mode_value}
            if csv_file is not None:
                csv_file.seek(0)
                _files.append(("csv_file", (csv_file.name, csv_file.read(), "text/csv")))
            elif csv_text and csv_text.strip():
                _data["pinmap_csv"] = csv_text
            _begin_review(_data, _files)
            st.rerun()

    # ── 진행 중: 원형 프로그레스 + 파이프라인 GUI + 로그 ─────────────────
    if st.session_state._rv_running:
        elapsed = time.time() - (st.session_state._rv_start or time.time())
        remaining = max(0.0, REVIEW_TIMEOUT - elapsed)
        pct = min(int(elapsed / REVIEW_TIMEOUT * 100), 99)
        st.markdown(_circular_progress_html(pct, elapsed, remaining), unsafe_allow_html=True)

        # 중간 결과/타이밍을 먼저 가져와 파이프라인 단계 갱신.
        # /v1/logs는 누적 버퍼라 이전 실행의 완료 마커가 남음 → 단계 판단엔 쓰지 않고
        # 매 실행마다 비워지는 partial을 근거로 사용 (재실행 시 단계가 옛 상태로 안 남음).
        try:
            _pr = requests.get(f"{BACKEND_URL}/v1/review/partial", timeout=3).json() or {}
        except Exception:
            _pr = {}
        if _pr.get("timing"):
            st.session_state["_rv_partial_timing"] = _pr["timing"]
        _update_stages_from_partial(_pr)

        st.markdown("**파이프라인 진행 상태**")
        st.markdown(_pipeline_status_html(), unsafe_allow_html=True)

        # 중간 결과
        try:
            if _pr:
                with st.expander("중간 결과 (현재까지 완료된 단계)", expanded=True):
                    if _pr.get("vision_analysis"):
                        st.markdown("**Vision 분석**")
                        st.markdown(_pr["vision_analysis"][:800] + ("…" if len(_pr.get("vision_analysis","")) > 800 else ""))
                    if _pr.get("extracted_csv"):
                        st.markdown("**추출된 핀맵 CSV**")
                        st.code(_pr["extracted_csv"][:600], language="text")
                    if "rule_errors" in _pr:
                        errs = _pr["rule_errors"]
                        warns = _pr["rule_warnings"]
                        st.markdown(f"**Rule Engine** — 오류 {len(errs)}건 / 경고 {len(warns)}건")
                        for e in errs[:5]:
                            st.error(e)
                        for w in warns[:5]:
                            st.warning(w)
                    if "rag_docs_count" in _pr:
                        st.markdown(f"**RAG** — 검색된 청크: {_pr['rag_docs_count']}개")
                    if "llm_errors" in _pr:
                        st.markdown(f"**LLM** — 추가 오류 {len(_pr['llm_errors'])}건 / 제안 {len(_pr.get('llm_suggestions',[]))}건")
        except Exception:
            pass

        # 서버 로그 (표시용 — 단계 판단엔 쓰지 않음)
        _logs: list[str] = []
        try:
            _log_r = requests.get(f"{BACKEND_URL}/v1/logs?n=60", timeout=3)
            if _log_r.status_code == 200:
                _logs = _log_r.json().get("logs", [])
        except Exception:
            pass
        if _logs:
            with st.expander("서버 로그", expanded=False):
                st.code("\n".join(_logs[-30:]), language=None)

        time.sleep(3)
        st.rerun()

    # ── 에러 표시 ────────────────────────────────────────────────────────
    if st.session_state._rv_error:
        _err = st.session_state._rv_error
        st.session_state._rv_error = None
        st.error(f"요청 실패: {_err}")

    # ── 결과 처리 (스레드 완료 시) ────────────────────────────────────────
    _rv_resp = st.session_state._rv_result
    if _rv_resp is not None:
        st.session_state._rv_result = None
        # 결과 도착 직후 중간 데이터/로그를 스냅샷 — 결과 표시 후에도 디버그 패널에서 유지
        try:
            _fp = requests.get(f"{BACKEND_URL}/v1/review/partial", timeout=3).json()
            st.session_state._rv_final_partial = _fp or {}
        except Exception:
            pass
        try:
            _fl = requests.get(f"{BACKEND_URL}/v1/logs?n=200", timeout=3)
            if _fl.status_code == 200:
                st.session_state._rv_final_logs = _fl.json().get("logs", [])
        except Exception:
            pass
        r = _rv_resp
        if r.status_code == 200:
            report = r.json()
            has_errors = bool(report.get("errors"))
            st.session_state.review_passed = not has_errors
            st.session_state.validated_pins = report.get("validated_pins", {})
            st.session_state.last_report = report
            st.session_state.vision_analysis = report.get("vision_analysis", "")
        elif r.status_code == 403:
            body = r.json()
            report = body.get("report", body)
            st.session_state.review_passed = False
            st.session_state.last_report = report
            st.session_state.vision_analysis = report.get("vision_analysis", "")
        else:
            st.error(f"서버 오류 {r.status_code}: {r.text[:300]}")

    # ── 결과 표시 ──────────────────────────────────────────────────────────
    if st.session_state.last_report:
        report = st.session_state.last_report
        errors = report.get("errors", [])
        warnings = report.get("warnings", [])
        suggestions = report.get("suggestions", [])
        vision_txt = report.get("vision_analysis", "")

        st.divider()

        # Vision 분석 결과
        if vision_txt:
            with st.expander("Vision 분석 — Gemma 4 31B 이미지 분석 결과", expanded=True):
                st.markdown(vision_txt)

        st.subheader("검증 결과")

        if errors:
            st.error(f"**{len(errors)}개 오류 — 펌웨어 생성 차단**")
            for e in errors:
                st.error(f"오류: {e}")
        else:
            st.success("모든 핀 검증 통과 — Step 2로 진행 가능합니다.")

        for w in warnings:
            st.warning(f"경고: {w}")

        for s in suggestions:
            st.info(f"권장: {s}")

        if st.session_state.validated_pins:
            with st.expander("확정 핀 JSON 보기", expanded=False):
                st.json(st.session_state.validated_pins)

        if not errors and st.session_state.validated_pins:
            # 오류 없음 → 바로 Step 2
            if st.button(
                "Step 2: HAL 코드 생성하기 →",
                type="primary",
                key="go_to_step2_btn",
                use_container_width=True,
            ):
                st.session_state.go_to_step2 = True
                st.rerun()

        elif errors and st.session_state.validated_pins:
            # 오류 있음 — 채팅으로 논의 후 사용자가 판단해서 넘어갈 수 있게
            st.warning(
                "오류가 있습니다. 아래 채팅에서 각 오류가 실제 문제인지 확인한 뒤, "
                "이상이 없다고 판단되면 아래 버튼으로 Step 2를 진행하세요."
            )
            if st.button(
                "오류 확인 완료 — Step 2로 진행 →",
                type="secondary",
                key="go_to_step2_override_btn",
                use_container_width=True,
                help="채팅에서 오류를 검토한 뒤, 실제 이슈가 없다고 판단될 때만 사용하세요.",
            ):
                st.session_state.go_to_step2 = True
                st.rerun()

        if st.session_state.get("go_to_step2"):
            st.session_state.go_to_step2 = False
            components.html(
                """<script>
                setTimeout(function() {
                    var tabs = window.parent.document.querySelectorAll('button[role="tab"]');
                    if (tabs.length >= 2) { tabs[1].click(); }
                }, 200);
                </script>""",
                height=0,
            )

        # ── 🛠 개발자 디버그 패널 (결과 표시 후에도 유지, 접이식) ──────────────
        fp = st.session_state._rv_final_partial
        if st.session_state._dev_debug and fp:
            st.divider()
            st.markdown("### 🛠 개발자 디버그 정보")
            st.caption("결과 표시 후에도 유지되는 중간 단계 입출력. 운영 시 사이드바에서 끄세요.")

            if fp.get("requirements"):
                with st.expander("① 인식된 요구사항 (프롬프트 파싱 결과)", expanded=False):
                    st.json(fp["requirements"])

            if fp.get("pinmap_rows"):
                with st.expander(f"② Rule Engine 입력 핀맵 ({len(fp['pinmap_rows'])}핀)", expanded=False):
                    try:
                        st.dataframe(pd.DataFrame(fp["pinmap_rows"]), use_container_width=True)
                    except Exception:
                        st.json(fp["pinmap_rows"])

            if fp.get("extracted_csv"):
                with st.expander("②′ Vision 추출 원본 CSV", expanded=False):
                    st.code(fp["extracted_csv"], language="text")

            if fp.get("peripherals"):
                with st.expander("②″ 페리페럴/외부 부품 (Vision 추출)", expanded=False):
                    st.markdown(fp["peripherals"])

            if "rule_errors" in fp or "rule_warnings" in fp:
                _errs = fp.get("rule_errors", [])
                _warns = fp.get("rule_warnings", [])
                with st.expander(f"③ Rule Engine 결과 — 오류 {len(_errs)} / 경고 {len(_warns)}", expanded=False):
                    for e in _errs:
                        st.error(e)
                    for w in _warns:
                        st.warning(w)

            if fp.get("rag_query") or "rag_docs_count" in fp:
                with st.expander(f"④ RAG — 검색 청크 {fp.get('rag_docs_count', 0)}개", expanded=False):
                    if fp.get("rag_query"):
                        st.markdown("**RAG 쿼리 (입력)**")
                        st.code(fp["rag_query"], language="text")
                    _chunks = fp.get("rag_chunks")
                    if _chunks:
                        st.markdown(f"**검색된 청크 {len(_chunks)}개 (LLM 컨텍스트로 전달됨)**")
                        for _i, _ch in enumerate(_chunks, 1):
                            st.markdown(f"청크 {_i}")
                            st.code(_ch, language="text")
                    elif fp.get("rag_context_preview"):  # 구버전 호환
                        st.markdown("**검색된 컨텍스트 (미리보기)**")
                        st.code(fp["rag_context_preview"], language="text")

            if fp.get("timing"):
                with st.expander("⑤ 단계별 소요 시간", expanded=False):
                    st.json(fp["timing"])

            if st.session_state._rv_final_logs:
                with st.expander("⑥ 서버 로그 (스냅샷)", expanded=False):
                    st.code("\n".join(st.session_state._rv_final_logs), language=None)

    # ── 멀티턴 채팅 ──────────────────────────────────────────────────────────
    if st.session_state.last_report:
        st.divider()
        col_chat_header, col_chat_clear = st.columns([5, 1])
        with col_chat_header:
            st.subheader("리뷰 결과에 대해 질문하기")
        with col_chat_clear:
            if st.session_state.chat_history:
                if st.button("초기화", key="clear_chat"):
                    st.session_state.chat_history = []
                    st.rerun()

        # 이전 대화 표시
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("sources"):
                    with st.expander("근거 문서"):
                        for src in msg["sources"]:
                            st.caption(src)

        # 입력창
        if question := st.chat_input("검증 결과에 대해 질문하세요... (근거 자료도 요청 가능)"):
            st.session_state.chat_history.append({"role": "user", "content": question, "sources": []})
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                with st.spinner("답변 생성 중..."):
                    _report = st.session_state.last_report
                    try:
                        _r = requests.post(
                            f"{BACKEND_URL}/v1/chat",
                            json={
                                "chip": _report.get("chip", ""),
                                "question": question,
                                "history": [
                                    {"role": m["role"], "content": m["content"]}
                                    for m in st.session_state.chat_history[:-1]
                                ],
                                "report_context": _report,
                            },
                            timeout=600,  # LLM 답변 생성(gemma4:31b)이 길 수 있음 — 백엔드 생성 예산과 맞춤
                        )
                        if _r.status_code == 200:
                            _data = _r.json()
                            _answer = _data["answer"]
                            _sources = _data.get("sources", [])
                        else:
                            _answer = f"오류 {_r.status_code}: {_r.text[:200]}"
                            _sources = []
                    except Exception as _e:
                        _answer = f"요청 실패: {_e}"
                        _sources = []

                st.markdown(_answer)
                if _sources:
                    with st.expander("근거 문서"):
                        for src in _sources:
                            st.caption(src)

            st.session_state.chat_history.append({
                "role": "assistant",
                "content": _answer,
                "sources": _sources,
            })


# ===========================================================================
# Tab 2 — Step 2 코드 생성
# ===========================================================================

with tab2:
    st.header("Step 2 — CubeMX HAL 코드 생성")

    # Step 1에서 이어받을 핀맵이 실제로 있으면 재입력 폼을 띄우지 않는다.
    # (오류가 있어도 사용자가 '오류 확인 완료'로 넘어온 경우 포함 — review_passed가 아니라
    #  validated_pins 존재 여부로 판단해야 데이터가 정상적으로 인계된다.)
    _carried_pins = st.session_state.validated_pins.get("pins") if isinstance(
        st.session_state.validated_pins, dict) else None

    if not _carried_pins:
        st.info("Step 1 핀 검증을 통과하거나, 핀맵을 직접 입력해 바로 시작할 수 있습니다.")

        col_s1_btn, _ = st.columns([1, 2])
        with col_s1_btn:
            if st.button("← Step 1 핀 검증으로 이동", key="go_to_step1_btn"):
                components.html(
                    """<script>
                    setTimeout(function() {
                        var tabs = window.parent.document.querySelectorAll('button[role="tab"]');
                        if (tabs.length >= 1) { tabs[0].click(); }
                    }, 200);
                    </script>""",
                    height=0,
                )

        st.divider()
        with st.expander("핀맵 직접 입력하여 Step 2 바로 시작", expanded=True):
            chip2 = st.selectbox("칩 선택", CHIPS[1:], index=2, key="step2_chip")  # AUTO 제외, 기본 G474

            # ── 방법 1: 회로도 이미지 붙여넣기 → Vision 추출 ────────────────
            st.markdown("**방법 1 — 회로도 이미지** (Ctrl+V 붙여넣기 → Vision 핀맵 자동 추출)")
            pasted_s2 = _paste_comp(key="paste_zone_step2", default=None)
            if pasted_s2 is not None:
                if pasted_s2:
                    st.session_state.step2_pasted_image_b64 = pasted_s2
                    st.session_state.step2_extracted_pins = None
                else:
                    st.session_state.step2_pasted_image_b64 = None
                    st.session_state.step2_extracted_pins = None

            if st.session_state.step2_pasted_image_b64 and st.session_state.step2_extracted_pins is None:
                if st.button("Vision으로 핀맵 추출", key="step2_vision_btn", disabled=not backend_ok):
                    with st.spinner("Vision으로 핀맵 추출 중... (30~90초)"):
                        try:
                            b64_data = st.session_state.step2_pasted_image_b64.split(",", 1)[-1]
                            img_bytes = base64.b64decode(b64_data)
                            _vr = requests.post(
                                f"{BACKEND_URL}/v1/review",
                                data={"chip": chip2, "prompt": "회로도에서 핀맵을 추출해주세요."},
                                files={"schematic_image": ("pasted.png", img_bytes, "image/png")},
                                timeout=180,
                            )
                            _body = _vr.json() if _vr.status_code == 200 else _vr.json().get("report", {})
                            _vp = _body.get("validated_pins", {})
                            if _vp.get("pins"):
                                st.session_state.step2_extracted_pins = _vp
                                st.rerun()
                            else:
                                st.warning("핀맵을 추출하지 못했습니다. CSV를 직접 입력해주세요.")
                        except Exception as _e:
                            st.error(f"Vision 추출 오류: {_e}")

            if st.session_state.step2_extracted_pins:
                _ep = st.session_state.step2_extracted_pins
                st.success(f"Vision 추출 완료 — {len(_ep.get('pins', []))}개 핀 감지")
                st.dataframe(pd.DataFrame(_ep.get("pins", [])), use_container_width=True)

            st.divider()

            # ── 방법 2: CSV 직접 입력 ────────────────────────────────────────
            st.markdown("**방법 2 — 핀맵 CSV** (파일 업로드 또는 직접 입력)")
            csv_input_mode2 = st.radio("입력 방식", ["파일 업로드", "직접 입력"],
                                       horizontal=True, key="step2_csv_mode")
            csv_direct: Optional[str] = None
            if csv_input_mode2 == "파일 업로드":
                _csv_up = st.file_uploader("CSV 파일 (chip, pin, function, label)",
                                           type=["csv"], key="step2_csv_upload")
                if _csv_up:
                    csv_direct = _csv_up.read().decode("utf-8-sig")
                    _csv_up.seek(0)
                    try:
                        st.dataframe(pd.read_csv(StringIO(csv_direct)).head(10), use_container_width=True)
                    except Exception:
                        pass
            else:
                csv_direct = st.text_area(
                    "CSV 직접 입력", value=EXAMPLE_CSV.strip(), height=160, key="step2_csv_text"
                )
                if csv_direct:
                    try:
                        _df_prev = pd.read_csv(StringIO(csv_direct))
                        st.dataframe(_df_prev.head(10), use_container_width=True)
                        st.caption(f"총 {len(_df_prev)}개 핀")
                    except Exception:
                        pass

            st.divider()
            _can_start = (
                st.session_state.step2_extracted_pins is not None
                or bool(csv_direct and csv_direct.strip())
            )
            if st.button("Step 2 바로 시작 →", type="primary",
                         key="step2_direct_btn", disabled=not _can_start):
                try:
                    if st.session_state.step2_extracted_pins:
                        _vp2 = st.session_state.step2_extracted_pins.copy()
                        _vp2["chip"] = chip2
                    else:
                        _df2 = pd.read_csv(StringIO(csv_direct))
                        _vp2 = {
                            "chip": chip2,
                            "clock_mhz": 170, "crystal_mhz": 24,
                            "motor_count": 1, "control_type": "FOC",
                            "encoder_type": "incremental", "pwm_channels": 6,
                            "deadtime_ns": 500, "current_sense": "internal_opamp",
                            "comms": [], "spi_eeprom": False,
                            "pins": [
                                {"pin": r.get("pin", ""), "function": r.get("function", ""), "label": r.get("label", "")}
                                for _, r in _df2.iterrows()
                            ],
                        }
                    st.session_state.validated_pins = _vp2
                    st.session_state.review_passed = True
                    st.rerun()
                except Exception as _e:
                    st.error(f"파싱 오류: {_e}")
    else:
        vp = st.session_state.validated_pins
        _src = "Step 1 통과" if st.session_state.review_passed else "Step 1 핀맵 인계(오류 검토 후 진행)"
        st.success(f"{_src} — 칩: {vp.get('chip', '?')}, 핀 수: {len(vp.get('pins', []))}")

        # ── 핀 function 지정 (CubeMX AF 드롭다운) ─────────────────────────
        # function이 비면 .ioc에 신호가 안 들어가므로(라벨만), 핀별 AF에서 선택하게 한다.
        def _get_pin_options(_chip: str) -> dict:
            _cache = st.session_state.setdefault("_pin_opts_cache", {})
            if _chip not in _cache:
                try:
                    _r = requests.get(f"{BACKEND_URL}/v1/pin-options/{_chip}", timeout=10)
                    _cache[_chip] = _r.json() if _r.status_code == 200 else {}
                except Exception:
                    _cache[_chip] = {}
            return _cache[_chip]

        _opts = _get_pin_options(vp.get("chip", ""))
        _pin_opts = _opts.get("pins", {})
        _common = _opts.get("common", ["GPIO_Output", "GPIO_Input", "GPIO_Analog"])
        _vpins = vp.get("pins", [])
        _missing = sum(1 for p in _vpins if (p.get("pin") or "").strip()
                       and not (p.get("function") or "").strip())
        with st.expander(f"🔧 핀 function 지정 — 미지정 {_missing}개 (드롭다운 선택)",
                         expanded=_missing > 0):
            if not _opts.get("found"):
                st.warning(
                    f"이 칩({_opts.get('mcu_name', '?')})의 AF 옵션을 찾지 못했습니다. "
                    "백엔드에 agent/pin_function_options.json이 있는지, 부품번호가 맞는지 확인하세요."
                )
            st.caption(
                "ENBL→TIMx_CHy(PWM), 전류센스/포텐셔미터→ADCx_INy, 엔코더→TIMx_CHy, "
                "DIR/nFAULT→GPIO_Output, 입력→GPIO_Input. 빈 핀은 그대로 두면 됩니다."
            )
            with st.form("fn_assign_form"):
                _hc = st.columns([1.2, 2, 3])
                _hc[0].markdown("**핀**"); _hc[1].markdown("**라벨**"); _hc[2].markdown("**function**")
                for p in _vpins:
                    pin = (p.get("pin") or "").strip().upper()
                    if not pin:
                        continue
                    cur = (p.get("function") or "").strip()
                    opts = _pin_opts.get(pin, [])
                    choices = ["(미지정)"] + opts + [c for c in _common if c not in opts]
                    if cur and cur not in choices:
                        choices.insert(1, cur)  # 기존 값(예: SWDIO/FDCAN) 보존
                    c1, c2, c3 = st.columns([1.2, 2, 3])
                    c1.markdown(f"`{pin}`")
                    c2.caption(p.get("label", "") or "—")
                    c3.selectbox(
                        "fn", choices, index=(choices.index(cur) if cur in choices else 0),
                        key=f"fn_{pin}", label_visibility="collapsed",
                    )
                _applied = st.form_submit_button("이 function으로 적용 →", type="primary",
                                                 use_container_width=True)
            if _applied:
                for p in _vpins:
                    pin = (p.get("pin") or "").strip().upper()
                    if not pin:
                        continue
                    _sel = st.session_state.get(f"fn_{pin}", "(미지정)")
                    p["function"] = "" if _sel == "(미지정)" else _sel
                st.session_state.validated_pins["pins"] = _vpins
                st.session_state.ioc_result = None  # 변경됐으니 .ioc 재생성 유도
                st.success("function 적용 완료 — 아래에서 .ioc를 다시 생성하세요.")

        # ── Step A: .ioc 파일 생성 ────────────────────────────────────────
        st.subheader("Step A — STM32CubeMX 프로젝트 파일(.ioc) 생성")
        st.caption(
            ".ioc 파일은 STM32CubeMX의 프로젝트 설정 파일입니다. "
            "CubeMX에서 이 파일을 열면 핀 배치·클럭·페리페럴 설정을 확인하고 "
            "HAL 초기화 코드(main.c, tim.c 등)를 생성할 수 있습니다."
        )

        if st.button(".ioc 파일 생성", type="primary", disabled=not backend_ok, key="btn_gen_ioc"):
            with st.spinner(".ioc 파일 생성 중..."):
                try:
                    r = requests.post(
                        f"{BACKEND_URL}/v1/generate-ioc",
                        json={"validated_pins": vp},
                        timeout=60,
                    )
                    if r.status_code == 200:
                        st.session_state.ioc_result = r.json()
                    else:
                        st.error(f"오류 {r.status_code}: {r.text[:300]}")
                except Exception as e:
                    st.error(f"요청 실패: {e}")

        if st.session_state.ioc_result:
            ioc = st.session_state.ioc_result
            st.success(f"생성 완료 — `{ioc.get('ioc_filename', '')}`")

            # .ioc 다운로드
            try:
                dl_url = f"{BACKEND_URL}{ioc.get('download_url', '')}"
                ioc_bytes = requests.get(dl_url, timeout=10).content
                st.download_button(
                    label="⬇ .ioc 파일 다운로드",
                    data=ioc_bytes,
                    file_name=ioc.get("ioc_filename", "output.ioc"),
                    mime="application/octet-stream",
                    key="btn_dl_ioc",
                )
            except Exception as e:
                st.error(f"다운로드 준비 오류: {e}")

            with st.expander("CubeMX GUI에서 사용하는 방법", expanded=False):
                st.markdown("""
1. **STM32CubeMX 실행**
2. **File → Load Project** → 다운로드한 `.ioc` 파일 선택
3. Pinout & Configuration 탭에서 핀 배치·페리페럴 설정 확인
4. **Project → Generate Code** (단축키 `Alt+K`)
5. 생성된 프로젝트 폴더를 **STM32CubeIDE / Keil / IAR** 에서 열기
""")

            st.divider()

            # ── Step B: CubeMX CLI로 HAL 코드 자동 생성 ────────────────────
            st.subheader("Step B — HAL 코드 자동 생성 및 ZIP 다운로드")

            # CubeMX 설치 상태 확인
            try:
                _cs = requests.get(f"{BACKEND_URL}/v1/cubemx-status", timeout=5).json()
                cubemx_installed = _cs.get("installed", False)
                cubemx_path = _cs.get("path", None)
            except Exception:
                cubemx_installed = False
                cubemx_path = None

            if cubemx_installed:
                st.success(f"STM32CubeMX 감지됨: `{cubemx_path}`")
            else:
                st.warning(
                    "STM32CubeMX가 설치되지 않았습니다.  \n"
                    "DGX Spark에 CubeMX를 설치하면 이 버튼으로 HAL 코드를 자동 생성할 수 있습니다."
                )
                with st.expander("CubeMX 설치 방법"):
                    st.markdown("""
1. [ST 공식 사이트](https://www.st.com/en/development-tools/stm32cubemx.html)에서 Linux용 설치 파일 다운로드
2. 설치 후 `/opt/STM32CubeMX/` 에 위치하거나 환경변수 설정:
```bash
export CUBEMX_PATH=/path/to/STM32CubeMX
```
3. 백엔드 재시작 후 이 버튼이 활성화됩니다.
""")

            if st.button(
                "HAL 코드 생성 및 ZIP 다운로드",
                type="primary",
                disabled=(not backend_ok or not cubemx_installed),
                key="btn_gen_code",
            ):
                with st.spinner("CubeMX로 HAL 코드 생성 중... (최대 3분 소요)"):
                    try:
                        _cr = requests.post(
                            f"{BACKEND_URL}/v1/generate-code",
                            json={"validated_pins": vp},
                            timeout=300,
                        )
                        if _cr.status_code == 200:
                            st.session_state.code_zip = {
                                "data": _cr.content,
                                "filename": _cr.headers.get(
                                    "content-disposition", ""
                                ).split("filename=")[-1].strip('"') or "MotorDrive.zip",
                            }
                            st.rerun()
                        elif _cr.status_code == 503:
                            st.error(_cr.json().get("message", "CubeMX 미설치"))
                        else:
                            st.error(f"오류 {_cr.status_code}: {_cr.text[:300]}")
                    except Exception as _e:
                        st.error(f"요청 실패: {_e}")

            if st.session_state.get("code_zip"):
                _cz = st.session_state.code_zip
                st.success("HAL 코드 생성 완료!")
                st.download_button(
                    label="⬇ HAL 코드 ZIP 다운로드",
                    data=_cz["data"],
                    file_name=_cz["filename"],
                    mime="application/zip",
                    key="btn_dl_zip",
                )
                with st.expander("ZIP 내용물"):
                    st.markdown("""
```
{chip}_MotorDrive/
├── Core/
│   ├── Inc/  main.h  tim.h  adc.h  fdcan.h  dma.h
│   └── Src/  main.c  tim.c  adc.c  fdcan.c  dma.c
├── Drivers/  STM32G4xx_HAL_Driver/
└── {chip}_MotorDrive.ioc
```
""")

            st.caption("Step 3 탭에서 FOC 알고리즘을 삽입할 수 있습니다.")


# ===========================================================================
# Tab 3 — Step 3 알고리즘 통합
# ===========================================================================

with tab3:
    st.header("Step 3 — 알고리즘 통합")

    import io as _io, zipfile as _zf, json as _json, time as _time

    from agent.step3_codegen_agent import select_modules as _select_modules

    _MOD_DESC = {
        "dc_motor_pid":    "dc_motor_pid — DC/FOC/PMSM PID 제어 + H-bridge PWM",
        "bldc_6step_hall": "bldc_6step_hall — BLDC 6-step 홀센서 구동",
        "fdcan_motor_cmd": "fdcan_motor_cmd — FDCAN 커맨드 파싱 (1Mbps)",
        "multi_axis_sync": "multi_axis_sync — 다축 동기화",
    }

    # ── 핀맵 확보 (Step 1 결과 or 직접 입력) ─────────────────────────────────
    vp3: Optional[Dict[str, Any]] = (
        st.session_state.validated_pins if st.session_state.review_passed else None
    )

    if not vp3:
        st.info("Step 1 결과가 없습니다. 아래에서 핀맵을 직접 입력하거나 JSON을 업로드하세요.")
        with st.expander("핀맵 직접 입력 (Step 1 없이 진입)", expanded=True):
            _col_chip3, _col_ctrl = st.columns(2)
            with _col_chip3:
                _chip3 = st.selectbox("칩", CHIPS[1:], index=2, key="s3_chip")  # AUTO 제외, 기본 G474
            with _col_ctrl:
                _ctrl3 = st.selectbox("제어 방식", ["FOC", "PMSM", "BLDC_6step", "DC"], key="s3_ctrl")
            _enc3  = st.selectbox("엔코더", ["incremental", "hall", "sensorless"], key="s3_enc")
            _comms3 = st.multiselect("통신", ["fdcan", "uart", "spi"], key="s3_comms")
            _nm3   = st.number_input("모터 수", 1, 4, 1, key="s3_nm")

            _json_up = st.file_uploader("또는 validated_pins JSON 업로드", type=["json"], key="s3_json")
            if _json_up:
                try:
                    vp3 = _json.loads(_json_up.read().decode())
                    st.success(f"JSON 로드 완료 — 칩: {vp3.get('chip','?')}")
                except Exception as _e:
                    st.error(f"JSON 파싱 오류: {_e}")

            if not vp3:
                if st.button("이 설정으로 Step 3 진행", key="s3_manual_btn"):
                    vp3 = {
                        "chip": _chip3, "clock_mhz": 170, "crystal_mhz": 24,
                        "motor_count": int(_nm3), "control_type": _ctrl3,
                        "encoder_type": _enc3, "pwm_channels": 6,
                        "deadtime_ns": 500, "current_sense": "internal_opamp",
                        "comms": _comms3, "spi_eeprom": False, "pins": [],
                    }
                    st.session_state.validated_pins = vp3
                    st.session_state.review_passed = True
                    st.rerun()
    else:
        st.success(f"핀맵 확보 — 칩: {vp3.get('chip','?')}, 핀 수: {len(vp3.get('pins',[]))}")

    st.divider()

    if vp3:

        # ── 알고리즘 요구사항 프롬프트 ───────────────────────────────────
        st.subheader("알고리즘 요구사항 프롬프트")
        _s3_prompt = st.text_area(
            "제어 알고리즘 요구사항을 자연어로 입력하세요",
            value=st.session_state.step3_prompt or STEP3_EXAMPLE_PROMPT,
            height=120,
            help="모터 타입, 제어 방식, 센서, 보호 기능 등을 자유롭게 기술하세요. LLM이 Golden Module 적응 시 참고합니다.",
            key="s3_prompt_input",
        )
        st.session_state.step3_prompt = _s3_prompt

        # 프롬프트 체크리스트
        _s3_checks = {
            "모터 타입": any(w in _s3_prompt.lower() for w in ["pmsm", "bldc", "dc모터", "dc motor", "브러시드", "brushed"]),
            "제어 방식": any(w in _s3_prompt.lower() for w in ["foc", "6-step", "6step", "pid", "캐스케이드", "cascade"]),
            "피드백 센서": any(w in _s3_prompt.lower() for w in ["엔코더", "encoder", "hall", "홀", "앱솔루트", "absolute", "ssi", "센서리스"]),
            "전류 센싱": any(w in _s3_prompt.lower() for w in ["샨트", "shunt", "opamp", "전류 센서", "current"]),
            "보호 기능": any(w in _s3_prompt.lower() for w in ["ocp", "ovp", "brk", "watchdog", "온도", "temperature", "보호"]),
            "통신": any(w in _s3_prompt.lower() for w in ["fdcan", "can", "uart", "spi", "i2c"]),
        }
        _chk_cols = st.columns(len(_s3_checks))
        for _ci, (_lbl, _ok) in enumerate(_s3_checks.items()):
            _chk_cols[_ci].caption(f"{'✅' if _ok else '⬜'} {_lbl}")

        st.divider()

        # ── Step 2 HAL 코드 업로드 (선택) ────────────────────────────────
        st.subheader("Step 2 HAL 코드 업로드 (선택)")
        st.caption("Step 2에서 CubeMX로 생성한 ZIP을 올리면 적응된 코드를 USER CODE 마커에 자동 주입합니다.")
        _hal_zip_up = st.file_uploader(
            "HAL 코드 ZIP 업로드 (Step 2 결과물)", type=["zip"], key="s3_hal_zip"
        )
        if _hal_zip_up:
            st.session_state.step3_hal_zip = _hal_zip_up.read()
            st.success(f"ZIP 로드 완료 — {_hal_zip_up.name}")
        elif st.session_state.get("step3_hal_zip"):
            st.info("이전에 업로드한 HAL ZIP이 있습니다.")

        st.divider()

        # ── 선택될 모듈 미리보기 ─────────────────────────────────────────
        st.subheader("선택될 Golden Module")
        preview_modules = _select_modules(vp3)
        for m in preview_modules:
            st.markdown(f"- `{_MOD_DESC.get(m, m)}`")
        st.caption("검증된 Golden Module을 핀맵에 맞게 LLM이 적응합니다. 핵심 로직은 변경하지 않습니다.")

        st.divider()

        # ── 실행 버튼 ─────────────────────────────────────────────────────
        if not st.session_state.get("step3_job_id") and not st.session_state.get("step3_result"):
            if st.button(
                "Step 3 실행 — Golden Module 적응 코드 생성",
                type="primary",
                disabled=not backend_ok,
                key="btn_step3_run",
            ):
                try:
                    _sr = requests.post(
                        f"{BACKEND_URL}/v1/generate-step3",
                        json={
                            "validated_pins": vp3,
                            "prompt": st.session_state.step3_prompt,
                        },
                        timeout=30,
                    )
                    if _sr.status_code == 200:
                        st.session_state.step3_job_id = _sr.json()["job_id"]
                        st.rerun()
                    else:
                        st.error(f"오류 {_sr.status_code}: {_sr.text[:200]}")
                except Exception as _e:
                    st.error(f"요청 실패: {_e}")

        # ── 진행바 (폴링) ─────────────────────────────────────────────────
        if st.session_state.get("step3_job_id") and not st.session_state.get("step3_result"):
            import time as _time
            _jid = st.session_state.step3_job_id
            try:
                _jr = requests.get(f"{BACKEND_URL}/v1/step3-status/{_jid}", timeout=5)
                _jd = _jr.json()
            except Exception:
                _jd = {"status": "running", "progress": 0, "message": "연결 중..."}

            _pct = _jd.get("progress", 0)
            _msg = _jd.get("message", "")
            _jst = _jd.get("status", "running")

            st.progress(_pct / 100, text=_msg)
            st.caption(f"진행: {_pct}%  |  상태: {_jst}")

            if _jst == "complete":
                st.session_state.step3_result = _jd.get("result")
                st.session_state.step3_job_id = None
                st.rerun()
            elif _jst == "error":
                st.error(f"오류: {_msg}")
                st.session_state.step3_job_id = None
            else:
                _time.sleep(2)
                st.rerun()

        # ── 결과 표시 + 다운로드 ──────────────────────────────────────────
        if st.session_state.get("step3_result"):
            _res = st.session_state.step3_result
            _mods = _res.get("modules", {})
            _sel  = _res.get("selected", [])

            st.success(f"완료 — {len(_mods)}개 모듈 적응 완료: {', '.join(_sel)}")

            # 핀맵 요약 확인
            with st.expander("적용된 핀맵 요약", expanded=False):
                st.text(_res.get("pinmap_summary", ""))

            # 각 모듈 코드 보기 + 다운로드
            for mod_name, mod_code in _mods.items():
                with st.expander(f"📄 {mod_name} 적응 코드", expanded=False):
                    _tabs = st.tabs([f"{mod_name}.h", f"{mod_name}.c"])
                    with _tabs[0]:
                        st.code(mod_code.get("h", ""), language="c")
                    with _tabs[1]:
                        st.code(mod_code.get("c", ""), language="c")

                col_h, col_c, _ = st.columns([1, 1, 3])
                with col_h:
                    st.download_button(
                        f"⬇ {mod_name}.h",
                        data=mod_code.get("h", "").encode(),
                        file_name=f"{mod_name}.h",
                        mime="text/plain",
                        key=f"dl_{mod_name}_h",
                    )
                with col_c:
                    st.download_button(
                        f"⬇ {mod_name}.c",
                        data=mod_code.get("c", "").encode(),
                        file_name=f"{mod_name}.c",
                        mime="text/plain",
                        key=f"dl_{mod_name}_c",
                    )

            st.divider()

            # ── HAL 코드에 주입 (Step 2 ZIP 업로드된 경우) ───────────────
            _hal_zip_bytes = st.session_state.get("step3_hal_zip")
            if _hal_zip_bytes:
                st.subheader("HAL 코드에 적응 코드 주입")
                from agent.step3_codegen_agent import Step3Agent as _S3A
                _injected: Dict[str, bytes] = {}
                try:
                    with _zf.ZipFile(_io.BytesIO(_hal_zip_bytes)) as _src_zip:
                        for _zname in _src_zip.namelist():
                            _raw = _src_zip.read(_zname)
                            try:
                                _src_txt = _raw.decode("utf-8")
                            except Exception:
                                _injected[_zname] = _raw
                                continue
                            for _mn, _mc in _mods.items():
                                _src_txt = _S3A.inject_into_marker(_src_txt, _mc.get("c", ""), "2")
                                _src_txt = _S3A.inject_into_marker(_src_txt, _mc.get("h", ""), "Includes")
                            _injected[_zname] = _src_txt.encode("utf-8")

                    _inj_buf = _io.BytesIO()
                    with _zf.ZipFile(_inj_buf, "w", _zf.ZIP_DEFLATED) as _out_zip:
                        for _mn, _mc in _mods.items():
                            _out_zip.writestr(f"Step3_Modules/{_mn}.h", _mc.get("h", ""))
                            _out_zip.writestr(f"Step3_Modules/{_mn}.c", _mc.get("c", ""))
                        for _zname, _data in _injected.items():
                            _out_zip.writestr(_zname, _data)

                    st.success("주입 완료 — HAL 코드 + Step 3 모듈 통합 ZIP")
                    st.download_button(
                        "⬇ 통합 ZIP 다운로드 (HAL + Step 3)",
                        data=_inj_buf.getvalue(),
                        file_name=f"{vp3.get('chip','STM32G4')}_Integrated.zip",
                        mime="application/zip",
                        key="dl_integrated_zip",
                    )
                except Exception as _ie:
                    st.error(f"주입 오류: {_ie}")
            else:
                st.info("Step 2 HAL 코드 ZIP을 업로드하면 USER CODE 마커에 자동 주입된 통합 ZIP을 다운로드할 수 있습니다.")

            # ── 적응 모듈만 ZIP 다운로드 ─────────────────────────────────
            _zbuf = _io.BytesIO()
            with _zf.ZipFile(_zbuf, "w", _zf.ZIP_DEFLATED) as _z:
                for _mn, _mc in _mods.items():
                    _z.writestr(f"{_mn}.h", _mc.get("h", ""))
                    _z.writestr(f"{_mn}.c", _mc.get("c", ""))
            st.download_button(
                "⬇ 적응 모듈만 ZIP 다운로드",
                data=_zbuf.getvalue(),
                file_name=f"{vp3.get('chip','STM32G4')}_Step3_Modules.zip",
                mime="application/zip",
                key="dl_step3_zip",
            )

            if st.button("다시 실행", key="btn_step3_reset"):
                st.session_state.step3_result = None
                st.session_state.step3_job_id = None
                st.session_state.step3_hal_zip = None
                st.rerun()
