#!/usr/bin/env python3
"""
CubeMX XML 파싱 → 핀 AF DB JSON 생성
입력: dataset/official_docs/cubemx_db/STM32G4*.xml
출력: dataset/pin_af_db.json

목표: {chip: {pin_name: [AF_list]}} JSON

멀티모터 필터: TIM1/TIM8/TIM20 채널 충돌 자동 감지

필요 패키지: lxml (없으면 xml.etree 사용)
설치: pip install lxml
"""

import argparse
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path("/Users/younlea/source_code/MotorDriveForge")
DEFAULT_XML_DIR = BASE_DIR / "dataset/official_docs/cubemx_db"
DEFAULT_OUTPUT = BASE_DIR / "dataset/pin_af_db.json"

# STM32G4 계열 커버 대상
G4_CHIPS = ["G431", "G471", "G474", "G491", "G4A1"]

# ── 핀 AF 하드코딩 기본 테이블 (STM32G474 기준, XML 없을 때 폴백) ──────────
FALLBACK_PIN_AF: dict[str, dict[str, list[str]]] = {
    "STM32G474RET6": {
        # TIM1 (FOC 모터1 6채널 PWM)
        "PA8":  ["TIM1_CH1",  "USART1_CK", "I2C3_SCL"],
        "PA9":  ["TIM1_CH2",  "USART1_TX", "I2C3_SMBA"],
        "PA10": ["TIM1_CH3",  "USART1_RX", "I2C3_SDA"],
        "PA11": ["TIM1_CH4",  "USB_DM",    "FDCAN1_RX"],
        "PB13": ["TIM1_CH1N", "SPI2_SCK",  "FDCAN2_TX"],
        "PB14": ["TIM1_CH2N", "SPI2_MISO", "FDCAN2_RX"],
        "PB15": ["TIM1_CH3N", "SPI2_MOSI"],
        "PA12": ["TIM1_ETR",  "USB_DP",    "FDCAN1_TX"],
        "PB12": ["TIM1_BKIN", "SPI2_NSS",  "FDCAN2_RX"],
        # TIM8 (FOC 모터2 6채널 PWM)
        "PC6":  ["TIM8_CH1",  "USART6_TX", "I2C4_SCL"],
        "PC7":  ["TIM8_CH2",  "USART6_RX", "I2C4_SDA"],
        "PC8":  ["TIM8_CH3",  "USART6_CK"],
        "PC9":  ["TIM8_CH4",  "I2C3_SDA"],
        "PA5":  ["TIM8_CH1N", "SPI1_SCK",  "DAC1_OUT2"],
        "PB0":  ["TIM8_CH2N", "ADC3_IN12", "COMP4_OUT"],
        "PB1":  ["TIM8_CH3N", "ADC3_IN1",  "COMP6_OUT"],
        "PA6":  ["TIM8_BKIN", "SPI1_MISO", "COMP1_OUT"],
        # TIM2 (엔코더)
        "PA0":  ["TIM2_CH1_ETR", "USART2_CTS", "COMP1_INM"],
        "PA1":  ["TIM2_CH2",     "USART2_RTS", "COMP1_INP"],
        "PA2":  ["TIM2_CH3",     "USART2_TX",  "COMP2_INM"],
        # TIM3 (Hall 센서)
        "PA4":  ["TIM3_CH2", "SPI1_NSS", "DAC1_OUT1"],
        "PB4":  ["TIM3_CH1", "SPI1_MISO"],
        "PB5":  ["TIM3_CH2", "SPI1_MOSI"],
        # ADC (전류 센싱)
        "PA3":  ["ADC1_IN4",  "USART2_RX",  "TIM2_CH4"],
        "PC0":  ["ADC1_IN6",  "ADC2_IN6"],
        "PC1":  ["ADC1_IN7",  "ADC2_IN7"],
        "PC2":  ["ADC1_IN8",  "ADC2_IN8"],
        "PC3":  ["ADC1_IN9",  "ADC2_IN9"],
        "PC4":  ["ADC2_IN5"],
        "PC5":  ["ADC2_IN11"],
        # FDCAN
        "PB8":  ["FDCAN1_RX",  "I2C1_SCL", "TIM16_CH1"],
        "PB9":  ["FDCAN1_TX",  "I2C1_SDA", "TIM17_CH1"],
        "PB5":  ["FDCAN2_RX",  "SPI1_MOSI"],
        "PB6":  ["FDCAN2_TX",  "I2C1_SCL", "TIM16_CH1N"],
        # SPI (EEPROM)
        "PA5":  ["SPI1_SCK",   "TIM8_CH1N"],
        "PA6":  ["SPI1_MISO",  "TIM8_BKIN"],
        "PA7":  ["SPI1_MOSI",  "ADC2_IN4"],
        "PD2":  ["SPI1_NSS"],
        # I2C
        "PB6":  ["I2C1_SCL",   "FDCAN2_TX"],
        "PB7":  ["I2C1_SDA",   "FDCAN2_RX"],
        # USART
        "PC10": ["USART3_TX",  "SPI3_SCK"],
        "PC11": ["USART3_RX",  "SPI3_MISO"],
        # OPAMP (내부 게인)
        "PA1":  ["OPAMP1_VINM", "TIM2_CH2"],
        "PA2":  ["OPAMP1_VINP", "TIM2_CH3"],
        "PA3":  ["OPAMP1_VOUT"],
        "PC5":  ["OPAMP2_VINM"],
        "PA7":  ["OPAMP2_VINP"],
        "PA6":  ["OPAMP2_VOUT"],
        "PB10": ["OPAMP3_VINM"],
        "PB0":  ["OPAMP3_VINP"],
        "PB1":  ["OPAMP3_VOUT"],
        "PB11": ["OPAMP4_VINM"],
        "PB13": ["OPAMP4_VINP"],
        "PB12": ["OPAMP4_VOUT"],
        "PC3":  ["OPAMP5_VINM"],
        "PC4":  ["OPAMP5_VINP"],
        "PC7":  ["OPAMP5_VOUT"],
        "PC1":  ["OPAMP6_VINM"],
        "PB12": ["OPAMP6_VINP"],
        "PA8":  ["OPAMP6_VOUT"],
    }
}
# G431은 OPAMP 3개, TIM8 없음
FALLBACK_PIN_AF["STM32G431RBT6"] = {
    k: [af for af in v if "TIM8" not in af and "OPAMP4" not in af
        and "OPAMP5" not in af and "OPAMP6" not in af]
    for k, v in FALLBACK_PIN_AF["STM32G474RET6"].items()
}


def parse_xml_file(xml_path: Path) -> dict[str, dict[str, list[str]]]:
    """CubeMX STM32G4xx.xml 파싱"""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        log.warning("XML 파싱 실패 %s: %s", xml_path.name, e)
        return {}

    # 칩명 추출 (파일명에서)
    chip_name = xml_path.stem  # e.g. STM32G474RETx

    pin_af: dict[str, list[str]] = {}

    # CubeMX XML 구조: <GPIO_Pin Name="PA0">...<PinSignal Name="TIM2_CH1">
    ns_prefix = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
        ns_prefix = ns

    for pin_el in root.iter(f"{ns_prefix}GPIO_Pin"):
        pin_name = pin_el.get("Name", "")
        if not pin_name:
            continue
        afs = []
        for sig in pin_el.iter(f"{ns_prefix}PinSignal"):
            sig_name = sig.get("Name", "")
            if sig_name:
                afs.append(sig_name)
        if afs:
            pin_af[pin_name] = afs

    return {chip_name: pin_af} if pin_af else {}


def build_db(xml_dir: Path) -> dict[str, dict[str, list[str]]]:
    """전체 XML → 통합 DB"""
    db: dict[str, dict[str, list[str]]] = {}

    xml_files = list(xml_dir.glob("STM32G4*.xml"))
    log.info("XML 파일 %d개 발견", len(xml_files))

    if not xml_files:
        log.warning("XML 없음 — 폴백 하드코딩 테이블 사용")
        return FALLBACK_PIN_AF

    for xml_path in xml_files:
        parsed = parse_xml_file(xml_path)
        db.update(parsed)
        log.info("[OK] %s → %d 핀", xml_path.name, sum(len(v) for v in parsed.values()))

    # 폴백: 해당 칩이 없으면 추가
    for chip, pins in FALLBACK_PIN_AF.items():
        if chip not in db:
            db[chip] = pins

    return db


# ── 멀티모터 충돌 감지 ────────────────────────────────────────────────────────
MOTOR_TIMER_CHANNELS = {
    "TIM1": ["TIM1_CH1", "TIM1_CH2", "TIM1_CH3", "TIM1_CH1N", "TIM1_CH2N", "TIM1_CH3N"],
    "TIM8": ["TIM8_CH1", "TIM8_CH2", "TIM8_CH3", "TIM8_CH1N", "TIM8_CH2N", "TIM8_CH3N"],
    "TIM20": ["TIM20_CH1", "TIM20_CH2", "TIM20_CH3", "TIM20_CH1N", "TIM20_CH2N", "TIM20_CH3N"],
}


def check_timer_conflicts(
    pin_af_db: dict[str, dict[str, list[str]]],
    chip: str,
    required_channels: list[str],
) -> dict:
    """
    멀티모터 타이머 채널 충돌 감지.
    required_channels: ["TIM1_CH1", "TIM1_CH2", ..., "TIM8_CH1", ...]
    """
    chip_pins = pin_af_db.get(chip, {})
    if not chip_pins:
        return {"conflict": False, "details": [f"칩 {chip} 정보 없음"]}

    # 각 required_channel을 지원하는 핀 찾기
    channel_to_pins: dict[str, list[str]] = {}
    for pin, afs in chip_pins.items():
        for af in afs:
            for ch in required_channels:
                if ch == af or af.startswith(ch):
                    channel_to_pins.setdefault(ch, []).append(pin)

    # 핀 중복 검사: 같은 핀이 두 채널에 할당 가능한지
    pin_to_channels: dict[str, list[str]] = {}
    for ch, pins in channel_to_pins.items():
        for pin in pins:
            pin_to_channels.setdefault(pin, []).append(ch)

    conflicts = []
    for pin, chans in pin_to_channels.items():
        if len(chans) > 1:
            # 다른 타이머 채널이 같은 핀에 매핑 → 충돌
            timers = set(re.match(r"(TIM\d+)", c).group(1) for c in chans if re.match(r"(TIM\d+)", c))
            if len(timers) > 1:
                conflicts.append({
                    "pin": pin,
                    "channels": chans,
                    "timers": list(timers),
                })

    # 미지원 채널 확인
    missing = [ch for ch in required_channels if ch not in channel_to_pins]

    return {
        "conflict": bool(conflicts or missing),
        "pin_conflicts": conflicts,
        "missing_channels": missing,
        "details": [
            f"핀 {c['pin']}: {c['channels']} 충돌 (타이머 {c['timers']})"
            for c in conflicts
        ] + [f"채널 {ch}: 해당 핀 없음" for ch in missing],
    }


def main():
    parser = argparse.ArgumentParser(description="CubeMX XML → 핀 AF DB")
    parser.add_argument("--xml-dir", default=str(DEFAULT_XML_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--test-conflict", action="store_true",
                        help="멀티모터 충돌 검사 예시 실행")
    args = parser.parse_args()

    xml_dir = Path(args.xml_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = build_db(xml_dir)

    output_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("저장 완료: %s (%d 칩)", output_path, len(db))

    if args.test_conflict:
        chip = "STM32G474RET6"
        # 2모터 FOC: TIM1(6ch) + TIM8(6ch) 필요
        required = (
            MOTOR_TIMER_CHANNELS["TIM1"]
            + MOTOR_TIMER_CHANNELS["TIM8"]
        )
        result = check_timer_conflicts(db, chip, required)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
