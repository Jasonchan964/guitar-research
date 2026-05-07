"""
Reverb API 客户端（Personal Access Token）。

文档：https://www.reverb-api.com/docs/
"""

from __future__ import annotations

from typing import Any

import httpx

REVERB_API_ROOT = "https://api.reverb.com"
# 官方标准列表搜索终结点（GET）：仅使用 ``query`` / ``page`` / ``per_page`` / ``conditions[]``
REVERB_LISTINGS_SEARCH_URL = f"{REVERB_API_ROOT}/api/listings"
REVERB_WEB_ORIGIN = "https://reverb.com"

# 与搜索合并接口约定一致：每页 24 条
REVERB_LISTINGS_PER_PAGE_DEFAULT = 24
REVERB_HTTP_TIMEOUT_SEC = 10.0


def reverb_request_headers(access_token: str) -> dict[str, str]:
    """官方 PAT 请求头：Bearer + v2 Accept + Accept-Version（API 要求，缺省会 400）。"""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.reverb.v2+json",
        "Accept-Version": "3.0",
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
    Reverb ``GET /api/listings`` 查询串（键名必须与官方一致）：

    - ``query``：搜索词（**不得**使用 ``q``）
    - ``page``：页码（从 1 起）
    - ``per_page``：每页条数（默认 24）
    - ``conditions[]``：仅当 ``condition`` 为 ``new`` / ``used`` 时追加；``all`` 不带任何 conditions

    使用 ``list[tuple]`` 以便稳定生成 ``conditions[]=…`` 数组形式。
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
    per_page: int = REVERB_LISTINGS_PER_PAGE_DEFAULT,
    condition: str = "all",
    request_headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    ``GET https://api.reverb.com/api/listings``，返回原始 ``listings`` 数组（每项为 HAL 风格对象）。
    """
    headers = (
        request_headers if request_headers is not None else reverb_request_headers(access_token)
    )
    params = _reverb_listings_query_params(
        query, page=page, per_page=per_page, condition=condition
    )

    try:
        with httpx.Client(
            timeout=REVERB_HTTP_TIMEOUT_SEC,
            follow_redirects=True,
        ) as client:
            response = client.get(
                REVERB_LISTINGS_SEARCH_URL,
                headers=headers,
                params=params,
            )
        if response.status_code != 200:
            print(
                f"❌ [Reverb API 错误] 状态码: {response.status_code} | 返回内容: {response.text}",
                flush=True,
            )
            return []
        try:
            data = response.json()
        except Exception as je:
            print(
                f"❌ [Reverb API 错误] HTTP 200 但 JSON 解析失败: {je} | 正文: {(response.text or '')[:1200]}",
                flush=True,
            )
            return []
    except Exception as e:
        print(f"💥 [Reverb 异常] 请求发生错误: {e}", flush=True)
        return []

    return _extract_listings_from_reverb_payload(data)


async def search_reverb_listings_async(
    access_token: str,
    query: str,
    *,
    page: int = 1,
    per_page: int = REVERB_LISTINGS_PER_PAGE_DEFAULT,
    condition: str = "all",
    request_headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """异步版本，供 FastAPI 路由使用。``request_headers`` 若传入则优先（须含 Bearer / Accept v2）。"""
    headers = (
        request_headers if request_headers is not None else reverb_request_headers(access_token)
    )
    params = _reverb_listings_query_params(
        query, page=page, per_page=per_page, condition=condition
    )

    try:
        async with httpx.AsyncClient(
            timeout=REVERB_HTTP_TIMEOUT_SEC,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                REVERB_LISTINGS_SEARCH_URL,
                headers=headers,
                params=params,
            )
        if response.status_code != 200:
            print(
                f"❌ [Reverb API 错误] 状态码: {response.status_code} | 返回内容: {response.text}",
                flush=True,
            )
            return []
        try:
            data = response.json()
        except Exception as je:
            print(
                f"❌ [Reverb API 错误] HTTP 200 但 JSON 解析失败: {je} | 正文: {(response.text or '')[:1200]}",
                flush=True,
            )
            return []
    except Exception as e:
        print(f"💥 [Reverb 异常] 请求发生错误: {e}", flush=True)
        return []

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
