"""联网核查服务层（P5 Task 9，设计 §2.5 联网核查行）：检索 → digest → LLM 核实 → research.md 落账。

**口径**（seed ``app/services/web_service.py`` 移植，见勘察报告 §2）：

1. ``search`` —— 两个 provider 适配（kimi_api / minimax），归一为
   ``{title, url, snippet, date, site_name}``；**HTTP 失败 → 空列表不 raise**
   （联网失败一律降级，不阻断写作）；
2. ``verify`` —— search → digest（每条 ``[site] title（date）\\n snippet[:400]\\n url``，
   总量截 4000 字）→ 渲染 ``task.web_search_verify`` → **自由文本结论**（非 JSON）；
   「未能核实」也是一条结论，照样落 entry；
3. ``verify_deep`` —— 逐 query：research.md 命中直接复用；否则 verify +
   ≤``followup_rounds`` 轮「渲 ``task.web_search_followup`` → ``{"queries":[≤2]}`` → 补搜再核实」。

**落账** ``.harness/research.md``（运行态记录，与 issues/events 同层；**不是**内容文件）：
``## <query>`` + ``<!-- key: … kind: … hits: N -->`` + 结论正文 + sources 行。
去重两级：query_key 精确命中 → hits+1 不追加；字符集重叠 ``|A∩B|/min ≥ 0.9`` → 同上。
**写前重读合并**（store 纪律：聚合文件不并发丢写）。

**联网纪律**：未配 ``[search]`` 或 ``web_verify=false`` → web 工具明确回绝（零 LLM 零 HTTP）；
本模块自身不做开关判定（那是工具层的事），但 ``settings is None`` 时所有方法**零 HTTP 零 LLM**。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from harness.config import SearchSettings
from harness.prompts.loader import PromptLoader
from harness.writing.pure import WRITING_ROUTING

__all__ = [
    "RESEARCH_FILE",
    "RESULTS_MAX_CHARS",
    "ResearchEntry",
    "WebSearchService",
    "append_research",
    "genre_canon_refs",
    "load_research",
    "query_key_of",
    "real_world_refs",
]

log = logging.getLogger("websearch")

#: research.md 落点（运行态记录，与 issues/events 同层）
RESEARCH_FILE = Path(".harness") / "research.md"

#: 检索结果 digest 的总字数上限（seed RESULTS_MAX_CHARS 同值）
RESULTS_MAX_CHARS = 4000

#: 单条检索结果的摘要长度上限
_SNIPPET_MAX = 400

#: query_key 归一：去空白与标点 + 小写
_KEY_STRIP_CHARS = frozenset(" \t　\r\n，。、；：！？…—·,.;:!?\"'“”‘’()（）【】《》[]{}「」『』")
_KEY_STRIP_RE = re.compile(r"[\s　，。、；：！？…—·,.;:!?\"'“”‘’()（）【】《》\[\]{}「」『』]+")

#: research.md 的元信息行：<!-- key: <key> kind: <kind> hits: <n> sources: ["u1", "u2"] -->
#: （sources 走元信息行的 JSON 数组——旧格式把 sources 写成 '- ' 开头的行，结论里以
#: '- ' 开头的行会被误当 sources；旧格式仍可读，读到时按旧口径解析 sources）
_META_RE = re.compile(
    r"^<!--\s*key:\s*(?P<key>.*?)\s+kind:\s*(?P<kind>.*?)\s+hits:\s*(?P<hits>\d+)"
    r"(?:\s+sources:\s*(?P<sources>\[.*\]))?\s*-->$")

#: 近义合并的**共享字符数**下限（对齐 seed 的 need=3 口径）
_SHARED_CHARS_MIN = 3


@dataclass
class ResearchEntry:
    """一条联网核查结论（research.md 的一节）。"""

    query: str
    query_key: str
    kind: str
    conclusion: str
    sources: list[str] = field(default_factory=list)
    hit_count: int = 1


# ------------------------------------------------------------------ 归一与去重

def query_key_of(query: str) -> str:
    """查询归一键：去空白与标点 + 小写（精确去重判定用）。"""
    return _KEY_STRIP_RE.sub("", (query or "").strip().lower())


def _char_set(text: str) -> set[str]:
    """去重用的字符集（中文按字，无需分词）。"""
    return {c for c in (text or "") if c not in _KEY_STRIP_CHARS}


def _overlap_ratio(a: str, b: str) -> float:
    """字符集重叠率 ``|A∩B|/min(|A|,|B|)``（空集 → 0）。"""
    sa, sb = _char_set(a), _char_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


# ------------------------------------------------------------------ 现实参照确定性兜底
# 逐字底本：seed web_dream/backend/app/workflows/autopilot_runner.py:2091-2146
# （本地抽取件 seed-ref.real_refs_fallback.py）。移植口径：常量值一个字母不改。

#: 现实世界真实对象的兜底线索（预检模型偶尔会漏；这些是网文里最常被当成「现实参照」的）
_REAL_WORK_HINTS: tuple[str, ...] = (
    "率土之滨", "文明6", "文明六", "三国志", "全面战争", "太阁立志传", "魔兽世界", "战锤",
    "冰与火之歌", "龙与地下城", "D&D", "克苏鲁", "沙丘", "银河英雄传说", "模拟城市",
    "帝国时代", "纪元1800", "戴森球计划", "缺氧", "饥荒", "十字军之王", "钢铁雄心",
)

#: 书名号内的作品名（《…》）
_QUOTED_TITLE = re.compile(r"《([^》]{2,30})》")

#: 题材 → **正典参照**的检索词（确定性兜底）
_GENRE_CANON: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("剑与魔法", "西幻", "西方奇幻", "魔法", "骑士", "精灵", "矮人", "魔王", "奇幻"),
     ("龙与地下城 职业 阶位 法术位 规则", "魔戒 中土 种族 力量体系 设定")),
    (("修仙", "仙侠", "修真"),
     ("网文 修真 境界体系 炼气 筑基 金丹 常见设定", "仙侠 门派 丹药 法器 常见套路")),
    (("玄幻", "爽文", "打脸"),
     ("网文 玄幻 开局流 金手指 常见套路", "网文 爽点 打脸 升级 节奏 设计")),
    (("种田", "农业", "领地", "领主", "经营", "基建", "庄园"),
     ("中世纪庄园 农业 耕作 经济 制度", "率土之滨 内政 科技树 建筑升级机制")),
    (("科技树", "文明", "策略", "发展"),
     ("文明6 尤里卡 触发条件 科技研究加速", "文明系列 科技树 分时代 研究解锁机制")),
    (("都市", "校园", "职场", "青春"),
     ("都市网文 开局 常见套路 读者期待",)),
    (("科幻", "星际", "赛博", "机甲", "末世"),
     ("科幻 常见设定 星系 政治 科技 体系",)),
    (("克苏鲁", "怪谈", "洛氏"),
     ("克苏鲁神话 体系 神话生物 常见设定",)),
    (("历史", "古代", "王朝", "宫斗"),
     ("中国古代 官制 经济 日常生活 常识",)),
    (("悬疑", "推理", "刑侦"),
     ("推理小说 诡计 常见类型 本格 社会派",)),
)


def genre_canon_refs(text: str) -> list[str]:
    """按题材关键词给出**正典参照**的检索词（确定性兜底）。

    为什么必须有：`task.auto_ref_check` 曾明说「架空西幻常常就是没有（现实参照）」，
    于是「剑与魔法」这类需求一条参照都列不出来，预备的槽位全被需求里点名的游戏占满——
    模型只能凭训练记忆编一套通用西方奇幻设定。正典（《魔戒》《龙与地下城》这类真实存在的
    作品及其体系）本来就是现实参照，必须能进清单。
    """
    t = text or ""
    out: list[str] = []
    for keys, queries in _GENRE_CANON:
        if any(k in t for k in keys):
            out += [q for q in queries if q not in out]
    return out


def real_world_refs(text: str, genre: str = "") -> list[str]:
    """确定性兜底：从需求文本里抠出现实参照（《书名》 + 常见真实作品/游戏名 + 题材正典）。"""
    refs = [m.group(1).strip() for m in _QUOTED_TITLE.finditer(text or "")]
    refs += [h for h in _REAL_WORK_HINTS if h in (text or "")]
    refs += genre_canon_refs(f"{genre}\n{text}")
    return list(dict.fromkeys(r for r in refs if r))[:8]


def _is_same_query(a: str, b: str) -> bool:
    """两条 query 是否同一（精确 key 命中 或 字符集重叠 ≥0.9 **且共享字符数 ≥3**）。

    「共享字符数 ≥3」是必需的：只看重叠率时，包含关系（「内政」⊂「内政系统」）的重叠率
    恒为 1.0（min() 分母）→ 两个不同的问题被合并 → 复用路径把前者的结论当后者的答案。
    """
    if a == b:
        return True
    sa, sb = _char_set(a), _char_set(b)
    if len(sa & sb) < _SHARED_CHARS_MIN:
        return False
    return _overlap_ratio(a, b) >= 0.9


# ------------------------------------------------------------------ research.md 读写

def _research_path(root: Path) -> Path:
    return Path(root) / RESEARCH_FILE


def _render_entry(entry: ResearchEntry) -> str:
    """条目渲染：``## <query>`` + 元信息行（key/kind/hits/sources）+ 结论正文（逐字）。

    sources 进元信息行（JSON 数组）→ 结论里以 '- ' 开头的行不再被误当 sources；
    结论里的 '## ' 行也不再被切成新条目（解析规则：'## ' 之后须跟元信息行才是新条目）。
    """
    sources = json.dumps(list(entry.sources), ensure_ascii=False)
    meta = (f"<!-- key: {entry.query_key} kind: {entry.kind} hits: {entry.hit_count} "
            f"sources: {sources} -->")
    lines = [f"## {entry.query}", meta]
    if entry.conclusion:
        lines.extend(entry.conclusion.split("\n"))
    return "\n".join(lines) + "\n"


def _parse_sources(raw: str) -> list[str]:
    """元信息行里的 sources JSON 数组 → 字符串列表。

    坏 JSON / 不是数组 → **空列表 + 警告**（出处不得静默丢失——丢了就再也查不回
    「这条结论是从哪来的」）。
    """
    try:
        value = json.loads(raw)
    except ValueError as exc:
        log.warning("research.md 的 sources 不是合法 JSON，出处按空处理：%s", exc)
        return []
    if not isinstance(value, list):
        log.warning("research.md 的 sources 不是数组（%s），出处按空处理",
                    type(value).__name__)
        return []
    return [str(v) for v in value]


def _next_is_meta(lines: list[str], start: int) -> bool:
    """从 start 起第一个非空行是不是元信息行（新条目的判据）。"""
    for line in lines[start:]:
        if not line.strip():
            continue
        return _META_RE.match(line.strip()) is not None
    return False


def load_research(root: Path) -> list[ResearchEntry]:
    """读 research.md → 条目列表；文件不存在 → 空列表。

    **无损往返**：sources 在元信息行里（JSON 数组）；结论 = 元信息行之后的全部正文行
    （去掉尾部空行），原样保留——结论里以 '- ' 开头的行不再被当 sources，以 '## ' 开头的
    行也不再被切成新条目（'## ' 之后须跟元信息行才是新条目）。
    旧格式（元信息行无 sources、sources 是 '- ' 行）仍可读，按旧口径解析。
    """
    path = _research_path(root)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("research.md 读取失败，按空处理：%s", exc)
        return []
    entries: list[dict] = []
    cur: dict | None = None
    lines = text.splitlines()
    i = 0
    total = len(lines)
    while i < total:
        line = lines[i]
        if line.startswith("## ") and _next_is_meta(lines, i + 1):
            cur = {"query": line[3:].strip(), "query_key": "", "kind": "",
                   "hit_count": 1, "sources": [], "body": [], "legacy": False}
            entries.append(cur)
            i += 1
            # 取元信息行：**同样跳过空行**（与 _next_is_meta 的判定口径一致）——
            # 否则「## 标题」与元信息行之间有空行时，元信息不被解析（key/kind/hits/sources
            # 全丢），而且那行元信息会被吞进结论。
            while i < total and not lines[i].strip():
                i += 1
            m = _META_RE.match(lines[i].strip()) if i < total else None
            if m:
                cur["query_key"] = m.group("key")
                cur["kind"] = m.group("kind")
                cur["hit_count"] = int(m.group("hits"))
                raw_sources = m.group("sources")
                if raw_sources is None:
                    cur["legacy"] = True        # 旧格式：sources 是 '- ' 开头的行
                else:
                    cur["sources"] = _parse_sources(raw_sources)
            i += 1
            continue
        if cur is not None:
            cur["body"].append(line)
        i += 1
    out: list[ResearchEntry] = []
    for e in entries:
        body = list(e["body"])
        while body and not body[-1].strip():
            body.pop()                          # 去掉尾部空行
        if e["legacy"]:
            sources = [ln[2:].strip() for ln in body if ln.startswith("- ")]
            conclusion = "\n".join(ln.strip() for ln in body
                                   if ln.strip() and not ln.startswith("- "))
        else:
            sources = list(e["sources"])
            conclusion = "\n".join(body)
        out.append(ResearchEntry(query=e["query"], query_key=e["query_key"], kind=e["kind"],
                                  conclusion=conclusion, sources=sources,
                                  hit_count=e["hit_count"]))
    return out


def _write_research(root: Path, entries: list[ResearchEntry]) -> None:
    """整文件重写（tmp + replace 原子写）。"""
    path = _research_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_render_entry(e) for e in entries)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def append_research(root: Path, entry: ResearchEntry) -> None:
    """落账：命中已有条目 → hit_count+1（不重复追加正文）；否则追加新节。

    **写前重读合并**（store 纪律）：聚合文件不并发丢写。
    """
    entries = load_research(root)
    for existing in entries:
        if _is_same_query(existing.query_key, entry.query_key):
            existing.hit_count += 1
            _write_research(root, entries)
            return
    entries.append(entry)
    _write_research(root, entries)


# ------------------------------------------------------------------ 服务层

def _normalize(item: dict) -> dict:
    """检索结果归一（两个 provider 的字段名不同，统一到一套）。

    url 取 ``url`` 或 ``link``——minimax 的 ``/v1/coding_plan/search`` 响应用的是
    **``link``**（seed ``_search_minimax`` 同款）；漏映射会让 url/site_name 全空 →
    digest 无链接、``ResearchEntry.sources`` 无出处 → research.md 丢掉全部溯源信息。
    """
    if not isinstance(item, dict):
        return {"title": "", "url": "", "snippet": "", "date": "", "site_name": ""}
    return {
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or item.get("link") or ""),
        "snippet": str(item.get("snippet") or item.get("content") or item.get("text") or ""),
        "date": str(item.get("date") or ""),
        "site_name": str(item.get("site_name") or item.get("site") or ""),
    }


def _digest(results: list[dict]) -> str:
    """检索结果 digest：每条 ``[site] title（date）\\n snippet[:400]\\n url``，总量截 4000。"""
    blocks: list[str] = []
    total = 0
    for item in results:
        site = item.get("site_name") or ""
        head = f"[{site}] {item.get('title') or ''}".strip()
        if item.get("date"):
            head = f"{head}（{item['date']}）"
        block = f"{head}\n{(item.get('snippet') or '')[:_SNIPPET_MAX]}\n{item.get('url') or ''}"
        if total + len(block) > RESULTS_MAX_CHARS:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


class WebSearchService:
    """联网核查服务（检索 + 核实 + 落账）。

    ``settings`` 为 None（未配 ``[search]``）时：**零 HTTP 零 LLM**（search 返回空、
    verify 返回 None）——「未配置」由工具层明确回绝，本层只保证不偷偷出网。
    """

    def __init__(self, settings: SearchSettings | None, llm, prompts: PromptLoader, *,
                 http_client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._llm = llm
        self._prompts = prompts
        self._http = http_client
        self._owns_client = http_client is None
        #: 本次服务的 LLM 用量累计（工具层冒泡到 ToolResult.usage；无调用时 None）
        self.usage_total = None

    # ------------------------------------------------------------------ 内部

    def _client(self) -> httpx.AsyncClient | None:
        if self._settings is None:
            return None
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._settings.timeout_seconds)
        return self._http

    async def aclose(self) -> None:
        """关掉自建 client（注入的不关——所有权在注入方）。"""
        if self._http is not None and self._owns_client:
            await self._http.aclose()
            self._http = None

    async def _post(self, url: str, payload: dict) -> dict:
        client = self._client()
        if client is None:
            return {}
        headers = {"Content-Type": "application/json"}
        api_key = str(getattr(self._settings, "api_key", "") or "").strip()
        if api_key:
            # seed 同款：带鉴权头（此前**从不发送** → 真调用一律 401）。
            # api_key 为空时不带该头——不发空的 ``Bearer ``。
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.InvalidURL, httpx.UnsupportedProtocol, ValueError) as exc:
            # 联网失败一律降级（不阻断写作），不 raise。
            # httpx.InvalidURL **不是** HTTPError 子类（坏 base_url 的端口/协议非法）——
            # 不显式捕获就会把异常抛穿工具层。
            log.warning("联网检索失败（已降级为空结果）：%s", exc)
            return {}

    async def _complete(self, prompt: str, task_key: str) -> str:
        """LLM 自由文本调用（verify/followup 的结论不是 JSON）。"""
        from harness.tools.base import usage_add

        provider = self._llm.provider
        tier, temp, mt = WRITING_ROUTING[task_key]
        model = getattr(provider, tier, None) or provider.standard
        completion = await self._llm.complete([{"role": "user", "content": prompt}],
                                              model=model, temperature=temp, max_tokens=mt)
        self.usage_total = usage_add(self.usage_total, completion.usage)
        return completion.text

    # ------------------------------------------------------------------ 检索

    async def search(self, query: str) -> list[dict]:
        """检索 → 归一结果列表；HTTP 失败 → 空列表（不 raise）。"""
        if self._settings is None:
            return []
        text = str(query or "").strip()
        if not text:
            return []
        s = self._settings
        base = s.base_url.rstrip("/")
        if s.provider == "minimax":
            url = f"{base}/v1/coding_plan/search"
            payload = {"q": text}
            raw = await self._post(url, payload)
            items = raw.get("organic") if isinstance(raw, dict) else None
        else:
            url = f"{base}/tools/search"
            payload = {"text_query": text, "limit": s.limit,
                       "timeout_seconds": s.timeout_seconds}
            raw = await self._post(url, payload)
            items = raw.get("search_results") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        return [_normalize(item) for item in items]

    # ------------------------------------------------------------------ 核实

    async def verify(self, query: str, kind: str = "fact") -> ResearchEntry | None:
        """检索 → digest → LLM 核实 → 一条结论（None = 核查失败，不是「无问题」）。

        「未能核实」是**成功的核查结论**（照样落 entry）；只有调用失败才返回 None。

        **检索零结果 → 直接返回 None、零 LLM 调用**（seed 护栏 ``if not results: continue``）：
        没有检索结果就把空 digest 交给模型「核实」，模型只能凭训练记忆编结论——那份编造
        结论会被 append_research 写进 research.md，之后 verify_deep 命中缓存后零 HTTP
        零 LLM 直接复用 → 编造内容永久缓存。宁可不写，不许编。
        """
        if self._settings is None:
            return None
        text = str(query or "").strip()
        if not text:
            return None
        results = await self.search(text)
        if not results:
            log.warning("联网核查跳过（检索零结果，不凭记忆编造）：%s", text)
            return None
        sources = [r["url"] for r in results if r.get("url")]
        if not sources:
            # 检索有结果但**一条出处都没有**（provider 返回的条目没有 url/link）→ 结论无从
            # 核对，也不该进 research.md（进去后会被缓存复用，零 HTTP 零 LLM 当成「已核实」）。
            log.warning("联网核查跳过（检索结果无出处，不凭记忆编造）：%s", text)
            return None
        prompt = self._prompts.render("task.web_search_verify",
                                       query=text, results=_digest(results))
        try:
            conclusion = await self._complete(prompt, "task.web_search_verify")
        except Exception as exc:  # noqa: BLE001 - 核实失败降级（不阻断写作）
            log.warning("联网核查失败（已跳过）：%s（%s）", text, exc)
            return None
        return ResearchEntry(query=text, query_key=query_key_of(text), kind=kind,
                             conclusion=(conclusion or "").strip(),
                             sources=sources, hit_count=1)

    async def _followup_queries(self, goal: str, existing: str,
                                asked: list[str]) -> list[str]:
        """缺口检查 → 补充检索词（≤2 条；无缺口 → 空列表）。"""
        prompt = self._prompts.render("task.web_search_followup",
                                       goal=goal, existing=existing or "（无）",
                                       asked="、".join(asked))
        try:
            raw = await self._complete(prompt, "task.web_search_followup")
        except Exception as exc:  # noqa: BLE001 - 补搜失败降级（已有结论仍然可用）
            log.warning("联网核查补搜判定失败（按无缺口处理）：%s", exc)
            return []
        try:
            from harness.tools.base import extract_json
            value = extract_json(raw)
        except Exception:  # noqa: BLE001 - 解析失败按无缺口
            return []
        queries = value.get("queries") if isinstance(value, dict) else None
        if not isinstance(queries, list):
            return []
        out = [str(q).strip() for q in queries if isinstance(q, str) and q.strip()]
        return out[:2]

    async def verify_deep(self, *, root: Path, queries: list[str],
                          rounds: int | None = None) -> list[ResearchEntry]:
        """逐 query 深度核查：命中 research.md 直接复用；否则 verify + ≤rounds 轮补搜。

        全部结果落 research.md（命中旧条目只 hits+1）。

        **命中条目没有出处（sources 为空）→ 不复用、不返回、不重新落盘**（读侧与写侧
        同口径：写侧「无 sources → 不落库」，读侧不得把一条无出处的旧条目——手写或旧
        版本留下的——当成「已核实」复用）。该 query 本轮直接跳过。
        """
        if self._settings is None:
            return []
        limit = rounds if rounds is not None else self._settings.followup_rounds
        out: list[ResearchEntry] = []
        for query in queries:
            text = str(query or "").strip()
            if not text:
                continue
            key = query_key_of(text)
            hit = next((e for e in load_research(root) if _is_same_query(e.query_key, key)),
                       None)
            if hit is not None:
                if not hit.sources:
                    log.warning("research.md 命中条目无出处（不复用、不落库）：%s", text)
                    continue
                append_research(root, ResearchEntry(query=text, query_key=key, kind=hit.kind,
                                                    conclusion=hit.conclusion,
                                                    sources=list(hit.sources), hit_count=0))
                hit.hit_count += 1
                out.append(hit)
                continue
            entry = await self.verify(text)
            if entry is None:
                continue
            asked = [text]
            for _ in range(max(0, limit)):
                gap_queries = await self._followup_queries(text, entry.conclusion, asked)
                if not gap_queries:
                    break
                asked.extend(gap_queries)
                for q in gap_queries:
                    extra = await self.verify(q, kind="followup")
                    if extra is None:
                        continue
                    entry.conclusion = f"{entry.conclusion}\n{extra.conclusion}".strip()
                    for src in extra.sources:
                        if src not in entry.sources:
                            entry.sources.append(src)
            append_research(root, entry)
            out.append(entry)
        return out
