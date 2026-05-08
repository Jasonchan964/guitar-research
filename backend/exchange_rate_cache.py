"""
USD/CNY：ExchangeRate-API v6 pair + 进程内内存缓存（默认 1 小时）。

未配置密钥或上游失败时返回静态备选汇率，避免 ``GET /api/exchange-rate`` 503 拖垮前端。

文档：https://www.exchangerate-api.com/docs/pair-conversion-requests
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SEC = 3600

PAIR_URL_TEMPLATE = "https://v6.exchangerate-api.com/v6/{api_key}/pair/USD/CNY"

_lock = asyncio.Lock()
_cache: dict[str, Any] = {"rate": None, "expires_at": 0.0}

FALLBACK_USD_CNY = float(os.getenv("FALLBACK_USD_CNY", "7.2"))


async def get_usd_cny_rate_cached() -> float:
    """返回 1 USD 折合多少 CNY；同一进程内 1 小时内复用缓存；失败时永不抛错。"""
    async with _lock:
        now = time.monotonic()
        expires_at = float(_cache["expires_at"])
        if _cache["rate"] is not None and now < expires_at:
            return float(_cache["rate"])

        api_key = os.environ.get("EXCHANGE_RATE_API_KEY", "").strip()
        if not api_key:
            logger.warning(
                "EXCHANGE_RATE_API_KEY unset; using fallback USD/CNY=%s",
                FALLBACK_USD_CNY,
            )
            return FALLBACK_USD_CNY

        url = PAIR_URL_TEMPLATE.format(api_key=api_key)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("result") == "error":
                err_type = data.get("error-type", "unknown")
                raise RuntimeError(f"ExchangeRate-API error-type={err_type}")
            rate = float(data["conversion_rate"])
        except Exception as e:
            logger.warning(
                "ExchangeRate-API USD/CNY failed (%s); using fallback %s",
                e,
                FALLBACK_USD_CNY,
            )
            return FALLBACK_USD_CNY

        _cache["rate"] = rate
        _cache["expires_at"] = now + CACHE_TTL_SEC
        return rate
