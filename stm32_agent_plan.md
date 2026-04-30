STM32 회로도 검증 & 펌웨어 생성 Agent 설계 계획

최초 작성: 2026-04-10 마지막 수정: 2026-04-27 (7차 — 모델 Gemma-4 변경, Step 1 HW Expert Agent 방향 전환, 실제 데이터셋 현황 반영)
서버 사양

항목	내용
장비	NVIDIA DGX Spark (GB10 Grace Blackwell Superchip)
통합 메모리	128GB LPDDR5X (CPU + GPU 공유, unified memory)
AI 성능	~1 PFLOPS (FP4)
특징	외부망 차단 완전 로컬 운영 가능, 31B급 모델 여유롭게 실행
확장	2대 NVLink 연결 시 256GB → 추가 확장 가능
unified memory 구조이므로 "VRAM 부족" 문제 없음. 두 모델 동시 로드도 가능.
개요

HW 개발자가 만든 회로도를 입력받아 3단계 파이프라인으로 처리:

Step	역할	담당
Step 1. HW 설계 검증 Agent	회로도 핀 연결의 인프라·모터 논리·페리페럴 제약 검증 → 검증 완료 핀 JSON 확정	LLM + 규칙 엔진 + RAG
Step 2. CubeMX 자동화	검증된 핀 JSON → .ioc 생성 → CubeMX CLI → HAL 초기화 코드 + 스니펫 주입	4단계 워크플로우 (LLM 최소 개입)
Step 3. 알고리즘 통합 Agent	CubeMX 생성 코드 + Golden Module 선택·통합 → 완성 펌웨어	LLM
핵심 설계 원칙: LLM은 HAL API를 직접 생성하지 않음. CubeMX가 보장하는 정확한 초기화 코드 위에 LLM이 알고리즘 레이어만 조립.
사내 전용 운영 (외부 데이터/API 사용 불가)

타겟 칩: STM32G4 계열 (모터 제어 최적화 MCU)
보유 자산: 오픈소스 FOC 알고리즘 코드 (Arduino-FOC, MESC, VESC, ODrive, moteus 등) → Golden Module 등록
향후 사내 최적화 코드 확보 시 교체 예정.

STM32G4 — 모터 제어 특화 하드웨어

G4는 ST가 모터 제어 전용으로 설계한 시리즈. 일반 F4와 달리 아래 가속기가 내장되어 있어 코드 생성 시 이를 활용하는 것이 핵심.
주변장치	역할	코드 생성 시 고려사항
TIM1 / TIM8 / TIM20	3상 보완 PWM + 데드타임 삽입	모터 드라이버 핵심 타이머
CORDIC	하드웨어 sin/cos 연산	FOC 좌표 변환 가속 (Clarke/Park)
FMAC	하드웨어 FIR/IIR 필터	전류 제어 루프 필터
OPAMP (내장)	전류 감지 증폭	외부 OP-AMP 불필요
ADC (12bit, 동기 샘플링)	전류/전압 측정	Injected ch. + TIM 트리거 동기화
FDCAN	CAN-FD 통신	classic CAN과 다름, 최대 8Mbps
HRTIM	고분해능 PWM	전력 변환 / DC-DC 용도
BLDC 제어 알고리즘 구조

전류제어와 FOC는 독립 모듈로 존재하며, 제어 루프 캐스케이드 구조로 조합됨. 오픈소스 레퍼런스 코드가 이 레이어를 담당 → 학습 데이터의 핵심 자산.
제어 루프 캐스케이드

┌─────────────────────────────────────────────────────────┐
│  외부 루프 (저속, ~1kHz)                                  │
│                                                         │
│  [위치 지령] → [Position PI] → [속도 지령]               │
│  [속도 지령] → [Speed PI]    → [Iq 지령 (토크)]          │
└──────────────────────────┬──────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│  전류 제어 루프 (고속, PWM 주파수 ~20kHz)                 │
│                                                         │
│  [Ia, Ib, Ic] ← ADC Injected (TIM1 트리거 동기화)        │
│       ↓                                                 │
│  [Clarke 변환]  abc → αβ                                │
│       ↓                                                 │
│  [Park 변환]    αβ  → dq    ← CORDIC sin/cos(θ) 가속    │
│       ↓                                                 │
│  ┌─── Id PI 제어기 ──→ Vd  (자속 축 전류 제어)            │
│  └─── Iq PI 제어기 ──→ Vq  (토크 축 전류 제어)  ← FMAC  │
│                           ↓                             │
│  [역 Park 변환] dq  → αβ   ← CORDIC 재사용              │
│       ↓                                                 │
│  [SVPWM]        αβ  → Ta, Tb, Tc (듀티비)               │
│       ↓                                                 │
│  [TIM1/TIM8 보완 PWM + 데드타임]                         │
└─────────────────────────────────────────────────────────┘
                           ↑
                    [θ 로터 각도]
                    ├─ 엔코더   : TIM encoder mode 카운터
                    ├─ Hall 센서: 섹터 → 각도 추정
                    └─ Sensorless: SMO / HFI 옵저버
알고리즘 모듈 목록 (오픈소스 기반 매핑)

모듈 파일	역할	G4 가속기 활용	데이터 소스
foc_clarke.c	Clarke 변환 (abc→αβ)	— (단순 연산)	Arduino-FOC / MESC
foc_park.c	Park 변환 (αβ→dq)	CORDIC sin/cos	Arduino-FOC / MESC
foc_inv_park.c	역 Park 변환 (dq→αβ)	CORDIC 재사용	Arduino-FOC / MESC
foc_svpwm.c	Space Vector PWM	—	VESC / MESC
foc_current_pi.c	Id/Iq PI 제어기 + Anti-windup	FMAC 옵션	moteus / MESC
foc_speed_pi.c	속도 PI 제어기	—	Arduino-FOC
foc_position_pi.c	위치 PI 제어기 (선택)	—	moteus
foc_angle_encoder.c	엔코더 각도/속도 추정	TIM encoder mode	Arduino-FOC
foc_angle_hall.c	Hall 센서 섹터 → 각도	—	VESC
foc_angle_smo.c	Sensorless SMO 옵저버	—	MESC
foc_current_sense.c	ADC 전류 샘플링 + 옵셋 보정	Internal OPAMP	flatmcu
bldc_6step.c	6-step 전환 (Hall 기반)	—	golden_modules
오픈소스 코드를 Golden Module 형태로 가공하여 등록.
향후 사내 최적화 코드 확보 시 교체 예정.

전류제어 단독 모드 vs FOC 전체

[전류제어 단독]                    [FOC 전체]
  Ia, Ib 측정                        Ia, Ib, Ic 측정
       ↓                                   ↓
  Peak / Average 전류 제어          Clarke → Park → dq PI
       ↓                                   ↓
  PWM 듀티 직접 조정                역Park → SVPWM
  
  용도: 단순 토크 제한,              용도: 고성능 속도/위치 제어,
        과전류 보호                        효율 최적화
전체 아키텍처 (3-Step Pipeline)

┌─────────────────────────────────────────────────────────────────────┐
│  STEP 1 : HW 설계 검증 Agent                                         │
│                                                                     │
│  [입력 1] 회로도 핀맵 CSV          [검증 지식베이스]                  │
│   chip, pin, function, label ──→ ┌─ CubeMX DB XML (핀 AF 맵)        │
│                                  ├─ STM32G4 Datasheet RAG            │
│  [입력 2] 자연어 프롬프트          ├─ 오픈소스 회로도 (정답 레퍼런스)  │
│   "STM32G474, BLDC FOC,          └─ 3계층 규칙 엔진                  │
│    엔코더, FDCAN, SPI EEPROM"              ↓                         │
│          ↓ LLM 파싱                                                  │
│   {chip, motors[], peripherals[],    [검증 LLM : Gemma 4 31B]       │
│    communication[], sensors[]}               ↓                      │
│                                  [검증 리포트 + 확정된 핀 연결 JSON]  │
│                                                                     │
│  3계층 검증:                                                         │
│    ① 필수 인프라 (VDD, VCAP, BOOT0, NRST, SWD)                      │
│    ② 모터 제어 논리 (상보 PWM 쌍, 데드타임, 엔코더 타이머)           │
│    ③ 페리페럴 제약 (ADC 충돌, OPAMP 수, FDCAN 핀)                    │
└──────────────────────────┬──────────────────────────────────────────┘
                           ↓
              ┌────────────────────────┐
              │   검증 게이트 (GATE)    │
              │  errors[] == [] ?      │
              └────────────────────────┘
               PASS ↓            FAIL ↓
                                 ┌─────────────────────────┐
                                 │  ❌ 진행 차단            │
                                 │  오류 리포트 사용자 반환 │
                                 │  회로도 수정 후 재실행   │
                                 └─────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 2 : CubeMX 자동화 — 4단계 워크플로우                            │
│                                                                     │
│  [확정된 핀 연결 JSON]                                               │
│          ↓                                                          │
│  [2-1] .ioc 템플릿 수정 (Python 정규식 + LLM 파라미터 보조)          │
│          ↓                                                          │
│  [2-2] CubeMX Headless CLI 실행 (java -jar STM32CubeMX -q)          │
│          ↓                                                          │
│  [2-3] LLM 스니펫 → USER CODE BEGIN/END 영역 주입                    │
│          ↓                                                          │
│  [2-4] 프로젝트 패키징 (.zip)                                        │
│                                                                     │
│  결과: HAL 초기화 코드 + 모터 기본 구동 스니펫 포함 프로젝트           │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 3 : 알고리즘 통합 Agent                                        │
│                                                                     │
│  [CubeMX 생성 코드]  +  [파싱된 요구사항 (Step 1 출력)]              │
│          ↓                      ↓                                   │
│  [Golden Module RAG]  ← 오픈소스 FOC 코드 + golden_modules/          │
│   요구사항에 맞는 모듈 검색·선택                                     │
│          ↓                                                          │
│  [통합 LLM : Gemma 4 26B MoE]                                       │
│   - 선택된 모듈을 CubeMX 코드에 삽입할 위치 결정                     │
│   - 인터럽트 핸들러 / 메인 루프 연결 코드 작성                       │
│   - 모듈 간 데이터 흐름 (전류값 → FOC → PWM) 연결                   │
│          ↓                                                          │
│  [완성된 펌웨어]                                                     │
│   main.c (통합), motor_foc.c, fdcan_handler.c, ...                  │
└─────────────────────────────────────────────────────────────────────┘
Step 1 — HW 설계 검증 Agent 상세

역할 (기존 AF 검증을 넘어 3계층 HW Expert 검증)

기존 검증의 한계: CubeMX XML 기반 AF 핀맵 호환성 위주 → TIM1_CH1과 TIM2_CH1을 상·하단 스위치에 물리는 논리적 설계 오류나 BOOT0 플로팅 같은 실무 치명적 결함을 놓침.

새로운 3계층 검증:
  ① 필수 인프라 검증: VDD, VCAP, BOOT0, NRST, SWD(디버그) 핀 할당 및 결선 확인
  ② 모터 제어 논리 검증: 상보형 PWM 쌍(TIM1_CH1 & TIM1_CH1N), 데드타임 삽입 가능 여부
  ③ 페리페럴 제약 검증: ADC 핀 할당, 통신(FDCAN, UART) 핀 매핑 점검

입력 방식

입력 1 — 핀맵 CSV (회로도에서 추출)

chip,STM32G474RET6
hse_mhz,24
sysclk_mhz,170

pin,function,label
PA8,TIM1_CH1,PWM_UH
PB8,FDCAN1_RX,CAN_RX
입력 2 — 자연어 프롬프트 (HW 개발자가 직접 작성)

STM32G474RET6 칩을 쓸 거고, 외부 크리스탈 24MHz / 시스템 170MHz야.
BLDC 모터 1개를 FOC로 제어할 건데 증분형 엔코더(A/B/Z)로 각도 읽고,
3상 6채널 PWM으로 인버터 구동해. 데드타임 500ns, 전류는 내부 OPAMP.
통신은 FDCAN 1Mbps 쓰고, 파라미터 저장용으로 SPI EEPROM도 연결할 거야. (CS: PA4)
JSON을 직접 작성하지 않아도 됨. LLM이 자연어를 파싱해 구조화 처리.
프롬프트 작성 가이드 (포함 권장 항목)

항목	예시 표현
칩 정보	"STM32G474RET6, 24MHz 크리스탈, 170MHz"
모터/제어 방식	"BLDC 1축 FOC", "DC 모터 2축 듀티 제어"
피드백 센서	"증분형 엔코더 A/B/Z", "Hall 센서 3개"
PWM/인버터	"3상 6채널 PWM, 데드타임 500ns, 내부 OPAMP 전류 감지"
통신	"FDCAN 1Mbps", "UART 디버그", "SPI EEPROM"
외부 장치	"SPI EEPROM CS: PA4", "I2C 온도 센서"
LLM 처리 흐름 (Step 1 내부)

자연어 프롬프트
      ↓ (Gemma 4 31B 파싱)
{chip, clock, motors[], feedback[], pwm, communication[], external[]}
      ↓
핀맵 CSV + 구조화된 요구사항 → 3계층 규칙 엔진 + RAG 검증
      ↓
검증 리포트 (errors[], warnings[]) + 확정 핀 JSON (Step 2 입력)

검증 출력 예시:
  ✅ PASS: 전원(VDD, VCAP), 디버그(SWD) 인프라 정상 연결
  🚨 Critical: TIM1_CH1(PA8) 상단, TIM2_CH1(PA0) 하단 — 상보 PWM 불가, TIM1_CH1N으로 변경 필요
  ⚠️ Warning: OPAMP PGA 출력 저항 매칭 미최적화 (AN5306 참조)
사용 모델: Google Gemma 4 31B Dense (Q4_K_M, ~20GB)

선택 이유:
- Apache 2.0 라이선스 → 사내 자유 활용
- 31B Dense 아키텍처: 모든 파라미터 활성 → 복잡한 논리 추론·자연어 파싱 정확도 최상급
- QLoRA Fine-tuning 안정적 (Dense 구조가 MoE보다 학습 용이)
- 128GB 통합 메모리 → Q4 양자화 ~20GB + KV캐시 포함 ~24GB 사용, 여유 충분
- 코드 생성 모델과 동시 로드 가능 (합산 ~42GB)

메모리 사용량:
- Q4_K_M 양자화 기준 ~20GB 사용
출력 형식 (Step 2 입력 — .ioc 생성 기반)

{
  "chip": "STM32G474RET6",
  "validation": "PASS",
  "clock": { "hse_mhz": 24, "sysclk_mhz": 170 },
  "pins": [
    { "pin": "PA8",  "function": "TIM1_CH1",  "af": "AF6", "label": "PWM_UH", "status": "OK" },
    { "pin": "PA7",  "function": "TIM1_CH1N", "af": "AF6", "label": "PWM_UL", "status": "OK" },
    { "pin": "PB9",  "function": "FDCAN1_TX", "af": "AF9", "status": "OK" }
  ],
  "peripherals": ["TIM1", "ADC1", "ADC2", "FDCAN1", "CORDIC", "FMAC"],
  "errors": [],
  "warnings": ["PA0 풀업 저항 미설정 권장"]
}
Step 2 — CubeMX 자동화 상세 (4단계 워크플로우)

기존 LLM 코드 생성의 문제점: 챗GPT 등에 "STM32G4 코드 짜줘"하면 레지스터 오프셋이나 HAL 버전이 틀린 코드를 뱉음 → 실제 칩에서 동작 안 함.
해결책: AI가 C 코드를 직접 타이핑하지 않고, .ioc 텍스트 파일만 조작하여 CubeMX CLI가 정확한 초기화 코드를 생성.

[2-1] .ioc 템플릿 수정

역할: Step 1 JSON → .ioc 파일 자동 생성
구현: Python 정규식 기반 skill (skill_ioc_text_modifier.py)
방식:
  - STM32G4 빈 프로젝트 .ioc 템플릿 로드
  - {"PA8": "TIM1_CH1"} → PA8.Signal=TIM1_CH1, PA8.Locked=true 변환
  - 통신 비트레이트 등 복잡 파라미터는 LLM 보조 판단

[2-2] CubeMX Headless CLI 실행

역할: .ioc → HAL 초기화 C 코드 프로젝트 생성
구현: Shell skill (skill_cubemx_headless_runner.sh)
명령: java -jar STM32CubeMX -q generate.script
결과 검증: Inc/, Src/, main.c 파일 존재 여부 자동 확인

[2-3] 스니펫 주입 (LLM → USER CODE 영역)

역할: 빈 main.c에 모터 구동 초기 함수 호출문 삽입
구현: Python skill (skill_inject_c_code.py)
방식:
  - LLM이 3~10줄의 구동 스니펫 생성 (HAL_TIM_PWM_Start 등)
  - main.c 내 /* USER CODE BEGIN 2 */ 마커 검색
  - 마커 사이에 스니펫 안전 삽입

[2-4] 프로젝트 패키징

역할: 완성 프로젝트 → .zip 압축 → 다운로드 링크 발급
구현: Python shutil.make_archive

Step 2 출력 구조 (Step 3 입력)

project/
├── Core/Src/
│   ├── main.c       ← /* USER CODE BEGIN/END */ + 기본 스니펫 포함
│   ├── tim.c        ← MX_TIM1_Init (PWM, 데드타임 자동 설정)
│   ├── adc.c        ← MX_ADC1_Init (Injected + TIM 트리거)
│   ├── fdcan.c      ← MX_FDCAN1_Init
│   └── cordic.c     ← MX_CORDIC_Init
└── Makefile
Step 3 — 알고리즘 통합 Agent 상세

LLM이 실제로 하는 일 (범위 명확히 제한)

작성 O:
  ✅ USER CODE 영역 내 함수 호출 및 변수 선언
  ✅ ADC 완료 인터럽트 핸들러 → FOC 루틴 호출
  ✅ FDCAN 수신 콜백 → 커맨드 파싱 호출
  ✅ 메인 루프 → 속도/위치 제어 루프 호출
  ✅ Golden Module include + 초기화 파라미터 설정
  ✅ 모듈 간 데이터 흐름 연결 구조체/전역변수

작성 X (CubeMX 또는 기존 코드가 담당):
  ❌ HAL_TIM_PWM_Init 등 HAL 초기화 함수 본체
  ❌ Clarke/Park/SVPWM 알고리즘 내부 구현
  ❌ CORDIC/FMAC 레지스터 접근 코드
사용 모델: Google Gemma 4 26B MoE (Q8, ~22GB, Active ~4B)

선택 이유:
- MoE 아키텍처: 26B 총 파라미터 중 ~4B만 활성화 → 추론 속도 2.5배 이상 빠름
- USER CODE 삽입은 함수 호출 + 연결 코드 수준 → 31B Dense 수준의 깊은 추론 불필요
- 코드 생성 용도 최적화, 실시간 스트리밍 응답에 유리
- Q8 양자화 ~22GB → Step 1 모델(~20GB)과 동시 로드 합계 ~42GB (128GB 내 매우 여유)
128GB 모델 배치

Step 1 (검증)	Step 3 (통합)	동시 로드 합계
권장	Gemma 4 31B Dense Q4 (~20GB)	Gemma 4 26B MoE Q8 (~22GB)	~42GB ✓
최고 품질	Gemma 4 31B Dense Q8 (~36GB)	Gemma 4 26B MoE Q8 (~22GB)	~58GB ✓
경량	Gemma 4 26B MoE Q4 (~18GB)	Gemma 4 26B MoE Q4 (~18GB)	~36GB ✓
Step 2는 LLM 없음 — 두 모델 128GB 내 상시 로드, Step 전환 시 재로드 없음
RAG 구축 가이드

RAG가 필요한 이유

LLM은 특정 STM32 칩의 정확한 AF 번호, 레지스터 주소 등을 할루시네이션할 수 있음. RAG로 실제 데이터시트를 실시간 참조하게 해야 신뢰도 확보 가능.

Step 1 RAG — HW 설계 검증용 지식베이스

수집 대상 (실제 수집 완료 현황)

① CubeMX DB XML — 핵심 (핀 AF 매핑)
   처리 방식: Python 파싱 → pin_af_db.json (규칙 엔진 직접 조회, RAG 불필요)

② ST 공식 문서 — 14건 수집 완료 (55MB)
   ├── rm0440 (레퍼런스 매뉴얼, 39MB)
   ├── stm32g474re 데이터시트 (3MB)
   ├── dm00445657 HW 개발 가이드 (AN5031, 4.3MB)
   ├── an5306 OPAMP 전류 센싱 (907KB)
   ├── an5346 ADC 최적화 (201KB)
   ├── an5348 FDCAN 가이드 (888KB)
   ├── an3070 RS-485 DE핀 (194KB)
   ├── evspin32g4-dual 회로도 (299KB)
   ├── evspin32g4-dual 매뉴얼 (1.2MB)
   ├── evspin32g4-dual BOM (197KB)
   ├── evspin32g4-dual 제조 데이터 (645KB)
   ├── um3027 MCSDK v6 Workbench (3.9MB)
   ├── um3016 MCSDK v6 Profiler (1.2MB)
   └── 64361889 (추가 문서, 1.2MB)

③ 오픈소스 회로도 — "정답 레퍼런스" (8개 프로젝트)
   ├── ✅ flatmcu (12MB) — STM32G473CB FOC KiCad 회로도
   ├── ✅ STM32CubeG4 (43MB) — ST 공식 HAL 예제 (HRTIM/TIM/ADC/OPAMP/FDCAN/CORDIC)
   ├── ⬜ Arduino-FOC — SimpleFOC STM32G4 포팅 (submodule 등록, 초기화 필요)
   ├── ⬜ stm32-esc — B-G431B-ESC1 최적화 (submodule 등록, 초기화 필요)
   ├── ⬜ moteus — 고정밀 로봇 관절 액추에이터 (submodule 등록, 초기화 필요)
   ├── ⬜ MESC_FOC_ESC — 하이엔드 모터 구동 (submodule 등록, 초기화 필요)
   ├── ⬜ bldc_vesc — VESC 오픈소스 ESC (submodule 등록, 초기화 필요)
   └── ⬜ ODriveHardware — ODrive 하드웨어 회로도 (submodule 등록, 초기화 필요)

④ ST 포럼 Q&A — 에러 사례 (수집 파이프라인 준비, 데이터 0건)
   scripts/scrape_st_forum.py 실행하여 st_forum_qa.jsonl 생성 예정
청킹(Chunking) 전략

문서 타입별로 다르게 처리:

[XML → JSON 변환]  (청킹 불필요, 직접 쿼리)
  CubeMX XML → Python 파싱 → {chip: {pin: [AF목록]}} JSON DB
  → 규칙 엔진에서 직접 조회 (RAG 불필요, 100% 정확)

[PDF 데이터시트]  (RAG 적용)
  - 핀 테이블 페이지: 표(Table) 단위로 추출 → 행(row) 단위 청킹
  - 일반 설명 페이지: 512 토큰 단위 + 50 토큰 overlap
  - 툴: PyMuPDF (표 추출), pdfplumber

[오픈소스 코드]  (RAG 적용)
  - C/H 파일: 함수 단위 청킹 (정규식으로 함수 경계 탐지)
  - KiCad 회로도: 서브시스템 단위 청킹
  - 메타데이터: {source, file, module_type, g4_accel}
임베딩 모델

권장: BAAI/bge-m3
  - 한국어 + 영어 동시 지원
  - 기술 문서(영문) + 사내 문서(한글) 혼재 환경에 최적
  - 완전 로컬 실행 가능
벡터 DB

권장: Qdrant (로컬 Docker)
  - 메타데이터 필터링 지원 (칩 패밀리별 필터링 핵심)
  - 예: query("PA9 USART", filter={"chip": "STM32G4xx"})
RAG 파이프라인 구성

[질의 입력]
     ↓
[질의 재작성]  ← LLM으로 검색 최적화
     ↓
[하이브리드 검색]
  ├─ Dense 검색  : BGE-M3 임베딩 → 벡터 유사도
  └─ Sparse 검색 : BM25 키워드 검색 (핀 번호, 함수명 등 정확 매칭)
     ↓
[Re-ranking]  ← cross-encoder로 상위 20개 → 상위 5개 재정렬
  모델: cross-encoder/ms-marco-MiniLM-L-6-v2 (로컬 실행 가능)
     ↓
[컨텍스트 주입 → LLM 생성]
기술 스택 정리

레이어	Step 1 (검증)	Step 3 (코드 통합)
LLM	Gemma 4 31B Dense (Q4_K_M, ~20GB)	Gemma 4 26B MoE (Q8, ~22GB)
실행 환경	Ollama (로컬, DGX Spark)	Ollama (로컬, DGX Spark)
동시 로드	두 모델 합산 ~42GB → 128GB 내 상시 메모리 상주	
RAG 프레임워크	LlamaIndex	LlamaIndex
벡터 DB	Qdrant (Docker)	Qdrant (Docker)
임베딩	BAAI/bge-m3	BAAI/bge-m3
핀 DB	CubeMX XML → JSON (규칙 기반)	—
Fine-tuning	QLoRA 31B — DGX Spark 단독으로 가능	QLoRA 26B — DGX Spark 단독으로 가능
API 서버	FastAPI	FastAPI
UI	Streamlit	Streamlit
단계별 개발 계획

Phase 1 — MVP (4~6주)

목표: CSV 핀 연결표 → 3계층 규칙 기반 검증 → CubeMX 코드 생성 (LLM 최소 개입)

CubeMX XML 파서 → 핀 AF JSON DB 생성
3계층 규칙 기반 검증 엔진 구현 (인프라 / 모터 논리 / 페리페럴)
.ioc 템플릿 수정 + CubeMX CLI 연동
Streamlit UI (3-Step 화면 구성)
Phase 2 — RAG 통합 (4~6주)

목표: LLM + RAG로 검증 설명 자연어 생성, 복잡한 케이스 처리

Step 1 RAG: 데이터시트 + 오픈소스 회로도 임베딩, Qdrant 구성
Gemma 4 31B → 검증 결과 자연어 설명 생성 + 3계층 검증 보조
Step 3 RAG: 오픈소스 FOC 소스 + HAL 예제 코드 임베딩
Gemma 4 26B MoE → USER CODE 영역 코드 통합
Phase 3 — Fine-tuning (선택, 4~8주)

목표: 사내/도메인 특화 응답 품질 최적화

학습 데이터: 오픈소스 회로도 + CubeMX 생성 코드 + 검증 에러 사례 (500~1,000쌍)
검증 모델: Gemma 4 31B QLoRA fine-tuning (r=32, JSON 리포트 양식 내재화)
코드 모델: Gemma 4 26B MoE QLoRA fine-tuning (r=64, USER CODE 삽입 정확도 향상)
GPU 요구: DGX Spark 128GB 단독으로 두 모델 모두 fine-tuning 가능
  31B QLoRA (4-bit): ~60~70GB 사용 (128GB 내 처리 가능)
  26B QLoRA (4-bit): ~40~50GB 사용
학습 데이터(정답값) 구축 전략

Golden Module 소스 (오픈소스 기반)

현재 오픈소스 코드를 기반으로 Golden Module을 구성:

[✅ golden_modules/ — 현재 등록됨 (신규 작성)]
  bldc_6step_hall.c/.h    → 6-step 전환 (Hall 인터럽트 기반, BRK 보호)
  dc_motor_pid.c/.h       → DC모터 H-bridge PWM + PID (Anti-windup)
  fdcan_motor_cmd.c/.h    → FDCAN 커맨드 파싱 + 비상정지 즉시 처리
  multi_axis_sync.c/.h    → TIM1/TIM8/TIM20 PWM 동기화 (CR2/SMCR 직접)

[🔨 오픈소스에서 추출 → 등록 예정]
  foc_clarke.c/.h         → Clarke 변환 (Arduino-FOC / MESC)
  foc_park.c/.h           → Park 변환 + CORDIC (Arduino-FOC / MESC)
  foc_svpwm.c/.h          → SVPWM (VESC / MESC)
  foc_current_pi.c/.h     → PI 제어기 (moteus / MESC)
  foc_current_sense.c/.h  → ADC + OPAMP 연동 (flatmcu)

[🏢 향후 사내 코드 확보 시 교체 예정]
  사내 최적화 FOC/전류제어 코드가 확보되면 위 오픈소스 모듈을 대체.
코드 레이어 구조 (3계층)

┌────────────────────────────────────────┐  ← Step 3 LLM이 생성
│  Layer 3. 통합 레이어                   │
│  USER CODE 영역: 함수 호출, 연결 코드   │
├────────────────────────────────────────┤  ← 오픈소스 기반 Golden Module
│  Layer 2. 드라이버 알고리즘             │
│  FOC, 전류제어, PID, Clarke/Park,      │
│  SVPWM, SMO, FDCAN 핸들러              │
├────────────────────────────────────────┤  ← Step 2 CubeMX CLI가 자동 생성
│  Layer 1. HAL 초기화                   │
│  MX_TIM1_Init, MX_ADC_Init,           │
│  MX_FDCAN_Init, MX_CORDIC_Init, ...    │
└────────────────────────────────────────┘

전략 요약:
Layer 1 → CubeMX CLI 완전 자동 (정확도 100%, LLM 불필요)
Layer 2 → 오픈소스 Golden Module (검증된 알고리즘, 향후 사내 코드로 교체)
Layer 3 → LLM이 Layer 1~2를 연결하는 접착 코드만 작성 (범위 최소화)
권장 시작 순서

STM32G474 CubeMX XML 파서 → 핀 AF JSON DB
3계층 규칙 기반 핀 검증기 (Step 1 MVP)
핀 JSON → .ioc 템플릿 수정기 + CubeMX CLI 연동 (Step 2 핵심)
오픈소스 FOC 코드 → Golden Module 가공·등록·태깅 ← git submodule update 후 시작
Step 2 출력(CubeMX 코드) + Golden Module 조합 → 초기 학습 샘플 구성
Ollama + Gemma 4 31B / Gemma 4 26B MoE 세팅
Step 3 RAG 파이프라인 구성 (Golden Module RAG, BGE-M3 + Qdrant)
Step 1 LLM 연결 (Gemma 4 31B + 3계층 검증 RAG)
조합 자동화 스크립트 → 데이터셋 300개 목표
데이터 충분히 쌓이면 Fine-tuning
미결 사항 (결정 필요)

 ✅ 지원할 STM32 패밀리 범위 → STM32G4 계열 단일 타겟 확정
 ✅ 주요 회로도 입력 포맷 → CSV 우선 확정
 ✅ 사내 GPU 서버 → DGX Spark 128GB 확정
 ✅ Step 1 → Step 2 진행 조건 → errors[] 비어있어야만 Step 2 진행
 🔨 기존 FOC 코드 → 당분간 오픈소스 기반, 향후 사내 코드 확보 시 교체
 🔨 BLDC 6-step / DC모터 / FDCAN 모듈 → golden_modules/ 등록 완료
 🔨 MCSDK 라이선스 확인 (사내 학습 데이터 활용 가능 여부)
 🔨 제어 목표 우선순위 (속도 제어 먼저? 위치 제어 먼저?)
 🔨 Git submodule 6개 초기화 (Arduino-FOC, stm32-esc, moteus, MESC, VESC, ODrive)