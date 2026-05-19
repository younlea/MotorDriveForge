# DGX Spark 시작 가이드

MotorDriveForge를 DGX Spark에서 처음 실행할 때 이 순서대로 진행합니다.

---

## 전제 조건 확인

- Ollama 설치 및 `gemma4:31b` 로드 완료
- Docker 실행 중
- Python 3.10+

```bash
ollama list          # gemma4:31b 있는지 확인
docker ps            # Docker 동작 확인
python3 --version
```

---

## Step 0 — 코드 받기

```bash
cd ~/source-code/MotorDriveForge   # 실제 경로로 변경
git pull
```

---

## Step 1 — Python 패키지 설치

```bash
pip install -r backend/requirements.txt
pip install -r frontend/requirements.txt
pip install sentence-transformers qdrant-client tqdm lxml  # 스크립트용
```

> `sentence-transformers` 첫 실행 시 BAAI/bge-m3 모델 (~1.1GB) 자동 다운로드됩니다.

---

## Step 2 — Qdrant 벡터 DB 시작

```bash
docker run -d --name qdrant \
  -p 6333:6333 \
  -v qdrant_storage:/qdrant/storage \
  qdrant/qdrant

# 정상 확인
curl http://localhost:6333/healthz
```

---

## Step 3 — 임베딩 → Qdrant 적재

chunks는 이미 git에 있음. 벡터만 생성하면 됨.

```bash
python3 scripts/embed_and_index.py
```

> 3411개 청크 × BGE-M3 임베딩. 128GB 통합메모리에서 10~20분 소요 예상.
> 완료 후 `stm32g4_docs` 컬렉션 생성됨.

---

## Step 4 — 핀 AF DB 생성

```bash
# X-CUBE-MCSDK 미설치 시: 폴백 테이블(G474/G431)로 생성 (이미 git에 있음)
python3 scripts/parse_cubemx_xml.py

# X-CUBE-MCSDK 설치된 경우: CubeMX XML 경로 지정 → 전체 G4 계열 DB
python3 scripts/parse_cubemx_xml.py \
  --xml-dir /path/to/cubemx_db/STM32G4_series/
```

> CubeMX XML 위치: 보통 `~/.mxcube/` 또는 X-CUBE-MCSDK 설치 디렉토리 안
> `dataset/pin_af_db.json`은 fallback 버전이 이미 git에 있으므로 건너뛰어도 기본 동작 가능.

---

## Step 5 — BM25 인덱스 확인 (이미 git에 있음, 재빌드 불필요)

```bash
ls dataset/bm25_index/
# bm25_index.pkl  doc_map.jsonl  이 두 파일이 있으면 OK
```

필요 시 재빌드:

```bash
python3 scripts/build_bm25.py
```

---

## Step 6 — 서비스 실행

터미널을 2개 열어서:

**터미널 1 — FastAPI 백엔드**

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

**터미널 2 — Streamlit 프론트엔드**

```bash
streamlit run frontend/app.py --server.port 8501 --server.address 0.0.0.0
```

---

## 접속 확인

| 서비스 | URL |
|---|---|
| Streamlit UI | http://dgx-spark:8501 |
| FastAPI Swagger | http://dgx-spark:8000/docs |
| Qdrant Dashboard | http://dgx-spark:6333/dashboard |

---

## 빠른 동작 확인 (curl)

```bash
# 헬스체크
curl http://localhost:8000/v1/health

# 서비스 상태 (Ollama, Qdrant, 모델 목록)
curl http://localhost:8000/v1/status

# 핀 검증 테스트 (fast 모드 — LLM 없이 Rule Engine만)
curl -X POST http://localhost:8000/v1/review \
  -F "chip=STM32G474RET6" \
  -F "prompt=BLDC 1개 FOC, 증분형 엔코더, FDCAN 1Mbps" \
  -F "csv_file=@dataset/official_docs/README.md" \
  -F "mode=fast"
```

---

## 환경 변수 (기본값으로 동작, 변경 필요 시만)

```bash
export OLLAMA_URL="http://localhost:11434"   # 기본값
export QDRANT_URL="http://localhost:6333"    # 기본값
export QDRANT_COLLECTION="stm32g4_docs"     # 기본값
```

---

## Spark 재시작 후 (다음번 실행 시)

Step 1~4는 한 번만 하면 됩니다. 재시작 후에는:

```bash
# Qdrant 재시작 (컨테이너가 중지된 경우)
docker start qdrant

# 백엔드 + 프론트엔드 재실행
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
streamlit run frontend/app.py --server.port 8501 --server.address 0.0.0.0
```

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `Connection refused :11434` | Ollama 미실행 | `ollama serve` |
| `Connection refused :6333` | Qdrant 미실행 | `docker start qdrant` |
| RAG 결과 없음 | embed_and_index.py 미실행 | Step 3 재실행 |
| `gemma4:31b not found` | 모델 미로드 | `ollama pull gemma4:31b` |
| pin_af_db 경고 | XML 없이 폴백 사용 중 | X-CUBE-MCSDK 설치 후 Step 4 재실행 |
