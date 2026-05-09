# Task 05 — 5 페르소나 토론 시스템 구현

**우선순위**: 🟡 높음
**예상 소요**: 2 ~ 3일
**관련 파일**: `agent/personas/` (신규 디렉토리), `agent/step1_review_agent.py`

---

## 배경

회로 검토는 본질적으로 다관점 작업입니다 (전원/신호/모터/MCU/안전). 시니어들도 분야별로 나눠 봅니다. 하지만 LLM 다중 에이전트는 함정이 있어요:

- 모든 항목에 토론 태우면 비용/시간 폭증
- 근거 없으면 "다 좋아 보이는데요" 합의로 끝남 (흔한 실패 모드)
- Self-consistency만으로 충분한 경우도 많음

따라서 다음 원칙으로 구현:

1. **WARNING과 판단형 항목에만** 토론 적용 (ERROR는 Rule Engine 단독)
2. **모든 발언은 chunk_id 인용 강제**
3. **모더레이터가 근거 없는 의견 기각**
4. 베이스라인(self-consistency)과 비교해 토론이 정말 +α 주는지 측정

## 5 페르소나 정의

각자 자기 도메인만 보고, 도메인 외 사항은 발언 금지.

### Persona 1 — MCU/Periph Expert
- 핀 AF 매핑, 타이머 자원 충돌
- DMA 채널 할당, ADC 트리거 충돌
- 클럭 트리, 저전력 모드
- STM32G4 errata 적용 가능성
- 시스템 클럭 vs 페리페럴 클럭 일관성

### Persona 2 — Motor Control Expert
- 상보 PWM 쌍 정확성
- 데드타임 설정 (BDTR 또는 외부)
- BRK 입력 (긴급 차단 경로)
- 엔코더 인터페이스 (TIM Encoder Mode)
- Hall 센서 시퀀스 검출
- FOC vs 6-step 적정성

### Persona 3 — Power/EMI Expert
- 디커플링 캡 (값, 위치, 카운트)
- GND 플레인 분리 (analog/digital/power)
- 게이트 드라이버 부트스트랩
- 션트 저항 위치 (high-side/low-side/inline)
- 슬류 레이트, dV/dt 영향
- VDD 시퀀싱

### Persona 4 — Safety/Failsafe Expert
- 비상정지 경로 (하드웨어 vs 소프트웨어)
- 와치독 (IWDG, WWDG) 활용
- OCP/OVP/UVLO 감지 회로
- FDCAN 통신 끊김 시 자동 차단
- 단일 고장점(SPOF) 식별
- ASIL/SIL 요구사항 (해당 시)

### Persona Moderator
- 충돌 의견 조정 (예: Power가 캡 추가 권고 vs Motor가 응답성 우려)
- 근거 없는 발언 기각 ("chunk_id 없음" → 발언 무효)
- 중복 지적 통합
- 우선순위 정렬 (안전 > 기능 > 최적화)
- 최종 합의 보고서 작성

## 구현 구조

### 1. 디렉토리

```
agent/
├── step1_review_agent.py           # 메인 오케스트레이터
└── personas/
    ├── __init__.py
    ├── base.py                     # PersonaBase 추상 클래스
    ├── mcu_periph.py
    ├── motor_control.py
    ├── power_emi.py
    ├── safety_failsafe.py
    ├── moderator.py
    └── prompts/                    # 시스템 프롬프트 yaml/txt
        ├── mcu_periph.yaml
        ├── motor_control.yaml
        ├── power_emi.yaml
        ├── safety_failsafe.yaml
        └── moderator.yaml
```

### 2. PersonaBase

```python
# agent/personas/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class PersonaUtterance:
    persona_name: str
    severity: str            # "ERROR" | "WARNING" | "INFO"
    category: str
    finding: str             # 자연어 발견사항
    explanation: str         # 신입 대상 자연어 설명
    fix: str                 # 수정 가이드
    evidence_chunk_ids: list # 인용된 chunk_id (필수, 비어있으면 모더레이터가 기각)

class PersonaBase(ABC):
    def __init__(self, llm_client, persona_name, system_prompt_path):
        self.llm = llm_client
        self.name = persona_name
        self.system_prompt = load_prompt(system_prompt_path)

    @abstractmethod
    def review(
        self,
        pinmap: dict,
        prompt: str,
        rule_result: dict,
        chunks: list,
    ) -> list[PersonaUtterance]:
        ...
```

### 3. 시스템 프롬프트 템플릿 (예: motor_control.yaml)

```yaml
name: "Motor Control Expert"
focus_areas:
  - 상보 PWM 쌍 정확성
  - 데드타임 설정
  - BRK 보호 경로
  - 엔코더 인터페이스
  - Hall 센서 시퀀스

system_prompt: |
  당신은 STM32G4 기반 모터 드라이버 보드의 모터 제어 전문가입니다.

  검토 범위는 오직 다음 영역에만 한정됩니다:
  {focus_areas}

  다른 영역(전원, 통신, 안전 등)에 대해서는 발언하지 마세요.
  다른 페르소나가 그쪽을 담당합니다.

  모든 지적은 다음 형식을 지키세요:
  - finding: 무엇이 문제인지 한 문장
  - explanation: 신입 설계자가 이해할 수 있게 자연어로 설명
  - fix: 구체적 수정 방법
  - evidence_chunk_ids: 근거가 된 RAG 청크 ID 목록 (필수)

  근거 없이 발언하면 모더레이터가 무시합니다.
  컨텍스트 청크에서 관련 근거를 찾을 수 없으면 발언하지 마세요.

  심각도 분류:
  - ERROR: 회로가 정상 동작 불가
  - WARNING: 동작은 하지만 위험/비효율
  - INFO: 권장 개선

  Rule Engine이 이미 ERROR로 표시한 항목은 다시 ERROR로 발언하지 말고,
  WARNING 영역에 집중하세요.
```

### 4. Moderator 로직

```python
# agent/personas/moderator.py
class Moderator(PersonaBase):
    def synthesize(
        self,
        utterances: list[PersonaUtterance],
        rule_result: dict,
        chunks: list,
    ) -> ReviewReport:
        # 1. 근거 없는 발언 기각
        valid = [u for u in utterances if u.evidence_chunk_ids]
        rejected = [u for u in utterances if not u.evidence_chunk_ids]

        # 2. 중복 지적 통합 (같은 category + 유사 finding)
        merged = merge_duplicates(valid)

        # 3. 충돌 검출 (예: 한 페르소나는 캡 추가, 다른 페르소나는 제거 권고)
        conflicts = detect_conflicts(merged)
        for conflict in conflicts:
            resolution = self.resolve_conflict(conflict, chunks)
            merged = apply_resolution(merged, conflict, resolution)

        # 4. 우선순위 정렬
        merged.sort(key=priority_key)  # 안전 > 기능 > 최적화

        # 5. 최종 보고서
        return ReviewReport(
            errors=rule_result["errors"],
            warnings=[m for m in merged if m.severity == "WARNING"],
            suggestions=[m for m in merged if m.severity == "INFO"],
            evidence=collect_evidence(merged, chunks),
            rejected_utterances=rejected,  # 디버깅용 trace
        )
```

### 5. 메인 오케스트레이터

```python
# agent/step1_review_agent.py
def run_persona_debate(pinmap, prompt, rule_result, chunks):
    # 4 페르소나 병렬 실행 (각자 독립적으로 LLM 호출)
    personas = [
        MCUPeripheryExpert(llm),
        MotorControlExpert(llm),
        PowerEMIExpert(llm),
        SafetyExpert(llm),
    ]

    # asyncio.gather로 병렬 호출
    all_utterances = await asyncio.gather(*[
        p.review(pinmap, prompt, rule_result, chunks)
        for p in personas
    ])
    flat = [u for sublist in all_utterances for u in sublist]

    # 모더레이터가 합성
    moderator = Moderator(llm)
    return moderator.synthesize(flat, rule_result, chunks)
```

## 평가

### 베이스라인 비교

토론이 정말 가치 있는지 검증:

```python
# tests/eval_persona_vs_baseline.py
def eval():
    # Task 04에서 만든 합성 데이터로 평가
    test_cases = load_synthetic_cases()

    metrics = {
        "single_llm": [],
        "self_consistency_5": [],   # 같은 모델 5회 호출 + 다수결
        "persona_debate": [],
    }

    for case in test_cases:
        gt = case.expected_findings

        # 각 방식으로 검토
        for method in metrics:
            findings = run_review(case, method=method)
            metrics[method].append({
                "recall": compute_recall(findings, gt),
                "precision": compute_precision(findings, gt),
                "latency_s": ...,
            })

    print_table(metrics)
```

토론이 self-consistency보다 명확히 우월하지 않으면 self-consistency로 단순화 검토.

## 완료 기준

- [ ] 4개 페르소나 + 모더레이터 클래스 구현
- [ ] 시스템 프롬프트 5개 yaml 파일
- [ ] chunk_id 인용 강제 + 모더레이터 기각 로직
- [ ] 충돌 검출/해결 로직
- [ ] 비동기 병렬 호출
- [ ] 합성 데이터셋(Task 04)으로 베이스라인 비교 평가
- [ ] 토론이 self-consistency 대비 recall +5% 이상이면 채택, 아니면 단순화

## 의존성

- **Task 04 선행** (평가용 데이터 필요)
- Task 02 선행 권장 (RAG 청크 품질이 좋아야 의미 있음)
