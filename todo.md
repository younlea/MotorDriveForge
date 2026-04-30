# TODO — STM32G4 Motor Drive Agent

최종 업데이트: 2026-04-27

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
# gemma4:31b 와 gemma4:26b 가 없으면:
ollama pull gemma4:31b          # Step 1 (~20GB, Q4_K_M, Dense)
ollama pull gemma4:26b          # Step 3 (~22GB, Q8, MoE)
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

### Step 3 구현 — 알고리즘 통합 에이전트

```
파일 위치: agent/step3_integration_agent.py (미작성)
모델: gemma4:26b (Ollama, MoE)
동작:
  1. Golden Module RAG → 요구사항에 맞는 모듈 선택
  2. USER CODE BEGIN/END 영역에 FOC 알고리즘 삽입
  3. 완성 펌웨어 .zip 반환
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
