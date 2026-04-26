STM32G4 Agent — Claude Code 컨텍스트

이 파일은 Claude Code가 세션 시작 시 자동으로 읽는 프로젝트 컨텍스트입니다.

프로젝트 개요

STM32G4 전용 사내 Agent 시스템. HW 개발자가 회로도를 올리면 → 핀 검증 → HAL 코드 생성 → 알고리즘 통합까지 자동화.

운영 환경: NVIDIA DGX Spark 128GB, 외부망 차단 완전 로컬.
타겟 칩: STM32G4 계열만 (G431, G474 등).
기존 자산: 최적화된 BLDC FOC / 전류제어 알고리즘 코드 보유 → Golden Module 등록 예정.

3-Step 파이프라인 (확정된 아키텍처)

[입력] 핀맵 CSV  +  자연어 프롬프트
         ↓
[STEP 1] 핀 검증 Agent
         - 모델: Gemma-4-31B-It (Q4_K_M, ~40GB, Ollama)
         - 자연어 프롬프트 파싱 → 요구사항 구조화
         - CubeMX XML DB + RAG + 규칙 엔진으로 핀 AF 검증
         - 출력: 검증 리포트 + 확정 핀 JSON
         ↓
[검증 게이트]  errors[] == [] ?
         - FAIL → 완전 차단, 오류 리포트 반환, 회로도 수정 요구
         - warnings → 표시 후 통과 허용
         ↓ PASS only
[STEP 2] CubeMX 자동화 (LLM 없음)
         - 핀 JSON → .ioc 파일 생성 (Python 스크립트)
         - STM32CubeMX -q script.txt 실행
         - 출력: HAL 초기화 코드 (main.c, tim.c, adc.c, fdcan.c, ...)
         ↓
[STEP 3] 알고리즘 통합 Agent
         - 모델: Gemma-4-31B-It (Q8, ~32GB, Ollama)
         - Golden Module RAG → 요구사항에 맞는 모듈 선택
         - USER CODE BEGIN/END 영역에 FOC 알고리즘 삽입
         - 출력: 완성 펌웨어 (.zip 다운로드)
메모리: 40GB + 32GB = 72GB → 128GB에서 두 모델 동시 로드 가능.

Step 1 입력 방식 (2026-04-10 확정)

입력 1: 핀맵 CSV (chip, pin, function, label 컬럼)
입력 2: 자연어 프롬프트 (HW 개발자가 자유 작성, JSON 아님)
프롬프트 예시:

STM32G474RET6 칩을 쓸 거고, 외부 크리스탈 24MHz / 시스템 170MHz야.
BLDC 모터 1개를 FOC로 제어할 건데 증분형 엔코더(A/B/Z)로 각도 읽고,
3상 6채널 PWM으로 인버터 구동해. 데드타임 500ns, 전류는 내부 OPAMP.
통신은 FDCAN 1Mbps 쓰고, 파라미터 저장용으로 SPI EEPROM도 연결할 거야.
프롬프트 포함 권장 항목: 칩명·클럭 / 모터종류·제어방식 / 피드백센서 / PWM채널·데드타임 / 통신프로토콜 / 외부장치

주요 파일

파일	설명
stm32_agent_plan.md	메인 설계 계획 문서 (6차 수정)
stm32_agent_appendix.md	데이터 수집 / 학습 방법 / 웹 개발 프로세스
generate_ppt.py	PPT 자동 생성 스크립트 (python-pptx)
STM32G4_Agent_Plan.pptx	최종 PPT (총 36슬라이드)
PPT 슬라이드 구조 (총 36슬라이드)

범위	내용
1	표지
2~13	개요 / 아키텍처 / 알고리즘 / RAG / 모델 / 개발 단계
14	사용 가이드 전체 흐름 (CSV + 자연어 프롬프트 예시)
15	Step 1 가이드 + 프롬프트 작성 체크리스트
16~18	Step 2/3 가이드, 결과 확인
19~21	GUI 화면 예시 (메인 대시보드 / Step 1 UI / Step 2·3 UI)
22	기대 효과 & 다음 단계
23~27	Appendix A: 학습 데이터 수집 가이드
28~32	Appendix B: QLoRA 학습 방법 상세
33~36	Appendix C: 웹 개발 프로세스
PPT 재생성 방법

"C:/Users/usejen_id/AppData/Local/Programs/Python/Python313/python.exe" "generate_ppt.py"
python-pptx 1.0.2 이미 설치됨.
PPT 수정 시 주의사항

footer() 함수의 total=36 → 슬라이드 추가/삭제 시 반드시 맞춰줄 것
slide_header() 내부 순서: 흰 배경 → 헤더바 → 텍스트 (순서 바꾸면 텍스트가 헤더에 가려짐)
슬라이드 삽입 시 뒤에 오는 모든 footer(s, N) 번호 일괄 업데이트 필요
인프라 & 기술 스택

구분	선택	비고
모델 서빙	Ollama + GGUF	두 모델 동시 로드
벡터 DB	Qdrant (Docker)	hybrid search
임베딩	BAAI/bge-m3 + BM25 + cross-encoder	
파인튜닝	Unsloth QLoRA	Step1: r=32 / Step3: r=64
백엔드	FastAPI	검증 게이트: errors[]>0 → HTTP 403
프론트 MVP	Streamlit	http://dgx-spark:8501
프론트 Production	React 18 + TypeScript + Tailwind + shadcn/ui	
배포	Docker Compose	nginx + frontend + backend + ollama + qdrant
소통 원칙

간결하게 답변. 장황한 설명 불필요.
설계 변경 시 stm32_agent_plan.md와 PPT 동시 업데이트.
구체적 수치/경로/예시 포함 (두리뭉실한 가이드 지양).
HW 개발자 UX 우선 — 복잡한 포맷보다 자연어 입력 선호.