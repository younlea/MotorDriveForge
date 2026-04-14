#!/usr/bin/env python3
"""
PDF → 텍스트 파싱
입력: dataset/official_docs/ 하위 모든 .pdf
출력: dataset/parsed_text/{카테고리}/{파일명}.txt + metadata.json

필요 패키지: pdfplumber
설치: pip install pdfplumber
"""

import argparse
import json
import logging
from pathlib import Path

import pdfplumber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path("/Users/younlea/source_code/MotorDriveForge")
DEFAULT_INPUT = BASE_DIR / "dataset/official_docs"
DEFAULT_OUTPUT = BASE_DIR / "dataset/parsed_text"


def parse_pdf(pdf_path: Path, output_dir: Path) -> dict | None:
    """단일 PDF 파싱 → 텍스트 + 메타데이터"""
    # 카테고리: official_docs 하위 첫 번째 디렉토리 이름
    try:
        rel = pdf_path.relative_to(DEFAULT_INPUT)
        category = rel.parts[0] if len(rel.parts) > 1 else "misc"
    except ValueError:
        category = "misc"

    out_dir = output_dir / category
    out_dir.mkdir(parents=True, exist_ok=True)

    txt_path = out_dir / (pdf_path.stem + ".txt")
    meta_path = out_dir / (pdf_path.stem + "_meta.json")

    if txt_path.exists():
        log.info("[SKIP] %s", pdf_path.name)
        return None

    try:
        pages_text = []
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages_text.append(text)

        full_text = "\n\n".join(pages_text)
        txt_path.write_text(full_text, encoding="utf-8")

        meta = {
            "source_path": str(pdf_path),
            "category": category,
            "page_count": page_count,
            "char_count": len(full_text),
            "filename": pdf_path.name,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        log.info("[OK] %s — %d 페이지, %d자", pdf_path.name, page_count, len(full_text))
        return meta

    except Exception as e:
        log.warning("[FAIL] %s: %s", pdf_path.name, e)
        return None


def main():
    parser = argparse.ArgumentParser(description="PDF → 텍스트 파싱")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = list(input_dir.rglob("*.pdf"))
    log.info("PDF 파일 %d개 발견", len(pdf_files))

    results = []
    for pdf in pdf_files:
        meta = parse_pdf(pdf, output_dir)
        if meta:
            results.append(meta)

    log.info("파싱 완료: %d개 처리", len(results))

    # 전체 메타데이터 인덱스
    index_path = output_dir / "index.json"
    index_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("인덱스 저장: %s", index_path)


if __name__ == "__main__":
    main()
