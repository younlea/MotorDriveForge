STM32G4 Agent — 어펜딕스

최초 작성: 2026-04-10 마지막 수정: 2026-04-27 (모델 Gemma-4 변경, 실제 데이터셋 반영, Step 2 워크플로우 반영)
목차

A. 모델별 학습 데이터 수집 가이드
B. 모델 학습 방법 상세
C. 웹 애플리케이션 개발 프로세스
A. 모델별 학습 데이터 수집 가이드

A-1. Step 1 — HW 설계 검증 Agent (RAG 지식베이스)

수집 대상 문서 목록 — 실제 수집 현황

① CubeMX XML 핀 데이터베이스 (최우선)

경로: C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeMX\db\mcu\

수집 대상 파일 (STM32G4 전체):
  STM32G431C6Tx.xml     STM32G431C8Tx.xml     STM32G431CBTx.xml
  STM32G431K6Tx.xml     STM32G431K8Tx.xml     STM32G431KBTx.xml
  STM32G431R6Tx.xml     STM32G431R8Tx.xml     STM32G431RBTx.xml
  STM32G474CETx.xml     STM32G474MEYx.xml     STM32G474PETx.xml
  STM32G474QETx.xml     STM32G474RETx.xml     STM32G474VETx.xml
  STM32G491CETx.xml     STM32G491KETx.xml     STM32G491RETx.xml
  (총 40~50개 파일)

처리 방식: RAG 불필요 — Python으로 파싱해 JSON DB 직접 구축
결과물: pin_af_db.json  (약 500KB)
상태: ⬜ X-CUBE-MCSDK 설치 후 수집 필요
# 파싱 스크립트 예시
import xml.etree.ElementTree as ET, json, glob

def build_pin_db(xml_dir: str) -> dict:
    db = {}
    for path in glob.glob(f"{xml_dir}/STM32G4*.xml"):
        tree = ET.parse(path)
        root = tree.getroot()
        chip = root.get("RefName")          # e.g. "STM32G474RETx"
        db[chip] = {}
        for pin in root.findall(".//Pin"):
            name = pin.get("Name")          # e.g. "PA8"
            sigs = [s.get("Name") for s in pin.findall("Signal")
                    if s.get("Name") != "GPIO"]
            afs  = {s.get("Name"): s.get("Af","") for s in pin.findall("Signal")}
            db[chip][name] = {"signals": sigs, "af": afs}
    return db

db = build_pin_db(r"C:\...\STM32CubeMX\db\mcu")
json.dump(db, open("pin_af_db.json","w"), indent=2)
② ST 공식 문서 (PDF → RAG) — ✅ 14건 수집 완료 (55MB)

실제 수집 파일 (dataset/official_docs/):
  rm0440-stm32g4-series-*.pdf                  (39MB, 레퍼런스 매뉴얼)
  stm32g474re.pdf                              (3MB, 데이터시트)
  dm00445657-getting-started-*.pdf             (4.3MB, HW 개발 가이드 AN5031)
  an5306-operational-amplifier-*.pdf           (907KB, OPAMP 전류 센싱)
  an5346-stm32g4-adc-*.pdf                    (201KB, ADC 최적화)
  an5348-fdcan-*.pdf                           (888KB, FDCAN 가이드)
  an3070-managing-the-driver-*.pdf             (194KB, RS-485 DE핀)
  evspin32g4-dual-schematics.pdf              (299KB, 다축 레퍼런스 회로도)
  evspin32g4-dual.pdf                          (1.2MB, 평가보드 매뉴얼)
  evspin32g4-dual-bom.pdf                      (197KB, BOM)
  evspin32g4-dual-manufacturing.zip            (645KB, 제조 데이터)
  um3027-*.pdf                                 (3.9MB, MCSDK v6 Workbench)
  um3016-*.pdf                                 (1.2MB, MCSDK v6 Profiler)
  64361889.pdf                                 (1.2MB, 추가 문서)

청킹 전략:
  - 핀 테이블 행 단위: 1행 = 1청크
  - 설명 섹션: 512토큰 단위, 64토큰 overlap
  - 툴: pdfplumber (테이블 추출), PyMuPDF (텍스트)
③ 오픈소스 회로도 및 코드 — "정답 레퍼런스" (8개 프로젝트)

✅ 실파일 다운로드 완료:
  flatmcu (12MB)        — STM32G473CB FOC KiCad 회로도, 3상 브릿지 설계
  STM32CubeG4 (43MB)    — ST 공식 HAL 예제 (HRTIM, TIM, ADC, OPAMP, FDCAN, CORDIC)

⬜ Git Submodule 등록 완료, 초기화 필요 (git submodule update --init):
  Arduino-FOC           — SimpleFOC, 다양한 환경 포팅 STM32G4 핀 할당 지침
  stm32-esc             — B-G431B-ESC1 보드 맞춤형 최적화 레지스터 세팅
  moteus (mjbots)       — 고정밀 로봇 관절 제어 STM32G4 액추에이터 레퍼런스
  MESC_FOC_ESC          — 하이엔드 드론/모빌리티 대전력 설계 핀 충돌 회피
  bldc_vesc (VESC)      — 세계 표준 오픈소스 ESC, 다양한 모터 토폴로지 레퍼런스
  ODriveHardware        — 글로벌 산업 표준 FOC 아날로그 결선 규칙

청킹 전략:
  - C/H 파일: 함수 단위 청킹 (정규식 함수 경계 탐지)
  - KiCad 회로도: 서브시스템 단위 (MCU, ThreePhaseBridge, GateDriver 등)
  - 메타데이터: {source, file, module_type, g4_accel_used}
④ ST 포럼 Q&A — 에러 사례

상태: ⬜ st_forum_qa.jsonl 생성 예정 (현재 0건)
파이프라인: scripts/scrape_st_forum.py --max-items 300
청킹: Q+A 쌍 1개 = 1 청크
최종 RAG DB 규모 (Step 1)

소스	청크 수	상태
CubeMX XML → JSON DB	—	⬜ (규칙 엔진 직접 조회)
데이터시트/AN 등 공식 PDF	~1,500	✅ 14건 수집 완료
오픈소스 코드/회로도	~800	✅ 2건 / ⬜ 6건
포럼 에러 사례	~300	⬜ 수집 예정
합계	~2,600 청크	
A-2. Step 1 — HW 설계 검증 Agent (Fine-tuning 데이터)

규칙 엔진 + 오픈소스 회로도로 정답을 자동 생성할 수 있어 대부분 자동화 가능.
데이터 형식

// 입력
{
  "instruction": "STM32G474RET6 회로도를 검증해줘.",
  "input": {
    "chip": "STM32G474RET6",
    "prompt": "BLDC 1축 FOC, 엔코더, FDCAN 1Mbps, 내부 OPAMP",
    "pins": [
      {"pin": "PA8",  "function": "TIM1_CH1",  "af": "AF6"},
      {"pin": "PA9",  "function": "TIM1_CH2",  "af": "AF7"},
      {"pin": "PB9",  "function": "FDCAN1_TX", "af": "AF9"}
    ]
  }
}

// 출력 (정답) — 3계층 검증 결과
{
  "output": {
    "validation": "FAIL",
    "infra_check": {"VDD": "OK", "VCAP": "OK", "BOOT0": "OK", "SWD": "OK"},
    "motor_logic_check": {
      "complementary_pwm": "FAIL — PA9(TIM1_CH2)의 하단은 TIM1_CH2N이어야 함"
    },
    "errors": [
      {
        "layer": "motor_logic",
        "pin": "PA9",
        "assigned_function": "TIM1_CH2",
        "assigned_af": "AF7",
        "correct_af": "AF6",
        "message": "PA9의 TIM1_CH2는 AF6이어야 합니다. AF7은 USART1_TX입니다."
      }
    ],
    "warnings": [],
    "passed_pins": ["PA8", "PB9"]
  }
}
데이터 수집 방법 및 목표 수량

자동 생성 가능 데이터:
  방법: pin_af_db.json + 오픈소스 회로도 기반으로 올바른/잘못된 설계 자동 생성
  스크립트: generate_verification_dataset.py

  케이스 종류:
    ① 완전 정상 (PASS) — 오픈소스 실제 설계 기반     → 전체의 25%
    ② AF 번호 오류 1~2개                              → 전체의 20%
    ③ 상보 PWM 쌍 오류 (다른 타이머 사용)             → 전체의 15%
    ④ 인프라 핀 누락 (BOOT0 플로팅, VCAP 미연결)     → 전체의 15%
    ⑤ 핀 중복 배정                                    → 전체의 10%
    ⑥ 전용 핀 (NRST/BOOT0) 잘못 사용                → 전체의 10%
    ⑦ OPAMP/ADC 충돌                                  → 전체의 5%

목표 수량:
  RAG 없이 기본 동작:   500쌍 (자동 생성 가능)
  Fine-tuning 적용:   1,000쌍
A-3. Step 3 — 알고리즘 통합 Agent (RAG 지식베이스)

수집 대상

① Golden Module 소스 코드

현재 등록됨 (golden_modules/):
  bldc_6step_hall.c/.h    → Hall 6-Step + BRK 보호
  dc_motor_pid.c/.h       → DC모터 H-bridge + PID
  fdcan_motor_cmd.c/.h    → FDCAN 커맨드 파싱
  multi_axis_sync.c/.h    → 2축/3축 동기화 타이밍

오픈소스에서 추출 예정:
  foc_clarke.c/.h         → Clarke 변환 (Arduino-FOC / MESC)
  foc_park.c/.h           → Park 변환 + CORDIC (Arduino-FOC / MESC)
  foc_svpwm.c/.h          → SVPWM (VESC / MESC)
  foc_current_pi.c/.h     → PI 제어기 (moteus / MESC)
  foc_current_sense.c/.h  → ADC + OPAMP (flatmcu)

향후 사내 코드 확보 시 교체 예정.

청킹 전략:
  - 함수 단위 청킹 (정규식 또는 AST 파싱)
  - 메타데이터: {"file":"foc_park.c","func":"FOC_ParkTransform",
                 "module":"FOC","type":"transform","g4_accel":"CORDIC"}
  예상 청크 수: ~150개

② STM32CubeG4 HAL 드라이버 소스 (dataset/opensource/STM32CubeG4/)

수집 파일 (sparse checkout 완료):
  Projects/STM32G474E-EVAL/Examples/OPAMP/ — 타이머 제어 먹스, PGA
  Projects/STM32G474E-EVAL/Examples/ADC/   — 보정, 주입채널, 연속변환
  Projects/STM32G474E-EVAL/Examples/FDCAN/ — FDCAN 통신 예제
  Projects/STM32G474E-EVAL/Examples/CORDIC/— Sin/Cos DMA
  Projects/NUCLEO-G474RE/Examples_LL/HRTIM/— 파형생성, CBC 데드타임
  Projects/NUCLEO-G474RE/Examples_LL/TIM/  — PWM, BreakAndDeadtime

청킹 전략:
  - 공개 API 함수 단위 (HAL_로 시작하는 함수)
  예상 청크 수: ~200개

③ 오픈소스 모터 제어 프로젝트 소스 (submodule 초기화 후)

  Arduino-FOC    — STM32 하드웨어 레이어 + 드라이버 코드
  MESC_FOC_ESC   — 고급 전류 제어 + 관측기
  bldc_vesc      — 모터 제어 상태 머신 + 통신 프로토콜
  moteus         — 정밀 위치 제어 + 전류 루프
  예상 청크 수: ~500개

최종 RAG DB 규모 (Step 3)

소스	청크 수
Golden Module 함수 단위	~150
STM32CubeG4 HAL 예제	~200
오픈소스 모터 제어 코드	~500
공식 PDF 주변장치 챕터 (Step 1과 공유)	~1,200
합계	~2,050 청크
A-4. Step 3 — 알고리즘 통합 Agent (Fine-tuning 데이터)

가장 중요하고 가장 어렵다. 반자동화 파이프라인 필요.
데이터 형식

{
  "instruction": "검증된 핀맵과 요구사항을 바탕으로 STM32G4 펌웨어를 생성해줘.",
  "input": {
    "cubemx_generated": {
      "main_c_skeleton": "/* CubeMX main.c 내용 전체 */",
      "tim_c": "/* tim.c 내용 */",
      "adc_c": "/* adc.c 내용 */"
    },
    "requirements": {
      "motor_type": "BLDC",
      "control_method": "FOC",
      "num_axes": 1,
      "feedback": "quadrature_encoder",
      "control_target": "speed",
      "communication": ["FDCAN"]
    },
    "available_modules": ["foc_clarke", "foc_park", "foc_current_pi", "fdcan_motor_cmd"]
  },
  "output": {
    "main_c_integrated": "/* USER CODE 채워진 main.c 전체 */",
    "motor_foc_c": "/* 통합 드라이버 코드 */",
    "fdcan_handler_c": "/* FDCAN 핸들러 */"
  }
}
목표 수량:
  1단계 (RAG 운영 시작):     50쌍  — 품질 중심
  2단계 (기본 fine-tuning): 200쌍  — 컴파일 검증 100%
  3단계 (고품질 fine-tuning):500쌍  — 다양한 조합 포함
B. 모델 학습 방법 상세

B-1. 환경 설정

필수 패키지 설치 (DGX Spark — Ubuntu)

# 기본 환경
conda create -n stm32_train python=3.11
conda activate stm32_train

# PyTorch (Blackwell GB10 지원 버전)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

# 학습 라이브러리
pip install transformers==4.48.0
pip install peft==0.14.0          # QLoRA
pip install trl==0.13.0           # SFT Trainer
pip install bitsandbytes==0.45.0  # 4bit 양자화
pip install accelerate==1.3.0
pip install datasets==3.2.0
pip install unsloth                # 학습 속도 2배 가속

# 평가
pip install evaluate rouge-score
pip install arm-none-eabi-gcc     # 코드 컴파일 검증용
B-2. RAG 지식베이스 구축

임베딩 및 인덱싱 파이프라인

# rag/build_index.py
from llama_index.core import VectorStoreIndex, Document
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core.node_parser import CodeSplitter, SentenceSplitter
import qdrant_client

# 임베딩 모델 로드 (로컬)
embed_model = HuggingFaceEmbedding(
    model_name="BAAI/bge-m3",
    device="cuda",
    embed_batch_size=32
)

# Qdrant 클라이언트 (로컬 Docker)
client = qdrant_client.QdrantClient(host="localhost", port=6333)
vector_store = QdrantVectorStore(
    client=client,
    collection_name="stm32g4_knowledge"
)

# 코드 파일 청킹
code_splitter = CodeSplitter(
    language="c",
    chunk_lines=40,
    chunk_lines_overlap=5,
    max_chars=1500
)

# 문서 청킹 (PDF → 텍스트 변환 후)
text_splitter = SentenceSplitter(
    chunk_size=512,
    chunk_overlap=64
)
Qdrant Docker 실행

docker compose up -d qdrant

# 인덱스 구축 실행
python rag/build_index.py \
  --golden_modules ./golden_modules/ \
  --hal_sources ./dataset/opensource/STM32CubeG4/ \
  --opensource_sources ./dataset/opensource/ \
  --datasheet_chunks ./dataset/official_docs/ \
  --output_collection stm32g4_knowledge
B-3. Step 1 모델 — QLoRA Fine-tuning

베이스 모델: Google/Gemma-4-31B-IT (Dense)
목적: 3계층 HW 검증 리포트 포맷 통일 + 한국어 설명 품질 향상
데이터 준비

# scripts/prepare_data.py
from datasets import Dataset
import json

def load_verification_data(jsonl_path: str) -> Dataset:
    records = []
    with open(jsonl_path) as f:
        for line in f:
            item = json.loads(line)
            records.append({
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "당신은 STM32G4 MCU HW 설계 검증 전문가입니다. "
                            "회로도의 핀 연결이 STM32G4 스펙에 맞는지 3계층(인프라/모터논리/페리페럴) 검증하고 "
                            "결과를 JSON 형식으로 반환하세요."
                        )
                    },
                    {
                        "role": "user",
                        "content": json.dumps(item["input"], ensure_ascii=False)
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(item["output"], ensure_ascii=False)
                    }
                ]
            })
    return Dataset.from_list(records)
학습 스크립트

# scripts/train_step1.py
from unsloth import FastLanguageModel
from trl import SFTTrainer, SFTConfig

# 모델 로드 (4bit 양자화)
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name  = "google/gemma-4-31b-it",
    max_seq_length = 4096,
    dtype       = None,          # bfloat16 자동 감지
    load_in_4bit = True,
)

# LoRA 어댑터 설정
model = FastLanguageModel.get_peft_model(
    model,
    r              = 32,
    target_modules = [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj"
    ],
    lora_alpha     = 32,
    lora_dropout   = 0.05,
    bias           = "none",
    use_gradient_checkpointing = "unsloth",
    random_state   = 42,
)

# 학습 설정
trainer = SFTTrainer(
    model        = model,
    tokenizer    = tokenizer,
    train_dataset = dataset["train"],
    eval_dataset  = dataset["validation"],
    args = SFTConfig(
        output_dir              = "/workspace/models/finetuned/step1_v1",
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 8,
        num_train_epochs        = 3,
        learning_rate           = 2e-4,
        lr_scheduler_type       = "cosine",
        warmup_ratio            = 0.05,
        bf16                    = True,
        logging_steps           = 10,
        save_steps              = 100,
        eval_steps              = 100,
        load_best_model_at_end  = True,
        max_seq_length          = 4096,
        packing                 = True,
    ),
)

trainer.train()
model.save_pretrained("/workspace/models/finetuned/step1_v1/adapter")
예상 학습 시간 (DGX Spark 128GB)

데이터 수	Epoch	예상 시간
500쌍	3	~3시간
1,000쌍	3	~6시간
B-4. Step 3 모델 — QLoRA Fine-tuning

베이스 모델: Google/Gemma-4-26B-IT (MoE)
목적: USER CODE 영역에 Golden Module 통합 코드 생성

주의: MoE 모델 QLoRA는 Dense보다 복잡. Expert 라우팅 레이어 안정성 주의 필요.

주요 차이점 (Step 1 대비)

# scripts/train_step3.py
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name   = "google/gemma-4-26b-it",
    max_seq_length = 8192,       # 코드는 길다 → 더 큰 컨텍스트
    load_in_4bit = True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r              = 64,         # 코드 생성은 rank 높게
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                      "gate_proj", "up_proj", "down_proj"],
    lora_alpha     = 64,
    lora_dropout   = 0.05,
)

SYSTEM_PROMPT = """당신은 STM32G4 임베디드 펌웨어 전문가입니다.
CubeMX가 생성한 HAL 초기화 코드에 모터 제어 알고리즘을 통합하는 코드를 작성합니다.
규칙:
1. /* USER CODE BEGIN */ ~ /* USER CODE END */ 영역에만 코드를 삽입합니다.
2. HAL 초기화 함수(MX_*)는 절대 수정하지 않습니다.
3. 제공된 Golden Module 헤더를 include하고 함수를 호출합니다.
4. 주석은 한국어로 작성합니다."""
B-5. 어댑터 병합 및 Ollama 배포

# Ollama에 등록 (Step 1)
cat > Modelfile_step1 << 'EOF'
FROM /workspace/models/gguf/step1_finetuned/model-q4_k_m.gguf
PARAMETER num_ctx 4096
PARAMETER temperature 0.1
PARAMETER top_p 0.9
SYSTEM "당신은 STM32G4 HW 설계 검증 전문가입니다..."
EOF

ollama create stm32-verifier-step1 -f Modelfile_step1

# Ollama에 등록 (Step 3)
cat > Modelfile_step3 << 'EOF'
FROM /workspace/models/gguf/step3_finetuned/model-q8_0.gguf
PARAMETER num_ctx 8192
PARAMETER temperature 0.1
PARAMETER top_p 0.9
SYSTEM "당신은 STM32G4 임베디드 펌웨어 전문가입니다..."
EOF

ollama create stm32-coder-step3 -f Modelfile_step3
B-6. 평가 지표 및 기준

Step 1 (HW 설계 검증) 평가

def evaluate_step1(model, test_data):
    metrics = {
        "exact_match":      0,   # 목표: > 90%
        "error_recall":     0,   # 실제 오류 중 잡아낸 비율 — 목표: > 95%
        "false_positive":   0,   # 정상 핀을 오류로 판단 — 목표: < 5%
        "json_valid":       0,   # JSON 형식 유효 — 목표: 100%
        "infra_check":      0,   # 인프라 검증 정확도 — 목표: > 98%
        "motor_logic":      0,   # 모터 논리 검증 정확도 — 목표: > 95%
    }
Step 3 (코드 통합) 평가

def evaluate_step3(model, test_data):
    metrics = {
        "compile_pass":     0,   # arm-none-eabi-gcc 컴파일 성공 — 목표: > 95%
        "user_code_only":   0,   # USER CODE 영역에만 삽입했는지 — 목표: 100%
        "module_included":  0,   # 필요 모듈 include 여부 — 목표: > 98%
        "hal_untouched":    0,   # HAL 초기화 코드 수정 없음 — 목표: 100%
    }
C. 웹 애플리케이션 개발 프로세스

C-1. 기술 스택 결정

MVP vs 프로덕션

MVP (Phase 1)	프로덕션 (Phase 2)
프론트엔드	Streamlit	React 18 + TypeScript
UI 라이브러리	Streamlit 기본	Tailwind CSS + shadcn/ui
백엔드	FastAPI	FastAPI
실시간 통신	Streamlit 폴링	WebSocket (FastAPI)
인증	없음	사내 LDAP/AD 연동
파일 저장	로컬 디스크	MinIO (S3 호환)
개발 기간	2~3주	6~8주
권장: MVP로 내부 테스트 후 프로덕션으로 전환
C-2. 시스템 아키텍처 (프로덕션)

[사용자 브라우저]
       ↕ HTTPS
[Nginx 리버스 프록시]  — DGX Spark 80/443 포트
       ↕
┌──────────────────────────────────────────────────────┐
│  DGX Spark (Ubuntu)                                   │
│                                                       │
│  ┌─────────────────┐    ┌──────────────────────────┐ │
│  │ React Frontend  │    │   FastAPI Backend         │ │
│  │ :3000           │    │   :8000                   │ │
│  └─────────────────┘    │                           │ │
│                         │  /api/v1/validate   Step1 │ │
│                         │  /api/v1/generate   Step2 │ │
│                         │  /api/v1/integrate  Step3 │ │
│                         │  /ws/progress      WS     │ │
│                         └────────────┬──────────────┘ │
│                                      ↕                 │
│  ┌─────────────┐  ┌────────────┐  ┌──────────────┐   │
│  │ Ollama      │  │  Qdrant    │  │  CubeMX CLI  │   │
│  │ :11434      │  │  :6333     │  │  (subprocess)│   │
│  │ Gemma4 31B  │  │  Vector DB │  │              │   │
│  │ Gemma4 26B  │  │            │  │              │   │
│  └─────────────┘  └────────────┘  └──────────────┘   │
└──────────────────────────────────────────────────────┘
C-3. 백엔드 API 설계 (FastAPI)

# backend/main.py
from fastapi import FastAPI, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="STM32G4 Agent API", version="2.0.0")

# ── Step 1: HW 설계 검증 ──────────────────────────────────
@app.post("/api/v1/validate")
async def validate_pins(
    csv_file: UploadFile,
    prompt: str  # 자연어 프롬프트
) -> ValidationResult:
    """
    입력: 핀맵 CSV + 자연어 프롬프트
    출력: 3계층 검증 결과 (PASS/FAIL + 오류 목록)
    게이트: errors > 0이면 FAIL 상태 반환, Step2 호출 불가
    """
    pins = parse_csv(await csv_file.read())

    # 1단계: 3계층 규칙 엔진 (빠름, 100% 정확)
    rule_result = rule_engine.validate_3layer(pins)

    # 2단계: RAG 검색 (관련 ST 규칙 실시간 참조)
    rag_context = await rag_client.search(pins, prompt)

    # 3단계: LLM (자연어 설명 생성 + 추가 검증)
    llm_explanation = await llm_client.explain_errors(rule_result, rag_context)

    return ValidationResult(
        status="PASS" if not rule_result.errors else "FAIL",
        infra_check=rule_result.infra,
        motor_logic_check=rule_result.motor_logic,
        peripheral_check=rule_result.peripheral,
        errors=rule_result.errors,
        warnings=rule_result.warnings,
        explanation=llm_explanation,
        validated_json=rule_result.to_json() if not rule_result.errors else None
    )

# ── Step 2: CubeMX 자동화 (4단계 워크플로우) ─────────────
@app.post("/api/v1/generate")
async def generate_hal_code(
    validated_json: ValidatedPinJSON,
    session_id: str
) -> GenerationResult:
    if validated_json.status != "PASS":
        raise HTTPException(403, "핀 검증을 먼저 통과해야 합니다.")

    # [2-1] .ioc 템플릿 수정
    ioc_path = ioc_modifier.apply(validated_json)
    # [2-2] CubeMX CLI 실행
    success = cubemx_runner.generate(ioc_path)
    # [2-3] LLM 스니펫 주입
    snippet = await llm_client.generate_snippet(validated_json)
    code_injector.inject(session_id, snippet)
    # [2-4] ZIP 패키징
    zip_path = packager.create_zip(session_id)

    return GenerationResult(files=files, zip_url=zip_path)

# ── Step 3: 알고리즘 통합 (WebSocket — 스트리밍) ─────────
@app.websocket("/ws/integrate/{session_id}")
async def integrate_algorithm(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        requirements = data["requirements"]

        await websocket.send_json({"status": "모듈 검색 중...", "progress": 10})
        modules = await rag_client.retrieve_modules(requirements)

        await websocket.send_json({"status": "코드 생성 중...", "progress": 30})
        async for chunk in llm_client.stream_integration(
            session_id, requirements, modules
        ):
            await websocket.send_json({
                "status": "생성 중",
                "progress": 30 + chunk.progress * 0.6,
                "partial_code": chunk.text
            })

        await websocket.send_json({"status": "완료", "progress": 100})
    except Exception as e:
        await websocket.send_json({"status": "error", "message": str(e)})
    finally:
        await websocket.close()
C-4. 프론트엔드 화면 구성 (React)

화면 목록

/                   → 대시보드 (프로젝트 목록)
/project/new        → 새 프로젝트 시작
/project/:id/step1  → Step 1: HW 설계 검증
/project/:id/step2  → Step 2: CubeMX 코드 생성 (4단계 진행 화면)
/project/:id/step3  → Step 3: 알고리즘 통합
/project/:id/result → 최종 결과 & 다운로드
/history            → 이전 프로젝트 이력
C-5. Docker Compose 전체 구성

# docker-compose.yml (DGX Spark 배포용)
version: '3.9'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on: [frontend, backend]

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://backend:8000
      - NEXT_PUBLIC_WS_URL=ws://backend:8000

  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      - OLLAMA_HOST=http://ollama:11434
      - QDRANT_HOST=qdrant
      - QDRANT_PORT=6333
      - CUBEMX_PATH=/opt/STM32CubeMX/STM32CubeMX
      - GOLDEN_MODULES_PATH=/workspace/golden_modules
    volumes:
      - /workspace/models:/workspace/models:ro
      - /workspace/golden_modules:/workspace/golden_modules:ro
      - ./sessions:/workspace/sessions
    depends_on: [ollama, qdrant]

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - /workspace/models/ollama:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - ./qdrant_storage:/qdrant/storage
C-6. 웹 개발 단계별 프로세스

Phase 1 — MVP (Streamlit, 2~3주)

Week 1:
  - FastAPI 백엔드 기본 구조 (3 엔드포인트)
  - 3계층 규칙 엔진 연결
  - CubeMX CLI 자동화 연결 (4단계 워크플로우)

Week 2:
  - Streamlit 화면 구성 (Step 1~3)
  - Ollama 모델 API 연결 (Gemma 4 31B + 26B)
  - 파일 업로드 / 다운로드

Week 3:
  - 내부 테스트 (HW/FW 엔지니어 5명)
  - 피드백 반영 및 버그 수정
  - Docker Compose 배포

산출물: 동작하는 프로토타입 (내부망 http://dgx-spark:8501)
Phase 2 — 프로덕션 (React, 6~8주)

Week 1~2:  프로젝트 초기 설정
  - Next.js 14 + TypeScript + Tailwind CSS 설정
  - shadcn/ui 컴포넌트 라이브러리 설치
  - FastAPI 백엔드 WebSocket 추가
  - 라우팅 및 레이아웃 구조

Week 3~4:  핵심 화면 개발
  - 대시보드 (프로젝트 목록 / 이력)
  - Step 1: 드래그앤드롭 CSV 업로드 + 3계층 실시간 검증 결과
  - Step 2: CubeMX 4단계 진행 상태 표시

Week 5~6:  Step 3 + 결과 화면
  - Step 3: 모듈 선택 UI + 코드 생성 스트리밍
  - 결과 화면: 코드 뷰어 (Monaco Editor) + ZIP 다운로드
  - 프로젝트 이력 저장

Week 7:  사내 인증 + 배포
  - 사내 LDAP/AD 연동
  - Nginx + HTTPS 설정
  - Docker Compose 최종 배포

Week 8:  사용자 테스트 + 개선
  - HW/FW 엔지니어 전체 대상 테스트
  - 피드백 반영
  - 운영 가이드 문서 작성