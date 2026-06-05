#!/usr/bin/env python3
"""ST 데이터시트 'Pin definitions' 표 → 핀별 I/O structure(FT/TT) JSON.

CubeMX MCU DB에는 5V 내성(FT/TT) 정보가 없어서, 데이터시트 핀 표에서 추출한다.
FT=5V tolerant, TT=3.6V tolerant (접미사 _a 아날로그스위치, _f Fm+, _c/_d USB-C 등).

dataset/official_docs/ 의 데이터시트 PDF(stm32xxxx.pdf)를 모두 파싱해 **서브패밀리별**로
키를 만든다(STM32G431, STM32G474 ...). I/O structure는 같은 서브패밀리 내에선 동일하지만
서브패밀리 간엔 다를 수 있다(예: PB11 G431=FT, G474=TT). 또 패밀리 폴백 키(STM32G4)를
가장 핀 많은 서브패밀리로 둬서, 전용 데이터시트가 없는 칩(G491 등)도 근사값을 쓴다.

새 칩 추가: 해당 데이터시트(stm32<sub>.pdf)를 official_docs/에 넣고 이 스크립트 재실행.

입력 : dataset/official_docs/stm32*.pdf  (pdftotext -layout 필요)
출력 : agent/pin_io_structure.json   {"STM32G431": {"PA8":"FT_f",...}, "STM32G4": {...}, ...}
"""
import glob
import json
import os
import re
import subprocess
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "dataset", "official_docs")
OUT = os.path.join(ROOT, "agent", "pin_io_structure.json")

# 핀명 … I/O … 구조코드(FT/TT 계열 또는 B/RST)
_PAT = re.compile(r"\b(P[A-G]\d{1,2})(?:-[A-Z0-9_]+)?\b.*?\bI/O\b\s+(FT[a-z_]*|TT[a-z_]*|TC|RST|B)\b")


def parse_pdf(path):
    txt = subprocess.run(["pdftotext", "-layout", path, "-"],
                         capture_output=True, text=True).stdout
    io = {}
    for line in txt.splitlines():
        m = _PAT.search(line)
        if m:
            io.setdefault(m.group(1), m.group(2))  # 첫 등장(본 표) 우선
    return io


def main():
    # 데이터시트만: 파일명이 stm32 + 패밀리코드로 시작 (앱노트 an/dm/um/rm/evspin 제외)
    files = sorted(glob.glob(os.path.join(DOCS, "stm32[a-z][0-9]*.pdf")))
    db = {}
    for f in files:
        base = os.path.basename(f).lower()
        sub = "STM32" + base[5:9].upper()   # stm32g431c6 → STM32G431
        io = parse_pdf(f)
        if len(io) < 40:
            print(f"  skip {base} ({len(io)}핀 — 데이터시트 핀표 아님?)")
            continue
        db[sub] = dict(sorted(io.items()))
        print(f"  {sub:11s} ← {base}: {len(io)}핀 "
              f"{dict(Counter(v.split('_')[0] for v in io.values()))}")

    # 패밀리 폴백 키(STM32G4 등) = 그 패밀리에서 핀 가장 많은 서브패밀리
    for fam in {k[:7] for k in db}:
        best = max((k for k in db if k.startswith(fam)), key=lambda k: len(db[k]))
        db.setdefault(fam, dict(db[best]))

    if not db:
        raise SystemExit(f"파싱된 데이터시트 없음: {DOCS}/stm32*.pdf 확인")
    with open(OUT, "w") as fp:
        json.dump(db, fp, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"→ {OUT}  (키: {', '.join(sorted(db))})")


if __name__ == "__main__":
    main()
