"""
吉他搜索测试后端：根据实时汇率把多币种价格换算成人民币（CNY）。

汇率来源：Frankfurter（欧洲央行参考汇率，免费、无需 API Key）
文档：https://www.frankfurter.app/docs/

另：`GET /search` 使用 Reverb API（需环境变量 REVERB_TOKEN）。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from env_load import load_project_dotenv

load_project_dotenv()

from exchange_rate_cache import get_usd_cny_rate_cached
from reverb_client import listing_to_search_item, search_reverb_listings_async

FRANKFURTER = "https://api.frankfurter.app/latest"

# 与 Dockerfile 一致：构建产物在仓库根目录的 dist/，由同一进程托管前端（公网单域名）
DIST_DIR = Path(__file__).resolve().parent.parent / "dist"
HAS_FRONTEND = (DIST_DIR / "index.html").is_file()

# 假数据：金额 + ISO 货币代码，供汇率换算；原价展示文案给前端直接显示
RAW_LISTINGS: list[dict[str, Any]] = [
    {
        "id": "1",
        "imageUrl": "https://picsum.photos/seed/guitar1/640/480",
        "title": "Fender Japan Mustang MG69 / CIJ",
        "platform": "Digimart",
        "amount": 168_000.0,
        "currency": "JPY",
        "priceOriginal": "¥168,000",
    },
    {
        "id": "2",
        "imageUrl": "https://picsum.photos/seed/guitar2/640/480",
        "title": "Fender Mustang Offset 2018",
        "platform": "Reverb",
        "amount": 1249.0,
        "currency": "USD",
        "priceOriginal": "$1,249 USD",
    },
    {
        "id": "3",
        "imageUrl": "https://picsum.photos/seed/guitar3/640/480",
        "title": "Squier Classic Vibe Mustang",
        "platform": "Reverb",
        "amount": 429.0,
        "currency": "USD",
        "priceOriginal": "$429 USD",
    },
    {
        "id": "4",
        "imageUrl": "https://picsum.photos/seed/guitar4/640/480",
        "title": "Fender MIJ Mustang Bass",
        "platform": "Digimart",
        "amount": 142_000.0,
        "currency": "JPY",
        "priceOriginal": "¥142,000",
    },
    {
        "id": "5",
        "imageUrl": "https://picsum.photos/seed/guitar5/640/480",
        "title": "Offset Mustang Shell Pink",
        "platform": "Reverb",
        "amount": 980.0,
        "currency": "EUR",
        "priceOriginal": "€980 EUR",
    },
    {
        "id": "6",
        "imageUrl": "https://picsum.photos/seed/guitar6/640/480",
        "title": "Mustang 短弦长 改装拾音器",
        "platform": "Digimart",
        "amount": 198_000.0,
        "currency": "JPY",
        "priceOriginal": "¥198,000",
    },
]

app = FastAPI(
    title="Guitar Search API",
    version="0.1.0",
    # 生产环境由 StaticFiles 托管根路径时，避免 /docs 与前端路由混淆
    docs_url=None if HAS_FRONTEND else "/docs",
    redoc_url=None if HAS_FRONTEND else "/redoc",
)

_cors_origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
]
for _piece in os.getenv("ALLOWED_ORIGINS", "").split(","):
    _p = _piece.strip()
    if _p and _p not in _cors_origins:
        _cors_origins.append(_p)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def fetch_cny_rate(client: httpx.AsyncClient, currency: str) -> float:
    """返回 1 单位 `currency` 等于多少 CNY。"""
    if currency == "CNY":
        return 1.0
    r = await client.get(FRANKFURTER, params={"from": currency, "to": "CNY"})
    r.raise_for_status()
    data = r.json()
    try:
        return float(data["rates"]["CNY"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"汇率接口返回异常: {e}") from e


async def get_rates_to_cny(client: httpx.AsyncClient, currencies: set[str]) -> dict[str, float]:
    currencies.discard("CNY")
    tasks = [fetch_cny_rate(client, c) for c in sorted(currencies)]
    keys = sorted(currencies)
    values = await asyncio.gather(*tasks)
    return dict(zip(keys, values, strict=True))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/exchange-rate")
async def exchange_rate() -> dict[str, float]:
    """
    USD→CNY 参考汇率（ExchangeRate-API v6），进程内缓存 1 小时。

    环境变量：``EXCHANGE_RATE_API_KEY``
    """
    rate = await get_usd_cny_rate_cached()
    return {"rate": round(rate, 4)}


@app.get("/search")
async def search_reverb(
    q: str = Query(..., min_length=1, description="搜索关键词，例如 Fender"),
) -> dict[str, Any]:
    """
    调用 Reverb ``/api/listings/all``，返回标题、图片、价格、原页链接。

    需在 ``backend/.env`` 中配置 ``REVERB_TOKEN``（Personal Access Token）。
    """
    token = os.environ.get("REVERB_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="未配置 REVERB_TOKEN。请在 backend 目录创建 .env 并写入 REVERB_TOKEN=你的令牌",
        )

    try:
        raw = await search_reverb_listings_async(token, q.strip())
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:500] if e.response else str(e)
        raise HTTPException(
            status_code=502,
            detail=f"Reverb API 返回 {e.response.status_code if e.response else '?'}: {detail}",
        ) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"请求 Reverb 失败: {e}") from e

    results = [listing_to_search_item(item) for item in raw]
    return {"query": q.strip(), "results": results}


@app.get("/api/search")
async def search(q: str = Query("", description="型号关键词，匹配标题（不区分大小写）")) -> dict[str, Any]:
    q_norm = q.strip().lower()
    filtered = (
        [x for x in RAW_LISTINGS if q_norm in x["title"].lower()]
        if q_norm
        else list(RAW_LISTINGS)
    )

    if not filtered:
        return {
            "query": q.strip(),
            "listings": [],
            "fxNote": "没有匹配的条目；未请求汇率。",
        }

    currencies = {row["currency"] for row in filtered}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            rates = await get_rates_to_cny(client, currencies)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"请求汇率服务失败: {e}") from e

    listings: list[dict[str, Any]] = []
    for row in filtered:
        cur = row["currency"]
        rate = 1.0 if cur == "CNY" else rates[cur]
        cny = row["amount"] * rate
        listings.append(
            {
                "id": row["id"],
                "imageUrl": row["imageUrl"],
                "title": row["title"],
                "platform": row["platform"],
                "priceOriginal": row["priceOriginal"],
                "priceTarget": f"约 ¥{cny:,.2f}",
                "priceTargetCny": round(cny, 2),
                "fxRateUsed": rate,
                "fxFromCurrency": cur,
            }
        )

    return {
        "query": q.strip(),
        "listings": listings,
        "fxNote": "汇率来自 Frankfurter（ECB），工作日更新，仅供参考。",
    }


if HAS_FRONTEND:
    app.mount(
        "/",
        StaticFiles(directory=str(DIST_DIR), html=True),
        name="frontend",
    )
