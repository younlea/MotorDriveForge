STM32 회로도 검증 & 펌웨어 생성 Agent 설계 계획

최초 작성: 2026-04-10 마지막 수정: 2026-04-10 (6차 — Step 1 입력 방식을 구조화 JSON → 자연어 프롬프트로 변경)
서버 사양

항목	내용
장비	NVIDIA DGX Spark (GB10 Grace Blackwell Superchip)
통합 메모리	128GB LPDDR5X (CPU + GPU 공유, unified memory)
AI 성능	~1 PFLOPS (FP4)
특징	외부망 차단 완전 로컬 운영 가능, 70B급 모델 여유롭게 실행
확장	2대 NVLink 연결 시 256GB → 405B급 모델도 가능
unified memory 구조이므로 "VRAM 부족" 문제 없음. 두 모델 동시 로드도 가능.
개요

HW 개발자가 만든 회로도를 입력받아 두 단계로 분리된 파이프라인으로 처리:

Step	역할	담당
Step 1. 핀 검증 Agent	회로도 핀 연결이 G4 AF 스펙에 맞는지 검증 → 검증 완료 핀 JSON 확정	LLM + 규칙 엔진
Step 2. CubeMX 자동화	검증된 핀 JSON → .ioc 생성 → CubeMX CLI → HAL 초기화 코드	코드 (LLM 불필요)
Step 3. 알고리즘 통합 Agent	CubeMX 생성 코드 + Golden Module 선택·통합 → 완성 펌웨어	LLM
핵심 설계 원칙: LLM은 HAL API를 직접 생성하지 않음. CubeMX가 보장하는 정확한 초기화 코드 위에 LLM이 알고리즘 레이어만 조립.
사내 전용 운영 (외부 데이터/API 사용 불가)

타겟 칩: STM32G4 계열 (모터 제어 최적화 MCU) 보유 자산: BLDC 전류제어 / FOC 알고리즘 최적화 코드 (기존 검증 완료) → Golden Module 직접 등록

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

전류제어와 FOC는 독립 모듈로 존재하며, 제어 루프 캐스케이드 구조로 조합됨. 기존 보유 최적화 코드가 이 레이어를 담당 → 학습 데이터의 핵심 자산.
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
알고리즘 모듈 목록 (기존 최적화 코드 매핑)

모듈 파일	역할	G4 가속기 활용	기존 코드
foc_clarke.c	Clarke 변환 (abc→αβ)	— (단순 연산)	✅ 보유
foc_park.c	Park 변환 (αβ→dq)	CORDIC sin/cos	✅ 보유
foc_inv_park.c	역 Park 변환 (dq→αβ)	CORDIC 재사용	✅ 보유
foc_svpwm.c	Space Vector PWM	—	✅ 보유
foc_current_pi.c	Id/Iq PI 제어기 + Anti-windup	FMAC 옵션	✅ 보유
foc_speed_pi.c	속도 PI 제어기	—	✅ 보유
foc_position_pi.c	위치 PI 제어기 (선택)	—	✅ 보유
foc_angle_encoder.c	엔코더 각도/속도 추정	TIM encoder mode	✅ 보유
foc_angle_hall.c	Hall 센서 섹터 → 각도	—	✅ 보유
foc_angle_smo.c	Sensorless SMO 옵저버	—	✅ 보유
foc_current_sense.c	ADC 전류 샘플링 + 옵셋 보정	Internal OPAMP	✅ 보유
bldc_6step.c	6-step 전환 (Hall 기반)	—	신규 작성 필요
기존 보유 코드 → Layer 2 대부분 해결됨. 남은 신규 작업은 6-step과 통합 레이어.
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
│  STEP 1 : 핀 검증 Agent                                              │
│                                                                     │
│  [입력 1] 회로도 핀맵 CSV          [검증 지식베이스]                  │
│   chip, pin, function, label ──→ ┌─ CubeMX DB XML (핀 AF 맵)        │
│                                  ├─ STM32G4 Datasheet RAG            │
│  [입력 2] 자연어 프롬프트          └─ 규칙 엔진 (충돌/전용핀 체크)    │
│   "STM32G474, BLDC FOC,                                             │
│    엔코더, FDCAN, SPI EEPROM"              ↓                         │
│          ↓ LLM 파싱                                                  │
│   {chip, motors[], peripherals[],    [검증 LLM : Qwen2.5-72B]       │
│    communication[], sensors[]}               ↓                      │
│                                  [검증 리포트 + 확정된 핀 연결 JSON]  │
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
│  STEP 2 : CubeMX 자동화  (LLM 불필요 — 코드로 처리)                  │
│                                                                     │
│  [확정된 핀 연결 JSON]                                               │
│          ↓                                                          │
│  [.ioc 파일 생성기]  ← Python 스크립트, 완전 자동                    │
│   핀 AF 설정, 클럭, 주변장치 파라미터 → project.ioc                  │
│          ↓                                                          │
│  [CubeMX CLI 실행]                                                  │
│   STM32CubeMX -q generate.script                                    │
│          ↓                                                          │
│  [HAL 초기화 코드 출력]  ← 100% CubeMX 보장, 할루시네이션 없음        │
│   main.c (MX_Init 구조), stm32g4xx_hal_conf.h,                      │
│   각 주변장치 Init 파일 (tim.c, adc.c, fdcan.c ...)                  │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 3 : 알고리즘 통합 Agent                                        │
│                                                                     │
│  [CubeMX 생성 코드]  +  [파싱된 요구사항 (Step 1 출력)]              │
│          ↓                      ↓                                   │
│  [Golden Module RAG]  ← 기존 FOC/전류제어 코드 + 신규 모듈           │
│   요구사항에 맞는 모듈 검색·선택                                     │
│          ↓                                                          │
│  [통합 LLM : Qwen2.5-Coder-32B]                                     │
│   - 선택된 모듈을 CubeMX 코드에 삽입할 위치 결정                     │
│   - 인터럽트 핸들러 / 메인 루프 연결 코드 작성                       │
│   - 모듈 간 데이터 흐름 (전류값 → FOC → PWM) 연결                   │
│          ↓                                                          │
│  [완성된 펌웨어]                                                     │
│   main.c (통합), motor_foc.c, fdcan_handler.c, ...                  │
└─────────────────────────────────────────────────────────────────────┘
Step 1 — 핀 검증 Agent 상세

역할

HW 개발자의 자연어 프롬프트를 파싱하여 요구 기능(칩·모터·통신·센서 등) 추출
추출된 요구사항 + 핀맵 CSV를 기반으로 AF 스펙 검증 수행
충돌/오류 핀 탐지 및 리포트 생성
검증 통과된 핀 연결을 구조화된 JSON으로 출력 (Step 2의 입력)
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
      ↓ (Qwen2.5-72B 파싱)
{chip, clock, motors[], feedback[], pwm, communication[], external[]}
      ↓
핀맵 CSV + 구조화된 요구사항 → 규칙 엔진 + RAG 검증
      ↓
검증 리포트 (errors[], warnings[]) + 확정 핀 JSON (Step 2 입력)
검증 항목

Alternate Function 호환성 (e.g., PA9 → USART1_TX ✓ / USART2_TX ✗)
핀 중복 배정 (두 기능이 같은 핀 사용)
전용 핀 오용 (OSC_IN/OUT, NRST, BOOT0 등)
전원 핀 누락 (VDD, VDDA, VSS)
클럭 소스 설정 일관성 (HSE / HSI / LSE)
요구사항 미충족 핀 누락 (e.g., FDCAN 요구했는데 핀맵에 없음)
사용 모델: Qwen2.5-72B-Instruct (Q4_K_M, ~40GB)

선택 이유:
- 128GB 통합 메모리 → 7B 쓸 이유 없음, 72B 여유롭게 실행
- Qwen2.5-72B: 한국어 자연어 파싱 + 기술 추론 모두 최상급
- 자연어 → 구조화 JSON 변환, 핀 제약 조건 추론 모두 우수
- 차선: LLaMA 3.1 70B (한국어 약간 약하지만 범용성 좋음)

메모리 사용량:
- Q4_K_M 양자화 기준 ~40GB 사용 → 코드 생성 모델과 동시 로드 가능
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
Step 2 — CubeMX 자동화 상세

역할

Step 1 JSON → .ioc 파일 자동 생성 → CubeMX CLI 실행 → HAL 초기화 코드 출력
LLM 불필요 — 완전 결정론적 처리, 할루시네이션 원천 차단
.ioc 파일 자동 생성

.ioc는 일반 텍스트(properties 포맷)로 Python으로 완전 자동 생성 가능.

def generate_ioc(verified: dict, output_path: str):
    lines = [
        f"Mcu.UserName={verified['chip']}",
        f"Mcu.Family=STM32G4",
        f"RCC.HSEFreq_Value={verified['clock']['hse_mhz']}000000",
        f"RCC.SYSCLKFreq_VALUE={verified['clock']['sysclk_mhz']}000000",
    ]
    for p in verified["pins"]:
        lines += [
            f"{p['pin']}.Signal={p['function']}",
            f"{p['pin']}.GPIO_Label={p.get('label','')}",
        ]
    for periph in verified["peripherals"]:
        lines.append(f"IP.{periph}=enabled")
    with open(output_path, "w") as f:
        f.write("\n".join(lines))
CubeMX CLI 호출 (DGX Spark Ubuntu 환경)

import subprocess

def run_cubemx(ioc_path: str) -> bool:
    script = f"loadproject {ioc_path}\ngenerate code\nexit\n"
    with open("/tmp/cx_script.txt", "w") as f:
        f.write(script)
    result = subprocess.run(
        ["STM32CubeMX", "-q", "/tmp/cx_script.txt"],
        capture_output=True, timeout=60
    )
    return result.returncode == 0
Step 2 출력 구조 (Step 3 입력)

project/
├── Core/Src/
│   ├── main.c       ← /* USER CODE BEGIN/END */ 영역 포함
│   ├── tim.c        ← MX_TIM1_Init (PWM, 데드타임 자동 설정)
│   ├── adc.c        ← MX_ADC1_Init (Injected + TIM 트리거)
│   ├── fdcan.c      ← MX_FDCAN1_Init
│   └── cordic.c     ← MX_CORDIC_Init
└── Makefile
/* USER CODE BEGIN */ ~ /* USER CODE END */ 가 Step 3에서 LLM이 채울 공간.
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
사용 모델: Qwen2.5-Coder-32B-Instruct (Q8, ~32GB)

역할 축소로 난이도가 크게 낮아짐:
  이전: HAL API 전체를 정확히 생성 (어려움, 할루시네이션 위험)
  이후: USER CODE 영역에 함수 호출 + 연결 코드만 작성 (훨씬 쉬움)
  → 32B로 충분, 정확도 대폭 향상
128GB 모델 배치

Step 1 (검증)	Step 3 (통합)	동시 로드 합계
권장	Qwen2.5-72B Q4 (~40GB)	Qwen2.5-Coder-32B Q8 (~32GB)	~72GB ✓
최고 품질	Qwen2.5-72B Q8 (~72GB)	Qwen2.5-Coder-32B Q8 (~32GB)	~104GB ✓
빠른 응답	Qwen2.5-72B Q4 (~40GB)	DeepSeek-Coder-V2-Lite (~16GB)	~56GB ✓
Step 2는 LLM 없음 — 두 모델 128GB 내 상시 로드, Step 전환 시 재로드 없음
RAG 구축 가이드

RAG가 필요한 이유

LLM은 특정 STM32 칩의 정확한 AF 번호, 레지스터 주소 등을 할루시네이션할 수 있음. RAG로 실제 데이터시트를 실시간 참조하게 해야 신뢰도 확보 가능.

Step 1 RAG — 핀 검증용 지식베이스

수집 문서

① CubeMX DB XML      ← 핵심, 이미 구조화됨
   └ STM32CubeMX\db\mcu\STM32F407VGTx.xml

② STM32 Datasheet (PDF)
   └ 핀 기능 테이블, Alternate Function 맵 페이지만 추출

③ Errata Sheet       ← 알려진 핀 버그/제약사항
청킹(Chunking) 전략

문서 타입별로 다르게 처리:

[XML → JSON 변환]  (청킹 불필요, 직접 쿼리)
  CubeMX XML → Python 파싱 → {chip: {pin: [AF목록]}} JSON DB
  → 규칙 엔진에서 직접 조회 (RAG 불필요, 100% 정확)

[PDF 데이터시트]  (RAG 적용)
  - 핀 테이블 페이지: 표(Table) 단위로 추출 → 행(row) 단위 청킹
  - 일반 설명 페이지: 512 토큰 단위 + 50 토큰 overlap
  - 툴: PyMuPDF (표 추출), pdfplumber
임베딩 모델

권장: BAAI/bge-m3
  - 한국어 + 영어 동시 지원
  - 기술 문서(영문) + 사내 문서(한글) 혼재 환경에 최적
  - 완전 로컬 실행 가능
  - 차선: nomic-embed-text (영문 only, 더 빠름)
벡터 DB

권장: Qdrant (로컬 Docker)
  - 메타데이터 필터링 지원 (칩 패밀리별 필터링 핵심)
  - 예: query("PA9 USART", filter={"chip": "STM32F4xx"})
  - 차선: ChromaDB (더 단순, 소규모에 충분)
Step 2 RAG — 코드 생성용 지식베이스

수집 문서

① STM32 HAL 드라이버 소스
   └ STM32CubeF4\Drivers\STM32F4xx_HAL_Driver\Src\*.c

② Reference Manual (RM0090 등)
   └ 주변장치 설정 챕터만 (USART, SPI, I2C, TIM 등)

③ CubeMX 자동 생성 코드 모음  ← 학습/참조 데이터로 최고
   └ 다양한 핀 설정으로 CubeMX 돌려서 생성한 main.c 수백 개

④ 사내 기존 펌웨어 프로젝트
   └ 회사 코딩 스타일, 공통 유틸 함수 참조용
청킹 전략

[HAL 소스 코드]
  - 함수 단위로 청킹 (AST 파싱 또는 정규식으로 함수 경계 탐지)
  - 함수명, 파라미터를 메타데이터로 저장
  - 예: {"func": "HAL_UART_Init", "file": "stm32f4xx_hal_uart.c", "content": "..."}

[Reference Manual PDF]
  - 챕터/섹션 단위 청킹 (헤더 기반 분할)
  - 1024 토큰 + 100 토큰 overlap (기술 내용이 길어서 크게)

[예제 코드]
  - 파일 단위 또는 기능 블록 단위
RAG 파이프라인 구성

[질의 입력]
     ↓
[질의 재작성]  ← LLM으로 검색 최적화 (e.g., "PA9 UART 설정" → "STM32 PA9 AF7 USART1")
     ↓
[하이브리드 검색]
  ├─ Dense 검색  : BGE-M3 임베딩 → 벡터 유사도
  └─ Sparse 검색 : BM25 키워드 검색 (핀 번호, 함수명 등 정확 매칭)
     ↓
[Re-ranking]  ← cross-encoder로 상위 20개 → 상위 5개 재정렬
  모델: cross-encoder/ms-marco-MiniLM-L-6-v2 (로컬 실행 가능)
     ↓
[컨텍스트 주입 → LLM 생성]
RAG 구축 단계별 작업

1단계 — 데이터 수집 및 전처리

# CubeMX XML 파싱 예시
import xml.etree.ElementTree as ET

def parse_cubemx_db(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    pin_map = {}
    for pin in root.findall('.//Pin'):
        pin_name = pin.get('Name')
        signals = [s.get('Name') for s in pin.findall('Signal')]
        pin_map[pin_name] = signals
    return pin_map
2단계 — 임베딩 및 인덱싱

from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore

embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-m3")
# 문서 청킹 → 임베딩 → Qdrant 저장
3단계 — 검색 파이프라인

# 메타데이터 필터로 칩 패밀리 범위 제한
from llama_index.core.vector_stores import MetadataFilter

filters = MetadataFilter.from_dict({"chip_family": "STM32F4"})
retriever = index.as_retriever(similarity_top_k=5, filters=filters)
4단계 — 평가 및 품질 관리

평가 지표:
- 핀 검증 정확도: CubeMX 정답과 비교 (자동화 가능)
- 코드 컴파일 성공률: arm-none-eabi-gcc로 자동 컴파일 테스트
- 검색 품질: Recall@5 (정답 문서가 상위 5개에 포함되는 비율)
기술 스택 정리

레이어	Step 1 (검증)	Step 2 (코드 생성)
LLM	Qwen2.5-72B-Instruct (Q4_K_M)	Qwen2.5-Coder-32B-Instruct (Q8)
실행 환경	Ollama (로컬, DGX Spark)	Ollama (로컬, DGX Spark)
동시 로드	두 모델 합산 ~72GB → 128GB 내 상시 메모리 상주	
RAG 프레임워크	LlamaIndex	LlamaIndex
벡터 DB	Qdrant (Docker)	Qdrant (Docker)
임베딩	BAAI/bge-m3	BAAI/bge-m3
핀 DB	CubeMX XML → JSON (규칙 기반)	—
Fine-tuning	QLoRA 72B — DGX Spark 단독으로 가능	QLoRA 32B — DGX Spark 단독으로 가능
API 서버	FastAPI	FastAPI
UI	Streamlit	Streamlit
단계별 개발 계획

Phase 1 — MVP (4~6주)

목표: CSV 핀 연결표 → 규칙 기반 검증 → Jinja2 코드 생성 (LLM 없이)

CubeMX XML 파서 → 핀 AF JSON DB 생성
규칙 기반 검증 엔진 구현
Jinja2 템플릿으로 HAL 초기화 코드 생성
Streamlit UI (2-Step 화면 구성)
Phase 2 — RAG 통합 (4~6주)

목표: LLM + RAG로 자연어 설명, 복잡한 케이스 처리

Step 1 RAG: 데이터시트 임베딩, Qdrant 구성
Mistral 7B → 검증 결과 자연어 설명 생성
Step 2 RAG: HAL 소스 + 예제 코드 임베딩
CodeLlama 34B → 실제 코드 생성 테스트
Phase 3 — Fine-tuning (선택, 8~12주)

목표: 사내 코딩 스타일에 최적화

학습 데이터: CubeMX 자동 생성 코드 + 사내 코드 수집 (500~1,000쌍)
검증 모델: Qwen2.5-72B QLoRA fine-tuning
코드 모델: Qwen2.5-Coder-32B QLoRA fine-tuning
GPU 요구: DGX Spark 128GB 단독으로 두 모델 모두 fine-tuning 가능
별도 GPU 서버 불필요
72B QLoRA: 6080GB 사용 (128GB 내 처리 가능)
32B QLoRA: 3040GB 사용
학습 데이터(정답값) 구축 전략

가장 어려운 부분. CubeMX는 HAL 초기화 코드만 생성하고 드라이버 알고리즘은 생성하지 않음. "어떤 기능을 원하는가" → "완성된 드라이버 코드" 정답 쌍을 체계적으로 만들어야 함.
드라이버 모듈 분류 체계 (Taxonomy)

먼저 만들어야 할 드라이버의 조합 공간을 정의. 각 축이 독립 변수이고 조합이 학습 샘플 하나가 됨.

분류 축	옵션	기존 코드
모터 종류	BLDC, DC Motor (H-bridge)	—
BLDC 제어 방식	FOC (전류제어 포함), 6-step (Hall)	FOC ✅ / 6-step 신규
전류 제어	FOC 내 dq축 PI, 단독 전류 제한 모드	✅ 보유
FOC 각도 소스	증분형 엔코더, Hall 센서, Sensorless (SMO)	✅ 보유
제어 목표	토크 제어, 속도 제어, 위치 제어	✅ 보유
모터 수	1축, 2축, 3축 이상	—
통신 프로토콜	FDCAN, UART, FDCAN + UART	신규 작성
전류 감지	내부 OPAMP (G4 내장), 외부 션트 저항	✅ 보유
이론상 조합 수: 2 × 2 × 2 × 3 × 3 × 3 × 3 × 2 = 864개
현실적으로 유효한 조합: 약 50~70개
제외 예: 6-step + 위치제어, Sensorless + 위치제어, DC Motor + FOC 등
코드 레이어 구조 (3계층)

학습 데이터를 만들 때 3개 레이어를 분리해서 접근해야 함.

┌────────────────────────────────────────┐  ← Step 3 LLM이 생성
│  Layer 3. 통합 레이어                   │
│  USER CODE 영역: 함수 호출, 연결 코드   │
├────────────────────────────────────────┤  ← 기존 보유 코드 (Golden Module)
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
Layer 2 → 기존 보유 코드 + 신규 Golden Module (검증 완료된 알고리즘)
Layer 3 → LLM이 Layer 1~2를 연결하는 접착 코드만 작성 (범위 최소화)
Layer 2 정답값 확보 방법

① ST Motor Control SDK (X-CUBE-MCSDK) — 가장 빠른 시작

ST 공식 FOC 모터 제어 SDK, G4 CORDIC/FMAC 최적화 코드 포함

활용 방법:
  1. MCSDK Workbench에서 모터 파라미터 조합을 바꾸며 코드 생성
  2. (파라미터 설정 JSON) → (생성된 코드) 쌍을 학습 데이터로 수집
  3. 다양한 모터 수, 피드백 조합으로 반복

한계: BLDC/PMSM FOC 위주. DC 모터 / 6-step은 별도 작성 필요
② 사내 Golden Module 라이브러리 — 기존 코드 + 신규 작성

[✅ 기존 최적화 코드 → 즉시 등록 가능]  ← Layer 2 핵심 대부분 해결

  알고리즘 모듈 (재사용):
    foc_clarke.c          → Clarke 변환 (abc→αβ)
    foc_park.c            → Park 변환 (αβ→dq), CORDIC 최적화
    foc_inv_park.c        → 역 Park 변환
    foc_svpwm.c           → Space Vector PWM
    foc_current_pi.c      → Id/Iq PI 제어기 + Anti-windup (FMAC 옵션)
    foc_speed_pi.c        → 속도 PI 제어기
    foc_position_pi.c     → 위치 PI 제어기
    foc_angle_encoder.c   → 증분 엔코더 각도/속도 추정
    foc_angle_hall.c      → Hall 센서 섹터 → 각도 매핑
    foc_angle_smo.c       → Sensorless SMO 옵저버
    foc_current_sense.c   → ADC 전류 샘플링 + G4 OPAMP 연동

[🔨 신규 작성 필요]  ← 상대적으로 적은 양

  bldc_6step_hall.c     → 6-step 전환 로직 (Hall 인터럽트 기반)
  dc_motor_pid.c        → DC모터 H-bridge PWM + PID
  fdcan_motor_cmd.c     → FDCAN 커맨드 파싱 / 상태 송신
  multi_axis_sync.c     → 2축/3축 동기화 타이밍 (TIM1 + TIM8)
③ 모듈 조합 자동화 + 컴파일 검증 — 데이터 수량 증폭

검증된 Golden Module들을 조합하여 새 학습 샘플 자동 생성:
  bldc_foc_single + fdcan_motor_cmd   → BLDC 1축 FOC + FDCAN
  dc_motor_pid × 2 + fdcan_motor_cmd → DC모터 2축 + FDCAN

컴파일 자동 검증으로 최소 품질 보장:
  arm-none-eabi-gcc 빌드 통과 → 문법 오류 없음 확인
  실제 동작 검증은 샘플링(전체의 20~30%)으로 진행
학습 데이터 형식

{
  "id": "bldc_foc_1axis_encoder_fdcan_001",
  "input": {
    "chip": "STM32G474RET6",
    "requirements": {
      "motors": [
        {
          "type": "BLDC",
          "control_method": "FOC",
          "num_axes": 1,
          "feedback": "quadrature_encoder",
          "control_target": "speed",
          "pwm_freq_khz": 20,
          "deadtime_ns": 500
        }
      ],
      "communication": ["FDCAN"],
      "fdcan": { "baudrate_kbps": 1000, "fd_mode": true }
    },
    "pin_mapping": {
      "TIM1_CH1": "PA8", "TIM1_CH1N": "PA7",
      "TIM1_CH2": "PA9", "TIM1_CH2N": "PB0",
      "TIM1_CH3": "PA10","TIM1_CH3N": "PB1",
      "ENC_A": "PA0",   "ENC_B": "PA1",
      "FDCAN_TX": "PB9","FDCAN_RX": "PB8"
    }
  },
  "output": {
    "main.c": "...",
    "motor_foc.c": "...",
    "motor_foc.h": "...",
    "fdcan_handler.c": "..."
  },
  "metadata": {
    "layer1_source": "cubemx_generated",
    "layer2_source": "golden_module_v1",
    "compile_verified": true,
    "hw_tested": true,
    "tags": ["BLDC", "FOC", "FDCAN", "single_axis"]
  }
}
수집 로드맵 및 목표 수량

1순위 (즉시 시작 — 기존 코드 등록):
  ├── 보유 FOC 알고리즘 코드 → Golden Module로 태깅/문서화
  ├── CubeMX로 STM32G474 HAL 초기화 코드 수집 (Layer 1)
  └── 기존 코드 + CubeMX 초기화 조합 → 첫 학습 샘플 20~30개

2순위 (엔지니어 1~2주 — 신규 작성):
  ├── BLDC 6-step + Hall 센서 모듈
  ├── DC 모터 H-bridge + PID 모듈
  ├── FDCAN 커맨드 핸들러
  └── 2축 동기화 모듈 (TIM1 + TIM8)

3순위 (자동화, 2주):
  ├── 조합 자동 생성 스크립트
  │     (기존 알고리즘 모듈 × 통신 × 축 수 × 센서 조합)
  ├── arm-none-eabi-gcc 컴파일 자동 검증 CI
  └── JSON 메타데이터 자동 태깅

목표 데이터셋 규모:
  RAG 지식베이스 : 50~80개   (기존 코드 기반, 빠르게 구축 가능)
  Fine-tuning    : 300~500개 (조합 자동화로 확장)
  
  ※ 기존 보유 코드 덕분에 2순위까지만 완료해도 RAG 운영 가능
CubeMX DB 활용 (핵심 팁)

STM32CubeMX 설치 시 이미 구조화된 핀 데이터베이스 포함:

<!-- STM32CubeMX\db\mcu\STM32G474RETx.xml -->
<Pin Name="PA8" Position="41">
  <Signal Name="TIM1_CH1"/>
  <Signal Name="I2C2_SCL"/>
</Pin>
RAG 전에 이 XML을 파싱한 규칙 엔진만으로도 핀 검증 80% 커버 가능.

권장 시작 순서

STM32G474 CubeMX XML 파서 → 핀 AF JSON DB
규칙 기반 핀 검증기 (Step 1 MVP)
핀 JSON → .ioc 생성기 + CubeMX CLI 연동 구현 (Step 2 핵심)
기존 보유 FOC/전류제어 코드 → Golden Module 등록 + 태깅 ← 바로 시작 가능
Step 2 출력(CubeMX 코드) + Golden Module 조합 → 초기 학습 샘플 구성
FDCAN 핸들러 / 6-step / DC모터 모듈 신규 작성
Ollama + Qwen2.5-72B / Qwen2.5-Coder-32B 세팅
Step 3 RAG 파이프라인 구성 (Golden Module RAG, BGE-M3 + Qdrant)
Step 1 LLM 연결 (Qwen2.5-72B + 핀 검증 RAG)
조합 자동화 스크립트 → 데이터셋 300개 목표
데이터 충분히 쌓이면 Fine-tuning
미결 사항 (결정 필요)

 지원할 STM32 패밀리 범위 → STM32G4 계열 단일 타겟 확정
 BLDC 알고리즘 코드 → 기존 FOC/전류제어 최적화 코드 보유 확인
 주요 회로도 입력 포맷 (CSV 우선? KiCad?)
 사내 GPU 서버 보유 여부 및 VRAM 크기 → DGX Spark 128GB 확정
 기존 FOC 코드 목록 확정 및 Golden Module 등록 담당자 지정
 BLDC 6-step / DC모터 / FDCAN 모듈 신규 작성 일정
 MCSDK 라이선스 확인 (사내 학습 데이터 활용 가능 여부)
 Step 1 → Step 2 진행 조건 → errors[] 비어있어야만 Step 2 진행. 경고(warnings)는 표시 후 통과.
 제어 목표 우선순위 (속도 제어 먼저? 위치 제어 먼저?)

관련해서 내용들을 이 repo에 반영해줄래? git push까지 햊