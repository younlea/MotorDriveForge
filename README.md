# MotorDriveForge

STM32G4 전용 사내 Agent 시스템 — 회로도 입력부터 완성 펌웨어까지 자동화.

> 운영 환경: NVIDIA DGX Spark 128GB · 완전 로컬 (외부망 차단)
> 타겟 칩: STM32G4 계열 (G431 / G471 / G474 / G491 / G4A1)

**자세한 시스템 다이어그램, 모델 카탈로그, 데이터 소스:** [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 3-Step 파이프라인

```
[입력] 회로도 이미지 (선택) + 핀맵 CSV + 자연어 프롬프트
         ↓
[Vision] Gemma 4 31B Multimodal — 이미지 → 핀맵 자동 추출 + 초기 분석 (이미지 없으면 스킵)
         ↓
[STEP 1] HW 설계 검증 Agent      (Gemma 4 31B Dense · Ollama)
         A Rule Engine (결정론) → B Hybrid RAG → C LLM Persona Debate
         mode=fast: Rule Engine만, errors > 0 → HTTP 403
         mode=full(기본): A→B→C 전체, errors 있어도 자연어 설명 반환
         ↓ PASS only (errors == 0)
[STEP 2] CubeMX 자동화            (4단계 워크플로우, LLM 없음)
         .ioc 템플릿 수정 → CubeMX CLI → 스니펫 주입 → ZIP
         ↓
[STEP 3] 알고리즘 통합 Agent      (Gemma 4 26B MoE · Ollama)
         Golden Module RAG → USER CODE BEGIN/END 삽입
         ↓
[출력] 완성 펌웨어 .zip
```

---

## 진행 현황 (2026-06-06 갱신)

### ✅ Step 1 — HW 검증 (가동)
- Vision(Gemma 4 31B)으로 회로도 → 핀맵 추출. **백그라운드 실행**(블로킹 폐기 문제 해결).
- **CSV 업로드 우선** + Vision은 페리페럴/분석 담당(둘 다 있으면 CSV가 핀맵, Vision이 페리페럴).
- 확정 편집기: **칩의 전체 핀을 이름순**으로 + 핀별 **IO(FT/TT) 배지** + **AF 드롭다운**(CubeMX DB 파생).
  - **라벨→function 자동 추정**(검토 후 수정) + **확정 매핑 누적 학습**(`/v1/label-hints`).
- Rule Engine: AF 검증을 **완전한 CubeMX AF**로(빈약한 내장표 오경고 제거), 핀·신호(채널) 중복,
  JTAG 핀 재사용(경고), 모터 미가정(control_type 기본 unspecified — 명시될 때만 FOC/3상).
- **다중 MCU**(같은 칩 N개 포함) — `mcu` 지정자로 그룹핑, MCU별 검증.
- 핀맵 **CSV/Excel(MCU별 탭·AF 전체+드롭다운) 내보내기 + 재업로드**.

### ✅ Step 2 — CubeMX .ioc 생성 (가동, BGA 포함)
- `_build_ioc_content`: 깡통 스켈레톤 + 부품번호 디코드 식별자 + 핀/주변장치 자동 합성
  (TIM PWM/엔코더, ADC 채널, SPI, FDCAN, GPIO). **LQFP·UFBGA 모두 CubeMX 로드 검증**.
- MCU별 `.ioc` 생성(Step 2에서 MCU 토글 선택).

### ⏳ Step 3 — 알고리즘 통합 (다음 작업)
- Golden Module 적응 골격은 있음. **다중 MCU·최신 핀맵 연동 정리 필요.**

---

## 디렉토리 구조

```
MotorDriveForge/
├── agent/                              # Step 1 리뷰 에이전트
│   ├── step1_review_agent.py           # 규칙엔진 + Vision + LLM + RAG 오케스트레이터
│   └── personas/                       # 5 페르소나 (Task 05, 예정)
│       ├── base.py
│       ├── mcu_periph.py / motor_control.py / power_emi.py / safety_failsafe.py
│       ├── moderator.py
│       └── prompts/*.yaml
├── backend/                            # FastAPI 백엔드
│   ├── main.py                         # POST /v1/review (mode, schematic_image 파라미터)
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                           # Streamlit MVP UI
│   ├── app.py                          # 이미지 업로더 + CSV 보조 입력
│   ├── requirements.txt
│   └── Dockerfile
├── golden_modules/                     # STM32G4 HAL 레퍼런스 구현체 (C/H)
│   ├── dc_motor_pid.c/.h               # H-bridge PWM + PID (Anti-windup)
│   ├── multi_axis_sync.c/.h            # TIM1/TIM8/TIM20 PWM 동기화
│   ├── bldc_6step_hall.c/.h            # Hall 인터럽트 6-Step + BRK 보호
│   └── fdcan_motor_cmd.c/.h            # FDCAN 커맨드 파싱 (비상정지 즉시처리)
├── scripts/                            # 데이터 수집 & RAG 파이프라인
│   ├── parse_pdfs.py                   # PDF → 텍스트 (pdfplumber)
│   ├── chunk_docs.py                   # 섹션/블록/슬라이딩윈도우 청킹
│   ├── embed_and_index.py              # BGE-M3 → Qdrant 적재
│   ├── build_bm25.py                   # BM25 역인덱스 구축
│   ├── parse_cubemx_xml.py             # CubeMX XML → 핀 AF DB JSON
│   ├── parse_opensource_code.py        # OSS .ioc/netlist → 청크
│   ├── scrape_st_forum.py              # ST 커뮤니티 포럼 Q&A 수집
│   └── scrape_ti_e2e.py                # 🆕 TI E2E 포럼 크롤러 (Task 02)
├── dataset/
│   ├── official_docs/                  # ST 공식 PDF (14건)
│   │   └── errata/                     # 🆕 G4 errata PDF (Task 03)
│   ├── forum_qa/
│   │   ├── st_forum_qa.jsonl           # ST 포럼 수집 결과
│   │   └── ti_e2e/                     # 🆕 TI E2E 크롤링 결과 (Task 02)
│   ├── synthetic/                      # 🆕 합성 망가진 스키매틱 (Task 04)
│   │   ├── seeds/                      # EVM 정규화 JSON
│   │   └── pairs/                      # mutation 결과 페어
│   ├── chunks/                         # 청킹 결과 JSONL
│   ├── bm25_index/                     # BM25 인덱스
│   ├── multi_motor/                    # 멀티모터 설계 가이드
│   └── opensource/                     # 오픈소스 레퍼런스 (submodules)
│       ├── STM32CubeG4/
│       ├── flatmcu/
│       ├── Arduino-FOC/
│       ├── stm32-esc/
│       ├── moteus/
│       ├── MESC_FOC_ESC/
│       ├── bldc_vesc/
│       └── ODriveHardware/
├── tasks/                              # 작업 명세 (우선순위 순)
│   ├── README.md                       # 의존성 Mermaid + 주차별 실행 순서
│   ├── 01_split_review_modes.md
│   ├── 02_ti_e2e_crawler.md
│   ├── 03_g4_errata_ingestion.md
│   ├── 04_synthetic_broken_schematics.md
│   ├── 05_persona_debate.md
│   ├── 06_codegen_benchmark.md
│   └── 07_vision_multimodal_input.md   # ✅ 완료
├── work/                               # Step 1/2 상세 기획 문서
│   ├── step1_workflow/ · step2_workflow/ · skills/
├── docker-compose.yml
├── ARCHITECTURE.md                     # ⭐ 시스템 다이어그램·모델·데이터 카탈로그
├── CLAUDE.md                           # ⭐ Claude Code 작업 가이드 (이 저장소 개발 규칙)
├── stm32_agent_plan.md
├── stm32_agent_appendex.md
├── generate_ppt.py
└── todo.md
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

> **모든 명령은 프로젝트 루트(`MotorDriveForge/`)에서 실행합니다.**

#### 3-1. Python 가상환경 준비

```bash
# 프로젝트 루트에서 실행
cd /home/robot/source-code/MotorDriveForge

python3 -m venv .venv
source .venv/bin/activate

pip install pdfplumber sentence-transformers qdrant-client rank_bm25 tqdm lxml
```

#### 3-2. Qdrant 벡터 DB 실행 (Docker)

```bash
# 프로젝트 루트에서 실행
cd /home/robot/source-code/MotorDriveForge

docker run -d --name qdrant \
  -p 6333:6333 \
  -v qdrant_storage:/qdrant/storage \
  qdrant/qdrant

# 실행 확인
curl http://localhost:6333/healthz
```

#### 3-3. 소스별 파싱 → 청킹

각 스크립트는 **프로젝트 루트를 자동 감지**합니다. 실행 디렉토리에 무관하게 동작합니다.

```bash
# 프로젝트 루트에서 실행
cd /home/robot/source-code/MotorDriveForge

# [소스 1] ST 공식 PDF → 텍스트 파일
#   입력: dataset/official_docs/**/*.pdf  (14건)
#   출력: dataset/parsed_text/{카테고리}/{파일명}.txt
python scripts/parse_pdfs.py

# [소스 1] PDF 텍스트 → 청크 JSONL
#   입력: dataset/parsed_text/**/*.txt
#   출력: dataset/chunks/{파일명}_chunks.jsonl
python scripts/chunk_docs.py

# [소스 2] 오픈소스 핀 연결 정보 추출 → 청크 JSONL
#   입력: dataset/opensource/ (STM32CubeG4 .ioc 117개, stm32-esc pinmap,
#          moteus pinout.txt, VESC hwconf, flatmcu KiCad netlist)
#   출력: dataset/chunks/opensource_pin_chunks.jsonl
#   ※ git submodule이 초기화되어 있어야 합니다
python scripts/parse_opensource_code.py
```

#### 3-4. 임베딩 → Qdrant 적재 + BM25 인덱스

```bash
# 프로젝트 루트에서 실행
cd /home/robot/source-code/MotorDriveForge

# BGE-M3 dense 임베딩 → Qdrant collection 'stm32g4_docs' 적재
#   입력: dataset/chunks/*_chunks.jsonl  (PDF + 오픈소스 청크 전부)
#   출력: Qdrant collection (http://localhost:6333)
#   ※ 첫 실행 시 BGE-M3 모델 다운로드 (~1.1GB), 시간 소요
python scripts/embed_and_index.py

# BM25 역인덱스 구축 (TIM1_CH1N 같은 정확 매칭용)
#   입력: dataset/chunks/*_chunks.jsonl
#   출력: dataset/bm25_index/bm25_index.pkl
#         dataset/bm25_index/doc_map.jsonl
python scripts/build_bm25.py
```

#### 3-5. 핀 AF DB 생성

```bash
# 프로젝트 루트에서 실행
cd /home/robot/source-code/MotorDriveForge

# CubeMX XML → 핀 AF DB JSON
#   입력: dataset/official_docs/cubemx_db/STM32G4*.xml
#         (X-CUBE-MCSDK 설치 후 수집 — 없으면 하드코딩 폴백 테이블 사용)
#   출력: dataset/pin_af_db.json
python scripts/parse_cubemx_xml.py
```

#### 3-6. 파이프라인 완료 후 디렉토리 구조 확인

```
dataset/
├── official_docs/          # 입력: ST PDF 원본 (14건)
├── parsed_text/            # 생성: parse_pdfs.py 출력
│   ├── misc/
│   └── {카테고리}/
├── chunks/                 # 생성: chunk_docs.py + parse_opensource_code.py 출력
│   ├── *_chunks.jsonl      #   (PDF 청크)
│   └── opensource_pin_chunks.jsonl
├── bm25_index/             # 생성: build_bm25.py 출력
│   ├── bm25_index.pkl
│   └── doc_map.jsonl
└── pin_af_db.json          # 생성: parse_cubemx_xml.py 출력
```

> **Note**: `embed_and_index.py`의 출력은 Qdrant 컨테이너 내부(`qdrant_storage` Docker volume)에 저장됩니다. `dataset/` 안에 파일로 남지 않습니다.

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
# 풀 리뷰 (기본, 자연어 설명 포함)
curl -X POST http://localhost:8000/v1/review \
  -F "chip=STM32G474RET6" \
  -F "prompt=BLDC 1개 FOC, 증분형 엔코더, FDCAN 1Mbps, 시스템 170MHz" \
  -F "csv_file=@pinmap.csv" \
  -F "mode=full"

# 빠른 차단 모드 (CI용, Rule Engine만 실행)
curl -X POST http://localhost:8000/v1/review \
  -F "csv_file=@pinmap.csv" \
  -F "mode=fast"

# 회로도 이미지로 핀맵 자동 추출 + 리뷰 (Task 07 완료)
curl -X POST http://localhost:8000/v1/review \
  -F "schematic_image=@schematic.png" \
  -F "prompt=BLDC FOC 설계 검토해줘"

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
| Step 3 LLM (1차) | Gemma 4 26B MoE | Q8, ~22GB, Active ~4B, 코드 생성 |
| Step 3 LLM (A/B) | Qwen3-Coder 30B A3B | HAL 정확도 비교 후 채택 (Task 06) |
| 벡터 DB | Qdrant (Docker) | hybrid search, port 6333 |
| 임베딩 | BAAI/bge-m3 + BM25 | dense + sparse |
| 백엔드 | FastAPI + uvicorn | port 8000 |
| 프론트 MVP | Streamlit | port 8501 |
| 배포 | Docker Compose | nginx 추가 예정 |

---

## 남은 작업 (Tasks & TODO)

상세 작업 명세 및 우선순위는 [`tasks/README.md`](./tasks/README.md)와 [`todo.md`](./todo.md)에서 관리됩니다.

### ✅ 완료
- [x] [Task 07] Vision 멀티모달 입력 — 회로도 이미지 → 핀맵 자동 추출 (2026-05-14)

### 🔴 최우선 진행 (Tasks)
- [ ] [Task 01] Step 1 운영 모드 분리 (fast/full)
- [ ] [Task 02] TI E2E 모터드라이버 포럼 크롤러 파이프라인 구축
- [ ] [Task 03] STM32G4 Errata 명시적 인제스천 (RAG 품질 개선)
- [ ] [Task 04] 합성 망가진 스키매틱 파이프라인 구축 (평가셋 확보)
- [ ] [Task 05] 5 페르소나 토론 시스템

### 🟢 중간
- [ ] [Task 06] Step 3 코드 생성 모델 A/B 벤치마크 (Gemma 4 26B vs Qwen3-Coder 30B)

### 🟡 일반 과제 (TODO)
- [ ] Git submodule 6개 초기화 (`git submodule update --init --recursive`)
- [ ] X-CUBE-MCSDK 설치 → `dataset/official_docs/cubemx_db/` XML 수집
- [ ] ST 포럼 Q&A 수집 마무리
- [ ] 오픈소스 FOC 코드 → Golden Module 가공·등록
- [ ] Step 2 CubeMX 자동화 (4단계 워크플로우 구현)
- [ ] Step 3 알고리즘 통합 에이전트 구현
- [ ] React 18 프로덕션 UI (Streamlit MVP 이후)
- [ ] QLoRA Fine-tuning (에러 사례 수집 후)

---

## 기여 / 개발

작업 단위 task 명세는 [`tasks/`](./tasks/) 디렉토리에 정리되어 있습니다.

- 시작 전 [`CLAUDE.md`](./CLAUDE.md)와 [`ARCHITECTURE.md`](./ARCHITECTURE.md)를 먼저 읽어주세요.
- Claude Code 사용 시 `tasks/<번호>_<제목>.md` 파일을 컨텍스트로 제공하세요.
- 작업 우선순위와 의존성은 [`tasks/README.md`](./tasks/README.md) 참조.
