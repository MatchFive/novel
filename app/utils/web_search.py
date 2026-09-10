"""轻量联网检索（Bing 优先，DuckDuckGo 兜底；均无需 API Key）。

用途：正文/大纲涉及现实知识（高考分数线、真实机构、历史事实等）时，
写作前的上下文策划步可触发联网核查，把结果注入提示词，避免"618 分上北大"类硬伤。
失败一律返回空字符串（不阻塞主流程）；调用结果写入 LLM 调用日志供复盘。
"""
from __future__ import annotations

import re

from app.core.logger import get_logger

log = get_logger("web_search")

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Accept-Language": "zh-CN,zh;q=0.9"}


def web_search(query: str, limit: int = 3, timeout: float = 12.0) -> str:
    """联网搜索：搜狗网页 + 搜狗知乎垂类合并（中文事实类查询实测最准），Bing/DDG 兜底。"""
    query = (query or "").strip()
    if not query:
        return ""
    results: list[str] = []
    for engine in (_search_sogou, _search_zhihu):  # 两个搜狗源合并，互补网页与问答内容
        try:
            r = engine(query, limit, timeout)
            if r:
                log.info("web_search %s：%s → %d 行", engine.__name__, query,
                         len(r.splitlines()))
                results.extend(r.splitlines())
        except Exception as exc:
            log.warning("web_search %s 失败: %s", engine.__name__, exc)
    if not results:  # 搜狗系全挂才用兜底引擎
        for engine in (_search_bing, _search_ddg):
            try:
                r = engine(query, limit, timeout)
                if r:
                    results.extend(r.splitlines())
                    break
            except Exception as exc:
                log.warning("web_search %s 失败: %s", engine.__name__, exc)
    if not results:
        log.warning("web_search 所有引擎均失败: %s", query)
        return ""
    # 按标题去重，网页+问答合并输出
    seen: set[str] = set()
    out: list[str] = []
    for line in results:
        key = line.split("：")[0]
        if key not in seen:
            seen.add(key)
            out.append(line)
    return "\n".join(out[: limit * 2])


def web_search_logged(query: str, data_dir=None, limit: int = 3) -> str:
    """联网检索并写入 LLM 调用日志（工具调用留痕，供复盘）。"""
    import time as _time
    t0 = _time.perf_counter()
    result = web_search(query, limit=limit)
    if data_dir is not None:
        try:
            from app.llm.call_log import write_llm_call
            write_llm_call(data_dir, {
                "module": "tool", "caller": "web_search", "provider": "bing/ddg",
                "tier": "", "model": "web-search", "prompt_key": "tool.web_search",
                "input_summary": query,
                "output_summary": result or "（无结果/失败）",
                "in_tokens": 0, "out_tokens": 0,
                "duration_ms": int((_time.perf_counter() - t0) * 1000),
                "status": "ok" if result else "failed",
            })
        except Exception:
            pass
    return result


# ---------------- 引擎 ----------------
def _search_sogou(query: str, limit: int, timeout: float) -> str:
    """搜狗网页（中文查询质量最好，实测可直出分数线类精准结果）。"""
    import httpx
    resp = httpx.get("https://www.sogou.com/web", params={"query": query},
                     headers=_UA, timeout=timeout, follow_redirects=True)
    if resp.status_code != 200:
        return ""
    return _parse_sogou(resp.text, limit)


def _search_zhihu(query: str, limit: int, timeout: float) -> str:
    """搜狗·知乎垂类（事实问答质量高，如「618 分能上什么大学」直接给院校清单）。"""
    import httpx
    resp = httpx.get("https://zhihu.sogou.com/zhihu", params={"query": query},
                     headers=_UA, timeout=timeout, follow_redirects=True)
    if resp.status_code != 200:
        return ""
    return _parse_sogou(resp.text, limit)  # 知乎垂类与网页版同为 vrwrap 结构


def _parse_sogou(html: str, limit: int) -> str:
    """解析搜狗结果页：<div class="vrwrap"> 块，h3.vr-title 为标题，首个 <p> 为摘要。"""
    blocks = re.findall(r'<div class="vrwrap".*?(?=<div class="vrwrap"|$)', html, flags=re.S)
    lines = []
    for b in blocks:
        m = re.search(r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>',
                      b, flags=re.S)
        if not m:
            continue
        title = _clean(m.group(1))
        # 摘要：块内去掉标题后的第一个 <p>
        rest = b[m.end():]
        p = re.search(r"<p[^>]*>(.*?)</p>", rest, flags=re.S)
        snippet = _clean(p.group(1)) if p else ""
        if title:
            lines.append(f"· {title}：{snippet[:200]}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def _search_bing(query: str, limit: int, timeout: float) -> str:
    import httpx
    resp = httpx.get(
        "https://cn.bing.com/search",
        params={"q": query, "mkt": "zh-CN", "setlang": "zh-hans", "count": "10"},
        headers=_UA, timeout=timeout, follow_redirects=True)
    if resp.status_code != 200:
        return ""
    return _parse_bing(resp.text, limit)


def _parse_bing(html: str, limit: int) -> str:
    """解析 Bing 结果页：<li class="b_algo"><h2><a>标题</a></h2>…<p>摘要</p>。"""
    blocks = re.findall(r'<li class="b_algo".*?</li>', html, flags=re.S)
    lines = []
    for b in blocks[:limit]:
        m = re.search(r"<h2[^>]*>.*?<a[^>]*>(.*?)</a>", b, flags=re.S)
        if not m:
            continue
        title = _clean(m.group(1))
        p = re.search(r"<p[^>]*>(.*?)</p>", b, flags=re.S)
        snippet = _clean(p.group(1)) if p else ""
        if title:
            lines.append(f"· {title}：{snippet[:200]}")
    return "\n".join(lines)


def _search_ddg(query: str, limit: int, timeout: float) -> str:
    import httpx
    resp = httpx.get("https://html.duckduckgo.com/html/", params={"q": query},
                     headers=_UA, timeout=timeout, follow_redirects=True)
    if resp.status_code != 200:
        return ""
    return _parse_ddg(resp.text, limit)


def _parse_ddg(html: str, limit: int) -> str:
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, flags=re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, flags=re.S)
    lines = []
    for i, t in enumerate(titles[:limit]):
        title = _clean(t)
        snippet = _clean(snippets[i]) if i < len(snippets) else ""
        if title:
            lines.append(f"· {title}：{snippet[:200]}")
    return "\n".join(lines)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()
