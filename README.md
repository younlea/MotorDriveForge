# MotorDriveForge

STM32G4 전용 사내 Agent 시스템 — 회로도 입력부터 완성 펌웨어까지 자동화.

> **운영 환경**: NVIDIA DGX Spark 128GB · 완전 로컬 (외부망 차단)
> **타겟 칩**: STM32G4 계열 (G431 / G471 / G474 / G491 / G4A1)

**시스템 다이어그램, 모델 카탈로그, 데이터 소스:** [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 3-Step 파이프라인

```
[입력] 회로도 이미지 (Ctrl+V 붙여넣기 or 업로드) + 핀맵 CSV + 자연어 프롬프트
         ↓
[Vision] Gemma 4 31B Multimodal — 이미지 → 핀맵 자동 추출 + 초기 분석
         (이미지 없으면 스킵, CSV 직접 입력 가능)
         ↓
[STEP 1] HW 설계 검증 Agent      (Gemma 4 31B · Ollama)
         A Rule Engine (결정론) → B Hybrid RAG → C LLM Persona Debate
         mode=fast : Rule Engine만, errors > 0 → HTTP 403 즉시 반환 (CI용)
         mode=full : A→B→C 전체, errors 있어도 자연어 설명 반환 (기본)
         ✅ 검증 결과에 대한 멀티턴 채팅 지원 (RAG 근거 자료 인용 포함)
         ↓ PASS only (errors == 0)
[STEP 2] CubeMX 자동화            (LLM 없음, 결정론)
         A: .ioc 파일 생성 + 다운로드 (CubeMX 입력 파일)
         B: CubeMX CLI headless → HAL 코드 자동 생성 → ZIP 다운로드
            (CubeMX 설치 시 활성, /opt/STM32CubeMX/ 또는 CUBEMX_PATH 환경변수)
         ↓
[STEP 3] 알고리즘 통합 Agent      (Gemma 4 31B · Ollama)
         Golden Module 선택(결정론) → 바인딩 glue 코드 생성(LLM)
         → USER CODE BEGIN/END 마커 주입 → ZIP 다운로드
         ↓
[출력] 완성 펌웨어 .zip
```

---

## 빠른 시작 (DGX Spark)

### 전제 조건

| 항목 | 필요 | 비고 |
|------|------|------|
| Docker + NVIDIA Container Toolkit | ✅ | DGX 기본 설치 |
| Ollama 모델 파일 | ✅ | 호스트 어딘가에 저장 (기본: `~/.ollama`) |
| git | ✅ | 코드 클론용 |
| CubeMX CLI | 선택 | Step 2B 코드 생성 시 필요 |

### 1. 저장소 클론

```bash
git clone https://github.com/younlea/MotorDriveForge.git
cd MotorDriveForge
```

### 2. 전체 서비스 시작 (한 줄)

```bash
./start.sh
```

내부에서 자동으로:
1. Ollama 컨테이너 시작 (호스트 모델 디렉토리 마운트, GPU 사용)
2. Qdrant 벡터 DB 시작
3. Backend (FastAPI) 빌드 및 시작
4. Frontend (Streamlit) 빌드 및 시작
5. 모든 서비스 healthcheck 통과 확인 후 URL 출력

모델 경로가 `~/.ollama`가 아닌 경우:
```bash
OLLAMA_MODELS_DIR=/data/models/ollama ./start.sh
```

### 3. RAG 임베딩 (최초 1회)

서비스가 뜬 후 실행:
```bash
./start.sh ingest
```

자동으로 idempotent 실행 (이미 적재된 컬렉션은 건너뜀):
- `stm32g4_docs` — ST 공식 문서/포럼 청크 (Step 1 검증 RAG)
- `stm32g4_code` — 오픈소스 알고리즘 청크 (Step 3 코드 RAG)

### 4. 접속

| 서비스 | URL |
|------|------|
| Streamlit UI | http://dgx-spark:8501 |
| FastAPI Swagger | http://dgx-spark:8000/docs |
| Qdrant Dashboard | http://dgx-spark:6333/dashboard |
| Ollama API | http://dgx-spark:11434 |

---

## start.sh 명령어

```bash
./start.sh              # Docker 모드로 전체 시작 (기본)
./start.sh stop         # 모든 컨테이너 중지
./start.sh restart      # 재빌드 후 재시작
./start.sh status       # 서비스 상태 + 로드된 모델 확인
./start.sh logs         # 전체 컨테이너 로그 실시간 출력
./start.sh ingest       # RAG 임베딩 (idempotent, 최초 1회)
./start.sh dev          # 개발 모드 (Qdrant=Docker, Backend/Frontend=Python)
```

---

## 프론트엔드 기능 (Streamlit)

### Step 1 — 핀 검증
- **입력**: 회로도 이미지 (Ctrl+V 붙여넣기 / 파일 업로드) + 핀맵 CSV + 자연어 프롬프트
- Vision 분석 → Rule Engine → RAG → LLM Debate 순차 실행
- 검증 결과(오류/경고/권장) 표시
- **멀티턴 채팅**: 검증 결과 기반 추가 질문 → RAG 근거 문서 인용
- 통과 시 **"Step 2로 이동"** 버튼

### Step 2 — HAL 코드 생성
- **직접 진입** 지원: Step 1 없이 CSV/이미지 업로드로 바로 시작
- **Step A**: `.ioc` 파일 생성 + 다운로드 + CubeMX GUI 사용 안내
- **Step B**: CubeMX CLI → HAL 코드 ZIP 자동 생성 (CubeMX 설치 시 활성)

### Step 3 — 알고리즘 통합
- **직접 진입** 지원: JSON/CSV 핀맵 업로드 or 수동 설정
- **자연어 프롬프트** + 6개 항목 체크리스트 (모터타입/제어방식/센서/전류/보호/통신)
- Golden Module 자동 선택 (결정론) → 바인딩 glue 코드 LLM 생성
- **진행바** (백그라운드 job 폴링)
- Step 2 HAL ZIP 업로드 → USER CODE 마커 자동 주입 → 통합 ZIP 다운로드

---

## Golden Modules (5종)

`golden_modules/` — 검증된 STM32G4 HAL 레퍼런스 구현체

| 모듈 | 용도 | 자동 선택 조건 |
|------|------|------|
| `foc_pmsm.{c,h}` | PMSM/BLDC FOC (Clarke/Park/SVPWM + PI) | control_type=FOC/PMSM |
| `dc_motor_pid.{c,h}` | DC 브러시드 PID + H-bridge PWM | DC/미지정 |
| `bldc_6step_hall.{c,h}` | BLDC 6-step 홀센서 구동 + BRK 보호 | BLDC + hall encoder |
| `fdcan_motor_cmd.{c,h}` | FDCAN 커맨드 파싱 (비상정지 즉시처리) | comms에 fdcan 포함 |
| `multi_axis_sync.{c,h}` | TIM1/TIM8/TIM20 다축 동기화 | motor_count > 1 |

---

## API 엔드포인트

```bash
# 핀 검증 (전체)
curl -X POST http://localhost:8000/v1/review \
  -F "chip=STM32G474RET6" \
  -F "prompt=BLDC 1개 FOC, 증분형 엔코더, FDCAN 1Mbps" \
  -F "csv_file=@pinmap.csv" \
  -F "mode=full"

# 핀 검증 (빠른 차단, CI용)
curl -X POST http://localhost:8000/v1/review \
  -F "csv_file=@pinmap.csv" \
  -F "mode=fast"

# 회로도 이미지 → 핀맵 자동 추출 + 검증
curl -X POST http://localhost:8000/v1/review \
  -F "schematic_image=@schematic.png" \
  -F "prompt=BLDC FOC 설계 검토"

# 검증 결과 기반 멀티턴 채팅
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"chip":"STM32G474RET6","question":"BRK 핀 배치 근거 알려줘","history":[],"report_context":{}}'

# .ioc 파일 생성 (Step 2A)
curl -X POST http://localhost:8000/v1/generate-ioc \
  -H "Content-Type: application/json" \
  -d '{"validated_pins": {...}}'

# HAL 코드 생성 ZIP (Step 2B, CubeMX 필요)
curl -X POST http://localhost:8000/v1/generate-code \
  -H "Content-Type: application/json" \
  -d '{"validated_pins": {...}}'

# CubeMX 설치 여부 확인
curl http://localhost:8000/v1/cubemx-status

# Step 3 Golden Module 적응 시작 (job 반환)
curl -X POST http://localhost:8000/v1/generate-step3 \
  -H "Content-Type: application/json" \
  -d '{"validated_pins": {...}, "prompt": "FOC + OPAMP 3상 전류 센싱"}'

# Step 3 진행상태 폴링
curl http://localhost:8000/v1/step3-status/{job_id}

# 서비스 상태
curl http://localhost:8000/v1/status
```

**pinmap.csv 형식:**
```csv
chip,pin,function,label
STM32G474RET6,PA8,TIM1_CH1,U_PWM_H
STM32G474RET6,PA9,TIM1_CH2,V_PWM_H
STM32G474RET6,PA10,TIM1_CH3,W_PWM_H
STM32G474RET6,PB13,TIM1_CH1N,U_PWM_L
STM32G474RET6,PB14,TIM1_CH2N,V_PWM_L
STM32G474RET6,PB15,TIM1_CH3N,W_PWM_L
STM32G474RET6,PB8,FDCAN1_RX,CAN_RX
STM32G474RET6,PB9,FDCAN1_TX,CAN_TX
```

---

## Step 1 검증 항목

### Rule Engine (결정론, mode=fast/full 공통)

| 항목 | 심각도 |
|------|--------|
| 핀 AF 유효성 (CubeMX DB 기반) | ERROR |
| 핀 충돌 (동일 핀 중복 배정) | ERROR |
| 상보 PWM 쌍 (CHx/CHxN 동일 타이머) | ERROR |
| BDTR 미지원 타이머에 데드타임 요청 | ERROR |
| OPAMP 수 초과 (G431=3개, G474=6개) | ERROR |
| 엔코더 타이머 Encoder Mode 지원 여부 | ERROR |
| DMA 채널 초과 (최대 16채널) | ERROR |
| FDCAN 외부 크리스탈 필요 여부 | WARNING |
| JTAG 핀 재사용 | WARNING |
| CPU 부하 추정 (20kHz FOC × 모터 수) | WARNING |
| SPI EEPROM 핀 누락 | WARNING |

### LLM Debate (mode=full 전용)

5 페르소나(MCU/Motor/Power/Safety/Moderator)가 Rule Engine 결과 + RAG 청크를 컨텍스트로 자연어 설명·권장사항 생성.

---

## 디렉토리 구조

```
MotorDriveForge/
├── agent/
│   ├── step1_review_agent.py       # Step 1: Vision + Rule Engine + RAG + LLM
│   ├── step3_codegen_agent.py      # Step 3: 역할 매퍼 + Golden Module 선택 + glue 생성
│   ├── pin_options/                # STM32 전 계열 핀 AF JSON (CubeMX DB 파생)
│   └── pin_io_structure.json       # 핀 FT/TT (5V 내성) — 데이터시트 파생
├── backend/
│   ├── main.py                     # FastAPI: /v1/review, /v1/chat, /v1/generate-*
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── app.py                      # Streamlit: Step 1~3 UI + 멀티턴 채팅
│   ├── paste_component/            # Ctrl+V 이미지 붙여넣기 커스텀 컴포넌트
│   ├── .streamlit/config.toml
│   ├── requirements.txt
│   └── Dockerfile
├── golden_modules/                 # 검증된 STM32G4 HAL C/H (5종: foc_pmsm, dc_motor_pid, bldc_6step_hall, fdcan_motor_cmd, multi_axis_sync)
├── scripts/                        # 데이터 수집 & RAG 파이프라인
│   ├── embed_and_index.py          # BGE-M3 → Qdrant 적재
│   ├── parse_cubemx_db.py          # CubeMX DB → 핀 AF JSON
│   ├── parse_cubemx_xml.py         # CubeMX XML → 핀 AF DB (폴백 포함)
│   ├── parse_opensource_algorithms.py  # OSS 알고리즘 → Step3 RAG
│   └── ...
├── dataset/
│   ├── official_docs/              # ST 공식 PDF (14건)
│   ├── chunks/                     # 청킹 결과 JSONL (git에 포함)
│   ├── bm25_index/                 # BM25 인덱스 (git에 포함)
│   ├── pin_af_db.json              # 핀 AF DB 폴백 (git에 포함)
│   └── parsed_text/                # PDF 파싱 중간 결과물
├── tests/
│   └── test_review_modes.py        # 단위 테스트 (pytest)
├── doc/                            # 발표 자료 (PPT/HTML/MP4/PNG)
├── docker-compose.yml              # Ollama + Qdrant + Backend + Frontend
├── start.sh                        # ⭐ 통합 시작 스크립트
├── run_embed_docker.sh             # RAG 임베딩 (레거시, start.sh ingest 권장)
├── SPARK_STARTUP.md                # DGX Spark 기동 절차 가이드
├── ARCHITECTURE.md
├── CLAUDE.md
└── todo.md
```

---

## 인프라 스택

| 구분 | 선택 | 비고 |
|------|------|------|
| 모델 서빙 | Ollama (Docker) | 호스트 모델 디렉토리 마운트, GPU 직접 접근 |
| Step 1 LLM | Gemma 4 31B Dense | Q4_K_M, 논리 추론·자연어 파싱 |
| Step 1 Vision | Gemma 4 31B Multimodal | 회로도 → 핀맵 추출 |
| Step 3 LLM | Gemma 4 31B (공유) | glue 코드 생성 |
| 벡터 DB | Qdrant (Docker) | hybrid search, port 6333 |
| 임베딩 | BAAI/bge-m3 + BM25 | dense + sparse, 오프라인 HF 캐시 |
| 백엔드 | FastAPI + uvicorn | port 8000 |
| 프론트 | Streamlit | port 8501 |
| 배포 | Docker Compose | `./start.sh` 한 줄 기동 |

---

## 개발 가이드

작업 전 [`CLAUDE.md`](./CLAUDE.md)와 [`ARCHITECTURE.md`](./ARCHITECTURE.md)를 먼저 읽어주세요.

- 결정론적 검증은 Rule Engine이 단독 처리 (LLM 대체 금지)
- Step 2는 LLM 호출 금지 (스크립트만)
- Step 3 LLM은 처음부터 코드를 새로 쓰지 않음 — Golden Module 적응(glue)만
- 상세 작업 명세: [`tasks/`](./tasks/) 디렉토리
