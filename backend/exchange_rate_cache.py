"""
进程内汇率：``EXCHANGE_RATES`` 默认值 + 启动时从 Frankfurter 拉取更新。

搜索与详情只读内存字典，不发起汇率 HTTP。刷新失败（超时、503 等）保留上次成功值或默认值，仅 ``warning`` 日志。
文档：https://www.frankfurter.app/docs/
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Final

import httpx

logger = logging.getLogger(__name__)

FRANKFURTER: Final[str] = "https://api.frankfurter.dev/v1/latest"
FX_HTTP_TIMEOUT: Final[float] = 2.0

# 1 单位 ISO 货币 → CNY；启动成功后由 Frankfurter 覆盖对应键。
EXCHANGE_RATES: dict[str, float] = {
    "USD": 7.2,
    "GBP": 9.2,
    "JPY": 0.046,
    "SGD": 5.35,
}

REFRESH_ISO_CODES: Final[tuple[str, ...]] = ("USD", "GBP", "JPY", "SGD")


def fallback_rate_to_cny(iso: str) -> float:
    """未出现在 ``EXCHANGE_RATES`` 或键从未刷新成功时的静态/环境备选：1 单位 ``iso`` → CNY。"""
    u = (iso or "USD").strip().upper()[:3]
    if u == "CNY":
        return 1.0
    defaults = {"USD": 7.2, "GBP": 9.2, "JPY": 0.046, "SGD": 5.35}
    env_keys = {
        "USD": "FALLBACK_USD_CNY",
        "GBP": "FALLBACK_GBP_CNY",
        "JPY": "FALLBACK_JPY_CNY",
        "SGD": "FALLBACK_SGD_CNY",
    }
    if u in env_keys:
        raw = os.getenv(env_keys[u], str(defaults[u])).strip()
        try:
            v = float(raw)
            return v if v > 0 else defaults[u]
        except ValueError:
            return defaults[u]
    return defaults["USD"]


def resolve_rate_to_cny(iso: str) -> float:
    """搜索/详情换算：优先内存缓存，否则 ``fallback_rate_to_cny``。"""
    u = (iso or "USD").strip().upper()[:3]
    if u == "CNY":
        return 1.0
    if u in EXCHANGE_RATES:
        return float(EXCHANGE_RATES[u])
    return fallback_rate_to_cny(iso)


async def refresh_exchange_rates() -> None:
    """
    启动时调用：并行请求 Frankfurter，``timeout=2``；单项失败保留原有 ``EXCHANGE_RATES[code]``。
    """
    global EXCHANGE_RATES
    merged = dict(EXCHANGE_RATES)

    async def fetch_one(
        client: httpx.AsyncClient, code: str
    ) -> tuple[str, float | None]:
        try:
            r = await client.get(
                FRANKFURTER,
                params={"from": code, "to": "CNY"},
            )
            if r.status_code >= 500:
                logger.warning(
                    "[fx] Frankfurter %s→CNY HTTP %s; keeping %.4f",
                    code,
                    r.status_code,
                    merged.get(code, float("nan")),
                )
                return code, None
            r.raise_for_status()
            data = r.json()
            rate = float(data["rates"]["CNY"])
            if rate <= 0:
                raise ValueError("non-positive rate")
            return code, rate
        except Exception as e:
            prev = merged.get(code)
            logger.warning(
                "[fx] Frankfurter %s→CNY refresh failed (%s); keeping prior %.4f",
                code,
                e,
                prev if prev is not None else fallback_rate_to_cny(code),
            )
            return code, None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(FX_HTTP_TIMEOUT),
        follow_redirects=True,
    ) as client:
        pairs = await asyncio.gather(
            *[fetch_one(client, c) for c in REFRESH_ISO_CODES],
        )

    for code, rate in pairs:
        if rate is not None:
            merged[code] = rate

    EXCHANGE_RATES = merged


async def get_usd_cny_rate_cached() -> float:
    """供 ``GET /api/exchange-rate`` 使用：仅从内存读取（启动时已尝试刷新）。"""
    return float(EXCHANGE_RATES.get("USD") or fallback_rate_to_cny("USD"))
