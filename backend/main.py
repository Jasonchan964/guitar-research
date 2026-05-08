"""
吉他搜索测试后端：根据内存缓存汇率把多币种价格换算成人民币（CNY）。

汇率：进程内 ``EXCHANGE_RATES``（默认 USD/GBP 等），应用启动时用 Frankfurter 刷新一次（``exchange_rate_cache``）。
文档：https://www.frankfurter.app/docs/

另：`GET /search` 与 ``scrape_reverb`` 使用 Reverb API，仅读取环境变量 ``REVERB_API_TOKEN``。
`GET /api/search` 可按 ``platforms`` 仅抓取勾选站点（默认五站全开），支持 ``sort``（``relevance`` / ``price_desc`` / ``price_asc``）；多站时各平台**同一页码**并发抓取，按固定顺序 **extend** 合并后经过去重 / 成色过滤（自第 ``SEARCH_FAST_STREAM_PAGE_THRESHOLD`` 页起不做跨平台价格全局重排）；每平台独立 **3s** 超时。
返回统一结构：title / image / price_usd / price_cny / source / url / condition。
``/api/search`` 合并换算只读内存汇率（``resolve_rate_to_cny``），请求路径不发 Frankfurter。
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback
from pathlib import Path
from collections.abc import Awaitable
from typing import Annotated, Any
from urllib.parse import quote_plus, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from env_load import load_project_dotenv

load_project_dotenv()

# 优先从环境变量读取（Render 等生产环境）；本地未配置时使用固定兜底密钥，须在导入依赖 JWT 的模块之前写入 os.environ
_JWT_LOCAL_DEFAULT = "your-super-secret-key-for-local-development-ribo-precision"
JWT_SECRET_KEY = (os.environ.get("JWT_SECRET_KEY") or "").strip() or _JWT_LOCAL_DEFAULT
os.environ["JWT_SECRET_KEY"] = JWT_SECRET_KEY
if JWT_SECRET_KEY == _JWT_LOCAL_DEFAULT:
    print(
        "[安全提示] 正在使用本地默认 JWT_SECRET_KEY。部署到 Render 生产环境时，请务必在环境变量中配置真实的 JWT_SECRET_KEY！",
        flush=True,
    )

_reverb_tok = (os.environ.get("REVERB_API_TOKEN") or "").strip()
_reverb_preview = f"{_reverb_tok[:8]}***" if _reverb_tok else "未检测到"
print(
    f"=== [系统启动检查] Reverb Token 状态: {_reverb_preview} ===",
    flush=True,
)

from database import SessionLocal, init_db
from deps import get_current_user_optional
from url_normalize import normalize_original_url
from exchange_rate_cache import get_usd_cny_rate_cached, refresh_exchange_rates, resolve_rate_to_cny
from guitar_detail import fetch_guitar_detail
from routers.auth import router as auth_router
from routers.favorites import router as favorites_router
from scrapers.guitarguitar import GUITARGUITAR_FULL_PAGE, GUITARGUITAR_ORIGIN, scrape_guitarguitar
from scrapers.sweelee import (
    SWEELEE_ACCESSORY_DEMOTE_SUBSTRINGS,
    SWEELEE_BROWSER_HEADERS,
    SWEELEE_FORCE_CURRENCY_PARAMS,
    SWEELEE_GUITAR_BOOST_SUBSTRINGS,
    SWEELEE_HAS_MORE_HINT,
    SWEELEE_MAX_CATALOG_PAGES_WALK,
    SWEELEE_MIN_PREFERRED_PRICE_CNY,
    SWEELEE_ORIGIN,
    SWEELEE_PAGE_LIMIT,
    SWEELEE_PRODUCTS_JSON,
    SWEELEE_SEARCH_JSON,
    SWEELEE_SUGGEST_JSON,
    SWEELEE_SUGGEST_LIMIT,
)

if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
logger = logging.getLogger(__name__)

from models import Favorite, User
from reverb_client import (
    REVERB_LISTINGS_PER_PAGE_DEFAULT,
    extract_first_photo_url,
    extract_listing_web_url,
    hal_listing_price_amount_currency as _reverb_amount_currency,
    listing_to_search_item,
    search_reverb_listings_async,
)

# 与 Dockerfile 一致：构建产物在仓库根目录的 dist/，由同一进程托管前端（公网单域名）
DIST_DIR = Path(__file__).resolve().parent.parent / "dist"
HAS_FRONTEND = (DIST_DIR / "index.html").is_file()

DIGIMART_ORIGIN = "https://www.digimart.net"
DIGIMART_SEARCH = f"{DIGIMART_ORIGIN}/search"
# 搜索页表单：checkbox ``productTypes`` — ``NEW`` = 新品 / ``USED`` = 中古（与站内「新品」「中古」筛选项一致）
# 分页：站点认 ``currentPage``；若仅用 ``page``/``currentPageNo`` 而不带 ``currentPage``，可能始终返回第 1 页列表
DIGIMART_PRODUCT_TYPE_NEW = "NEW"
DIGIMART_PRODUCT_TYPE_USED = "USED"
# Reverb ``per_page``（与 ``reverb_client.REVERB_LISTINGS_PER_PAGE_DEFAULT`` 一致）与列表「满页」启发式
REVERB_PER_PAGE = REVERB_LISTINGS_PER_PAGE_DEFAULT
# ``/api/search`` 返回给前端的统一分页长度（多站合并后按该宽度切片）
SEARCH_PAGE_SIZE = REVERB_PER_PAGE
# 从该页起：多平台不再做跨列表全局排序，合并走轻量 ``extend`` 路径并跳过 Swee Lee 站内二次重排
SEARCH_FAST_STREAM_PAGE_THRESHOLD = 3
# Digimart 搜索页常见每页 20 条
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

ISHIBASHI_ORIGIN = "https://intl.ishibashi.co.jp"
# 主题常把 ``/search.json`` 渲染成 HTML；真实 JSON 多为 ``/search/suggest.json``（Predictive Search）
ISHIBASHI_SEARCH_JSON = f"{ISHIBASHI_ORIGIN}/search.json"
ISHIBASHI_SUGGEST_JSON = f"{ISHIBASHI_ORIGIN}/search/suggest.json"
ISHIBASHI_PRODUCTS_JSON = f"{ISHIBASHI_ORIGIN}/products.json"
ISHIBASHI_SUGGEST_LIMIT = 24
ISHIBASHI_PRODUCTS_LIMIT = 50
# ``has_more`` 启发式：石桥单页接近「满页」时认为可能还有下一页
ISHIBASHI_HAS_MORE_HINT = 24
# ``/api/search`` 可选平台（小写 slug，与前端 ``platforms`` 参数一致）
ALL_PLATFORM_SLUGS: frozenset[str] = frozenset(
    ("reverb", "digimart", "guitarguitar", "ishibashi", "sweelee")
)
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

# Swee Lee：常见吉他品牌（小写，用于词边界匹配）
SWEELEE_SINGLE_WORD_BRANDS: frozenset[str] = frozenset(
    {
        "fender",
        "gibson",
        "ibanez",
        "prs",
        "squier",
        "epiphone",
        "yamaha",
        "taylor",
        "martin",
        "maton",
        "guild",
        "rickenbacker",
        "jackson",
        "charvel",
        "schecter",
        "esp",
        "ltd",
        "gretsch",
        "sterling",
        "suhr",
        "cort",
        "washburn",
        "strandberg",
        "mayones",
        "caparison",
        "fgn",
        "kiesel",
        "heritage",
        "duesenberg",
        "dangelico",
        "godin",
        "seagull",
        "alvarez",
        "lag",
        "hartke",
    }
)
SWEELEE_MULTI_WORD_BRANDS: frozenset[str] = frozenset(
    {
        "paul reed smith",
        "music man",
        "musicman",
        "harley benton",
        "sterling by music man",
        "tom anderson",
        "d angelico",
        "d'angelico",
    }
)
_SWEELEE_CATEGORY_FILTER_KEY = "filter.p.m.custom.category"
_SWEELEE_ELECTRIC_GUITARS_CATEGORY = "Electric Guitars"
# 含以下意图时不强加「电吉他」集合过滤 / 品牌扩词，以免误伤 bass、原声、配件等
_SWEELEE_SKIP_GUITAR_CATEGORY_BOOST = re.compile(
    r"\b("
    r"bass|acoustic|classical|ukulele|violin|mandolin|banjo|lap\s*steel|"
    r"amp|amplifier|amps|pedal|pedals|cab|cabinets?|"
    r"interface|headphones?|strings?|strap|cables?|tuner|capo|picks?|"
    r"vinyl|drum|piano|keyboard|microphone|mic\b"
    r")\b",
    re.I,
)


def _sweelee_query_starts_with_brand(lower: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    joined = " ".join(tokens)
    for phrase in SWEELEE_MULTI_WORD_BRANDS:
        if lower == phrase or lower.startswith(phrase + " "):
            return True
    return tokens[0] in SWEELEE_SINGLE_WORD_BRANDS


def _sweelee_brand_boost(keyword: str) -> tuple[str, dict[str, str]]:
    """
    针对「Fender」这类品牌词：为 Swee Lee Shopify 搜索附加分类过滤（与站点 URL 中
    ``filter.p.m.custom.category=Electric+Guitars`` 一致），并在仅单个品牌词时将
    ``q`` 扩成 ``… Electric Guitar`` 以提高吉他本体在建议结果中的权重。
    """
    q0 = (keyword or "").strip()
    if not q0:
        return q0, {}
    lower = q0.lower()
    if _SWEELEE_SKIP_GUITAR_CATEGORY_BOOST.search(lower):
        return q0, {}
    tokens = [t for t in re.split(r"\s+", lower) if t]
    extra: dict[str, str] = {}
    if _sweelee_query_starts_with_brand(lower, tokens):
        extra[_SWEELEE_CATEGORY_FILTER_KEY] = _SWEELEE_ELECTRIC_GUITARS_CATEGORY
    expand = False
    if len(tokens) == 1 and tokens[0] in SWEELEE_SINGLE_WORD_BRANDS:
        expand = True
    elif joined := " ".join(tokens):
        if joined in SWEELEE_MULTI_WORD_BRANDS:
            expand = True
    api_q = f"{q0} Electric Guitar" if expand else q0
    return api_q, extra


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await refresh_exchange_rates()
    yield


app = FastAPI(
    title="Guitar Search API",
    version="0.1.0",
    lifespan=lifespan,
    # 生产环境由 StaticFiles 托管根路径时，避免 /docs 与前端路由混淆
    docs_url=None if HAS_FRONTEND else "/docs",
    redoc_url=None if HAS_FRONTEND else "/redoc",
)

# 允许跨域的源列表（Vite 预览常用端口、本地 React 脚手架 + 环境变量追加）
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
for _piece in os.getenv("ALLOWED_ORIGINS", "").split(","):
    _p = _piece.strip()
    if _p and _p not in origins:
        origins.append(_p)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(favorites_router)


def _load_favorite_hits_for_urls_sync(user_id: int, normalized_urls: list[str]) -> frozenset[str]:
    """仅查询当前页 URL 是否收藏：一次 ``IN`` 查询，避免载入全表收藏键。"""
    if not normalized_urls:
        return frozenset()
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(Favorite.original_url_normalized).where(
                Favorite.user_id == user_id,
                Favorite.original_url_normalized.in_(normalized_urls),
            )
        ).all()
        return frozenset(str(x).strip() for x in rows if x and str(x).strip())
    finally:
        db.close()


def _apply_favorite_flags(rows: list[dict[str, Any]], fav_urls: frozenset[str]) -> None:
    """就地写入 ``is_favorited``（与收藏入库键 ``normalize_original_url`` 一致）。"""
    if not fav_urls:
        for r in rows:
            r["is_favorited"] = False
        return
    for r in rows:
        k = normalize_original_url(str(r.get("url") or ""))
        r["is_favorited"] = bool(k and k in fav_urls)


def _compact_search_api_item(row: dict[str, Any]) -> dict[str, Any]:
    """
    列表接口精简字段：卡片渲染 + 登录态 ``is_favorited``；省略 ``all_images`` / ``description`` 等大字段。
    ``id`` 为规范化 URL 的短哈希，便于前端稳定 key。
    """
    url = str(row.get("url") or "")
    norm = normalize_original_url(url)
    item_id = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16] if norm else ""
    return {
        "id": item_id,
        "title": row.get("title"),
        "image": row.get("image"),
        "url": url,
        "price_usd": row.get("price_usd"),
        "price_cny": row.get("price_cny"),
        "source": row.get("source"),
        "condition": row.get("condition"),
        "is_favorited": bool(row.get("is_favorited")),
    }


def _gbp_to_cny_rate(rates_map: dict[str, float]) -> float:
    """
    1 GBP → CNY。优先 ``rates_map``（内存汇价）；缺失时使用环境变量 ``GBP_CNY_RATE``（默认 9.15）。
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


def get_digimart_hd_image(url: str) -> str:
    """
    Digimart 图片 URL：去掉查询参数，将缩略/中图后缀精准替换为 ``_l.`` 大图。
    （``_s`` 极小模糊、``_m`` 略模糊 → ``_l`` 琴行上传原图尺寸；可选 ``_g`` 超清另议。）
    """
    if not url:
        return ""
    url = url.split("?")[0]
    if "_s." in url:
        url = url.replace("_s.", "_l.")
    elif "_m." in url:
        url = url.replace("_m.", "_l.")
    elif "_thumb." in url:
        url = url.replace("_thumb.", "_l.")
    return url


def _parse_jpy_amount(text: str) -> int | None:
    """
    解析日元标价。不得使用「全文去非数字」：列表卡片上常见 ``2026/05/07`` 等日期，
    会与 ``¥198,000`` 拼成二十余位错误整数。
    """
    if not text or not str(text).strip():
        return None
    blob = str(text)
    # 显式 ¥ / ￥ 后金额（含千分位或无逗号）；同一区块多条时取**最后一条**（常为现价/税込价）
    yen_vals: list[int] = []
    for m in re.finditer(r"[¥￥]\s*([\d]{1,3}(?:,\d{3})+|[\d]{2,9})(?!\d)", blob):
        raw = m.group(1).replace(",", "")
        if raw.isdigit():
            n = int(raw)
            if 100 <= n <= 500_000_000:
                yen_vals.append(n)
    if yen_vals:
        return yen_vals[-1]
    # 「123,456 円」
    en_vals: list[int] = []
    for m in re.finditer(r"([\d]{1,3}(?:,\d{3})+|[\d]{2,9})\s*円", blob):
        raw = m.group(1).replace(",", "")
        if raw.isdigit():
            n = int(raw)
            if 100 <= n <= 500_000_000:
                en_vals.append(n)
    if en_vals:
        return en_vals[-1]
    # 保守回退：仅当去标点后的数字段足够短（避免吞日期+价格）
    digits = re.sub(r"\D", "", blob)
    if not digits or len(digits) > 9:
        return None
    try:
        n = int(digits)
    except ValueError:
        return None
    return n if 100 <= n <= 500_000_000 else None


def _blob_indicates_new_condition(blob: str) -> bool:
    """
    日文/英文常见「全新」信号：新品、S/S ランク、New、Unused、B-Stock 等。
    优先于二手关键词（由调用方先判断本函数）。
    """
    if not blob or not str(blob).strip():
        return False
    t = str(blob)
    tl = t.lower()
    if "新品" in t or "未使用" in t:
        return True
    if "brand new" in tl:
        return True
    if re.search(r"\bunused\b", tl):
        return True
    if re.search(r"\bnew\b", tl):
        return True
    # B-Stock：站点常视为未使用/近新品档（与 Swee Lee 原逻辑一致）
    if re.search(r"b[-\s]?stock", tl):
        return True
    # S 級 / S ランク / Rank S（Digimart 等；单独一个「S」也按评级处理）
    if "sランク" in tl or "ｓランク" in tl:
        return True
    if re.search(r"ランク\s*[sｓＳ]", t):
        return True
    if re.search(r"\brank\s*s\b", tl):
        return True
    if re.search(
        r"(?:コンディション|状態|condition)\s*[:：/／]?\s*[sｓＳ](?:\b|$|ランク)",
        t,
        re.I,
    ):
        return True
    if re.search(r"(?:^|[\s:：/／|・【（+＋])[sｓＳ](?:$|[\s+）】/／+・]|ランク)", t):
        return True
    compact = re.sub(r"[\s　]+", "", t)
    if re.fullmatch(r"[sｓＳ]", compact):
        return True
    return False


def _blob_indicates_used_condition(blob: str) -> bool:
    if not blob or not str(blob).strip():
        return False
    t = str(blob)
    tl = t.lower()
    if "中古" in t:
        return True
    if "二手" in t:
        return True
    if re.search(r"\bused\b", tl):
        return True
    if "second hand" in tl:
        return True
    if re.search(r"\bpre[- ]owned\b", tl):
        return True
    if re.search(r"pre[- ]loved", tl):
        return True
    if re.search(r"\brefurbished\b", tl) or "翻新" in t or "リファービッシュ" in t:
        return True
    if "ヴィンテージ" in t or re.search(r"\bvintage\b", tl):
        return True
    return False


def _classify_new_vs_used_from_text(*parts: str) -> str:
    """
    合并多段文案后判定成色。

    **必须先判二手、再判全新**：日文常见「中古 S ランク」表示二手里品相 S 级，
    若先命中 ``S`` 会误标为全新。
    """
    blob = " ".join(p for p in parts if (p or "").strip()).strip()
    if not blob:
        return "二手"
    if _blob_indicates_used_condition(blob):
        return "二手"
    if _blob_indicates_new_condition(blob):
        return "全新"
    return "二手"


def _digimart_condition_from_block(block: Any) -> str:
    """
    从 Digimart 列表卡片解析成色。
    ``.itemState`` 内常见単品评级 ``S`` / ``Sランク``，须判为「全新」，不可用单独的「新」字启发式。
    """
    state_el = block.select_one(".itemState")
    status_focus = state_el.get_text(" ", strip=True) if state_el is not None else ""

    title_text = ""
    ttl_a = block.select_one("p.ttl a")
    if ttl_a is not None:
        title_text = re.sub(r"\s+", " ", ttl_a.get_text(strip=True).replace("\xa0", " "))

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
            txt = el.get_text(" ", strip=True)
            if txt:
                chunks.append(txt)

    blob = " ".join(chunks) if chunks else block.get_text(" ", strip=True)
    # 标题常含「中古」而状态区仅标等级字母，须一并参与判定
    combined = " ".join(x for x in (title_text, status_focus, blob) if x).strip()
    return _classify_new_vs_used_from_text(combined)


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
    image: str | None = None
    if src:
        image = get_digimart_hd_image(_digimart_abs_url(src)) or None

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


def _reverb_condition_cn(listing: dict[str, Any]) -> str:
    """
    Reverb listing 的 ``condition`` → 统一中文「全新」/「二手」。

    仅将 **全新上架/原封** 类映射为「全新」。Reverb 的 **Mint / Excellent**
    等为二手里的品相等级，**不得**当作「全新」，否则会大量误判。
    """
    raw: Any = listing.get("condition")
    slug_part = ""
    if isinstance(raw, dict):
        slug_part = str(raw.get("slug") or raw.get("name") or "").strip()
        raw = (
            raw.get("display_name")
            or raw.get("name")
            or raw.get("display")
            or raw.get("slug")
            or raw.get("uuid")
        )
    if raw is None and not slug_part:
        return "二手"
    blob = " ".join(
        x for x in (str(raw) if raw is not None else "", slug_part) if str(x).strip()
    )
    normalized = blob.strip().casefold().replace("_", " ")
    if not normalized:
        return "二手"
    # 明确新品 / NOS
    if normalized == "brand new" or "brand new" in normalized:
        return "全新"
    if normalized in (
        "new",
        "new item",
        "new other",
        "new old stock",
        "new in box",
        "nos",
    ):
        return "全新"
    if "new old stock" in normalized or re.search(r"\bnos\b", normalized):
        return "全新"
    return "二手"


async def scrape_digimart(
    keyword: str,
    page: int = 1,
    *,
    condition: str = "all",
    sort: str = "relevance",
) -> list[dict[str, Any]]:
    """
    异步抓取 Digimart 搜索页（与 ``test_digimart.py`` 同源解析逻辑）。

    ``condition``（与 ``/api/search`` 一致，经 ``normalize_condition_param`` 规范）：
    - ``all``：不按新品/中古筛选；
    - ``new``：请求参数 ``productTypes=NEW``，服务端只返回「新品」库存，避免先抓整页再筛「全新」导致翻页被清空；
    - ``used``：``productTypes=USED``，只抓中古。

    分页：使用官网列表使用的 ``currentPage``（正整数页码）。勿单独依赖 ``page`` / ``currentPageNo``，
    否则部分请求会静默回落到第 1 页，导致与其它平台合并后 URL 去重异常或翻页「跳空白页」。

    解析结果仍经 ``_digimart_condition_from_block`` 做二次归类。

    网络/HTML 异常时返回空列表，不向外抛错，避免拖累其它平台。
    """
    q = keyword.strip()
    if not q:
        logger.info("[Digimart] scrape skipped (empty keyword)")
        return []

    pg = max(1, int(page))
    cond = normalize_condition_param(condition)
    sort_norm = normalize_sort_param(sort)

    logger.info(
        "[Digimart] scrape start keyword=%r page=%s condition=%s sort=%s",
        q,
        pg,
        cond,
        sort_norm,
    )
    try:
        params: dict[str, Any] = {"keyword": q, "currentPage": pg}
        if cond == "new":
            params["productTypes"] = DIGIMART_PRODUCT_TYPE_NEW
        elif cond == "used":
            params["productTypes"] = DIGIMART_PRODUCT_TYPE_USED
        sk = _digimart_sort_key_param(sort_norm)
        if sk:
            params["sortKey"] = sk
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


def _ishibashi_tags_blob(prod: dict[str, Any]) -> str:
    raw = prod.get("tags")
    if isinstance(raw, list):
        return ", ".join(str(x) for x in raw if x is not None)
    if isinstance(raw, str):
        return raw
    return ""


def _ishibashi_condition_from_product(prod: dict[str, Any]) -> str:
    """标题 + tags + vendor：与全站一致，识别 S 级 / New / 新品 / Unused 等为全新。"""
    title = str(prod.get("title") or "")
    tags = _ishibashi_tags_blob(prod)
    vendor = str(prod.get("vendor") or "")
    return _classify_new_vs_used_from_text(title, tags, vendor)


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


def _shopify_extract_all_image_urls(
    prod: dict[str, Any],
    upgrade_url: Any,
) -> list[str]:
    """
    从 Shopify 风格 ``products`` JSON 的 ``images`` 数组提取全部图片 URL；
    ``upgrade_url`` 为接受原始 ``src`` 并返回展示用 URL 的单参函数。
    """
    out: list[str] = []
    images = prod.get("images")
    if isinstance(images, list):
        for im in images:
            u = ""
            if isinstance(im, dict):
                u = (im.get("src") or im.get("url") or "").strip()
            elif isinstance(im, str):
                u = im.strip()
            if u:
                try:
                    out.append(str(upgrade_url(u)))
                except Exception:
                    out.append(u)
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def _shopify_body_html(prod: dict[str, Any]) -> str:
    """``body_html`` 原样透出给前端富文本展示；缺失或非字符串时为空串。"""
    raw = prod.get("body_html")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return str(raw)


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
    condition = _ishibashi_condition_from_product(prod)
    all_images = _shopify_extract_all_image_urls(prod, _ishibashi_upgrade_image_url)
    if not all_images and image_url:
        all_images = [image_url]
    description = _shopify_body_html(prod)
    return {
        "title": title,
        "image": image_url or None,
        "price_raw": float(price_raw),
        "original_currency": oc,
        "url": url,
        "condition": condition,
        "all_images": all_images,
        "description": description,
    }


def _ishibashi_amount_to_cny(
    price_raw: float,
    original_currency: str | None,
    rates_map: dict[str, float],
) -> float | None:
    """
    按 JSON 中的真实标价货币换算为 CNY；与全站内存汇价一致。
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
    Swee Lee：标题 / 标签 / 集合 / 类型 合并判定（与 Ishibashi / Digimart 信号一致）。
    B-Stock、New、S ランク、Unused 等为全新；Used / Pre-Loved / 中古 等为二手。
    """
    title = str(prod.get("title") or "")
    tags_blob = _sweelee_tags_blob(prod)
    col_blob = _sweelee_collections_blob(prod)
    product_type = str(prod.get("product_type") or prod.get("type") or "")
    return _classify_new_vs_used_from_text(title, tags_blob, col_blob, product_type)


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
    all_images = _shopify_extract_all_image_urls(prod, _sweelee_upgrade_image_url)
    if not all_images and image_url:
        all_images = [image_url]
    description = _shopify_body_html(prod)
    return {
        "title": title,
        "image": image_url or None,
        "price_raw": float(price_raw),
        "original_currency": oc,
        "url": url,
        "condition": _sweelee_condition_from_product(prod),
        "all_images": all_images,
        "description": description,
    }


async def scrape_ishibashi(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """
    石桥乐器国际站（Shopify）：所有请求带 ``currency=JPY``，尽量固定标价口径；
    仍从每条 JSON 解析 ``original_currency`` + ``price_raw``，由 ``/api/search`` 侧按内存汇价换算。

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
    ``products.json``。

    **分页**：站内与 JSON 均使用查询参数 ``page``（1-based），不用 ``offset``。
    回退路径下 ``products.json`` 是按全店目录分页的：必须顺序扫描目录页并按关键词命中做
    ``skip/take``，不能把目录页码直接当作搜索页码（详见 ``scrapers/sweelee`` 模块注释）。

    标价货币：解析 JSON 中真实 ``original_currency``；缺省为 ``SGD``；``currency=SGD``
    参数用于尽量固定标价口径。
    """
    q_raw = keyword.strip()
    if not q_raw:
        logger.info("[Swee Lee] scrape skipped (empty keyword)")
        return []

    pg = max(1, int(page))
    q_api, sweelee_boost_params = _sweelee_brand_boost(q_raw)

    logger.info(
        "[Swee Lee] scrape start keyword=%r api_q=%r page=%s boost=%r",
        q_raw,
        q_api,
        pg,
        sweelee_boost_params,
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(9.0, connect=5.0),
            follow_redirects=True,
        ) as client:
            merged_entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
            payload_search: dict[str, Any] | None = None

            products_primary: list[dict[str, Any]] = []
            r_search = await client.get(
                SWEELEE_SEARCH_JSON,
                params={
                    **SWEELEE_FORCE_CURRENCY_PARAMS,
                    **sweelee_boost_params,
                    "q": q_api,
                    "page": pg,
                    "limit": SWEELEE_PAGE_LIMIT,
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
            else:
                stride = SWEELEE_PAGE_LIMIT
                skip_n = (pg - 1) * stride
                need_end = skip_n + stride
                matches_stream: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
                seen_handles: set[str] = set()

                def _append_by_handle(
                    prod: dict[str, Any],
                    root: dict[str, Any] | None,
                ) -> None:
                    hx = prod.get("handle")
                    if not isinstance(hx, str) or not hx.strip():
                        return
                    hs = hx.strip()
                    if hs in seen_handles:
                        return
                    seen_handles.add(hs)
                    matches_stream.append((prod, root))

                r_suggest = await client.get(
                    SWEELEE_SUGGEST_JSON,
                    params={
                        **SWEELEE_FORCE_CURRENCY_PARAMS,
                        **sweelee_boost_params,
                        "q": q_api,
                        "resources[type]": "product",
                        "resources[limit]": str(SWEELEE_SUGGEST_LIMIT),
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
                            if isinstance(p, dict):
                                _append_by_handle(p, sug_root)
                    except Exception:
                        pass

                cat_page = 1
                while (
                    len(matches_stream) < need_end
                    and cat_page <= SWEELEE_MAX_CATALOG_PAGES_WALK
                ):
                    r_fb = await client.get(
                        SWEELEE_PRODUCTS_JSON,
                        params={
                            **SWEELEE_FORCE_CURRENCY_PARAMS,
                            **sweelee_boost_params,
                            "limit": stride,
                            "page": cat_page,
                        },
                        headers=SWEELEE_BROWSER_HEADERS,
                    )
                    if r_fb.status_code == 200 and _ishibashi_response_looks_json(r_fb):
                        try:
                            fb_payload = r_fb.json()
                            fb_root = (
                                fb_payload if isinstance(fb_payload, dict) else None
                            )
                            fb_all = fb_payload.get("products")
                            if isinstance(fb_all, list):
                                for p in fb_all:
                                    if not isinstance(p, dict):
                                        continue
                                    hx = p.get("handle")
                                    if (
                                        not isinstance(hx, str)
                                        or not hx.strip()
                                        or hx.strip() in seen_handles
                                    ):
                                        continue
                                    if _ishibashi_matches_keyword(
                                        str(p.get("title") or ""),
                                        str(p.get("vendor") or ""),
                                        q_raw,
                                    ):
                                        _append_by_handle(p, fb_root)
                        except Exception:
                            pass
                    cat_page += 1

                merged_entries = matches_stream[skip_n:need_end]

            out: list[dict[str, Any]] = []
            for prod, root_ctx in merged_entries:
                raw = _sweelee_product_to_raw(prod, root_payload=root_ctx)
                if raw is not None:
                    out.append(raw)

            logger.info(
                "[Swee Lee] scrape success keyword=%r page=%s items=%s",
                q_raw,
                pg,
                len(out),
            )
            return out

    except Exception as e:
        line = (
            f"[Swee Lee] scrape_sweelee error | keyword={q_raw!r} page={pg} | "
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
    """供 ``/api/search`` 合并：限时兜底；目录回退需连续翻页，略高于单请求超时。"""
    q = keyword.strip()
    if not q:
        return []
    pg = max(1, int(page))
    try:
        return await asyncio.wait_for(scrape_sweelee(q, pg), timeout=22.0)
    except asyncio.TimeoutError:
        logger.warning("[Swee Lee] asyncio.wait_for timeout (22s) keyword=%r page=%s", q, pg)
        return []
    except Exception as e:
        logger.error("[Swee Lee] _safe_scrape_sweelee unexpected: %s", e, exc_info=True)
        return []


def _reverb_api_token() -> str:
    """Reverb 官方 Personal Access Token，仅从 ``REVERB_API_TOKEN`` 读取。"""
    return (os.environ.get("REVERB_API_TOKEN") or "").strip()


def _reverb_official_request_headers(token: str) -> dict[str, str]:
    """与 Reverb 官方要求一致：Bearer、v2 Accept、``Accept-Version``、JSON Content-Type。"""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.reverb.v2+json",
        "Accept-Version": "3.0",
        "Content-Type": "application/json",
    }


async def scrape_reverb(
    keyword: str,
    page: int = 1,
    *,
    condition: str = "all",
    sort: str = "relevance",
) -> list[dict[str, Any]]:
    """
    通过 Reverb 官方 API（Bearer PAT）拉取列表；失败原因见 ``reverb_client`` 内终端调试输出。

    **Token / Headers**：仅 ``REVERB_API_TOKEN``；请求头由 ``_reverb_official_request_headers`` 与官方文档对齐。

    - Base URL：``https://api.reverb.com/api/listings``
    - Params：``query``、``page``、``per_page``（默认 24）；成色 ``new``/``used`` 时追加 ``conditions[]``
    """
    q = keyword.strip()
    if not q:
        logger.info("[Reverb] scrape_reverb skipped (empty keyword)")
        return []

    token = os.environ.get("REVERB_API_TOKEN", "").strip()
    if not token:
        print(
            "⚠️ [警告] 未从环境变量中检测到 REVERB_API_TOKEN，Reverb 搜索可能失效！",
            flush=True,
        )
        logger.warning(
            "[Reverb] scrape_reverb skipped: missing REVERB_API_TOKEN in environment",
        )
        return []

    headers = _reverb_official_request_headers(token)

    pg = max(1, int(page))
    cond = normalize_condition_param(condition)
    sort_norm = normalize_sort_param(sort)
    logger.info(
        "[Reverb] scrape_reverb start keyword=%r page=%s condition=%s sort=%s",
        q,
        pg,
        cond,
        sort_norm,
    )

    try:
        return await search_reverb_listings_async(
            token,
            q,
            page=pg,
            per_page=REVERB_PER_PAGE,
            condition=cond,
            sort=sort_norm,
            request_headers=headers,
        )
    except Exception as e:
        print(f"💥 [Reverb 异常] scrape_reverb 未预期错误: {e}", flush=True)
        logger.error("[Reverb] scrape_reverb unexpected: %s", e, exc_info=True)
        return []


async def _fetch_reverb_listings(
    query: str,
    page: int = 1,
    *,
    condition: str = "all",
    sort: str = "relevance",
) -> list[dict[str, Any]]:
    """内部封装：与 ``scrape_reverb`` 同源，供合并搜索复用。"""
    return await scrape_reverb(query, page, condition=condition, sort=sort)


async def _safe_fetch_reverb_listings_for_merge(
    query: str,
    page: int = 1,
    *,
    condition: str = "all",
    sort: str = "relevance",
) -> list[dict[str, Any]]:
    """供 ``/api/search`` 合并结果使用：Reverb 异常时返回空列表，不阻断其他平台。"""
    q = query.strip()
    if not q:
        return []
    pg = max(1, int(page))
    try:
        return await _fetch_reverb_listings(q, pg, condition=condition, sort=sort)
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
    all_images: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    price_usd: float | None = None
    if price_cny is not None and usd_to_cny > 0:
        price_usd = round(price_cny / usd_to_cny, 2)
    imgs: list[str] = []
    if all_images:
        imgs = [str(u).strip() for u in all_images if str(u).strip()]
    if not imgs and image:
        imgs = [str(image).strip()]
    desc = (description or "").strip() if description is not None else ""
    return {
        "title": title,
        "image": image,
        "price_usd": price_usd,
        "price_cny": round(price_cny, 2) if price_cny is not None else None,
        "source": source,
        "url": url,
        "condition": condition,
        "all_images": imgs,
        "description": desc,
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
    """
    **单次** ``/api/search`` 响应内：对本次并发合并后的列表按规范化 ``url`` 去重，
    保留首次出现顺序；无 URL 的条目不去重。不与历史页、前端缓存做跨请求去重。
    """
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


REVERB_CROSS_PAGE_SESSION_TTL_SEC = 3600.0
REVERB_CROSS_PAGE_SESSION_MAX_KEYS = 8192
_REVERB_CROSS_PAGE_LOCK = threading.Lock()
# ``cache_key -> (last_touch_monotonic, set[normalized_reverb_product_url])``
_REVERB_CROSS_PAGE_SEEN: dict[str, tuple[float, set[str]]] = {}


def _reverb_cross_page_scope_signature(
    q_clean: str,
    sort_norm: str,
    cond_norm: str,
    selected: frozenset[str],
) -> str:
    """同一会话下按搜索条件隔离已返回的 Reverb 商品 URL。"""
    blob = json.dumps(
        {
            "q": q_clean.casefold(),
            "sort": sort_norm,
            "cond": cond_norm,
            "platforms": sorted(selected),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _normalize_cross_page_session_id(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s or len(s) > 128:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", s):
        return ""
    return s


def _prune_reverb_cross_page_sessions_unlocked(now: float) -> None:
    global _REVERB_CROSS_PAGE_SEEN
    if len(_REVERB_CROSS_PAGE_SEEN) > REVERB_CROSS_PAGE_SESSION_MAX_KEYS:
        drop_n = len(_REVERB_CROSS_PAGE_SEEN) - REVERB_CROSS_PAGE_SESSION_MAX_KEYS // 2
        for key, _ in sorted(
            _REVERB_CROSS_PAGE_SEEN.items(),
            key=lambda kv: kv[1][0],
        )[: max(0, drop_n)]:
            _REVERB_CROSS_PAGE_SEEN.pop(key, None)
    expired = [
        k
        for k, (ts, _) in _REVERB_CROSS_PAGE_SEEN.items()
        if now - ts > REVERB_CROSS_PAGE_SESSION_TTL_SEC
    ]
    for k in expired:
        _REVERB_CROSS_PAGE_SEEN.pop(k, None)


def _apply_reverb_cross_page_session_filter(
    rows: list[dict[str, Any]],
    *,
    session_id: str | None,
    scope_sig: str | None,
    treats_all_as_reverb: bool = False,
) -> list[dict[str, Any]]:
    """
    同一 ``session_id`` + 同一搜索快照（``scope_sig``）下：跨请求过滤已在前面页下发过的 **Reverb** 行，
    减少翻页/API 漂移导致的重复。（依赖规范化商品 URL；未传 ``session_id`` 时不做任何事。）

    ``treats_all_as_reverb``：``GET /search`` 的扁平条目无 ``source`` 字段时为真。
    """
    sid = _normalize_cross_page_session_id(session_id)
    if not sid or not scope_sig:
        return rows
    canonical = f"{sid}::{scope_sig}"
    out: list[dict[str, Any]] = []

    now = time.monotonic()
    with _REVERB_CROSS_PAGE_LOCK:
        _prune_reverb_cross_page_sessions_unlocked(now)
        ent = _REVERB_CROSS_PAGE_SEEN.get(canonical)
        seen: set[str] = ent[1].copy() if ent else set()

        for r in rows:
            src = str(r.get("source") or "").strip()
            is_rev = src == "Reverb" or (
                treats_all_as_reverb and not src and str(r.get("url") or "").strip()
            )
            if not is_rev:
                out.append(r)
                continue
            uk = _normalize_url_for_dedup(str(r.get("url") or ""))
            if uk and uk in seen:
                continue
            out.append(r)

        for r in out:
            src = str(r.get("source") or "").strip()
            is_rev = src == "Reverb" or (
                treats_all_as_reverb and not src and str(r.get("url") or "").strip()
            )
            if not is_rev:
                continue
            uk = _normalize_url_for_dedup(str(r.get("url") or ""))
            if uk:
                seen.add(uk)

        _REVERB_CROSS_PAGE_SEEN[canonical] = (now, seen)

    return out


def _normalize_platform_slug_token(token: str) -> str | None:
    """
    将 ``platforms`` 查询串中的片段规范为 ``ALL_PLATFORM_SLUGS`` 之一；无法识别则 ``None``。
    允许去掉空格与常见分隔符（如 ``swee lee`` → ``sweelee``）。
    """
    s = re.sub(r"[\s_-]+", "", (token or "").strip().lower())
    if not s:
        return None
    if s in ALL_PLATFORM_SLUGS:
        return s
    return None


def parse_platforms_param(raw: str) -> set[str]:
    """
    解析 ``platforms``：``all`` 或未传有效列表时表示五站全开；否则按逗号拆分并去重。
    若调用方显式给出了**非 all** 的列表但无任何可识别 slug，返回空集（由路由层返回 400）。
    """
    s = (raw or "").strip().lower()
    if not s or s == "all":
        return set(ALL_PLATFORM_SLUGS)
    parts = [p for p in s.split(",") if p.strip()]
    if not parts:
        return set(ALL_PLATFORM_SLUGS)
    if any(p.strip().lower() == "all" for p in parts):
        return set(ALL_PLATFORM_SLUGS)
    out: set[str] = set()
    for p in parts:
        slug = _normalize_platform_slug_token(p)
        if slug:
            out.add(slug)
    return out


def normalize_condition_param(raw: str) -> str:
    c = (raw or "all").strip().lower()
    if c in ("all", "new", "used"):
        return c
    return "all"


def normalize_sort_param(raw: str | None) -> str:
    """
    统一排序：``relevance``（默认）| ``price_desc`` | ``price_asc``。
    接受 ``default`` 作为 ``relevance`` 的别名（与前端历史排序选项对齐）。
    """
    s = (raw or "relevance").strip().lower()
    if s == "default":
        return "relevance"
    if s in ("relevance", "price_desc", "price_asc"):
        return s
    return "relevance"


def _digimart_sort_key_param(sort_norm: str) -> str | None:
    """Digimart ``sortKey``：新品排序走站点默认（不传）；价格类显式传 ``PRICE_*``。"""
    if sort_norm == "price_desc":
        return "PRICE_DESC"
    if sort_norm == "price_asc":
        return "PRICE_ASC"
    return None


def _keyword_title_match_score(title: str, query: str) -> float:
    """关键词在标题中的出现频率加权（相关度回退，及多站合并时的全局相关度）。"""
    q = (query or "").strip()
    if not q:
        return 0.0
    tl = title.lower()
    ql = q.lower()
    score = float(tl.count(ql)) * 3.0
    for tok in re.split(r"[\s　]+", q):
        t = tok.strip().lower()
        if len(t) < 2:
            continue
        score += float(tl.count(t))
    return score


def _price_sort_tuple_desc(row: dict[str, Any]) -> tuple[int, float]:
    """价格降序键：无价格排最后。"""
    p = row.get("price_cny")
    if p is None:
        return (1, 0.0)
    try:
        return (0, -float(p))
    except (TypeError, ValueError):
        return (1, 0.0)


def _price_sort_tuple_asc(row: dict[str, Any]) -> tuple[int, float]:
    """价格升序键：无价格排最后。"""
    p = row.get("price_cny")
    if p is None:
        return (1, 0.0)
    try:
        return (0, float(p))
    except (TypeError, ValueError):
        return (1, 0.0)


def _sweelee_relevance_sort_adjustment(row: dict[str, Any]) -> float:
    """
    仅在 ``relevance`` 合并排序时生效：Swee Lee 行在统一 ``-keyword_score`` 上叠加。
    正值推后（配件、低价），负值略提前（吉他关键词）。与 ``_reorder_sweelee_raw_guitar_priority``
    方向一致。
    """
    if row.get("source") != "Swee Lee":
        return 0.0
    title = str(row.get("title") or "").lower()
    if any(s in title for s in SWEELEE_ACCESSORY_DEMOTE_SUBSTRINGS):
        return 5000.0
    p_raw = row.get("price_cny")
    try:
        pcny = float(p_raw) if p_raw is not None else None
    except (TypeError, ValueError):
        pcny = None
    adj = 0.0
    if pcny is None or pcny < float(SWEELEE_MIN_PREFERRED_PRICE_CNY):
        adj += 800.0
    boost = sum(1 for tok in SWEELEE_GUITAR_BOOST_SUBSTRINGS if tok in title)
    adj -= float(boost) * 120.0
    return adj


def _reorder_sweelee_raw_guitar_priority(
    swee_raw: list[dict[str, Any]],
    rates_map: dict[str, float],
) -> list[dict[str, Any]]:
    """
    Swee Lee 原始行合并前重排：优先展示高价 + 标题含吉他型号词；低价（< 阈值）次之；
    背带 / 拨片 / 线材等配件固定置底。不改变条数，仅调整顺序。
    """
    if len(swee_raw) <= 1:
        return swee_raw

    def row_key(idx: int, sw: dict[str, Any]) -> tuple[int, float, str, int]:
        title = str(sw.get("title") or "")
        tl = title.lower()
        is_acc = any(s in tl for s in SWEELEE_ACCESSORY_DEMOTE_SUBSTRINGS)
        try:
            amt = float(sw["price_raw"])
        except (TypeError, ValueError, KeyError):
            amt = 0.0
        pcny = _ishibashi_amount_to_cny(
            amt,
            str(sw.get("original_currency") or "SGD"),
            rates_map,
        )
        if is_acc:
            return (2, 0.0, title.casefold(), idx)
        low = pcny is None or float(pcny) < float(SWEELEE_MIN_PREFERRED_PRICE_CNY)
        bucket = 1 if low else 0
        boost = float(
            sum(1 for tok in SWEELEE_GUITAR_BOOST_SUBSTRINGS if tok in tl),
        )
        return (bucket, -boost, title.casefold(), idx)

    pairs = list(enumerate(swee_raw))
    pairs.sort(key=lambda iv: row_key(iv[0], iv[1]))
    return [sw for _, sw in pairs]


def sort_unified_search_rows(
    rows: list[dict[str, Any]],
    sort_norm: str,
    query: str,
) -> list[dict[str, Any]]:
    """合并后的全局排序（同一请求内去重后的列表）。"""
    if sort_norm == "price_desc":
        return sorted(rows, key=_price_sort_tuple_desc)
    if sort_norm == "price_asc":
        return sorted(rows, key=_price_sort_tuple_asc)
    if sort_norm == "relevance":

        def rel_key(r: dict[str, Any]) -> tuple[float, str]:
            sc = _keyword_title_match_score(str(r.get("title") or ""), query)
            adj = _sweelee_relevance_sort_adjustment(r)
            return (-sc + adj, str(r.get("title") or ""))

        return sorted(rows, key=rel_key)
    return rows


def filter_results_by_condition(rows: list[dict[str, Any]], condition: str) -> list[dict[str, Any]]:
    """
    合并去重后的成色过滤：``new`` → 仅 ``全新``；``used`` → 仅 ``二手``；``all`` 不变。
    """
    c = normalize_condition_param(condition)
    if c == "all":
        return rows
    if c == "new":
        return [r for r in rows if str(r.get("condition") or "") == "全新"]
    if c == "used":
        return [r for r in rows if str(r.get("condition") or "") == "二手"]
    return rows


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/exchange-rate")
async def exchange_rate() -> dict[str, float]:
    """
    USD→CNY 参考汇率：读进程内 ``EXCHANGE_RATES``（启动时已尝试从 Frankfurter 刷新）。
    """
    rate = await get_usd_cny_rate_cached()
    return {"rate": round(rate, 4)}


@app.get("/api/guitar/detail")
async def api_guitar_detail(
    url: str = Query(
        ...,
        min_length=8,
        description="原站商品详情页完整 URL（http/https）",
    ),
    platform: str = Query(
        ...,
        min_length=2,
        description="平台名：Ishibashi / Swee Lee / Digimart / Reverb / GuitarGuitar",
    ),
) -> dict[str, Any]:
    """
    站内详情页数据：按平台抓取高清图、规格与描述，并换算 ``price_cny``（内存汇率）。

    返回字段：``title`` / ``price_cny`` / ``price_original`` / ``platform`` / ``condition`` /
    ``images`` / ``specs`` / ``description_html`` / ``buy_url``。
    """
    return await fetch_guitar_detail(url, platform)


@app.get("/search")
async def search_reverb(
    q: str = Query(
        ...,
        min_length=1,
        description="搜索关键词，例如 Fender（前端搜索框输入后点「搜索」或按回车提交）",
    ),
    sort: str = Query(
        "relevance",
        description="排序：``relevance`` | ``price_desc`` | ``price_asc``（传入 Reverb ``order``）",
    ),
    session_id: str = Query(
        "",
        description="可选。与 ``/api/search`` 相同语义：跨页时在进程内跳过已下发的 Reverb 商品（URL）。",
        max_length=128,
    ),
) -> dict[str, Any]:
    """
    调用 Reverb ``GET https://api.reverb.com/api/listings``，返回标题、图片、价格、原页链接。

    前端默认使用 ``GET /api/search``（含 Digimart）；本路由保留给仅需 Reverb 的调用方。

    需在 ``backend/.env`` 中配置 ``REVERB_API_TOKEN``（Personal Access Token）。
    """
    token = _reverb_api_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="未配置 REVERB_API_TOKEN。请在 backend/.env 中设置 REVERB_API_TOKEN=你的令牌",
        )

    sort_norm = normalize_sort_param(sort)
    raw = await search_reverb_listings_async(
        token,
        q.strip(),
        page=1,
        per_page=REVERB_PER_PAGE,
        condition="all",
        sort=sort_norm,
        request_headers=_reverb_official_request_headers(token),
    )

    results = [listing_to_search_item(item) for item in raw]
    scope_sig = _reverb_cross_page_scope_signature(
        q.strip(), sort_norm, "all", frozenset(("reverb",))
    )
    results = _apply_reverb_cross_page_session_filter(
        results,
        session_id=session_id,
        scope_sig=scope_sig,
        treats_all_as_reverb=True,
    )
    return {"query": q.strip(), "sort": sort_norm, "results": results}


# 请求第 N 页结果为空时，向后最多再尝试的页数（N+1 … N+API_SEARCH_EMPTY_PAGE_MAX_SKIP）
API_SEARCH_EMPTY_PAGE_MAX_SKIP = 2

# ``GET /api/search``：同关键词 + 筛选 + 请求页进程内短时缓存（不缓存收藏状态，命中后现查）
SEARCH_CACHE_TTL_SEC = 300.0
SEARCH_CACHE_MAX_ENTRIES = 2048
_SEARCH_CACHE_LOCK = threading.Lock()
# key -> (monotonic_expire_at, frozen payload dict)
_SEARCH_RESULT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# 多平台合并搜索：单平台任务墙钟上限（秒）；超时则该平台本轮视为空，其它平台照常返回
SEARCH_PLATFORM_FETCH_TIMEOUT_SEC = 3.0


def _search_result_cache_key(
    *,
    q_clean: str,
    page_req: int,
    selected: set[str],
    cond_norm: str,
    sort_norm: str,
    sid_norm: str,
) -> str:
    blob = json.dumps(
        {
            "q": q_clean,
            "page": page_req,
            "platforms": sorted(selected),
            "condition": cond_norm,
            "sort": sort_norm,
            "session_id": sid_norm,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _prune_search_result_cache_unlocked() -> None:
    now = time.monotonic()
    dead = [k for k, (exp, _) in _SEARCH_RESULT_CACHE.items() if exp <= now]
    for k in dead:
        del _SEARCH_RESULT_CACHE[k]
    while len(_SEARCH_RESULT_CACHE) > SEARCH_CACHE_MAX_ENTRIES:
        try:
            _SEARCH_RESULT_CACHE.pop(next(iter(_SEARCH_RESULT_CACHE)))
        except StopIteration:
            break


def _search_cache_get_unlocked(key: str) -> dict[str, Any] | None:
    entry = _SEARCH_RESULT_CACHE.get(key)
    if not entry:
        return None
    exp, payload = entry
    if time.monotonic() >= exp:
        del _SEARCH_RESULT_CACHE[key]
        return None
    return payload


def _search_cache_put_unlocked(key: str, payload: dict[str, Any]) -> None:
    _SEARCH_RESULT_CACHE[key] = (time.monotonic() + SEARCH_CACHE_TTL_SEC, payload)
    _prune_search_result_cache_unlocked()


async def _await_platform_search_list(
    label: str,
    coro: Awaitable[Any],
) -> list[dict[str, Any]]:
    """并发合并中的一路抓取：严格限时，超时或异常返回空列表，不拖累 ``asyncio.gather`` 其它任务。"""
    try:
        out = await asyncio.wait_for(coro, timeout=SEARCH_PLATFORM_FETCH_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.warning(
            "[api/search] platform timeout after %.1fs: %s",
            SEARCH_PLATFORM_FETCH_TIMEOUT_SEC,
            label,
        )
        return []
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[api/search] platform error [%s]: %s", label, e, exc_info=True)
        return []
    return out if isinstance(out, list) else []


async def _scrape_guitarguitar_for_merge(q_clean: str, pg: int) -> list[dict[str, Any]]:
    """GuitarGuitar：异常时返回空；墙钟限制由 ``_await_platform_search_list`` 统一施加。"""
    try:
        return await scrape_guitarguitar(q_clean, pg)
    except Exception as e:
        logger.exception("[GuitarGuitar] merge fetch failed: %s", e)
        return []


async def _merge_search_single_round(
    q_clean: str,
    fetch_page: int,
    selected: set[str],
    cond_norm: str,
    sort_norm: str,
    *,
    requested_page: int = 1,
) -> tuple[list[dict[str, Any]], bool]:
    """
    抓取 ``fetch_page`` 对应的一轮（每站同一页码）：各平台请求经 ``asyncio.gather`` **同时** 发出，
    每路 ``asyncio.wait_for(..., SEARCH_PLATFORM_FETCH_TIMEOUT_SEC)``，慢站超时返回空列表。
    汇率仅从内存 ``EXCHANGE_RATES`` / ``resolve_rate_to_cny`` 读取，搜索路径不请求 Frankfurter。

    ``requested_page`` ≥ ``SEARCH_FAST_STREAM_PAGE_THRESHOLD`` 时：固定顺序 **extend** 拼接各平台换算后的行，
    跳过 Swee Lee 站内吉他优先重排（省 CPU）；仍会 **去重 + 成色过滤**。前几页可走完整合并（含 Swee 重排）。

    第三方在支持时携带 Reverb ``order``、Digimart ``sortKey``。
    返回 ``(results, has_more)``；若所有平台原始列表均为空则 ``([], False)``。
    """
    pg = max(1, int(fetch_page))
    fast_stream = max(1, int(requested_page)) >= SEARCH_FAST_STREAM_PAGE_THRESHOLD

    coros: list[Awaitable[list[dict[str, Any]]]] = []
    labels: list[str] = []

    if "reverb" in selected:
        coros.append(
            _await_platform_search_list(
                "reverb",
                _safe_fetch_reverb_listings_for_merge(
                    q_clean, pg, condition=cond_norm, sort=sort_norm
                ),
            )
        )
        labels.append("reverb")
    if "digimart" in selected:
        coros.append(
            _await_platform_search_list(
                "digimart",
                scrape_digimart(q_clean, pg, condition=cond_norm, sort=sort_norm),
            )
        )
        labels.append("digimart")
    if "guitarguitar" in selected:
        coros.append(
            _await_platform_search_list(
                "guitarguitar",
                _scrape_guitarguitar_for_merge(q_clean, pg),
            )
        )
        labels.append("guitarguitar")
    if "ishibashi" in selected:
        coros.append(
            _await_platform_search_list(
                "ishibashi",
                _safe_scrape_ishibashi(q_clean, pg),
            )
        )
        labels.append("ishibashi")
    if "sweelee" in selected:
        coros.append(
            _await_platform_search_list(
                "sweelee",
                _safe_scrape_sweelee(q_clean, pg),
            )
        )
        labels.append("sweelee")

    gathered = await asyncio.gather(*coros)

    raw_rev: list[dict[str, Any]] = []
    digi_raw: list[dict[str, Any]] = []
    gg_raw: list[dict[str, Any]] = []
    ishi_raw: list[dict[str, Any]] = []
    swee_raw: list[dict[str, Any]] = []

    for name, out in zip(labels, gathered):
        if name == "reverb":
            raw_rev = out
            continue
        if name == "digimart":
            digi_raw = out
            continue
        if name == "guitarguitar":
            gg_raw = out
            continue
        if name == "ishibashi":
            ishi_raw = out
            continue
        if name == "sweelee":
            swee_raw = out

    if not raw_rev and not digi_raw and not gg_raw and not ishi_raw and not swee_raw:
        return [], False

    has_more = (
        ("reverb" in selected and len(raw_rev) >= REVERB_PER_PAGE)
        or ("digimart" in selected and len(digi_raw) >= DIGIMART_PER_PAGE)
        or ("guitarguitar" in selected and len(gg_raw) >= GUITARGUITAR_FULL_PAGE)
        or ("ishibashi" in selected and len(ishi_raw) >= ISHIBASHI_HAS_MORE_HINT)
        or ("sweelee" in selected and len(swee_raw) >= SWEELEE_HAS_MORE_HINT)
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

    rates_map: dict[str, float] = {
        iso: resolve_rate_to_cny(iso) for iso in currencies
    }
    usd_to_cny = rates_map.get("USD") or resolve_rate_to_cny("USD")
    rates_map["USD"] = usd_to_cny

    if swee_raw and not fast_stream:
        swee_raw = _reorder_sweelee_raw_guitar_priority(swee_raw, rates_map)

    reverb_rows: list[dict[str, Any]] = []
    for listing in raw_rev:
        title = str(listing.get("title") or listing.get("name") or "")
        image = extract_first_photo_url(listing)
        url = extract_listing_web_url(listing)
        amt, cur = _reverb_amount_currency(listing)
        price_cny: float | None = None
        if amt is not None:
            iso = (cur or "USD").strip().upper()[:3]
            if iso == "CNY":
                price_cny = amt
            elif iso in rates_map:
                price_cny = amt * rates_map[iso]
        reverb_rows.append(
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

    digi_rows: list[dict[str, Any]] = []
    for d in digi_raw:
        jpy_amt = int(d["jpy"])
        pcny: float | None = None
        if "JPY" in rates_map:
            pcny = jpy_amt * rates_map["JPY"]
        digi_condition = str(d.get("condition") or "二手")
        if digi_condition not in ("全新", "二手"):
            digi_condition = "二手"
        digi_rows.append(
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
    gg_rows: list[dict[str, Any]] = []
    for g in gg_raw:
        gbp_amt = float(g["gbp"])
        pcny_gg = gbp_amt * gbp_rate
        gg_rows.append(
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

    ishi_rows: list[dict[str, Any]] = []
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
        ishi_rows.append(
            _unified_row(
                title=str(ib["title"]),
                image=ib.get("image"),
                url=str(ib["url"]),
                source="Ishibashi",
                price_cny=pcny_ib,
                usd_to_cny=usd_to_cny,
                condition=ib_cond,
                all_images=ib.get("all_images")
                if isinstance(ib.get("all_images"), list)
                else None,
                description=str(ib.get("description") or ""),
            )
        )

    swee_rows: list[dict[str, Any]] = []
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
        swee_rows.append(
            _unified_row(
                title=str(sw["title"]),
                image=sw.get("image"),
                url=str(sw["url"]),
                source="Swee Lee",
                price_cny=pcny_sw,
                usd_to_cny=usd_to_cny,
                condition=sw_cond,
                all_images=sw.get("all_images")
                if isinstance(sw.get("all_images"), list)
                else None,
                description=str(sw.get("description") or ""),
            )
        )

    merged: list[dict[str, Any]] = []
    merged.extend(reverb_rows)
    merged.extend(digi_rows)
    merged.extend(gg_rows)
    merged.extend(ishi_rows)
    merged.extend(swee_rows)
    results = merged

    before_dedupe = len(results)
    results = _dedupe_results_preserve_order(results)
    if before_dedupe > len(results):
        logger.info(
            "[api/search] deduped by url (single response): %s -> %s rows",
            before_dedupe,
            len(results),
        )

    results = filter_results_by_condition(results, cond_norm)

    n_rev = sum(1 for row in results if row.get("source") == "Reverb")
    n_dig = sum(1 for row in results if row.get("source") == "Digimart")
    n_gg = sum(1 for row in results if row.get("source") == "GuitarGuitar")
    n_ishi = sum(1 for row in results if row.get("source") == "Ishibashi")
    n_swee = sum(1 for row in results if row.get("source") == "Swee Lee")
    logger.info(
        "[api/search] fetch_page=%s done query=%r total=%s "
        "(reverb=%s digimart=%s guitarguitar=%s ishibashi=%s sweelee=%s) has_more=%s",
        pg,
        q_clean,
        len(results),
        n_rev,
        n_dig,
        n_gg,
        n_ishi,
        n_swee,
        has_more,
    )

    return results, has_more


async def _run_global_search(
    q_clean: str,
    page_no: int,
    selected: set[str],
    cond_norm: str,
    sort_norm: str,
) -> tuple[list[dict[str, Any]], bool]:
    """
    统一分页：

    - **仅选一个平台**：只请求该平台第 ``page_no`` 页（第三方已按 ``sort`` 排序，必要时服务端再排一次）。
    - **多平台**：并发各站第 ``page_no`` 页后，按固定顺序 **extend** 合并 → 去重 → 成色过滤 → 截断。
      第 1–2 页：``price_*`` 仍对本批合并结果做一次全局排序；自第 ``SEARCH_FAST_STREAM_PAGE_THRESHOLD`` 页起
      **不再**跨平台重排，直接保留各站 API 顺序（仅依赖各站自身 ``sort``）。
    """
    if len(selected) == 1:
        rows, hm = await _merge_search_single_round(
            q_clean,
            page_no,
            selected,
            cond_norm,
            sort_norm,
            requested_page=page_no,
        )
        # 仅 Reverb 时「相关度」已由接口 ``order=relevance`` 排序，不再用标题词频覆盖。
        if sort_norm == "relevance" and selected == {"reverb"}:
            return rows, hm
        rows = sort_unified_search_rows(rows, sort_norm, q_clean)
        return rows, hm

    batch, hm = await _merge_search_single_round(
        q_clean,
        page_no,
        selected,
        cond_norm,
        sort_norm,
        requested_page=page_no,
    )
    if (
        sort_norm in ("price_desc", "price_asc")
        and page_no < SEARCH_FAST_STREAM_PAGE_THRESHOLD
    ):
        batch = sort_unified_search_rows(batch, sort_norm, q_clean)
    overflow = len(batch) > SEARCH_PAGE_SIZE
    window = batch[:SEARCH_PAGE_SIZE]
    has_more = hm or overflow
    return window, has_more


async def _api_search_collect_page(
    *,
    q_clean: str,
    page_no: int,
    selected: set[str],
    cond_norm: str,
    sort_norm: str,
    sid_norm: str,
    scope_sig: str,
) -> tuple[list[dict[str, Any]], bool]:
    """单页聚合 + Reverb 跨页会话过滤。"""
    results, has_more = await _run_global_search(
        q_clean, page_no, selected, cond_norm, sort_norm
    )
    results = _apply_reverb_cross_page_session_filter(
        results,
        session_id=sid_norm or None,
        scope_sig=scope_sig,
        treats_all_as_reverb=False,
    )
    return results, has_more


@app.get("/api/search")
async def api_search(
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    q: str = Query(
        "",
        description=(
            "搜索关键词；按 ``platforms`` 仅并发抓取勾选的平台（见该参数说明）"
        ),
    ),
    page: int = Query(1, ge=1, description="页码，从 1 开始；五方使用同一页码参数"),
    platforms: str = Query(
        "all",
        description='平台列表：``all`` 为五站全开；否则逗号分隔 slug，如 ``reverb,digimart``',
    ),
    condition: str = Query(
        "all",
        description='成色：``all`` | ``new``（仅全新）| ``used``（仅二手），在合并去重后过滤',
    ),
    sort: str = Query(
        "relevance",
        description=(
            "排序：``relevance``（默认）| ``price_desc`` | ``price_asc``；"
            "多站时各平台仅请求当前 ``page``；``price_*`` 在第 1–2 页对本页合并结果排序，第 3 页起不做跨平台全局重排"
        ),
    ),
    session_id: str = Query(
        "",
        description=(
            "可选。前端 Tab 会话 ID（字母数字 ``.-_~``，≤128）；"
            "与关键词/平台/成色/排序一起构成作用域：翻页时在进程内跳过已返回过的 **Reverb** 商品。"
        ),
        max_length=128,
    ),
) -> dict[str, Any]:
    """
    按需 ``asyncio.gather``：仅将 ``platforms`` 勾选的任务加入并发池（未选平台不发起网络请求）。

    性能：各平台第 ``page`` 路并发；每路 ``asyncio.wait_for`` **3s** 封顶，超时该平台本轮为空。
    汇率仅在内存读取。已登录时仅对**当前页商品 URL** 做一条 ``IN`` 查询填充 ``is_favorited``。

    Digimart：``condition=new|used`` 时先在请求上加 ``productTypes``（新品/中古），再解析列表；
    Reverb：``condition=new|used`` 时在 API 上追加 ``conditions[]=new|used``，``all`` 不传该参数；
    合并后仍按 ``condition`` 做一次全局成色过滤。

    **多页排序**：第 1–2 页可对合并结果做一次 ``price_*`` 全局排序；自第 **3** 页起仅按固定顺序 **extend**
    拼接（Reverb → Digimart → GuitarGuitar → Ishibashi → Swee Lee），不再跨平台全局重排。

    合并结果按规范化 ``url`` 去重（仅本次响应内）后，再按 ``condition`` 做二次过滤。
    Reverb：API 返回的单页列表在 ``reverb_client`` 内按稳定 listing 主键去重；若传 ``session_id``，
    同一搜索快照下跨页会跳过已下发过的 Reverb 商品（规范化 URL，进程内 TTL 缓存）。

    **结果缓存**：内存缓存 **5 分钟**（``SEARCH_CACHE_TTL_SEC``），键为关键词 + 页码 + 平台 + 成色 + 排序 +
    ``session_id``；缓存**不含**收藏标记，命中后不访问各站爬虫。短时间在第 2 / 4 页间跳转可复用同一份缓存。

    当 ``page>1`` 且过滤后结果为空时，自动向后最多尝试 ``API_SEARCH_EMPTY_PAGE_MAX_SKIP`` 页，
    返回首个非空页；此时响应含 ``requested_page`` 与 ``page_adjusted``。

    每条 ``results``（精简）：``id`` / ``title`` / ``image`` / ``url`` / ``price_usd`` /
    ``price_cny`` / ``source`` / ``condition`` / ``is_favorited``（已登录时一次 ``IN`` 查询批量填充）。
    """
    q_clean = q.strip()
    sort_norm = normalize_sort_param(sort)
    _empty_list: dict[str, Any] = {
        "items": [],
        "page": 1,
        "has_more": False,
        "query": "",
        "sort": sort_norm,
        "results": [],
        "total": 0,
        "total_count": 0,
    }
    if not q_clean:
        return _empty_list

    page_no = max(1, page)

    try:
        selected = parse_platforms_param(platforms)
        raw_plat = (platforms or "").strip()
        if raw_plat and raw_plat.lower() != "all" and not selected:
            raise HTTPException(
                status_code=400,
                detail="请至少选择一个有效的搜索平台（reverb,digimart,guitarguitar,ishibashi,sweelee）",
            )
        cond_norm = normalize_condition_param(condition)

        sid_norm = _normalize_cross_page_session_id(session_id)
        scope_sig = _reverb_cross_page_scope_signature(
            q_clean, sort_norm, cond_norm, frozenset(selected)
        )

        logger.info(
            "[api/search] start query=%r page=%s platforms=%s condition=%s sort=%s sid=%s user=%s",
            q_clean,
            page_no,
            sorted(selected),
            cond_norm,
            sort_norm,
            bool(sid_norm),
            current_user.id if current_user else None,
        )

        page_kw = dict(
            q_clean=q_clean,
            selected=selected,
            cond_norm=cond_norm,
            sort_norm=sort_norm,
            sid_norm=sid_norm,
            scope_sig=scope_sig,
        )

        cache_key = _search_result_cache_key(
            q_clean=q_clean,
            page_req=page_no,
            selected=selected,
            cond_norm=cond_norm,
            sort_norm=sort_norm,
            sid_norm=sid_norm,
        )

        cached_snapshot: dict[str, Any] | None = None
        with _SEARCH_CACHE_LOCK:
            _prune_search_result_cache_unlocked()
            cached_snapshot = _search_cache_get_unlocked(cache_key)

        if cached_snapshot is not None:
            logger.info(
                "[api/search] cache hit key=%s… query=%r page=%s",
                cache_key[:12],
                q_clean,
                page_no,
            )
            results = copy.deepcopy(cached_snapshot["results_rows"])
            has_more = bool(cached_snapshot["has_more"])
            effective_page = int(cached_snapshot["effective_page"])
            page_adjusted = bool(cached_snapshot["page_adjusted"])
        else:
            results, has_more = await _api_search_collect_page(page_no=page_no, **page_kw)

            effective_page = page_no
            page_adjusted = False

            if page_no > 1 and len(results) == 0:
                for step in range(1, API_SEARCH_EMPTY_PAGE_MAX_SKIP + 1):
                    fp = page_no + step
                    results, has_more = await _api_search_collect_page(page_no=fp, **page_kw)
                    if len(results) > 0:
                        effective_page = fp
                        page_adjusted = True
                        logger.info(
                            "[api/search] empty-page forward: requested=%s effective=%s",
                            page_no,
                            fp,
                        )
                        break

            with _SEARCH_CACHE_LOCK:
                _search_cache_put_unlocked(
                    cache_key,
                    {
                        "results_rows": copy.deepcopy(results),
                        "has_more": has_more,
                        "effective_page": effective_page,
                        "page_adjusted": page_adjusted,
                    },
                )

        if current_user is not None:
            norm_keys = [
                normalize_original_url(str(r.get("url") or ""))
                for r in results
            ]
            norm_keys_u = list(dict.fromkeys(k for k in norm_keys if k))
            fav_urls = await asyncio.to_thread(
                _load_favorite_hits_for_urls_sync,
                current_user.id,
                norm_keys_u,
            )
            _apply_favorite_flags(results, fav_urls)

        compact_rows = [_compact_search_api_item(r) for r in results]
        n = len(compact_rows)

        payload: dict[str, Any] = {
            "items": compact_rows,
            "page": effective_page,
            "has_more": has_more,
            "query": q_clean,
            "sort": sort_norm,
            "results": compact_rows,
            "total": n,
            "total_count": n,
        }
        if page_adjusted:
            payload["requested_page"] = page_no
            payload["page_adjusted"] = True
        return payload

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[api/search] failed query=%r page=%s: %s", q_clean, page_no, e)
        print(f"[api/search] ERROR: {e!s}", flush=True)
        traceback.print_exc()
        return {
            "items": [],
            "page": page_no,
            "has_more": False,
            "query": q_clean,
            "sort": sort_norm,
            "results": [],
            "total": 0,
            "total_count": 0,
            "error": str(e),
        }


if HAS_FRONTEND:
    app.mount(
        "/",
        StaticFiles(directory=str(DIST_DIR), html=True),
        name="frontend",
    )
