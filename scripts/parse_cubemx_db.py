#!/usr/bin/env python3
"""STM32CubeMX MCU DB(XML) → 패밀리별 핀 function 옵션 JSON.

CubeMX 설치폴더의 db/mcu/STM32*.xml 을 파싱해, 칩(RefName)별로 각 I/O 핀에서 선택
가능한 신호(Signal) 목록을 추출한다. Step 2 .ioc 생성 시 'function 드롭다운'의 권위 있는
옵션 소스. (raw DB는 ~600MB라 커밋하지 않고, 이 파생 JSON만 커밋 — .gitignore 참고.)

패밀리별로 분리 저장해 backend가 필요한 패밀리만 로드한다(전체 ~16MB).

입력 : dataset/STM32CubeMX/db/mcu/STM32*.xml  (CubeMX에서 복사)
출력 : agent/pin_options/STM32<FAM>.json   (FAM = RefName[5:7], 예 G4/F4/H7/U5)
        { "STM32G431R(6-8-B)Ix": { "package": "UFBGA64",
            "pins": { "PA8": ["TIM1_CH1", ...], ... } }, ... }

키(RefName)는 backend _chip_identity()가 만드는 Mcu.Name과 동일하다.
"""
import glob
import json
import os
import xml.etree.ElementTree as ET
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MCU_DIR = os.path.join(ROOT, "dataset", "STM32CubeMX", "db", "mcu")
OUT_DIR = os.path.join(ROOT, "agent", "pin_options")

_SKIP = {"GPIO"}  # .Signal로 직접 못 쓰는 항목


def _tag(e):
    return e.tag.split("}")[-1]


def parse_mcu(path):
    root = ET.parse(path).getroot()
    pins = {}
    for pin in root:
        if _tag(pin) != "Pin" or pin.get("Type") != "I/O":
            continue
        name = pin.get("Name", "").split("-")[0].split("/")[0].strip()  # "PA0-OSC.." → "PA0"
        if not name.startswith("P"):
            continue
        sigs = sorted({
            s.get("Name") for s in pin
            if _tag(s) == "Signal" and s.get("Name") and s.get("Name") not in _SKIP
        })
        if sigs:
            pins[name] = sigs
    return root.get("RefName"), root.get("Package"), pins


def main():
    files = sorted(glob.glob(os.path.join(MCU_DIR, "STM32*.xml")))
    if not files:
        raise SystemExit(f"MCU xml 없음: {MCU_DIR} (CubeMX db/mcu 를 여기에 복사했는지 확인)")
    os.makedirs(OUT_DIR, exist_ok=True)
    by_family = defaultdict(dict)
    for f in files:
        ref, pkg, pins = parse_mcu(f)
        if not ref or not pins or not ref.startswith("STM32"):
            continue
        fam = ref[5:7]  # G4, F4, H7, U5, MP ...
        by_family[fam][ref] = {"package": pkg, "pins": pins}

    total = 0
    for fam, db in sorted(by_family.items()):
        out = os.path.join(OUT_DIR, f"STM32{fam}.json")
        with open(out, "w") as fp:
            json.dump(db, fp, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        total += len(db)
        print(f"  STM32{fam}: {len(db):4d} chips → {os.path.getsize(out)/1024:.0f} KB")
    print(f"총 {total} chips / {len(by_family)} families → {OUT_DIR}")


if __name__ == "__main__":
    main()
