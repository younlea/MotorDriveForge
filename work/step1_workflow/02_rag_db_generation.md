# Step 1-2: RAG Vector DB Generation Workflow

## 개요
수집된 ST 문서를 기반으로 텍스트를 청킹(Chunking)하고, `BAAI/bge-m3` 등 다국어 임베딩 모델을 사용하여 벡터 DB를 생성하는 워크플로우.

## 진행 절차
1. **텍스트 청킹 (Chunking)**
   - 문맥이 끊기지 않는 단위(Paragraph, Header 단위)로 500~1000 코인 단위로 청킹.
2. **임베딩 및 Qdrant 적재**
   - 로컬 구동 가능한 오픈소스 임베딩 모델 로드.
   - 문장 간 유사도 검색용 **Vector 인덱스(Qdrant)**와 키워드(예: FDCAN, TIM1) 매칭용 **BM25 역색인(Sparse)** 하이브리드 세팅.

## 사용 스킬
- `skills/skill_rag_indexer.py` (토크나이징 및 벡터DB 적재 스킬)
