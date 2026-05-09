# Task 01 — Step 1 운영 모드 분리 (fast / full)

**우선순위**: 🔴 최우선 (다른 모든 작업의 전제)
**예상 소요**: 0.5 ~ 1일
**관련 파일**: `agent/step1_review_agent.py`, `backend/main.py`

---

## 배경

현재 Step 1은 단일 흐름으로 동작하고 `errors > 0`이면 HTTP 403을 반환합니다.
하지만 ERROR가 났어도 신입 설계자에겐 **"왜 문제고 어떻게 고치라"는 자연어 설명**이 더 가치 있습니다.

## 목표

Step 1을 두 모드로 분리:

| 모드 | 동작 | 사용처 |
|---|---|---|
| `fast` | Rule Engine만 실행, ERROR 즉시 반환, LLM 호출 안 함 | CI 자동검증, 게이트키퍼 |
| `full` (기본) | A→B→C 전체 실행, ERROR 있어도 LLM 자연어 설명 작성 | 사람이 보는 리뷰 |

## 구현 단계

### 1. API 스펙 변경

`backend/main.py`의 `POST /v1/review`에 `mode` 쿼리 파라미터 추가:

```python
@app.post("/v1/review")
async def review(
    chip: str = Form(...),
    prompt: str = Form(...),
    csv_file: UploadFile = File(...),
    mode: Literal["fast", "full"] = "full",
):
    ...
```

### 2. 에이전트 분기

`agent/step1_review_agent.py`:

```python
def review(pinmap, prompt, mode: str = "full") -> ReviewReport:
    rule_result = rule_engine.run(pinmap)

    if mode == "fast":
        return ReviewReport(
            errors=rule_result.errors,
            warnings=rule_result.warnings,
            llm_explanation=None,
        )

    # full mode: 항상 RAG + LLM 실행 (errors 있어도)
    rag_chunks = hybrid_rag.retrieve(
        query=build_query(prompt, pinmap, rule_result),
        top_k=12,
    )
    debate_result = persona_debate.run(
        prompt=prompt,
        pinmap=pinmap,
        rule_result=rule_result,
        chunks=rag_chunks,
    )
    return ReviewReport(
        errors=rule_result.errors,
        warnings=rule_result.warnings + debate_result.advisory_warnings,
        suggestions=debate_result.suggestions,
        llm_explanation=debate_result.report,
        evidence=debate_result.evidence,
    )
```

### 3. HTTP 상태 코드 정책

| mode | errors | HTTP | body |
|---|---|---|---|
| fast | 0 | 200 | rule_result만 |
| fast | >0 | 403 | rule_result만 |
| full | 0 | 200 | 전체 리포트 |
| full | >0 | 200 | 전체 리포트 (LLM 설명 포함) — **403 아님** |

full 모드에서 errors가 있어도 200을 반환하는 게 핵심. body의 `errors[]` 길이로 클라이언트가 판단.

### 4. Streamlit UI 토글 추가

`frontend/app.py`에 라디오 버튼:
- ⚡ Fast review (CI용)
- 🔍 Full review (기본, 권장)

## 테스트

```python
# tests/test_review_modes.py
def test_fast_mode_skips_llm():
    pinmap = make_invalid_pinmap()  # PWM 충돌 등
    report = review(pinmap, "test", mode="fast")
    assert report.llm_explanation is None
    assert len(report.errors) > 0

def test_full_mode_includes_explanation_on_error():
    pinmap = make_invalid_pinmap()
    report = review(pinmap, "test", mode="full")
    assert report.llm_explanation is not None
    assert "TIM1" in report.llm_explanation  # 자연어 설명 포함
```

## 완료 기준

- [ ] API에 `mode` 파라미터 추가
- [ ] 에이전트가 모드별 분기 처리
- [ ] HTTP 상태 코드 정책 적용
- [ ] Streamlit에 모드 선택 UI
- [ ] 테스트 2개 이상 통과
- [ ] CLAUDE.md "운영 모드 2가지" 섹션 코드와 일치 확인
