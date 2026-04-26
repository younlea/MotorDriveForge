# Step 2-3: main.c Code Snippet Injection Workflow

## 개요
빈 `main.c`가 생성되면, 모터 돌기 바로 전 필수 함수 호출문을 LLM이 작성해 `/* USER CODE BEGIN 2 */` 블록 안에 주입합니다.

## 진행 절차
1. **스니펫 생성 (LLM)**: "이 프로젝트는 2축 엔코더 모터야. 초기 구동 함수 스니펫 짜줘" -> `HAL_TIM_Encoder_Start(&htim1, TIM_CHANNEL_ALL);` 등 3~4줄 도출.
2. **파일 AST/Regex 스플릿**: `main.c` 텍스트를 읽어 `USER CODE BEGIN 2` 마커를 검색.
3. **스니펫 접합**: 확보된 마커 사이에 제공받은 문자열 스니펫을 밀어넣고 저장.

## 사용 스킬
- `skills/skill_inject_c_code.py` (문자열 내 안전 구역 타겟팅 주입 스킬)
