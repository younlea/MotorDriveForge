#!/usr/bin/env python3
"""
ST 커뮤니티 포럼 Q&A 수집기
URL: https://community.st.com/t5/stm32-mcus-motor-control/bd-p/mcu-motor-control-forum
출력: dataset/forum_qa/st_forum_qa.jsonl

필요 패키지: requests, beautifulsoup4, tqdm
설치: pip install requests beautifulsoup4 tqdm
"""

import argparse
import json
import logging
import random
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://community.st.com"
BOARD_URL = f"{BASE_URL}/t5/stm32-mcus-motor-control/bd-p/mcu-motor-control-forum"

PRIORITY_KEYWORDS = [
    "overcurrent protection triggered",
    "deadtime calculation bldc",
    "opamp offset calibration",
    "tim1 tim8 synchronization",
    "encoder count error",
    "hall sensor commutation",
    "bootstrap capacitor",
    "multi motor foc",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _sleep():
    time.sleep(random.uniform(1.0, 2.5))


def get_thread_urls(session: requests.Session, max_pages: int = 30) -> list[str]:
    """게시판 목록 페이지에서 스레드 URL 수집"""
    urls = []
    for page in range(1, max_pages + 1):
        page_url = f"{BOARD_URL}?page={page}"
        try:
            r = session.get(page_url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Lithium 포럼 구조: message-subject 링크
            links = soup.select("a.message-subject, a.page-link, h3.message-subject a")
            if not links:
                # 대안 셀렉터
                links = soup.select("a[href*='/t5/stm32-mcus-motor-control/']")

            new_urls = [
                urljoin(BASE_URL, a["href"])
                for a in links
                if a.get("href") and "/td-p/" in a["href"]
            ]
            if not new_urls:
                log.info("페이지 %d: 더 이상 스레드 없음, 중단", page)
                break
            urls.extend(new_urls)
            log.info("페이지 %d: %d개 스레드 수집 (누적 %d)", page, len(new_urls), len(urls))
            _sleep()
        except Exception as e:
            log.warning("페이지 %d 오류: %s", page, e)
    return list(dict.fromkeys(urls))  # 중복 제거


def _is_relevant(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in PRIORITY_KEYWORDS)


def parse_thread(session: requests.Session, url: str) -> dict | None:
    """스레드에서 에러-원인-해결 트리플릿 추출"""
    try:
        r = session.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        title_el = soup.select_one("h1.page-title, h1.lia-message-subject")
        title = title_el.get_text(strip=True) if title_el else ""

        # 본문 메시지들
        messages = soup.select(
            "div.lia-message-body-content, div.message-body, div[class*='lia-message-body']"
        )
        if not messages:
            return None

        question_text = messages[0].get_text(" ", strip=True) if messages else ""
        answer_texts = [m.get_text(" ", strip=True) for m in messages[1:6]]

        full_text = title + " " + question_text + " " + " ".join(answer_texts)
        if not _is_relevant(full_text):
            return None

        # 수용 가능한 답변 표시 (Accepted Solution)
        solution_el = soup.select_one(".lia-component-accepted-solution .lia-message-body-content")
        solution_text = solution_el.get_text(" ", strip=True) if solution_el else ""
        if not solution_text and answer_texts:
            solution_text = answer_texts[0]

        return {
            "error": title,
            "cause": question_text[:800],
            "solution": solution_text[:800],
            "url": url,
            "keywords": [kw for kw in PRIORITY_KEYWORDS if kw in full_text.lower()],
        }
    except Exception as e:
        log.debug("스레드 파싱 오류 %s: %s", url, e)
        return None


def main():
    parser = argparse.ArgumentParser(description="ST 포럼 Q&A 수집기")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-items", type=int, default=300)
    parser.add_argument(
        "--output",
        default="dataset/forum_qa/st_forum_qa.jsonl",
    )
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 이미 수집된 URL 로드 (재시작 내성)
    existing_urls: set[str] = set()
    existing_count = 0
    if out_path.exists():
        with open(out_path) as f:
            for line in f:
                obj = json.loads(line)
                existing_urls.add(obj["url"])
                existing_count += 1
    log.info("기존 수집 건수: %d", existing_count)

    session = requests.Session()

    log.info("스레드 URL 수집 중 (최대 %d 페이지)...", args.max_pages)
    thread_urls = get_thread_urls(session, args.max_pages)
    new_urls = [u for u in thread_urls if u not in existing_urls]
    log.info("신규 스레드 %d건 처리 예정", len(new_urls))

    collected = existing_count
    with open(out_path, "a", encoding="utf-8") as f:
        for url in tqdm(new_urls, desc="스레드 파싱"):
            if collected >= args.max_items:
                break
            record = parse_thread(session, url)
            if record:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                collected += 1
            _sleep()

    log.info("완료: 총 %d건 저장 → %s", collected, out_path)


if __name__ == "__main__":
    main()
