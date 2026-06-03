#!/bin/bash

# RAG 데이터를 Qdrant에 임베딩하기 위한 일회용 도커 실행 스크립트
# 호스트(Spark) 환경을 오염시키지 않기 위해 python:3.11-slim 컨테이너를 임시로 띄워 실행합니다.

echo "🚀 RAG 임베딩 작업을 위한 일회용 도커 컨테이너를 시작합니다..."

docker run --rm -it \
  -v $(pwd):/app \
  -w /app \
  --network motordriveforge_default \
  -u root \
  dgx-antigravity-corp:v2 \
  bash -c "echo '📦 필요한 파이썬 패키지를 설치 중입니다...' && \
           pip install sentence-transformers qdrant-client tqdm lxml && \
           echo '🧠 임베딩 스크립트(embed_and_index.py)를 실행합니다...' && \
           python3 scripts/embed_and_index.py --qdrant-url http://qdrant:6333"

echo "✅ 모든 작업이 완료되었으며 일회용 도커 컨테이너가 삭제되었습니다."
