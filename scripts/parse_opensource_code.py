#!/usr/bin/env python3
"""
오픈소스 프로젝트에서 핀 연결 정보 추출 → RAG 청크 생성
목적: Step 1 HW 설계 검증 시 "실제 동작하는 레퍼런스 설계"를 RAG 근거로 활용

입력: dataset/opensource/ 하위 프로젝트들
출력: dataset/chunks/opensource_pin_chunks.jsonl
      (embed_and_index.py가 *_chunks.jsonl 패턴으로 자동 수집)

지원 소스:
  STM32CubeG4  : .ioc 파일 (CubeMX 핀 설정, 117개 예제)
  stm32-esc    : pinmap_custom.h (PinMap 배열 + AF 번호)
  moteus       : hw/*/pinout.txt (STM32G4 핀 기능 텍스트)
  VESC         : hwconf/**/*_core.h (#define GPIO/PIN + ADC vector)
  flatmcu      : design/flatmcu.net (KiCad netlist, STM32G431 실제 FOC PCB)

필요 패키지: 없음 (표준 라이브러리만 사용)
"""

import json
import logging
import re
import uuid
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path("/home/robot/source-code/MotorDriveForge")
OPENSOURCE_DIR = BASE_DIR / "dataset/opensource"
OUTPUT_PATH = BASE_DIR / "dataset/chunks/opensource_pin_chunks.jsonl"


# ─────────────── 공통 헬퍼 ───────────────

def make_chunk(doc_id: str, text: str, source: str, section: str = "") -> dict:
    return {
        "doc_id": doc_id,
        "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}::{text[:60]}")),
        "chunk_index": 0,          # 재인덱싱은 main()에서
        "source": source,
        "category": "opensource_code",
        "section": section,
        "page_start": 1,
        "page_end": 1,
        "text": text,
        "token_estimate": len(text) // 4,
    }


# ─────────────── 1. STM32CubeG4 .ioc 파서 ───────────────
# CubeMX 공식 예제: PA10.Signal=TIM1_CH3 형태로 핀-페리페럴 매핑 명시

def _parse_single_ioc(ioc_path: Path) -> list[dict]:
    content = ioc_path.read_text(encoding="utf-8", errors="ignore")

    chip_name = ""
    m = re.search(r"Mcu\.Name=(\S+)", content)
    if m:
        chip_name = m.group(1)
    cpn = ""
    m2 = re.search(r"Mcu\.CPN=(\S+)", content)
    if m2:
        cpn = m2.group(1)

    # PAx.Signal=TIM1_CH1  or  PAx.Signal=S_TIM1_CH1 (복수 모드)
    pin_signals = re.findall(r"(P[A-H]\d+)\.Signal=(\S+)", content)
    if not pin_signals:
        return []

    # "S_TIM1_CH1" → "TIM1_CH1" (S_ 접두사는 정확히 2자 제거)
    pin_signals = [(p, s[2:] if s.startswith("S_") else s) for p, s in pin_signals]

    # 관심 페리페럴만 포함 (SWD/JTAG/VP/RCC/SYS/GPXTI/YS 제외)
    SKIP_PERIPH = {"RCC", "SYS", "VP", "GPXTI", "YS", "NVIC", "CORTEX"}
    periph_pins: dict[str, list[str]] = defaultdict(list)
    for pin, sig in pin_signals:
        periph = sig.split("_")[0]
        if periph not in SKIP_PERIPH and not periph.startswith("SYS"):
            periph_pins[periph].append(f"{pin}={sig}")

    if not periph_pins:
        return []

    example_name = ioc_path.stem
    chip_str = cpn or chip_name
    periph_str = "; ".join(
        f"{k}: {', '.join(v)}" for k, v in sorted(periph_pins.items())
    )
    text = (
        f"[STM32CubeG4 예제] {example_name} ({chip_str}): "
        f"페리페럴별 핀 설정 — {periph_str}."
    )

    doc_id = f"stm32cubeg4_{example_name}"
    return [make_chunk(doc_id, text, str(ioc_path), example_name)]


def parse_stm32cubeg4(base: Path) -> list[dict]:
    chunks: list[dict] = []
    ioc_files = list(base.rglob("*.ioc"))
    log.info("STM32CubeG4: .ioc 파일 %d개 처리", len(ioc_files))
    for ioc in ioc_files:
        try:
            chunks.extend(_parse_single_ioc(ioc))
        except Exception as e:
            log.warning("ioc 파싱 실패 %s: %s", ioc.name, e)
    log.info("STM32CubeG4: %d 청크 생성", len(chunks))
    return chunks


# ─────────────── 2. stm32-esc pinmap_custom.h 파서 ───────────────
# B-G431B-ESC1 보드 (STM32G431) 핀맵: PinMap 배열 + AF 번호

def parse_stm32_esc(base: Path) -> list[dict]:
    pinmap_h = base / "src/pinmap_custom.h"
    if not pinmap_h.exists():
        log.warning("stm32-esc: %s 없음", pinmap_h)
        return []

    content = pinmap_h.read_text(encoding="utf-8", errors="ignore")

    # const PinMap PinMap_XXX[] = { {Pxy, PERIPH, STM_PIN_DATA(..., GPIO_AFn_...)}, ... }
    blocks = re.findall(
        r"const PinMap\s+(PinMap_\w+)\[\]\s*=\s*\{([^;]+?)\};",
        content, re.DOTALL
    )

    chunks: list[dict] = []
    for map_name, block in blocks:
        # 주석 처리된 라인 제거 후 활성 엔트리 추출
        active_block = re.sub(r"//.*", "", block)
        entries = re.findall(
            r"\{(P[A-H]_\d+\w*)\s*,\s*(\w+)\s*,\s*STM_PIN_DATA\([^)]*?(GPIO_AF\w+)\)",
            active_block
        )
        if not entries:
            continue

        friendly = map_name.replace("PinMap_", "").replace("_", " ")
        entry_strs = [
            f"{pin.replace('_', '')}→{periph}({af})"
            for pin, periph, af in entries
        ]
        text = (
            f"[stm32-esc B-G431B-ESC1 (STM32G431)] {friendly} 핀맵: "
            f"{', '.join(entry_strs)}."
        )
        doc_id = f"stm32esc_{map_name}"
        chunks.append(make_chunk(doc_id, text, str(pinmap_h), friendly))

    log.info("stm32-esc: %d 청크 생성", len(chunks))
    return chunks


# ─────────────── 3. moteus pinout.txt 파서 ───────────────
# moteus hw/*/pinout.txt: "PA0 TIM2_CH1 TIM5_CH1 ADC1_IN1 ADC2_IN1 ANALOG" 형태

def parse_moteus(base: Path) -> list[dict]:
    chunks: list[dict] = []
    BLOCK_SIZE = 20

    for pinout in sorted(base.rglob("pinout.txt")):
        # hw/n1/pinout.txt → board=n1
        board_name = pinout.parent.name
        content = pinout.read_text(encoding="utf-8", errors="ignore")
        lines = [l.strip() for l in content.splitlines()
                 if l.strip() and not l.startswith("#")]
        if not lines:
            continue

        for i in range(0, len(lines), BLOCK_SIZE):
            block = lines[i:i + BLOCK_SIZE]
            pin_range = f"({i+1}~{i+len(block)}번)"
            text = (
                f"[moteus {board_name}] STM32G4 핀 기능 레퍼런스 {pin_range} — "
                + "; ".join(block)
            )
            doc_id = f"moteus_{board_name}_pinout_blk{i}"
            chunks.append(make_chunk(doc_id, text, str(pinout), f"moteus_{board_name}"))

    log.info("moteus: %d 청크 생성", len(chunks))
    return chunks


# ─────────────── 4. VESC hwconf *_core.h 파서 ───────────────
# #define HW_XXX_GPIO GPIOB + #define HW_XXX_PIN 5 → PB5
# ADC Vector 주석에서 채널→기능 매핑 추출

def parse_vesc(base: Path) -> list[dict]:
    chunks: list[dict] = []

    for hw_file in sorted(base.rglob("*_core.h")):
        try:
            content = hw_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # GPIO/PIN 쌍 수집
        gpio_map = {n: p for n, p in re.findall(
            r"#define\s+(HW_\w+)_GPIO\s+GPIO([A-H])\b", content)}
        pin_map = {n: num for n, num in re.findall(
            r"#define\s+(HW_\w+)_PIN\s+(\d+)\b", content)}

        gpio_defs: dict[str, str] = {
            name: f"P{gpio_map[name]}{pin_map[name]}"
            for name in gpio_map if name in pin_map
        }

        # ADC Vector 주석 파싱
        adc_entries: list[str] = []
        adc_m = re.search(r"ADC Vector(.*?)\*/", content, re.DOTALL)
        if adc_m:
            adc_entries = [
                l.strip(" *\t")
                for l in adc_m.group(1).splitlines()
                if re.search(r"\d+:.*IN\d+", l)
            ][:12]

        if not gpio_defs and not adc_entries:
            continue

        board_name = hw_file.stem.replace("_core", "")
        parts = [f"[VESC {board_name}] 보드 핀 설정."]
        if gpio_defs:
            parts.append("GPIO: " + ", ".join(
                f"{k}={v}" for k, v in sorted(gpio_defs.items())
            ) + ".")
        if adc_entries:
            parts.append("ADC 채널: " + "; ".join(adc_entries) + ".")

        text = " ".join(parts)
        doc_id = f"vesc_{board_name}"
        chunks.append(make_chunk(doc_id, text, str(hw_file), board_name))

    log.info("VESC: %d 청크 생성", len(chunks))
    return chunks


# ─────────────── 5. flatmcu KiCad netlist 파서 ───────────────
# STM32G431 FOC PCB 실제 설계: 핀번호→GPIO명 + 네트명(INHA/INLC/CUR_A 등)

def parse_flatmcu(base: Path) -> list[dict]:
    net_file = base / "design/flatmcu.net"
    if not net_file.exists():
        log.warning("flatmcu: %s 없음", net_file)
        return []

    content = net_file.read_text(encoding="utf-8", errors="ignore")

    # libpart 섹션에서 STM32G431 핀번호→GPIO명 매핑 추출
    libpart_m = re.search(
        r"\(libpart \(lib Custom\) \(part STM32G431CBTx\)(.*?)(?=\(libpart |\(nets )",
        content, re.DOTALL
    )
    if not libpart_m:
        log.warning("flatmcu: libpart STM32G431CBTx 못 찾음")
        return []

    pin_num_to_gpio = {
        num: name
        for num, name, _ in re.findall(
            r"\(pin \(num (\d+)\) \(name ([^\)]+)\) \(type ([^\)]+)\)",
            libpart_m.group(1)
        )
        if name.startswith(("PA", "PB", "PC", "PF", "PG"))
    }

    # nets 섹션에서 U4(STM32G431) 핀→시그널명 추출
    nets_m = re.search(r"\(nets(.*)", content, re.DOTALL)
    if not nets_m:
        return []

    net_blocks = re.findall(
        r"\(net \(code \d+\) \(name ([^\)]+)\)(.*?)(?=\(net \(code|\)\s*$)",
        nets_m.group(1), re.DOTALL
    )

    # 전원/VSS/VDD 등 제외할 네트
    SKIP_NETS = {"GND", "VCC", "VDD", "VSS", "VBAT", "VDDA", "VSSA",
                 "VREF+", "+3V3", "+5V", "+12V", "3V3_MCU", "3V3_MCU_A"}

    pin_to_signal: dict[str, str] = {}
    for net_name, body in net_blocks:
        u4_pins = re.findall(r"\(node \(ref U4\) \(pin (\d+)\)", body)
        for pin_num in u4_pins:
            gpio = pin_num_to_gpio.get(pin_num)
            if not gpio:
                continue
            # 네트명에서 마지막 /계층 이후만 사용: "/MCU/INHA" → "INHA"
            sig = net_name.strip().rstrip('"').split("/")[-1].rstrip('"')
            if sig not in SKIP_NETS:
                pin_to_signal[gpio] = sig

    if not pin_to_signal:
        return []

    # 기능별 그룹화
    groups: dict[str, list[str]] = defaultdict(list)
    for gpio, sig in sorted(pin_to_signal.items()):
        if "INH" in sig:
            groups["3상 High-side PWM"].append(f"{gpio}={sig}")
        elif "INL" in sig:
            groups["3상 Low-side PWM"].append(f"{gpio}={sig}")
        elif "CUR" in sig or "VA" == sig or "VB" == sig or "VC" == sig:
            groups["전류/전압 센싱 ADC"].append(f"{gpio}={sig}")
        elif "Hall" in sig:
            groups["Hall 센서"].append(f"{gpio}={sig}")
        elif "DRV" in sig:
            groups["모터드라이버 SPI"].append(f"{gpio}={sig}")
        elif sig in ("SWDIO", "SWCLK"):
            groups["SWD 디버그"].append(f"{gpio}={sig}")
        else:
            groups["기타"].append(f"{gpio}={sig}")

    group_str = "; ".join(
        f"{k}: {', '.join(sorted(v))}" for k, v in sorted(groups.items())
    )
    text = (
        f"[flatmcu FOC PCB (STM32G431CBTx)] 실제 3상 FOC 모터 드라이버 PCB 핀 연결 — "
        f"{group_str}."
    )
    doc_id = "flatmcu_stm32g431_foc"
    chunk = make_chunk(doc_id, text, str(net_file), "flatmcu_foc_pcb")

    log.info("flatmcu: 1 청크 생성 (%d 핀 매핑)", len(pin_to_signal))
    return [chunk]


# ─────────────── main ───────────────

def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict] = []
    all_chunks.extend(parse_stm32cubeg4(OPENSOURCE_DIR / "STM32CubeG4"))
    all_chunks.extend(parse_stm32_esc(OPENSOURCE_DIR / "stm32-esc"))
    all_chunks.extend(parse_moteus(OPENSOURCE_DIR / "moteus"))
    all_chunks.extend(parse_vesc(OPENSOURCE_DIR / "bldc_vesc"))
    all_chunks.extend(parse_flatmcu(OPENSOURCE_DIR / "flatmcu"))

    # doc_id별 chunk_index 재부여
    counters: dict[str, int] = defaultdict(int)
    for chunk in all_chunks:
        chunk["chunk_index"] = counters[chunk["doc_id"]]
        counters[chunk["doc_id"]] += 1

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    log.info("완료: 총 %d 청크 → %s", len(all_chunks), OUTPUT_PATH)
    log.info("소스별: STM32CubeG4=%d, stm32-esc=%d, moteus=%d, VESC=%d, flatmcu=%d",
             sum(1 for c in all_chunks if "stm32cubeg4" in c["doc_id"]),
             sum(1 for c in all_chunks if "stm32esc" in c["doc_id"]),
             sum(1 for c in all_chunks if "moteus" in c["doc_id"]),
             sum(1 for c in all_chunks if "vesc" in c["doc_id"]),
             sum(1 for c in all_chunks if "flatmcu" in c["doc_id"]))


if __name__ == "__main__":
    main()
