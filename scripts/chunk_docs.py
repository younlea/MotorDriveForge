#!/usr/bin/env python3
"""
청킹 전략별 텍스트 분할
입력: dataset/parsed_text/ 하위 .txt
출력: dataset/chunks/{파일명}_chunks.jsonl

청킹 전략:
- RM0440 (reference_manual): 섹션 단위, max 1000 tokens
- Application Notes: 회로 블록 단위, max 800 tokens
- 나머지: sliding window 512 tokens, overlap 128

필요 패키지: (없음 — 표준 라이브러리만 사용)
"""

import argparse
import json
import logging
import re
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path("/Users/younlea/source_code/MotorDriveForge")
DEFAULT_INPUT = BASE_DIR / "dataset/parsed_text"
DEFAULT_OUTPUT = BASE_DIR / "dataset/chunks"

# 대략적인 토큰 수 추정 (문자 수 / 4)
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def chunk_by_section(text: str, max_tokens: int = 1000) -> list[dict]:
    """섹션 헤더(##, 숫자.숫자) 기준 청킹"""
    # RM0440 같은 레퍼런스 매뉴얼: 챕터/섹션 기준
    section_pattern = re.compile(
        r"(?m)^(?:(?:\d+\.){1,3}\d*\s+[A-Z][^\n]{5,}|#{1,3}\s+.+)$"
    )
    splits = list(section_pattern.finditer(text))

    chunks = []
    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        section_text = text[start:end].strip()
        if not section_text:
            continue

        # max_tokens 초과 시 추가 분할
        if estimate_tokens(section_text) <= max_tokens:
            chunks.append({"text": section_text, "section": match.group().strip()})
        else:
            # 추가 슬라이딩 윈도우
            sub = sliding_window(section_text, max_tokens, overlap=128)
            for s in sub:
                chunks.append({"text": s, "section": match.group().strip()})

    if not chunks:
        # 섹션 없으면 슬라이딩 윈도우 폴백
        chunks = [{"text": t, "section": ""} for t in sliding_window(text, max_tokens)]

    return chunks


def chunk_by_block(text: str, max_tokens: int = 800) -> list[dict]:
    """Figure/Table/번호 섹션 기준 청킹 (Application Notes)"""
    block_pattern = re.compile(
        r"(?m)^(?:Figure\s+\d+|Table\s+\d+|\d+\s+[A-Z][^\n]{10,})$"
    )
    splits = list(block_pattern.finditer(text))

    if not splits:
        return [{"text": t, "section": ""} for t in sliding_window(text, max_tokens)]

    chunks = []
    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        block_text = text[start:end].strip()
        if not block_text:
            continue
        if estimate_tokens(block_text) <= max_tokens:
            chunks.append({"text": block_text, "section": match.group().strip()})
        else:
            for s in sliding_window(block_text, max_tokens, overlap=100):
                chunks.append({"text": s, "section": match.group().strip()})

    return chunks


def sliding_window(text: str, max_tokens: int = 512, overlap: int = 128) -> list[str]:
    """슬라이딩 윈도우 청킹"""
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap * CHARS_PER_TOKEN
    step = max_chars - overlap_chars
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += step
    return chunks


def process_file(txt_path: Path, output_dir: Path) -> int:
    """텍스트 파일 → 청크 JSONL"""
    category = txt_path.parent.name  # reference_manual, application_notes, etc.
    doc_id = txt_path.stem

    out_path = output_dir / f"{doc_id}_chunks.jsonl"
    if out_path.exists():
        log.info("[SKIP] %s", doc_id)
        return 0

    text = txt_path.read_text(encoding="utf-8")

    # 청킹 전략 선택
    if category == "reference_manual":
        raw_chunks = chunk_by_section(text, max_tokens=1000)
    elif category == "application_notes":
        raw_chunks = chunk_by_block(text, max_tokens=800)
    else:
        raw_chunks = [{"text": t, "section": ""} for t in sliding_window(text, 512, 128)]

    # 페이지 추정 (4000자 = 1페이지 기준)
    chars_per_page = 4000
    records = []
    char_pos = 0
    for i, chunk in enumerate(raw_chunks):
        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{i}"))
        page_start = char_pos // chars_per_page + 1
        page_end = (char_pos + len(chunk["text"])) // chars_per_page + 1
        records.append({
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "chunk_index": i,
            "source": str(txt_path),
            "category": category,
            "section": chunk.get("section", ""),
            "page_start": page_start,
            "page_end": page_end,
            "text": chunk["text"],
            "token_estimate": estimate_tokens(chunk["text"]),
        })
        char_pos += len(chunk["text"])

    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    log.info("[OK] %s → %d 청크", doc_id, len(records))
    return len(records)


def main():
    parser = argparse.ArgumentParser(description="텍스트 청킹")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    txt_files = list(input_dir.rglob("*.txt"))
    # 메타데이터 파일 제외
    txt_files = [f for f in txt_files if not f.name.endswith("_meta.txt")]
    log.info("텍스트 파일 %d개 처리 예정", len(txt_files))

    total_chunks = 0
    for txt in txt_files:
        total_chunks += process_file(txt, output_dir)

    log.info("청킹 완료: 총 %d 청크 생성", total_chunks)


if __name__ == "__main__":
    main()
