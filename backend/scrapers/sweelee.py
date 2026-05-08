"""
Swee Lee（Shopify 店面 ``www.sweelee.com.sg``）搜索与常量。

**分页（与浏览器一致，已核对）**

- 站内搜索页 URL 形态为 ``/search?q=…&page=N``（``N`` 从 1 起），不使用 ``offset`` /
  ``start``。
- Storefront JSON：``/search.json``、``/products.json`` 使用查询参数 ``page``（1-based）与
  ``limit``；不是 ``offset``。
- 当 ``search.json`` 返回 SPA HTML 时，回退为 ``search/suggest.json`` +
  ``products.json``。后者按 **整店目录** 分页：必须把 ``products.json`` 的 ``page`` 连续
  向后翻，再按关键词筛选并 **跳过前 (page-1)×limit 条命中**，才能得到第 N 页搜索结果
  （仅传 ``page=N`` 会错拿到目录的第 N 页，而非搜索的第 N 页）。

**每页条数**

- 统一使用 ``SWEELEE_PAGE_LIMIT``（40）；Shopify ``products.json`` 单次 ``limit`` 最大为 250。
"""

from __future__ import annotations

SWEELEE_ORIGIN = "https://www.sweelee.com.sg"
SWEELEE_SEARCH_JSON = f"{SWEELEE_ORIGIN}/search.json"
SWEELEE_SUGGEST_JSON = f"{SWEELEE_ORIGIN}/search/suggest.json"
SWEELEE_PRODUCTS_JSON = f"{SWEELEE_ORIGIN}/products.json"

# 与 ``/api/search`` 单次抓取宽度一致；勿使用 10 等小默认值
SWEELEE_PAGE_LIMIT = 40
# suggest.json：Shopify 允许的上限（常见为 10～50，按主题）；与 PAGE_LIMIT 对齐
SWEELEE_SUGGEST_LIMIT = min(SWEELEE_PAGE_LIMIT, 50)
# 目录回退时最多向后翻多少页 ``products.json``，防止极端关键词扫库过久
SWEELEE_MAX_CATALOG_PAGES_WALK = 80
# ``has_more`` 启发式：本页达到该条数时认为可能还有下一页
SWEELEE_HAS_MORE_HINT = SWEELEE_PAGE_LIMIT

# 合并进统一搜索前：吉他相关标题加权、常见配件降序、低价非首选（CNY，与 ``price_cny`` 一致）
SWEELEE_MIN_PREFERRED_PRICE_CNY = 2000.0
SWEELEE_GUITAR_BOOST_SUBSTRINGS = (
    "guitar",
    "stratocaster",
    "telecaster",
    "jazzmaster",
)
SWEELEE_ACCESSORY_DEMOTE_SUBSTRINGS = (
    "strap",
    "picks",
    "cable",
)

SWEELEE_FORCE_CURRENCY_PARAMS = {"currency": "SGD"}
SWEELEE_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*;q=0.1",
    "Accept-Language": "en-SG,en-US;q=0.9,en;q=0.8",
}
