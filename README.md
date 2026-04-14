# MotorDriveForge

STM32G4 전용 사내 Agent 시스템 — 회로도 입력부터 완성 펌웨어까지 자동화.

> 운영 환경: NVIDIA DGX Spark 128GB · 완전 로컬 (외부망 차단)
> 타겟 칩: STM32G4 계열 (G431 / G471 / G474 / G491 / G4A1)

---

## 3-Step 파이프라인

```
[입력] 핀맵 CSV  +  자연어 프롬프트
         ↓
[STEP 1] 핀 검증 Agent          (Qwen2.5-72B · Ollama)
         규칙엔진 + RAG + LLM → errors[] / warnings[] / suggestions[]
         errors > 0 → HTTP 403 차단
         ↓ PASS only
[STEP 2] CubeMX 자동화          (LLM 없음 · Python 스크립트)
         핀 JSON → .ioc 생성 → STM32CubeMX -q 실행
         ↓
[STEP 3] 알고리즘 통합 Agent    (Qwen2.5-Coder-32B · Ollama)
         Golden Module RAG → USER CODE BEGIN/END 삽입
         ↓
[출력] 완성 펌웨어 .zip
```

---

## 디렉토리 구조

```
MotorDriveForge/
├── agent/                          # Step 1 리뷰 에이전트
│   └── step1_review_agent.py       # 규칙엔진 7항목 + LLM + RAG
├── backend/                        # FastAPI 백엔드
│   ├── main.py                     # POST /v1/review, GET /v1/status, ...
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                       # Streamlit MVP UI
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── golden_modules/                 # STM32G4 HAL 레퍼런스 구현체 (C/H)
│   ├── dc_motor_pid.c/.h           # H-bridge PWM + PID (Anti-windup)
│   ├── multi_axis_sync.c/.h        # TIM1/TIM8/TIM20 PWM 동기화 (레지스터 직접)
│   ├── bldc_6step_hall.c/.h        # Hall 인터럽트 6-Step + BRK 보호
│   └── fdcan_motor_cmd.c/.h        # FDCAN 커맨드 파싱 (비상정지 즉시처리)
├── scripts/                        # 데이터 수집 & RAG 파이프라인
│   ├── scrape_st_forum.py          # ST 커뮤니티 포럼 Q&A 수집 (200~300건)
│   ├── parse_pdfs.py               # PDF → 텍스트 (pdfplumber)
│   ├── chunk_docs.py               # 섹션/블록/슬라이딩윈도우 청킹
│   ├── embed_and_index.py          # BGE-M3 → Qdrant 적재
│   ├── build_bm25.py               # BM25 역인덱스 구축
│   └── parse_cubemx_xml.py         # CubeMX XML → 핀 AF DB JSON
├── dataset/
│   ├── official_docs/              # ★ ST PDF 수동 저장 위치 (아래 참조)
│   │   ├── reference_manual/       # RM0440 등
│   │   ├── application_notes/      # AN5306, AN5789, AN4277 등
│   │   ├── datasheets/             # STM32G474, G431, STSPIN32G4
│   │   ├── eval_boards/            # EVSPIN32G4 회로도/매뉴얼
│   │   ├── sdk_docs/               # UM3027 MCSDK v6 등
│   │   └── cubemx_db/              # STM32CubeMX XML (X-CUBE-MCSDK 설치 후)
│   ├── forum_qa/                   # 포럼 수집 결과 (st_forum_qa.jsonl)
│   ├── chunks/                     # 청킹 결과
│   ├── bm25_index/                 # BM25 인덱스
│   ├── parsed_text/                # PDF 파싱 결과
│   ├── multi_motor/                # 멀티모터 설계 가이드
│   └── opensource/
│       ├── STM32CubeG4/            # 공식 HAL 예제
│       └── flatmcu/                # KiCad 회로도 레퍼런스
├── docker-compose.yml              # Qdrant + Backend + Frontend
├── stm32_agent_plan.md             # 메인 설계 계획 (6차)
├── stm32_agent_appendex.md         # Appendix A/B/C
├── generate_ppt.py                 # PPT 36슬라이드 자동 생성
└── todo.md                         # 작업 현황
```

---

## 빠른 시작 (DGX Spark)

### 전제 조건

```bash
# Ollama 설치 및 모델 로드 (128GB VRAM — 두 모델 동시 상주 가능)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:72b          # Step 1 (~40GB, Q4_K_M)
ollama pull qwen2.5-coder:32b    # Step 3 (~32GB, Q8)

# Docker 설치 확인
docker --version
```

### 1. 저장소 클론

```bash
git clone https://github.com/younlea/MotorDriveForge.git
cd MotorDriveForge
```

### 2. ST 공식 문서 수동 저장 ⚠️

ST 서버는 직접 curl 다운로드를 차단합니다. **st.com 로그인 후 브라우저에서 직접 저장**하세요.

| 우선도 | 파일명 | 저장 위치 |
|--------|--------|-----------|
| ★★★ | RM0440_STM32G4_Reference_Manual.pdf | `dataset/official_docs/reference_manual/` |
| ★★★ | STM32G474_datasheet.pdf | `dataset/official_docs/datasheets/` |
| ★★★ | AN5306_OPAMP_current_sensing.pdf | `dataset/official_docs/application_notes/` |
| ★★★ | AN5789_bootstrap_circuit_design.pdf | `dataset/official_docs/application_notes/` |
| ★★★ | AN4277_PWM_shutdown_protection.pdf | `dataset/official_docs/application_notes/` |
| ★★★ | EVSPIN32G4_DUAL_schematics.pdf | `dataset/official_docs/eval_boards/` |

전체 목록 및 URL: `dataset/download_st_docs.sh` 참조.

### 3. RAG 파이프라인 구축

```bash
pip install pdfplumber sentence-transformers qdrant-client rank_bm25 tqdm

# PDF 파싱
python scripts/parse_pdfs.py

# 청킹
python scripts/chunk_docs.py

# Qdrant 실행 (Docker)
docker run -d -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant

# 임베딩 → Qdrant 적재
python scripts/embed_and_index.py

# BM25 인덱스
python scripts/build_bm25.py

# 핀 AF DB 생성 (CubeMX XML 없으면 폴백 테이블 사용)
python scripts/parse_cubemx_xml.py
```

### 4. 서비스 실행

```bash
# 전체 스택 (Qdrant + Backend + Frontend)
docker-compose up -d

# 또는 개발 모드
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

pip install -r frontend/requirements.txt
streamlit run frontend/app.py
```

**접속:**
- Streamlit UI: http://dgx-spark:8501
- FastAPI Swagger: http://dgx-spark:8000/docs

### 5. ST 포럼 Q&A 수집 (선택, 인터넷 연결 필요)

```bash
pip install requests beautifulsoup4 tqdm
python scripts/scrape_st_forum.py --max-items 300
# 출력: dataset/forum_qa/st_forum_qa.jsonl
```

---

## API 사용 예시

```bash
# 핀 검증 요청
curl -X POST http://localhost:8000/v1/review \
  -F "chip=STM32G474RET6" \
  -F "prompt=BLDC 1개 FOC, 증분형 엔코더, FDCAN 1Mbps, 시스템 170MHz" \
  -F "csv_file=@pinmap.csv"

# 서비스 상태 확인
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

## Step 1 검증 항목 (규칙 엔진)

| 항목 | 설명 | 심각도 |
|------|------|--------|
| TIM1/TIM8 핀 충돌 | PB0/PB1 등 공유 핀 | ERROR |
| OPAMP 수 초과 | FOC×3채널, G474 최대 6개 → 2모터 한도 | ERROR |
| BRK 핀 독립성 | 모터별 독립 보호 불가 시 | WARNING |
| ADC 트리거 중복 | 동일 트리거 소스 다중 할당 | ERROR |
| DMA 채널 초과 | G4 최대 16채널 | ERROR |
| CPU 부하 추정 | 20kHz FOC × 모터 수 → 권장 최대 2개 | WARNING |
| 핀 AF 검증 | pin_af_db.json 기반 유효성 확인 | ERROR |

---

## Golden Modules 사용법

`golden_modules/` 파일을 STM32CubeIDE 프로젝트에 복사:

```c
// multi_axis_sync — 2모터 FOC 동기화 예시
TIM_HandleTypeDef *slaves[2] = { &htim8, &htim20 };
MultiAxisSync_TypeDef sync;
MultiAxisSync_Init(&sync, &htim1, slaves, 2);
MultiAxisSync_Start(&sync);

// BLDC Hall 6-Step
BLDC_TypeDef motor1 = {
    .htim_pwm   = &htim1,
    .htim_speed = &htim2,
    .hall_gpio  = GPIOA,
    .hall_pin_a = GPIO_PIN_0,
    .hall_pin_b = GPIO_PIN_1,
    .hall_pin_c = GPIO_PIN_2,
    .pole_pairs = 4,
};
BLDC_Init(&motor1);
BLDC_Start(&motor1, +1, 0.3f);   // 정방향 30% 듀티

// EXTI 핸들러에서:
void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin) {
    BLDC_HallISR(&motor1);
}
```

---

## 인프라 스택

| 구분 | 선택 | 비고 |
|------|------|------|
| 모델 서빙 | Ollama + GGUF | 두 모델 동시 로드 (72GB 사용) |
| 벡터 DB | Qdrant (Docker) | hybrid search, port 6333 |
| 임베딩 | BAAI/bge-m3 + BM25 | dense + sparse |
| 백엔드 | FastAPI + uvicorn | port 8000 |
| 프론트 MVP | Streamlit | port 8501 |
| 배포 | Docker Compose | nginx 추가 예정 |

---

## 남은 작업 (todo.md 참조)

- [ ] ST 공식 PDF 수동 다운로드 → RAG 파이프라인 실제 가동
- [ ] X-CUBE-MCSDK 설치 → `dataset/official_docs/cubemx_db/` XML 수집
- [ ] STM32G474/G431 Errata Sheet 수동 다운로드
- [ ] Step 2 CubeMX 자동화 (Python .ioc 생성 스크립트) 구현
- [ ] Step 3 알고리즘 통합 에이전트 구현
- [ ] React 18 프로덕션 UI (Streamlit MVP 이후)
- [ ] QLoRA Fine-tuning (Step 1 오류 사례 1,000건 수집 후)
