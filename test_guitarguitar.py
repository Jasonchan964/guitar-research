import httpx
from bs4 import BeautifulSoup
import urllib.parse
import json

async def test_scrape_guitarguitar(keyword: str, page: int = 1):
    # 1. 编码关键词并拼接 URL
    encoded_keyword = urllib.parse.quote_plus(keyword)
    url = f"https://www.guitarguitar.co.uk/pre-owned/?Query={encoded_keyword}&page={page}"
    
    # 2. 模拟高级浏览器 Headers 防爬伪装
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Referer": "https://www.guitarguitar.co.uk/",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"'
    }
    
    print(f"正在请求 GuitarGuitar URL: {url}")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                print(f"请求失败，状态码: {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # 3. 定位商品卡片容器
            # GuitarGuitar 的商品通常包裹在具有特定 class 的 grid 元素中，这里定位其商品列表项
            items = soup.select(".product-list .product-item, .grid-layout .product-card, [class*='product-item']")
            
            # 如果上述宽泛选择器失效，备用精确定位（根据其实际渲染的普通商品列表卡片 class）
            if not items:
                items = soup.find_all("div", class_="sh-item") or soup.select(".product-grid-item")
            
            results = []
            
            for item in items[:15]:  # 限制单页解析数量，提升响应速度
                try:
                    # 提取标题
                    title_elem = item.select_one(".title, [class*='title'], h3, h4")
                    title = title_elem.text.strip() if title_elem else "未知吉他"
                    
                    # 提取链接
                    link_elem = item.select_one("a[href]")
                    if not link_elem:
                        continue
                    relative_url = link_elem["href"]
                    full_url = urllib.parse.urljoin("https://www.guitarguitar.co.uk", relative_url)
                    
                    # 提取图片
                    img_elem = item.select_one("img[src]")
                    img_url = ""
                    if img_elem:
                        img_url = img_elem.get("data-src") or img_elem.get("src") or ""
                        img_url = urllib.parse.urljoin("https://www.guitarguitar.co.uk", img_url)
                    
                    # 提取英镑价格
                    price_elem = item.select_one(".price, [class*='price'], .amount")
                    price_gbp = 0.0
                    if price_elem:
                        # 过滤掉英镑符号、逗号等，提取浮点数
                        price_text = price_elem.text.replace("£", "").replace(",", "").strip()
                        try:
                            price_gbp = float(price_text.split()[0]) # 防止带单位
                        except ValueError:
                            pass
                    
                    # 统一状态：GuitarGuitar Pre-Owned 频道全部为二手
                    condition = "二手"
                    
                    results.append({
                        "title": title,
                        "image": img_url,
                        "price_raw": price_gbp,
                        "currency": "GBP",
                        "condition": condition,
                        "source": "GuitarGuitar",
                        "url": full_url
                    })
                except Exception as card_err:
                    print(f"解析单个商品卡片失败: {card_err}")
                    continue
            
            print(f"成功抓取到 {len(results)} 个 GuitarGuitar 商品！")
            print(json.dumps(results[:3], indent=2, ensure_ascii=False))
            return results
            
        except Exception as e:
            print(f"抓取 GuitarGuitar 过程中发生异常: {e}")
            return []

# 运行本地测试
if __name__ == "__main__":
    import asyncio
    asyncio.run(test_scrape_guitarguitar("Fender Mustang", page=1))
