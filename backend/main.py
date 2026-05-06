"""
吉他搜索测试后端：根据实时汇率把多币种价格换算成人民币（CNY）。

汇率来源：Frankfurter（欧洲央行参考汇率，免费、无需 API Key）
文档：https://www.frankfurter.app/docs/

另：`GET /search` 使用 Reverb API（需环境变量 REVERB_TOKEN）。
`GET /api/search` 并发请求 Reverb、Digimart、GuitarGuitar、Ishibashi（石桥乐器国际站 Shopify）与 Swee Lee（新加坡站 Shopify）；单方失败返回空列表，不影响其余平台。
返回统一结构：title / image / price_usd / price_cny / source / url / condition（USD/JPY/GBP→CNY 汇率来自 Frankfurter；GBP 缺失时可回落 ``GBP_CNY_RATE``）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from env_load import load_project_dotenv

load_project_dotenv()

from exchange_rate_cache import get_usd_cny_rate_cached

if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
logger = logging.getLogger(__name__)
from reverb_client import (
    extract_first_photo_url,
    extract_listing_web_url,
    listing_to_search_item,
    search_reverb_listings_async,
)

FRANKFURTER = "https://api.frankfurter.dev/v1/latest"

# 与 Dockerfile 一致：构建产物在仓库根目录的 dist/，由同一进程托管前端（公网单域名）
DIST_DIR = Path(__file__).resolve().parent.parent / "dist"
HAS_FRONTEND = (DIST_DIR / "index.html").is_file()

DIGIMART_ORIGIN = "https://www.digimart.net"
DIGIMART_SEARCH = f"{DIGIMART_ORIGIN}/search"
# Reverb ``per_page`` 与列表「满页」启发式；Digimart 搜索页常见每页 20 条
REVERB_PER_PAGE = 24
DIGIMART_PER_PAGE = 20
# 常见桌面 Chrome UA，降低被站点拒绝的概率（仍需遵守对方 robots/条款）
DIGIMART_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

GUITARGUITAR_ORIGIN = "https://www.guitarguitar.co.uk"
# 列表页常见每页 40 条；用于 ``has_more`` 启发式
GUITARGUITAR_FULL_PAGE = 40
GUITARGUITAR_MAX_PARSE = 40
ISHIBASHI_ORIGIN = "https://intl.ishibashi.co.jp"
# 主题常把 ``/search.json`` 渲染成 HTML；真实 JSON 多为 ``/search/suggest.json``（Predictive Search）
ISHIBASHI_SEARCH_JSON = f"{ISHIBASHI_ORIGIN}/search.json"
ISHIBASHI_SUGGEST_JSON = f"{ISHIBASHI_ORIGIN}/search/suggest.json"
ISHIBASHI_PRODUCTS_JSON = f"{ISHIBASHI_ORIGIN}/products.json"
ISHIBASHI_SUGGEST_LIMIT = 24
ISHIBASHI_PRODUCTS_LIMIT = 50
# ``has_more`` 启发式：石桥单页接近「满页」时认为可能还有下一页
ISHIBASHI_HAS_MORE_HINT = 24
# 请求侧强制日元定价，降低按 IP 自动切货币的概率（与 Shopify ``currency`` 查询参数配合）
ISHIBASHI_FORCE_CURRENCY_PARAMS = {"currency": "JPY"}
ISHIBASHI_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*;q=0.1",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
}

SWEELEE_ORIGIN = "https://www.sweelee.com.sg"
SWEELEE_SEARCH_JSON = f"{SWEELEE_ORIGIN}/search.json"
SWEELEE_SUGGEST_JSON = f"{SWEELEE_ORIGIN}/search/suggest.json"
SWEELEE_PRODUCTS_JSON = f"{SWEELEE_ORIGIN}/products.json"
SWEELEE_SUGGEST_LIMIT = 24
SWEELEE_PRODUCTS_LIMIT = 50
SWEELEE_HAS_MORE_HINT = 24
SWEELEE_FORCE_CURRENCY_PARAMS = {"currency": "SGD"}
SWEELEE_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*;q=0.1",
    "Accept-Language": "en-SG,en-US;q=0.9,en;q=0.8",
}

GUITARGUITAR_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Referer": f"{GUITARGUITAR_ORIGIN}/",
    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    'Sec-Ch-Ua-Platform': '"Windows"',
}

app = FastAPI(
    title="Guitar Search API",
    version="0.1.0",
    # 生产环境由 StaticFiles 托管根路径时，避免 /docs 与前端路由混淆
    docs_url=None if HAS_FRONTEND else "/docs",
    redoc_url=None if HAS_FRONTEND else "/redoc",
)

_cors_origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
]
for _piece in os.getenv("ALLOWED_ORIGINS", "").split(","):
    _p = _piece.strip()
    if _p and _p not in _cors_origins:
        _cors_origins.append(_p)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def fetch_cny_rate(client: httpx.AsyncClient, currency: str) -> float:
    """返回 1 单位 `currency` 等于多少 CNY。"""
    if currency == "CNY":
        return 1.0
    r = await client.get(
        FRANKFURTER,
        params={"from": currency, "to": "CNY"},
        follow_redirects=True,
    )
    r.raise_for_status()
    data = r.json()
    try:
        return float(data["rates"]["CNY"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"汇率接口返回异常: {e}") from e


async def get_rates_to_cny(client: httpx.AsyncClient, currencies: set[str]) -> dict[str, float]:
    currencies = set(currencies)
    currencies.discard("CNY")
    if not currencies:
        return {}
    keys = sorted(currencies)
    tasks = [fetch_cny_rate(client, c) for c in keys]
    values = await asyncio.gather(*tasks)
    return dict(zip(keys, values, strict=True))


def _gbp_to_cny_rate(rates_map: dict[str, float]) -> float:
    """
    1 GBP → CNY。优先 Frankfurter；缺失时使用环境变量 ``GBP_CNY_RATE``（默认 9.15）。
    """
    if "GBP" in rates_map:
        return rates_map["GBP"]
    raw = os.getenv("GBP_CNY_RATE", "9.15").strip()
    try:
        return float(raw)
    except ValueError:
        return 9.15


def _digimart_abs_url(href_or_src: str) -> str:
    s = (href_or_src or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("//"):
        return f"https:{s}"
    if s.startswith("/"):
        return f"{DIGIMART_ORIGIN}{s}"
    return f"{DIGIMART_ORIGIN}/{s}"


def _parse_jpy_amount(text: str) -> int | None:
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    try:
        n = int(digits)
    except ValueError:
        return None
    return n if n > 0 else None


def _digimart_condition_from_block(block: Any) -> str:
    """
    从 Digimart 列表卡片上解析成色标签文案。
    优先扫描常见标签区域，避免标题里单独的「新」字误判（尽力而为）。
    """
    chunks: list[str] = []
    for sel in (
        ".itemState",
        ".itemTags",
        ".itemTag",
        ".itemLabel",
        ".labels",
        "[class*='Tag']",
        "[class*='tag']",
        "[class*='Label']",
        "[class*='label']",
        "[class*='State']",
        "[class*='state']",
        "[class*='badge']",
        "[class*='Badge']",
    ):
        for el in block.select(sel):
            t = el.get_text(" ", strip=True)
            if t:
                chunks.append(t)
    blob = " ".join(chunks) if chunks else block.get_text(" ", strip=True)
    blob_lower = blob.lower()
    if "中古" in blob or "used" in blob_lower:
        return "二手"
    if "新" in blob:
        return "全新"
    return "二手"


def _digimart_block_to_raw(block: Any) -> dict[str, Any] | None:
    """单条 Digimart ``.itemSearchListItem`` → 标题、图片、日元整数、链接、成色。"""
    ttl = block.select_one("p.ttl a")
    if ttl is None:
        return None
    href = (ttl.get("href") or "").strip()
    if not href:
        return None
    title = re.sub(r"\s+", " ", ttl.get_text(strip=True).replace("\xa0", " "))
    url = _digimart_abs_url(href)

    img = block.select_one(".pic img")
    src = (img.get("src") or "").strip() if img is not None else ""
    image: str | None = _digimart_abs_url(src) if src else None

    jpy: int | None = None
    state = block.select_one(".itemState")
    if state is not None:
        for price_el in state.select("p.price"):
            n = _parse_jpy_amount(price_el.get_text(" ", strip=True))
            if n is not None:
                jpy = n
                break
    if jpy is None:
        return None

    condition = _digimart_condition_from_block(block)
    return {"title": title, "image": image, "jpy": jpy, "url": url, "condition": condition}


def _guitarguitar_search_url(keyword: str, page: int) -> str:
    """
    GuitarGuitar 全局搜索 URL（SSR 商品列表）。

    必须与站点搜索表单一致使用查询参数 ``Query``；使用 ``q`` 时服务端通常不渲染 ``a.product`` 列表，
    易被误认为「关键词无效」而只看到导航等无关内容。
    """
    enc = quote_plus(keyword.strip())
    pg = max(1, int(page))
    return f"{GUITARGUITAR_ORIGIN}/search/?Query={enc}&page={pg}"


def _guitarguitar_keyword_tokens(keyword: str) -> list[str]:
    """搜索词拆分为核心词（小写、长度 > 1）；无可用词时退回整条短语（若长度足够）。"""
    parts = [w.lower() for w in keyword.split() if len(w) > 1]
    if parts:
        return parts
    core = keyword.strip().lower()
    return [core] if len(core) > 1 else []


def _guitarguitar_title_matches_tokens(title: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    tl = title.lower()
    return any(tok in tl for tok in tokens)


def _guitarguitar_card_is_pre_owned(anchor: Any, title: str) -> bool:
    """
    列表卡片是否表现为二手：合并标题、锚点 ``title``、整张卡片可见文案（标题/闪光标签/价格区等），
    检测 ``pre-owned``、``second hand`` 或独立单词 ``used``（正则边界，避免 ``unused``）。
    """
    blob = " ".join(
        [
            title,
            (anchor.get("title") or "").strip(),
            anchor.get_text(" ", strip=True),
        ]
    ).lower()
    if "pre-owned" in blob or "second hand" in blob:
        return True
    return bool(re.search(r"\bused\b", blob))


def _parse_gbp_price_text(price_blob: str) -> float | None:
    """解析列表卡片上的英镑字符串（含 ``£899. 00`` 一类空格）。"""
    if not price_blob:
        return None
    compact = re.sub(r"\s+", "", price_blob.strip())
    m = re.search(r"£?([\d,]+)\.(\d{2})", compact)
    if m:
        whole = m.group(1).replace(",", "")
        try:
            return float(f"{whole}.{m.group(2)}")
        except ValueError:
            return None
    digits = re.sub(r"[^\d.]", "", compact)
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _guitarguitar_upgrade_image_url(absolute_url: str) -> str:
    """
    GuitarGuitar 列表缩略图 URL → 尽量换成高清地址。

    规则摘要（见下方「替换规则」）；任一步异常则返回原始 ``absolute_url``，避免裂图。
    """
    original = (absolute_url or "").strip()
    if not original:
        return absolute_url
    try:
        # 路径/文件名中的低清标记
        s = (
            original.replace("/120/", "/1000/")
            .replace("/250/", "/1000/")
            .replace("_preview", "")
            .replace("_small", "")
            .replace("-thumb", "")
            .replace("_thumb", "")
        )
        parts = urlparse(s)
        qsl = parse_qsl(parts.query, keep_blank_values=True)
        hd = "1000"
        new_qsl: list[tuple[str, str]] = []
        for k, v in qsl:
            kl = k.lower()
            vs = v.strip()
            if kl in ("w", "width") and vs.isdigit() and int(vs) < 800:
                new_qsl.append((k, hd))
            elif kl in ("h", "height") and vs.isdigit() and int(vs) < 800:
                new_qsl.append((k, hd))
            elif kl == "size" and v.lower() in ("small", "thumb", "thumbnail", "s"):
                continue
            else:
                new_qsl.append((k, v))
        query = urlencode(new_qsl)
        out = urlunparse(parts._replace(query=query))
        if not out.startswith("http://") and not out.startswith("https://"):
            return original
        return out
    except Exception:
        return original


def _guitarguitar_anchor_to_raw(anchor: Any) -> dict[str, Any] | None:
    """单条 ``a.product``（全局搜索 ``/search/`` 列表）→ 标题、图片、英镑价格、链接。"""
    href = (anchor.get("href") or "").strip()
    if not href or "/product/" not in href:
        return None

    ttl = anchor.select_one(".qa-product-list-item-title")
    if ttl is None:
        return None
    title = re.sub(r"\s+", " ", ttl.get_text(" ", strip=True).replace("\xa0", " "))

    price_el = anchor.select_one(".product-main-price")
    if price_el is None:
        return None
    price_gbp = _parse_gbp_price_text(price_el.get_text(" ", strip=True))
    if price_gbp is None or price_gbp <= 0:
        return None

    image: str | None = None
    for img in anchor.select("img"):
        try:
            raw = (
                (img.get("data-src") or "").strip()
                or (img.get("data-original") or "").strip()
                or (img.get("src") or "").strip()
            )
            if not raw or "blank" in raw.casefold():
                continue
            base = urljoin(GUITARGUITAR_ORIGIN, raw)
            image = _guitarguitar_upgrade_image_url(base)
            break
        except Exception:
            continue

    url = urljoin(GUITARGUITAR_ORIGIN, href)
    return {
        "title": title,
        "image": image,
        "gbp": price_gbp,
        "url": url,
    }


def _reverb_condition_cn(listing: dict[str, Any]) -> str:
    """Reverb listing 的 ``condition`` → 统一中文「全新」/「二手」。"""
    raw: Any = listing.get("condition")
    if isinstance(raw, dict):
        raw = (
            raw.get("display_name")
            or raw.get("name")
            or raw.get("display")
            or raw.get("slug")
            or raw.get("uuid")
        )
    if raw is None:
        return "二手"
    normalized = str(raw).strip().casefold().replace("_", " ")
    if normalized == "brand new":
        return "全新"
    return "二手"


async def scrape_digimart(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """
    异步抓取 Digimart 搜索页（与 ``test_digimart.py`` 同源解析逻辑）。

    分页说明：Digimart 搜索页对服务端 GET 往往**只渲染第 1 页** HTML；即使用
    ``currentPageNo`` / ``page`` 传参，列表内容仍可能与第 1 页相同。若在第 2 页及以后
    继续抓取，会导致各页出现**同一批 Digimart 商品**，与 Reverb 真分页叠在一起形成
    「分页重复」。因此 **page > 1 时不再请求 Digimart**，仅保留 Reverb 分页结果。

    第 1 页请求同时携带 ``currentPageNo`` 与 ``page``（站点不同入口可能认其中一种）。

    网络/HTML 异常时返回空列表，不向外抛错，避免拖累 Reverb。
    """
    q = keyword.strip()
    if not q:
        logger.info("[Digimart] scrape skipped (empty keyword)")
        return []

    pg = max(1, int(page))
    if pg > 1:
        logger.info(
            "[Digimart] skip scrape for page=%s (SSR 仅首屏列表；避免与第 1 页重复)",
            pg,
        )
        return []

    logger.info("[Digimart] scrape start keyword=%r page=%s", q, pg)
    try:
        params: dict[str, Any] = {"keyword": q, "currentPageNo": pg, "page": pg}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(
                DIGIMART_SEARCH,
                params=params,
                headers=DIGIMART_BROWSER_HEADERS,
            )
            r.raise_for_status()

        logger.info(
            "[Digimart] http ok page=%s status=%s response_bytes=%s",
            pg,
            r.status_code,
            len(r.text or ""),
        )

        soup = BeautifulSoup(r.text, "html.parser")
        out: list[dict[str, Any]] = []
        for block in soup.select(".itemSearchListItem"):
            item = _digimart_block_to_raw(block)
            if item is not None:
                out.append(item)

        logger.info(
            "[Digimart] scrape success keyword=%r page=%s parsed_items=%s",
            q,
            pg,
            len(out),
        )
        if not out and len(r.text or "") > 500:
            logger.warning(
                "[Digimart] zero parsed items but large HTML (%s bytes) — "
                "likely layout/selector mismatch or blocking page",
                len(r.text),
            )
        return out
    except Exception as e:
        line = (
            f"[Digimart] scrape_digimart error | keyword={q!r} page={pg} | "
            f"type={type(e).__name__} | details={str(e)}"
        )
        print(line, flush=True)
        logger.error(line, exc_info=True)
        if isinstance(e, httpx.HTTPStatusError):
            resp = e.response
            if resp is not None:
                snippet = (resp.text or "")[:500].replace("\n", " ")
                detail = (
                    f"[Digimart] HTTPStatusError status_code={resp.status_code} "
                    f"url={resp.url} body_snippet={snippet!r}"
                )
                print(detail, flush=True)
                logger.error(detail)
        elif isinstance(e, httpx.TimeoutException):
            detail = (
                f"[Digimart] Timeout {type(e).__name__}: "
                "Connect/Read/Write/Pool — 可调大 timeout 或检查出站网络"
            )
            print(detail, flush=True)
            logger.error(detail)
        elif isinstance(e, httpx.RequestError):
            detail = f"[Digimart] RequestError (连接/TLS/DNS 等): {e!r}"
            print(detail, flush=True)
            logger.error(detail)
        tb = traceback.format_exc()
        print(f"[Digimart] full traceback:\n{tb}", flush=True)
        return []


async def scrape_guitarguitar(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """
    抓取 GuitarGuitar 全局搜索 ``/search/?Query=…`` 的 ``a.product`` 列表。

    解析后经两道过滤：（1）标题须命中用户搜索核心词；（2）卡片须表现为二手（Pre-Owned /
    Second Hand / ``used``）。商品图在 ``_guitarguitar_anchor_to_raw`` 中经
    ``_guitarguitar_upgrade_image_url`` 高清化。异常或超时返回空列表，不向外抛错。
    """
    q = keyword.strip()
    if not q:
        logger.info("[GuitarGuitar] scrape skipped (empty keyword)")
        return []

    tokens = _guitarguitar_keyword_tokens(q)
    if not tokens:
        logger.info("[GuitarGuitar] scrape skipped (no keyword tokens len>1): %r", q)
        return []

    pg = max(1, int(page))
    url = _guitarguitar_search_url(q, pg)
    logger.info("[GuitarGuitar] scrape start keyword=%r page=%s url=%s", q, pg, url)

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get(url, headers=GUITARGUITAR_BROWSER_HEADERS)
            r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        blocks = soup.select(".product-list-products a.product")
        out: list[dict[str, Any]] = []
        parsed_ok = 0
        dropped_kw = 0
        dropped_po = 0
        for anchor in blocks[:GUITARGUITAR_MAX_PARSE]:
            try:
                raw = _guitarguitar_anchor_to_raw(anchor)
                if raw is None:
                    continue
                parsed_ok += 1
                title = str(raw.get("title") or "")
                if not _guitarguitar_title_matches_tokens(title, tokens):
                    dropped_kw += 1
                    continue
                if not _guitarguitar_card_is_pre_owned(anchor, title):
                    dropped_po += 1
                    continue
                out.append(raw)
            except Exception:
                continue

        logger.info(
            "[GuitarGuitar] scrape success keyword=%r page=%s kept=%s "
            "(parsed=%s dropped_kw=%s dropped_pre_owned=%s)",
            q,
            pg,
            len(out),
            parsed_ok,
            dropped_kw,
            dropped_po,
        )
        if not out and len(r.text or "") > 500:
            logger.warning(
                "[GuitarGuitar] zero parsed items but large HTML (%s bytes) — "
                "likely layout/selector mismatch or blocking page",
                len(r.text),
            )
        return out
    except Exception as e:
        line = (
            f"[GuitarGuitar] scrape_guitarguitar error | keyword={q!r} page={pg} | "
            f"type={type(e).__name__} | details={str(e)}"
        )
        print(line, flush=True)
        logger.error(line, exc_info=True)
        if isinstance(e, httpx.HTTPStatusError):
            resp = e.response
            if resp is not None:
                snippet = (resp.text or "")[:500].replace("\n", " ")
                detail = (
                    f"[GuitarGuitar] HTTPStatusError status_code={resp.status_code} "
                    f"url={resp.url} body_snippet={snippet!r}"
                )
                print(detail, flush=True)
                logger.error(detail)
        elif isinstance(e, httpx.TimeoutException):
            detail = (
                f"[GuitarGuitar] Timeout {type(e).__name__}: "
                "可调大 timeout 或检查出站网络"
            )
            print(detail, flush=True)
            logger.error(detail)
        elif isinstance(e, httpx.RequestError):
            detail = f"[GuitarGuitar] RequestError (连接/TLS/DNS 等): {e!r}"
            print(detail, flush=True)
            logger.error(detail)
        tb = traceback.format_exc()
        print(f"[GuitarGuitar] full traceback:\n{tb}", flush=True)
        return []


def _ishibashi_response_looks_json(response: httpx.Response) -> bool:
    t = (response.text or "").lstrip()
    return t.startswith("{") or t.startswith("[")


def _ishibashi_products_from_json_payload(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    raw = data.get("products")
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    resources = data.get("resources")
    if isinstance(resources, dict):
        results = resources.get("results")
        if isinstance(results, dict):
            rp = results.get("products")
            if isinstance(rp, list):
                return [p for p in rp if isinstance(p, dict)]
    return []


def _ishibashi_upgrade_image_url(src: str) -> str:
    s = (src or "").strip()
    if not s:
        return s
    out = s
    for suf in ("_small", "_medium", "_compact"):
        if suf in out:
            out = out.replace(suf, "_1024x1024")
            break
    return out


def _ishibashi_condition_from_title(title: str) -> str:
    """USED / 中古 → 二手；NEW / 新品 → 全新；否则默认二手。"""
    t = title or ""
    tl = t.lower()
    if "中古" in t or "second hand" in tl or re.search(r"\bused\b", tl):
        return "二手"
    if "新品" in t or "brand new" in tl or re.search(r"\bnew\b", tl):
        return "全新"
    return "二手"


def _ishibashi_matches_keyword(title: str, vendor: str | None, keyword: str) -> bool:
    """标题 / 品牌（vendor）联合模糊匹配：整词包含或（长度>1 的）词全部命中。"""
    q = (keyword or "").strip().lower()
    if not q:
        return False
    blob = f"{title or ''} {vendor or ''}".lower()
    if q in blob:
        return True
    tokens = [tok for tok in re.split(r"\s+", q) if len(tok) > 1]
    if not tokens:
        return q in blob
    return all(tok in blob for tok in tokens)


def _ishibashi_normalize_iso_currency(code: Any) -> str:
    """三位 ISO 货币码大写；无效时返回空串。"""
    if code is None:
        return ""
    s = str(code).strip().upper()
    if len(s) >= 3 and s[:3].isalpha():
        return s[:3]
    return ""


def _ishibashi_currency_from_variant(variant: dict[str, Any] | None) -> str:
    if not isinstance(variant, dict):
        return ""
    for key in ("currency", "price_currency", "presentment_currency"):
        c = _ishibashi_normalize_iso_currency(variant.get(key))
        if c:
            return c
    pp = variant.get("presentment_prices")
    if isinstance(pp, dict):
        for sub_key in ("shop_money", "presentment_money"):
            sm = pp.get(sub_key)
            if isinstance(sm, dict):
                c2 = sm.get("currency_code") or sm.get("currencyCode")
                c = _ishibashi_normalize_iso_currency(c2)
                if c:
                    return c
    return ""


def _ishibashi_extract_currency(
    prod: dict[str, Any],
    *,
    root_payload: dict[str, Any] | None,
) -> str:
    """Shopify JSON 中可能出现的货币字段（不假设一定是日元）。"""
    variants = prod.get("variants")
    if isinstance(variants, list) and variants:
        v0 = variants[0]
        if isinstance(v0, dict):
            c = _ishibashi_currency_from_variant(v0)
            if c:
                return c
    for key in ("currency", "price_currency"):
        c = _ishibashi_normalize_iso_currency(prod.get(key))
        if c:
            return c
    if isinstance(root_payload, dict):
        for key in ("currency", "presentment_currency"):
            c = _ishibashi_normalize_iso_currency(root_payload.get(key))
            if c:
                return c
    return ""


def _ishibashi_parse_price_raw_from_product(prod: dict[str, Any]) -> float | None:
    """金额与 Shopify 展示货币一致（由 ``original_currency`` 描述）。"""
    variants = prod.get("variants")
    if isinstance(variants, list) and variants:
        v0 = variants[0]
        if isinstance(v0, dict):
            raw = v0.get("price")
            if raw is not None:
                try:
                    x = float(raw)
                    return x if x > 0 else None
                except (TypeError, ValueError):
                    pass
    raw2 = prod.get("price")
    if raw2 is None:
        return None
    try:
        x = float(raw2)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _ishibashi_extract_image_url(prod: dict[str, Any]) -> str:
    fi = prod.get("featured_image")
    if isinstance(fi, dict):
        u = (fi.get("url") or "").strip()
        if u:
            return _ishibashi_upgrade_image_url(u)
    img = (prod.get("image") or "").strip()
    if img:
        return _ishibashi_upgrade_image_url(img)
    images = prod.get("images")
    if isinstance(images, list) and images:
        im0 = images[0]
        if isinstance(im0, dict):
            return _ishibashi_upgrade_image_url(str(im0.get("src") or ""))
    return ""


def _ishibashi_product_to_raw(
    prod: dict[str, Any],
    *,
    root_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    handle = prod.get("handle")
    if not isinstance(handle, str) or not handle.strip():
        return None
    title = str(prod.get("title") or "").strip()
    if not title:
        return None
    url = f"{ISHIBASHI_ORIGIN}/products/{handle.strip()}"
    image_url = _ishibashi_extract_image_url(prod)
    price_raw = _ishibashi_parse_price_raw_from_product(prod)
    if price_raw is None:
        return None
    oc = _ishibashi_extract_currency(prod, root_payload=root_payload)
    if not oc:
        oc = "JPY"
    condition = _ishibashi_condition_from_title(title)
    return {
        "title": title,
        "image": image_url or None,
        "price_raw": float(price_raw),
        "original_currency": oc,
        "url": url,
        "condition": condition,
    }


def _ishibashi_amount_to_cny(
    price_raw: float,
    original_currency: str | None,
    rates_map: dict[str, float],
) -> float | None:
    """
    按 JSON 中的真实标价货币换算为 CNY；与全站 Frankfurter 汇价一致。
    若未识别货币或缺失汇价，则按 JPY 兜底（与 ``original_currency`` 默认为 JPY 对齐）。
    """
    cur = _ishibashi_normalize_iso_currency(original_currency) or "JPY"
    if cur == "CNY":
        return price_raw
    if cur in rates_map:
        return float(price_raw) * rates_map[cur]
    if "JPY" in rates_map:
        return float(price_raw) * rates_map["JPY"]
    return None


def _sweelee_upgrade_image_url(src: str) -> str:
    s = (src or "").strip()
    if not s:
        return s
    out = s
    if "_small" in out or "_medium" in out:
        for suf in ("_small", "_medium"):
            if suf in out:
                out = out.replace(suf, "_1024x1024")
                break
    return out


def _sweelee_tags_blob(prod: dict[str, Any]) -> str:
    raw = prod.get("tags")
    if isinstance(raw, list):
        return ", ".join(str(t) for t in raw if t is not None)
    if isinstance(raw, str):
        return raw
    return ""


def _sweelee_collections_blob(prod: dict[str, Any]) -> str:
    parts: list[str] = []
    cols = prod.get("collections")
    if isinstance(cols, list):
        for c in cols:
            if isinstance(c, dict):
                parts.append(str(c.get("handle") or c.get("title") or ""))
            elif isinstance(c, str):
                parts.append(c)
    return ", ".join(parts)


def _sweelee_condition_from_product(prod: dict[str, Any]) -> str:
    """
    Swee Lee：B-Stock → ``全新``（按站点常见口径）；Used / Pre-Loved 等 → ``二手``；
    默认 ``二手``。
    """
    title = str(prod.get("title") or "")
    tags_blob = _sweelee_tags_blob(prod)
    col_blob = _sweelee_collections_blob(prod)
    product_type = str(prod.get("product_type") or prod.get("type") or "")
    blob_lower = f"{title} {tags_blob} {col_blob} {product_type}".lower()
    if re.search(r"b[-\s]?stock", blob_lower):
        return "全新"
    if re.search(r"\b(pre-owned|pre-loved|second[\s-]hand)\b", blob_lower):
        return "二手"
    if re.search(r"\bused\b", blob_lower) or ("used-" in blob_lower):
        return "二手"
    if "中古" in title:
        return "二手"
    if "二手" in (tags_blob + col_blob):
        return "二手"
    return "二手"


def _sweelee_parse_price_raw_from_product(prod: dict[str, Any]) -> float | None:
    variants = prod.get("variants")
    if isinstance(variants, list) and variants:
        v0 = variants[0]
        if isinstance(v0, dict) and v0.get("price") is not None:
            try:
                x = float(v0["price"])
                return x if x > 0 else None
            except (TypeError, ValueError):
                pass
    for key in ("price", "price_min", "price_max"):
        raw_v = prod.get(key)
        if raw_v is not None:
            try:
                x = float(raw_v)
                return x if x > 0 else None
            except (TypeError, ValueError):
                continue
    return None


def _sweelee_extract_first_image_src(prod: dict[str, Any]) -> str:
    images = prod.get("images")
    if isinstance(images, list) and images:
        im0 = images[0]
        if isinstance(im0, dict):
            u = (im0.get("src") or "").strip()
            if u:
                return _sweelee_upgrade_image_url(u)
        elif isinstance(im0, str):
            s = im0.strip()
            if s:
                return _sweelee_upgrade_image_url(s)
    fi = prod.get("featured_image")
    if isinstance(fi, dict):
        u = (fi.get("url") or "").strip()
        if u:
            return _sweelee_upgrade_image_url(u)
    u2 = str(prod.get("image") or "").strip()
    if u2:
        return _sweelee_upgrade_image_url(u2)
    return ""


def _sweelee_products_from_suggest_payload(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    resources = data.get("resources")
    if not isinstance(resources, dict):
        return []
    results = resources.get("results")
    if not isinstance(results, dict):
        return []
    prods = results.get("products")
    if isinstance(prods, list):
        return [p for p in prods if isinstance(p, dict)]
    return []


def _sweelee_product_to_raw(
    prod: dict[str, Any],
    *,
    root_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    handle = prod.get("handle")
    if not isinstance(handle, str) or not handle.strip():
        return None
    title = str(prod.get("title") or "").strip()
    if not title:
        return None
    url = f"{SWEELEE_ORIGIN}/products/{handle.strip()}"
    image_url = _sweelee_extract_first_image_src(prod)
    price_raw = _sweelee_parse_price_raw_from_product(prod)
    if price_raw is None:
        return None
    oc = _ishibashi_extract_currency(prod, root_payload=root_payload)
    if not oc:
        oc = "SGD"
    return {
        "title": title,
        "image": image_url or None,
        "price_raw": float(price_raw),
        "original_currency": oc,
        "url": url,
        "condition": _sweelee_condition_from_product(prod),
    }


async def scrape_ishibashi(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """
    石桥乐器国际站（Shopify）：所有请求带 ``currency=JPY``，尽量固定标价口径；
    仍从每条 JSON 解析 ``original_currency`` + ``price_raw``，由 ``/api/search`` 侧按 Frankfurter 换算。

    优先 ``/search.json``；若返回非 JSON 或无效，则依次使用 ``/search/suggest.json`` 与
    ``products.json`` 内存筛选。异常或超时返回空列表，不向外抛错。
    """
    q = keyword.strip()
    if not q:
        logger.info("[Ishibashi] scrape skipped (empty keyword)")
        return []

    pg = max(1, int(page))

    try:
        async with httpx.AsyncClient(timeout=22.0, follow_redirects=True) as client:
            products_primary: list[dict[str, Any]] = []
            payload_search: dict[str, Any] | None = None

            r_search = await client.get(
                ISHIBASHI_SEARCH_JSON,
                params={
                    **ISHIBASHI_FORCE_CURRENCY_PARAMS,
                    "q": q,
                    "page": pg,
                    "limit": 24,
                },
                headers=ISHIBASHI_BROWSER_HEADERS,
            )
            if (
                r_search.status_code == 200
                and _ishibashi_response_looks_json(r_search)
            ):
                try:
                    payload_search = r_search.json()
                    products_primary = _ishibashi_products_from_json_payload(
                        payload_search
                    )
                except Exception:
                    products_primary = []

            merged_entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
            seen_handles: set[str] = set()

            if products_primary:
                root_ps = payload_search if isinstance(payload_search, dict) else None
                for p in products_primary:
                    merged_entries.append((p, root_ps))
            else:
                r_suggest = await client.get(
                    ISHIBASHI_SUGGEST_JSON,
                    params={
                        **ISHIBASHI_FORCE_CURRENCY_PARAMS,
                        "q": q,
                        "resources[type]": "product",
                        "resources[limit]": str(min(ISHIBASHI_SUGGEST_LIMIT, 50)),
                    },
                    headers=ISHIBASHI_BROWSER_HEADERS,
                )
                if r_suggest.status_code == 200 and _ishibashi_response_looks_json(
                    r_suggest
                ):
                    try:
                        sug_payload = r_suggest.json()
                        sug_products = _ishibashi_products_from_json_payload(
                            sug_payload
                        )
                        sug_root = (
                            sug_payload if isinstance(sug_payload, dict) else None
                        )
                        for p in sug_products:
                            h = p.get("handle")
                            if isinstance(h, str) and h and h not in seen_handles:
                                merged_entries.append((p, sug_root))
                                seen_handles.add(h)
                    except Exception:
                        pass

                r_fb = await client.get(
                    ISHIBASHI_PRODUCTS_JSON,
                    params={
                        **ISHIBASHI_FORCE_CURRENCY_PARAMS,
                        "limit": ISHIBASHI_PRODUCTS_LIMIT,
                        "page": pg,
                    },
                    headers=ISHIBASHI_BROWSER_HEADERS,
                )
                if r_fb.status_code == 200 and _ishibashi_response_looks_json(r_fb):
                    try:
                        fb_payload = r_fb.json()
                        fb_root = fb_payload if isinstance(fb_payload, dict) else None
                        fb_all = fb_payload.get("products")
                        if isinstance(fb_all, list):
                            for p in fb_all:
                                if not isinstance(p, dict):
                                    continue
                                h = p.get("handle")
                                if not isinstance(h, str) or not h or h in seen_handles:
                                    continue
                                if _ishibashi_matches_keyword(
                                    str(p.get("title") or ""),
                                    str(p.get("vendor") or ""),
                                    q,
                                ):
                                    merged_entries.append((p, fb_root))
                                    seen_handles.add(h)
                    except Exception:
                        pass

            out: list[dict[str, Any]] = []
            for prod, root_ctx in merged_entries:
                raw = _ishibashi_product_to_raw(prod, root_payload=root_ctx)
                if raw is not None:
                    out.append(raw)

            logger.info(
                "[Ishibashi] scrape success keyword=%r page=%s items=%s",
                q,
                pg,
                len(out),
            )
            return out

    except Exception as e:
        line = (
            f"[Ishibashi] scrape_ishibashi error | keyword={q!r} page={pg} | "
            f"type={type(e).__name__} | details={str(e)}"
        )
        print(line, flush=True)
        logger.error(line, exc_info=True)
        if isinstance(e, httpx.HTTPStatusError):
            resp = e.response
            if resp is not None:
                snippet = (resp.text or "")[:500].replace("\n", " ")
                detail = (
                    f"[Ishibashi] HTTPStatusError status_code={resp.status_code} "
                    f"url={resp.url} body_snippet={snippet!r}"
                )
                print(detail, flush=True)
                logger.error(detail)
        elif isinstance(e, httpx.TimeoutException):
            detail = "[Ishibashi] Timeout — 请求石桥乐器超时"
            print(detail, flush=True)
            logger.error(detail)
        elif isinstance(e, httpx.RequestError):
            detail = f"[Ishibashi] RequestError: {e!r}"
            print(detail, flush=True)
            logger.error(detail)
        tb = traceback.format_exc()
        print(f"[Ishibashi] full traceback:\n{tb}", flush=True)
        return []


async def _safe_scrape_ishibashi(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """供 ``/api/search`` 合并：石桥超时或异常时返回空列表，不拖累其它平台。"""
    q = keyword.strip()
    if not q:
        return []
    pg = max(1, int(page))
    try:
        return await asyncio.wait_for(scrape_ishibashi(q, pg), timeout=26.0)
    except asyncio.TimeoutError:
        logger.warning("[Ishibashi] asyncio.wait_for timeout (26s) keyword=%r page=%s", q, pg)
        return []
    except Exception as e:
        logger.error("[Ishibashi] _safe_scrape_ishibashi unexpected: %s", e, exc_info=True)
        return []


async def scrape_sweelee(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """
    Swee Lee 新加坡站：优先调用官方 Shopify 风格 ``search.json``（与设计 URL 对齐）；
    若当前主题为 Headless/React 以至返回 HTML，则降级 ``search/suggest.json`` 与
    ``products.json`` 分页筛选（与 Ishibashi 合并策略同源）。

    标价货币：解析 JSON 中真实 ``original_currency``；缺省为 ``SGD``；``currency=SGD``
    参数用于尽量固定标价口径。
    """
    q = keyword.strip()
    if not q:
        logger.info("[Swee Lee] scrape skipped (empty keyword)")
        return []

    pg = max(1, int(page))

    logger.info("[Swee Lee] scrape start keyword=%r page=%s", q, pg)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(9.0, connect=5.0),
            follow_redirects=True,
        ) as client:
            merged_entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
            seen_handles: set[str] = set()
            payload_search: dict[str, Any] | None = None

            products_primary: list[dict[str, Any]] = []
            r_search = await client.get(
                SWEELEE_SEARCH_JSON,
                params={
                    **SWEELEE_FORCE_CURRENCY_PARAMS,
                    "q": q,
                    "page": pg,
                    "limit": 24,
                },
                headers=SWEELEE_BROWSER_HEADERS,
            )

            if r_search.status_code == 200 and _ishibashi_response_looks_json(r_search):
                try:
                    payload_search = r_search.json()
                    products_primary = _ishibashi_products_from_json_payload(
                        payload_search
                    )
                except Exception:
                    products_primary = []

            if products_primary:
                root_ps = payload_search if isinstance(payload_search, dict) else None
                for p in products_primary:
                    h = p.get("handle")
                    if isinstance(h, str) and h and h.strip():
                        merged_entries.append((p, root_ps))
                        seen_handles.add(h.strip())
            else:
                if pg == 1:
                    r_suggest = await client.get(
                        SWEELEE_SUGGEST_JSON,
                        params={
                            **SWEELEE_FORCE_CURRENCY_PARAMS,
                            "q": q,
                            "resources[type]": "product",
                            "resources[limit]": str(
                                min(SWEELEE_SUGGEST_LIMIT, 50),
                            ),
                        },
                        headers=SWEELEE_BROWSER_HEADERS,
                    )
                    if r_suggest.status_code == 200 and _ishibashi_response_looks_json(
                        r_suggest
                    ):
                        try:
                            sug_payload = r_suggest.json()
                            sug_products = _sweelee_products_from_suggest_payload(
                                sug_payload,
                            )
                            sug_root = (
                                sug_payload if isinstance(sug_payload, dict) else None
                            )
                            for p in sug_products:
                                h = p.get("handle")
                                if isinstance(h, str) and h and h not in seen_handles:
                                    merged_entries.append((p, sug_root))
                                    seen_handles.add(h)
                        except Exception:
                            pass

                r_fb = await client.get(
                    SWEELEE_PRODUCTS_JSON,
                    params={
                        **SWEELEE_FORCE_CURRENCY_PARAMS,
                        "limit": SWEELEE_PRODUCTS_LIMIT,
                        "page": pg,
                    },
                    headers=SWEELEE_BROWSER_HEADERS,
                )
                if r_fb.status_code == 200 and _ishibashi_response_looks_json(r_fb):
                    try:
                        fb_payload = r_fb.json()
                        fb_root = fb_payload if isinstance(fb_payload, dict) else None
                        fb_all = fb_payload.get("products")
                        if isinstance(fb_all, list):
                            for p in fb_all:
                                if not isinstance(p, dict):
                                    continue
                                h = p.get("handle")
                                if (
                                    not isinstance(h, str)
                                    or not h
                                    or h in seen_handles
                                ):
                                    continue
                                if _ishibashi_matches_keyword(
                                    str(p.get("title") or ""),
                                    str(p.get("vendor") or ""),
                                    q,
                                ):
                                    merged_entries.append((p, fb_root))
                                    seen_handles.add(h)
                    except Exception:
                        pass

            out: list[dict[str, Any]] = []
            for prod, root_ctx in merged_entries:
                raw = _sweelee_product_to_raw(prod, root_payload=root_ctx)
                if raw is not None:
                    out.append(raw)

            logger.info(
                "[Swee Lee] scrape success keyword=%r page=%s items=%s",
                q,
                pg,
                len(out),
            )
            return out

    except Exception as e:
        line = (
            f"[Swee Lee] scrape_sweelee error | keyword={q!r} page={pg} | "
            f"type={type(e).__name__} | details={str(e)}"
        )
        print(line, flush=True)
        logger.error(line, exc_info=True)
        if isinstance(e, httpx.HTTPStatusError):
            resp = e.response
            if resp is not None:
                snippet = (resp.text or "")[:500].replace("\n", " ")
                detail = (
                    f"[Swee Lee] HTTPStatusError status_code={resp.status_code} "
                    f"url={resp.url} body_snippet={snippet!r}"
                )
                print(detail, flush=True)
                logger.error(detail)
        elif isinstance(e, httpx.TimeoutException):
            detail = "[Swee Lee] Timeout — 请求超时"
            print(detail, flush=True)
            logger.error(detail)
        elif isinstance(e, httpx.RequestError):
            detail = f"[Swee Lee] RequestError: {e!r}"
            print(detail, flush=True)
            logger.error(detail)
        tb = traceback.format_exc()
        print(f"[Swee Lee] full traceback:\n{tb}", flush=True)
        return []


async def _safe_scrape_sweelee(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """供 ``/api/search`` 合并：强制 10 秒兜底，不因 Swee Lee 卡住主链路。"""
    q = keyword.strip()
    if not q:
        return []
    pg = max(1, int(page))
    try:
        return await asyncio.wait_for(scrape_sweelee(q, pg), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("[Swee Lee] asyncio.wait_for timeout (10s) keyword=%r page=%s", q, pg)
        return []
    except Exception as e:
        logger.error("[Swee Lee] _safe_scrape_sweelee unexpected: %s", e, exc_info=True)
        return []


def _reverb_amount_currency(listing: dict[str, Any]) -> tuple[float | None, str | None]:
    """Reverb HAL listing 的 ``price`` 对象 → 金额与 ISO 货币。"""
    price_obj = listing.get("price")
    if not isinstance(price_obj, dict):
        return None, None
    raw_amt = price_obj.get("amount")
    if raw_amt is None:
        return None, None
    try:
        amt = float(raw_amt)
    except (TypeError, ValueError):
        return None, None
    cur = price_obj.get("currency") or price_obj.get("currency_iso") or ""
    c = str(cur).strip().upper()
    return amt, c if c else None


async def _fetch_reverb_listings(query: str, page: int = 1) -> list[dict[str, Any]]:
    token = os.environ.get("REVERB_TOKEN", "").strip()
    if not token:
        return []
    pg = max(1, int(page))
    return await search_reverb_listings_async(
        token,
        query,
        page=pg,
        per_page=REVERB_PER_PAGE,
    )


async def _safe_fetch_reverb_listings_for_merge(query: str, page: int = 1) -> list[dict[str, Any]]:
    """供 ``/api/search`` 合并结果使用：Reverb 异常时返回空列表，不阻断其他平台。"""
    q = query.strip()
    if not q:
        return []
    pg = max(1, int(page))
    try:
        return await _fetch_reverb_listings(q, pg)
    except Exception as e:
        line = (
            f"[Reverb] merge fetch failed | query={q!r} page={pg} | "
            f"type={type(e).__name__} | details={str(e)}"
        )
        logger.error(line, exc_info=True)
        print(line, flush=True)
        return []


def _unified_row(
    *,
    title: str,
    image: str | None,
    url: str,
    source: str,
    price_cny: float | None,
    usd_to_cny: float,
    condition: str,
) -> dict[str, Any]:
    price_usd: float | None = None
    if price_cny is not None and usd_to_cny > 0:
        price_usd = round(price_cny / usd_to_cny, 2)
    return {
        "title": title,
        "image": image,
        "price_usd": price_usd,
        "price_cny": round(price_cny, 2) if price_cny is not None else None,
        "source": source,
        "url": url,
        "condition": condition,
    }


def _normalize_url_for_dedup(url: str) -> str:
    """合并多平台结果时按 URL 去重用的规范化键（scheme/host 小写、去尾斜杠）。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = (parsed.path or "").rstrip("/")
    if not path:
        path = "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _dedupe_results_preserve_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一响应内按 ``url`` 去重，保留首次出现顺序；无 URL 的条目不去重。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = _normalize_url_for_dedup(str(row.get("url") or ""))
        if not key:
            out.append(row)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/exchange-rate")
async def exchange_rate() -> dict[str, float]:
    """
    USD→CNY 参考汇率（ExchangeRate-API v6），进程内缓存 1 小时。

    环境变量：``EXCHANGE_RATE_API_KEY``
    """
    rate = await get_usd_cny_rate_cached()
    return {"rate": round(rate, 4)}


@app.get("/search")
async def search_reverb(
    q: str = Query(
        ...,
        min_length=1,
        description="搜索关键词，例如 Fender（前端搜索框输入后点「搜索」或按回车提交）",
    ),
) -> dict[str, Any]:
    """
    调用 Reverb ``/api/listings/all``，返回标题、图片、价格、原页链接。

    前端默认使用 ``GET /api/search``（含 Digimart）；本路由保留给仅需 Reverb 的调用方。

    需在 ``backend/.env`` 中配置 ``REVERB_TOKEN``（Personal Access Token）。
    """
    token = os.environ.get("REVERB_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="未配置 REVERB_TOKEN。请在 backend 目录创建 .env 并写入 REVERB_TOKEN=你的令牌",
        )

    try:
        raw = await search_reverb_listings_async(token, q.strip())
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:500] if e.response else str(e)
        raise HTTPException(
            status_code=502,
            detail=f"Reverb API 返回 {e.response.status_code if e.response else '?'}: {detail}",
        ) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"请求 Reverb 失败: {e}") from e

    results = [listing_to_search_item(item) for item in raw]
    return {"query": q.strip(), "results": results}


@app.get("/api/search")
async def api_search(
    q: str = Query(
        "",
        description=(
            "搜索关键词；并发查询 Reverb、Digimart、GuitarGuitar、Ishibashi、Swee Lee"
        ),
    ),
    page: int = Query(1, ge=1, description="页码，从 1 开始；五方使用同一页码参数"),
) -> dict[str, Any]:
    """
    ``asyncio.gather`` 并发：``_safe_fetch_reverb_listings_for_merge``、``scrape_digimart``、
    ``scrape_guitarguitar``、``_safe_scrape_ishibashi``、``_safe_scrape_sweelee``。任一源失败时
    该源返回空列表，不阻断其它平台；合并结果按规范化 ``url`` 去重。

    每条 ``results``：``title`` / ``image`` / ``price_usd`` / ``price_cny`` / ``source`` /
    ``url`` / ``condition``（``全新`` 或 ``二手``）。

    Digimart 仅在第 1 页抓取（避免 SSR 多页重复）；GuitarGuitar 使用
    ``/search/?Query=…&page=…`` 并在后端按标题关键词与二手文案过滤。
    Ishibashi：Shopify ``search.json``（若非 JSON 则 ``search/suggest.json`` + ``products.json`` 筛选）。
    Swee Lee：同上策略；``search.json`` 带 ``currency=SGD``；汇价按 JSON 实际货币（ ``SGD`` / ``CNY`` 等）。

    汇价：Frankfurter（含 JPY / SGD→CNY）；GBP→CNY 优先接口结果，缺省时 ``GBP_CNY_RATE``（默认 9.15）。
    ``price_usd`` = ``price_cny / (1 USD→CNY)``。
    """
    q_clean = q.strip()
    if not q_clean:
        return {"query": "", "page": 1, "has_more": False, "results": []}

    page_no = max(1, page)
    logger.info(
        "[api/search] start concurrent Reverb+Digimart+GuitarGuitar+Ishibashi+SweeLee query=%r page=%s",
        q_clean,
        page_no,
    )

    rev_out, digi_out, gg_out, ishi_out, swee_out = await asyncio.gather(
        _safe_fetch_reverb_listings_for_merge(q_clean, page_no),
        scrape_digimart(q_clean, page_no),
        scrape_guitarguitar(q_clean, page_no),
        _safe_scrape_ishibashi(q_clean, page_no),
        _safe_scrape_sweelee(q_clean, page_no),
        return_exceptions=True,
    )

    if not isinstance(digi_out, list):
        logger.error(
            "[api/search] Digimart task returned non-list (unexpected): %r",
            digi_out,
            exc_info=(
                (type(digi_out), digi_out, digi_out.__traceback__)
                if isinstance(digi_out, BaseException)
                else None
            ),
        )
    if not isinstance(gg_out, list):
        logger.error(
            "[api/search] GuitarGuitar task returned non-list (unexpected): %r",
            gg_out,
            exc_info=(
                (type(gg_out), gg_out, gg_out.__traceback__)
                if isinstance(gg_out, BaseException)
                else None
            ),
        )
    if not isinstance(ishi_out, list):
        logger.error(
            "[api/search] Ishibashi task returned non-list (unexpected): %r",
            ishi_out,
            exc_info=(
                (type(ishi_out), ishi_out, ishi_out.__traceback__)
                if isinstance(ishi_out, BaseException)
                else None
            ),
        )
    if not isinstance(swee_out, list):
        logger.error(
            "[api/search] Swee Lee task returned non-list (unexpected): %r",
            swee_out,
            exc_info=(
                (type(swee_out), swee_out, swee_out.__traceback__)
                if isinstance(swee_out, BaseException)
                else None
            ),
        )

    digi_raw: list[dict[str, Any]] = digi_out if isinstance(digi_out, list) else []
    gg_raw: list[dict[str, Any]] = gg_out if isinstance(gg_out, list) else []
    ishi_raw: list[dict[str, Any]] = ishi_out if isinstance(ishi_out, list) else []
    swee_raw: list[dict[str, Any]] = swee_out if isinstance(swee_out, list) else []

    if not isinstance(rev_out, list):
        if isinstance(rev_out, BaseException):
            logger.error(
                "[api/search] Reverb task raised unexpectedly (should be empty list): %r",
                rev_out,
                exc_info=(type(rev_out), rev_out, rev_out.__traceback__),
            )
        raw_rev: list[dict[str, Any]] = []
    else:
        raw_rev = rev_out

    if not raw_rev and not digi_raw and not gg_raw and not ishi_raw and not swee_raw:
        return {"query": q_clean, "page": page_no, "has_more": False, "results": []}

    has_more = (
        (len(raw_rev) >= REVERB_PER_PAGE)
        or (page_no == 1 and len(digi_raw) >= DIGIMART_PER_PAGE)
        or (len(gg_raw) >= GUITARGUITAR_FULL_PAGE)
        or (len(ishi_raw) >= ISHIBASHI_HAS_MORE_HINT)
        or (len(swee_raw) >= SWEELEE_HAS_MORE_HINT)
    )

    currencies: set[str] = {"USD"}
    for listing in raw_rev:
        _, cur = _reverb_amount_currency(listing)
        if cur:
            currencies.add(cur)
    if digi_raw:
        currencies.add("JPY")
    for ib in ishi_raw:
        ic = _ishibashi_normalize_iso_currency(ib.get("original_currency")) or "JPY"
        currencies.add(ic)
    if gg_raw:
        currencies.add("GBP")
    for sw in swee_raw:
        sc = _ishibashi_normalize_iso_currency(sw.get("original_currency")) or "SGD"
        currencies.add(sc)

    async with httpx.AsyncClient(timeout=20.0) as fx_client:
        try:
            rates_map = await get_rates_to_cny(fx_client, currencies)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"请求汇率服务失败: {e}") from e

    try:
        usd_to_cny = rates_map["USD"]
    except KeyError as e:
        raise HTTPException(status_code=502, detail="汇率结果缺少 USD→CNY") from e

    results: list[dict[str, Any]] = []

    for listing in raw_rev:
        title = str(listing.get("title") or listing.get("name") or "")
        image = extract_first_photo_url(listing)
        url = extract_listing_web_url(listing)
        amt, cur = _reverb_amount_currency(listing)
        price_cny: float | None = None
        if amt is not None and cur:
            if cur == "CNY":
                price_cny = amt
            elif cur in rates_map:
                price_cny = amt * rates_map[cur]
        results.append(
            _unified_row(
                title=title,
                image=image,
                url=url,
                source="Reverb",
                price_cny=price_cny,
                usd_to_cny=usd_to_cny,
                condition=_reverb_condition_cn(listing),
            )
        )

    for d in digi_raw:
        jpy_amt = int(d["jpy"])
        pcny: float | None = None
        if "JPY" in rates_map:
            pcny = jpy_amt * rates_map["JPY"]
        digi_condition = str(d.get("condition") or "二手")
        if digi_condition not in ("全新", "二手"):
            digi_condition = "二手"
        results.append(
            _unified_row(
                title=str(d["title"]),
                image=d.get("image"),
                url=str(d["url"]),
                source="Digimart",
                price_cny=pcny,
                usd_to_cny=usd_to_cny,
                condition=digi_condition,
            )
        )

    gbp_rate = _gbp_to_cny_rate(rates_map)
    for g in gg_raw:
        gbp_amt = float(g["gbp"])
        pcny_gg = gbp_amt * gbp_rate
        results.append(
            _unified_row(
                title=str(g["title"]),
                image=g.get("image"),
                url=str(g["url"]),
                source="GuitarGuitar",
                price_cny=pcny_gg,
                usd_to_cny=usd_to_cny,
                condition="二手",
            )
        )

    for ib in ishi_raw:
        amt_ib = float(ib["price_raw"])
        pcny_ib = _ishibashi_amount_to_cny(
            amt_ib,
            str(ib.get("original_currency") or "JPY"),
            rates_map,
        )
        ib_cond = str(ib.get("condition") or "二手")
        if ib_cond not in ("全新", "二手"):
            ib_cond = "二手"
        results.append(
            _unified_row(
                title=str(ib["title"]),
                image=ib.get("image"),
                url=str(ib["url"]),
                source="Ishibashi",
                price_cny=pcny_ib,
                usd_to_cny=usd_to_cny,
                condition=ib_cond,
            )
        )

    for sw in swee_raw:
        amt_sw = float(sw["price_raw"])
        pcny_sw = _ishibashi_amount_to_cny(
            amt_sw,
            str(sw.get("original_currency") or "SGD"),
            rates_map,
        )
        sw_cond = str(sw.get("condition") or "二手")
        if sw_cond not in ("全新", "二手"):
            sw_cond = "二手"
        results.append(
            _unified_row(
                title=str(sw["title"]),
                image=sw.get("image"),
                url=str(sw["url"]),
                source="Swee Lee",
                price_cny=pcny_sw,
                usd_to_cny=usd_to_cny,
                condition=sw_cond,
            )
        )

    before_dedupe = len(results)
    results = _dedupe_results_preserve_order(results)
    if before_dedupe > len(results):
        logger.info(
            "[api/search] deduped by url: %s -> %s rows",
            before_dedupe,
            len(results),
        )

    n_rev = sum(1 for row in results if row.get("source") == "Reverb")
    n_dig = sum(1 for row in results if row.get("source") == "Digimart")
    n_gg = sum(1 for row in results if row.get("source") == "GuitarGuitar")
    n_ishi = sum(1 for row in results if row.get("source") == "Ishibashi")
    n_swee = sum(1 for row in results if row.get("source") == "Swee Lee")
    logger.info(
        "[api/search] done query=%r page=%s total=%s "
        "(reverb=%s digimart=%s guitarguitar=%s ishibashi=%s sweelee=%s) has_more=%s",
        q_clean,
        page_no,
        len(results),
        n_rev,
        n_dig,
        n_gg,
        n_ishi,
        n_swee,
        has_more,
    )

    return {
        "query": q_clean,
        "page": page_no,
        "has_more": has_more,
        "results": results,
    }


if HAS_FRONTEND:
    app.mount(
        "/",
        StaticFiles(directory=str(DIST_DIR), html=True),
        name="frontend",
    )
