# TODO — STM32G4 Motor Drive Agent

최종 업데이트: 2026-06-06

> **안내:** 단기 최우선 과제(Task 01~06) 및 상세 작업 우선순위는 [`tasks/README.md`](./tasks/README.md)에서 별도로 관리되고 있습니다. 본 `todo.md`는 전체 프로젝트의 거시적 진행 상황과 인프라 설정 작업을 주로 추적합니다.

---

## ✅ 완료 (2026-06-06 세션)

**Step 1 — HW 검증**
- [x] Vision 백그라운드 추출(블로킹 폐기 버그 해결), CSV 우선 + Vision 페리페럴 분담
- [x] 확정 편집기: 전체 핀 이름순 + IO(FT/TT) 배지 + AF 드롭다운(CubeMX DB 파생)
- [x] 라벨→function 자동추정 + 확정 매핑 누적 학습(`/v1/label-hints`)
- [x] Rule Engine: 완전 AF 검증(오경고 제거), 핀/신호 중복, 모터 미가정 기본값
- [x] 다중 MCU(같은 칩 N개 포함) — `mcu` 지정자 그룹핑, MCU별 검증
- [x] 핀맵 CSV/Excel(MCU별 탭) 내보내기 + 재업로드

**Step 2 — .ioc 생성 (신규 가동, LQFP·UFBGA 검증)**
- [x] `_build_ioc_content` — 스켈레톤 + 부품번호 디코드 식별자 + 주변장치 자동 합성
- [x] CubeMX 로드 크래시 6종 해결(parseInt, CurrentBGAView, 특수핀 풀네임, GPIOParameters,
      function없는핀/고아주변장치 강등, SH 매핑) — 메모리 `project_ioc_template_based` 참조
- [x] CubeMX DB(db/mcu/*.xml) → `agent/pin_options/`, 데이터시트 → `agent/pin_io_structure.json`

**Step 3 — Golden Module "바인딩 glue" 재설계 (✅ 구현, Spark 검증 대기)**
- [x] 결정론 역할 매퍼 `map_roles`(PWM/ENC/전류/DIR/BRK/FDCAN) — flat·다중 MCU(MCU별 처리) 대응
- [x] 생성 HAL 프로젝트 파서 `parse_hal_project`(핸들/GPIO라벨매크로/ADC채널/USER CODE 마커)
      + 결정론 폴백 `derive_binding`(프로젝트 없을 때)
- [x] glue 생성 `_llm_glue` — 모듈 내부 미변경, 구조체 바인딩 4블록(Includes/PV/2/3)
- [x] 코드 RAG `rag_query_code`(stm32g4_code) + `parse_opensource_algorithms.py`(957청크, 라이선스 태그)
- [x] 통합 `integrate` — 모듈 복사 + Makefile/CMake 등록 + main.c 주입(idempotent)
- [x] 백엔드: CLI 자동생성 우선→업로드 폴백, 통합 ZIP 다운로드. 프론트 Tab3 재구성
- [ ] **Spark 검증**: `embed_and_index.py --chunks-dir dataset/chunks_code --collection stm32g4_code` 후
      실제 CubeMX 프로젝트로 glue 생성·빌드성(arm-none-eabi-gcc) 확인

**다음 작업**
- [ ] (선택) .ioc RCC 기본을 보수적(16MHz HSI)으로 — G431 USB/RNG 클럭 경고 정리
- [ ] 동시 사용자: 전역 상태(`_review_partial`) 요청별 분리 (동시 검증 충돌 방지)
- [ ] STM32CubeG4 청크 상한(400) 도달 시 HAL 예제 보일러플레이트 우선순위 하향(품질 개선)

---

## 🔴 즉시 필요 — Git Submodule 초기화

```bash
cd /path/to/MotorDriveForge
git submodule update --init --recursive

# 확인 (6개 프로젝트에 파일이 존재해야 함)
ls dataset/opensource/Arduino-FOC/
ls dataset/opensource/stm32-esc/
ls dataset/opensource/moteus/
ls dataset/opensource/MESC_FOC_ESC/
ls dataset/opensource/bldc_vesc/
ls dataset/opensource/ODriveHardware/
```

---

## 🔴 DGX Spark 환경 구성

### Step 1. Ollama 모델 확인

```bash
ollama list
# gemma4:31b 가 없으면:
ollama pull gemma4:31b          # Step 1 + Step 3 공유 (~20GB, Q4_K_M, Dense)
# ※ gemma4:26b 는 2026-06-10 단일화로 더 이상 필요 없음
```

### Step 2. X-CUBE-MCSDK 설치 → XML/알고리즘 수집

```bash
# Windows PC에서 설치 후 DGX Spark로 복사
# 설치 URL: https://www.st.com/en/embedded-software/x-cube-mcsdk.html

# 수집 대상:
STM32CubeMX/db/mcu/STM32G4*.xml        → dataset/official_docs/cubemx_db/
MCSDK/MotorControl/MCSDK/MCLib/**/*.c  → 알고리즘 레퍼런스 참고
```

### Step 3. RAG 파이프라인 가동 (PDF 이미 수집됨)

```bash
pip install pdfplumber sentence-transformers qdrant-client rank_bm25 tqdm requests beautifulsoup4

# PDF → 텍스트
python scripts/parse_pdfs.py

# 청킹
python scripts/chunk_docs.py

# Qdrant 실행
docker run -d -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant

# 임베딩 → Qdrant 적재 (BGE-M3, 배치 32)
python scripts/embed_and_index.py

# BM25 인덱스
python scripts/build_bm25.py

# 핀 AF DB 생성 (cubemx_db XML 있으면 자동 파싱, 없으면 폴백 테이블)
python scripts/parse_cubemx_xml.py
```

### Step 4. 전체 서비스 가동

```bash
docker-compose up -d

# 접속 확인
curl http://localhost:8000/v1/health
curl http://localhost:8000/v1/status
# UI: http://localhost:8501
```

---

## 🟡 인터넷 PC에서 — ST 포럼 Q&A 수집

```bash
pip install requests beautifulsoup4 tqdm
python scripts/scrape_st_forum.py --max-items 300
# 출력: dataset/forum_qa/st_forum_qa.jsonl
# 수집 후 DGX Spark로 복사 → embed_and_index.py 재실행
```

우선 수집 키워드: overcurrent protection, deadtime BLDC, OPAMP offset, TIM1/TIM8 sync, encoder error, Hall commutation, bootstrap capacitor, multi motor FOC

---

## 🟠 미완료 코드 작업

### Step 1 — 5 페르소나 + 모더레이터 (미구현)

```
현재: agent/step1_review_agent.py::_llm_validate() 단일 LLM 호출 1회
      (system 프롬프트 1개, STM32 전문가 단일 역할, chunk_id 인용 없음)
구현 필요:
  - 4개 페르소나 프롬프트 분리 (MCU/Motor/Power/Safety)
  - 모더레이터 통합 단계 추가
  - 접근 방식 결정 필요:
    a) 순차 5회 LLM 호출 (품질↑, 응답시간 5×)
    b) 단일 호출 in-context 5-role (빠름, 현실적)
  - chunk_id 인용 강제 패턴 추가
```

### 오픈소스 → Golden Module 가공

```
대상: dataset/opensource/Arduino-FOC, MESC_FOC_ESC, bldc_vesc, moteus 에서 추출
결과물: golden_modules/ 에 foc_clarke.c, foc_park.c, foc_svpwm.c 등 등록
향후: 사내 최적화 코드 확보 시 교체 예정
```

### Step 2 구현 — CubeMX 자동화 (4단계 워크플로우)

```
워크플로우 계획: work/step2_workflow/ 에 4단계 정의 완료
스킬 코드: work/skills/ 에 4개 스킬 초안 작성 완료

구현 필요:
  [2-1] skill_ioc_text_modifier.py → 프로덕션 수준 구현
  [2-2] skill_cubemx_headless_runner.sh → CubeMX CLI 연동 테스트
  [2-3] skill_inject_c_code.py → USER CODE 마커 정확 탐지
  [2-4] ZIP 패키징 → shutil 기반 구현
```

### Step 3 — Spark 검증 (미완료)

```
파일 위치: agent/step3_codegen_agent.py (✅ 구현 완료)
모델: gemma4:31b (Ollama, Step 1과 동일 모델 단일 상주 — 2026-06-10 단일화)
남은 작업:
  - embed_and_index.py --chunks-dir dataset/chunks_code --collection stm32g4_code 실행
  - 실제 CubeMX 프로젝트로 glue 생성·빌드성(arm-none-eabi-gcc) 확인
```

---

## 🔵 장기 (2~4주)

### React 18 프로덕션 UI

- Streamlit MVP 검증 후 전환
- React 18 + TypeScript + Tailwind + shadcn/ui
- nginx + Docker 배포

### Fine-tuning (Phase 3 — 선택)

```
조건: 검증 에이전트 오류 사례 500건 수집 후
Step 1: Gemma 4 31B Dense QLoRA (r=32, JSON 리포트 양식 내재화)
Step 3: Gemma 4 26B MoE QLoRA (r=64, USER CODE 삽입 정확도 향상)
설정: Unsloth QLoRA, lr=2e-4, 4096 ctx
소요: ~3일 (DGX Spark)
```

---

## ✅ 완료됨

### 설계 문서
- [x] CLAUDE.md — 전체 컨텍스트 문서 (7차 업데이트)
- [x] stm32_agent_plan.md — 7차 설계 계획 (Gemma-4, HW Expert Agent)
- [x] stm32_agent_appendex.md — Appendix A/B/C (학습 데이터·QLoRA·웹 개발)
- [x] generate_ppt.py — PPT 자동 생성 스크립트

### 상세 기획 (work/ 디렉토리)
- [x] work/step1_agent_plan.md — HW Expert Agent 상세 (3계층 검증, Gemma-4, 데이터 플로우)
- [x] work/step2_code_gen_plan.md — C 코드 자동생성 파이프라인 (CubeMX CLI, 스니펫 주입)
- [x] work/step1_workflow/ — Step 1 구현 워크플로우 4단계
- [x] work/step2_workflow/ — Step 2 구현 워크플로우 4단계
- [x] work/skills/ — 4개 Python/Shell 스킬 초안

### 데이터셋 기반
- [x] dataset/README.md — 데이터셋 카탈로그 + 다운로드 가이드
- [x] dataset/download_st_docs.sh — ST PDF URL 매핑 스크립트 (14종)
- [x] dataset/official_docs/ — **14건 PDF 수집 완료** (55MB)
- [x] dataset/multi_motor/ — 멀티모터 설계 가이드 (2~4모터)
- [x] dataset/opensource/flatmcu/ — STM32G473 FOC KiCad 회로도 ✅
- [x] dataset/opensource/STM32CubeG4/ — 공식 HAL 예제 ✅
- [x] dataset/opensource/ — 6개 추가 프로젝트 Git Submodule 등록 (초기화 필요)

### RAG 파이프라인 스크립트
- [x] scripts/scrape_st_forum.py — ST 포럼 Q&A 수집기 (에러-원인-해결 트리플릿)
- [x] scripts/parse_pdfs.py — PDF → 텍스트 (pdfplumber, 카테고리별)
- [x] scripts/chunk_docs.py — 섹션/블록/슬라이딩윈도우 청킹 전략
- [x] scripts/embed_and_index.py — BGE-M3 임베딩 → Qdrant upsert
- [x] scripts/build_bm25.py — BM25 역인덱스 구축
- [x] scripts/parse_cubemx_xml.py — CubeMX XML → 핀 AF DB JSON + 멀티모터 충돌 감지

### Golden Modules (STM32G4 HAL)
- [x] golden_modules/dc_motor_pid.c/.h — H-bridge PWM + PID (Anti-windup, ±100% 듀티)
- [x] golden_modules/multi_axis_sync.c/.h — TIM1/TIM8/TIM20 PWM 동기화 (CR2/SMCR 레지스터 직접)
- [x] golden_modules/bldc_6step_hall.c/.h — Hall 인터럽트 6-Step + BRK 보호
- [x] golden_modules/fdcan_motor_cmd.c/.h — FDCAN 커맨드 파싱 + 비상정지 즉시 처리

### 에이전트 & 서비스
- [x] agent/step1_review_agent.py — Step 1 MVP (규칙엔진 + Ollama LLM + Qdrant RAG)
- [x] backend/main.py — FastAPI (POST /v1/review, 검증 게이트 HTTP 403, GET /v1/status)
- [x] frontend/app.py — Streamlit MVP UI (errors 빨강, warnings 노랑, 연결 상태 사이드바)
- [x] docker-compose.yml — Qdrant + Backend + Frontend 통합 배포
- [x] README.md — 전체 셋업 가이드 (DGX Spark 기준)

### Task 07 — Vision 멀티모달 입력 (2026-05-14)
- [x] agent/step1_review_agent.py — Vision 스텝 추가 (이미지 → pinmap 자동 추출, RAG 쿼리 보강)
- [x] backend/main.py — `schematic_image` 이미지 업로드 수신, base64 변환
- [x] frontend/app.py — 이미지 업로더 주 입력 전환, Vision 분석 결과 표시 섹션
- [x] ARCHITECTURE.md — Vision 노드 다이어그램 추가, 데이터 흐름 업데이트
- [x] CLAUDE.md — Step 1 의존성 다이어그램 업데이트
- [x] tasks/07_vision_multimodal_input.md — 작업 명세 문서
