"""抓取创作指南核心文章正文并保存为文本。"""
import re

import httpx

BASE = "https://www.wangwenclub.com"
ARTICLES = [
    "/handbook/创作指南/写作流程",
    "/handbook/创作指南/情节设计",
    "/handbook/创作指南/人物塑造",
    "/handbook/创作指南/世界观构建",
    "/handbook/创作指南/金手指设计大全",
    "/handbook/创作指南/主角人设完全手册",
    "/handbook/创作指南/对话写作技巧",
    "/handbook/创作指南/反派设计方法",
    "/handbook/创作指南/伏笔与照应",
]


def fetch_article(path: str) -> str:
    r = httpx.get(BASE + path, timeout=25,
                  headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    text = r.text
    m = re.search(r"<article[^>]*>(.*?)</article>", text, re.S)
    body = m.group(1) if m else text
    # 去脚本/样式/标签
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S)
    body = re.sub(r"</(h1|h2|h3|p|li)>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    return body.strip()


def main() -> None:
    out = []
    for path in ARTICLES:
        try:
            body = fetch_article(path)
            title = path.split("/")[-1]
            out.append(f"# {title}\n{body}")
            print(f"{title}: {len(body)} chars")
        except Exception as exc:
            print(f"{path} FAILED: {exc}")
    all_text = "\n\n".join(out)
    with open("resources/tutorial_raw.txt", "w", encoding="utf-8") as f:
        f.write(all_text)
    print("saved to resources/tutorial_raw.txt, total", len(all_text))


if __name__ == "__main__":
    main()
