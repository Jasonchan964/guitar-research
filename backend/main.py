"""
吉他搜索测试后端：根据实时汇率把多币种价格换算成人民币（CNY）。

汇率来源：Frankfurter（欧洲央行参考汇率，免费、无需 API Key）
文档：https://www.frankfurter.app/docs/

另：`GET /search` 使用 Reverb API（需环境变量 REVERB_TOKEN）。
`GET /api/search` 并发请求 Reverb、Digimart 与 GuitarGuitar（Pre-Owned）；单方失败返回空列表，不影响其余平台。
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
from urllib.parse import quote_plus, urljoin, urlparse, urlunparse

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
    GuitarGuitar Pre-Owned 搜索 URL。

    分页必须用路径 ``/pre-owned/page-N/``；单独使用 ``?page=N`` 查询参数时列表仍停留在第 1 页。
    """
    enc = quote_plus(keyword.strip())
    if page <= 1:
        return f"{GUITARGUITAR_ORIGIN}/pre-owned/?Query={enc}"
    return f"{GUITARGUITAR_ORIGIN}/pre-owned/page-{page}/?Query={enc}"


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


def _guitarguitar_anchor_to_raw(anchor: Any) -> dict[str, Any] | None:
    """单条 ``a.product``（Pre-Owned 列表）→ 标题、图片、英镑价格、链接。"""
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
        ds = (img.get("data-src") or "").strip()
        if ds and "blank" not in ds.casefold():
            image = urljoin(GUITARGUITAR_ORIGIN, ds)
            break

    url = urljoin(GUITARGUITAR_ORIGIN, href)
    return {
        "title": title,
        "image": image,
        "gbp": price_gbp,
        "url": url,
        "condition": "二手",
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
    异步抓取 GuitarGuitar Pre-Owned 搜索列表（与站点 HTML ``a.product`` 解析一致）。

    异常或超时返回空列表，不向外抛错。
    """
    q = keyword.strip()
    if not q:
        logger.info("[GuitarGuitar] scrape skipped (empty keyword)")
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
        for anchor in blocks[:GUITARGUITAR_MAX_PARSE]:
            raw = _guitarguitar_anchor_to_raw(anchor)
            if raw is not None:
                out.append(raw)

        logger.info(
            "[GuitarGuitar] scrape success keyword=%r page=%s parsed_items=%s",
            q,
            pg,
            len(out),
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
    """合并 Reverb / Digimart / GuitarGuitar 时按 URL 去重用的规范化键（scheme/host 小写、去尾斜杠）。"""
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
    q: str = Query("", description="搜索关键词；并发查询 Reverb、Digimart、GuitarGuitar"),
    page: int = Query(1, ge=1, description="页码，从 1 开始；三方使用同一页码参数"),
) -> dict[str, Any]:
    """
    ``asyncio.gather`` 并发：``_safe_fetch_reverb_listings_for_merge``、``scrape_digimart``、
    ``scrape_guitarguitar``。Reverb / Digimart / GuitarGuitar 任一失败时该源返回空列表，
    不阻断其它平台；合并结果按规范化 ``url`` 去重。

    每条 ``results``：``title`` / ``image`` / ``price_usd`` / ``price_cny`` / ``source`` /
    ``url`` / ``condition``（``全新`` 或 ``二手``；GuitarGuitar Pre-Owned 均为 ``二手``）。

    Digimart 仅在第 1 页抓取（避免 SSR 多页重复）；GuitarGuitar 使用路径分页 ``/pre-owned/page-N/``。

    汇价：Frankfurter；GBP→CNY 优先接口结果，缺省时 ``GBP_CNY_RATE``（默认 9.15）。
    ``price_usd`` = ``price_cny / (1 USD→CNY)``。
    """
    q_clean = q.strip()
    if not q_clean:
        return {"query": "", "page": 1, "has_more": False, "results": []}

    page_no = max(1, page)
    logger.info(
        "[api/search] start concurrent Reverb+Digimart+GuitarGuitar query=%r page=%s",
        q_clean,
        page_no,
    )

    rev_out, digi_out, gg_out = await asyncio.gather(
        _safe_fetch_reverb_listings_for_merge(q_clean, page_no),
        scrape_digimart(q_clean, page_no),
        scrape_guitarguitar(q_clean, page_no),
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

    digi_raw: list[dict[str, Any]] = digi_out if isinstance(digi_out, list) else []
    gg_raw: list[dict[str, Any]] = gg_out if isinstance(gg_out, list) else []

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

    if not raw_rev and not digi_raw and not gg_raw:
        return {"query": q_clean, "page": page_no, "has_more": False, "results": []}

    has_more = (
        (len(raw_rev) >= REVERB_PER_PAGE)
        or (page_no == 1 and len(digi_raw) >= DIGIMART_PER_PAGE)
        or (len(gg_raw) >= GUITARGUITAR_FULL_PAGE)
    )

    currencies: set[str] = {"USD"}
    for listing in raw_rev:
        _, cur = _reverb_amount_currency(listing)
        if cur:
            currencies.add(cur)
    if digi_raw:
        currencies.add("JPY")
    if gg_raw:
        currencies.add("GBP")

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
    logger.info(
        "[api/search] done query=%r page=%s total=%s (reverb=%s digimart=%s guitarguitar=%s) has_more=%s",
        q_clean,
        page_no,
        len(results),
        n_rev,
        n_dig,
        n_gg,
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
