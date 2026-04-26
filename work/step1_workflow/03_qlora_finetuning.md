# Step 1-3: Base LLM QLoRA Fine-tuning Workflow

## 개요
RAG의 결과물과 프롬프트를 취합해 "특정 JSON 구조"로만 답변을 반환하도록 Base 모델(Gemma-4-31B/32B)을 4-bit 환경에서 도메인 튜닝(Fine-Tuning) 합니다.

## 진행 절차
1. **JSONL 학습 데이터 조립**
   - Rule Extractor를 거쳐 생성된 약 500개의 논리적 쌍을 `{"instruction": "x", "input": "y", "output": "z"}` 형태로 구성.
2. **unsloth 환경 기동 (DGX Spark)**
   - 128GB 메모리 한계 내에서 4-bit 양자화된 베이스 모델을 메모리에 로드.
3. **훈련 및 어댑터 도출**
   - 약 3 epoch 훈련 수행 뒤, LoRA Adapter Weights를 `.safetensors` 나 GGML 등 서비스 환경에 맞게 익스포트.

## 사용 스킬
- `skills/skill_build_jsonl_dataset.py` (데이터 컨버터 스킬)
