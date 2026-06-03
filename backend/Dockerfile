FROM dgx-antigravity-corp:v2

WORKDIR /app
USER root

# Python 의존성 먼저 설치 (레이어 캐시 활용)
# docker-compose build context = 프로젝트 루트
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 소스 복사 (agent + golden_modules + backend)
COPY agent/          /app/agent/
COPY golden_modules/ /app/golden_modules/
COPY backend/        /app/backend/

WORKDIR /app/backend

ENV PYTHONPATH=/app
ENV OLLAMA_URL=http://host.docker.internal:11434
ENV QDRANT_URL=http://qdrant:6333
ENV IOC_OUTPUT_DIR=/tmp/ioc_outputs
ENV CODE_OUTPUT_DIR=/tmp/code_outputs

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD bash -c ':> /dev/tcp/127.0.0.1/8000' 2>/dev/null || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
