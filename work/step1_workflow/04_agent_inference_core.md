# Step 1-4: Agent Evaluation Core Workflow

## 개요
최종적으로 사용자 웹으로부터 API 콜을 받아 핀맵 CSV와 프롬프트를 해석하고 결함 레포트를 리턴하는 운영 시스템입니다.

## 진행 절차
1. **Web Request 수신** -> 핀맵 데이터 (CSV 파일 인메모리).
2. **CSV to 핀맵 JSON 변환** -> 스킬 활용하여 기계가 읽을 수 있게 변환.
3. **RAG 질의 (BM25 + Semantic)** -> 관련 설계 규칙 수집.
4. **추론 전송** -> 프롬프트 + JSON + Rules 를 LLM 에이전트에 전송.
5. **결과 JSON 응답** -> 에러/경고 판별 후 웹 뷰어로 Response 반환.

## 사용 스킬
- `skills/skill_parse_pinmap_csv.py` (모든 하드웨어 CAD 툴의 CSV 넷리스트를 단일 형태의 JSON 구조로 통합하는 스킬)
