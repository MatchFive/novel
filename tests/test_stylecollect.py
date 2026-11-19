"""P39：文风采集流水线（C3 收官项，seed StyleService.collect 本地移植，brief 见
sdd/2026-11-20-p39-style-collect）。

口径钉死（brief）：
- split_chunks 逐字移植（40,000 上限 / 1,800 目标片长 / 句边界截断）；
  逐片 style_chunk_analyze（lite/0.2/512）——keep=false 或 note 空的片不入库；
  style_profile（standard/0.3/2048）按**全量片段**重归纳。
- 片段落 `.harness/style_fragments.json`（append 续 seq / replace 清零）；
  档案写 `.harness/skills/style/SKILL.md`（项目 style skill，写作注入现成）。
"""
from __future__ import annotations

import json
from importlib import resources

import pytest

from harness.config import ProviderConfig
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.skills import scan_skills
from harness.store.project import ProjectStore
from harness.stylecollect import (
    StyleCollectError,
    analyze_chunks,
    build_profile,
    collect,
    read_fragments,
    render_style_skill,
    split_chunks,
)


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
    info = create_project(tmp_path / "lib", "文风书")
    return ProjectStore(info.path)


# ------------------------------------------------------------------ split_chunks


def test_split_chunks_paragraph_first_and_target():
    paras = [f"第{i}段。" + "字" * 400 for i in range(12)]
    chunks = split_chunks("\n\n".join(paras))
    assert len(chunks) >= 3                      # 12×~408 字 → ~1800/片
    assert all(len(c) <= 2000 for c in chunks)
    assert "第0段" in chunks[0]                  # 段落不切断（整段进同一片）


def test_split_chunks_long_paragraph_by_sentence():
    para = "".join(f"第{i}句收束。" for i in range(400))     # 一长段无空行
    chunks = split_chunks(para)
    assert len(chunks) > 1                                   # 按句末标点再切
    assert all(len(c) <= 2000 for c in chunks)
    assert all(c.endswith(("。", "”")) for c in chunks)      # 不留半句


def test_split_chunks_truncates_at_sentence_boundary():
    big = "完。" * 30000                                     # ~9 万字，超 4 万上限
    chunks = split_chunks(big)
    total = sum(len(c) for c in chunks)
    assert total <= 40000
    joined = "".join(chunks)
    assert joined.endswith("。")                              # 截在句边界


def test_split_chunks_empty():
    assert split_chunks("") == []
    assert split_chunks("   \n\n  ") == []


# ------------------------------------------------------------------ analyze


_ANALYZE_OK = json.dumps({"category": "对话", "note": "短句为主，对话不用引号", "keep": True},
                         ensure_ascii=False)
_ANALYZE_DROP = json.dumps({"category": "叙事", "note": "", "keep": True}, ensure_ascii=False)
_ANALYZE_KEEP_FALSE = json.dumps({"category": "环境", "note": "场景铺陈细腻", "keep": False},
                                 ensure_ascii=False)


@pytest.mark.asyncio
async def test_analyze_chunks_filters_and_routing(store):
    llm = FakeLLM([_Resp(_ANALYZE_OK), _Resp(_ANALYZE_DROP), _Resp(_ANALYZE_KEEP_FALSE),
                   _Resp(RuntimeError("boom"))])
    prompts = PromptLoader(home_dir=store.root)
    out = await analyze_chunks(llm, prompts, ["片一正文", "片二正文", "片三正文", "片四正文"])
    assert len(out) == 4
    kept = [a for a in out if a["keep"]]
    assert len(kept) == 1 and kept[0]["category"] == "对话"
    assert kept[0]["note"] == "短句为主，对话不用引号"
    call = llm.calls[0]
    assert call["model"] == "m-lite" and call["temperature"] == 0.2
    assert call["max_tokens"] == 512
    assert "片一正文" in call["prompt"]                 # chunk 逐字进 prompt


# ------------------------------------------------------------------ profile


def _frags(n: int) -> list[dict]:
    return [{"seq": i + 1, "category": "叙事" if i % 2 else "对话",
             "note": f"要点{i}", "text": f"正文{i}" + "字" * 350, "chars": 360}
            for i in range(n)]


_PROFILE_OK = json.dumps({"narrative": "第三人称限知，短句", "dialogue": "对话占比低",
                          "signature_moves": ["用气味开场"], "avoid": ["不用不禁"]},
                         ensure_ascii=False)


@pytest.mark.asyncio
async def test_build_profile_vars_and_routing(store):
    llm = FakeLLM([_Resp(_PROFILE_OK)])
    profile = await build_profile(llm, PromptLoader(home_dir=store.root), _frags(3))
    assert profile["narrative"] == "第三人称限知，短句"
    assert profile["source_chars"] == 1080 and profile["chunk_count"] == 3
    call = llm.calls[0]
    assert call["model"] == "m-std" and call["temperature"] == 0.3
    assert call["max_tokens"] == 2048
    assert "3" in call["prompt"]                       # chunk_count
    assert "1080" in call["prompt"]                    # source_chars
    assert "要点0" in call["prompt"] and "【对话】" in call["prompt"]


@pytest.mark.asyncio
async def test_build_profile_failure_raises(store):
    llm = FakeLLM([RuntimeError("down")])
    with pytest.raises(StyleCollectError):
        await build_profile(llm, PromptLoader(home_dir=store.root), _frags(2))


# ------------------------------------------------------------------ render


def test_render_style_skill_seed_format():
    profile = {"narrative": "narr", "dialogue": "dlg",
               "signature_moves": ["move"], "avoid": ["avoid1"]}
    text = render_style_skill(profile, source_chars=2)
    assert text.startswith("---\nname: style\n")
    assert "【文风档案（取自 2 字样本，务必贯彻）】" in text
    assert "叙事：narr" in text and "对话：dlg" in text
    assert "标志手法：1. move" in text and "避用：avoid1" in text
    assert "只模仿这份档案描述的笔法与语言质感" in text


# ------------------------------------------------------------------ 端到端 collect


_SAMPLE = "\n\n".join(f"第{i}段样本正文。" + "字" * 300 for i in range(6))


@pytest.mark.asyncio
async def test_collect_writes_style_skill_and_fragments(store):
    notes = json.dumps({"category": "叙事", "note": "要点", "keep": True}, ensure_ascii=False)
    llm = FakeLLM([_Resp(notes)] * 6 + [_Resp(_PROFILE_OK)])
    summary = await collect(store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
                            text=_SAMPLE)
    assert summary["kept"] == 2 and summary["total_chunks"] == 2   # _SAMPLE 6×308 字 → 2 片
    skill = store.root / ".harness" / "skills" / "style" / "SKILL.md"
    assert skill.is_file()
    found = scan_skills(store.root, user_dir=store.root / "nope")
    assert "style" in found                        # 注入侧（skills 扫描器）读得到
    assert read_fragments(store.root)[0]["seq"] == 1


@pytest.mark.asyncio
async def test_collect_append_then_replace(store):
    prompts = PromptLoader(home_dir=store.root)
    notes = json.dumps({"category": "叙事", "note": "要点", "keep": True}, ensure_ascii=False)
    llm1 = FakeLLM([_Resp(notes)] * 2 + [_Resp(_PROFILE_OK)])
    await collect(store=store, llm=llm1, prompts=prompts, text=_SAMPLE)
    llm2 = FakeLLM([_Resp(notes)] + [_Resp(_PROFILE_OK)])
    await collect(store=store, llm=llm2, prompts=prompts, text="另一段样本。" * 200)
    seqs = [f["seq"] for f in read_fragments(store.root)]
    assert seqs == [1, 2, 3]                       # append 续 seq
    llm3 = FakeLLM([_Resp(notes)] + [_Resp(_PROFILE_OK)])
    await collect(store=store, llm=llm3, prompts=prompts, text="覆盖段。" * 200,
                  replace=True)
    seqs = [f["seq"] for f in read_fragments(store.root)]
    assert seqs == [1]                             # replace 清零


@pytest.mark.asyncio
async def test_collect_empty_text_raises(store):
    with pytest.raises(StyleCollectError):
        await collect(store=store, llm=FakeLLM([]),
                      prompts=PromptLoader(home_dir=store.root), text="  \n ")


# ------------------------------------------------------------------ REPL wiring


def test_cmd_stylecollect_stub_notice(store, capsys):
    from harness import repl

    class _Sess:
        def make_components(self):
            return None

    repl._cmd_stylecollect(_Sess(), store, "x.txt")
    assert "未启动" in capsys.readouterr().out


def test_cmd_stylecollect_missing_file(store, capsys, tmp_path):
    from harness import repl

    class _Sess:
        def make_components(self):
            return object()          # 非 None 即可（不该被用到读文件之前）

    repl._cmd_stylecollect(_Sess(), store, str(tmp_path / "不存在.txt"))
    assert "不存在" in capsys.readouterr().out


# ------------------------------------------------------------------ 提示词钉死

_SEED_CHUNK = (
    "你在为一部长篇小说的「文风样本」做逐片采样。只分析这段文字的**写法**（遣词、句式、节奏、对话处理），不要概括剧情、不要评价好坏、不要摘抄情节。\n"
    "【片段原文】\n"
    "{{chunk}}\n"
    "【要求】\n"
    "① category 从这五类里选最贴切的一类：叙事（以叙述/描写为主）、对话（以人物对白为主）、心理（以内心活动为主）、动作（以动作/打斗为主）、环境（以景物/场景铺陈为主）；\n"
    "② note 用不超过 40 字点明这段最值得模仿的写法特征（如「短句为主，句末用句号收束，少用形容词」「对话不用引号，靠动作与停顿区分说话人」）；\n"
    "③ keep：这段是不是可作样本的正文？前言/目录/作者的话/注释/菜单式罗列等非正文内容填 false。\n"
    "【输出格式】严格 JSON：{\"category\": \"叙事|对话|心理|动作|环境\", \"note\": \"不超过40字的写法要点\", \"keep\": true}"
)

_SEED_PROFILE = (
    "你把一批「文风样本」的逐片标注归纳成一份可直接指导写作的文风档案。样本共 {{chunk_count}} 片、约 {{source_chars}} 字。\n"
    "【逐片标注汇总】\n"
    "{{summaries}}\n"
    "【各类代表片段开头（供你复核细节）】\n"
    "{{samples}}\n"
    "【要求】\n"
    "① narrative（150~250 字）：叙事特点——人称与时态、句子长短与节奏、段落密度、视角距离（贴人物还是拉开）、叙述腔调、标点与断句习惯、比喻/白描的偏好；\n"
    "② dialogue（100~200 字）：对话风格——**必须单独写清**：对话在全文中的占比与密度、引号样式（「」/“”/无引号）、说话人如何标注（“他说”/动作夹叙/不加标注）、不同角色的语气差异、口癖与语气词习惯、对话与动作/心理描写的夹叙比例；\n"
    "③ signature_moves：3~6 条最有辨识度的标志性手法（每条 ≤20 字，如「用气味或温度开场」）；\n"
    "④ avoid：3~6 条从样本里能反推出的「作者不会这么写」（每条 ≤20 字，如「不用‘不禁’‘宛如’这类书面词」）；\n"
    "⑤ 只写可操作的写法，不要概括剧情、人物或主题。\n"
    "【输出格式】严格 JSON：{\"narrative\": \"...\", \"dialogue\": \"...\", \"signature_moves\": [\"...\"], \"avoid\": [\"...\"]}"
)


def _builtin(key: str) -> str:
    ref = resources.files("harness.prompts").joinpath("builtin").joinpath(f"{key}.md")
    return ref.read_text(encoding="utf-8")


def test_prompt_chunk_seed_verbatim_and_routing():
    text = _builtin("task.style_chunk_analyze")
    assert "\r" not in text
    assert text.split("\n")[0] == "<!-- tier: lite | temperature: 0.2 | max_tokens: 512 -->"
    assert _SEED_CHUNK in text


def test_prompt_profile_seed_verbatim_and_routing():
    text = _builtin("task.style_profile")
    assert "\r" not in text
    assert text.split("\n")[0] == "<!-- tier: standard | temperature: 0.3 | max_tokens: 2048 -->"
    assert _SEED_PROFILE in text
    import re as _re
    assert set(_re.findall(r"\{\{(\w+)\}\}", text)) == \
        {"summaries", "samples", "chunk_count", "source_chars"}
