import os
import re
import json
import time
import requests

from datetime import datetime
from urllib.parse import quote
from playwright.sync_api import sync_playwright


# =========================
# 配置
# =========================

KEYWORDS = "solid-state batteries conductivity"
MAX_PAGES = 3
MAX_NEW_PER_DAY = 20

OUTPUT_DIR = r"D:\AutoCrawler\PatentScraper"
STATE_FILE = os.path.join(OUTPUT_DIR, "patent_downloaded_records.json")

HEADLESS = True

BASE_URL = "https://patents.google.com"


# =========================
# 工具
# =========================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def build_search_url(keywords, page):

    q = f"({keywords.replace(' ', '+')})"

    return f"https://patents.google.com/?q={q}&page={page}"


def load_state():
    if not os.path.exists(STATE_FILE):
        return set()

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_state(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(data), f, indent=2)


# =========================
# 下载PDF
# =========================

def download_pdf(url, path):

    try:

        r = requests.get(url, timeout=60)

        if r.status_code != 200:
            log("下载失败")
            return False

        with open(path, "wb") as f:
            f.write(r.content)

        log(f"下载完成 {path}")
        return True

    except Exception as e:

        log(f"下载异常 {e}")
        return False


# =========================
# 抓取PDF链接
# =========================

def crawl_pdf_links():

    results = []

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=HEADLESS)

        page = browser.new_page()

        for i in range(1, MAX_PAGES + 1):

            url = build_search_url(KEYWORDS, i)

            log(f"打开 {url}")

            page.goto(url)

            page.wait_for_timeout(3000)

            pdf_links = page.locator("a.pdfLink")

            count = pdf_links.count()

            log(f"发现PDF数量 {count}")

            for j in range(count):

                a = pdf_links.nth(j)

                href = a.get_attribute("href")

                text = a.inner_text().strip()

                if not href:
                    continue

                patent_no = text

                results.append({
                    "patent_no": patent_no,
                    "pdf_url": href
                })

        browser.close()

    return results


# =========================
# 主程序
# =========================

def main():

    ensure_dir(OUTPUT_DIR)

    downloaded = load_state()

    log(f"历史记录 {len(downloaded)}")

    candidates = crawl_pdf_links()

    log(f"候选专利 {len(candidates)}")

    new_items = []

    for item in candidates:

        if item["patent_no"] in downloaded:
            continue

        new_items.append(item)

        if len(new_items) >= MAX_NEW_PER_DAY:
            break

    log(f"今日下载 {len(new_items)}")

    for item in new_items:

        patent_no = item["patent_no"]
        pdf_url = item["pdf_url"]

        filename = sanitize_filename(patent_no) + ".pdf"

        path = os.path.join(OUTPUT_DIR, filename)

        log(f"下载 {patent_no}")

        ok = download_pdf(pdf_url, path)

        if ok:
            downloaded.add(patent_no)

        time.sleep(1)

    save_state(downloaded)

    log("完成")


if __name__ == "__main__":
    main()