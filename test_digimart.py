"""
独立脚本：验证能否抓取 Digimart 搜索结果（前 3 条标题 / 价格 / 链接）。

运行（在项目根目录 guitar-search/ 下）：
    pip install httpx beautifulsoup4
    python test_digimart.py
"""

from __future__ import annotations

import re
import sys

import httpx
from bs4 import BeautifulSoup

DIGIMART_ORIGIN = "https://www.digimart.net"
URL = "https://www.digimart.net/search"
KEYWORD = "YAMAHA rss20"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def abs_url(href: str) -> str:
    h = (href or "").strip()
    if not h:
        return ""
    if h.startswith("http://") or h.startswith("https://"):
        return h
    if h.startswith("//"):
        return "https:" + h
    if h.startswith("/"):
        return DIGIMART_ORIGIN + h
    return f"{DIGIMART_ORIGIN}/{h}"


def parse_price_yen(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    return digits


def main() -> None:
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(URL, params={"keyword": KEYWORD}, headers=HEADERS)
    except httpx.HTTPError as e:
        print("请求异常:", e, file=sys.stderr)
        sys.exit(1)

    if r.status_code != 200:
        print("请求失败，HTTP 状态码:", r.status_code)
        print("响应头 Content-Type:", r.headers.get("content-type", ""))
        snippet = (r.text or "")[:500].replace("\n", " ")
        if snippet:
            print("响应正文片段:", snippet)
        sys.exit(1)

    soup = BeautifulSoup(r.text, "html.parser")
    blocks = soup.select(".itemSearchListItem")[:3]

    if not blocks:
        print("未找到 .itemSearchListItem，页面结构可能已变更或结果为空。")
        sys.exit(0)

    for i, block in enumerate(blocks, start=1):
        ttl = block.select_one("p.ttl a")
        title = ""
        link = ""
        if ttl is not None:
            title = re.sub(r"\s+", " ", ttl.get_text(strip=True).replace("\xa0", " "))
            link = abs_url((ttl.get("href") or "").strip())

        price_raw = ""
        state = block.select_one(".itemState")
        if state is not None:
            for p in state.select("p.price"):
                t = p.get_text(" ", strip=True)
                num = parse_price_yen(t)
                if num is not None:
                    price_raw = num
                    break

        print(f"--- 商品 {i} ---")
        print("Title:", title or "(无)")
        print("Price (日元数字):", price_raw or "(未解析)")
        print("URL:", link or "(无)")
        print()


if __name__ == "__main__":
    main()
