"""写作闸（spec §4.7 / Task 19）：write_plot 按章硬拦——无卡主要角色 → 待卡跳章 / 整步 NOPROGRESS。

假 provider + 假 llm（复用 test_writing 的 make_llm/default_router——写正文的既有 mock 链）。
fixture：情节 1-1 两章——第1章 units 含龙套「守阶弟子」（无卡也**不拦**，Task 3 分层），
第2章 units 含「苏晚」（无卡，beats 占比 ≥3% → 重要配角候选 → 第2章被拦）。
判定口径唯一源 = harness.characters（spec X3），本文件不断言其内部口径。
"""
from types import SimpleNamespace

import pytest
from test_writing import PLOT, Collector, beats, chapter, default_router, run_tool, write_chapters

# 借 test_writing 的 make_llm fixture 给 fx/spy_llm 按名注入（pytest fixture 机制，
# 代码内无直接引用——别名导入避免与 fixture 参数名撞 F811；赋值回 make_llm 让
# pytest 以参数名注册这个借来的 fixture）。
from test_writing import make_llm as make_llm_fixture

make_llm = make_llm_fixture

from harness.projects import create_project
from harness.store.project import ProjectStore


def _unit(n, characters):
    """一个剧情单元（与 tests/test_characters.py 同款形状）。"""
    return {"seq": n, "time": "清晨", "location": "山脚", "characters": characters,
            "event": f"事件{n}", "covers": f"覆盖{n}"}


def _gate_chapter(seq, title, beat_events, units):
    """既有 chapter() 骨架 + gate 判据用的 meta.units。"""
    doc = chapter(seq, title, beats(*beat_events))
    doc.meta["units"] = units
    return doc


@pytest.fixture
def fx(tmp_path, make_llm):
    """两章情节 1-1：分母 50 拍——苏晚 3/50=6% ≥ 阈值（候选），守阶弟子 1/50=2% < 阈值（龙套）。"""
    info = create_project(tmp_path, "闸书")
    store = ProjectStore(info.path)
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    store.upsert_plots(1, [dict(PLOT)])
    ch1_units = [_unit(i, []) for i in range(1, 21)]
    ch1_units[4]["characters"] = ["守阶弟子"]
    ch2_units = [_unit(20 + i, ["苏晚"] if i <= 3 else []) for i in range(1, 31)]
    write_chapters(store, [
        _gate_chapter(1, "山门", ("一之起", "一之承"), ch1_units),
        _gate_chapter(2, "雪落", ("二之起", "二之承"), ch2_units),
    ])
    llm, calls = make_llm(default_router)
    return SimpleNamespace(store=store, llm=llm, calls=calls, emit=Collector(),
                           args={"plot_id": "1-1"})


@pytest.fixture
def spy_llm(make_llm):
    """路由即炸的假 llm：写作闸拦下时**零 LLM 调用**——被调一次即测试失败。"""
    def router(body):  # pragma: no cover - 闸不该触发任何 LLM 调用
        raise AssertionError(
            f"写作闸拦下时不得调用 LLM：{body['messages'][-1]['content'][:60]}…")

    return make_llm(router)[0]


def _block_chapter_one(fx):
    """把苏晚也写进第1章 units → 第1章同样被拦（供全拦用例复用）。"""
    doc = fx.store.read_chapter(1)
    doc.meta["units"][0]["characters"] = ["苏晚"]
    fx.store.write_chapter(doc)


async def test_gate_blocks_only_uncarded_chapter(tmp_path, fx):
    """部分章被拦**不整步拒绝**：第1章（龙套无卡）照写，第2章（苏晚无卡）跳——观测含「待卡」。"""
    result = await run_tool(fx.store, fx.llm, fx.emit, fx.args)

    assert fx.store.read_chapter(1).meta["status"] == "done"      # 第1章写了
    two = fx.store.read_chapter(2)
    assert two.meta["status"] == "planned" and two.body == ""     # 第2章零写盘
    assert "第2章：待卡（苏晚 无角色卡）——去角色视图建卡" in result.text
    assert "拦下 1 章" in result.text
    assert "成功 1 章" in result.text
    assert "NOPROGRESS" not in result.text                        # 不整步拒绝


async def test_gate_all_blocked_is_nopgrogress(tmp_path, fx):
    """全部待写章被拦 → 整步 NOPROGRESS 前缀；观测说清拦的是谁（角色类型/章拍数/首次出场）。"""
    _block_chapter_one(fx)
    result = await run_tool(fx.store, fx.llm, fx.emit, fx.args)

    assert result.text.startswith("NOPROGRESS: write_plot 未执行")
    assert "第1章有 1 个角色还没有角色卡" in result.text
    assert "他们会出现在第1章「山门」" in result.text
    assert "按你的规矩：戏份多的角色要先建卡再写正文。" in result.text
    # member 详情：苏晚此时 4 拍 / 2 章 / 首次出场第1章
    assert "苏晚（重要配角，2章/4拍，首次出场第0001章）" in result.text
    # 逐章 skip 行照列
    assert "第1章：待卡（苏晚 无角色卡）——去角色视图建卡" in result.text
    assert "第2章：待卡（苏晚 无角色卡）——去角色视图建卡" in result.text
    for seq in (1, 2):                                            # 零写盘
        doc = fx.store.read_chapter(seq)
        assert doc.meta["status"] == "planned" and doc.body == ""
    assert result.usage is None                                   # 零 usage


async def test_gate_zero_llm_zero_write(tmp_path, fx, spy_llm):
    """全拦 → llm 调用 0 次、无新文件（无正文/审核快照/章记忆/卷 digest）。"""
    _block_chapter_one(fx)
    result = await run_tool(fx.store, spy_llm, Collector(), fx.args)

    assert result.text.startswith("NOPROGRESS: write_plot 未执行")
    assert result.usage is None
    root = fx.store.root
    # 零写盘：memory/ 是项目脚手架预建的（state.md），闸不得新增任何文件
    assert sorted(p.name for p in (root / "memory").iterdir()) == ["state.md"]
    assert not (root / "memory" / "digests").exists()
    assert not (root / ".harness" / "review").exists()
    for seq in (1, 2):
        doc = fx.store.read_chapter(seq)
        assert doc.meta["status"] == "planned" and doc.body == ""


async def test_walkon_does_not_block(tmp_path, fx):
    """Review Focus #5：龙套天然不拦——苏晚补上卡后两章全写，观测零「待卡」。"""
    fx.store.upsert_character({"name": "苏晚", "role_type": "major", "aliases": [],
                               "profile": {}, "arc": {"stages": []}})

    result = await run_tool(fx.store, fx.llm, fx.emit, fx.args)

    assert "写作完成" in result.text and "成功 2 章" in result.text
    assert "待卡" not in result.text and "NOPROGRESS" not in result.text
    assert fx.store.read_chapter(1).meta["status"] == "done"
    assert fx.store.read_chapter(2).meta["status"] == "done"


async def test_chapter_not_needing_uncarded_writes(tmp_path, fx):
    """口径1 按章：seq 范围只含第1章（不需要苏晚）→ 照常写作、观测无「待卡」。"""
    result = await run_tool(fx.store, fx.llm, fx.emit, {"plot_id": "1-1", "seq_to": 1})

    assert "成功 1 章" in result.text
    assert "待卡" not in result.text and "NOPROGRESS" not in result.text
    assert fx.store.read_chapter(1).meta["status"] == "done"
    two = fx.store.read_chapter(2)                                # 范围外不动
    assert two.meta["status"] == "planned" and two.body == ""
