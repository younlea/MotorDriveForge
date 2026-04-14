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
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path("/Users/younlea/source_code/MotorDriveForge")
DEFAULT_CHUNKS_DIR = BASE_DIR / "dataset/chunks"
COLLECTION_NAME = "stm32g4_docs"
VECTOR_DIM = 1024
BATCH_SIZE = 32


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


def ensure_collection(client: QdrantClient):
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        log.info("컬렉션 생성: %s", COLLECTION_NAME)
    else:
        log.info("컬렉션 기존 존재: %s", COLLECTION_NAME)


def chunk_to_point_id(doc_id: str, chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}::{chunk_id}"))


def embed_and_upsert(
    client: QdrantClient,
    model: SentenceTransformer,
    records: list[dict],
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

        client.upsert(collection_name=COLLECTION_NAME, points=points)
        log.info("업서트 %d/%d", min(i + batch_size, total), total)


def main():
    parser = argparse.ArgumentParser(description="임베딩 → Qdrant 적재")
    parser.add_argument("--chunks-dir", default=str(DEFAULT_CHUNKS_DIR))
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    chunks_dir = Path(args.chunks_dir)
    records = load_chunks(chunks_dir)
    if not records:
        log.warning("청크 없음. parse_pdfs.py → chunk_docs.py 먼저 실행하세요.")
        return

    log.info("임베딩 모델 로드: %s", args.model)
    model = SentenceTransformer(args.model)

    log.info("Qdrant 연결: %s", args.qdrant_url)
    client = QdrantClient(url=args.qdrant_url)
    ensure_collection(client)

    embed_and_upsert(client, model, records, args.batch_size)

    # 통계
    info = client.get_collection(COLLECTION_NAME)
    log.info("완료 — 컬렉션 통계: vectors=%s", info.vectors_count)


if __name__ == "__main__":
    main()
