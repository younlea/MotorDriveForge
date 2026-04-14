# STM32G4 멀티모터 제어 설계 가이드

최초 작성: 2026-04-14  
대상 칩: STM32G474RET6 (TIM1/TIM8/TIM20/HRTIM 모두 탑재)

---

## 1. 멀티모터 제어 가능성 요약

| 구성 | 모터 수 | 타이머 | 전류센싱 | OPAMP | 비고 |
|------|--------|-------|---------|-------|------|
| 기본 단일 FOC | 1 | TIM1 | 저측 3션트 | OP1~OP3 | 표준 구성 |
| 듀얼 FOC | 2 | TIM1 + TIM8 | 각 3션트 | OP1~OP6 | G474만 가능 |
| 듀얼 FOC + DC | 3 | TIM1 + TIM8 + TIM20 | 혼합 | OP1~OP6 | TIM20 = DC |
| 쿼드 DC/BLDC 6-Step | 4 | TIM1+TIM8+TIM20+HRTIM | 1션트/채널 | 공유 가능 | 핀 제약 큼 |

> **핵심 제약**: STM32G474 = OPAMP 6개, ADC 5개, TIM1/TIM8/TIM20(고급타이머) 3개 + HRTIM 1개  
> 모터 수가 늘수록 ADC 채널 배정 및 DMA 스케줄링이 병목

---

## 2. 타이머별 역할 분배 (듀얼~쿼드)

### 2.1 듀얼 모터 (권장: BLDC FOC × 2)

```
TIM1  → Motor 1: 3상 6채널 PWM (CH1/CH1N, CH2/CH2N, CH3/CH3N)
TIM8  → Motor 2: 3상 6채널 PWM (CH1/CH1N, CH2/CH2N, CH3/CH3N)
TIM3  → 두 모터 공통 속도 루프 타이머 (1kHz 인터럽트)
TIM4  → Motor 1 엔코더 카운터 (ETR 입력)
TIM5  → Motor 2 엔코더 카운터
ADC1  → Motor 1 전류 (IA, IB — OPAMP1/OPAMP2 출력)
ADC2  → Motor 1 전류 (IC — OPAMP3) + 버스전압
ADC3  → Motor 2 전류 (IA, IB — OPAMP4/OPAMP5)
ADC4  → Motor 2 전류 (IC — OPAMP6) + 온도
ADC5  → 공통 (전원 모니터링, 포텐셔미터)
FDCAN → 외부 제어 명령 수신
```

**중요 주의사항**:
- TIM1, TIM8은 각자 독립적인 BRK/BRK2 입력 → 모터별 독립 OCP 트리거 가능
- 두 타이머의 카운터 동기화: TIM1을 마스터, TIM8을 슬레이브로 설정 (TIM8 ITR0 = TIM1 TRGO)
- 동기화 안 하면 PWM 위상 어긋나 전류 측정 타이밍 불일치 발생

### 2.2 삼중 모터 (BLDC FOC × 2 + DC × 1)

```
TIM1  → Motor 1: 3상 BLDC FOC 6채널 PWM
TIM8  → Motor 2: 3상 BLDC FOC 6채널 PWM
TIM20 → Motor 3: DC 모터 H-브리지 PWM (CH1/CH1N = 정방향/역방향)
또는 TIM20 → Motor 3: 단상 BLDC 2채널 PWM
```

### 2.3 쿼드 모터 (DC 4개 또는 6-Step BLDC 4개)

```
TIM1  → Motor 1: H-브리지 or 6-Step (CH1/CH1N, CH2/CH2N)
TIM8  → Motor 2: H-브리지 or 6-Step
TIM20 → Motor 3: H-브리지 or 6-Step
HRTIM → Motor 4: HRTIM TimerA/B/C/D/E/F 중 2~4개 사용
         (HRTIM 184ps 해상도 → 고전력 변환기에 유리)
```

> FOC 4모터는 ADC + OPAMP 부족으로 STM32G474 단독으로는 불가.  
> 6-Step (Hall 또는 센서리스)이라면 OPAMP 없이 외부 션트+ADC 조합으로 가능.

---

## 3. ADC 스케줄링 전략

### 3.1 문제: ADC 채널이 모든 모터를 커버해야 함

모터당 최소 ADC 측정 항목:
- FOC: IA, IB (또는 IA, IB, IC 중 2개 + 계산), Vbus
- 6-Step: Ibus 1개 or Hall 위상

STM32G474 ADC 가용 채널:
```
ADC1: 16채널 (OP1 OUT, OP2 OUT 포함)
ADC2: 16채널 (OP3 OUT, OP4 OUT 포함)
ADC3: 16채널 (OP5 OUT, OP6 OUT 포함)
ADC4: 16채널 (OP6 OUT 공유)
ADC5: 4채널 (Vbat, 온도 센서 포함)
```

### 3.2 듀얼 모터 ADC 트리거 타이밍

```
TIM1 PWM Center (TRGO2 → ADC1/ADC2 주입 채널 트리거)
TIM8 PWM Center (TRGO2 → ADC3/ADC4 주입 채널 트리거)
```

PWM 중앙에서 ADC 트리거 = 스위칭 노이즈 최소 구간에서 샘플링  
두 타이머를 동기화하면 ADC 트리거도 정렬됨.

### 3.3 DMA 스케줄링

```
DMA1 CH1 → ADC1 주입 (Motor 1 전류)
DMA1 CH2 → ADC2 주입 (Motor 1 전류 + Vbus)
DMA2 CH1 → ADC3 주입 (Motor 2 전류)
DMA2 CH2 → ADC4 주입 (Motor 2 전류 + 온도)
DMAMUX   → ADC 트리거 소스 매핑 관리
```

---

## 4. 핀맵 충돌 주의 항목

### 4.1 TIM1 vs TIM8 핀 배치 (STM32G474RE — LQFP64)

| 신호 | TIM1 핀 (AF6) | TIM8 핀 (AF5) |
|------|-------------|-------------|
| CH1  | PA8 | PC6 |
| CH1N | PA7 / PB13 | PA5 / PB3 |
| CH2  | PA9 | PC7 |
| CH2N | PB0 / PB14 | PB0 / PC10 |
| CH3  | PA10 | PC8 |
| CH3N | PB1 / PB15 | PB1 / PC11 |
| CH4  | PA11 | PC9 |
| BRK  | PA6 / PB12 | PA6 / PB4 |
| BRK2 | PG9 / PE15 | PB6 / PC9 |

> **PB0, PB1**: TIM1과 TIM8이 같은 핀 공유 불가 → 반드시 한 타이머만 사용  
> **BRK 신호**: 두 타이머 OCP를 같은 핀으로 묶으면 의도치 않은 셧다운 발생 가능 → 분리 권장

### 4.2 OPAMP 핀 (전류센싱)

| OPAMP | INP | INM | OUT |
|-------|-----|-----|-----|
| OP1 | PA1 | PA3 | PA2 |
| OP2 | PA7 | PC5 | PA6 |
| OP3 | PB0 | PB2 | PB1 |
| OP4 | PB13 | PB11 | PB12 |
| OP5 | PB14 | PB10 | PB15 |
| OP6 | PB11 | PB10 | PC7 (ADC3_IN9) |

> OP2 OUT = PA6 = TIM1 BRK 핀! OCP 비교기 출력이 BRK와 겹치면 오동작  
> OP6 OUT = PC7 = TIM8 CH2 → 전류센싱과 PWM 핀 충돌 → PC7 대신 PB12로 라우팅

---

## 5. 멀티모터 회로 설계 체크리스트

### 5.1 전원 설계

- [ ] 각 모터 인버터 전원 레일 분리 (공통 GND, 독립 VCC 권장)
- [ ] 모터별 독립 게이트드라이버 전원 (15V or 12V)
- [ ] MCU 3.3V 전원: LDO별 100nF + 10µF 디커플링
- [ ] OPAMP VDD별 100nF 디커플링 (VDDA 핀 근처 배치)
- [ ] 고측 부트스트랩 커패시터: 각 암마다 100~220nF (예: CBOOT)
- [ ] 공통 버스 커패시터: 최소 100µF/모터 (고주파 필름 + 전해 병렬)

### 5.2 신호 배선

- [ ] PWM 신호선: 게이트드라이버까지 5cm 이하, 트위스트 or 차폐
- [ ] 전류센싱 션트: 전력 루프 안에 배치 (기생 인덕턴스 최소화)
- [ ] OPAMP INP/INM: 차동 배선 등장 (동일 길이, 인접 라우팅)
- [ ] 인코더/Hall 신호: 슈미트 트리거 + RC 필터 (100Ω + 10nF)
- [ ] BRK 입력: 글리치 필터 추가 (MCU 내부 디지털 필터 or 외부 RC)

### 5.3 소프트웨어 자원 배분

- [ ] 두 FOC 루프는 같은 주기(예: 20kHz)로 동작하는지 확인
- [ ] TIM1/TIM8 카운터 위상 동기화 설정
- [ ] ADC 주입 채널 트리거 소스 각 타이머에 독립 배정
- [ ] DMA 완료 인터럽트: 모터별 핸들러 분리, 공유 변수 보호
- [ ] FDCAN 명령 수신 시 모터 인덱스(0~3) 명시적으로 라우팅
- [ ] 속도/전류 PID 게인 모터별 독립 저장 (Flash or EEPROM per motor)

---

## 6. X-CUBE-MCSDK 듀얼 모터 설정

X-CUBE-MCSDK v6에서 듀얼 모터 설정 순서:

```
1. Motor Control Workbench 실행
2. "New Project" → Motor Count = 2
3. Motor 1: 
   - Drive: 3-phase FOC
   - Topology: ICS (내부 OPAMP 전류센싱) or Three shunts
   - PWM Timer: TIM1
   - Encoder/Hall 타이머 선택
4. Motor 2:
   - Drive: 3-phase FOC 또는 6-Step
   - PWM Timer: TIM8 (TIM1과 독립 선택 필수)
   - 동기화: "Synchronized PWM" 체크 시 TIM8가 TIM1 TRGO에 슬레이브
5. Safety:
   - OCP 임계값 모터별 독립 설정 (BRK 핀 분리)
6. Generate → CubeMX .ioc 연동 → 코드 생성
```

---

## 7. 레퍼런스 설계

| 보드 | 모터 수 | 핵심 특징 | 문서 |
|------|--------|---------|------|
| EVSPIN32G4 | 1 | STSPIN32G4 통합 IC | UM2850 |
| EVSPIN32G4-DUAL | 2 | STSPIN32G4 × 2 | UM2896 + 회로도 |
| STEVAL-SPIN3201 | 2 | 이중 브러시리스 | UM2719 |
| flatmcu (GitHub) | 1 | KiCad 오픈소스 FOC | GyrocopterLLC/flatmcu |

---

## 8. 에이전트 회로 리뷰 시 체크 포인트 (멀티모터 전용)

에이전트가 회로도/핀맵을 받았을 때 추가로 검사해야 할 항목:

1. **타이머 중복 배정**: 같은 TIM이 두 모터에 할당되어 있으면 오류
2. **PB0/PB1 핀 충돌**: TIM1 CH3N과 TIM8 CH2N이 같은 핀인지 확인
3. **OPAMP 수 초과**: FOC × 3 이상은 OPAMP 6개를 초과 → 외부 OPAMP 필요
4. **BRK 핀 공유**: 두 모터의 OCP가 같은 BRK 핀이면 경고 (독립 보호 불가)
5. **ADC 트리거 충돌**: 같은 ADC에 두 타이머가 동시에 트리거하면 데이터 오염
6. **DMA 채널 초과**: G4 DMA1 = 8채널, DMA2 = 8채널 — 총 16채널 한도
7. **전류 루프 개수**: 20kHz FOC × 2모터 = 인터럽트 40kHz 상당 → CPU 부하 검증 필요
8. **FPU 사용 여부**: Cortex-M4 FPU 활성화 안 하면 FOC 연산 속도 부족
