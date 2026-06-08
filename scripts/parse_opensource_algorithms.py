#!/usr/bin/env python3
"""
오픈소스 프로젝트에서 *제어 알고리즘 코드*를 함수 단위로 추출 → 코드 RAG 청크 생성
목적: Step 3 Golden Module 적응(glue 생성) 시 LLM의 *참고 컨텍스트*로 활용
      (golden_modules가 출력 실체. opensource는 verbatim 복사 금지, chunk_id 인용용)

핀 정보만 뽑는 parse_opensource_code.py 와는 별개다. 이쪽은 알고리즘 본문.

입력: dataset/opensource/ 하위 프로젝트들
출력: dataset/chunks_code/opensource_algo_chunks.jsonl
      (Step1 컬렉션 오염 방지를 위해 dataset/chunks/ 가 아닌 별도 디렉토리)

인덱싱(별도 컬렉션):
    python scripts/embed_and_index.py \
        --chunks-dir dataset/chunks_code --collection stm32g4_code

라이선스 주의: VESC/MESC = GPL → license 태그로 구분. permissive(MIT/Apache/ST) 우선.
필요 패키지: 없음 (표준 라이브러리만 사용)
"""

import json
import logging
import re
import uuid
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
OPENSOURCE_DIR = BASE_DIR / "dataset/opensource"
OUTPUT_DIR = BASE_DIR / "dataset/chunks_code"
OUTPUT_PATH = OUTPUT_DIR / "opensource_algo_chunks.jsonl"

# 소스 디렉토리 → (라이선스, permissive 여부). permissive=True가 우선 인용 대상.
SOURCES = {
    "Arduino-FOC":  ("MIT",        True),
    "STM32CubeG4":  ("ST-BSD3",    True),
    "moteus":       ("Apache-2.0", True),
    "stm32-esc":    ("MIT",        True),
    "MESC_FOC_ESC": ("GPL-3.0",    False),
    "bldc_vesc":    ("GPL-3.0",    False),
}

# 알고리즘 관련성 키워드 — 파일 선별 + 청크 태깅에 사용
ALGO_KEYWORDS = [
    "foc", "svpwm", "svm", "space_vector", "clarke", "park", "inverse_park",
    "pid", "current_loop", "speed_loop", "position_loop", "cascade",
    "observer", "sensorless", "bemf", "back_emf", "flux", "torque",
    "commutation", "six_step", "sixstep", "6step", "hall", "encoder",
    "openloop", "deadtime", "dead_time", "modulation", "duty", "dq",
    "id_ref", "iq_ref", "ocp", "ovp", "uvlo", "fault", "protection",
]

# 파일/디렉토리 제외 (테스트·빌드·서드파티)
SKIP_PATH_PARTS = {
    ".git", "test", "tests", "unittest", "examples_old", "doc", "docs",
    "third_party", "thirdparty", "cmsis", "hal_driver", "drivers", "ldscript",
}

SRC_EXT = {".c", ".cpp", ".cc", ".h", ".hpp"}
MAX_FILE_BYTES = 200_000
MAX_CHUNKS_PER_PROJECT = 400
MIN_BODY_LINES = 6          # 너무 짧은 getter/setter 제외
MAX_CHUNK_CHARS = 2500

# C/C++ 함수 정의 시그니처(대략): 타입 ... name(args) {
FUNC_SIG_RE = re.compile(
    r"^[A-Za-z_][\w\s\*\(\),:<>]*?\b([A-Za-z_]\w+)\s*\([^;{}]*\)\s*\{",
    re.MULTILINE,
)


def make_chunk(doc_id: str, text: str, source: str, section: str,
               license_: str, permissive: bool, keywords: list[str]) -> dict:
    return {
        "doc_id": doc_id,
        "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}::{text[:80]}")),
        "chunk_index": 0,                 # main()에서 재부여
        "source": source,
        "category": "opensource_algo",
        "license": license_,
        "permissive": permissive,
        "keywords": sorted(set(keywords)),
        "section": section,
        "page_start": 1,
        "page_end": 1,
        "text": text,
        "token_estimate": len(text) // 4,
    }


def _matched_keywords(text: str) -> list[str]:
    low = text.lower()
    return [k for k in ALGO_KEYWORDS if k in low]


def _is_interesting_file(path: Path, head: str) -> bool:
    hay = (str(path).lower() + "\n" + head.lower())
    return any(k in hay for k in ALGO_KEYWORDS)


def _extract_functions(content: str) -> list[tuple[str, str]]:
    """(함수명, 함수 전체 텍스트) 목록. 중괄호 매칭으로 본문 끝 탐지."""
    out: list[tuple[str, str]] = []
    for m in FUNC_SIG_RE.finditer(content):
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "return", "sizeof"):
            continue
        brace_start = content.index("{", m.start())
        depth = 0
        i = brace_start
        end = -1
        while i < len(content):
            c = content[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1
        if end == -1:
            continue
        body = content[m.start():end]
        if body.count("\n") + 1 < MIN_BODY_LINES:
            continue
        out.append((name, body))
    return out


def parse_project(name: str) -> list[dict]:
    base = OPENSOURCE_DIR / name
    if not base.exists() or not any(base.iterdir() if base.is_dir() else []):
        log.warning("%s: 디렉토리 없음/비어있음(서브모듈 미초기화?) — 건너뜀", name)
        return []

    license_, permissive = SOURCES[name]
    chunks: list[dict] = []

    for path in sorted(base.rglob("*")):
        if len(chunks) >= MAX_CHUNKS_PER_PROJECT:
            log.info("%s: 청크 상한(%d) 도달 — 이후 파일 생략", name, MAX_CHUNKS_PER_PROJECT)
            break
        if path.suffix.lower() not in SRC_EXT or not path.is_file():
            continue
        if any(part.lower() in SKIP_PATH_PARTS for part in path.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        head = content[:4096]
        if not _is_interesting_file(path, head):
            continue

        rel = path.relative_to(OPENSOURCE_DIR)
        for fname, body in _extract_functions(content):
            kws = _matched_keywords(body)
            if not kws:                       # 함수 자체에 알고리즘 키워드 없으면 제외
                continue
            text = body[:MAX_CHUNK_CHARS]
            doc_id = f"{name.lower().replace('-', '')}_{path.stem}"
            section = f"{rel} :: {fname}()"
            chunks.append(make_chunk(doc_id, text, name, section, license_, permissive, kws))
            if len(chunks) >= MAX_CHUNKS_PER_PROJECT:
                break

    log.info("%s [%s%s]: %d 청크", name, license_,
             "" if permissive else " ⚠GPL", len(chunks))
    return chunks


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict] = []
    for name in SOURCES:
        all_chunks.extend(parse_project(name))

    # doc_id별 chunk_index 재부여
    counters: dict[str, int] = defaultdict(int)
    for chunk in all_chunks:
        chunk["chunk_index"] = counters[chunk["doc_id"]]
        counters[chunk["doc_id"]] += 1

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    n_perm = sum(1 for c in all_chunks if c["permissive"])
    log.info("완료: 총 %d 청크 (permissive=%d, GPL=%d) → %s",
             len(all_chunks), n_perm, len(all_chunks) - n_perm, OUTPUT_PATH)


if __name__ == "__main__":
    main()
