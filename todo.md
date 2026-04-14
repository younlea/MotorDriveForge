# TODO — STM32G4 Motor Drive Agent

최종 업데이트: 2026-04-14

---

## 내일 출근 후 즉시 (DGX Spark or 인터넷 PC에서)

### 1. ST 공식 문서 다운로드

```bash
cd /path/to/MotorDriveForge/dataset
chmod +x download_st_docs.sh
./download_st_docs.sh
```

다운로드 대상 (총 14개 PDF):

| 우선도 | 파일 | 용도 |
|--------|------|------|
| ★★★ | RM0440_STM32G4_Reference_Manual.pdf | 핀 AF 검증 RAG 핵심 |
| ★★★ | STM32G474_datasheet.pdf | 핀 테이블 파싱 |
| ★★★ | EVSPIN32G4_DUAL_schematics.pdf | 이중 모터 레퍼런스 설계 |
| ★★★ | AN5306_OPAMP_current_sensing.pdf | 전류센싱 회로 리뷰 |
| ★★★ | AN5789_bootstrap_circuit_design.pdf | 게이트드라이버 리뷰 |
| ★★★ | AN4277_PWM_shutdown_protection.pdf | OCP/BRK 보호 리뷰 |
| ★★☆ | AN4539_HRTIM_cookbook.pdf | HRTIM 멀티모터 타이밍 |
| ★★☆ | AN4220_sensorless_6step_BLDC.pdf | 6-Step 센서리스 |
| ★★☆ | AN4835_highside_current_sensing.pdf | High-side 전류센싱 |
| ★★☆ | AN5036_thermal_management.pdf | 열 관리 설계 |
| ★★☆ | UM2896_EVSPIN32G4_DUAL_user_manual.pdf | 이중 모터 보드 |
| ★★☆ | UM2850_EVSPIN32G4_user_manual.pdf | 단일 모터 보드 |
| ★☆☆ | UM3027_MCSDK_v6_workbench.pdf | SDK 사용법 |
| ★☆☆ | STSPIN32G4_datasheet.pdf | 통합 IC 스펙 |

---

### 2. ST 커뮤니티 포럼 Q&A 수집

```
URL: https://community.st.com/t5/stm32-mcus-motor-control/bd-p/mcu-motor-control-forum
수집 항목: 에러-원인-해결 트리플릿 (에이전트 가이던스 학습 데이터)
목표 수량: 200~300건
도구: BeautifulSoup or Scrapy (로그인 불필요, 공개 포럼)
```

수집 우선 키워드:
- overcurrent protection triggered
- deadtime calculation BLDC
- OPAMP offset calibration
- TIM1 TIM8 synchronization
- encoder count error
- Hall sensor commutation
- bootstrap capacitor
- multi motor FOC

---

### 3. STM32G474 STM32G431 Errata Sheet 다운로드

```
ST 사이트에서 수동 다운로드:
- ES0430 (STM32G474) → dataset/official_docs/datasheets/
- STM32G431 Errata → 동일 경로
URL: https://www.st.com/en/microcontrollers-microprocessors/stm32g474re.html
     → "Design Resources" 탭 → "Errata sheet"
```

---

### 4. X-CUBE-MCSDK 설치 및 HAL 드라이버 수집

```bash
# DGX Spark에서 (또는 Windows에서 설치 후 파일 복사)
# 설치: https://www.st.com/en/embedded-software/x-cube-mcsdk.html
# 설치 후 수집 대상:
STM32CubeMX/db/mcu/STM32G4*.xml          → dataset/official_docs/cubemx_db/
MCSDK/Lib/CMSIS/DSP/Src/*.c              → Golden Module 참고
MCSDK/MotorControl/MCSDK/MCLib/**/*.c   → 알고리즘 레퍼런스
```

---

## 중기 (1~2주 내)

### 5. RAG 파이프라인 구축 (Phase 1 핵심)

```bash
# Qdrant Docker 실행
docker run -d -p 6333:6333 qdrant/qdrant

# 임베딩 모델 설치
ollama pull BAAI/bge-m3  # or pip install sentence-transformers

# PDF 파싱 및 청킹 스크립트 작성
pip install pdfplumber langchain-text-splitters
```

작업 순서:
1. `scripts/parse_pdfs.py` — pdfplumber로 PDF → 텍스트
2. `scripts/chunk_docs.py` — 섹션별 청킹 (RM0440 레지스터 단위 등)
3. `scripts/embed_and_index.py` — BGE-M3 임베딩 → Qdrant 적재
4. `scripts/build_bm25.py` — BM25 역인덱스 구축

청킹 전략 요약:
- RM0440: 섹션 단위 (타이머/ADC/OPAMP 챕터 분리)
- Application Notes: 회로 블록 단위
- 평가보드 회로도: 서브시스템 단위
- GitHub 예제 코드: 함수 단위 (AST 파싱)
- 멀티모터 가이드: 섹션 단위

---

### 6. CubeMX XML 파싱 스크립트 작성

```python
# 목표: {chip: {pin: [AF_list]}} JSON 생성
# 입력: dataset/official_docs/cubemx_db/STM32G4*.xml
# 출력: dataset/pin_af_db.json
```

- G431, G471, G474, G491, G4A1 전 계열 커버
- **멀티모터 필터**: TIM1/TIM8/TIM20 채널 충돌 자동 감지 로직 포함

---

### 7. Golden Module 신규 작성

```
파일 위치: golden_modules/ (신규 폴더)
작성 대상:
  - dc_motor_pid.c / .h      (H-bridge PWM + PID 제어)
  - multi_axis_sync.c / .h   (TIM1+TIM8 카운터 동기화 — 멀티모터 필수)
  - bldc_6step_hall.c / .h   (Hall 인터럽트 6-step)
  - fdcan_motor_cmd.c / .h   (FDCAN 커맨드 파싱)
```

`multi_axis_sync.c` 설계 노트:
- TIM1을 마스터, TIM8/TIM20을 슬레이브로 연결
- TRGO → ITR0 체인 설정
- 두 타이머의 PWM 중앙 정렬 동기화 (ADC 트리거 타이밍 일치 필수)

---

### 8. 회로 리뷰 에이전트 (Step 1) MVP 개발

```
입력: 핀맵 CSV + 자연어 프롬프트
출력: 리뷰 리포트 (errors[], warnings[], suggestions[])
```

추가 체크 항목 (멀티모터):
- [ ] TIM1/TIM8 핀 충돌 (PB0/PB1 공유 여부)
- [ ] OPAMP 수 초과 (FOC × 3 = OPAMP 9개 필요 → G474 한도 초과)
- [ ] BRK 핀 공유 여부 (모터별 독립 보호 불가 시 경고)
- [ ] ADC 트리거 소스 중복
- [ ] DMA 채널 개수 초과 (최대 16채널)
- [ ] CPU 부하 추정 (20kHz FOC × 모터 수 → 권장 최대 2개 FOC)

---

## 장기 (2~4주)

### 9. FastAPI 백엔드 구현

- POST /v1/review: 핀맵 CSV + 프롬프트 → 리뷰 리포트
- GET  /v1/status: 파이프라인 상태 확인
- 검증 게이트: errors[] > 0 → HTTP 403

### 10. Streamlit MVP UI

- 파일 업로드 (PDF, CSV, 핀맵)
- 자연어 프롬프트 입력창
- 리뷰 결과 표시 (errors 빨간색, warnings 노란색)

### 11. Fine-tuning (Phase 5 — 선택)

- 리뷰 에이전트 검증 후 오류 사례 1,000건 수집
- Unsloth QLoRA로 Qwen2.5-72B fine-tune (Step 1용)
- r=32, lr=2e-4, 4096 ctx, ~3일 소요

---

## 완료됨

- [x] CLAUDE.md — 전체 컨텍스트 문서 작성
- [x] stm32_agent_plan.md — 6차 설계 계획 작성
- [x] stm32_agent_appendex.md — Appendix A/B/C 작성
- [x] generate_ppt.py — 36슬라이드 PPT 스크립트
- [x] dataset/README.md — 데이터셋 카탈로그 + 다운로드 가이드
- [x] dataset/download_st_docs.sh — ST PDF 자동 다운로드 스크립트 (14종)
- [x] dataset/multi_motor/ — 멀티모터 설계 가이드 (2~4모터)
- [x] dataset/opensource/flatmcu/ — STM32G473 FOC KiCad 회로도 수집
- [x] dataset/opensource/STM32CubeG4/ — 공식 HAL 예제 수집 (HRTIM/TIM/ADC/OPAMP/FDCAN/CORDIC)
