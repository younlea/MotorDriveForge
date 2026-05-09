# Task 04 — 합성 망가진 스키매틱 파이프라인

**우선순위**: 🟡 높음 (QLoRA 학습 데이터 부트스트래핑)
**예상 소요**: 3 ~ 5일
**관련 파일**: `scripts/mutate_evm.py` (신규), `dataset/synthetic/`

---

## 배경

QLoRA 학습용 사내 에러 사례가 부족합니다. 해결책: **검증된 EVM/레퍼런스 스키매틱(정답)을 자동으로 망가뜨려 학습 페어 합성**.

EVM 정답 1개에 12개 변형 적용 → 12개 라벨링된 학습 페어. EVM 20개면 240개 페어. 자동 라벨이라 일관성도 보장.

## 12 Mutation Rules (1차 정의)

| # | Mutation | 예상 검출 카테고리 |
|---|---|---|
| M01 | 디커플링 캡 제거 (VDD/AVDD) | 전원 인프라 |
| M02 | 디커플링 캡 값 변경 (100nF → 1nF) | 전원 인프라 |
| M03 | 부트스트랩 다이오드 방향 반전 | 게이트 드라이버 |
| M04 | 게이트 저항 누락 | 게이트 드라이버 |
| M05 | 데드타임 제어 회로 제거 (외부 dead-time IC 사용 시) | 모터 논리 |
| M06 | BOOT0 풀다운 누락 (floating) | MCU 인프라 |
| M07 | VCAP 캡 누락 또는 값 오류 | MCU 인프라 |
| M08 | ADC 입력 RC 필터 누락 | 신호 무결성 |
| M09 | Hall 센서 풀업 누락 | 센서 인터페이스 |
| M10 | FDCAN 종단 저항 누락/위치 오류 | 통신 |
| M11 | SPI CSN 풀업 누락 | 통신 |
| M12 | Motor 출력 TVS/스너버 누락 | EMI/보호 |

## 입력: EVM 시드 라이브러리

`dataset/synthetic/seeds/`에 검증된 정답 스키매틱:

| 시드 | 출처 | 모터 타입 |
|---|---|---|
| `drv8353rh_evm.json` | TI EVM (BOM + netlist) | BLDC FOC |
| `odrive_v3_6.json` | ODrive 오픈소스 | BLDC dual |
| `vesc_6_4.json` | VESC 오픈소스 | BLDC FOC |
| `moteus_r4_11.json` | moteus 오픈소스 | 로봇 액추에이터 |
| `b_g431b_esc1.json` | ST 공식 EVM | BLDC FOC |
| `simplefoc_shield.json` | SimpleFOC | BLDC/stepper |

KiCad 파일 직접 파싱은 어려우니 **중간 표현(JSON 넷리스트 + 부품 리스트)**로 정규화. SKiDL 라이브러리 활용 검토.

## 합성 출력 스키마

```json
{
  "synthetic_id": "drv8353rh_evm__M03__001",
  "source_seed": "drv8353rh_evm",
  "mutation_id": "M03",
  "mutation_description": "Bootstrap diode D1 direction reversed (anode/cathode swapped)",
  "broken_schematic": { /* 변형된 넷리스트 */ },
  "ground_truth": {
    "original_schematic": { /* 원본 넷리스트 */ },
    "diff": { "components": [...], "nets": [...] },
    "expected_findings": [
      {
        "severity": "ERROR",
        "category": "gate_driver",
        "title": "Bootstrap diode D1 direction reversed",
        "explanation": "부트스트랩 다이오드 D1의 애노드/캐소드가 반전되어 있습니다. 결과적으로 부트스트랩 캡이 충전되지 않아 high-side MOSFET이 켜지지 않습니다.",
        "fix": "D1을 뒤집어 애노드를 VDD에, 캐소드를 HB 핀에 연결하세요.",
        "evidence_keywords": ["bootstrap", "high-side gate driver", "DRV8353"]
      }
    ]
  }
}
```

## 구현 단계

### 1. EVM 시드 정규화

```bash
python scripts/normalize_seed.py \
    --kicad-project dataset/opensource/ODriveHardware/ODrive_v3.6.kicad_pro \
    --out dataset/synthetic/seeds/odrive_v3_6.json
```

KiCad 직접 파싱이 까다로우면 BOM CSV + netlist export(.net)에서 시작.

### 2. Mutation 적용

```bash
python scripts/mutate_evm.py \
    --seed dataset/synthetic/seeds/drv8353rh_evm.json \
    --rules all \
    --variations-per-rule 3 \
    --out dataset/synthetic/pairs/
```

각 mutation은 결정론적 + 약간의 랜덤 (예: 디커플링 캡 값 변경 시 1nF/10nF/100uF 중 랜덤).

### 3. 자연어 설명 보강

mutation 자체는 결정론적이지만, "왜 문제고 어떻게 고치라"의 자연어 설명은 LLM으로 보강:

```python
def generate_explanation(mutation, broken_schematic, original):
    prompt = f"""
    원본 회로의 정상 부분: {get_relevant_section(original, mutation)}
    변형된 부분: {get_relevant_section(broken_schematic, mutation)}
    적용된 변형: {mutation.description}

    이 변형이 모터 드라이버 회로에서 일으키는 문제를 신입 회로 설계자가
    이해할 수 있게 설명하라. 그리고 수정 방법을 제시하라.
    """
    return ollama.generate("gemma4:31b", prompt)
```

### 4. 학습 데이터 포맷 변환

QLoRA 학습용 instruction 포맷:

```json
{
  "instruction": "다음 STM32G4 모터 드라이버 회로를 검토하라.",
  "input": "{ pinmap + netlist 요약 }",
  "output": "{ expected_findings[]를 자연어 리포트로 직렬화 }"
}
```

## 후속 작업

이 데이터셋이 준비되면:
- **Task 07: QLoRA 학습** 가능해짐
- RAG 검색 평가셋 (정답이 있으니 recall@k 측정 가능)
- 페르소나 토론 정확도 벤치마크

## 완료 기준

- [ ] 시드 라이브러리 6개 이상 정규화
- [ ] 12개 mutation rule 모두 구현
- [ ] 시드 6 × mutation 12 × variation 3 = 216개 이상 페어 생성
- [ ] LLM 자연어 설명 보강 파이프라인 동작
- [ ] QLoRA 포맷 변환 스크립트
- [ ] 샘플 10개 수동 검증 (mutation이 의도대로, 라벨이 정확한지)

## 주의사항

- **시드 출처 라이선스 확인 필수** — ODrive(MIT), VESC(GPL3), moteus(Apache 2.0). 합성 데이터의 라이선스는 시드를 따름.
- 합성 데이터로만 학습하면 distribution gap 발생 가능 — 실제 사내 데이터와 혼합 학습 필요.
