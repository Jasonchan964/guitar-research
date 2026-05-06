"""
独立脚本：从石桥乐器国际站 Shopify products.json 抓取商品并导出 CSV。

运行（在项目根目录 guitar-search/ 下）：
    pip install requests pandas
    python fetch_ishibashi_products.py
"""

from __future__ import annotations

import time

import pandas as pd
import requests


def fetch_ishibashi_products(max_pages: int = 5) -> pd.DataFrame:
    base_url = "https://intl.ishibashi.co.jp/products.json"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    all_products: list[dict[str, str]] = []

    for page in range(1, max_pages + 1):
        print(f"正在抓取第 {page} 页商品数据...")
        params = {"limit": 50, "page": page}

        try:
            response = requests.get(
                base_url, headers=headers, params=params, timeout=10
            )
            if response.status_code != 200:
                print(f"请求失败，状态码: {response.status_code}")
                break

            data = response.json()
            products = data.get("products", [])

            if not products:
                print("已无更多商品，抓取结束。")
                break

            for prod in products:
                title = prod.get("title")
                vendor = prod.get("vendor")
                handle = prod.get("handle")
                product_url = f"https://intl.ishibashi.co.jp/products/{handle}"

                images = prod.get("images", [])
                image_url = images[0].get("src") if images else ""

                variants = prod.get("variants", [])
                if variants:
                    variant = variants[0]
                    price = variant.get("price")
                    sku = variant.get("sku")
                    available = "Available" if variant.get("available") else "Sold Out"
                else:
                    price = "N/A"
                    sku = "N/A"
                    available = "Unknown"

                all_products.append(
                    {
                        "Title": title,
                        "Brand/Vendor": vendor,
                        "Item Code/SKU": sku,
                        "Price (JPY)": price,
                        "Status": available,
                        "URL": product_url,
                        "Image URL": image_url,
                    }
                )

            time.sleep(1.5)

        except Exception as e:
            print(f"抓取第 {page} 页时发生错误: {e}")
            break

    return pd.DataFrame(all_products)


if __name__ == "__main__":
    df_instruments = fetch_ishibashi_products(max_pages=5)
    output_filename = "ishibashi_guitars_data.csv"
    df_instruments.to_csv(output_filename, index=False, encoding="utf-8-sig")
    print(f"\n抓取完成！共收集 {len(df_instruments)} 款乐器商品数据。")
    print(f"数据已成功保存至: {output_filename}")
