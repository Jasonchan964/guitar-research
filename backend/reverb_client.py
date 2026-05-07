"""
Reverb API 客户端（Personal Access Token）。

文档：https://www.reverb-api.com/docs/
"""

from __future__ import annotations

from typing import Any

import httpx

REVERB_API_ROOT = "https://api.reverb.com"
# 市场搜索列表（与官方分页文档一致）：``GET /api/listings?page=…``
REVERB_LISTINGS_SEARCH_URL = f"{REVERB_API_ROOT}/api/listings"
REVERB_WEB_ORIGIN = "https://reverb.com"


def reverb_request_headers(access_token: str) -> dict[str, str]:
    """官方 PAT 请求头：Bearer + v2 Accept（勿与浏览器 UA / hal+json 混用导致行为异常）。"""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.reverb.v2+json",
        "Content-Type": "application/json",
    }


def _abs_href(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"{REVERB_WEB_ORIGIN}{href}"
    return href


def extract_listing_web_url(listing: dict[str, Any]) -> str:
    """优先使用 HAL `_links` 中的前台链接；否则尝试 `slug` 拼商品页。"""
    links = listing.get("_links") or {}
    for key in ("web", "permalink", "public", "html"):
        block = links.get(key)
        if isinstance(block, dict):
            href = block.get("href") or ""
            if href:
                return _abs_href(href)

    slug = listing.get("slug")
    if isinstance(slug, str) and slug.strip():
        return f"{REVERB_WEB_ORIGIN}/item/{slug.strip()}"

    self_link = links.get("self")
    if isinstance(self_link, dict):
        href = self_link.get("href") or ""
        if href:
            return _abs_href(href)
    return ""


def extract_first_photo_url(listing: dict[str, Any]) -> str | None:
    photos = listing.get("photos")
    if not photos or not isinstance(photos, list):
        return None
    p0 = photos[0]
    if isinstance(p0, str):
        return p0 if p0.startswith("http") else _abs_href(p0)
    if not isinstance(p0, dict):
        return None
    if p0.get("url"):
        u = str(p0["url"])
        return u if u.startswith("http") else _abs_href(u)
    plinks = p0.get("_links") or {}
    for key in ("large_crop", "medium_crop", "thumbnail", "full", "small_crop"):
        block = plinks.get(key)
        if isinstance(block, dict):
            href = block.get("href") or ""
            if href:
                return _abs_href(href)
    return None


def format_price(listing: dict[str, Any]) -> str:
    price_obj = listing.get("price") or {}
    if isinstance(price_obj, dict):
        amount = price_obj.get("amount")
        currency = (
            price_obj.get("currency")
            or price_obj.get("currency_iso")
            or ""
        )
        if amount is not None:
            return f"{amount} {currency}".strip()
        return str(price_obj)
    return str(price_obj)


def listing_to_search_item(listing: dict[str, Any]) -> dict[str, Any]:
    """转为前端需要的扁平字段。"""
    title = listing.get("title") or listing.get("name") or ""
    return {
        "title": title,
        "imageUrl": extract_first_photo_url(listing),
        "price": format_price(listing),
        "url": extract_listing_web_url(listing),
    }


def _reverb_listings_query_params(
    query: str,
    *,
    page: int,
    per_page: int,
    condition: str = "all",
) -> list[tuple[str, str | int]]:
    """
    查询参数：``page``、``query``、``per_page``；成色为 ``new``/``used`` 时追加 ``conditions[]``。
    使用元组列表以便稳定编码 ``conditions[]=…``。
    """
    q = query.strip()
    cond = (condition or "all").strip().lower()
    pairs: list[tuple[str, str | int]] = [
        ("query", q),
        ("page", max(1, int(page))),
        ("per_page", int(per_page)),
    ]
    if cond == "new":
        pairs.append(("conditions[]", "new"))
    elif cond == "used":
        pairs.append(("conditions[]", "used"))
    return pairs


def search_reverb_listings_sync(
    access_token: str,
    query: str,
    *,
    page: int = 1,
    per_page: int = 24,
    condition: str = "all",
) -> list[dict[str, Any]]:
    """
    ``GET https://api.reverb.com/api/listings``，返回原始 ``listings`` 数组（每项为 HAL 风格对象）。
    """
    headers = reverb_request_headers(access_token)
    params = _reverb_listings_query_params(
        query, page=page, per_page=per_page, condition=condition
    )

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        r = client.get(REVERB_LISTINGS_SEARCH_URL, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()

    return _extract_listings_from_reverb_payload(data)


async def search_reverb_listings_async(
    access_token: str,
    query: str,
    *,
    page: int = 1,
    per_page: int = 24,
    condition: str = "all",
) -> list[dict[str, Any]]:
    """异步版本，供 FastAPI 路由使用。"""
    headers = reverb_request_headers(access_token)
    params = _reverb_listings_query_params(
        query, page=page, per_page=per_page, condition=condition
    )

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(REVERB_LISTINGS_SEARCH_URL, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()

    return _extract_listings_from_reverb_payload(data)


def _extract_listings_from_reverb_payload(data: Any) -> list[dict[str, Any]]:
    """
    Reverb 搜索接口历史上曾直接返回 ``listings``；部分环境/HAL 版本下在 ``_embedded.listings``。
    """
    if not isinstance(data, dict):
        return []
    raw = data.get("listings")
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    emb = data.get("_embedded")
    if isinstance(emb, dict):
        inner = emb.get("listings")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


def fetch_first_listing_title_and_price(
    access_token: str,
    query: str = "Fender Mustang",
) -> tuple[str, str]:
    """连通性测试：取第一条的标题与价格文案。"""
    listings = search_reverb_listings_sync(access_token, query, per_page=5)
    if not listings:
        raise RuntimeError("响应中没有 listings")
    first = listings[0]
    title = first.get("title") or first.get("name") or "(无标题)"
    return title, format_price(first)
