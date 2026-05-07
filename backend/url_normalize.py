"""收藏去重：将商品详情 URL 规范为稳定字符串（与搜索合并去重思路一致）。"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def normalize_original_url(url: str) -> str:
    """
    scheme/host 小写，path 去尾斜杠，保留 query（部分站点用查询区分商品）。
    """
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
