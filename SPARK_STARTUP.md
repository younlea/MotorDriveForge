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

## Step 0.5 — Ollama 모델 설정 (gemma4-fast 공유 방식)

이 Spark의 Ollama는 **여러 서비스가 공유**합니다 (`team-chat-ui`/open-webui가 `gemma4-fast` 사용).
단일 iGPU(GB10)에서 Ollama 0.23.1은 모델을 사실상 1개만 상주시켜서, 서로 다른 모델을 쓰면
호출 때마다 evict→콜드로드가 반복됩니다.

**핵심 사실**: `gemma4-fast`는 `gemma4:31b`와 **완전히 같은 모델**입니다 (`/workspace/Modelfile`
= `FROM gemma4:31b` + `num_ctx`만 지정). 품질 동일. 그래서 **MotorDriveForge도 webui와 똑같이
`gemma4-fast`를 쓰면 같은 runner 하나를 공유** → evict/콜드로드/충돌이 원천 차단됩니다.
(앱 코드는 이미 `gemma4-fast`를 쓰도록 설정됨, 요청에 num_ctx를 싣지 않아 Modelfile 기본값을 공유)

### 1) gemma4-fast 컨텍스트를 32K로 (Vision 이미지 ~5천 토큰 + RAG 여유 확보)

기본 8K는 Vision(이미지만 ~5,376 토큰) + RAG에 빠듯합니다. `/workspace/Modelfile`을 수정:

```bash
# /workspace/Modelfile 내용을 아래로
#   FROM gemma4:31b
#   PARAMETER num_ctx 32768
ollama create gemma4-fast:latest -f /workspace/Modelfile   # 재등록 (가중치는 캐시 재사용, 빠름)
ollama stop gemma4-fast:latest 2>/dev/null                  # 기존 8K 인스턴스 내려서 32K로 재로드되게
```

webui 사용자도 8K→32K로 컨텍스트가 늘 뿐(메모리 ~25→27GB) 손해 없습니다.

### 2) Ollama serve 환경변수 (`/workspace/ollama_launch.sh`)

이 호스트는 systemd가 없고, Ollama는 `/workspace/ollama_launch.sh`로 수동 기동됩니다.
스크립트에 아래 export가 있어야 합니다 (`/workspace`는 볼륨이라 영구 유지):

```bash
#!/bin/bash
export OLLAMA_HOST=0.0.0.0          # 없으면 127.0.0.1 바인딩 → 컨테이너가 접속 못 함(상태창 "X")
export OLLAMA_KEEP_ALIVE=-1         # idle에도 모델 유지 (콜드로드 방지)
export OLLAMA_MAX_LOADED_MODELS=3   # (공유 방식에선 1개만 써도 무방하나, 둬도 무해)
nohup ollama serve > /workspace/ollama.log 2>&1 &
```

적용: `pkill ollama; sleep 3; /workspace/ollama_launch.sh`
확인: `cat /proc/$(pgrep -x ollama | head -1)/environ | tr '\0' '\n' | grep -i ollama`

### 3) 검증

```bash
# MotorDriveForge 리뷰 1회 실행 후
ollama ps
# → gemma4-fast:latest 하나가 CONTEXT 32768 로 떠 있고, webui도 같은 걸 공유.
#   백엔드 로그 [TIMING] vision= 가 콜드로드 없이 안정적(warm ~60~90s).
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
| Vision 매번 느림/timeout, 모델이 자꾸 콜드 로드 | 앱과 webui가 서로 다른 모델/컨텍스트를 써서 단일 iGPU에서 evict 반복 | Step 0.5 — 앱이 webui와 같은 `gemma4-fast`(32K) 공유하도록 (이미 코드 반영, Modelfile 32K + 재시작만) |
| `start.sh restart` 후 상태창 Ollama "X" | compose가 `host.docker.internal`(host-gateway)로 호스트 연결 — Ollama가 127.0.0.1에만 바인딩됐거나 게이트웨이 변동 | `OLLAMA_HOST=0.0.0.0`로 `ollama serve` 재시작. compose는 이미 host-gateway 사용(하드코딩 IP 제거됨) |
| `Connection refused :6333` | Qdrant 미실행 | `docker start qdrant` |
| RAG 결과 없음 | embed_and_index.py 미실행 | Step 3 재실행 |
| `gemma4-fast not found` | 모델 미등록 | `ollama create gemma4-fast:latest -f /workspace/Modelfile` (베이스 `gemma4:31b` 필요 시 `ollama pull gemma4:31b`) |
| pin_af_db 경고 | XML 없이 폴백 사용 중 | X-CUBE-MCSDK 설치 후 Step 4 재실행 |
| 컨텍스트 부족(긴 RAG/대화 truncation 의심) | gemma4-fast num_ctx가 작음 | 아래 "gemma4-fast 컨텍스트(num_ctx) 변경" 참조 |

---

## gemma4-fast 컨텍스트(num_ctx) 변경 — 자주 찾는 절차

MotorDriveForge와 open-webui는 **같은 `gemma4-fast` 모델 인스턴스를 공유**합니다.
앱은 요청에 num_ctx를 싣지 않으므로, **컨텍스트 크기는 오직 `/workspace/Modelfile`에서만** 정해집니다.
즉 컨텍스트를 늘리거나 줄이려면 아래 한 곳만 바꾸면 되고, **앱 재배포는 불필요**합니다.

```bash
# 1) Modelfile 수정 — num_ctx 값만 원하는 크기로
cat > /workspace/Modelfile <<'EOF'
FROM gemma4:31b
PARAMETER num_ctx 65536
EOF
#    (32768=32K, 65536=64K, 131072=128K ... 원하는 값)

# 2) 모델 재등록 (가중치는 캐시 재사용 → 수 초)
ollama create gemma4-fast:latest -f /workspace/Modelfile

# 3) 기존에 떠 있던 인스턴스를 내려서 새 컨텍스트로 다시 로드되게
ollama stop gemma4-fast:latest

# 4) 다음 요청(리뷰 1회 또는 webui 사용) 후 확인
ollama ps      # CONTEXT 열이 새 값으로 바뀌었는지
```

### num_ctx ↔ 메모리(대략, gemma4-fast = gemma4:31b Q4_K_M 기준)

| num_ctx | 메모리 | 비고 |
|---|---|---|
| 8K (8192) | ~25GB | Vision 이미지(~5천 토큰)+RAG엔 빠듯 |
| 32K (32768) | ~27GB | 기본 권장 — 실사용(~1만 토큰)의 3배 |
| 64K (65536) | ~30GB | 여유 충분, 긴 대화에도 안전 |
| 256K (기본 자동) | ~47GB | 과도 — 단일 iGPU에서 다른 모델 evict 유발 |

> - 컨텍스트는 **클수록 KV 캐시로 메모리만 더 쓸 뿐 품질엔 영향 없음**. 우리가 실제로 넣는 토큰만큼만 쓰면 됨.
> - 이 변경은 **webui 사용자에게도 동일 적용**됩니다(같은 인스턴스 공유) — 컨텍스트가 늘어 이득이며 손해 없음.
> - 변경 후 `OLLAMA_HOST=0.0.0.0`는 그대로 유지되어야 함(컨테이너 접속용, `/workspace/ollama_launch.sh`).
