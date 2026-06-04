#!/bin/bash

# RAG 데이터를 Qdrant에 임베딩하기 위한 일회용 도커 실행 스크립트
# 호스트(Spark) 환경을 오염시키지 않기 위해 dgx-antigravity-corp:v2 컨테이너를 임시로 띄워 실행합니다.

set -e

echo "RAG 임베딩 작업을 위한 일회용 도커 컨테이너를 시작합니다..."

# HuggingFace 캐시 경로 (호스트에 이미 다운받은 모델 재활용)
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"

docker run --rm -it \
  -v "$(pwd)":/app \
  -v "${HF_CACHE}":/root/.cache/huggingface \
  -w /app \
  --network motordriveforge_default \
  -u root \
  -e CURL_CA_BUNDLE="" \
  -e REQUESTS_CA_BUNDLE="" \
  -e PYTHONHTTPSVERIFY=0 \
  -e HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
  -e no_proxy="localhost,127.0.0.1,qdrant,172.16.0.0/12" \
  -e NO_PROXY="localhost,127.0.0.1,qdrant,172.16.0.0/12" \
  dgx-antigravity-corp:v2 \
  bash -c "
    echo '--- 패키지 설치 ---' &&
    pip install -q sentence-transformers qdrant-client tqdm lxml &&
    echo '--- 임베딩 시작 ---' &&
    python3 scripts/embed_and_index.py --qdrant-url http://qdrant:6333
  "

echo "완료: 일회용 컨테이너가 삭제되었습니다."
