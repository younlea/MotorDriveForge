# MotorDriveForge

STM32G4 전용 사내 Agent 시스템 — 회로도 입력부터 완성 펌웨어까지 자동화.

> 운영 환경: NVIDIA DGX Spark 128GB · 완전 로컬 (외부망 차단)
> 타겟 칩: STM32G4 계열 (G431 / G471 / G474 / G491 / G4A1)

---

## 3-Step 파이프라인

```
[입력] 핀맵 CSV  +  자연어 프롬프트
         ↓
[STEP 1] HW 설계 검증 Agent      (Gemma 4 31B Dense · Ollama)
         3계층 검증: 인프라 → 모터 논리 → 페리페럴 제약
         규칙엔진 + RAG + LLM → errors[] / warnings[]
         errors > 0 → HTTP 403 차단
         ↓ PASS only
[STEP 2] CubeMX 자동화            (4단계 워크플로우)
         .ioc 템플릿 수정 → CubeMX CLI → 스니펫 주입 → ZIP
         ↓
[STEP 3] 알고리즘 통합 Agent      (Gemma 4 26B MoE · Ollama)
         Golden Module RAG → USER CODE BEGIN/END 삽입
         ↓
[출력] 완성 펌웨어 .zip
```

---

## 디렉토리 구조

```
MotorDriveForge/
├── work/                               # Step 1/2 상세 기획 및 워크플로우
│   ├── step1_agent_plan.md             # HW Expert Agent 상세 계획
│   ├── step2_code_gen_plan.md          # C 코드 자동생성 파이프라인 계획
│   ├── step1_workflow/                 # Step 1 구현 4단계 워크플로우
│   │   ├── 01_data_collection.md
│   │   ├── 02_rag_db_generation.md
│   │   ├── 03_qlora_finetuning.md
│   │   └── 04_agent_inference_core.md
│   ├── step2_workflow/                 # Step 2 구현 4단계 워크플로우
│   │   ├── 01_pinmap_to_ioc.md
│   │   ├── 02_cubemx_headless_gen.md
│   │   ├── 03_snippet_injection.md
│   │   └── 04_project_packaging.md
│   └── skills/                         # 구현 스킬 (Python/Shell)
│       ├── skill_parse_pinmap_csv.py
│       ├── skill_ioc_text_modifier.py
│       ├── skill_cubemx_headless_runner.sh
│       └── skill_inject_c_code.py
├── agent/                              # Step 1 리뷰 에이전트
│   └── step1_review_agent.py           # 규칙엔진 + LLM + RAG
├── backend/                            # FastAPI 백엔드
│   ├── main.py                         # POST /v1/review, GET /v1/status, ...
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                           # Streamlit MVP UI
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── golden_modules/                     # STM32G4 HAL 레퍼런스 구현체 (C/H)
│   ├── dc_motor_pid.c/.h               # H-bridge PWM + PID (Anti-windup)
│   ├── multi_axis_sync.c/.h            # TIM1/TIM8/TIM20 PWM 동기화
│   ├── bldc_6step_hall.c/.h            # Hall 인터럽트 6-Step + BRK 보호
│   └── fdcan_motor_cmd.c/.h            # FDCAN 커맨드 파싱 (비상정지 즉시처리)
├── scripts/                            # 데이터 수집 & RAG 파이프라인
│   ├── scrape_st_forum.py              # ST 커뮤니티 포럼 Q&A 수집
│   ├── parse_pdfs.py                   # PDF → 텍스트 (pdfplumber)
│   ├── chunk_docs.py                   # 섹션/블록/슬라이딩윈도우 청킹
│   ├── embed_and_index.py              # BGE-M3 → Qdrant 적재
│   ├── build_bm25.py                   # BM25 역인덱스 구축
│   └── parse_cubemx_xml.py             # CubeMX XML → 핀 AF DB JSON
├── dataset/
│   ├── official_docs/                  # ST 공식 PDF (14건, 55MB 수집 완료)
│   ├── forum_qa/                       # 포럼 수집 결과 (st_forum_qa.jsonl)
│   ├── multi_motor/                    # 멀티모터 설계 가이드
│   └── opensource/                     # 오픈소스 레퍼런스 (8개 프로젝트)
│       ├── STM32CubeG4/                # ST 공식 HAL 예제
│       ├── flatmcu/                    # STM32G473 FOC KiCad 회로도
│       ├── Arduino-FOC/                # SimpleFOC (submodule)
│       ├── stm32-esc/                  # B-G431B-ESC1 (submodule)
│       ├── moteus/                     # 로봇 관절 액추에이터 (submodule)
│       ├── MESC_FOC_ESC/               # 하이엔드 모터 구동 (submodule)
│       ├── bldc_vesc/                  # VESC 오픈소스 ESC (submodule)
│       └── ODriveHardware/             # ODrive 하드웨어 회로도 (submodule)
├── docker-compose.yml                  # Qdrant + Backend + Frontend
├── stm32_agent_plan.md                 # 메인 설계 계획 (7차)
├── stm32_agent_appendex.md             # Appendix A/B/C
├── generate_ppt.py                     # PPT 자동 생성
└── todo.md                             # 작업 현황
```

---

## 빠른 시작 (DGX Spark)

### 전제 조건

```bash
# Ollama 설치 및 모델 로드 (128GB 통합 메모리 — 두 모델 동시 상주 가능)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:31b          # Step 1 (~20GB, Q4_K_M)
ollama pull gemma4:26b          # Step 3 (~22GB, Q8, MoE)

# Docker 설치 확인
docker --version
```

### 1. 저장소 클론

```bash
git clone https://github.com/younlea/MotorDriveForge.git
cd MotorDriveForge
git submodule update --init --recursive   # 오픈소스 6개 프로젝트 다운로드
```

### 2. ST 공식 문서 (수집 완료 ✅)

`dataset/official_docs/`에 14건 PDF가 이미 저장되어 있습니다.
추가 문서가 필요한 경우: `dataset/download_st_docs.sh` 참조.

### 3. RAG 파이프라인 구축

```bash
pip install pdfplumber sentence-transformers qdrant-client rank_bm25 tqdm

# PDF → 텍스트
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

## Step 1 검증 항목 (3계층 규칙 엔진)

### ① 필수 인프라 검증

| 항목 | 설명 | 심각도 |
|------|------|--------|
| VDD/VSS 전원 | 전원 핀 누락 확인 | ERROR |
| VCAP 핀 | 내부 레귤레이터 커패시터 연결 | ERROR |
| BOOT0 핀 | 플로팅 방지 (풀다운 확인) | ERROR |
| NRST 핀 | 리셋 회로 구성 | WARNING |
| SWD 핀 | 디버그 포트 (SWDIO/SWCLK) 연결 | WARNING |

### ② 모터 제어 논리 검증

| 항목 | 설명 | 심각도 |
|------|------|--------|
| 상보 PWM 쌍 | TIM1_CH1 + TIM1_CH1N 동일 타이머 확인 | ERROR |
| 데드타임 삽입 | 상보 PWM 타이머의 BDTR 레지스터 지원 확인 | ERROR |
| TIM1/TIM8 핀 충돌 | PB0/PB1 등 공유 핀 | ERROR |
| BRK 핀 독립성 | 모터별 독립 보호 불가 시 | WARNING |
| 엔코더 전용 타이머 | 엔코더 A/B 핀이 TIM Encoder Mode 지원 확인 | ERROR |

### ③ 페리페럴 제약 검증

| 항목 | 설명 | 심각도 |
|------|------|--------|
| OPAMP 수 초과 | FOC×3채널, G474 최대 6개 → 2모터 한도 | ERROR |
| ADC 트리거 중복 | 동일 트리거 소스 다중 할당 | ERROR |
| DMA 채널 초과 | G4 최대 16채널 | ERROR |
| 핀 AF 검증 | pin_af_db.json 기반 유효성 확인 | ERROR |
| CPU 부하 추정 | 20kHz FOC × 모터 수 → 권장 최대 2개 | WARNING |
| FDCAN 클럭 | FDCAN 사용 시 외부 크리스탈 필요 여부 | WARNING |

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
| 모델 서빙 | Ollama + GGUF | 두 모델 동시 로드 (~42GB) |
| Step 1 LLM | Gemma 4 31B Dense | Q4_K_M, ~20GB, 논리 추론·자연어 파싱 |
| Step 3 LLM | Gemma 4 26B MoE | Q8, ~22GB, Active ~4B, 코드 생성 |
| 벡터 DB | Qdrant (Docker) | hybrid search, port 6333 |
| 임베딩 | BAAI/bge-m3 + BM25 | dense + sparse |
| 백엔드 | FastAPI + uvicorn | port 8000 |
| 프론트 MVP | Streamlit | port 8501 |
| 배포 | Docker Compose | nginx 추가 예정 |

---

## 남은 작업 (todo.md 참조)

- [ ] Git submodule 6개 초기화 (`git submodule update --init --recursive`)
- [ ] X-CUBE-MCSDK 설치 → `dataset/official_docs/cubemx_db/` XML 수집
- [ ] ST 포럼 Q&A 수집 → forum_qa/st_forum_qa.jsonl
- [ ] 오픈소스 FOC 코드 → Golden Module 가공·등록
- [ ] Step 2 CubeMX 자동화 (4단계 워크플로우 구현)
- [ ] Step 3 알고리즘 통합 에이전트 구현
- [ ] React 18 프로덕션 UI (Streamlit MVP 이후)
- [ ] QLoRA Fine-tuning (에러 사례 500건 수집 후)
