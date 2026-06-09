"""
Step 3 코드젠 에이전트 — Golden Module "바인딩 glue" 생성

설계(ARCHITECTURE/CLAUDE.md 원칙 #1·#6):
  golden_modules/*.c 는 이미 하드웨어 비종속(핸들을 구조체로 주입받음).
  따라서 적응 = 모듈 내부 수정이 아니라, 사용자 실제 핸들/채널/GPIO로
  구조체를 채우는 "바인딩 glue 코드"를 main.c USER CODE 마커에 생성하는 것.

파이프라인:
  validated_pins ─→ [결정론 역할 매퍼 map_roles]   (LLM 없음)
  생성 HAL 프로젝트 ─→ [parse_hal_project]         (핸들/매크로/ADC채널 ground truth)
  prompt + roles ─→ [코드 RAG: stm32g4_code]       (opensource 알고리즘 참고, verbatim 금지)
  golden .h API + roles + binding + RAG ─→ [LLM _llm_glue]  (알고리즘 구조만)
  glue ─→ [integrate] 모듈 복사 + Makefile/CMake 등록 + main.c 주입
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GOLDEN_DIR = Path(__file__).parent.parent / "golden_modules"
CODE_COLLECTION = "stm32g4_code"   # opensource 알고리즘 RAG 컬렉션 (Step1 stm32g4_docs와 분리)

# 오프라인 운영 — HF는 로컬 캐시만 (step1_review_agent와 동일)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

try:
    from sentence_transformers import SentenceTransformer as _ST
    _bge_model: Optional[_ST] = None

    def _get_bge_model() -> _ST:
        global _bge_model
        if _bge_model is None:
            _bge_model = _ST("BAAI/bge-m3")
        return _bge_model
except ImportError:
    _get_bge_model = None  # type: ignore


# ---------------------------------------------------------------------------
# 결정론적 모듈 선택 규칙
# ---------------------------------------------------------------------------

def select_modules(vp: Dict[str, Any]) -> List[str]:
    """validated_pins → 적용할 golden module 이름 목록 (LLM 없음)."""
    control = vp.get("control_type", "").upper()
    encoder = vp.get("encoder_type", "")
    comms   = [c.lower() for c in vp.get("comms", [])]
    n_motor = vp.get("motor_count", 1)

    selected: List[str] = []

    # 모터 제어 기반 모듈
    if control in ("BLDC_6STEP", "BLDC") and encoder == "hall":
        selected.append("bldc_6step_hall")
    else:
        # FOC / PMSM / DC 등 모두 dc_motor_pid 기반
        selected.append("dc_motor_pid")

    # 통신 모듈
    if "fdcan" in comms:
        selected.append("fdcan_motor_cmd")

    # 다축 동기화
    if n_motor > 1:
        selected.append("multi_axis_sync")

    return selected


def _read_module(name: str) -> Dict[str, str]:
    """golden module .c / .h 파일 읽기. 없으면 빈 dict."""
    result: Dict[str, str] = {}
    for ext in ("c", "h"):
        path = GOLDEN_DIR / f"{name}.{ext}"
        if path.exists():
            result[ext] = path.read_text(encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# 결정론적 역할 매퍼 — validated_pins → 역할(핸들/채널/GPIO) (LLM 없음)
# ---------------------------------------------------------------------------

def _pin_to_gpio(pin: str) -> Optional[Tuple[str, str]]:
    """'PA8' → ('GPIOA', 'GPIO_PIN_8'). 형식 안 맞으면 None."""
    m = re.match(r"^P([A-H])(\d{1,2})$", pin.strip().upper())
    if not m:
        return None
    return f"GPIO{m.group(1)}", f"GPIO_PIN_{m.group(2)}"


def _peripheral_handle(periph: str) -> str:
    """주변장치 인스턴스명 → CubeMX 기본 핸들명. 'TIM1'→'htim1', 'OPAMP2'→'hopamp2'."""
    m = re.match(r"^([A-Z]+)(\d+)$", periph.upper())
    if not m:
        return "h" + periph.lower()
    return "h" + m.group(1).lower() + m.group(2)


def map_roles(vp: Dict[str, Any]) -> Dict[str, Any]:
    """validated_pins → 모터 제어 역할 매핑 (결정론).

    반환 roles:
      pwm_timer, pwm_channels{CHx:pin}, enc_timer, enc_channels,
      current_sense[{kind,periph,pin,label}], brk{pin,function},
      fdcan, dir_gpios[{pin,label,gpio,gpio_pin}], comms[]
    """
    pins: List[Dict[str, Any]] = vp.get("pins", [])
    tim_channels: Dict[str, List[Tuple[str, str]]] = {}   # TIMx → [(CHsig, pin)]
    current: List[Dict[str, str]] = []
    brk: Optional[Dict[str, str]] = None
    fdcan: Optional[str] = None
    dir_gpios: List[Dict[str, str]] = []

    for p in pins:
        func = (p.get("function") or "").strip().upper()
        pin  = (p.get("pin") or "").strip().upper()
        lbl  = (p.get("label") or "").strip()

        if func.startswith("TIM"):
            periph = func.split("_")[0]
            sub = func[len(periph) + 1:] if "_" in func else ""
            if "BKIN" in sub:
                brk = {"pin": pin, "function": func}
            elif sub.startswith("CH"):
                tim_channels.setdefault(periph, []).append((sub, pin))
        elif "OPAMP" in func and "VOUT" in func:
            current.append({"kind": "opamp", "periph": func.split("_")[0], "pin": pin, "label": lbl})
        elif re.match(r"^ADC\d?_IN", func):
            current.append({"kind": "adc", "periph": func.split("_")[0], "pin": pin, "label": lbl})
        elif func.startswith("FDCAN"):
            fdcan = func.split("_")[0]
        elif func in ("", "GPIO", "NAN", "NONE"):
            # function 없는 라벨핀 → 방향/Enable/SD 등 GPIO 제어선 후보
            gp = _pin_to_gpio(pin)
            if gp and lbl:
                dir_gpios.append({"pin": pin, "label": lbl, "gpio": gp[0], "gpio_pin": gp[1]})

    # PWM 타이머: TIM1/TIM8 중 채널이 가장 많은 것 우선, 없으면 채널 최다 TIM
    def _ch_count(t: str) -> int:
        return len(tim_channels.get(t, []))
    pwm_candidates = [t for t in ("TIM1", "TIM8") if t in tim_channels]
    if pwm_candidates:
        pwm_timer = max(pwm_candidates, key=_ch_count)
    elif tim_channels:
        pwm_timer = max(tim_channels, key=_ch_count)
    else:
        pwm_timer = None

    # 엔코더 타이머: PWM 타이머가 아닌 TIM 중 CH1/CH2를 가진 것
    enc_timer = None
    for t, chs in tim_channels.items():
        if t == pwm_timer:
            continue
        sigs = {c for c, _ in chs}
        if "CH1" in sigs and "CH2" in sigs:
            enc_timer = t
            break

    return {
        "pwm_timer": pwm_timer,
        "pwm_handle": _peripheral_handle(pwm_timer) if pwm_timer else None,
        "pwm_channels": {c: pin for c, pin in tim_channels.get(pwm_timer, [])} if pwm_timer else {},
        "enc_timer": enc_timer,
        "enc_handle": _peripheral_handle(enc_timer) if enc_timer else None,
        "current_sense": current,
        "current_handles": sorted({_peripheral_handle(c["periph"]) for c in current}),
        "brk": brk,
        "fdcan": fdcan,
        "fdcan_handle": _peripheral_handle(fdcan) if fdcan else None,
        "dir_gpios": dir_gpios,
        "comms": vp.get("comms", []),
        "control_type": vp.get("control_type", ""),
        "encoder_type": vp.get("encoder_type", ""),
        "motor_count": vp.get("motor_count", 1),
    }


def check_required_peripherals(
    roles: Dict[str, Any], project_handles: Optional[Dict[str, str]],
) -> Tuple[List[str], List[str]]:
    """roles가 요구하는 주변장치(TIM/ADC/OPAMP/FDCAN)가 업로드 프로젝트에 실제 설정돼 있는지 점검.

    project_handles: parse_hal_project가 읽은 {periph:handle} (예: {'TIM3':'htim3'}).
        None이면(프로젝트 없음) 점검 불가 → 빈 결과.
    반환: (missing_periph[...], 사용자용 경고 메시지[...]).
    빠진 주변장치가 있으면 golden 모듈/glue가 TIM_HandleTypeDef·htimN·TIM_CHANNEL_x 등을
    참조하므로 빌드 에러가 난다(HAL 모듈 미활성 + tim.c 미생성).
    """
    if project_handles is None:
        return [], []
    required: Dict[str, str] = {}
    if roles.get("pwm_timer"):
        required[roles["pwm_timer"]] = "PWM 타이머"
    if roles.get("enc_timer"):
        required[roles["enc_timer"]] = "엔코더 타이머"
    if roles.get("fdcan"):
        required[roles["fdcan"]] = "FDCAN"
    for c in roles.get("current_sense", []):
        required[c["periph"]] = "전류 센싱"
    have = set(project_handles or {})
    missing = [(p, role) for p, role in required.items() if p not in have]
    msgs: List[str] = []
    for p, role in missing:
        msgs.append(
            f"{p}({role})가 업로드한 CubeMX 프로젝트에 설정돼 있지 않습니다 — "
            f"tim.c/adc.c 등 init과 핸들(h{p.lower()})이 없어 모듈/glue가 빌드되지 않습니다."
        )
    if missing:
        msgs.append(
            "해결: Step 2의 .ioc(주변장치가 활성화됨)로 CubeMX에서 코드 생성한 프로젝트를 "
            "업로드하세요. (빈/GPIO만 있는 프로젝트로는 모터 모듈을 통합할 수 없습니다.)"
        )
    return [p for p, _ in missing], msgs


def _label_macro(label: str) -> str:
    """CubeMX가 라벨로 만드는 매크로 베이스명. 'CURR_U' → 'CURR_U'(_Pin/_GPIO_Port 접미)."""
    s = re.sub(r"[^A-Za-z0-9]", "_", label).strip("_")
    if s and s[0].isdigit():
        s = "_" + s
    return s


def derive_binding(roles: Dict[str, Any], vp: Dict[str, Any]) -> Dict[str, Any]:
    """프로젝트 없이 validated_pins/roles만으로 바인딩을 결정론 유도(폴백).

    핸들명(TIM1→htim1)·GPIO 매크로(PA8→GPIOA/GPIO_PIN_8)는 결정론적.
    ADC 채널은 데이터시트/프로젝트 없으면 미상(placeholder).
    """
    handles: Dict[str, str] = {}
    for periph in filter(None, [roles.get("pwm_timer"), roles.get("enc_timer"), roles.get("fdcan")]):
        handles[periph] = _peripheral_handle(periph)
    for c in roles.get("current_sense", []):
        handles[c["periph"]] = _peripheral_handle(c["periph"])

    gpio_macros: Dict[str, Dict[str, str]] = {}
    for p in vp.get("pins", []):
        lbl = (p.get("label") or "").strip()
        gp = _pin_to_gpio((p.get("pin") or ""))
        if lbl and gp:
            base = _label_macro(lbl)
            gpio_macros[lbl] = {
                "pin_macro": f"{base}_Pin",
                "port_macro": f"{base}_GPIO_Port",
                "gpio": gp[0], "gpio_pin": gp[1],
            }

    return {
        "handles": handles,
        "gpio_macros": gpio_macros,
        "adc_channels": {},               # 프로젝트 파서가 채움
        "markers": {},                    # 프로젝트 파서가 채움
        "source": "derived",
    }


# ---------------------------------------------------------------------------
# 생성 HAL 프로젝트 파서 — 핸들/매크로/ADC채널/USER CODE 마커 ground truth
# ---------------------------------------------------------------------------

_USER_MARKERS = ["Includes", "PV", "PFP", "0", "1", "2", "3", "4", "WHILE"]


def _iter_project_files(project_dir: Path):
    for path in project_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".c", ".h"):
            yield path


def parse_hal_project(project_dir: Path) -> Dict[str, Any]:
    """CubeMX 생성 프로젝트에서 바인딩 정보 추출.

    main.h  : extern *_HandleTypeDef hXxxN; → 핸들, #define <Label>_Pin/_GPIO_Port → 매크로
    adc*.c  : sConfig.Channel = ADC_CHANNEL_x → 채널
    main.c  : USER CODE 마커 존재 여부
    """
    handles: Dict[str, str] = {}      # 'TIM1' → 'htim1'
    gpio_macros: Dict[str, Dict[str, str]] = {}
    adc_channels: List[str] = []
    markers: Dict[str, bool] = {}
    main_c_path: Optional[str] = None

    handle_re = re.compile(r"extern\s+\w+_HandleTypeDef\s+(h(\w+?))(\d+);")
    pin_def_re = re.compile(r"#define\s+(\w+)_Pin\s+(GPIO_PIN_\d+)")
    port_def_re = re.compile(r"#define\s+(\w+)_GPIO_Port\s+(GPIO[A-H])")
    adc_ch_re = re.compile(r"ADC_CHANNEL_\w+")

    for path in _iter_project_files(project_dir):
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        name = path.name.lower()

        if name == "main.h":
            for m in handle_re.finditer(txt):
                fam, num = m.group(2), m.group(3)
                handles[f"{fam.upper()}{num}"] = m.group(1) + num  # TIM1→htim1
            pins = {m.group(1): m.group(2) for m in pin_def_re.finditer(txt)}
            ports = {m.group(1): m.group(2) for m in port_def_re.finditer(txt)}
            for base, pin_macro in pins.items():
                gpio_macros[base] = {
                    "pin_macro": f"{base}_Pin",
                    "port_macro": f"{base}_GPIO_Port",
                    "gpio_pin": pin_macro,
                    "gpio": ports.get(base, ""),
                }
        if name.startswith("adc"):
            adc_channels.extend(sorted(set(adc_ch_re.findall(txt))))
        if name == "main.c":
            main_c_path = str(path)
            for mk in _USER_MARKERS:
                markers[mk] = f"USER CODE BEGIN {mk}" in txt

    return {
        "handles": handles,
        "gpio_macros": gpio_macros,
        "adc_channels": sorted(set(adc_channels)),
        "markers": markers,
        "main_c_path": main_c_path,
        "source": "project",
    }


def find_project_root(base: Path) -> Path:
    """추출/생성된 디렉토리에서 실제 프로젝트 루트를 찾는다.
    zip이 단일 폴더로 감싸져 있으면(예: MyProj/...) 그 폴더가 루트, 아니면 base 자체.
    → 업로드 구조(Core/Src·Src·Drivers·Makefile·.ioc 등)를 통째로 보존한다."""
    entries = [p for p in base.iterdir() if not p.name.startswith("__MACOSX")]
    subdirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if p.is_file()]
    if len(subdirs) == 1 and not files:
        return subdirs[0]
    return base


def _project_layout(project_dir: Path) -> Tuple[Path, Path]:
    """모듈 .c/.h를 둘 위치 (src_dir, inc_dir). CubeMX 레이아웃 자동 감지:
    Core/Src+Core/Inc (IDE 프로젝트) → Src+Inc (Makefile 프로젝트) → main.c가 있는 폴더."""
    for src_name, inc_name in (("Core/Src", "Core/Inc"), ("Src", "Inc")):
        s = project_dir / src_name
        if s.exists():
            i = project_dir / inc_name
            return s, (i if i.exists() else s)
    mc = next(iter(project_dir.rglob("main.c")), None)
    if mc:
        return mc.parent, mc.parent
    return project_dir, project_dir


def load_project_from_zip(zip_bytes: bytes, dest_dir: Path) -> Path:
    """업로드 ZIP을 dest_dir에 풀고 프로젝트 루트 경로 반환(구조 보존)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(dest_dir)
    return find_project_root(dest_dir)


# ---------------------------------------------------------------------------
# 핀맵/역할 요약 (LLM 컨텍스트용)
# ---------------------------------------------------------------------------

def _roles_summary(vp: Dict[str, Any], roles: Dict[str, Any], binding: Dict[str, Any]) -> str:
    lines = [
        f"칩: {vp.get('chip', '')}",
        f"제어방식: {roles.get('control_type') or '(미지정)'}, 엔코더: {roles.get('encoder_type') or '(미지정)'}, 모터 수: {roles.get('motor_count', 1)}",
        f"PWM 타이머: {roles.get('pwm_timer')} (핸들 {roles.get('pwm_handle')}), 채널: {roles.get('pwm_channels')}",
        f"엔코더 타이머: {roles.get('enc_timer')} (핸들 {roles.get('enc_handle')})",
        f"전류 센싱: {[ (c['periph'], c['pin'], c['label']) for c in roles.get('current_sense', []) ]}",
        f"BRK: {roles.get('brk')}",
        f"FDCAN: {roles.get('fdcan')} (핸들 {roles.get('fdcan_handle')})",
        f"방향/제어 GPIO: {[ (g['label'], g['pin']) for g in roles.get('dir_gpios', []) ]}",
        "",
        f"[바인딩 출처: {binding.get('source')}]",
        f"핸들 맵: {binding.get('handles')}",
        f"ADC 채널(프로젝트): {binding.get('adc_channels')}",
        "GPIO 라벨 매크로(이 이름을 glue에서 그대로 사용):",
    ]
    for lbl, mac in binding.get("gpio_macros", {}).items():
        lines.append(f"  {lbl}: {mac['port_macro']} / {mac['pin_macro']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# glue 블록 파싱
# ---------------------------------------------------------------------------

GLUE_MARKERS = ["Includes", "PV", "2", "3"]


def _parse_glue_blocks(response: str) -> Dict[str, str]:
    """'/* ==== GLUE:Includes ==== */' 센티넬로 구분된 블록 추출."""
    out: Dict[str, str] = {}
    pattern = re.compile(
        r"/\*\s*====\s*GLUE:(\w+)\s*====\s*\*/(.*?)(?=/\*\s*====\s*GLUE:|\Z)",
        re.DOTALL,
    )
    for m in pattern.finditer(response):
        key = m.group(1)
        body = m.group(2)
        # 코드펜스가 섞여 있으면 제거
        body = re.sub(r"```(?:c|cpp)?", "", body).strip()
        if key in GLUE_MARKERS:
            out[key] = body
    return out


# ---------------------------------------------------------------------------
# 메인 에이전트
# ---------------------------------------------------------------------------

class Step3Agent:
    def __init__(self, ollama_url: str = "http://localhost:11434",
                 qdrant_url: str = "http://localhost:6333"):
        self.ollama_url = ollama_url.rstrip("/")
        self.qdrant_url = qdrant_url.rstrip("/")

    # ── 모델/임베딩 ──────────────────────────────────────────────────────
    def _available_model(self) -> str:
        try:
            r = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if r.status_code == 200:
                names = [m["name"] for m in r.json().get("models", [])]
                for pref in ("gemma4:31b", "gemma4", "qwen2.5:32b", "qwen2.5:7b", "qwen2.5"):
                    for n in names:
                        if n.startswith(pref.split(":")[0]):
                            return n
        except Exception:
            pass
        return "gemma4:31b"

    def _embed(self, text: str) -> List[float]:
        if _get_bge_model is not None:
            try:
                return _get_bge_model().encode(text, normalize_embeddings=True).tolist()
            except Exception as e:
                logger.warning("embed 실패: %s", e)
        return [0.0] * 1024

    # ── 코드 RAG (opensource 알고리즘, 참고 컨텍스트 전용) ───────────────
    def rag_query_code(self, query: str, top_k: int = 4,
                       prefer_permissive: bool = True) -> List[Dict[str, Any]]:
        """stm32g4_code 컬렉션 검색 → [{text, source, license, section, chunk_id, permissive}]."""
        try:
            body = {"vector": self._embed(query), "limit": top_k * 2, "with_payload": True}
            r = requests.post(
                f"{self.qdrant_url}/collections/{CODE_COLLECTION}/points/search",
                json=body, timeout=10,
            )
            if r.status_code != 200:
                return []
            hits = []
            for h in r.json().get("result", []):
                p = h.get("payload", {})
                hits.append({
                    "text": p.get("text", ""),
                    "source": p.get("source", ""),
                    "license": p.get("license", ""),
                    "permissive": p.get("permissive", True),
                    "section": p.get("section", ""),
                    "chunk_id": p.get("chunk_id", ""),
                })
            # permissive(MIT/Apache/ST) 우선 정렬 후 top_k
            if prefer_permissive:
                hits.sort(key=lambda x: 0 if x.get("permissive") else 1)
            return hits[:top_k]
        except Exception as e:
            logger.warning("코드 RAG 실패: %s", e)
            return []

    # ── glue 생성 (LLM은 알고리즘 구조만) ───────────────────────────────
    def _llm_glue(
        self,
        module_apis: str,
        roles_summary: str,
        rag_chunks: List[Dict[str, Any]],
        model: str,
        user_prompt: str,
    ) -> Tuple[Dict[str, str], List[str]]:
        """golden 모듈 API + 실제 바인딩 + RAG로 main.c 주입용 glue 4블록 생성.

        반환: (blocks{Includes,PV,2,3}, 사용한 chunk_id 목록)
        """
        ref_text = ""
        used_ids: List[str] = []
        for i, c in enumerate(rag_chunks, 1):
            used_ids.append(c.get("chunk_id", ""))
            ref_text += (
                f"\n[참고{i} | source={c['source']} license={c['license']} "
                f"chunk_id={c['chunk_id']} | {c['section']}]\n{c['text'][:1200]}\n"
            )

        system = (
            "당신은 STM32G4 HAL 모터 제어 펌웨어 통합 전문가입니다. "
            "Golden Module의 .c/.h 내부는 절대 수정하지 마세요. "
            "대신 main.c의 USER CODE 마커에 들어갈 '바인딩 glue 코드'만 생성합니다. "
            "Golden 모듈은 핸들/채널/GPIO를 구조체로 주입받으므로, 제공된 실제 핸들명·"
            "GPIO 라벨 매크로를 그대로 사용해 구조체를 채우고 init/제어루프를 작성하세요. "
            "참고 코드(opensource)는 알고리즘 아이디어 참조용일 뿐 — 절대 그대로 복사하지 말고, "
            "반드시 Golden 모듈의 API로 재구성하세요(GPL 코드 복사 금지). "
            "핸들/매크로 이름을 추측하지 말고 제공된 것만 사용하세요."
        )

        user = f"""## 사용자 알고리즘 요구사항
{user_prompt.strip() or "(요구사항 없음 — 핀맵 기반 기본 통합)"}

## 실제 하드웨어 바인딩 (이 이름들을 그대로 사용)
{roles_summary}

## Golden Module 공개 API (.h)
{module_apis}

## 참고 알고리즘 코드 (verbatim 복사 금지, 아이디어만)
{ref_text or "(참고 청크 없음)"}

## 출력 형식 — 아래 4개 블록만, 각 센티넬 주석으로 구분해 순서대로 출력:
/* ==== GLUE:Includes ==== */   ← 모듈 헤더 #include (USER CODE Includes)
/* ==== GLUE:PV ==== */         ← 구조체 인스턴스 전역 선언 (USER CODE PV)
/* ==== GLUE:2 ==== */          ← init: 구조체 채움 + *_Init + PWM/ENC Start (USER CODE 2)
/* ==== GLUE:3 ==== */          ← while(1) 루프 본문: 제어 API 호출 (USER CODE 3)

각 블록은 순수 C 코드. 마커 외 설명/마크다운 금지."""

        payload = {
            "model": model,
            "prompt": user,
            "system": system,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 8192},
        }
        try:
            r = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=600)
            r.raise_for_status()
            resp = r.json().get("response", "")
            return _parse_glue_blocks(resp), [c for c in used_ids if c]
        except Exception as e:
            logger.error("glue 생성 실패: %s", e)
            return {}, []

    # ── USER CODE 마커 주입 ──────────────────────────────────────────────
    @staticmethod
    def inject_into_marker(hal_src: str, code: str, marker: str = "2") -> str:
        """USER CODE BEGIN {marker} ~ END {marker} 사이에 코드 주입(기존 내용 보존, append)."""
        if not code.strip():
            return hal_src
        pattern = (
            rf"(/\* USER CODE BEGIN {re.escape(marker)} \*/)"
            rf"(.*?)"
            rf"(/\* USER CODE END {re.escape(marker)} \*/)"
        )
        tag = "/* ---- Step3 generated glue ---- */"
        def _repl(m: re.Match) -> str:
            existing = m.group(2)
            if tag in existing:                       # idempotent: 이미 주입됨
                return m.group(0)
            return f"{m.group(1)}{existing}\n{tag}\n{code}\n{m.group(3)}"
        return re.sub(pattern, _repl, hal_src, count=1, flags=re.DOTALL)

    # ── 빌드 통합: 모듈 복사 + 빌드시스템 등록 + main.c 주입 ─────────────
    def integrate(
        self,
        project_dir: Path,
        modules: List[str],
        glue: Dict[str, str],
    ) -> Dict[str, Any]:
        """프로젝트 디렉토리(in-place)에 golden 모듈을 추가하고 glue를 주입."""
        report: Dict[str, Any] = {"copied": [], "build_registered": None, "injected_markers": []}

        src_dir, inc_dir = _project_layout(project_dir)
        src_dir.mkdir(parents=True, exist_ok=True)
        inc_dir.mkdir(parents=True, exist_ok=True)

        # 1) 모듈 파일 복사 + 등록용 상대 .c 경로 수집
        copied_c_rel: List[str] = []
        for name in modules:
            for ext, dst in (("c", src_dir), ("h", inc_dir)):
                srcf = GOLDEN_DIR / f"{name}.{ext}"
                if srcf.exists():
                    shutil.copy2(srcf, dst / srcf.name)
                    rel = (dst / srcf.name).relative_to(project_dir)
                    report["copied"].append(str(rel))
                    if ext == "c":
                        copied_c_rel.append(rel.as_posix())

        # 2) 빌드시스템 등록 (실제 복사 위치의 상대경로 사용)
        report["build_registered"] = self._register_build(project_dir, copied_c_rel)

        # 3) main.c 마커 주입
        main_c = next(iter(project_dir.rglob("main.c")), None)
        if main_c:
            txt = main_c.read_text(encoding="utf-8", errors="ignore")
            for mk in GLUE_MARKERS:
                if glue.get(mk):
                    new = self.inject_into_marker(txt, glue[mk], mk)
                    if new != txt:
                        report["injected_markers"].append(mk)
                    txt = new
            main_c.write_text(txt, encoding="utf-8")

        return report

    def _register_build(self, project_dir: Path, rel_srcs: List[str]) -> str:
        """Makefile(C_SOURCES) 또는 CMakeLists에 모듈 .c 등록. idempotent.
        rel_srcs: 프로젝트 루트 기준 모듈 .c 상대경로(레이아웃에 맞게 계산됨)."""
        if not rel_srcs:
            return "none (등록할 .c 없음)"

        makefile = next((p for p in project_dir.rglob("Makefile")), None)
        if makefile:
            txt = makefile.read_text(encoding="utf-8", errors="ignore")
            adds = [s for s in rel_srcs if s not in txt]
            if adds:
                block = "\n# Step3 golden modules\n" + "".join(f"C_SOURCES += {s}\n" for s in adds)
                # C_SOURCES 정의 끝 이후에 append (없으면 파일 끝)
                txt = txt.rstrip() + "\n" + block
                makefile.write_text(txt, encoding="utf-8")
            return f"Makefile (+{len(adds)})"

        cmake = next((p for p in project_dir.rglob("CMakeLists.txt")), None)
        if cmake:
            txt = cmake.read_text(encoding="utf-8", errors="ignore")
            if "Step3 golden modules" not in txt:
                srcs = " ".join(rel_srcs)
                txt = txt.rstrip() + (
                    f"\n\n# Step3 golden modules\n"
                    f"target_sources(${{CMAKE_PROJECT_NAME}} PRIVATE {srcs})\n"
                )
                cmake.write_text(txt, encoding="utf-8")
            return "CMakeLists.txt"

        return "none (수동 등록 필요)"

    # ── 메인 파이프라인 ──────────────────────────────────────────────────
    def run(
        self,
        vp: Dict[str, Any],
        prompt: str = "",
        project_dir: Optional[Path] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Step 3 전체 파이프라인.

        project_dir 가 주어지면 그 안의 생성 HAL 프로젝트를 ground truth로 쓰고
        glue를 in-place 주입(+모듈 복사·빌드등록). 없으면 결정론 유도 바인딩으로
        glue만 생성(주입 없음).
        """
        def _cb(pct: int, msg: str) -> None:
            logger.info("[Step3 %3d%%] %s", pct, msg)
            if progress_cb:
                progress_cb(pct, msg)

        # 1) 역할 매핑 (결정론)
        _cb(5, "역할 매핑(결정론) 중...")
        roles = map_roles(vp)
        selected = select_modules(vp)
        _cb(12, f"모듈 선택: {', '.join(selected)}")

        # 2) 바인딩: 프로젝트 우선, 없으면 결정론 유도
        proj_handles: Optional[Dict[str, str]] = None
        if project_dir is not None:
            _cb(18, "생성 프로젝트 파싱(핸들/매크로/ADC) 중...")
            binding = parse_hal_project(project_dir)
            proj_handles = dict(binding["handles"])   # 프로젝트 실제 핸들(보완 병합 전)
            # 프로젝트에 없는 핸들은 결정론 유도로 보완
            derived = derive_binding(roles, vp)
            for k, v in derived["handles"].items():
                binding["handles"].setdefault(k, v)
            for k, v in derived["gpio_macros"].items():
                binding["gpio_macros"].setdefault(k, v)
        else:
            binding = derive_binding(roles, vp)
        roles_summary = _roles_summary(vp, roles, binding)

        # 2.5) 주변장치 사전 점검 — roles가 요구하는 TIM/ADC/OPAMP/FDCAN이 업로드 프로젝트에
        #      실제로 설정돼 있는지(=핸들 존재). 없으면 모듈/glue가 컴파일 안 됨(빌드에러).
        periph_missing, periph_warnings = check_required_peripherals(roles, proj_handles)
        for _w in periph_warnings:
            _cb(20, f"⚠ {_w}")

        # 3) 코드 RAG (opensource 알고리즘 참고)
        _cb(30, "코드 RAG 검색 중...")
        rag_query = (
            f"{vp.get('chip','STM32G4')} {roles.get('control_type','')} motor control "
            f"{roles.get('encoder_type','')} {' '.join(roles.get('comms', []))} "
            f"{prompt}"
        ).strip()
        rag_chunks = self.rag_query_code(rag_query, top_k=4)
        _cb(38, f"RAG 청크 {len(rag_chunks)}건")

        # 4) glue 생성 (LLM)
        model = self._available_model()
        module_apis = "\n\n".join(
            f"// {name}.h\n{_read_module(name).get('h','')}" for name in selected
        )
        _cb(45, f"glue 생성 중 (모델 {model})...")
        glue, used_chunk_ids = self._llm_glue(module_apis, roles_summary, rag_chunks, model, prompt)
        _cb(80, f"glue 블록 {len(glue)}개 생성")

        # 5) 통합 (프로젝트가 있을 때만 주입)
        integration = None
        if project_dir is not None:
            _cb(88, "모듈 복사 + 빌드등록 + main.c 주입 중...")
            integration = self.integrate(project_dir, selected, glue)

        _cb(95, "정리 완료")

        # 호환용 modules(원본 golden) — 미리보기/다운로드
        modules_out: Dict[str, Dict[str, str]] = {}
        for name in selected:
            raw = _read_module(name)
            modules_out[name] = {"h": raw.get("h", ""), "c": raw.get("c", "")}

        return {
            "selected": selected,
            "roles": roles,
            "binding": binding,
            "glue": glue,
            "rag_sources": [
                {"source": c["source"], "license": c["license"],
                 "chunk_id": c["chunk_id"], "section": c["section"]}
                for c in rag_chunks
            ],
            "used_chunk_ids": used_chunk_ids,
            "modules": modules_out,
            "integration": integration,
            "pinmap_summary": roles_summary,
            "peripheral_missing": periph_missing,
            "peripheral_warnings": periph_warnings,
            # 빌드 가능 추정: 프로젝트가 있고 필요한 주변장치가 모두 존재할 때만.
            "buildable": (project_dir is not None and not periph_missing),
        }
