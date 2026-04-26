# Step 1-1: Data Collection Workflow

## 개요
ST 포럼의 질문/답변 데이터(에러 사례)와 ST 공식 하드웨어 가이드라인 PDF를 텍스트 형태로 수집하는 첫 번째 워크플로우입니다.

## 진행 절차
1. **수동 다운로드 자료 배치**
   - 사용자 주도하에 에이전트 기획서에 명시된 ST 공식 문서(AN5031, AN5306, AN5348 등) PDF를 `dataset/official_docs/` 에 저장합니다.
2. **포럼 데이터 스크래핑 (자동화)**
   - `scripts/scrape_st_forum.py` (또는 스킬) 을 확장하여 ST 커뮤니티의 Motor Control Hardware 관련 질의응답을 스크래핑합니다.
3. **텍스트 정제**
   - PDF의 텍스트와 스크래핑된 HTML을 순수 Text 포맷(Markdown)으로 정제하여 `dataset/cleaned_text/` 에 저장합니다.

## 사용 스킬
- `skills/skill_st_forum_scraper.py` (웹 크롤링 전문 스킬)
