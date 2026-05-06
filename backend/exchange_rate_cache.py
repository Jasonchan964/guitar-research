"""
USD/CNY：ExchangeRate-API v6 pair + 进程内内存缓存（默认 1 小时）。

文档：https://www.exchangerate-api.com/docs/pair-conversion-requests
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from fastapi import HTTPException

CACHE_TTL_SEC = 3600

PAIR_URL_TEMPLATE = "https://v6.exchangerate-api.com/v6/{api_key}/pair/USD/CNY"

_lock = asyncio.Lock()
_cache: dict[str, Any] = {"rate": None, "expires_at": 0.0}


async def get_usd_cny_rate_cached() -> float:
    """返回 1 USD 折合多少 CNY；同一进程内 1 小时内复用缓存。"""
    async with _lock:
        now = time.monotonic()
        expires_at = float(_cache["expires_at"])
        if _cache["rate"] is not None and now < expires_at:
            return float(_cache["rate"])

        api_key = os.environ.get("EXCHANGE_RATE_API_KEY", "").strip()
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="未配置 EXCHANGE_RATE_API_KEY，请在 backend/.env 中设置",
            )

        url = PAIR_URL_TEMPLATE.format(api_key=api_key)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response else ""
            raise HTTPException(
                status_code=502,
                detail=f"汇率 HTTP {e.response.status_code if e.response else '?'}: {body}",
            ) from e

        data = resp.json()
        if data.get("result") == "error":
            err_type = data.get("error-type", "unknown")
            raise HTTPException(
                status_code=502,
                detail=f"汇率 API 错误: {err_type}",
            )

        try:
            rate = float(data["conversion_rate"])
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(
                status_code=502,
                detail=f"汇率响应缺少 conversion_rate: {e}",
            ) from e

        _cache["rate"] = rate
        _cache["expires_at"] = now + CACHE_TTL_SEC
        return rate
