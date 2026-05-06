"""
Reverb API 客户端（Personal Access Token）。

文档：https://www.reverb-api.com/docs/
"""

from __future__ import annotations

from typing import Any

import httpx

REVERB_API_ROOT = "https://api.reverb.com"
REVERB_WEB_ORIGIN = "https://reverb.com"

DEFAULT_HEADERS = {
    "Accept": "application/hal+json",
    "Accept-Version": "3.0",
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


def search_reverb_listings_sync(
    access_token: str,
    query: str,
    *,
    page: int = 1,
    per_page: int = 24,
) -> list[dict[str, Any]]:
    """
    ``GET /api/listings/all``，返回原始 ``listings`` 数组（每项为 HAL 对象）。
    """
    url = f"{REVERB_API_ROOT}/api/listings/all"
    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {access_token}",
    }
    params = {"query": query.strip(), "page": page, "per_page": per_page}

    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()

    return list(data.get("listings") or [])


async def search_reverb_listings_async(
    access_token: str,
    query: str,
    *,
    page: int = 1,
    per_page: int = 24,
) -> list[dict[str, Any]]:
    """异步版本，供 FastAPI 路由使用。"""
    url = f"{REVERB_API_ROOT}/api/listings/all"
    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {access_token}",
    }
    params = {"query": query.strip(), "page": page, "per_page": per_page}

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()

    return list(data.get("listings") or [])


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
