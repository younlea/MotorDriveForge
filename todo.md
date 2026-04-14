# TODO — STM32G4 Motor Drive Agent

최종 업데이트: 2026-04-14

---

## 🔴 내일 출근 즉시 — DGX Spark 환경 구성

### Step 0. 저장소 클론

```bash
git clone https://github.com/younlea/MotorDriveForge.git
cd MotorDriveForge
```

---

### Step 1. ST 공식 PDF 수동 저장 ⚠️ 필수 (RAG 핵심 데이터)

> ST 서버가 curl 직접 다운로드 차단. st.com 로그인 후 브라우저에서 직접 저장.

저장 경로: `dataset/official_docs/` 하위

| 우선도 | 파일명 | 저장 위치 |
|--------|--------|-----------|
| ★★★ | RM0440_STM32G4_Reference_Manual.pdf | `reference_manual/` |
| ★★★ | STM32G474_datasheet.pdf | `datasheets/` |
| ★★★ | AN5306_OPAMP_current_sensing.pdf | `application_notes/` |
| ★★★ | AN5789_bootstrap_circuit_design.pdf | `application_notes/` |
| ★★★ | AN4277_PWM_shutdown_protection.pdf | `application_notes/` |
| ★★★ | EVSPIN32G4_DUAL_schematics.pdf | `eval_boards/` |
| ★★☆ | AN4539_HRTIM_cookbook.pdf | `application_notes/` |
| ★★☆ | AN4220_sensorless_6step_BLDC.pdf | `application_notes/` |
| ★★☆ | AN4835_highside_current_sensing.pdf | `application_notes/` |
| ★★☆ | AN5036_thermal_management.pdf | `application_notes/` |
| ★★☆ | UM2896_EVSPIN32G4_DUAL_user_manual.pdf | `eval_boards/` |
| ★★☆ | UM2850_EVSPIN32G4_user_manual.pdf | `eval_boards/` |
| ★☆☆ | UM3027_MCSDK_v6_workbench.pdf | `sdk_docs/` |
| ★☆☆ | STSPIN32G4_datasheet.pdf | `datasheets/` |

전체 URL 목록: `dataset/download_st_docs.sh` 참조.

---

### Step 2. Errata Sheet 수동 다운로드

```
- ES0430 (STM32G474) → dataset/official_docs/datasheets/
- STM32G431 Errata  → dataset/official_docs/datasheets/
경로: https://www.st.com/en/microcontrollers-microprocessors/stm32g474re.html
     → "Design Resources" 탭 → "Errata sheet"
```

---

### Step 3. X-CUBE-MCSDK 설치 → XML/알고리즘 수집

```bash
# Windows PC에서 설치 후 DGX Spark로 복사
# 설치 URL: https://www.st.com/en/embedded-software/x-cube-mcsdk.html

# 수집 대상:
STM32CubeMX/db/mcu/STM32G4*.xml        → dataset/official_docs/cubemx_db/
MCSDK/MotorControl/MCSDK/MCLib/**/*.c  → 알고리즘 레퍼런스 참고
```

---

### Step 4. Ollama 모델 확인

```bash
ollama list
# qwen2.5:72b 와 qwen2.5-coder:32b 가 없으면:
ollama pull qwen2.5:72b          # ~40GB (Q4_K_M)
ollama pull qwen2.5-coder:32b    # ~32GB (Q8)
```

---

### Step 5. RAG 파이프라인 가동 (PDF 저장 후)

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

---

### Step 6. 전체 서비스 가동

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

### Step 2 구현 — CubeMX 자동화

```
파일 위치: scripts/generate_ioc.py (미작성)
입력: validated_pins JSON (Step 1 출력)
동작:
  1. 핀 JSON → .ioc 파일 텍스트 생성
  2. STM32CubeMX -q script.txt 실행
  3. 생성된 HAL 코드(.zip) 반환
출력: main.c, tim.c, adc.c, fdcan.c, ...
```

### Step 3 구현 — 알고리즘 통합 에이전트

```
파일 위치: agent/step3_integration_agent.py (미작성)
모델: qwen2.5-coder:32b (Ollama)
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

### Fine-tuning (Phase 5 — 선택)

```
조건: 리뷰 에이전트 오류 사례 1,000건 수집 후
모델: Qwen2.5-72B (Step 1용), Qwen2.5-Coder-32B (Step 3용)
설정: Unsloth QLoRA, r=32/64, lr=2e-4, 4096 ctx
소요: ~3일 (DGX Spark)
```

---

## ✅ 완료됨

### 설계 문서
- [x] CLAUDE.md — 전체 컨텍스트 문서
- [x] stm32_agent_plan.md — 6차 설계 계획
- [x] stm32_agent_appendex.md — Appendix A/B/C (학습 데이터·QLoRA·웹 개발)
- [x] generate_ppt.py — 36슬라이드 PPT 자동 생성 스크립트

### 데이터셋 기반
- [x] dataset/README.md — 데이터셋 카탈로그 + 다운로드 가이드
- [x] dataset/download_st_docs.sh — ST PDF URL 매핑 스크립트 (14종)
- [x] dataset/multi_motor/ — 멀티모터 설계 가이드 (2~4모터)
- [x] dataset/opensource/flatmcu/ — STM32G473 FOC KiCad 회로도
- [x] dataset/opensource/STM32CubeG4/ — 공식 HAL 예제 (HRTIM/TIM/ADC/OPAMP/FDCAN/CORDIC)

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
- [x] agent/step1_review_agent.py — Step 1 MVP (규칙엔진 7항목 + Ollama LLM + Qdrant RAG)
- [x] backend/main.py — FastAPI (POST /v1/review, 검증 게이트 HTTP 403, GET /v1/status)
- [x] frontend/app.py — Streamlit MVP UI (errors 빨강, warnings 노랑, 연결 상태 사이드바)
- [x] docker-compose.yml — Qdrant + Backend + Frontend 통합 배포
- [x] README.md — 전체 셋업 가이드 (DGX Spark 기준)
