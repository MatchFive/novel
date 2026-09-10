"""抓取 wangwenclub 创作指南页，提取文章标题与链接。"""
import re

import httpx

url = "https://www.wangwenclub.com/handbook/category/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97"
r = httpx.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
print("status", r.status_code, "len", len(r.text))

# 提取文章标题与链接
links = re.findall(r'<a[^>]+href="(/handbook/[^"]+)"[^>]*>([^<]{3,80})</a>', r.text)
seen = set()
out = []
for href, title in links:
    t = title.strip()
    if t and t not in seen and "category" not in href and len(t) > 3:
        seen.add(t)
        out.append((href, t))
for href, t in out[:40]:
    print(href, "|", t)
print("total:", len(out))
