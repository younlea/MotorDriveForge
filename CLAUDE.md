# CLAUDE.md — MotorDriveForge 작업 가이드

이 파일은 Claude Code가 이 저장소에서 작업할 때 참고하는 컨텍스트 문서입니다.
새 작업을 시작하기 전에 항상 이 문서와 `ARCHITECTURE.md`를 먼저 읽어주세요.

---

## 프로젝트 한 줄 요약

STM32G4 모터 드라이버 회로를 검토하고, 통과 시 CubeMX로 펌웨어 프로젝트까지 자동 생성하는 사내 에이전트. 완전 로컬(외부망 차단), DGX Spark 128GB에서 동작.

## 운영 환경

- **하드웨어**: NVIDIA DGX Spark 128GB 통합 메모리, 외부망 차단
- **타겟 칩**: STM32G431 / G471 / G474 / G491 / G4A1
- **모델 서빙**: Ollama (로컬), Gemma 4 31B + 26B 동시 상주
- **벡터 DB**: Qdrant (Docker)
- **백엔드**: FastAPI, **프론트**: Streamlit (MVP) → React (추후)

---

## 핵심 아키텍처 원칙 (반드시 지킬 것)

### 1. 결정론과 LLM의 분업
- **결정론적 위반은 Rule Engine이 단독 처리** — 핀 충돌, AF 미존재, BDTR 미지원 등
- **LLM은 판단형/모호한 항목에만** — 디커플링 권고, CPU 부하 여유, 트레이드오프
- LLM이 결정론적 검증을 대체하면 안 됨 (재현성 망가짐)

### 2. RAG는 LLM의 입력을 보강하는 용도
- RAG가 단독으로 답을 만들지 않음
- Hybrid RAG(BGE-M3 + BM25) 결과는 **반드시 LLM 컨텍스트로 들어감**
- LLM의 모든 주장은 `chunk_id` 인용 강제 (모더레이터가 검증)

### 3. Step 1 데이터 의존성 (중요)
```
회로도 이미지 ─→ [Vision] Gemma 4 31B Multimodal
                    │ pinmap 추출 + 초기 분석
                    ↓
A Rule Engine ──┬─→ (errors+warnings 전체) ─→ C LLM Debate ←─ Vision 초기 분석
                └─→ (키워드) ──→ B RAG ──(Top-K 청크)─→ C LLM Debate
                                  ↑ Vision 분석도 쿼리 보강
                            사용자 prompt + pinmap ─────────────┘
```
**Vision → A → B → C 순차**. 이미지 없으면 CSV 직접 입력 → A부터 시작.
A의 출력은 B의 쿼리 보강과 C의 컨텍스트에 모두 사용.

### 4. 운영 모드 2가지
- `mode=fast`: Rule Engine만 실행, ERROR 즉시 반환 (CI/자동검증용)
- `mode=full` (기본): A→B→C 전체 실행, ERROR 있어도 LLM이 자연어 설명 작성

### 5. Step 2는 LLM 호출 금지
- CubeMX 자동화는 결정론적 영역. LLM 끼면 디버깅 지옥.
- `.ioc` 편집, CLI 실행, 스니펫 주입, 패키징 — 모두 스크립트로만.

### 6. Step 3는 Golden Module 우선
- 검증된 C/H 레퍼런스(`golden_modules/`)에서 **검색** → **사용자 pinmap에 적응** → **USER CODE 마커 사이 주입**
- LLM이 처음부터 코드를 새로 쓰지 않음. 적응 작업만.

---

## 디렉토리 구조와 책임

```
MotorDriveForge/
├── agent/
│   └── step1_review_agent.py      # ⭐ Step 1 핵심: A→B→C 오케스트레이션
├── backend/
│   └── main.py                     # FastAPI: /v1/review (mode 파라미터 추가 필요)
├── golden_modules/                 # ⭐ Step 3 RAG 소스 (검증된 C/H)
│   ├── dc_motor_pid.{c,h}
│   ├── multi_axis_sync.{c,h}
│   ├── bldc_6step_hall.{c,h}
│   └── fdcan_motor_cmd.{c,h}
├── scripts/                        # 오프라인 데이터 인제스천
│   ├── parse_pdfs.py
│   ├── chunk_docs.py
│   ├── embed_and_index.py
│   ├── build_bm25.py
│   ├── parse_cubemx_xml.py
│   ├── parse_opensource_code.py
│   ├── scrape_st_forum.py
│   └── scrape_ti_e2e.py            # 🆕 TODO
├── dataset/
│   ├── official_docs/              # ST PDFs (✅ 14건)
│   ├── official_docs/errata/       # 🆕 G4 errata 명시적 분리
│   ├── forum_qa/                   # ST + TI E2E
│   ├── opensource/                 # submodules
│   └── synthetic/                  # 🆕 합성 망가진 스키매틱
├── work/                           # 워크플로우 기획 문서
│   ├── step1_workflow/
│   └── step2_workflow/
├── ARCHITECTURE.md                 # ⭐ 시스템 다이어그램·모델·데이터
├── CLAUDE.md                       # 이 파일
└── todo.md                         # 작업 현황
```

---

## 모델 매핑 (어디에 무슨 모델 쓰는지)

| 위치 | 모델 | 비고 |
|---|---|---|
| Step 1 LLM Debate | Gemma 4 31B Dense (Q4_K_M) | 추론·자연어 파싱 |
| Step 3 Codegen | Gemma 4 26B MoE (Q8) | 1차 |
| Step 3 Codegen A/B | Qwen3-Coder 30B A3B | HAL 정확도로 비교 후 채택 |
| RAG dense | BAAI/bge-m3 | 다국어 |
| RAG sparse | rank_bm25 | TIM1_CH1N 같은 정확 매칭 |
| 페르소나 | (Gemma 4 31B + system prompt × 5) | 별도 가중치 없음 |

co-residency: 31B + 26B = ~42 GB < 128 GB ✓

---

## 5 페르소나 시스템 프롬프트 가이드

각 페르소나는 자기 도메인의 관점에서만 발언. 모더레이터가 통합.

1. **MCU/Periph Expert** — 핀 AF, 타이머/DMA/ADC 충돌, 클럭 트리, errata
2. **Motor Control Expert** — 상보 PWM, 데드타임, BRK, 엔코더/홀 신호 무결성
3. **Power/EMI Expert** — 디커플링, GND 플레인, 게이트 드라이버 부트스트랩, 감지 저항
4. **Safety/Failsafe Expert** — 비상정지, 와치독, OCP/OVP/UVLO, FDCAN 끊김 대응
5. **Moderator** — 충돌 의견 조정, 근거 없는 발언 기각, 최종 보고서 합의

**중요**: 각 페르소나 발언에 `chunk_id` 인용 강제. 모더레이터가 검증.

---

## 작업할 때 주의사항

### Do
- 변경 전 `ARCHITECTURE.md`와 `CLAUDE.md` 먼저 읽기
- Rule Engine 룰 추가 시 ERROR/WARNING 구분 명확히
- LLM 프롬프트 수정 시 chunk_id 인용 강제 패턴 유지
- Step 2 스크립트는 idempotent하게 (재실행 안전)
- Golden Module 추가 시 README에 사용 예시 함께
- 새로 만든 데이터 소스는 `dataset/` 하위 적절한 곳에

### Don't
- Step 2에 LLM 호출 추가하지 않음
- Rule Engine을 LLM으로 대체하지 않음
- ERROR로 분류해야 할 것을 WARNING으로 낮추지 않음
- 외부 API 호출 추가하지 않음 (오프라인 운영)
- pip install 시 requirements.txt 업데이트 누락하지 않음

---

## 개발 워크플로우

```bash
# 백엔드 개발 모드
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

# 프론트엔드
pip install -r frontend/requirements.txt
streamlit run frontend/app.py

# Qdrant 시작
docker run -d -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant

# Ollama 모델 확인
ollama list

# 단위 테스트 (TODO: pytest 셋업 필요)
pytest tests/

# RAG 인덱스 재빌드 (오프라인, 데이터 추가 시에만)
python scripts/parse_pdfs.py
python scripts/chunk_docs.py
python scripts/embed_and_index.py
python scripts/build_bm25.py
```

---

## 우선순위 작업 (ROI 순)

1. **Step 1 mode=fast / mode=full 분리** — 빠른 차단 vs 풀 리뷰
2. **TI E2E 포럼 크롤러** (`scripts/scrape_ti_e2e.py`) — 최고 ROI 데이터 소스
3. **G4 errata 명시적 인제스천** — `dataset/official_docs/errata/` 분리
4. **합성 망가진 스키매틱 파이프라인** (`scripts/mutate_evm.py`) — 12 mutation rules
5. **5 페르소나 프롬프트 + 모더레이터** 구현 — 현재 단일 LLM 호출이면 분리
6. **Qwen3-Coder vs Gemma 4 26B 벤치마크** (HAL 정확도)
7. **사내 리뷰 자동 아카이브** — 장기 해자

자세한 내용은 `tasks/` 하위 작업 명세 참조.

---

## 참고 문서

- `ARCHITECTURE.md` — 다이어그램, 모델/데이터 카탈로그
- `stm32_agent_plan.md` — 메인 설계 계획
- `stm32_agent_appendex.md` — Appendix
- `todo.md` — 진행 현황
- `work/step1_workflow/` — Step 1 4단계 워크플로우 상세
- `work/step2_workflow/` — Step 2 4단계 워크플로우 상세
