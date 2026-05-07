"""
连通性测试：从环境变量读取 REVERB_API_TOKEN，搜索并打印第一条结果的标题与价格。

推荐：在 backend 目录下创建 .env（不要提交到 Git），内容示例：

  REVERB_API_TOKEN=你的_token_粘贴在这里

然后：

  cd backend
  python reverb_search_smoke.py

也会读取系统环境变量或 .env 中的 REVERB_API_TOKEN。

切勿把 token 写进代码或提交到 Git。
"""

from __future__ import annotations

import os
import sys
from env_load import load_project_dotenv
from reverb_client import fetch_first_listing_title_and_price

load_project_dotenv()


def main() -> None:
    token = (os.environ.get("REVERB_API_TOKEN") or "").strip()
    if not token:
        print("请先设置环境变量 REVERB_API_TOKEN", file=sys.stderr)
        sys.exit(1)

    title, price = fetch_first_listing_title_and_price(
        token,
        query="Fender Mustang",
    )
    print(title)
    print(price)


if __name__ == "__main__":
    main()
