"""P38：提示词回归评估 harness（C3 子项②，brief 见 sdd/2026-11-19-p38-prompt-eval-harness）。

口径钉死（brief）：
- 黄金样例集内置 GOLDEN_SUITES：prompt 类（builtin 键 + 固定 vars）/ prose 类
  （固定 brief + routing standard/0.7/2048）。
- 路由 = 提示词首行头 parse_routing（不进 WRITING_ROUTING——19 键数有钉死测试）。
- 指标/样例：chars / score_per_k（detect 每千字加权分）/ hits / json_ok；
  LLM 失败 → error 字符串，其余样例照跑。
- 快照 <root>/.harness/prompt_eval/snapshot.json：{suite: {metrics, ts, model,
  prompt_sha1}}；对比打 Δscore/Δjson_ok/模板变更三列，只报告不裁决。
"""
from __future__ import annotations

import json

import pytest

from harness.config import ProviderConfig
from harness.projects import create_project
from harness.prompteval import (
    GOLDEN_SUITES,
    EvalResult,
    compare,
    load_snapshot,
    parse_routing,
    run_suite,
    save_snapshot,
)
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append({"model": model, "temperature": temperature,
                           "max_tokens": max_tokens,
                           "prompt": messages[-1]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self):
        pass


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path / "lib", "评估书")
    return ProjectStore(info.path)


# ------------------------------------------------------------------ parse_routing


def test_parse_routing_three_tiers():
    assert parse_routing("<!-- tier: standard | temperature: 1.0 | max_tokens: 8192 -->\n正文") \
        == ("standard", 1.0, 8192)
    assert parse_routing("<!-- tier: pro | temperature: 0.2 | max_tokens: 4096 -->\n正文") \
        == ("pro", 0.2, 4096)
    assert parse_routing("<!-- tier: lite | temperature: 0.7 | max_tokens: 2048 -->\n正文") \
        == ("lite", 0.7, 2048)


def test_parse_routing_bad_header_returns_none():
    assert parse_routing("没有头的正文") is None
    assert parse_routing("<!-- tier: standard | temperature: abc | max_tokens: 8192 -->") is None
    assert parse_routing("") is None


# ------------------------------------------------------------------ run_suite


def _prose_sample() -> str:
    """一段干净的叙述文本（detect 低分）。"""
    return ("山道很窄，陆昭走在前头，沈青落后半步。风从谷口灌进来，吹得火把忽明忽暗。"
            "谁也没有先开口。走到岔路口，陆昭停下，把干粮袋解下来递过去，"
            "只说了一句：你先吃。沈青接过来，咬了一口，又递回去。"
            "远处有狼嚎，两声，隔得很久。他们熄了火，摸黑赶路。")


def test_golden_suites_have_unique_names_and_kinds():
    names = list(GOLDEN_SUITES)
    assert len(names) == len(set(names))
    for name, spec in GOLDEN_SUITES.items():
        assert spec["kind"] in ("prompt", "prose"), name
        if spec["kind"] == "prompt":
            assert isinstance(spec["prompt_key"], str) and isinstance(spec["vars"], dict)
        else:
            assert isinstance(spec["brief"], str) and isinstance(spec["routing"], tuple)


@pytest.mark.asyncio
async def test_run_suite_prompt_kind_renders_vars_and_scores(store):
    llm = FakeLLM([_Resp(json.dumps({"candidates": [{"core": "c", "metaphor": "m",
                                                     "medium": "md", "godstate": "g",
                                                     "cost": "co", "why": "w"}]},
                                    ensure_ascii=False))])
    prompts = PromptLoader(home_dir=store.root)
    results = await run_suite(llm, prompts, ["golden_world_core"])
    assert len(results) == 1
    r = results[0]
    assert r.name == "golden_world_core" and r.error is None
    assert r.json_ok is True
    assert r.chars > 0 and r.score_per_k >= 0.0 and r.hits >= 0
    # vars 逐字进 prompt（render 断言：premise 固定值在发给模型的文本里）
    assert GOLDEN_SUITES["golden_world_core"]["vars"]["premise"] in llm.calls[0]["prompt"]
    # 路由来自首行头（standard/1.0/8192），model 用 provider 对应档
    assert llm.calls[0]["model"] == "m-std"
    assert llm.calls[0]["temperature"] == 1.0
    assert llm.calls[0]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_run_suite_prose_kind_uses_brief_and_detect(store):
    llm = FakeLLM([_Resp(_prose_sample())])
    prompts = PromptLoader(home_dir=store.root)
    results = await run_suite(llm, prompts, ["golden_prose_sample"])
    r = results[0]
    assert r.error is None and r.json_ok is None              # prose 类不判 JSON
    assert GOLDEN_SUITES["golden_prose_sample"]["brief"] in llm.calls[0]["prompt"]
    assert llm.calls[0]["model"] == "m-std"                    # routing standard/0.7/2048
    assert llm.calls[0]["temperature"] == 0.7
    assert llm.calls[0]["max_tokens"] == 2048
    assert r.chars == len(_prose_sample())
    assert r.score_per_k < 6.0                                 # 干净文本低分（detect 阈值 6）


@pytest.mark.asyncio
async def test_run_suite_bad_json_flagged(store):
    llm = FakeLLM([_Resp("这不是 JSON 也不是设定")])
    prompts = PromptLoader(home_dir=store.root)
    r = (await run_suite(llm, prompts, ["golden_master_plan"]))[0]
    assert r.json_ok is False


@pytest.mark.asyncio
async def test_run_suite_failure_isolated(store):
    llm = FakeLLM([RuntimeError("api down"), _Resp(_prose_sample())])
    prompts = PromptLoader(home_dir=store.root)
    names = [n for n in GOLDEN_SUITES][:2]
    results = await run_suite(llm, prompts, names)
    assert results[0].error is not None and results[0].score_per_k is None
    assert results[1].error is None                          # 其余样例照跑


@pytest.mark.asyncio
async def test_run_suite_unknown_name_raises(store):
    llm = FakeLLM([])
    with pytest.raises(KeyError):
        await run_suite(llm, PromptLoader(home_dir=store.root), ["nope"])


# ------------------------------------------------------------------ 快照与对比


def test_snapshot_roundtrip(store):
    results = [EvalResult(name="golden_world_core", chars=10, score_per_k=1.5,
                          hits=2, json_ok=True, error=None)]
    save_snapshot(store.root, results, model="m-std", prompt_hashes={"golden_world_core": "abc123"})
    snap = load_snapshot(store.root)
    entry = snap["golden_world_core"]
    assert entry["metrics"]["score_per_k"] == 1.5
    assert entry["model"] == "m-std"
    assert entry["prompt_sha1"] == "abc123"
    assert entry["ts"]


def test_snapshot_missing_returns_none(store):
    assert load_snapshot(store.root) is None


def test_compare_deltas_and_template_change():
    prev = {"a": {"metrics": {"score_per_k": 2.0, "json_ok": True},
                  "model": "m", "prompt_sha1": "h1", "ts": "t"},
            "b": {"metrics": {"score_per_k": 5.0, "json_ok": False},
                  "model": "m", "prompt_sha1": "h2", "ts": "t"}}
    now = [EvalResult(name="a", chars=1, score_per_k=3.5, hits=0, json_ok=True, error=None),
           EvalResult(name="b", chars=1, score_per_k=4.0, hits=0, json_ok=True, error=None),
           EvalResult(name="c", chars=1, score_per_k=1.0, hits=0, json_ok=None, error=None)]
    lines = compare(now, prev, prompt_hashes={"a": "h1", "b": "h2-changed", "c": "h3"})
    text = "\n".join(lines)
    assert "a" in text and "+1.50" in text                 # Δscore 升
    assert "b" in text and "-1.00" in text                 # Δscore 降
    assert "模板已变" in text                                 # prompt_sha1 变化明示
    assert "c" in text and "无基线" in text                 # 新样例如实标注


def test_compare_no_previous():
    now = [EvalResult(name="a", chars=1, score_per_k=1.0, hits=0, json_ok=None, error=None)]
    lines = compare(now, None, prompt_hashes={})
    assert any("无基线" in line for line in lines)


# ------------------------------------------------------------------ REPL wiring


def test_cmd_prompteval_stub_notice(store, capsys):
    from harness import repl

    class _Sess:
        def make_components(self):
            return None

    repl._cmd_prompteval(_Sess(), store, "")
    assert "未启动" in capsys.readouterr().out
