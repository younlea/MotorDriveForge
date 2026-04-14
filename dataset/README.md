# Dataset — STM32G4 모터 드라이브 Agent 학습 데이터

이 폴더는 STM32G4 기반 모터 드라이브 회로 설계 리뷰 Agent의 RAG 지식베이스 구축을 위한 데이터를 관리한다.

---

## 폴더 구조

```
dataset/
├── download_st_docs.sh          ← ST 공식 PDF 다운로드 스크립트 (인터넷 PC에서 실행)
├── official_docs/               ← ST 공식 문서 (별도 다운로드 필요)
│   ├── reference_manual/        RM0440 레퍼런스 매뉴얼
│   ├── datasheets/              STM32G474, G431, STSPIN32G4 데이터시트
│   ├── application_notes/       AN 시리즈 (전류센싱, Bootstrap, PWM, HRTIM 등)
│   ├── eval_boards/             평가보드 매뉴얼 + 회로도 (EVSPIN32G4, SPIN3201)
│   └── sdk_docs/                X-CUBE-MCSDK Workbench 사용설명서
├── opensource/                  ← GitHub 오픈소스 (자동 클론됨)
│   ├── flatmcu/                 STM32G473 FOC 컨트롤러 (KiCad 회로도 포함)
│   └── STM32CubeG4/             ST 공식 HAL 예제 (HRTIM, TIM, ADC, OPAMP, FDCAN, CORDIC)
└── multi_motor/                 ← 멀티모터 전용 자료
    └── multi_motor_stm32g4_guide.md   2~4모터 설계 가이드
```

---

## ST 공식 문서 다운로드 방법

ST 서버는 특정 환경에서 직접 접근이 차단될 수 있다.  
**인터넷이 연결된 PC 또는 DGX Spark에서** 아래를 실행:

```bash
cd dataset/
chmod +x download_st_docs.sh
./download_st_docs.sh
```

또는 아래 URL에서 수동 다운로드 후 `official_docs/` 하위 폴더에 배치.

---

## 수집 문서 목록

### Reference Manual & Datasheet

| 파일명 | 설명 | 직접 다운로드 URL |
|--------|------|-----------------|
| RM0440_STM32G4_Reference_Manual.pdf | STM32G4 레퍼런스 매뉴얼 (1,000p+) | [st.com](https://www.st.com/resource/en/reference_manual/rm0440-stm32g4-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf) |
| STM32G474_datasheet.pdf | STM32G474 데이터시트 | [st.com](https://www.st.com/resource/en/datasheet/stm32g474re.pdf) |
| STM32G431_datasheet.pdf | STM32G431 데이터시트 | [st.com](https://www.st.com/resource/en/datasheet/stm32g431rb.pdf) |
| STSPIN32G4_datasheet.pdf | STSPIN32G4 통합 모터드라이버 | [st.com](https://www.st.com/resource/en/datasheet/stspin32g4.pdf) |

### Application Notes (핵심)

| 파일명 | 내용 | URL |
|--------|------|-----|
| AN5306_OPAMP_current_sensing.pdf | STM32G4 OPAMP / 션트 전류센싱, PGA 설정 | [st.com](https://www.st.com/resource/en/application_note/an5306-operational-amplifier-opamp-usage-in-stm32g4-series-stmicroelectronics.pdf) |
| AN5789_bootstrap_circuit_design.pdf | Bootstrap 회로 (부트스트랩 다이오드, CBOOT 계산) | [st.com](https://www.st.com/resource/en/application_note/an5789-considerations-on-bootstrap-circuitry-for-gate-drivers-stmicroelectronics.pdf) |
| AN4277_PWM_shutdown_protection.pdf | PWM 셧다운, BRK/BRK2, OCP/OVP 보호 | [st.com](https://www.st.com/resource/en/application_note/an4277-how-to-use-pwm-shutdown-for-motor-control-and-digital-power-conversion-on-stm32-mcus-stmicroelectronics.pdf) |
| AN4539_HRTIM_cookbook.pdf | HRTIM (184ps 해상도 PWM, 데드타임, 복잡 파형) | [st.com](https://www.st.com/resource/en/application_note/an4539-hrtim-cookbook-stmicroelectronics.pdf) |
| AN4220_sensorless_6step_BLDC.pdf | 센서리스 6-Step BLDC, Back-EMF 감지 | [st.com](https://www.st.com/resource/en/application_note/an4220-sensorless-sixstep-bldc-commutation-stmicroelectronics.pdf) |
| AN4835_highside_current_sensing.pdf | 고측 전류센싱 (High-side, 공통모드 전압) | [st.com](https://www.st.com/resource/en/application_note/an4835-highside-current-sensing-for-applications-using-high-commonmode-voltage-stmicroelectronics.pdf) |
| AN5036_thermal_management.pdf | 열 관리 (접합온도 125°C, PCB 방열) | [st.com](https://www.st.com/resource/en/application_note/an5036-guidelines-for-thermal-management-on-stm32-applications-stmicroelectronics.pdf) |
| AN4938_hardware_development_guide.pdf | STM32 하드웨어 개발 가이드 (전원, 디커플링) | [st.com](https://www.st.com/resource/en/application_note/an4938-getting-started-with-stm32-mcu-hardware-development-stmicroelectronics.pdf) |
| AN4013_timer_overview.pdf | STM32 타이머 전체 개요 | [st.com](https://www.st.com/resource/en/application_note/an4013-stm32-cross-series-timer-overview-stmicroelectronics.pdf) |

### Evaluation Board / Reference Design

| 파일명 | 내용 | URL |
|--------|------|-----|
| UM2850_EVSPIN32G4_user_manual.pdf | EVSPIN32G4 단일 모터 평가보드 | [st.com](https://www.st.com/resource/en/user_manual/um2850-getting-started-with-the-evspin32g4-evspin32g4nh-stmicroelectronics.pdf) |
| UM2896_EVSPIN32G4_DUAL_user_manual.pdf | EVSPIN32G4-DUAL **이중 모터** 평가보드 | [st.com](https://www.st.com/resource/en/user_manual/um2896-getting-started-with-the-evspin32g4dual--stmicroelectronics.pdf) |
| EVSPIN32G4_DUAL_schematics.pdf | EVSPIN32G4-DUAL 회로도 (레퍼런스 설계) | [st.com](https://www.st.com/resource/en/schematic_pack/evspin32g4-dual-schematics.pdf) |
| UM2719_STEVAL_SPIN3201_dual_motor.pdf | STEVAL-SPIN3201 이중 BLDC 평가보드 | [st.com](https://www.st.com/resource/en/user_manual/um2719-getting-started-with-the-steval-spin3201-stmicroelectronics.pdf) |
| STEVAL_SPIN3201_schematics.pdf | STEVAL-SPIN3201 회로도 | [st.com](https://www.st.com/resource/en/schematic_pack/steval-spin3201-schematic.pdf) |

### SDK Docs

| 파일명 | 내용 | URL |
|--------|------|-----|
| UM3027_MCSDK_v6_workbench.pdf | X-CUBE-MCSDK v6 Workbench 사용설명서 | [st.com](https://www.st.com/resource/en/user_manual/um3027-how-to-use-stm32-motor-control-sdk-v60-workbench-stmicroelectronics.pdf) |
| UM2538_FOC_algorithm_pack.pdf | STM32 Motor Control Pack FOC 알고리즘 | [st.com](https://www.st.com/resource/en/user_manual/um2538-stm32-motorcontrol-pack-using-the-foc-algorithm-for-threephase-lowvoltage-and-lowcurrent-motor-evaluation-stmicroelectronics.pdf) |

---

## 오픈소스 코드 (자동 다운로드)

### flatmcu (`opensource/flatmcu/`)
- STM32G473CB 기반 3상 FOC BLDC 컨트롤러
- **KiCad 회로도 포함** (`design/` 폴더): MCU.kicad_sch, ThreePhaseBridge.kicad_sch
- 게이트드라이버, 부트스트랩, 3-션트 전류센싱 실제 구현
- 출처: https://github.com/GyrocopterLLC/flatmcu

### STM32CubeG4 예제 (`opensource/STM32CubeG4/`)

ST 공식 HAL 코드 예제 (아래 항목 sparse checkout):

| 경로 | 내용 |
|------|------|
| `Projects/NUCLEO-G474RE/Examples_LL/HRTIM/` | HRTIM 파형생성, CBC 데드타임, Multiple PWM |
| `Projects/NUCLEO-G474RE/Examples_LL/TIM/` | 타이머 PWM, BreakAndDeadtime, InputCapture, DMA |
| `Projects/NUCLEO-G431RB/Examples/` | G431 기본 예제 |
| `Projects/STM32G474E-EVAL/Examples/FDCAN/` | FDCAN 통신 예제 |
| `Projects/STM32G474E-EVAL/Examples/ADC/` | ADC 보정, 주입채널, 연속변환 |
| `Projects/STM32G474E-EVAL/Examples/OPAMP/` | OPAMP 타이머 제어 먹스, PGA, 캘리브레이션 |
| `Projects/STM32G474E-EVAL/Examples/CORDIC/` | CORDIC Sin/Cos DMA (FOC 연산 가속) |

---

## 멀티모터 전용 자료

`multi_motor/multi_motor_stm32g4_guide.md` 참조.

**핵심 요약**:
- STM32G474 단일 칩으로 **FOC 2모터** (TIM1 + TIM8 + OPAMP 6개)
- **FOC 2 + DC 1** = TIM1 + TIM8 + TIM20
- **DC/6-Step 최대 4모터** = TIM1 + TIM8 + TIM20 + HRTIM
- FOC 3모터 이상은 OPAMP 6개 초과 → 외부 OPAMP 필요
- TIM1/TIM8 카운터 동기화 필수 (마스터-슬레이브 연결)
- PB0, PB1: TIM1 CH3N ↔ TIM8 CH2N 핀 충돌 주의

---

## RAG 청킹 전략

| 문서 유형 | 청킹 단위 | 메타데이터 |
|-----------|---------|-----------|
| RM0440 | 섹션(챕터) 단위 | doc_type=reference, peripheral=TIM/ADC/OPAMP/... |
| Application Note | 회로 블록 단위 | doc_type=AN, topic=bootstrap/current_sense/protection |
| 평가보드 회로도 | 서브시스템 단위 | doc_type=schematic, board=EVSPIN32G4_DUAL |
| 예제 코드 | 함수 단위 | doc_type=code, peripheral=HRTIM, example=CBC_Deadtime |
| 멀티모터 가이드 | 섹션 단위 | doc_type=design_guide, motor_count=2~4 |
| 커뮤니티 Q&A | Q+A 쌍 1개 | doc_type=qa, topic=overcurrent/deadtime/encoder |

---

## 추가 수집 권장 (수동)

ST 서버 외 수집 가능 자료:

| 자료 | URL | 우선도 |
|------|-----|--------|
| ST 모터제어 커뮤니티 포럼 (에러 Q&A) | https://community.st.com/t5/stm32-mcus-motor-control/ | 높음 |
| SimpleFOC 문서 | https://docs.simplefoc.com/ | 중간 |
| EmbeddedExpertIO 블로그 | https://blog.embeddedexpert.io/ | 중간 |
| CORDIC/FMAC 수학 라이브러리 문서 | https://arm-software.github.io/CMSIS_5/DSP/html/ | 낮음 |
