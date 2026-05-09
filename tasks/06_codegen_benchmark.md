# Task 06 — Step 3 코드 생성 모델 A/B 벤치마크

**우선순위**: 🟢 중간
**예상 소요**: 1 ~ 2일
**관련 파일**: `agent/codegen/`, `tests/eval_codegen.py`

---

## 배경

Step 3 코드 생성에 현재 Gemma 4 26B MoE를 1차로 두고 있지만, 코드 특화 모델인 **Qwen3-Coder 30B A3B**가 STM32 HAL 같은 정형화된 패턴엔 더 강할 가능성이 큽니다.

MoE의 active 4B는 빠르지만 정확도가 dense 코더 모델보다 낮을 때가 많고, Step 3는 Golden Module 주입 방식이라 **HAL API 정확성**이 핵심입니다.

검증 없이 한쪽 채택하지 말고 평가셋으로 비교 후 결정.

## 평가셋 구성

`tests/eval/codegen_cases/`에 케이스 보관. 각 케이스:

```json
{
  "case_id": "bldc_hall_g474_001",
  "input": {
    "chip": "STM32G474RET6",
    "pinmap": [...],
    "natural_request": "BLDC 1개를 Hall 6-step으로 구동, FDCAN으로 명령 받기"
  },
  "golden_modules_to_use": ["bldc_6step_hall.c", "fdcan_motor_cmd.c"],
  "expected_substitutions": {
    "htim_pwm": "&htim1",
    "hall_gpio": "GPIOA",
    "hall_pin_a": "GPIO_PIN_0",
    "fdcan_handle": "&hfdcan1"
  },
  "expected_user_code_blocks": [
    {
      "marker": "/* USER CODE BEGIN 2 */",
      "must_contain": ["BLDC_Init", "FDCAN_Start"]
    }
  ]
}
```

최소 30개 케이스 권장 (모터 타입 × 센서 조합 × 통신 조합).

## 평가 지표

| 지표 | 정의 | 가중치 |
|---|---|---|
| **HAL API 정확도** | 사용한 HAL 함수 시그니처가 실제 G4 HAL과 일치 | 30% |
| **Golden Module 충실도** | 적응 결과가 원본 의도 보존 | 25% |
| **Pinmap 치환 정확도** | TIM/GPIO/AF가 사용자 입력과 일치 | 20% |
| **컴파일 통과율** | arm-none-eabi-gcc 빌드 성공 | 15% |
| **레이턴시** | 토큰/초 + 첫 토큰까지 시간 | 10% |

## 평가 스크립트

```python
# tests/eval_codegen.py
@dataclass
class CodegenResult:
    case_id: str
    model: str
    generated_code: str
    metrics: dict
    elapsed_s: float

def eval_model(model_name: str, cases: list) -> list[CodegenResult]:
    results = []
    for case in cases:
        start = time.time()
        code = generate_code(model_name, case)
        elapsed = time.time() - start

        metrics = {
            "hal_api_accuracy": check_hal_apis(code, case.chip),
            "golden_fidelity": check_golden_preservation(code, case),
            "pinmap_substitution": check_substitutions(code, case.expected_substitutions),
            "compiles": try_compile(code, case.chip),
            "latency_s": elapsed,
        }
        results.append(CodegenResult(case.id, model_name, code, metrics, elapsed))
    return results

def main():
    cases = load_cases("tests/eval/codegen_cases/*.json")

    print("Evaluating Gemma 4 26B MoE...")
    gemma_results = eval_model("gemma4:26b", cases)

    print("Evaluating Qwen3-Coder 30B A3B...")
    qwen_results = eval_model("qwen3-coder:30b-a3b", cases)

    print_comparison_table(gemma_results, qwen_results)
    save_to_csv(gemma_results + qwen_results, "tests/eval/codegen_results.csv")
```

## 검증 도구

### HAL API 정확도

ST 공식 헤더에서 함수 시그니처 추출 후 매칭:

```python
HAL_FUNCTIONS = parse_hal_headers("dataset/opensource/STM32CubeG4/Drivers/STM32G4xx_HAL_Driver/Inc/")

def check_hal_apis(code: str, chip: str) -> float:
    used_apis = extract_function_calls(code)
    valid = sum(1 for api in used_apis if matches_hal(api, HAL_FUNCTIONS))
    return valid / len(used_apis) if used_apis else 0
```

### 컴파일 검증

```bash
# Docker로 격리 빌드 (의존성 없는 환경에서)
docker run --rm -v $(pwd):/work \
    arm-none-eabi-gcc:latest \
    sh -c "cd /work && make"
```

## 결과 적용

```
if Qwen.f1 > Gemma.f1 + 0.05:
    primary = Qwen3-Coder
elif Qwen.latency < Gemma.latency * 0.7 and Qwen.f1 >= Gemma.f1 - 0.02:
    primary = Qwen3-Coder    # 거의 비슷한 정확도면 빠른 쪽
else:
    primary = Gemma 4 26B
```

결정 결과를 `ARCHITECTURE.md`와 `CLAUDE.md`에 반영.

## 완료 기준

- [ ] 평가 케이스 30개 이상 작성
- [ ] HAL API 검증 도구
- [ ] 컴파일 검증 환경
- [ ] 두 모델 평가 실행
- [ ] 결과 CSV + 비교 표
- [ ] `ARCHITECTURE.md`, `CLAUDE.md`에 채택 모델 반영

## 후속 고려

- Phase-2: 채택 모델에 QLoRA — Golden Module + 사내 코드 스타일 학습
- 평가셋은 회귀 테스트로 CI에 통합
