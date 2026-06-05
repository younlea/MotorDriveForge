#!/usr/bin/env python3
"""STM32CubeMX MCU DB(XML) → 컴팩트한 핀별 function 옵션 JSON.

CubeMX 설치폴더의 db/mcu/STM32G4*.xml 을 파싱해, 칩(RefName)별로 각 I/O 핀에서
선택 가능한 신호(Signal) 목록을 추출한다. Step 2 .ioc 생성 시 'function 드롭다운'의
권위 있는 옵션 소스로 사용된다. (raw DB는 ~600MB라 커밋하지 않고, 이 파생 JSON만 커밋.)

입력 : dataset/STM32CubeMX/db/mcu/STM32G4*.xml  (CubeMX에서 복사)
출력 : agent/pin_function_options.json
        { "STM32G431R(6-8-B)Ix": { "package": "UFBGA64",
            "pins": { "PA8": ["TIM1_CH1", ...], ... } }, ... }

키(RefName)는 backend의 _chip_identity()가 만드는 Mcu.Name과 동일하다.
"""
import glob
import json
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MCU_DIR = os.path.join(ROOT, "dataset", "STM32CubeMX", "db", "mcu")
OUT = os.path.join(ROOT, "agent", "pin_function_options.json")

# 드롭다운에서 의미 없는(혹은 .Signal로 직접 못 쓰는) 신호 제외
_SKIP = {"GPIO"}


def _tag(e):
    return e.tag.split("}")[-1]


def parse_mcu(path):
    root = ET.parse(path).getroot()
    pins = {}
    for pin in root:
        if _tag(pin) != "Pin" or pin.get("Type") != "I/O":
            continue
        name = pin.get("Name", "").split("-")[0].split("/")[0].strip()  # "PA0-OSC..." → "PA0"
        if not name.startswith("P"):
            continue
        sigs = [
            s.get("Name")
            for s in pin
            if _tag(s) == "Signal" and s.get("Name") and s.get("Name") not in _SKIP
        ]
        if sigs:
            pins[name] = sorted(set(sigs))
    return root.get("RefName"), root.get("Package"), pins


def main():
    files = sorted(glob.glob(os.path.join(MCU_DIR, "STM32G4*.xml")))
    if not files:
        raise SystemExit(f"MCU xml 없음: {MCU_DIR} (CubeMX db/mcu 를 여기에 복사했는지 확인)")
    db = {}
    for f in files:
        ref, pkg, pins = parse_mcu(f)
        if ref and pins:
            db[ref] = {"package": pkg, "pins": pins}
    with open(OUT, "w") as fp:
        json.dump(db, fp, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    size_kb = os.path.getsize(OUT) / 1024
    print(f"칩 {len(db)}종 → {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
