"""提示词回归评估 harness（P38，C3 子项②；设计 §3 P5 行「黄金样例集 + detect() 分数快照」）。

**做什么**：内置一组固定样例（黄金样例集），每个样例跑一次 LLM，输出过确定性 ``detect()``
（AI 味每千字加权分）+ JSON 合法性指标；结果落 ``<root>/.harness/prompt_eval/snapshot.json``
（带 model 与 prompt 模板 sha1）。**改提示词或换模型后同一批样例重跑** → 与上一快照逐样例
量化对比（Δscore / Δjson_ok / 模板变更），只报告不裁决（gate 由人读）。

**本地口径**（seed 无对应实现，按设计一行描述兑现）：
- 路由 = 提示词首行头解析（:func:`parse_routing`），**不进 WRITING_ROUTING**——该表有
  19 键数钉死测试，首行头惯例已有先例（character_create 等）。
- 样例调用是单次 ``llm.complete``（**不走 complete_structured 扩额阶梯**——评估要单次
  原始输出，重试会粉饰分数）。
- 快照按项目落盘（评估对象含项目级 prompt 覆盖，provider 配置也在项目 settings）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.llm.client import OpenAICompatClient
    from harness.prompts.loader import PromptLoader

__all__ = [
    "GOLDEN_SUITES",
    "EvalResult",
    "compare",
    "load_snapshot",
    "parse_routing",
    "prompt_hashes",
    "run_suite",
    "save_snapshot",
]

log = logging.getLogger("prompteval")

#: 快照落点（项目级）
SNAPSHOT_DIR = Path(".harness") / "prompt_eval"
SNAPSHOT_FILE = SNAPSHOT_DIR / "snapshot.json"

#: 首行路由头（builtin 提示词惯例；WRITING_ROUTING 之外的第二处路由源，仅评估用）
_ROUTING_RE = re.compile(
    r"^<!--\s*tier:\s*(\w+)\s*\|\s*temperature:\s*([\d.]+)\s*\|\s*max_tokens:\s*(\d+)\s*-->")

# ------------------------------------------------------------------ 黄金样例集

_CAST_CARDS = (
    "【陆昭】（主角） {\"personality\": \"隐忍\", \"motivation\": \"查清师父死因\", "
    "\"background\": \"山民出身\", \"appearance\": \"瘦高\", "
    "\"speech_style\": \"短句\", \"ability\": \"剑术\"}\n"
    "【沈青】（配角） {\"personality\": \"机敏\", \"motivation\": \"逃离师门\", "
    "\"background\": \"商贾之女\", \"appearance\": \"常着青衣\", "
    "\"speech_style\": \"快语\", \"ability\": \"医理\"}"
)

#: 黄金样例集：prompt 类 = builtin 键 + 固定 vars（少变量可渲染键）；
#: prose 类 = 固定 brief 自由写作段（detect 是核心指标）。
GOLDEN_SUITES: dict[str, dict] = {
    "golden_world_core": {
        "kind": "prompt",
        "prompt_key": "task.auto_world_core_draft",
        "vars": {"n": "6",
                 "premise": "少年在会退潮的世界里追赶一线陆地",
                 "constraints": "（无）",
                 "used": "（无——这是该作者的第一本书）",
                 "banned": "债务、账目"},
        "json": True,
    },
    "golden_master_plan": {
        "kind": "prompt",
        "prompt_key": "task.auto_master_plan",
        # master_plan 无首行路由头（路由代码硬编码 pro/0.5，tools/outline.py 钉死）→ 显式给
        "routing": ("pro", 0.5, 8192),
        "vars": {"instruction": "写一部两卷结构的长篇仙侠：第一卷主角入山学艺，"
                                 "第二卷卷入山门旧案"},
        "json": True,
    },
    "golden_relations": {
        "kind": "prompt",
        "prompt_key": "task.character_relations",
        "vars": {"cast_cards": _CAST_CARDS},
        "json": True,
    },
    "golden_prose_sample": {
        "kind": "prose",
        "brief": ("写一段 300 字左右的原创小说正文：两个赶夜路的角色在岔路口分食干粮，"
                  "没有打斗、没有金手指，只用动作与对话推进。直接输出正文，不要解释。"),
        "routing": ("standard", 0.7, 2048),
        "json": False,
    },
}


# ------------------------------------------------------------------ 数据结构

@dataclass
class EvalResult:
    """一个样例的一次评估结果；error 非 None 时指标为 None（该样例失败被隔离）。"""

    name: str
    chars: int | None
    score_per_k: float | None
    hits: int | None
    json_ok: bool | None
    error: str | None


# ------------------------------------------------------------------ 路由头解析

def parse_routing(template_text: str) -> tuple[str, float, int] | None:
    """解析 builtin 提示词首行路由头 ``<!-- tier: X | temperature: Y | max_tokens: Z -->``。

    坏头/无头 → None（调用方如实报错，不猜档位）。
    """
    m = _ROUTING_RE.match((template_text or "").strip())
    if not m:
        return None
    try:
        return m.group(1), float(m.group(2)), int(m.group(3))
    except ValueError:
        return None


def _tier_model(llm: OpenAICompatClient, tier: str) -> str:
    return getattr(llm.provider, tier, None) or llm.provider.standard


def prompt_hashes(prompts: PromptLoader, names: list[str]) -> dict[str, str]:
    """每样例渲染用模板原文的 sha1（改提示词即变；对比时明示「模板已变」）。"""
    out: dict[str, str] = {}
    for name in names:
        spec = GOLDEN_SUITES[name]
        if spec["kind"] == "prompt":
            raw = prompts.get(spec["prompt_key"])
        else:
            raw = spec["brief"]
        out[name] = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return out


# ------------------------------------------------------------------ 跑

async def _run_one(name: str, spec: dict, llm: OpenAICompatClient,
                   prompts: PromptLoader) -> EvalResult:
    from harness.tools.base import extract_json
    from harness.writing.flavor import detect

    if spec["kind"] == "prompt":
        template = prompts.get(spec["prompt_key"])
        # 路由：spec 显式 > 首行头（builtin 惯例）；都没有如实报错（不猜档位）
        routing = spec.get("routing") or parse_routing(template)
        if routing is None:
            return EvalResult(name, None, None, None, None,
                              f"路由不可得（spec 未给且首行头解析失败）：{spec['prompt_key']}")
        tier, temperature, max_tokens = routing
        prompt = prompts.render(spec["prompt_key"],
                                **{k: str(v) for k, v in spec["vars"].items()})
    else:
        tier, temperature, max_tokens = spec["routing"]
        prompt = spec["brief"]
    try:
        completion = await llm.complete(
            [{"role": "user", "content": prompt}],
            model=_tier_model(llm, tier), temperature=temperature,
            max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001 - harness 是诊断工具：单样例失败不拖垮全批
        log.warning("样例 %s 调用失败：%s", name, exc)
        return EvalResult(name, None, None, None, None, f"{type(exc).__name__}: {exc}")
    text = completion.text or ""
    report = detect(text)
    json_ok = (extract_json(text) is not None) if spec.get("json") else None
    return EvalResult(name=name, chars=len(text),
                      score_per_k=round(report.score, 4), hits=len(report.hits),
                      json_ok=json_ok, error=None)


async def run_suite(llm: OpenAICompatClient, prompts: PromptLoader,
                    names: list[str] | None = None) -> list[EvalResult]:
    """跑选定样例（缺省全部）；未知名 → KeyError（调用方输入错误，如实抛）。"""
    names = list(names) if names else list(GOLDEN_SUITES)
    results: list[EvalResult] = []
    for name in names:
        if name not in GOLDEN_SUITES:
            raise KeyError(f"未知样例：{name}（可用：{'、'.join(GOLDEN_SUITES)}）")
        results.append(await _run_one(name, GOLDEN_SUITES[name], llm, prompts))
    return results


# ------------------------------------------------------------------ 快照与对比

def save_snapshot(root: Path, results: list[EvalResult], *, model: str,
                  prompt_hashes: dict[str, str]) -> Path:
    path = Path(root) / SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    snap = {
        r.name: {
            "metrics": {"chars": r.chars, "score_per_k": r.score_per_k,
                        "hits": r.hits, "json_ok": r.json_ok, "error": r.error},
            "model": model,
            "prompt_sha1": prompt_hashes.get(r.name, ""),
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        for r in results
    }
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def load_snapshot(root: Path) -> dict | None:
    path = Path(root) / SNAPSHOT_FILE
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("评估快照损坏（按无基线处理）：%s（%s）", path, exc)
        return None
    return dict(value) if isinstance(value, dict) else None


def compare(results: list[EvalResult], previous: dict | None,
            *, prompt_hashes: dict[str, str]) -> list[str]:
    """与上一快照逐样例量化对比；只报告不裁决。行格式：

    ``样例名  score X.XX → Y.YY (Δ±Z.ZZ)  json_ok A→B  [模板已变]``
    无基线/无快照的样例如实标注「无基线」。
    """
    lines: list[str] = []
    for r in results:
        cur_hash = prompt_hashes.get(r.name, "")
        prev = (previous or {}).get(r.name)
        tmpl = ""
        if prev is not None and prev.get("prompt_sha1") and cur_hash \
                and prev["prompt_sha1"] != cur_hash:
            tmpl = "  [模板已变]"
        if r.error is not None:
            lines.append(f"{r.name}  本次失败（{r.error}）{tmpl}")
            continue
        score = "score ---" if r.score_per_k is None else f"score {r.score_per_k:.2f}"
        if prev is None:
            lines.append(f"{r.name}  {score}  无基线{tmpl}")
            continue
        pm = prev.get("metrics") or {}
        old_score = pm.get("score_per_k")
        if old_score is not None and r.score_per_k is not None:
            delta = r.score_per_k - old_score
            score = f"score {old_score:.2f} → {r.score_per_k:.2f}（Δ{delta:+.2f}）"
        old_json = pm.get("json_ok")
        json_part = "" if old_json is None or r.json_ok is None \
            else f"  json_ok {'T' if old_json else 'F'}→{'T' if r.json_ok else 'F'}"
        lines.append(f"{r.name}  {score}{json_part}{tmpl}")
    return lines
