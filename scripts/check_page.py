"""检查教程页 HTML 结构。"""
import re

import httpx

url = "https://www.wangwenclub.com/handbook/category/%E5%88%9B%E4%BD%9C%E6%8C%87%E5%8D%97"
r = httpx.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
text = r.text
print("=== title ===")
m = re.search(r"<title>(.*?)</title>", text)
print(m.group(1) if m else "no title")
print("=== 是否有 handbook/ 链接 ===")
print("handbook/ in text:", "handbook/" in text)
print("=== script/json 数据特征 ===")
print("__NEXT_DATA__" in text, "nuxt" in text.lower(), "window.__" in text)
print("=== 部分文本（去标签）===")
plain = re.sub(r"<[^>]+>", " ", text)
plain = re.sub(r"\s+", " ", plain)
print(plain[:1500])
