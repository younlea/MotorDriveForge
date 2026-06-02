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
OLLAMA_URL = "http://localhost:11434"
QDRANT_URL = "http://localhost:6333"

CHIPS = [
    "STM32G431RBT6",
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
    ("pasted_image_b64", None),
    ("chat_history", []),
    ("go_to_step2", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# 사이드바 — 연결 상태
# ---------------------------------------------------------------------------

def check_service(url: str, path: str = "", timeout: int = 3) -> bool:
    try:
        r = requests.get(f"{url}{path}", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def status_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


with st.sidebar:
    st.title("STM32G4 Agent")
    st.caption("Motor Drive Firmware 자동화")
    st.divider()

    st.subheader("서비스 연결 상태")

    if st.button("새로고침", key="refresh_status"):
        st.rerun()

    backend_ok = check_service(BACKEND_URL, "/v1/health")
    ollama_ok = check_service(OLLAMA_URL, "/api/tags")
    qdrant_ok = check_service(QDRANT_URL, "/collections")

    st.write(f"{status_icon(backend_ok)} **Backend** (`{BACKEND_URL}`)")
    st.write(f"{status_icon(ollama_ok)} **Ollama** (`:{11434}`)")
    st.write(f"{status_icon(qdrant_ok)} **Qdrant** (`:{6333}`)")

    if backend_ok:
        try:
            r = requests.get(f"{BACKEND_URL}/v1/status", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("ollama_models"):
                    st.caption(f"모델: {', '.join(data['ollama_models'][:2])}")
                if data.get("qdrant_collections"):
                    st.caption(f"컬렉션: {', '.join(data['qdrant_collections'])}")
        except Exception:
            pass

    st.divider()
    st.caption("3-Step 파이프라인")
    step1_badge = "🟢" if st.session_state.review_passed else "⚪"
    st.write(f"{step1_badge} Step 1: 핀 검증")
    step2_badge = "🟢" if st.session_state.ioc_result else ("🟡" if st.session_state.review_passed else "⚪")
    st.write(f"{step2_badge} Step 2: 코드 생성")
    st.write("⚪ Step 3: 알고리즘 통합")

    st.divider()
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
        chip = st.selectbox("칩 선택", CHIPS, index=1)

        st.subheader("입력 방식")

        # ── 이미지 업로드 (주 입력) ────────────────────────────────────────
        st.markdown("**회로도 이미지** (권장 — Gemma 4 31B Vision 자동 분석)")
        schematic_image = st.file_uploader(
            "회로도 이미지 업로드",
            type=["jpg", "jpeg", "png", "bmp", "webp"],
            help="JPEG/PNG 형식 회로도. Gemma 4 31B가 핀맵을 자동 추출합니다.",
            label_visibility="collapsed",
        )
        if schematic_image:
            st.image(schematic_image, caption="업로드된 회로도", use_container_width=True)

        st.divider()

        # ── CSV 보조 입력 ──────────────────────────────────────────────────
        with st.expander("핀맵 CSV 직접 입력 (선택 — 이미지 없을 때 필수)", expanded=not bool(schematic_image)):
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
                    value=EXAMPLE_CSV.strip() if not schematic_image else "",
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
        st.markdown("**스크린샷 붙여넣기** (Ctrl+V)")
        pasted_val = _paste_comp(key="paste_zone", default=None)
        if pasted_val is not None:
            st.session_state.pasted_image_b64 = pasted_val if pasted_val else None

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
    has_image = schematic_image is not None or bool(st.session_state.pasted_image_b64)
    has_csv = bool(csv_text and csv_text.strip()) or (csv_file is not None)
    can_submit = has_image or has_csv

    run_disabled = not backend_ok or not can_submit
    if not backend_ok:
        st.warning("Backend 서버에 연결할 수 없습니다. `uvicorn backend.main:app --port 8000` 실행 후 재시도하세요.")
    elif not can_submit:
        st.info("회로도 이미지 또는 핀맵 CSV를 입력해주세요.")

    if has_image:
        st.success("회로도 이미지 감지 — Gemma 4 31B Vision이 핀맵을 자동 추출합니다.")

    btn_label = "검증 실행 (Vision + Rule Engine + RAG + LLM)" if has_image else "검증 실행 (Rule Engine + RAG + LLM)"

    if st.button(
        btn_label,
        type="primary",
        disabled=run_disabled,
        use_container_width=True,
        key="btn_review",
    ):
        if not prompt.strip():
            st.error("프롬프트를 입력하세요.")
            st.stop()

        spinner_msg = (
            "Gemma 4 31B Vision으로 회로도 분석 중 → Rule Engine → RAG → LLM 검증 (총 60~180초 소요)..."
            if has_image
            else "STM32G4 Agent 검증 중 (LLM 응답에 30~120초 소요될 수 있습니다)..."
        )

        with st.spinner(spinner_msg):
            try:
                # multipart/form-data 조립
                files: dict = {}
                data: dict = {"chip": chip, "prompt": prompt}

                if schematic_image is not None:
                    schematic_image.seek(0)
                    files["schematic_image"] = (
                        schematic_image.name,
                        schematic_image,
                        schematic_image.type or "image/jpeg",
                    )
                elif st.session_state.pasted_image_b64:
                    b64_data = st.session_state.pasted_image_b64.split(",", 1)[-1]
                    img_bytes = base64.b64decode(b64_data)
                    files["schematic_image"] = ("pasted_image.png", img_bytes, "image/png")

                if csv_file is not None:
                    csv_file.seek(0)
                    files["csv_file"] = (csv_file.name, csv_file, "text/csv")
                elif csv_text and csv_text.strip():
                    data["pinmap_csv"] = csv_text

                r = requests.post(
                    f"{BACKEND_URL}/v1/review",
                    data=data,
                    files=files if files else None,
                    timeout=300,
                )

                if r.status_code == 200:
                    report = r.json()
                    st.session_state.review_passed = True
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
                    st.stop()

            except requests.exceptions.Timeout:
                st.error("요청 시간 초과 (300초). 서버 부하를 확인하세요.")
                st.stop()
            except Exception as e:
                st.error(f"요청 실패: {e}")
                st.stop()

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

        if not errors and st.session_state.validated_pins:
            with st.expander("확정 핀 JSON 보기", expanded=False):
                st.json(st.session_state.validated_pins)

            # Step 2 이동 버튼
            if st.button(
                "Step 2: HAL 코드 생성하기 →",
                type="primary",
                key="go_to_step2_btn",
                use_container_width=True,
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
                            timeout=120,
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

    if not st.session_state.review_passed:
        st.info("Step 1 핀 검증을 먼저 통과해야 합니다.")
    else:
        vp = st.session_state.validated_pins
        st.success(f"Step 1 통과 — 칩: {vp.get('chip', '?')}, 핀 수: {len(vp.get('pins', []))}")

        with st.expander("생성 예정 파일 목록", expanded=True):
            st.markdown("""
- `main.c` / `main.h` — HAL 초기화
- `tim.c` / `tim.h` — TIM1/TIM8 PWM 설정
- `adc.c` / `adc.h` — ADC + OPAMP 전류 센싱
- `fdcan.c` / `fdcan.h` — FDCAN 통신
- `spi.c` / `spi.h` — SPI EEPROM (필요 시)
- `dma.c` / `dma.h` — DMA 설정
""")

        if st.button(
            "CubeMX 코드 생성",
            type="primary",
            disabled=not backend_ok,
            key="btn_gen_ioc",
        ):
            with st.spinner(".ioc 파일 생성 중..."):
                try:
                    r = requests.post(
                        f"{BACKEND_URL}/v1/generate-ioc",
                        json={"validated_pins": vp},
                        timeout=60,
                    )
                    if r.status_code == 200:
                        result = r.json()
                        st.session_state.ioc_result = result
                        st.success(result.get("message", "생성 완료"))
                    else:
                        st.error(f"오류 {r.status_code}: {r.text[:300]}")
                except Exception as e:
                    st.error(f"요청 실패: {e}")

        if st.session_state.ioc_result:
            ioc = st.session_state.ioc_result
            st.subheader("생성 결과")
            st.write(f"파일명: `{ioc.get('ioc_filename', '')}`")

            col_dl, _ = st.columns([1, 3])
            with col_dl:
                dl_url = f"{BACKEND_URL}{ioc.get('download_url', '')}"
                if st.button("IOC 파일 다운로드", key="btn_dl_ioc"):
                    try:
                        r = requests.get(dl_url, timeout=10)
                        if r.status_code == 200:
                            st.download_button(
                                label="저장",
                                data=r.content,
                                file_name=ioc.get("ioc_filename", "output.ioc"),
                                mime="application/octet-stream",
                            )
                        else:
                            st.error("다운로드 실패")
                    except Exception as e:
                        st.error(f"다운로드 오류: {e}")

            st.caption("Step 3 탭에서 FOC 알고리즘을 삽입할 수 있습니다.")


# ===========================================================================
# Tab 3 — Step 3 알고리즘 통합
# ===========================================================================

with tab3:
    st.header("Step 3 — 알고리즘 통합")

    if not st.session_state.ioc_result:
        st.info("Step 2 코드 생성을 먼저 완료해야 합니다.")
    else:
        st.success("Step 2 완료 — 알고리즘 통합 준비됨")

    st.subheader("통합 예정 모듈 (Golden Module)")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("""
**FOC 알고리즘**
- Clarke / Park 변환
- Space Vector PWM (SVPWM)
- PI 전류 제어기 (d/q축)
- 속도 PI 제어기
""")
    with col_b:
        st.markdown("""
**센서 / 보호**
- 증분형 엔코더 (TIM encoder mode)
- Hall sensor 6-step
- 과전류 / 과전압 보호 (BRK)
- 온도 모니터링
""")

    st.divider()

    algo_option = st.selectbox(
        "알고리즘 선택",
        [
            "BLDC FOC (증분형 엔코더)",
            "BLDC FOC (홀센서)",
            "BLDC FOC (센서리스 BEMF)",
            "PMSM FOC (증분형 엔코더)",
        ],
        disabled=not st.session_state.ioc_result,
    )

    if st.button(
        "알고리즘 통합 실행 (준비 중)",
        disabled=True,
        key="btn_integrate",
        help="Step 3 통합 기능은 구현 예정입니다. (Gemma-4-31B + Golden Module RAG)",
    ):
        pass

    st.info(
        "Step 3는 Gemma-4-31B (Q4_K_M) 모델과 Golden Module RAG를 사용하여 "
        "USER CODE BEGIN/END 영역에 FOC 알고리즘을 자동 삽입합니다. (구현 예정)"
    )
