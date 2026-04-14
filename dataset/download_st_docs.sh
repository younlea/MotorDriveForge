#!/usr/bin/env bash
# ============================================================
# STM32G4 Motor Drive Agent — ST 공식 문서 다운로드 스크립트
# 실행 환경: 인터넷 접근이 허용된 PC or DGX Spark에서 실행
# 사용법: chmod +x download_st_docs.sh && ./download_st_docs.sh
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_BASE="$SCRIPT_DIR/official_docs"

download() {
    local url="$1"
    local dest="$2"
    local name="$3"
    if [ -f "$dest" ]; then
        echo "[SKIP] $name (already exists)"
        return
    fi
    echo "[DL]   $name ..."
    curl -L --retry 3 --retry-delay 2 -o "$dest" "$url" && \
        echo "[OK]   $name ($(du -sh "$dest" | cut -f1))" || \
        echo "[FAIL] $name"
}

# ──────────────────────────────────────────────────────────
# 1. Reference Manual & Datasheet
# ──────────────────────────────────────────────────────────
DIR="$OUT_BASE/reference_manual"
mkdir -p "$DIR"

download \
    "https://www.st.com/resource/en/reference_manual/rm0440-stm32g4-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf" \
    "$DIR/RM0440_STM32G4_Reference_Manual.pdf" \
    "RM0440 STM32G4 Reference Manual"

DIR="$OUT_BASE/datasheets"
mkdir -p "$DIR"

download \
    "https://www.st.com/resource/en/datasheet/stm32g474re.pdf" \
    "$DIR/STM32G474_datasheet.pdf" \
    "STM32G474 Datasheet"

download \
    "https://www.st.com/resource/en/datasheet/stm32g431rb.pdf" \
    "$DIR/STM32G431_datasheet.pdf" \
    "STM32G431 Datasheet"

download \
    "https://www.st.com/resource/en/datasheet/stspin32g4.pdf" \
    "$DIR/STSPIN32G4_datasheet.pdf" \
    "STSPIN32G4 Integrated Motor Driver Datasheet"

# ──────────────────────────────────────────────────────────
# 2. Application Notes
# ──────────────────────────────────────────────────────────
DIR="$OUT_BASE/application_notes"
mkdir -p "$DIR"

download \
    "https://www.st.com/resource/en/application_note/an5306-operational-amplifier-opamp-usage-in-stm32g4-series-stmicroelectronics.pdf" \
    "$DIR/AN5306_OPAMP_current_sensing.pdf" \
    "AN5306 - STM32G4 OPAMP / 전류센싱 (션트 PGA)"

download \
    "https://www.st.com/resource/en/application_note/an5789-considerations-on-bootstrap-circuitry-for-gate-drivers-stmicroelectronics.pdf" \
    "$DIR/AN5789_bootstrap_circuit_design.pdf" \
    "AN5789 - Bootstrap 회로 설계 (게이트드라이버)"

download \
    "https://www.st.com/resource/en/application_note/an4277-how-to-use-pwm-shutdown-for-motor-control-and-digital-power-conversion-on-stm32-mcus-stmicroelectronics.pdf" \
    "$DIR/AN4277_PWM_shutdown_protection.pdf" \
    "AN4277 - PWM 셧다운 & BRK 보호 (OCP/OVP)"

download \
    "https://www.st.com/resource/en/application_note/an4539-hrtim-cookbook-stmicroelectronics.pdf" \
    "$DIR/AN4539_HRTIM_cookbook.pdf" \
    "AN4539 - HRTIM Cookbook (184ps PWM, 데드타임)"

download \
    "https://www.st.com/resource/en/application_note/an4220-sensorless-sixstep-bldc-commutation-stmicroelectronics.pdf" \
    "$DIR/AN4220_sensorless_6step_BLDC.pdf" \
    "AN4220 - 센서리스 6-Step BLDC 코뮤테이션"

download \
    "https://www.st.com/resource/en/application_note/an4835-highside-current-sensing-for-applications-using-high-commonmode-voltage-stmicroelectronics.pdf" \
    "$DIR/AN4835_highside_current_sensing.pdf" \
    "AN4835 - 고측 전류 센싱 (High-side, 공통모드 전압)"

download \
    "https://www.st.com/resource/en/application_note/an5036-guidelines-for-thermal-management-on-stm32-applications-stmicroelectronics.pdf" \
    "$DIR/AN5036_thermal_management.pdf" \
    "AN5036 - 열 관리 가이드 (접합온도, PCB 방열)"

download \
    "https://www.st.com/resource/en/application_note/an4938-getting-started-with-stm32-mcu-hardware-development-stmicroelectronics.pdf" \
    "$DIR/AN4938_hardware_development_guide.pdf" \
    "AN4938 - STM32 하드웨어 개발 가이드 (전원, 디커플링)"

download \
    "https://www.st.com/resource/en/application_note/an4013-stm32-cross-series-timer-overview-stmicroelectronics.pdf" \
    "$DIR/AN4013_timer_overview.pdf" \
    "AN4013 - STM32 타이머 총괄 개요"

# ──────────────────────────────────────────────────────────
# 3. Evaluation Board / Reference Design Docs
# ──────────────────────────────────────────────────────────
DIR="$OUT_BASE/eval_boards"
mkdir -p "$DIR"

download \
    "https://www.st.com/resource/en/user_manual/um2850-getting-started-with-the-evspin32g4-evspin32g4nh-stmicroelectronics.pdf" \
    "$DIR/UM2850_EVSPIN32G4_user_manual.pdf" \
    "UM2850 - EVSPIN32G4 사용자 매뉴얼 (단일 모터)"

download \
    "https://www.st.com/resource/en/user_manual/um2896-getting-started-with-the-evspin32g4dual--stmicroelectronics.pdf" \
    "$DIR/UM2896_EVSPIN32G4_DUAL_user_manual.pdf" \
    "UM2896 - EVSPIN32G4-DUAL 사용자 매뉴얼 (이중 모터)"

download \
    "https://www.st.com/resource/en/schematic_pack/evspin32g4-dual-schematics.pdf" \
    "$DIR/EVSPIN32G4_DUAL_schematics.pdf" \
    "EVSPIN32G4-DUAL 회로도 (레퍼런스 설계)"

download \
    "https://www.st.com/resource/en/user_manual/um2719-getting-started-with-the-steval-spin3201-stmicroelectronics.pdf" \
    "$DIR/UM2719_STEVAL_SPIN3201_dual_motor.pdf" \
    "UM2719 - STEVAL-SPIN3201 이중 모터 평가 보드"

download \
    "https://www.st.com/resource/en/schematic_pack/steval-spin3201-schematic.pdf" \
    "$DIR/STEVAL_SPIN3201_schematics.pdf" \
    "STEVAL-SPIN3201 회로도 (이중 모터 레퍼런스)"

# ──────────────────────────────────────────────────────────
# 4. SDK & Workbench Docs
# ──────────────────────────────────────────────────────────
DIR="$OUT_BASE/sdk_docs"
mkdir -p "$DIR"

download \
    "https://www.st.com/resource/en/user_manual/um3027-how-to-use-stm32-motor-control-sdk-v60-workbench-stmicroelectronics.pdf" \
    "$DIR/UM3027_MCSDK_v6_workbench.pdf" \
    "UM3027 - X-CUBE-MCSDK v6 Workbench 사용설명서"

download \
    "https://www.st.com/resource/en/user_manual/um2538-stm32-motorcontrol-pack-using-the-foc-algorithm-for-threephase-lowvoltage-and-lowcurrent-motor-evaluation-stmicroelectronics.pdf" \
    "$DIR/UM2538_FOC_algorithm_pack.pdf" \
    "UM2538 - STM32 Motor Control Pack FOC 알고리즘"

echo ""
echo "============================================"
echo " 다운로드 완료! 결과 확인:"
find "$OUT_BASE" -name "*.pdf" | sort | while read f; do
    echo "  $(du -sh "$f" | cut -f1)  $(basename "$f")"
done
echo "============================================"
