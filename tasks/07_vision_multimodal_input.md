# Task 07 — Vision 멀티모달 입력 (회로도 이미지 → 핀맵 자동 추출)

**우선순위**: 🟢 완료 (2026-05-14)
**소요**: 1일
**관련 파일**: `agent/step1_review_agent.py`, `backend/main.py`, `frontend/app.py`, `ARCHITECTURE.md`, `CLAUDE.md`

---

## 배경

초기 설계에서 "회로도 이미지 입력"은 Phase 2 계획이었으나, Gemma 4 31B를 Ollama로 실제
운영해보니 멀티모달(이미지) 입력을 지원하고 핀맵 추출 + 기본 분석 품질이 즉시 활용
가능한 수준임을 확인. Phase 2를 당겨서 현재 Sprint에 반영.

## 목표

사용자가 회로도 이미지 + 자연어 프롬프트를 입력하면:

1. **Gemma 4 31B (Vision)** 이 이미지를 분석해 pinmap CSV 자동 추출 + 초기 설계 분석 생성
2. 추출된 pinmap을 **Rule Engine** 으로 결정론적 검증
3. Rule Engine 키워드 + Vision 초기 분석으로 **RAG 쿼리 보강**
4. Vision 분석 + RAG 청크를 컨텍스트로 **LLM Persona Debate** 실행
5. `vision_analysis` 필드 포함 리뷰 리포트 반환

## 새 흐름

```
회로도 이미지 + 프롬프트
    → [Vision]  Gemma 4 31B Multimodal — 핀맵 자동 추출 + 초기 분석
    → [A]       Rule Engine (결정론)
    → [B]       Hybrid RAG (Rule Engine 키워드 + Vision 분석 보강)
    → [C]       LLM Persona Debate (Vision 분석 컨텍스트 포함)
    → Review Report (vision_analysis 필드 포함)
```

이미지 없으면 CSV 직접 입력 → A부터 시작 (하위 호환 유지).

## 구현 내용

### agent/step1_review_agent.py

| 항목 | 변경 |
|---|---|
| `ReviewRequest.pinmap_csv` | Optional(기본 "") — 이미지 제공 시 자동 생성 |
| `ReviewRequest.schematic_image_b64` | 신규 Optional — 회로도 이미지 base64 |
| `ReviewReport.vision_analysis` | 신규 — Vision 초기 분석 텍스트 |
| `_ollama_multimodal()` | 신규 — Ollama `/api/generate` + `images` 배열 |
| `_vision_extract_pinmap()` | 신규 — 이미지 → `(pinmap_csv, vision_analysis)` |
| `_available_model()` | Gemma 4 31B 우선 탐색으로 변경 |
| `run()` | Vision → Rule Engine → RAG(보강) → LLM 순차 흐름 |

### backend/main.py

- `POST /v1/review`: `schematic_image` Optional UploadFile 추가
- 이미지를 base64로 변환 후 `ReviewRequest`에 전달
- 이미지도 CSV도 없을 때만 HTTP 422 반환

### frontend/app.py

- 이미지 업로더를 주 입력으로 배치 (파일 업로드 → 미리보기)
- CSV 입력은 보조 (expandable, 이미지 있으면 기본 접힘)
- `vision_analysis` 결과 섹션 표시
- 이미지 제공 여부에 따라 버튼 레이블·spinner 메시지 자동 변경

## Ollama Vision API

```python
payload = {
    "model": "gemma4:31b",
    "prompt": "...",
    "images": ["<base64_string>"],   # Ollama generate endpoint
    "stream": False,
    "options": {"temperature": 0.1, "num_predict": 3072},
}
requests.post(f"{ollama_url}/api/generate", json=payload, timeout=180)
```

Gemma 4 31B Vision과 LLM Debate가 동일 Ollama 인스턴스 공유 → 추가 메모리 없음.

## 검증 항목

- [ ] 회로도 이미지 업로드 → Vision 분석 결과 UI 표시 확인
- [ ] Vision 추출 pinmap → Rule Engine 정상 통과 확인
- [ ] CSV 직접 입력 경로 하위 호환 동작 확인
- [ ] 이미지 + CSV 동시 입력 시 CSV 우선 처리 확인
- [ ] Vision 실패(이미지 불명확)시 에러 메시지 확인

## 의존성

없음 (Gemma 4 31B가 이미 Ollama에 로드된 상태 전제).
