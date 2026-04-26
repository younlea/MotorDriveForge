# Step 2-1: .ioc Template Editing Workflow

## 개요
Step 1에서 합격 판정을 받은 핀맵 JSON 데이터를 바탕으로 STM32G4 깡통 템플릿 `.ioc` 파일의 파라미터를 파이썬(또는 LLM)이 정규표현식으로 교체/주입하는 워크플로우.

## 진행 절차
1. **Base 템플릿 로드**: STM32G4 빈 프로젝트 `.ioc` 파일 열기.
2. **파라미터 변환 매핑**: `{"PA8": "TIM1_CH1"}` 등의 JSON 을 `.ioc` 포맷 규격(`PA8.Signal=TIM1_CH1`, `PA8.Locked=true`) 문자열로 치환.
3. **LLM 문맥 주입 (선택적)**: 단순 핀맵 외에 통신 비트레이트 등의 설정은 LLM이 판단하여 해당 `.ioc` 구문을 추가.
4. **임시 파일 저장**.

## 사용 스킬
- `skills/skill_ioc_text_modifier.py` (정규표현식 기반 IOC 문자열 배치 전문 스킬)
