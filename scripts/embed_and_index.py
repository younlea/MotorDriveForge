#!/usr/bin/env python3
"""
임베딩 → Qdrant 적재
입력: dataset/chunks/ 하위 *_chunks.jsonl
출력: Qdrant collection 'stm32g4_docs'

필요 패키지: sentence-transformers, qdrant-client
설치: pip install sentence-transformers qdrant-client
"""

import argparse
import json
import logging
import os
import ssl
import uuid
from pathlib import Path

# 오프라인/기업망 환경: 자체 서명 인증서 우회
os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
ssl._create_default_https_context = ssl._create_unverified_context  # noqa: SLF001

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CHUNKS_DIR = BASE_DIR / "dataset/chunks"
COLLECTION_NAME = "stm32g4_docs"
VECTOR_DIM = 1024
BATCH_SIZE = 32


def load_chunks(chunks_dir: Path, pattern: str = "*_chunks.jsonl") -> list[dict]:
    records = []
    for jsonl in sorted(chunks_dir.glob(pattern)):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    log.info("총 %d 청크 로드", len(records))
    return records


def ensure_collection(client: QdrantClient, collection: str = COLLECTION_NAME):
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        log.info("컬렉션 생성: %s", collection)
    else:
        log.info("컬렉션 기존 존재: %s", collection)


def chunk_to_point_id(doc_id: str, chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}::{chunk_id}"))


def embed_and_upsert(
    client: QdrantClient,
    model: SentenceTransformer,
    records: list[dict],
    collection: str = COLLECTION_NAME,
    batch_size: int = BATCH_SIZE,
):
    total = len(records)
    for i in range(0, total, batch_size):
        batch = records[i : i + batch_size]
        texts = [r["text"] for r in batch]

        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

        points = []
        for rec, vec in zip(batch, embeddings):
            point_id = chunk_to_point_id(rec["doc_id"], rec["chunk_id"])
            payload = {k: v for k, v in rec.items() if k != "text"}
            payload["text"] = rec["text"][:2000]  # payload 크기 제한
            points.append(PointStruct(id=point_id, vector=vec.tolist(), payload=payload))

        client.upsert(collection_name=collection, points=points)
        log.info("업서트 %d/%d", min(i + batch_size, total), total)


def main():
    parser = argparse.ArgumentParser(description="임베딩 → Qdrant 적재")
    parser.add_argument("--chunks-dir", default=str(DEFAULT_CHUNKS_DIR))
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--collection", default=COLLECTION_NAME,
        help="적재할 Qdrant 컬렉션 (기본 stm32g4_docs). 코드 RAG는 stm32g4_code 사용.",
    )
    parser.add_argument(
        "--glob", default="*_chunks.jsonl",
        help="chunks-dir에서 읽을 파일 패턴 (예: opensource_algo_chunks.jsonl 하나만)",
    )
    args = parser.parse_args()

    chunks_dir = Path(args.chunks_dir)
    records = load_chunks(chunks_dir, args.glob)
    if not records:
        log.warning("청크 없음. parse_pdfs.py → chunk_docs.py 먼저 실행하세요.")
        return

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("임베딩 모델 로드: %s (device=%s)", args.model, device)
    model = SentenceTransformer(args.model, device=device)

    log.info("Qdrant 연결: %s (collection=%s)", args.qdrant_url, args.collection)
    client = QdrantClient(url=args.qdrant_url)
    ensure_collection(client, args.collection)

    embed_and_upsert(client, model, records, args.collection, args.batch_size)

    # 통계
    info = client.get_collection(args.collection)
    count = getattr(info, "vectors_count", None) or getattr(info.points_count, "__int__", lambda: info.points_count)()
    log.info("완료 — 컬렉션 포인트 수: %s", count)


if __name__ == "__main__":
    main()
