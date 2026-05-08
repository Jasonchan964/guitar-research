"""
GuitarGuitar (UK) pre-owned search list scraper.

Parses SSR HTML from ``/pre-owned/?Query=…`` using card-based selectors with
legacy ``a.product`` fallback.
"""

from __future__ import annotations

import asyncio
import logging
import re
import traceback
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

GUITARGUITAR_ORIGIN = "https://www.guitarguitar.co.uk"
GUITARGUITAR_FULL_PAGE = 40
GUITARGUITAR_MAX_PARSE = 40
PLATFORM_LABEL = "GuitarGuitar"

# 列表页：Chrome + Referer / Sec-Fetch-*（反爬与浏览器一致性）
GUITARGUITAR_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.guitarguitar.co.uk/",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

GUITARGUITAR_BROWSER_HEADERS_ALT: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.guitarguitar.co.uk/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def _guitarguitar_search_url(keyword: str, page: int) -> str:
    """
    GuitarGuitar 预购列表分页使用 **路径段** ``/pre-owned/page-N/``。

    查询参数 ``?page=N`` 在站点上无效（第 1、2 页 HTML 长度一致，实为同一页）。
    第 1 页：``/pre-owned/?Query=…``；第 2 页起：``/pre-owned/page-2/?Query=…``。
    """
    enc = quote_plus(keyword.strip())
    pg = max(1, int(page))
    if pg <= 1:
        return f"{GUITARGUITAR_ORIGIN}/pre-owned/?Query={enc}"
    return f"{GUITARGUITAR_ORIGIN}/pre-owned/page-{pg}/?Query={enc}"


def _guitarguitar_keyword_tokens(keyword: str) -> list[str]:
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


def _guitarguitar_card_is_pre_owned(
    root: Any,
    title: str,
    *,
    from_pre_owned_channel: bool = False,
) -> bool:
    if from_pre_owned_channel:
        return True
    blob = " ".join(
        [
            title,
            (root.get("title") or "").strip() if hasattr(root, "get") else "",
            root.get_text(" ", strip=True) if hasattr(root, "get_text") else "",
        ]
    ).lower()
    if "pre-owned" in blob or "second hand" in blob:
        return True
    return bool(re.search(r"\bused\b", blob))


def _parse_gbp_price_text(price_blob: str) -> float | None:
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


def _price_numeric_from_text(blob: str) -> float | None:
    """
    从片段中提取浮点价格：去掉逗号后用 ``\\d+\\.?\\d*``；
    多段数字时取合理区间内最大者（应对划线价 + 现价）。
    """
    if not blob or not str(blob).strip():
        return None
    s = str(blob).replace(",", "")
    candidates: list[float] = []
    for m in re.finditer(r"\d+\.?\d*", s):
        try:
            v = float(m.group(0))
            if 0 < v < 10_000_000:
                candidates.append(v)
        except ValueError:
            continue
    if not candidates:
        return None
    plausible = [x for x in candidates if 1 <= x <= 500_000]
    pool = plausible if plausible else candidates
    return max(pool)


def _parse_srcset_best_url(srcset: str) -> str | None:
    """从 ``srcset`` 中取带宽描述最大的一张（``NNNw``），否则取第一段 URL。"""
    if not srcset or not str(srcset).strip():
        return None
    best_url: str | None = None
    best_w = -1
    for segment in str(srcset).split(","):
        parts = segment.strip().split()
        if not parts:
            continue
        url = parts[0].strip()
        w = 0
        if len(parts) > 1:
            m = re.match(r"(\d+)w", parts[-1], re.I)
            if m:
                w = int(m.group(1))
        if w > best_w or (w == best_w and best_url is None):
            best_w = w
            best_url = url
    return best_url


def _is_placeholder_image_url(url: str) -> bool:
    u = (url or "").strip().casefold()
    if not u:
        return True
    if "blank.png" in u or "/content/images/blank" in u:
        return True
    if "blank" in u or "placeholder" in u or "data:image/svg" in u:
        return True
    if "/spacer" in u or "pixel.gif" in u:
        return True
    return False


def _is_brand_or_nav_image_url(url: str) -> bool:
    """站点 Logo / 导航图，勿当作商品主图。"""
    u = (url or "").strip().casefold()
    if not u:
        return True
    if "logo" in u and ("header" in u or "/content/" in u or "nav" in u):
        return True
    if "/content/images/logo" in u:
        return True
    return False


def _gg_normalize_image_href(raw: str) -> str:
    """
    将单条 URL / srcset 片段转为绝对地址。
    相对路径（含 ``/Content/...``）用本站 origin 拼接，见 GG 列表懒加载。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("//"):
        s = f"https:{s}"
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return urljoin(f"{GUITARGUITAR_ORIGIN}/", s.lstrip("/"))


def _lazy_urls_from_single_img_tag(img: Tag) -> list[str]:
    """
    仅读取 **当前这张** ``img`` 上的属性（等价 ``item.select_one('img')`` 后对单节点取值）。
    GG 懒加载优先级：``data-src`` → ``data-srcset`` → ``srcset`` → ``src``。
    """
    out: list[str] = []
    ds = (img.get("data-src") or "").strip()
    if ds:
        out.append(ds)
    for attr in ("data-srcset", "srcset"):
        blob = (img.get(attr) or "").strip()
        if blob:
            best = _parse_srcset_best_url(blob)
            if best:
                out.append(best)
    src = (img.get("src") or "").strip()
    if src:
        out.append(src)
    return out


def _first_usable_image_from_raw_candidates(raws: list[str]) -> str | None:
    for raw in raws:
        abs_u = _gg_normalize_image_href(raw)
        if not abs_u:
            continue
        if _is_placeholder_image_url(abs_u) or _is_brand_or_nav_image_url(abs_u):
            continue
        return _guitarguitar_upgrade_image_url(abs_u)
    return None


def _ordered_imgs_within_card(card: Tag) -> list[Tag]:
    """
    仅在 ``card`` 子树内查找 ``img``（``card.select`` / ``select_one``），绝不使用 ``soup``。
    优先商品图常见容器，再按文档顺序补齐其余 ``img``。
    """
    preferred: list[Tag] = []
    for sel in (
        '[class*="ProductImage"] img',
        '[class*="product-image"] img',
        '[class*="card-img"] img',
        '[class*="CardImage"] img',
        "picture img",
    ):
        for node in card.select(sel):
            if isinstance(node, Tag) and node.name == "img":
                preferred.append(node)
    seen_id = {id(x) for x in preferred}
    ordered: list[Tag] = list(preferred)
    for node in card.select("img"):
        if isinstance(node, Tag) and id(node) not in seen_id:
            seen_id.add(id(node))
            ordered.append(node)
    return ordered


def _lazy_urls_from_picture_source(tag: Tag) -> list[str]:
    """``picture > source``：data-src → data-srcset → srcset（与列表 img 懒加载优先级一致）。"""
    out: list[str] = []
    ds = (tag.get("data-src") or "").strip()
    if ds:
        out.append(ds)
    for attr in ("data-srcset", "srcset"):
        blob = (tag.get(attr) or "").strip()
        if blob:
            best = _parse_srcset_best_url(blob)
            if best:
                out.append(best)
    return out


def _image_url_from_item_container(card: Tag) -> str | None:
    """
    **仅限当前卡片节点** ``card``：只使用 ``card.select_one`` / ``card.select``，
    禁止 ``BeautifulSoup`` 级全局查找（否则会反复命中页顶同一张图）。

    懒加载字段顺序：data-src → data-srcset → srcset → src。
    """
    # 与主商品图同位的 picture/source
    pic = card.select_one("picture")
    if isinstance(pic, Tag):
        for src_el in pic.select("source"):
            if isinstance(src_el, Tag):
                got = _first_usable_image_from_raw_candidates(_lazy_urls_from_picture_source(src_el))
                if got:
                    return got
        img0 = pic.select_one("img")
        if isinstance(img0, Tag):
            got = _first_usable_image_from_raw_candidates(_lazy_urls_from_single_img_tag(img0))
            if got:
                return got

    for img in _ordered_imgs_within_card(card):
        got = _first_usable_image_from_raw_candidates(_lazy_urls_from_single_img_tag(img))
        if got:
            return got
    return None


def _guitarguitar_upgrade_image_url(absolute_url: str) -> str:
    original = (absolute_url or "").strip()
    if not original:
        return absolute_url
    try:
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


def _guitarguitar_normalize_list_href(href: str) -> str:
    full = urljoin(GUITARGUITAR_ORIGIN, (href or "").strip())
    p = urlparse(full)
    path = (p.path or "").rstrip("/").lower()
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    return f"{scheme}://{netloc}{path}"


def _first_three_div_class_names(soup: BeautifulSoup) -> list[str]:
    names: list[str] = []
    for div in soup.find_all("div", limit=3):
        c = div.get("class")
        if isinstance(c, list):
            names.append(" ".join(c))
        elif c:
            names.append(str(c))
        else:
            names.append("(no class)")
    return names


def _guitarguitar_html_snippet(html: str, max_chars: int = 500) -> str:
    if not html:
        return ""
    one_line = html.replace("\r\n", "\n").replace("\r", "\n")
    one_line = " ".join(one_line.split())
    return one_line[:max_chars]


def _guitarguitar_html_antibot_signals(html: str) -> list[str]:
    if not html:
        return []
    low = html.casefold()
    tags: list[str] = []
    if "cf-ray" in low or "cloudflare" in low:
        tags.append("cloudflare_marker")
    if "challenge-platform" in low or "cf-browser-verification" in low:
        tags.append("cloudflare_challenge_js")
    if "just a moment" in low and "checking your browser" in low:
        tags.append("cloudflare_interstitial")
    if "attention required" in low and "cloudflare" in low:
        tags.append("cloudflare_block")
    if "captcha" in low and ("enable javascript" in low or "verify you are human" in low):
        tags.append("possible_captcha")
    return tags


def _price_from_card_or_anchor(root: Tag) -> float | None:
    """优先新站点价格节点，再英镑解析，最后数字正则。"""
    for sel in (
        ".price-new",
        ".current-price",
        "span[data-price]",
        ".product-main-price",
        "[class*='product-main-price']",
        "[class*='MainPrice']",
        "[class*='product-price']",
        ".price",
        "[itemprop='price']",
    ):
        el = root.select_one(sel)
        if el is None:
            continue
        dp = (el.get("data-price") or "").strip()
        if dp:
            v = _parse_gbp_price_text(dp) or _price_numeric_from_text(dp)
            if v is not None and v > 0:
                return v
        txt = el.get_text(" ", strip=True)
        v = _parse_gbp_price_text(txt) or _price_numeric_from_text(txt)
        if v is not None and v > 0:
            return v
    meta = root.select_one("[itemprop='price'][content]")
    if meta is not None:
        raw = (meta.get("content") or "").strip()
        try:
            x = float(raw)
            if x > 0:
                return x
        except ValueError:
            pass
    blob = root.get_text(" ", strip=True)
    return _parse_gbp_price_text(blob) or _price_numeric_from_text(blob)


def _product_anchor_from_card(card: Tag) -> Tag | None:
    """
    当前 **卡片** 内的商品详链；仅 ``card.select_one('a[href*=\"/product/\"]')`` 或自身为 product 锚点。
    禁止在 ``soup`` 上搜造成误匹配。
    """
    if card.name == "a":
        href = (card.get("href") or "").strip()
        if href and "/product/" in href:
            return card
    a = card.select_one('a[href*="/product/"]')
    return a if isinstance(a, Tag) else None


def _title_from_product_card(card: Tag) -> str:
    """
    标题只从 **本卡片** 子树解析：先 ``card`` 内标题类节点，再商品 ``<a>`` 内子节点，最后该 ``<a>`` 的局部文本。
    不再对整张卡片用 ``get_text`` 糊成一段（易混入邻卡或页脚字）。
    """
    def _clean(t: str) -> str:
        return re.sub(r"\s+", " ", (t or "").replace("\xa0", " ")).strip()

    for sel in (
        ".qa-product-list-item-title",
        "[class*='product-list-item-title']",
        ".product-item-title",
        ".product-title",
        '[class*="product-title"]',
        "[data-product-title]",
        "h2",
        "h3",
        "h4",
        '[class*="Title"]',
    ):
        el = card.select_one(sel)
        if el is not None:
            t = _clean(el.get_text(" ", strip=True))
            if t:
                return t

    a = _product_anchor_from_card(card)
    if isinstance(a, Tag):
        for sel in ("h2", "h3", "h4", "[data-product-title]", '[class*="title"]'):
            el = a.select_one(sel)
            if el is not None:
                t = _clean(el.get_text(" ", strip=True))
                if t:
                    return t
        for img in a.select("img"):
            alt = _clean(img.get("alt") or "")
            if alt and len(alt) > 3:
                return alt
        t = _clean(a.get_text(" ", strip=True))
        if t:
            return t[:300]
    return ""


def _product_link_tag(root: Tag) -> Tag | None:
    return _product_anchor_from_card(root)


def _minimal_card_root_for_anchor(anchor: Tag) -> Tag:
    """
    锚点回退路径：向上找到包住该链接的 **小块** 容器（``li`` / ``article`` / 带 product 的 ``div``），
    避免父级过大导致 ``select('img')`` 命中其它商品的图。
    """
    node: Tag | None = anchor
    for _ in range(10):
        if node is None:
            break
        parent = node.parent
        if not isinstance(parent, Tag):
            break
        pn = (parent.name or "").lower()
        if pn in ("li", "article", "section"):
            return parent
        cls = parent.get("class")
        blob = (
            " ".join(cls)
            if isinstance(cls, list)
            else str(cls or "")
        ).casefold()
        if pn == "div" and (
            "productitem" in blob.replace("-", "").replace("_", "")
            or "product-card" in blob
            or "gg-product" in blob
            or "productlist" in blob.replace("-", "").replace("_", "")
        ):
            return parent
        node = parent
    return anchor


def _collect_product_roots(soup: BeautifulSoup) -> list[Tag]:
    """
    优先按 **单行商品容器** 收集（多 Section / 新 class 兼容），保证每张卡片边界清晰；
    每个容器内再取 ``a[href*='/product/']``，图片与标题均在容器内解析。

    对每种选择器统计「容器内含商品链」的数量，取 **命中最多** 的一组，避免
    ``.product-list-products > div`` 只匹配到包住整表格外壳的那一个 div。
    """
    seen: set[str] = set()
    ordered: list[Tag] = []

    shell = (
        soup.select_one(".product-list-products")
        or soup.select_one(".gg-product-list")
        or soup.select_one("[class*='product-list-products']")
    )
    scope: BeautifulSoup | Tag = shell if shell is not None else soup

    item_selectors = (
        "a.product[href*='/product/']",
        ".gg-product-list > div",
        'div[class*="ProductItem"]',
        'div[class*="ProductCard"]',
        'div[class*="product-item"]',
        ".product-list-products li",
        ".product-list-products .product-wrapper",
        ".product-list-products > div > div",
    )

    best_candidates: list[Tag] = []
    best_score = 0
    for sel in item_selectors:
        found = scope.select(sel)
        row: list[Tag] = []
        if sel.startswith("a."):
            row = [t for t in found if isinstance(t, Tag)]
        else:
            row = []
            for t in found:
                if not isinstance(t, Tag):
                    continue
                if t.select_one('a[href*="/product/"]'):
                    row.append(t)
        if len(row) > best_score:
            best_score = len(row)
            best_candidates = row

    candidates = best_candidates

    if candidates:
        for item in candidates:
            try:
                if item.name == "a":
                    a = item
                else:
                    a = item.select_one('a[href*="/product/"]')
                if not isinstance(a, Tag):
                    continue
                href = (a.get("href") or "").strip()
                key = _guitarguitar_normalize_list_href(href)
                if not key or key in seen:
                    continue
                seen.add(key)
                # 商品链为 ``a.product`` 时以锚点自身为容器，图/价均在 ``<a>`` 子树内
                ordered.append(item)
            except Exception:
                continue
        if ordered:
            return ordered

    list_roots: list[BeautifulSoup | Tag] = []
    plp = soup.select_one(".product-list-products")
    if plp is not None:
        list_roots.append(plp)
    ggl = soup.select_one(".gg-product-list")
    if ggl is not None:
        list_roots.append(ggl)
    list_roots.append(soup)

    for fb_scope in list_roots:
        for a in fb_scope.select('a[href*="/product/"]'):
            if not isinstance(a, Tag):
                continue
            href = (a.get("href") or "").strip()
            key = _guitarguitar_normalize_list_href(href)
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(_minimal_card_root_for_anchor(a))
        if ordered:
            break
    return ordered


def _root_to_raw(root: Tag) -> dict[str, Any] | None:
    """
    单卡解析：标题与价格为必填；图片尽量解析，失败则 ``image`` 为空串（仍保留条目）。
    """
    link = _product_link_tag(root)
    if link is None:
        return None
    href = (link.get("href") or "").strip()
    if not href or "/product/" not in href:
        return None

    try:
        title = _title_from_product_card(root)
    except Exception:
        title = ""
    if not (title or "").strip():
        return None

    try:
        price_gbp = _price_from_card_or_anchor(root)
    except Exception:
        price_gbp = None
    if price_gbp is None or price_gbp <= 0:
        return None

    image = ""
    try:
        got = _image_url_from_item_container(root)
        if got:
            image = got
    except Exception:
        pass

    url = urljoin(GUITARGUITAR_ORIGIN, href)
    return {
        "title": title.strip(),
        "image": image,
        "gbp": float(price_gbp),
        "url": url,
        "platform": PLATFORM_LABEL,
    }


def process_search_html(
    html: str,
    tokens: list[str],
    *,
    from_pre_owned_channel: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    soup = BeautifulSoup(html, "html.parser")
    blocks = _collect_product_roots(soup)
    out: list[dict[str, Any]] = []
    parsed_ok = 0
    dropped_kw = 0
    dropped_po = 0

    for root in blocks[:GUITARGUITAR_MAX_PARSE]:
        try:
            raw = _root_to_raw(root)
            if raw is None:
                continue
            parsed_ok += 1
            title = str(raw.get("title") or "")
            # 预购列表 URL 已带 ``Query=``，站点侧已按关键词筛过；再对标题做子串匹配会误丢
            # （例如品牌只在 URL / 副标题里、标题简写为型号名）。
            if not from_pre_owned_channel and not _guitarguitar_title_matches_tokens(
                title, tokens
            ):
                dropped_kw += 1
                continue
            if not _guitarguitar_card_is_pre_owned(
                root,
                title,
                from_pre_owned_channel=from_pre_owned_channel,
            ):
                dropped_po += 1
                continue
            out.append(raw)
        except Exception:
            continue

    stats = {
        "anchors": len(blocks),
        "parsed_ok": parsed_ok,
        "dropped_kw": dropped_kw,
        "dropped_po": dropped_po,
    }

    if stats["anchors"] == 0 or stats["parsed_ok"] == 0:
        logger.warning(
            "[GuitarGuitar] parse produced no usable items (anchors=%s parsed=%s); "
            "first 3 <div> class attrs: %s",
            stats["anchors"],
            stats["parsed_ok"],
            _first_three_div_class_names(soup),
        )

    return out, stats


async def scrape_guitarguitar(keyword: str, page: int = 1) -> list[dict[str, Any]]:
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

    header_plan: tuple[tuple[str, dict[str, str]], ...] = (
        ("chrome", GUITARGUITAR_BROWSER_HEADERS),
        ("firefox_alt", GUITARGUITAR_BROWSER_HEADERS_ALT),
    )

    try:
        out: list[dict[str, Any]] = []
        last_html = ""
        last_stats: dict[str, int] = {}

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            for attempt, (hdr_label, hdrs) in enumerate(header_plan):
                if attempt > 0:
                    await asyncio.sleep(1.0)
                    logger.info(
                        "[GuitarGuitar] retry after empty parse (delay=1s hdr=%s)",
                        hdr_label,
                    )
                r = await client.get(url, headers=hdrs)
                r.raise_for_status()
                html = r.text or ""
                last_html = html
                out, stats = process_search_html(
                    html,
                    tokens,
                    from_pre_owned_channel=True,
                )
                last_stats = stats
                logger.info(
                    "[GuitarGuitar] attempt=%s hdr=%s keyword=%r page=%s kept=%s "
                    "anchors=%s parsed=%s dropped_kw=%s dropped_pre_owned=%s bytes=%s",
                    attempt + 1,
                    hdr_label,
                    q,
                    pg,
                    len(out),
                    stats["anchors"],
                    stats["parsed_ok"],
                    stats["dropped_kw"],
                    stats["dropped_po"],
                    len(html),
                )
                if out:
                    return out
                parse_failed = stats["anchors"] == 0 or stats["parsed_ok"] == 0
                if not parse_failed:
                    break
                if attempt + 1 >= len(header_plan):
                    break

        if not out and last_html:
            parse_failed = (
                last_stats.get("anchors", 0) == 0
                or last_stats.get("parsed_ok", 0) == 0
            )
            if parse_failed and len(last_html) > 500:
                snip = _guitarguitar_html_snippet(last_html)
                anti = _guitarguitar_html_antibot_signals(last_html)
                logger.warning(
                    "[GuitarGuitar] parse yielded no usable cards; HTML prefix (500 chars): "
                    "%r | antibot_signals=%s | last_stats=%s | bytes=%s",
                    snip,
                    anti,
                    last_stats,
                    len(last_html),
                )
            elif parse_failed:
                logger.warning(
                    "[GuitarGuitar] parse yielded no usable cards; short HTML (%s bytes) "
                    "stats=%s",
                    len(last_html),
                    last_stats,
                )
            else:
                logger.info(
                    "[GuitarGuitar] zero kept after filters (HTML parsed OK) stats=%s",
                    last_stats,
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
