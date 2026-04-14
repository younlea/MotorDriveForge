#!/usr/bin/env python3
"""
BM25 역인덱스 구축
입력: dataset/chunks/ 하위 *_chunks.jsonl
출력: dataset/bm25_index/bm25_index.pkl + doc_map.jsonl

필요 패키지: rank_bm25
설치: pip install rank_bm25
"""

import argparse
import json
import logging
import pickle
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path("/Users/younlea/source_code/MotorDriveForge")
DEFAULT_CHUNKS_DIR = BASE_DIR / "dataset/chunks"
DEFAULT_INDEX_DIR = BASE_DIR / "dataset/bm25_index"


def tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return text.split()


def load_chunks(chunks_dir: Path) -> list[dict]:
    records = []
    for jsonl in sorted(chunks_dir.glob("*_chunks.jsonl")):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    log.info("총 %d 청크 로드", len(records))
    return records


def build_index(records: list[dict]) -> tuple[BM25Okapi, list[dict]]:
    tokenized = [tokenize(r["text"]) for r in records]
    bm25 = BM25Okapi(tokenized)
    doc_map = [
        {
            "index": i,
            "doc_id": r["doc_id"],
            "chunk_id": r["chunk_id"],
            "category": r.get("category", ""),
            "section": r.get("section", ""),
            "source": r.get("source", ""),
            "text_preview": r["text"][:300],
        }
        for i, r in enumerate(records)
    ]
    return bm25, doc_map


def test_search(bm25: BM25Okapi, doc_map: list[dict], query: str, top_k: int = 5):
    """검색 테스트"""
    tokens = tokenize(query)
    scores = bm25.get_scores(tokens)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    print(f"\n검색: '{query}'")
    print("-" * 60)
    for rank, idx in enumerate(top_indices, 1):
        doc = doc_map[idx]
        print(f"[{rank}] score={scores[idx]:.3f} | {doc['doc_id']} | {doc['section'][:40]}")
        print(f"    {doc['text_preview'][:120]}...")
    print()


def main():
    parser = argparse.ArgumentParser(description="BM25 인덱스 구축")
    parser.add_argument("--chunks-dir", default=str(DEFAULT_CHUNKS_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_INDEX_DIR))
    parser.add_argument("--test-query", default="TIM1 TIM8 synchronization deadtime")
    args = parser.parse_args()

    chunks_dir = Path(args.chunks_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    records = load_chunks(chunks_dir)
    if not records:
        log.warning("청크 없음. chunk_docs.py 먼저 실행하세요.")
        return

    log.info("BM25 인덱스 구축 중...")
    bm25, doc_map = build_index(records)

    # 저장
    index_path = output_dir / "bm25_index.pkl"
    with open(index_path, "wb") as f:
        pickle.dump(bm25, f)
    log.info("인덱스 저장: %s", index_path)

    doc_map_path = output_dir / "doc_map.jsonl"
    with open(doc_map_path, "w", encoding="utf-8") as f:
        for doc in doc_map:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    log.info("문서맵 저장: %s (%d 항목)", doc_map_path, len(doc_map))

    # 검색 테스트
    test_search(bm25, doc_map, args.test_query)
    test_search(bm25, doc_map, "OPAMP offset calibration current sensing")
    test_search(bm25, doc_map, "bootstrap capacitor gate driver")


if __name__ == "__main__":
    main()
