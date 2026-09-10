"""提取创作指南各文章链接。"""
import re

import httpx

url = "https://www.wangwenclub.com/handbook/category/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97"
r = httpx.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
text = r.text

# Docusaurus 卡片/链接：抓取所有 handbook 下的文章链接（非 category）
hrefs = re.findall(r'href="(/handbook/[^"#?]+)"', text)
seen = []
for h in hrefs:
    if h not in seen and "category" not in h and h.count("/") >= 2:
        seen.append(h)
print("links:", len(seen))
for h in seen[:40]:
    print(h)
