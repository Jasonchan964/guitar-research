"""
站内商品详情：按平台深度抓取单页，供 ``GET /api/guitar/detail`` 使用。

Reverb：使用官方 ``GET /api/listings/{id_or_slug}``（``REVERB_API_TOKEN``），不抓取前台 HTML，避免 Cloudflare 403。

仅允许已知电商域名，避免 SSRF。
"""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from reverb_client import (
    extract_all_listing_photo_urls,
    extract_listing_web_url,
    hal_listing_price_amount_currency,
    reverb_request_headers,
    reverb_single_listing_api_url,
)
from bs4 import BeautifulSoup
from bs4.element import Tag
from fastapi import HTTPException

logger = logging.getLogger(__name__)

DIGIMART_ORIGIN = "https://www.digimart.net"

DETAIL_ALLOWED_HOSTS: dict[str, tuple[str, ...]] = {
    "ishibashi": ("intl.ishibashi.co.jp",),
    "sweelee": ("www.sweelee.com.sg", "sweelee.com.sg"),
    "digimart": ("www.digimart.net", "digimart.net"),
    "reverb": ("reverb.com", "www.reverb.com"),
    "guitarguitar": ("www.guitarguitar.co.uk", "guitarguitar.co.uk"),
}

PLATFORM_DISPLAY: dict[str, str] = {
    "ishibashi": "Ishibashi",
    "sweelee": "Swee Lee",
    "digimart": "Digimart",
    "reverb": "Reverb",
    "guitarguitar": "GuitarGuitar",
}

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
}


def _normalize_detail_platform_key(raw: str) -> str | None:
    s = re.sub(r"[\s_-]+", "", (raw or "").strip().lower())
    if not s:
        return None
    aliases = {
        "ishibashi": "ishibashi",
        "sweelee": "sweelee",
        "digimart": "digimart",
        "reverb": "reverb",
        "guitarguitar": "guitarguitar",
        "guitarguitaruk": "guitarguitar",
        "gg": "guitarguitar",
    }
    return aliases.get(s)


def _validate_url_for_platform(page_url: str, plat: str) -> None:
    try:
        p = urlparse(page_url.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无效 URL: {e}") from e
    if p.scheme not in ("http", "https") or not p.netloc:
        raise HTTPException(status_code=400, detail="仅支持 http(s) 且需包含主机名")
    host = p.netloc.split("@")[-1].lower()
    if ":" in host:
        host = host.split(":")[0]
    allowed = DETAIL_ALLOWED_HOSTS.get(plat, ())
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise HTTPException(
            status_code=400,
            detail=f"URL 主机与平台 {PLATFORM_DISPLAY.get(plat, plat)} 不匹配",
        )


def _upgrade_shopify_image_src(src: str) -> str:
    u = (src or "").strip()
    if not u:
        return u
    out = u
    for suf in ("_small", "_medium", "_compact"):
        if suf in out:
            out = out.replace(suf, "_1024x1024")
            break
    return out


def _product_url_to_shopify_json_url(page_url: str) -> str:
    p = urlparse(page_url.strip())
    path = (p.path or "").rstrip("/")
    if path.endswith(".json"):
        json_path = path
    else:
        json_path = f"{path}.json"
    scheme = p.scheme or "https"
    return urlunparse((scheme, p.netloc, json_path, "", p.query, ""))


def _shopify_iso_currency_from_variant(v: dict[str, Any]) -> str:
    for key in ("currency", "price_currency"):
        c = str(v.get(key) or "").strip().upper()
        if len(c) >= 3 and c[:3].isalpha():
            return c[:3]
    pp = v.get("presentment_prices")
    if isinstance(pp, dict):
        for sub in ("shop_money", "presentment_money"):
            sm = pp.get(sub)
            if isinstance(sm, dict):
                c2 = sm.get("currency_code") or sm.get("currencyCode")
                c = str(c2 or "").strip().upper()
                if len(c) >= 3 and c[:3].isalpha():
                    return c[:3]
    return ""


def _shopify_price_and_currency(product: dict[str, Any]) -> tuple[float | None, str]:
    variants = product.get("variants")
    if isinstance(variants, list) and variants:
        v0 = variants[0]
        if isinstance(v0, dict) and v0.get("price") is not None:
            try:
                amt = float(v0["price"])
                if amt > 0:
                    cur = _shopify_iso_currency_from_variant(v0) or "JPY"
                    return amt, cur
            except (TypeError, ValueError):
                pass
    return None, ""


def _shopify_images(product: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for im in product.get("images") or []:
        if isinstance(im, dict):
            u = (im.get("src") or "").strip()
            if u:
                out.append(_upgrade_shopify_image_src(u))
        elif isinstance(im, str) and im.strip():
            out.append(_upgrade_shopify_image_src(im.strip()))
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def _digimart_is_excluded_chrome(tag: Tag) -> bool:
    """侧栏、推荐、近期浏览、购物车等区域内的节点，不参与主商品价格解析。"""
    p: Tag | Any = tag
    noise_class = re.compile(
        r"(sidebar|side-bar|subNav|recommend|recent|history|ranking|foot|banner|"
        r"cart|mini-cart|pickup|relation|related|also|viewed|popup)",
        re.I,
    )
    noise_id = re.compile(
        r"(sidebar|footer|header|cart|recommend|recent|history|ranking|banner|pickup)",
        re.I,
    )
    while p is not None:
        name = (p.name or "").lower()
        if name in ("aside", "footer", "nav"):
            return True
        pid = " ".join((p.get("id") or "").lower().split())
        if pid and noise_id.search(pid):
            return True
        classes = " ".join(p.get("class") or [])
        if classes and noise_class.search(classes):
            return True
        role = (p.get("role") or "").lower()
        if role in ("complementary", "navigation"):
            return True
        p = p.parent
    return False


def _digimart_main_product_root(soup: BeautifulSoup) -> Tag | None:
    """
    详情页【主商品】所在子树：含标题与标价，不包含侧栏/推荐等。
    优先从 ``h1`` 向上找第一个「子树内出现主标价块」的祖先（侧栏常在 DOM 前部时也能避开误匹配）。
    """
    price_hints = (
        ".itemPrice",
        "#itemPrice",
        ".priceArea",
        "[class*='itemPrice']",
        'meta[itemprop="price"]',
    )
    h1 = soup.select_one("h1")
    if h1 is not None and not _digimart_is_excluded_chrome(h1):
        node: Tag | Any = h1
        for _ in range(22):
            if node is None or not getattr(node, "name", None):
                break
            for sel in price_hints:
                hit = node.select_one(sel)
                if hit is not None and not _digimart_is_excluded_chrome(hit):
                    return node
            node = node.parent

    layout_selectors = (
        'article[itemtype*="Product"]',
        "article[itemscope]",
        "main#main",
        "main",
        "#mainContents",
        "#contentsInner",
        "#contents",
        "div.item-detail",
        "div.itemDetail",
        "#itemDetail",
        "div[class*='item-detail']",
        "div[class*='itemDetail']",
        "section.product-main",
        ".productMain",
    )
    for sel in layout_selectors:
        el = soup.select_one(sel)
        if el is not None and not _digimart_is_excluded_chrome(el):
            return el
    return None


def _digimart_price_from_json_ld(soup: BeautifulSoup) -> int | None:
    """整页级 Product JSON-LD 的标价（与主商品一致时优先于 DOM，避免侧栏干扰）。"""
    ld = _collect_json_ld_product(soup)
    if not ld:
        return None
    amt, cur = _ld_product_price_currency(ld)
    if amt is None:
        return None
    c = (cur or "JPY").strip().upper()
    c3 = c[:3] if len(c) >= 3 else ""
    if c3 and c3 != "JPY":
        return None
    try:
        n = int(round(float(amt)))
    except (TypeError, ValueError):
        return None
    return n if n >= 100 else None


def _digimart_parse_price_in_scope(scope: Tag | BeautifulSoup) -> int | None:
    """仅在给定子树内解析日元整数（不做整文档搜索）。"""
    meta = scope.select_one('meta[itemprop="price"]')
    if meta is not None and meta.get("content"):
        v = _parse_jpy_int(str(meta["content"]).strip())
        if v is not None:
            return v
    for sel in (
        ".itemPrice",
        "#itemPrice",
        ".priceArea",
        "[class*='itemPrice']",
        "p.itemPrice",
        ".item-price",
        ".main-price",
        "p.price",
        ".price",
    ):
        el = scope.select_one(sel)
        if el is None:
            continue
        if _digimart_is_excluded_chrome(el):
            continue
        v = _parse_jpy_int(el.get_text(" ", strip=True))
        if v is not None:
            return v
    return None


def _digimart_parse_price_fallback_global(soup: BeautifulSoup) -> int | None:
    """找不到主容器时：遍历候选 selector，跳过侧栏/推荐等区域内的匹配。"""
    for sel in (
        ".itemPrice",
        "#itemPrice",
        ".priceArea",
        "[class*='itemPrice']",
        "p.itemPrice",
        ".item-price",
        "p.price",
        ".price",
    ):
        for el in soup.select(sel):
            if _digimart_is_excluded_chrome(el):
                continue
            v = _parse_jpy_int(el.get_text(" ", strip=True))
            if v is not None:
                return v
    meta = soup.select_one('meta[itemprop="price"]')
    if meta is not None and meta.get("content") and not _digimart_is_excluded_chrome(meta):
        v = _parse_jpy_int(str(meta["content"]).strip())
        if v is not None:
            return v
    return None


def _digimart_abs(href: str) -> str:
    s = (href or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("/"):
        return f"{DIGIMART_ORIGIN}{s}"
    return f"{DIGIMART_ORIGIN}/{s}"


def _parse_jpy_int(text: str) -> int | None:
    import main as app_main

    return app_main._parse_jpy_amount(text or "")


def _digimart_collect_images(soup: BeautifulSoup) -> list[str]:
    """优先在主商品区域内收集图，避免侧栏推荐商品缩略图。"""
    selectors = (
        "#photoArea img",
        ".itemPhoto img",
        ".itemMainPhoto img",
        "div[class*='itemPhoto'] img",
        "div[class*='PhotoArea'] img",
        "ul.thumbList img",
        "ul[class*='thumb'] img",
        ".itemImage img",
    )

    def collect_from(base: BeautifulSoup | Tag) -> list[str]:
        import main as app_main

        out: list[str] = []
        seen_sel: set[str] = set()
        for sel in selectors:
            for img in base.select(sel):
                if _digimart_is_excluded_chrome(img):
                    continue
                raw = (
                    (img.get("data-src") or "").strip()
                    or (img.get("data-original") or "").strip()
                    or (img.get("src") or "").strip()
                )
                if not raw or "spacer" in raw.casefold() or "blank" in raw.casefold():
                    continue
                u = app_main.get_hd_image_url(_digimart_abs(raw))
                if u and "logo" not in u.casefold() and "icon" not in u.casefold():
                    if u not in seen_sel:
                        seen_sel.add(u)
                        out.append(u)
        return out

    main_root = _digimart_main_product_root(soup)
    urls = collect_from(main_root) if main_root is not None else []
    if not urls:
        urls = collect_from(soup)
    if not urls:
        import main as app_main

        og = soup.select_one('meta[property="og:image"]')
        if og and og.get("content"):
            u = app_main.get_hd_image_url(_digimart_abs(str(og["content"]).strip()))
            if u:
                urls.append(u)
    return urls


def _digimart_description_and_specs(soup: BeautifulSoup) -> tuple[str, dict[str, str]]:
    specs: dict[str, str] = {}
    html_parts: list[str] = []

    detail_root = (
        _digimart_main_product_root(soup)
        or soup.select_one("div.item-detail")
        or soup.select_one("div.itemDetail")
        or soup.select_one("#itemDetail")
        or soup.select_one("div[class*='itemDetail']")
    )
    if detail_root is not None:
        html_parts.append(str(detail_root))

    for h2 in soup.find_all(["h2", "h3"]):
        t = h2.get_text(" ", strip=True)
        if "商品の詳細" in t or "商品詳細" in t:
            sib = h2.find_next_sibling()
            if sib is not None:
                html_parts.append(str(sib))
            for li in h2.find_all_next("li", limit=40):
                if li.find_parent("div", class_=re.compile(r"shop|footer|related", re.I)):
                    break
                txt = li.get_text(" ", strip=True)
                if "：" in txt:
                    k, v = txt.split("：", 1)
                    k, v = k.strip(), v.strip()
                    if k and v and len(k) < 40:
                        specs[k] = v
            break

    if not specs:
        for row in soup.select("table tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                k = cells[0].get_text(" ", strip=True)
                v = cells[1].get_text(" ", strip=True)
                if k and v and len(k) < 50:
                    specs[k] = v

    desc_html = ""
    if html_parts:
        desc_html = f'<div class="digimart-detail">{" ".join(html_parts)}</div>'
    else:
        comment = soup.select_one("#itemComment, .itemComment, div[class*='Comment']")
        if comment is not None:
            desc_html = f'<div class="digimart-detail">{str(comment)}</div>'

    return desc_html, specs


def _digimart_title_price(soup: BeautifulSoup, page_url: str) -> tuple[str, int | None]:
    """
    标题与价格只在主商品区域内解析；价格优先 JSON-LD（Product），再主容器内 DOM。
    禁止整页首个 ``.price``（易与侧栏/推荐/购物车重复）直接命中。
    """
    main_scope = _digimart_main_product_root(soup)
    title_scope: Tag | BeautifulSoup = (
        main_scope if main_scope is not None else soup
    )

    title = ""
    h1_in_scope = title_scope.select_one("h1")
    if h1_in_scope is not None and not _digimart_is_excluded_chrome(h1_in_scope):
        title = re.sub(r"\s+", " ", h1_in_scope.get_text(" ", strip=True))
    if not title:
        og_t = soup.select_one('meta[property="og:title"]')
        if og_t and og_t.get("content"):
            title = re.sub(r"\s+", " ", str(og_t["content"]).strip())
    if not title:
        h1 = soup.select_one("h1")
        if h1 is not None:
            title = re.sub(r"\s+", " ", h1.get_text(" ", strip=True))

    jpy: int | None = _digimart_price_from_json_ld(soup)
    if jpy is None and main_scope is not None:
        jpy = _digimart_parse_price_in_scope(main_scope)
    if jpy is None:
        # 未识别主容器时禁止使用整页 ``select_one``（DOM 序常在侧栏）；仅遍历候选并跳过噪音区
        jpy = _digimart_parse_price_fallback_global(soup)

    return title, jpy


def _digimart_condition(soup: BeautifulSoup) -> str:
    import main as app_main

    main_scope = _digimart_main_product_root(soup)
    scope: Tag | BeautifulSoup = main_scope if main_scope is not None else soup

    chunks: list[str] = []
    for sel in (".itemState", ".itemTags", "h1", ".itemDetail", "#itemDetail"):
        el = scope.select_one(sel)
        if el is not None:
            chunks.append(el.get_text(" ", strip=True))
    blob = " ".join(chunks)
    return app_main._classify_new_vs_used_from_text(blob)


def _collect_json_ld_product(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            types = t if isinstance(t, list) else [t] if t else []
            if "Product" in types or item.get("@type") == "Product":
                return item
    return None


def _ld_product_images(prod: dict[str, Any]) -> list[str]:
    raw = prod.get("image")
    out: list[str] = []
    if isinstance(raw, str) and raw.strip():
        out.append(raw.strip())
    elif isinstance(raw, list):
        for x in raw:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
            elif isinstance(x, dict):
                u = (x.get("url") or x.get("contentUrl") or "").strip()
                if u:
                    out.append(u)
    seen: set[str] = set()
    deduped: list[str] = []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _ld_product_price_currency(prod: dict[str, Any]) -> tuple[float | None, str | None]:
    offers = prod.get("offers")
    block: Any = offers
    if isinstance(offers, list) and offers:
        block = offers[0]
    if not isinstance(block, dict):
        return None, None
    price_raw = block.get("price") or block.get("lowPrice") or block.get("highPrice")
    cur = block.get("priceCurrency") or block.get("pricecurrency")
    if price_raw is None:
        return None, str(cur).strip().upper() if cur else None
    try:
        amt = float(Decimal(str(price_raw)))
    except (InvalidOperation, ValueError, TypeError):
        return None, str(cur).strip().upper() if cur else None
    if amt <= 0:
        return None, str(cur).strip().upper() if cur else None
    c = str(cur or "").strip().upper()
    return amt, c[:3] if len(c) >= 3 else None


def _html_meta_og_image(soup: BeautifulSoup) -> str | None:
    og = soup.select_one('meta[property="og:image"]')
    if og and og.get("content"):
        return str(og["content"]).strip()
    return None


def _description_from_ld_or_meta(soup: BeautifulSoup, ld: dict[str, Any] | None) -> str:
    if ld:
        d = ld.get("description")
        if isinstance(d, str) and d.strip():
            return f'<div class="scraped-desc"><p>{_html_escape(d.strip())}</p></div>'
    meta = soup.select_one('meta[property="og:description"], meta[name="description"]')
    if meta and meta.get("content"):
        t = str(meta["content"]).strip()
        if t:
            return f'<div class="scraped-desc"><p>{_html_escape(t)}</p></div>'
    return ""


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _reverb_specs_from_ld(ld: dict[str, Any] | None) -> dict[str, str]:
    specs: dict[str, str] = {}
    if not ld:
        return specs
    brand = ld.get("brand")
    if isinstance(brand, dict):
        n = brand.get("name")
        if isinstance(n, str) and n.strip():
            specs["品牌"] = n.strip()
    elif isinstance(brand, str) and brand.strip():
        specs["品牌"] = brand.strip()
    sku = ld.get("sku")
    if isinstance(sku, str) and sku.strip():
        specs["SKU"] = sku.strip()
    mpn = ld.get("mpn")
    if isinstance(mpn, str) and mpn.strip():
        specs["型号"] = mpn.strip()
    return specs


def _guitarguitar_gallery(soup: BeautifulSoup, origin: str) -> list[str]:
    from urllib.parse import urljoin

    urls: list[str] = []
    for img in soup.select(".product-detail img, .product-gallery img, [data-product-image]"):
        raw = (
            (img.get("data-src") or "").strip()
            or (img.get("data-large") or "").strip()
            or (img.get("src") or "").strip()
        )
        if raw and "blank" not in raw.casefold():
            urls.append(urljoin(origin, raw))
    if not urls:
        u = _html_meta_og_image(soup)
        if u:
            urls.append(urljoin(origin, u))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _condition_from_description_text(desc: str) -> str:
    import main as app_main

    return app_main._classify_new_vs_used_from_text(desc)


async def _amount_to_cny(client: httpx.AsyncClient, amount: float, currency: str) -> float | None:
    import main as app_main

    cur = (currency or "USD").strip().upper()[:3]
    if not cur:
        cur = "USD"
    if cur == "CNY":
        return float(amount)
    try:
        rates = await app_main.get_rates_to_cny(client, {cur})
    except Exception as e:
        logger.warning("[guitar/detail] rate fetch failed: %s", e)
        return None
    if cur in rates:
        return float(amount) * rates[cur]
    return None


def _format_price_original(amount: float, currency: str) -> str:
    cur = (currency or "").strip().upper()
    if cur == "JPY":
        return f"{int(round(amount))} JPY"
    if cur in ("USD", "GBP", "SGD", "EUR"):
        return f"{amount:.2f} {cur}"
    return f"{amount} {cur}"


async def _fetch_shopify_detail(client: httpx.AsyncClient, page_url: str, plat: str) -> dict[str, Any]:
    json_url = _product_url_to_shopify_json_url(page_url)
    headers = (
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*;q=0.1",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if plat == "ishibashi"
        else {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*;q=0.1",
            "Accept-Language": "en-SG,en-US;q=0.9,en;q=0.8",
        }
    )
    r = await client.get(json_url, headers=headers, follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    product = data.get("product") if isinstance(data, dict) else None
    if not isinstance(product, dict):
        raise HTTPException(status_code=502, detail="Shopify JSON 无 product 字段")
    title = str(product.get("title") or "").strip()
    body_html = str(product.get("body_html") or "")
    images = _shopify_images(product)
    amt, cur = _shopify_price_and_currency(product)
    if not cur:
        cur = "JPY" if plat == "ishibashi" else "SGD"
    price_cny = None
    if amt is not None:
        price_cny = await _amount_to_cny(client, amt, cur)
    price_original = _format_price_original(amt, cur) if amt is not None else ""

    import main as app_main

    cond = app_main._ishibashi_condition_from_product(product)
    specs: dict[str, str] = {}
    for key in ("vendor", "product_type", "type"):
        v = product.get(key)
        if isinstance(v, str) and v.strip():
            label = {"vendor": "品牌", "product_type": "类型", "type": "类型"}.get(key, key)
            specs[label] = v.strip()

    return {
        "title": title or "商品",
        "price_cny": round(price_cny, 2) if price_cny is not None else None,
        "price_original": price_original,
        "platform": PLATFORM_DISPLAY[plat],
        "condition": cond,
        "images": images,
        "specs": specs,
        "description_html": body_html,
        "buy_url": page_url.strip(),
    }


async def _fetch_digimart_detail(client: httpx.AsyncClient, page_url: str) -> dict[str, Any]:
    r = await client.get(page_url.strip(), headers=DIGIMART_BROWSER_HEADERS, follow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title, jpy = _digimart_title_price(soup, page_url)
    images = _digimart_collect_images(soup)
    desc_html, specs = _digimart_description_and_specs(soup)
    cond = _digimart_condition(soup)
    price_cny = None
    price_original = ""
    if jpy is not None:
        price_original = f"{jpy} JPY"
        price_cny = await _amount_to_cny(client, float(jpy), "JPY")
    return {
        "title": title or "商品",
        "price_cny": round(price_cny, 2) if price_cny is not None else None,
        "price_original": price_original,
        "platform": PLATFORM_DISPLAY["digimart"],
        "condition": cond,
        "images": images,
        "specs": specs,
        "description_html": desc_html,
        "buy_url": page_url.strip(),
    }


def _parse_reverb_listing_key_from_url(page_url: str) -> str | None:
    """从 ``…/item/{id-or-slug}`` 或 ``…/listings/show/{id}`` 解析官方详情 API 路径参数。"""
    try:
        p = urlparse(page_url.strip())
    except Exception:
        return None
    path = p.path or ""
    m = re.search(r"/item/([^/?#]+)", path)
    if m:
        k = m.group(1).strip()
        return k or None
    m = re.search(r"/listings/show/([^/?#]+)", path)
    if m:
        k = m.group(1).strip()
        return k or None
    return None


def _reverb_api_specs_to_plain_dict(raw: Any) -> dict[str, str]:
    """详情 JSON ``specs`` → 扁平 ``dict[str, str]``。"""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        ks = str(k).strip()
        if not ks:
            continue
        if isinstance(v, (dict, list)):
            try:
                out[ks] = json.dumps(v, ensure_ascii=False)
            except TypeError:
                out[ks] = str(v)
        else:
            out[ks] = str(v).strip()
    return out


def _reverb_api_description_html(desc_raw: Any) -> str:
    if desc_raw is None:
        return ""
    s = desc_raw if isinstance(desc_raw, str) else str(desc_raw)
    s = s.strip()
    if not s:
        return ""
    if "<" in s and ">" in s:
        return f'<div class="reverb-api-desc">{s}</div>'
    return f'<div class="reverb-api-desc"><p>{_html_escape(s)}</p></div>'


async def _fetch_reverb_detail(client: httpx.AsyncClient, page_url: str) -> dict[str, Any]:
    """
    Reverb：仅调用 ``GET /api/listings/{id_or_slug}``，不使用浏览器 HTML（避免 Cloudflare 403）。
    """
    import main as app_main

    token = (os.environ.get("REVERB_API_TOKEN") or "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="未配置 REVERB_API_TOKEN，无法通过官方 API 获取 Reverb 详情",
        )

    key = _parse_reverb_listing_key_from_url(page_url)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="无法从 URL 解析 Reverb 商品 id/slug（需为 …/item/{标识} 等形式）",
        )

    api_url = reverb_single_listing_api_url(key)
    if not api_url:
        raise HTTPException(status_code=400, detail="无效的 Reverb 商品标识")

    headers = reverb_request_headers(token)
    r = await client.get(api_url, headers=headers)
    if r.status_code != 200:
        snippet = (r.text or "")[:1500]
        logger.warning(
            "Reverb listing detail API: status=%s url=%s body=%s",
            r.status_code,
            api_url,
            snippet[:800],
        )
        raise HTTPException(
            status_code=502,
            detail=f"Reverb 详情 API 返回 {r.status_code}: {snippet[:600]}",
        )

    try:
        listing = r.json()
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Reverb 详情 API 非合法 JSON: {e}") from e

    if not isinstance(listing, dict):
        raise HTTPException(status_code=502, detail="Reverb 详情 API 返回格式异常")

    title = str(listing.get("title") or listing.get("name") or "").strip()
    images = extract_all_listing_photo_urls(listing)
    description_html = _reverb_api_description_html(listing.get("description", ""))
    specs = _reverb_api_specs_to_plain_dict(listing.get("specs"))

    amt, cur = hal_listing_price_amount_currency(listing)
    if amt is None:
        price_cny = None
        price_original = ""
    else:
        c = cur or "USD"
        price_original = _format_price_original(amt, c)
        price_cny = await _amount_to_cny(client, amt, c)

    cond = app_main._reverb_condition_cn(listing)
    buy = extract_listing_web_url(listing).strip() or page_url.strip()

    return {
        "title": title or "商品",
        "price_cny": round(price_cny, 2) if price_cny is not None else None,
        "price_original": price_original,
        "platform": PLATFORM_DISPLAY["reverb"],
        "condition": cond,
        "images": images,
        "specs": specs,
        "description_html": description_html,
        "buy_url": buy,
    }


async def _fetch_guitarguitar_detail(client: httpx.AsyncClient, page_url: str) -> dict[str, Any]:
    pu = page_url.strip()
    r = await client.get(pu, headers=GUITARGUITAR_BROWSER_HEADERS, follow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    parsed = urlparse(pu)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    ld = _collect_json_ld_product(soup)
    title = ""
    if ld and isinstance(ld.get("name"), str):
        title = ld["name"].strip()
    if not title:
        h1 = soup.select_one("h1")
        if h1 is not None:
            title = h1.get_text(" ", strip=True)
    images = _guitarguitar_gallery(soup, origin)
    if not images and ld:
        images = _ld_product_images(ld)
    amt, cur = _ld_product_price_currency(ld) if ld else (None, None)
    if amt is None:
        price_cny = None
        price_original = ""
    else:
        c = cur or "GBP"
        price_original = _format_price_original(amt, c)
        price_cny = await _amount_to_cny(client, amt, c)
    desc_html = _description_from_ld_or_meta(soup, ld)
    specs: dict[str, str] = _reverb_specs_from_ld(ld)
    for dl in soup.select("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd is not None:
                k = dt.get_text(" ", strip=True)
                v = dd.get_text(" ", strip=True)
                if k and v and len(k) < 60:
                    specs[k] = v
    cond = _condition_from_description_text(
        (ld.get("description") if ld else "") or desc_html or title or "",
    )
    return {
        "title": title or "商品",
        "price_cny": round(price_cny, 2) if price_cny is not None else None,
        "price_original": price_original,
        "platform": PLATFORM_DISPLAY["guitarguitar"],
        "condition": cond,
        "images": images,
        "specs": specs,
        "description_html": desc_html,
        "buy_url": pu,
    }


async def fetch_guitar_detail(page_url: str, platform: str) -> dict[str, Any]:
    plat = _normalize_detail_platform_key(platform)
    if plat is None:
        raise HTTPException(
            status_code=400,
            detail="未知 platform，请使用 Ishibashi / Swee Lee / Digimart / Reverb / GuitarGuitar",
        )
    _validate_url_for_platform(page_url, plat)

    timeout = httpx.Timeout(28.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            if plat in ("ishibashi", "sweelee"):
                return await _fetch_shopify_detail(client, page_url, plat)
            if plat == "digimart":
                return await _fetch_digimart_detail(client, page_url)
            if plat == "reverb":
                return await _fetch_reverb_detail(client, page_url)
            if plat == "guitarguitar":
                return await _fetch_guitarguitar_detail(client, page_url)
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=502,
                detail=f"抓取详情页 HTTP {e.response.status_code if e.response else '?'}",
            ) from e
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"网络请求失败: {e}") from e

    raise HTTPException(status_code=500, detail="内部错误")
