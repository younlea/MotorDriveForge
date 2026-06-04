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

## Step 0.5 — Ollama 멀티 모델 동시 상주 설정 (중요)

이 Spark의 Ollama는 **여러 서비스가 공유**합니다 (예: `team-chat-ui`/open-webui가
`gemma4-fast`를 사용). Ollama 기본값은 모델을 **하나만** 메모리에 유지하려 해서,
우리 Vision 모델(`gemma4:31b`)과 다른 서비스의 모델이 호출 때마다 서로 밀어냅니다(evict).
그러면 gemma4:31b가 매번 콜드 로드(~19GB)되어 응답이 수십 초~수 분까지 느려지고
read timeout(현재 600s)이 납니다.

`ollama ps`로 진단:

```bash
ollama ps
# 비어 있거나, gemma4:31b가 안 보이거나, UNTIL이 "Stopping..."이면 → 상주 안 됨(evict)
ollama list   # gemma4:31b, gemma4-fast 가 설치돼 있는지 (없으면 ollama pull gemma4:31b)
```

**이 환경은 systemd가 없습니다** (`Failed to connect to bus: Host is down`).
Ollama는 systemd 서비스가 아니라 **워크스페이스 컨테이너(`ai-workspace-corp`) 안 또는
호스트에서 `ollama serve` 프로세스**로 떠서 `172.18.0.1:11434`로 공유됩니다.
따라서 `systemctl`이 아니라 **그 serve 프로세스의 환경변수**를 바꿔야 합니다.

### 1) Ollama가 실제로 어디서 도는지 확인

```bash
# 워크스페이스 컨테이너 안에서 도는지
sudo docker exec ai-workspace-corp ps aux | grep -i "ollama serve" | grep -v grep
# 또는 호스트에서 11434를 누가 잡고 있는지
sudo ss -tlnp | grep 11434 || sudo lsof -i:11434
```

### 2) 멀티 상주로 재시작 (gemma4:31b 19GB + gemma4-fast 19GB ≪ 128GB)

컨테이너(`ai-workspace-corp`) 안에서 도는 경우:

```bash
ai-in                              # = sudo docker exec -it ai-workspace-corp /bin/bash
ps aux | grep ollama               # 현재 실행 방식/런처 확인
pkill ollama; sleep 2
OLLAMA_HOST=0.0.0.0 OLLAMA_MAX_LOADED_MODELS=3 OLLAMA_KEEP_ALIVE=-1 nohup ollama serve > /tmp/ollama.log 2>&1 &
```

호스트 프로세스로 도는 경우 — 위 `pkill`/`nohup ... ollama serve` 줄을 호스트에서 실행.

- `OLLAMA_HOST=0.0.0.0`: **반드시 유지.** 이게 없으면 Ollama가 127.0.0.1에만 바인딩되어
  Docker 컨테이너(backend/frontend)가 접속 못 함 → 상태창 Ollama "X". 재시작 시 빠뜨리지 말 것.
- `OLLAMA_MAX_LOADED_MODELS`: 동시에 메모리에 유지할 모델 수. 1보다 크면 됨(모델 2개라
  2면 충분, 여유로 3). **이 값이 1이면 앱의 `keep_alive:-1`도 소용없이 서로 밀어냄.**
- `OLLAMA_KEEP_ALIVE=-1`: idle이어도 모델을 안 내림.
- **영구화**: 컨테이너/호스트의 ollama 자동 기동 스크립트(entrypoint·`~/.bashrc`·rc.local 등)에
  두 export를 추가. 안 하면 컨테이너/호스트 재시작 때마다 재설정해야 함.

> 주의: ollama 재시작은 같은 데몬을 쓰는 **open-webui(team-chat-ui)도 잠깐 영향**받음(모델 재로드).
> 이 박스에는 대형 모델이 많으므로(mixtral 79GB, qwen3.5:122b 81GB 등), 다른 사용자가 큰 모델을
> 동시에 올리면 용량 압박으로 evict가 다시 날 수 있음 — 그땐 `OLLAMA_MAX_LOADED_MODELS`를
> 더 올리기보다 사용 패턴을 조율.

### 3) 검증

검증 한 번 돌린 뒤 `ollama ps`에서 `gemma4:31b`와 `gemma4-fast`가 **둘 다 상주(UNTIL=Forever)**
하면 성공. 백엔드 로그의 `[TIMING] vision=` 가 warm 기준 ~60~90s로 안정되고 300s timeout이 사라짐.

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
| Vision 매번 느림/timeout, `ollama ps`가 비었거나 다른 모델만 보임 | 공유 Ollama가 모델 1개만 상주 → 호출마다 콜드 로드(evict) | Step 0.5 — `OLLAMA_MAX_LOADED_MODELS≥2`로 `ollama serve` 재시작 (systemd 아님) |
| `start.sh restart` 후 상태창 Ollama "X" | compose가 `host.docker.internal`(host-gateway)로 호스트 연결 — Ollama가 127.0.0.1에만 바인딩됐거나 게이트웨이 변동 | `OLLAMA_HOST=0.0.0.0`로 `ollama serve` 재시작. compose는 이미 host-gateway 사용(하드코딩 IP 제거됨) |
| `Connection refused :6333` | Qdrant 미실행 | `docker start qdrant` |
| RAG 결과 없음 | embed_and_index.py 미실행 | Step 3 재실행 |
| `gemma4:31b not found` | 모델 미로드 | `ollama pull gemma4:31b` |
| pin_af_db 경고 | XML 없이 폴백 사용 중 | X-CUBE-MCSDK 설치 후 Step 4 재실행 |
