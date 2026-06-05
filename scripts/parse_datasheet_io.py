#!/usr/bin/env python3
"""ST 데이터시트 'Pin definitions' 표 → 핀별 I/O structure(FT/TT) JSON.

CubeMX MCU DB에는 5V 내성(FT/TT) 정보가 없어서, 데이터시트 핀 표에서 추출한다.
FT=5V tolerant, TT=3.6V tolerant (접미사 _a 아날로그스위치, _f Fm+, _c/_d USB-C 등).

I/O structure는 패드 설계 속성이라 STM32G4 패밀리 내 공통 핀에서 사실상 동일 →
G474 데이터시트에서 뽑아 G4 패밀리 맵으로 둔다(정확 G431은 G431 DS로 갱신 가능).

입력 : dataset/official_docs/stm32g474re.pdf  (pdftotext -layout 필요)
출력 : agent/pin_io_structure.json   {"STM32G4": {"PA8": "FT_a", "PB11": "TT_a", ...}}
"""
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PDF = os.path.join(ROOT, "dataset", "official_docs", "stm32g474re.pdf")
OUT = os.path.join(ROOT, "agent", "pin_io_structure.json")

# 핀명 … I/O … 구조코드. (I/O = 입출력핀, 코드는 FT/TT 계열 또는 B/RST)
_PAT = re.compile(r"\b(P[A-G]\d{1,2})(?:-[A-Z0-9_]+)?\b.*?\bI/O\b\s+(FT[a-z_]*|TT[a-z_]*|TC|RST|B)\b")


def main():
    if not os.path.exists(PDF):
        raise SystemExit(f"데이터시트 없음: {PDF}")
    txt = subprocess.run(
        ["pdftotext", "-layout", "-f", "45", "-l", "95", PDF, "-"],
        capture_output=True, text=True,
    ).stdout
    io = {}
    for line in txt.splitlines():
        m = _PAT.search(line)
        if m:
            io.setdefault(m.group(1), m.group(2))  # 첫 등장(본 표) 우선
    if len(io) < 40:
        raise SystemExit(f"추출 실패 가능 — {len(io)}핀만 인식. PDF/페이지 범위 확인.")
    db = {"STM32G4": dict(sorted(io.items()))}
    with open(OUT, "w") as fp:
        json.dump(db, fp, ensure_ascii=False, indent=0, sort_keys=True)
    from collections import Counter
    print(f"STM32G4: {len(io)}핀 → {OUT}")
    print("분포:", dict(Counter(v.split('_')[0] for v in io.values())))


if __name__ == "__main__":
    main()
