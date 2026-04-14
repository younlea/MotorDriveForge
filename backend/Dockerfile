FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 먼저 설치 (레이어 캐시 활용)
# docker-compose build context = 프로젝트 루트
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 소스 복사 (agent 모듈 + backend)
COPY agent/ /app/agent/
COPY backend/ /app/backend/

WORKDIR /app/backend

ENV PYTHONPATH=/app
ENV OLLAMA_URL=http://host.docker.internal:11434
ENV QDRANT_URL=http://qdrant:6333
ENV IOC_OUTPUT_DIR=/tmp/ioc_outputs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
