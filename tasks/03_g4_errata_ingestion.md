# Task 03 — STM32G4 Errata 명시적 인제스천

**우선순위**: 🟡 높음
**예상 소요**: 0.5일
**관련 파일**: `dataset/official_docs/errata/`, `scripts/parse_pdfs.py`, `scripts/chunk_docs.py`

---

## 배경

ST 디바이스 errata 시트에는 **실리콘 레벨 버그**가 정리되어 있습니다. 신입 설계자는 절대 알 수 없는 내용이라 회로 검토에서 가장 높은 가치를 가집니다.

예: G4 시리즈 ADC 한계, VREFINT 보정값 부정확성, 특정 페리페럴의 알려진 동작 이슈 등.

현재 `dataset/official_docs/`에 일반 RM/DS와 섞여 있어 검색 시 우선순위 부스팅이 안 됩니다. 분리해서 메타데이터로 표시 필요.

## 목표

1. G4 패밀리 errata PDF를 `dataset/official_docs/errata/` 하위로 분리
2. 청크 메타데이터에 `is_errata: true` 추가
3. RAG 검색 시 errata 청크는 점수 부스팅 (회로 검토 컨텍스트에서)
4. LLM 프롬프트에 "errata 청크가 있으면 반드시 언급" 지시

## 수집 대상

- ES0430 (STM32G431/441 errata)
- STM32G471 errata
- STM32G474/G484 errata
- STM32G491/G4A1 errata
- 관련 Cortex-M4 코어 errata (있으면)

ST 사이트에서 PDF 다운로드 후 `dataset/official_docs/errata/`에 저장.

## 구현

### 1. 디렉토리 구조

```
dataset/official_docs/
├── reference_manuals/
├── datasheets/
├── application_notes/
└── errata/                    # 🆕
    ├── ES0430_G431_G441.pdf
    ├── ES_G471.pdf
    ├── ES_G474_G484.pdf
    └── ES_G491_G4A1.pdf
```

### 2. 청킹 시 메타데이터

`scripts/chunk_docs.py` 수정:

```python
def determine_doc_type(pdf_path: Path) -> str:
    if "errata" in pdf_path.parts:
        return "errata"
    if "reference_manual" in str(pdf_path).lower() or pdf_path.name.startswith("RM"):
        return "reference_manual"
    if "AN" in pdf_path.name:
        return "application_note"
    return "datasheet"

# 청크 메타데이터
chunk_meta = {
    "source": "st_official",
    "doc_type": determine_doc_type(pdf_path),
    "is_errata": pdf_path.parent.name == "errata",
    "chip_family": extract_chip_family(pdf_path.name),  # "G431", "G474", ...
    ...
}
```

### 3. 검색 부스팅

`agent/step1_review_agent.py`의 RAG 호출에서:

```python
def retrieve_with_errata_boost(query, top_k=12, chip=None):
    # 일반 검색
    base_results = hybrid_rag.search(query, top_k=top_k * 2)

    # errata 청크 부스트 (해당 칩 패밀리만)
    boosted = []
    for chunk in base_results:
        score = chunk.score
        if chunk.metadata.get("is_errata"):
            if chip and chunk.metadata.get("chip_family") in chip:
                score *= 1.5   # 해당 칩 errata 강하게 부스트
            else:
                score *= 1.2   # 일반 errata 약하게
        boosted.append((chunk, score))

    boosted.sort(key=lambda x: -x[1])
    return [c for c, _ in boosted[:top_k]]
```

### 4. LLM 프롬프트 추가 지시

`agent/step1_review_agent.py`의 LLM 시스템 프롬프트에:

```
검색된 컨텍스트 청크 중 metadata.is_errata == true인 것이 있으면,
리뷰 리포트에 별도 "Silicon Errata Considerations" 섹션을 만들어
관련 칩의 알려진 한계를 반드시 명시하라. errata 인용은 chunk_id와
함께 정확한 limitation 번호(예: 2.1.3)를 표시하라.
```

## 완료 기준

- [ ] G4 패밀리 errata PDF 4건 이상 `dataset/official_docs/errata/`에 수집
- [ ] `scripts/chunk_docs.py`가 errata 메타데이터 부착
- [ ] Qdrant 재인덱싱 후 errata 청크 검색 가능
- [ ] RAG 검색 부스팅 로직 구현
- [ ] 테스트: G474 핀맵 입력 시 review_report에 G474 errata 섹션 자동 등장

## 관련 자료

- ST 검색: https://www.st.com → "STM32G4 errata"
- 예시: https://www.st.com/resource/en/errata_sheet/es0430...pdf
