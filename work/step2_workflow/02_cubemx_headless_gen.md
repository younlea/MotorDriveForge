# Step 2-2: STM32CubeMX Headless CLI Workflow

## 개요
업데이트된 임시 `.ioc` 파일을 읽어들여, ST 엔진을 통해 완벽하게 검증된 문법의 C 프로젝트 코드를 자동 추출합니다.

## 진행 절차
1. **Script 생성**: `generate.script` 파일 속에 "프로젝트 로드 -> 코드 생성 -> 종료" 매크로 커멘드 작성.
2. **명령어 호출**: `subprocess` 모듈을 이용해 `java -jar STM32CubeMX -q generate.script` 커스텀 스크립트 실행 (백그라운드).
3. **결과 검증**: 생성된 `Inc/` 와 `Src/` 그리고 `main.c` 파일 존재 여부 Validation.

## 사용 스킬
- `skills/skill_cubemx_headless_runner.sh` (서버 환경 내 Java CLI 연동 쉘 스크립트 스킬)
