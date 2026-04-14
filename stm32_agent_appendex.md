STM32G4 Agent — 어펜딕스

최초 작성: 2026-04-10
목차

A. 모델별 학습 데이터 수집 가이드
B. 모델 학습 방법 상세
C. 웹 애플리케이션 개발 프로세스
A. 모델별 학습 데이터 수집 가이드

A-1. Step 1 — 핀 검증 Agent (RAG 지식베이스)

수집 대상 문서 목록

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
② STM32G4 데이터시트 (PDF → RAG)

문서명: STM32G474xB/xC/xE datasheet (DS12288)
페이지 수: ~200페이지
ST 공식 문서 번호: DS12288 Rev 5 이상

추출 대상 섹션만 처리:
  - Table 11~15: Alternate function mapping (약 30페이지)
  - Table 5~8:   Pin description (약 20페이지)
  - Section 3.4: Memory map
  총 대상: 약 50페이지 / ~300 청크

청킹 전략:
  - 핀 테이블 행 단위: 1행 = 1청크
    {"pin":"PA8", "af6":"TIM1_CH1", "af5":"...", "type":"I/O", "level":"FT"}
  - 설명 섹션: 256토큰 단위, 32토큰 overlap
  - 툴: pdfplumber (테이블 추출), PyMuPDF (텍스트)
③ Reference Manual RM0440

문서명: STM32G4 Series Reference Manual (RM0440)
페이지 수: ~2000페이지
추출 대상 챕터만:
  - Chapter 25: General-purpose timers (TIM2~TIM5)      약 80페이지
  - Chapter 24: Advanced-control timers (TIM1, TIM8, TIM20) 약 100페이지
  - Chapter 21: ADC                                      약 80페이지
  - Chapter 44: FDCAN                                    약 60페이지
  - Chapter 17: CORDIC                                   약 20페이지
  - Chapter 18: FMAC                                     약 20페이지
  합계: 약 360페이지 / ~1200 청크

청킹 전략:
  - 레지스터 설명: 레지스터 단위 청크
  - 기능 설명: 512토큰, 64토큰 overlap
④ Errata Sheet

문서명: STM32G4 Errata sheet (ES0430)
페이지 수: ~40페이지
전체 수집 (알려진 핀/주변장치 버그)
청킹: 항목 단위 (1 이슈 = 1 청크)
예상 청크 수: ~50개
최종 RAG DB 규모 (Step 1)

소스	청크 수	처리 방식
CubeMX XML → JSON DB	—	규칙 엔진 직접 조회 (RAG 아님)
데이터시트 핀 테이블	~300	벡터 임베딩
Reference Manual 선택 챕터	~1,200	벡터 임베딩
Errata Sheet	~50	벡터 임베딩
합계	~1,550 청크	
A-2. Step 1 — 핀 검증 Agent (Fine-tuning 데이터)

규칙 엔진으로 정답을 자동 생성할 수 있어 대부분 자동화 가능.
데이터 형식

// 입력
{
  "instruction": "STM32G474RET6 회로도를 검증해줘.",
  "input": {
    "chip": "STM32G474RET6",
    "pins": [
      {"pin": "PA8",  "function": "TIM1_CH1",  "af": "AF6"},
      {"pin": "PA9",  "function": "TIM1_CH2",  "af": "AF7"},
      {"pin": "PB9",  "function": "FDCAN1_TX", "af": "AF9"}
    ]
  }
}

// 출력 (정답)
{
  "output": {
    "validation": "FAIL",
    "errors": [
      {
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
  방법: pin_af_db.json 기반으로 올바른/잘못된 AF 조합 자동 생성
  스크립트: generate_verification_dataset.py

  케이스 종류:
    ① 완전 정상 (PASS)                    → 전체의 30%
    ② AF 번호 오류 1개                    → 전체의 25%
    ③ AF 번호 오류 2개 이상               → 전체의 15%
    ④ 핀 중복 배정                        → 전체의 10%
    ⑤ 전용 핀 (NRST/BOOT0) 잘못 사용    → 전체의 10%
    ⑥ 전원 핀 (VDD/VSS) 누락             → 전체의 10%

목표 수량:
  RAG 없이 기본 동작:   500쌍 (2~3일 자동 생성 가능)
  Fine-tuning 적용:   1,000쌍 (1주일 이내)
  권장 최종 목표:     2,000쌍 (다양한 칩 변형 포함)
# 자동 생성 스크립트 핵심 로직
import json, random

def generate_fault_case(db, chip, num_pins=6, fault_type="af_error"):
    chip_db = db[chip]
    pins = random.sample(list(chip_db.keys()), num_pins)
    result = []
    errors = []
    for pin in pins:
        signals = chip_db[pin]["signals"]
        if not signals:
            continue
        func = random.choice(signals)
        correct_af = chip_db[pin]["af"].get(func, "")

        if fault_type == "af_error" and random.random() < 0.3:
            # 의도적으로 틀린 AF 번호 주입
            wrong_af = f"AF{random.randint(0,15)}"
            while wrong_af == correct_af:
                wrong_af = f"AF{random.randint(0,15)}"
            result.append({"pin": pin, "function": func, "af": wrong_af})
            errors.append({"pin": pin, "correct_af": correct_af, "assigned_af": wrong_af})
        else:
            result.append({"pin": pin, "function": func, "af": correct_af})

    return {"pins": result}, {"validation": "FAIL" if errors else "PASS", "errors": errors}
A-3. Step 3 — 알고리즘 통합 Agent (RAG 지식베이스)

수집 대상

① Golden Module 소스 코드

수집 대상 파일 (사내 보유 코드):
  알고리즘 모듈 (.c + .h 쌍):
    foc_clarke.c / .h
    foc_park.c / .h
    foc_inv_park.c / .h
    foc_svpwm.c / .h
    foc_current_pi.c / .h
    foc_speed_pi.c / .h
    foc_position_pi.c / .h
    foc_angle_encoder.c / .h
    foc_angle_hall.c / .h
    foc_angle_smo.c / .h
    foc_current_sense.c / .h
    bldc_6step_hall.c / .h   (신규)
    dc_motor_pid.c / .h      (신규)
    fdcan_motor_cmd.c / .h   (신규)
    multi_axis_sync.c / .h   (신규)

청킹 전략:
  - 함수 단위 청킹 (AST 파싱 또는 정규식)
  - 함수 하나 = 청크 하나
  - 메타데이터: {"file":"foc_park.c","func":"FOC_ParkTransform",
                 "module":"FOC","type":"transform","g4_accel":"CORDIC"}
  예상 청크 수: ~150개 (함수 단위)
② STM32CubeG4 HAL 드라이버 소스

경로: STM32CubeG4\Drivers\STM32G4xx_HAL_Driver\Src\

수집 파일:
  stm32g4xx_hal_tim.c         (~3,000줄)
  stm32g4xx_hal_adc.c         (~2,500줄)
  stm32g4xx_hal_fdcan.c       (~2,000줄)
  stm32g4xx_hal_cordic.c      (~500줄)
  stm32g4xx_hal_fmac.c        (~800줄)
  stm32g4xx_hal_opamp.c       (~600줄)
  stm32g4xx_hal_gpio.c        (~400줄)

청킹 전략:
  - 공개 API 함수 단위 (HAL_로 시작하는 함수)
  - 내부 함수(_)는 제외
  예상 청크 수: ~200개
③ CubeMX 자동 생성 코드 예제 모음

생성 방법:
  CubeMX에서 다양한 G4 설정으로 코드 생성 → 수집
  최소 30가지 설정 조합으로 생성

  조합 축:
    모터 타이머:  TIM1 단독 / TIM1+TIM8 / TIM1+TIM8+TIM20
    ADC 설정:    단순 / Injected+DMA / 멀티채널
    FDCAN 유무:  있음 / 없음
    CORDIC 유무: 있음 / 없음

  수집 파일 (설정당):
    main.c / tim.c / adc.c / fdcan.c / cordic.c
  예상 청크 수: ~500개 (30설정 × 17청크 평균)
최종 RAG DB 규모 (Step 3)

소스	청크 수
Golden Module 함수 단위	~150
HAL 드라이버 공개 API	~200
CubeMX 생성 예제	~500
Reference Manual 주변장치 챕터 (Step 1과 공유)	~1,200
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
데이터 생성 파이프라인 (반자동화)

[Layer 1 자동화]
  CubeMX CLI로 다양한 설정 조합 코드 생성
  → 30~50가지 HAL 초기화 코드 확보

[Layer 2 수동 작업]
  시니어 FW 엔지니어가 각 Golden Module 작성/검증
  → 15개 모듈 × 1~2일 = 3~4주 작업

[Layer 3 반자동화]
  Layer 1(CubeMX) + Layer 2(모듈) 조합 스크립트
  엔지니어가 통합 코드 검토/수정
  → 1조합당 1~2시간

목표 수량 (단계별):
  1단계 (RAG 운영 시작):     50쌍  — 품질 중심, 전수 HW 검증
  2단계 (기본 fine-tuning): 200쌍  — 컴파일 검증 100%, HW 검증 30%
  3단계 (고품질 fine-tuning):500쌍  — 다양한 조합 포함
유효 조합 매트릭스 (~50개)

모터 종류	제어 방식	축 수	피드백	통신	샘플 수
BLDC	FOC	1	엔코더	FDCAN	5
BLDC	FOC	1	Hall	FDCAN	5
BLDC	FOC	1	Sensorless	FDCAN	4
BLDC	FOC	2	엔코더	FDCAN	5
BLDC	FOC	2	Hall	FDCAN	4
BLDC	6-step	1	Hall	FDCAN	4
BLDC	6-step	1	Hall	UART	3
DC Motor	PID	1	엔코더	FDCAN	4
DC Motor	PID	2	엔코더	FDCAN	4
DC Motor	PID	3	엔코더	FDCAN	3
BLDC	FOC	1	엔코더	FDCAN+UART	4
BLDC	FOC	3	엔코더	FDCAN	5
합계					~50
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
디렉토리 구조

/workspace/stm32_agent/
├── data/
│   ├── step1_verification/
│   │   ├── train.jsonl          # 학습 데이터 (80%)
│   │   ├── val.jsonl            # 검증 데이터 (10%)
│   │   └── test.jsonl           # 테스트 데이터 (10%)
│   └── step3_integration/
│       ├── train.jsonl
│       ├── val.jsonl
│       └── test.jsonl
├── models/
│   ├── base/                    # 다운받은 베이스 모델
│   └── finetuned/               # 학습 완료 모델
├── scripts/
│   ├── prepare_data.py
│   ├── train_step1.py
│   ├── train_step3.py
│   └── evaluate.py
└── rag/
    ├── build_index.py
    └── query.py
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
    chunk_lines=40,         # 함수 단위에 맞는 크기
    chunk_lines_overlap=5,
    max_chars=1500
)

# 문서 청킹 (PDF → 텍스트 변환 후)
text_splitter = SentenceSplitter(
    chunk_size=512,
    chunk_overlap=64
)

def ingest_golden_modules(module_dir: str):
    """Golden Module .c/.h 파일 → 청킹 → 인덱싱"""
    docs = []
    for path in glob.glob(f"{module_dir}/*.c"):
        with open(path) as f:
            content = f.read()
        doc = Document(
            text=content,
            metadata={
                "source": "golden_module",
                "file": os.path.basename(path),
                "module_type": extract_module_type(path),  # "FOC", "FDCAN" 등
                "language": "c"
            }
        )
        docs.append(doc)
    return docs
Qdrant Docker 실행

# docker-compose.yml
version: '3.8'
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./qdrant_storage:/qdrant/storage
    environment:
      - QDRANT__SERVICE__GRPC_PORT=6334
docker compose up -d qdrant

# 인덱스 구축 실행
python rag/build_index.py \
  --golden_modules ./golden_modules/ \
  --hal_sources ./STM32CubeG4/Drivers/ \
  --datasheet_chunks ./data/chunks/datasheet/ \
  --output_collection stm32g4_knowledge
B-3. Step 1 모델 — QLoRA Fine-tuning

베이스 모델: Qwen/Qwen2.5-72B-Instruct 목적: 핀 검증 리포트 포맷 통일 + 한국어 설명 품질 향상
데이터 준비

# scripts/prepare_data.py
from datasets import Dataset
import json

def load_verification_data(jsonl_path: str) -> Dataset:
    records = []
    with open(jsonl_path) as f:
        for line in f:
            item = json.loads(line)
            # Qwen2.5 채팅 포맷으로 변환
            records.append({
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "당신은 STM32G4 MCU 핀 검증 전문가입니다. "
                            "회로도의 핀 연결이 STM32G4 AF 스펙에 맞는지 검증하고 "
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
from datasets import load_dataset

# 모델 로드 (4bit 양자화)
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name  = "/workspace/models/base/Qwen2.5-72B-Instruct",
    max_seq_length = 4096,
    dtype       = None,          # bfloat16 자동 감지
    load_in_4bit = True,
)

# LoRA 어댑터 설정
model = FastLanguageModel.get_peft_model(
    model,
    r              = 32,         # LoRA rank — 높을수록 품질↑ 메모리↑
    target_modules = [           # Qwen2.5 어텐션 레이어
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj"
    ],
    lora_alpha     = 32,         # 보통 r과 동일하게 설정
    lora_dropout   = 0.05,
    bias           = "none",
    use_gradient_checkpointing = "unsloth",
    random_state   = 42,
)

# 데이터 로드
dataset = load_dataset("json", data_files={
    "train": "/workspace/data/step1_verification/train.jsonl",
    "validation": "/workspace/data/step1_verification/val.jsonl",
})

# 학습 설정
trainer = SFTTrainer(
    model        = model,
    tokenizer    = tokenizer,
    train_dataset = dataset["train"],
    eval_dataset  = dataset["validation"],
    args = SFTConfig(
        output_dir              = "/workspace/models/finetuned/step1_v1",
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 8,    # 실효 배치: 16
        num_train_epochs        = 3,
        learning_rate           = 2e-4,
        lr_scheduler_type       = "cosine",
        warmup_ratio            = 0.05,
        fp16                    = False,
        bf16                    = True,     # GB10 Blackwell bf16 지원
        logging_steps           = 10,
        save_steps              = 100,
        eval_steps              = 100,
        evaluation_strategy     = "steps",
        load_best_model_at_end  = True,
        report_to               = "tensorboard",
        max_seq_length          = 4096,
        packing                 = True,     # 짧은 시퀀스 패킹으로 속도↑
    ),
)

trainer.train()

# 어댑터 저장
model.save_pretrained("/workspace/models/finetuned/step1_v1/adapter")
tokenizer.save_pretrained("/workspace/models/finetuned/step1_v1/adapter")
예상 학습 시간 (DGX Spark 128GB)

데이터 수	Epoch	예상 시간
500쌍	3	~2시간
1,000쌍	3	~4시간
2,000쌍	3	~8시간
B-4. Step 3 모델 — QLoRA Fine-tuning

베이스 모델: Qwen/Qwen2.5-Coder-32B-Instruct 목적: USER CODE 영역에 Golden Module 통합 코드 생성
주요 차이점 (Step 1 대비)

# scripts/train_step3.py — Step 1과 다른 부분만 표시

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name   = "/workspace/models/base/Qwen2.5-Coder-32B-Instruct",
    max_seq_length = 8192,       # 코드는 길다 → 더 큰 컨텍스트
    load_in_4bit = True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r              = 64,         # 코드 생성은 rank 높게 (품질 중요)
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                      "gate_proj", "up_proj", "down_proj"],
    lora_alpha     = 64,
    lora_dropout   = 0.05,
)

# 시스템 프롬프트 (코드 생성 특화)
SYSTEM_PROMPT = """당신은 STM32G4 임베디드 펌웨어 전문가입니다.
CubeMX가 생성한 HAL 초기화 코드에 모터 제어 알고리즘을 통합하는 코드를 작성합니다.
규칙:
1. /* USER CODE BEGIN */ ~ /* USER CODE END */ 영역에만 코드를 삽입합니다.
2. HAL 초기화 함수(MX_*)는 절대 수정하지 않습니다.
3. 제공된 Golden Module 헤더를 include하고 함수를 호출합니다.
4. 주석은 한국어로 작성합니다."""

# 학습 설정 (코드는 시퀀스가 길어 배치 줄임)
SFTConfig(
    per_device_train_batch_size = 1,
    gradient_accumulation_steps = 16,   # 실효 배치: 16
    max_seq_length              = 8192,
    learning_rate               = 1e-4, # 코드 모델은 lr 낮게
    num_train_epochs            = 5,    # 코드는 epoch 더 필요
)
예상 학습 시간 (DGX Spark 128GB)

데이터 수	Epoch	예상 시간
50쌍	5	~1시간
200쌍	5	~6시간
500쌍	5	~15시간
B-5. 어댑터 병합 및 Ollama 배포

# scripts/merge_and_deploy.py
from unsloth import FastLanguageModel

# 어댑터 + 베이스 모델 병합
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name   = "/workspace/models/base/Qwen2.5-Coder-32B-Instruct",
    load_in_4bit = True,
)
model.load_adapter("/workspace/models/finetuned/step3_v1/adapter")

# GGUF 포맷 변환 (Ollama 사용 가능 형식)
model.save_pretrained_gguf(
    "/workspace/models/gguf/step3_finetuned",
    tokenizer,
    quantization_method = "q8_0"   # Q8 품질 유지
)
# Ollama에 등록
cat > Modelfile << 'EOF'
FROM /workspace/models/gguf/step3_finetuned/model-q8_0.gguf
PARAMETER num_ctx 8192
PARAMETER temperature 0.1
PARAMETER top_p 0.9
SYSTEM "당신은 STM32G4 임베디드 펌웨어 전문가입니다..."
EOF

ollama create stm32-coder-step3 -f Modelfile
ollama run stm32-coder-step3  # 테스트
B-6. 평가 지표 및 기준

Step 1 (핀 검증) 평가

# scripts/evaluate.py

def evaluate_step1(model, test_data):
    metrics = {
        "exact_match":      0,   # 목표: > 90%
        "error_recall":     0,   # 실제 오류 중 잡아낸 비율 — 목표: > 95%
        "false_positive":   0,   # 정상 핀을 오류로 판단 — 목표: < 5%
        "json_valid":       0,   # JSON 형식 유효 — 목표: 100%
    }
    # 규칙 엔진 정답과 LLM 출력 비교
    ...
Step 3 (코드 통합) 평가

def evaluate_step3(model, test_data):
    metrics = {
        "compile_pass":     0,   # arm-none-eabi-gcc 컴파일 성공 — 목표: > 95%
        "user_code_only":   0,   # USER CODE 영역에만 삽입했는지 — 목표: 100%
        "module_included":  0,   # 필요 모듈 include 여부 — 목표: > 98%
        "hal_untouched":    0,   # HAL 초기화 코드 수정 없음 — 목표: 100%
    }

    for sample in test_data:
        generated = model.generate(sample["input"])

        # 컴파일 자동 검증
        result = subprocess.run(
            ["arm-none-eabi-gcc", "-c", "-mcpu=cortex-m4",
             "-I./include", generated_file],
            capture_output=True
        )
        metrics["compile_pass"] += int(result.returncode == 0)
    ...
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
│  │ Qwen2.5-72B │  │  Vector DB │  │              │   │
│  │ Qwen2.5-Cod │  │            │  │              │   │
│  └─────────────┘  └────────────┘  └──────────────┘   │
└──────────────────────────────────────────────────────┘
C-3. 백엔드 API 설계 (FastAPI)

# backend/main.py
from fastapi import FastAPI, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="STM32G4 Agent API", version="1.0.0")

app.add_middleware(CORSMiddleware,
    allow_origins=["http://dgx-spark"],
    allow_methods=["*"], allow_headers=["*"])

# ── Step 1: 핀 검증 ──────────────────────────────────────
@app.post("/api/v1/validate")
async def validate_pins(
    csv_file: UploadFile,
    requirements: str  # JSON string
) -> ValidationResult:
    """
    입력: 핀맵 CSV + 요구사항 JSON
    출력: 검증 결과 (PASS/FAIL + 오류 목록)
    게이트: errors > 0이면 FAIL 상태 반환, Step2 호출 불가
    """
    pins = parse_csv(await csv_file.read())
    reqs = json.loads(requirements)

    # 1단계: 규칙 엔진 (빠름, 100% 정확)
    rule_result = rule_engine.validate(pins)

    # 2단계: LLM (자연어 설명 생성)
    llm_explanation = await llm_client.explain_errors(rule_result)

    return ValidationResult(
        status="PASS" if not rule_result.errors else "FAIL",
        errors=rule_result.errors,
        warnings=rule_result.warnings,
        explanation=llm_explanation,
        validated_json=rule_result.to_json() if not rule_result.errors else None
    )

# ── Step 2: CubeMX 자동화 ────────────────────────────────
@app.post("/api/v1/generate")
async def generate_hal_code(
    validated_json: ValidatedPinJSON,    # Step 1 PASS 결과만 허용
    session_id: str
) -> GenerationResult:
    """
    입력: Step 1 검증 완료 JSON (errors==[] 필수)
    출력: CubeMX 생성 코드 파일 목록
    검증 게이트: validated_json.status != "PASS"면 403 반환
    """
    if validated_json.status != "PASS":
        raise HTTPException(403, "핀 검증을 먼저 통과해야 합니다.")

    ioc_path = generate_ioc(validated_json)
    success  = run_cubemx_cli(ioc_path)
    files    = collect_generated_files(session_id)

    return GenerationResult(files=files, session_id=session_id)

# ── Step 3: 알고리즘 통합 (WebSocket — 스트리밍) ─────────
@app.websocket("/ws/integrate/{session_id}")
async def integrate_algorithm(websocket: WebSocket, session_id: str):
    """
    실시간 스트리밍으로 코드 생성 진행 상황 전송
    """
    await websocket.accept()

    try:
        data = await websocket.receive_json()
        requirements = data["requirements"]

        await websocket.send_json({"status": "모듈 검색 중...", "progress": 10})
        modules = await rag_client.retrieve_modules(requirements)

        await websocket.send_json({"status": "코드 생성 중...", "progress": 30})

        # LLM 스트리밍 출력
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
/project/:id/step1  → Step 1: 핀 검증
/project/:id/step2  → Step 2: CubeMX 코드 생성 (자동, 진행 화면)
/project/:id/step3  → Step 3: 알고리즘 통합
/project/:id/result → 최종 결과 & 다운로드
/history            → 이전 프로젝트 이력
핵심 컴포넌트 코드 (Step 1 — 핀 검증 화면)

// src/pages/Step1ValidationPage.tsx
import { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { CheckCircle2, XCircle, AlertTriangle, Upload } from 'lucide-react'

export default function Step1ValidationPage() {
  const [csvFile, setCsvFile]       = useState<File | null>(null)
  const [requirements, setReqs]     = useState(defaultRequirements)
  const [result, setResult]         = useState<ValidationResult | null>(null)
  const [loading, setLoading]       = useState(false)

  const onDrop = useCallback((files: File[]) => setCsvFile(files[0]), [])
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop, accept: { 'text/csv': ['.csv'] }
  })

  const handleValidate = async () => {
    setLoading(true)
    const form = new FormData()
    form.append('csv_file', csvFile!)
    form.append('requirements', JSON.stringify(requirements))

    const res  = await fetch('/api/v1/validate', { method: 'POST', body: form })
    const data = await res.json()
    setResult(data)
    setLoading(false)
  }

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      {/* 헤더 */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-blue-600 flex items-center
                        justify-center text-white font-bold text-lg">1</div>
        <div>
          <h1 className="text-2xl font-bold text-gray-900">핀 검증</h1>
          <p className="text-gray-500">회로도 핀맵이 STM32G4 AF 스펙에 맞는지 확인합니다</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* CSV 업로드 */}
        <Card>
          <CardHeader><CardTitle>회로도 핀맵 (CSV)</CardTitle></CardHeader>
          <CardContent>
            <div {...getRootProps()} className={`
              border-2 border-dashed rounded-xl p-8 text-center cursor-pointer
              transition-colors
              ${isDragActive ? 'border-blue-500 bg-blue-50'
                             : 'border-gray-300 hover:border-blue-400'}
            `}>
              <input {...getInputProps()} />
              <Upload className="mx-auto mb-3 text-gray-400" size={36} />
              {csvFile
                ? <p className="font-medium text-blue-600">{csvFile.name}</p>
                : <p className="text-gray-500">CSV 파일을 드래그하거나 클릭하여 업로드</p>
              }
            </div>
          </CardContent>
        </Card>

        {/* 요구사항 설정 */}
        <Card>
          <CardHeader><CardTitle>모터 요구사항</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: '모터 종류',   key: 'motor_type',      options: ['BLDC', 'DC Motor'] },
                { label: '제어 방식',   key: 'control_method',  options: ['FOC', '6-step', 'PID'] },
                { label: '피드백',      key: 'feedback',        options: ['encoder', 'hall', 'sensorless'] },
                { label: '통신',        key: 'communication',   options: ['FDCAN', 'UART', 'FDCAN+UART'] },
              ].map(({ label, key, options }) => (
                <div key={key}>
                  <label className="text-sm font-medium text-gray-700">{label}</label>
                  <select
                    className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                    value={(requirements as any)[key]}
                    onChange={e => setReqs(r => ({ ...r, [key]: e.target.value }))}
                  >
                    {options.map(o => <option key={o}>{o}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 실행 버튼 */}
      <Button
        className="w-full h-12 text-base font-semibold bg-blue-600 hover:bg-blue-700"
        onClick={handleValidate}
        disabled={!csvFile || loading}
      >
        {loading ? '검증 중...' : '🔍  핀 검증 실행'}
      </Button>

      {/* 결과 */}
      {result && (
        <Card className={`border-2 ${
          result.status === 'PASS' ? 'border-green-400' : 'border-red-400'
        }`}>
          <CardHeader>
            <div className="flex items-center gap-3">
              {result.status === 'PASS'
                ? <CheckCircle2 className="text-green-500" size={28} />
                : <XCircle className="text-red-500" size={28} />
              }
              <CardTitle className={
                result.status === 'PASS' ? 'text-green-700' : 'text-red-700'
              }>
                {result.status === 'PASS' ? '검증 통과 — Step 2 진행 가능' : '검증 실패 — 회로도 수정 필요'}
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 오류 목록 */}
            {result.errors.map((err, i) => (
              <Alert key={i} variant="destructive">
                <XCircle className="h-4 w-4" />
                <AlertDescription>
                  <span className="font-mono font-bold">{err.pin}</span>
                  {' — '}{err.message}
                </AlertDescription>
              </Alert>
            ))}
            {/* 경고 목록 */}
            {result.warnings.map((w, i) => (
              <Alert key={i} className="border-yellow-400 bg-yellow-50">
                <AlertTriangle className="h-4 w-4 text-yellow-600" />
                <AlertDescription className="text-yellow-800">{w}</AlertDescription>
              </Alert>
            ))}
            {/* 통과 핀 목록 */}
            <div className="flex flex-wrap gap-2">
              {result.passed_pins?.map(p => (
                <Badge key={p} variant="outline"
                       className="border-green-400 text-green-700">
                  ✓ {p}
                </Badge>
              ))}
            </div>
            {/* 다음 단계 버튼 (PASS만) */}
            {result.status === 'PASS' && (
              <Button className="w-full bg-green-600 hover:bg-green-700"
                      onClick={() => navigate(`/project/${id}/step2`)}>
                Step 2 — CubeMX 코드 생성 →
              </Button>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
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
      - ./sessions:/workspace/sessions   # 세션별 생성 파일
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

  tensorboard:               # 학습 모니터링
    image: tensorflow/tensorflow:latest
    ports:
      - "6006:6006"
    volumes:
      - /workspace/models/logs:/logs
    command: tensorboard --logdir=/logs --host=0.0.0.0
C-6. 웹 개발 단계별 프로세스

Phase 1 — MVP (Streamlit, 2~3주)

Week 1:
  - FastAPI 백엔드 기본 구조 (3 엔드포인트)
  - 핀 검증 규칙 엔진 연결
  - CubeMX CLI 자동화 연결

Week 2:
  - Streamlit 화면 구성 (Step 1~3)
  - Ollama 모델 API 연결
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
  - Step 1: 드래그앤드롭 CSV 업로드 + 실시간 검증 결과
  - Step 2: CubeMX 진행 상태 표시 (진행 바)

Week 5~6:  Step 3 + 결과 화면
  - Step 3: 모듈 선택 UI + 코드 생성 스트리밍 (실시간 타이핑 효과)
  - 결과 화면: 코드 뷰어 (Monaco Editor) + ZIP 다운로드
  - 프로젝트 이력 저장

Week 7:  사내 인증 + 배포
  - 사내 LDAP/AD 연동 (또는 간단한 계정 관리)
  - Nginx + HTTPS 설정
  - Docker Compose 최종 배포

Week 8:  사용자 테스트 + 개선
  - HW/FW 엔지니어 전체 대상 테스트
  - 피드백 반영
  - 운영 가이드 문서 작성
화면별 주요 기능 요약

화면	주요 기능	핵심 UX
대시보드	최근 프로젝트, 빠른 시작 버튼	카드 그리드, 상태 배지
Step 1	CSV 드래그앤드롭, 요구사항 폼, 검증 결과	PASS=초록/FAIL=빨강 즉시 표시
Step 2	자동 진행 바, 생성 파일 트리 미리보기	스핀 애니메이션, 파일 클릭 미리보기
Step 3	모듈 선택 체크박스, 코드 스트리밍	Monaco Editor 실시간 타이핑
결과	코드 전체 뷰어, 탭 파일 전환, ZIP 다운로드	신택스 하이라이팅 (C 언어)
C-7. 개발 우선순위 체크리스트

[MVP — 즉시 시작 가능]
  □ FastAPI 기본 서버 구성
  □ /api/v1/validate 엔드포인트 (규칙 엔진 연결)
  □ /api/v1/generate 엔드포인트 (CubeMX CLI 연결)
  □ /api/v1/integrate 엔드포인트 (Ollama 연결)
  □ Streamlit Step 1~3 화면
  □ Docker Compose 단일 명령 배포

[프로덕션 — Phase 2]
  □ React + Next.js 프로젝트 세팅
  □ Step 1 드래그앤드롭 + 실시간 검증 UI
  □ Step 2 진행 상태 WebSocket
  □ Step 3 코드 스트리밍 + Monaco Editor
  □ 프로젝트 이력 저장 (SQLite 또는 PostgreSQL)
  □ 파일 다운로드 (ZIP)
  □ 사내 인증 연동
  □ HTTPS + Nginx 배포