# Task 02 — TI E2E 모터드라이버 포럼 크롤러

**우선순위**: 🔴 최우선 (가장 ROI 높은 데이터 소스)
**예상 소요**: 1 ~ 2일
**관련 파일**: `scripts/scrape_ti_e2e.py` (신규), `dataset/forum_qa/ti_e2e/`

---

## 배경

ST 공식 포럼은 회로 검토 사례가 거의 없습니다. 반면 **TI E2E의 motor-drivers 포럼**은:

1. **공식 "How to Conduct a BLDC Schematic Review and Debug" 자료** — 일부러 흔한 실수를 박아넣은 mock 스키매틱(DRV8353RH) PDF + 코멘트 단 reviewed 버전 PDF 제공. 사실상 **before/after 라벨링된 골드 데이터셋**.
2. **실제 디버그 스레드 수백 건** — TI 엔지니어 답변 포함. 사용자 질문 + 실수 진단 + 수정안이 한 묶음이라 instruction-tuning 포맷 그대로 활용 가능.

이게 우리 RAG의 핵심 자산이 되어야 합니다.

## 목표

`e2e.ti.com/support/motor-drivers-group` 카테고리 크롤링.
- 스레드 메타데이터(제목, 작성자, 날짜, 태그)
- 본문(HTML→텍스트)
- 첨부파일(PDF, 이미지) 다운로드
- 답변 트리 (특히 TI verified answer 우선)

저장 형식: JSONL, 한 줄에 한 스레드.

## 구현 가이드

### 1. 기존 패턴 따르기

`scripts/scrape_st_forum.py`와 동일한 구조로 작성. 공통 유틸 추출 고려.

### 2. 스키마

```python
{
  "source": "ti_e2e_motor_drivers",
  "thread_id": "1039040",
  "url": "https://e2e.ti.com/support/motor-drivers-group/...",
  "title": "...",
  "tags": ["DRV8353", "BLDC", "schematic"],
  "created_at": "2021-09-23T...",
  "question": {
    "author": "...",
    "body_text": "...",
    "body_html_clean": "...",
    "attachments": [
      {"filename": "schematic.pdf", "local_path": "dataset/forum_qa/ti_e2e/attachments/..."}
    ]
  },
  "replies": [
    {
      "author": "...",
      "is_ti_verified": true,
      "body_text": "...",
      "is_accepted_answer": true,
      "created_at": "..."
    }
  ],
  "ic_part_number": "DRV8353RH",   // 추출 가능하면
  "schematic_review_type": "mock_with_mistakes" | "real_debug" | "design_question"
}
```

### 3. 우선 수집 대상 (FAQ 스레드)

이 스레드들이 가장 가치 높음 — 명시적으로 학습 자료로 큐레이팅된 것:

```python
PRIORITY_THREADS = [
    "1039040",  # How to Conduct a BLDC Schematic Review and Debug (FAQ)
    # ... 다른 FAQ들 추가. 카테고리 검색에서 [FAQ] 태그 우선
]
```

### 4. Rate limiting과 robots.txt

- robots.txt 확인 (`https://e2e.ti.com/robots.txt`)
- 요청 간격 최소 2초
- User-Agent 명확히 (예: `MotorDriveForge-Research-Bot/0.1 (contact: ...)`)
- 한 번에 다 안 받고 점진적으로 (resume 가능하게)

### 5. CLI

```bash
# 카테고리 전체 크롤
python scripts/scrape_ti_e2e.py \
    --category motor-drivers-group \
    --max-threads 500 \
    --out dataset/forum_qa/ti_e2e/ti_e2e_motor.jsonl \
    --download-attachments

# 특정 IC 부품 필터
python scripts/scrape_ti_e2e.py \
    --filter-ic DRV83 \
    --max-threads 200

# 이어받기 (이미 받은 thread_id 스킵)
python scripts/scrape_ti_e2e.py --resume
```

### 6. 청킹 시 메타데이터 활용

`scripts/chunk_docs.py` 수정 시 TI E2E 청크에는:
- `source: "ti_e2e_motor"`
- `is_ti_verified: true/false` — 검색 시 TI 공식 답변 부스팅
- `ic_part_number` — 사용자가 같은 IC 쓸 때 우선 노출

## 첨부 PDF 처리

mock/reviewed 스키매틱 PDF는 가장 가치 높은 자산이라 **별도 파이프라인**:
- `dataset/forum_qa/ti_e2e/golden_pairs/` 에 mock + reviewed 페어로 저장
- 향후 합성 데이터 생성(Task 04)의 시드로 사용

## 완료 기준

- [ ] `scripts/scrape_ti_e2e.py` 작성
- [ ] FAQ 스레드 최소 10건 + 일반 스레드 200건 이상 수집
- [ ] mock/reviewed PDF 페어 별도 분리
- [ ] JSONL 스키마 검증 통과
- [ ] `scripts/chunk_docs.py`가 TI E2E 청크 처리
- [ ] `scripts/embed_and_index.py` 재실행 시 정상 인덱싱
- [ ] robots.txt 준수, rate limit 적용

## 관련 자료

- 시작점: https://e2e.ti.com/support/motor-drivers-group/motor-drivers/f/motor-drivers-forum/1039040/faq-how-to-conduct-a-bldc-schematic-review-and-debug
- robots.txt: https://e2e.ti.com/robots.txt
