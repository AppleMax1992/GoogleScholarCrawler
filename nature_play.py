import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin, urlparse, unquote
from datetime import datetime

from playwright.sync_api import sync_playwright

import oss2


# =========================
# 配置区
# =========================
KEYWORDS = "solid-state batteries Conductivity"
MAX_NEW_PER_DAY = 100
MAX_PAGES = 30
BASE_URL = "https://www.nature.com/search"

OUTPUT_DIR = r"D:\GoogleScholarCrawler-master\NatureScraper"
STATE_FILE = os.path.join(OUTPUT_DIR, "downloaded_records.json")
TODAY_LOG = os.path.join(OUTPUT_DIR, f"nature_{datetime.now().strftime('%Y%m%d')}.json")

# 是否直接下载 PDF
DOWNLOAD_PDF = True

# 是否上传 OSS
UPLOAD_TO_OSS = False

OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET")
OSS_ENDPOINT = "https://oss-cn-shanghai.aliyuncs.com"
OSS_BUCKET_NAME = "solid-state"
OSS_PREFIX = "固态电池知识库/文献/nature/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.nature.com/",
}


# =========================
# 通用工具
# =========================
def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name: str, max_len: int = 180) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:max_len]


def load_downloaded_records() -> set[str]:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data)
    except Exception:
        return set()


def save_downloaded_records(records: set[str]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(records), f, ensure_ascii=False, indent=2)


def append_today_log(items: list[dict]) -> None:
    with open(TODAY_LOG, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def guess_filename_from_title_or_url(title: str, pdf_url: str | None) -> str:
    # 1️⃣ 优先使用 title
    if title:
        name = sanitize_filename(title)
        if name:
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            return name

    # 2️⃣ 如果 title 不可用，再从 pdf_url 推断
    if pdf_url:
        path = urlparse(pdf_url).path
        base = os.path.basename(path)
        base = unquote(base).strip()

        if base:
            if not base.lower().endswith(".pdf"):
                base += ".pdf"
            return sanitize_filename(base)

    # 3️⃣ 最终兜底
    return "nature_article.pdf"


# =========================
# Playwright 抓搜索结果
# =========================
def fetch_search_results_by_playwright(keywords: str, max_pages: int) -> list[dict]:
    all_articles = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN",
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
            },
        )
        page = context.new_page()

        # 先访问首页，建立 cookie / 会话
        print("[INFO] 打开 Nature 首页初始化会话")
        page.goto("https://www.nature.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        for page_num in range(1, max_pages + 1):
            search_url = f"{BASE_URL}?q={quote(keywords)}&page={page_num}"
            print(f"[INFO] 搜索页: {search_url}")

            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
            except Exception as e:
                print(f"[WARN] 第 {page_num} 页加载失败: {e}")
                continue

            current_url = page.url
            print(f"[DEBUG] 当前页面 URL: {current_url}")

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            page_articles = extract_articles_from_search_html(soup)
            print(f"[INFO] 第 {page_num} 页解析到 {len(page_articles)} 篇")

            if not page_articles:
                # 保存调试页
                debug_file = os.path.join(OUTPUT_DIR, f"debug_page_{page_num}.html")
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"[WARN] 第 {page_num} 页无结果，已保存调试 HTML: {debug_file}")

            for item in page_articles:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                all_articles.append(item)

            time.sleep(1)

        browser.close()

    return all_articles


def extract_articles_from_search_html(soup: BeautifulSoup) -> list[dict]:
    articles = []
    seen_page = set()

    # 优先抓 Nature 文章链接
    for a in soup.select('a[href*="/articles/"]'):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        full_url = urljoin("https://www.nature.com", href)
        if "/articles/" not in full_url:
            continue

        title = a.get_text(" ", strip=True)
        if not title or len(title) < 5:
            continue

        parent = a.find_parent(["li", "article", "div"])
        description = ""
        if parent:
            candidates = parent.find_all(["p", "div", "span"])
            for c in candidates:
                text = c.get_text(" ", strip=True)
                if len(text) > 40 and text != title:
                    description = text
                    break

        unique_id = full_url
        if unique_id in seen_page:
            continue
        seen_page.add(unique_id)

        articles.append({
            "id": unique_id,
            "title": title,
            "url": full_url,
            "description": description,
        })

    return articles


# =========================
# 文章页解析 PDF
# =========================
def find_pdf_link_from_article(article_url: str) -> str | None:
    try:
        resp = requests.get(article_url, headers=HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")

        selectors = [
            'meta[name="citation_pdf_url"]',
            'a[href$=".pdf"]',
            'a[href*="/pdf"]',
            'a[data-track-action*="pdf" i]',
            'a[aria-label*="pdf" i]',
            'a.c-pdf-download__link',
        ]

        for selector in selectors:
            for tag in soup.select(selector):
                if tag.name == "meta":
                    content = (tag.get("content") or "").strip()
                    if content:
                        return content
                else:
                    href = (tag.get("href") or "").strip()
                    if href:
                        return urljoin("https://www.nature.com", href)
    except Exception as e:
        print(f"[WARN] 解析 PDF 链接失败: {article_url} -> {e}")

    fallback = article_url.rstrip("/") + ".pdf"
    return fallback


# =========================
# 下载与上传
# =========================
def download_pdf(pdf_url: str, output_path: str) -> bool:
    try:
        with requests.get(pdf_url, headers=HEADERS, timeout=60, stream=True) as resp:
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and not pdf_url.lower().endswith(".pdf"):
                print(f"[WARN] 可能不是 PDF: {pdf_url} | Content-Type={content_type}")

            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        print(f"[INFO] 下载成功: {output_path}")
        return True
    except Exception as e:
        print(f"[ERROR] 下载 PDF 失败: {pdf_url} -> {e}")
        return False


def upload_to_oss(local_path: str, object_key: str) -> bool:
    if not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
        print("[WARN] 未配置 OSS 环境变量，跳过上传。")
        return False

    try:
        auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(
            auth,
            OSS_ENDPOINT,
            OSS_BUCKET_NAME,
            enable_crc=False,
            connect_timeout=300,
        )
        oss2.resumable_upload(bucket, object_key, local_path, multipart_threshold=500 * 1024)
        print(f"[INFO] 上传 OSS 成功: {object_key}")
        return True
    except Exception as e:
        print(f"[ERROR] 上传 OSS 失败: {local_path} -> {e}")
        return False


# =========================
# 主流程
# =========================
def process_new_articles():
    ensure_dir(OUTPUT_DIR)

    downloaded_records = load_downloaded_records()
    print(f"[INFO] 历史已记录数量: {len(downloaded_records)}")

    candidates = fetch_search_results_by_playwright(KEYWORDS, MAX_PAGES)
    print(f"[INFO] 本次搜索到候选文章数: {len(candidates)}")

    new_items = []
    for article in candidates:
        if article["id"] in downloaded_records:
            continue
        new_items.append(article)
        if len(new_items) >= MAX_NEW_PER_DAY:
            break

    print(f"[INFO] 今日待处理新增文章数: {len(new_items)}")

    today_results = []

    for idx, article in enumerate(new_items, start=1):
        title = article["title"]
        article_url = article["url"]
        unique_id = article["id"]

        print(f"\n[INFO] ({idx}/{len(new_items)}) 处理: {title}")
        print(f"[INFO] 文章链接: {article_url}")

        pdf_url = find_pdf_link_from_article(article_url)
        filename = guess_filename_from_title_or_url(title, pdf_url)
        local_path = os.path.join(OUTPUT_DIR, filename)
        oss_key = OSS_PREFIX + filename

        item = {
            "title": title,
            "article_url": article_url,
            "pdf_url": pdf_url,
            "filename": filename,
            "local_path": local_path,
            "oss_key": oss_key,
            "description": article["description"],
            "record_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        if DOWNLOAD_PDF and pdf_url:
            ok = download_pdf(pdf_url, local_path)
            if ok and UPLOAD_TO_OSS:
                upload_to_oss(local_path, oss_key)

        today_results.append(item)
        downloaded_records.add(unique_id)

        time.sleep(1)

    save_downloaded_records(downloaded_records)
    append_today_log(today_results)

    print("\n[INFO] 今日任务完成")
    print(f"[INFO] 新增记录数量: {len(today_results)}")
    print(f"[INFO] 状态文件: {STATE_FILE}")
    print(f"[INFO] 日志文件: {TODAY_LOG}")


if __name__ == "__main__":
    process_new_articles()