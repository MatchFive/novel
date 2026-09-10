"""模型路由（设计文档 6.1.1）：按任务难度/重要程度选择档位与厂商。"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import AppConfig
from app.core.constants import TIER_LITE, TIER_PRO, TIER_STANDARD

# 任务 → (默认档位, 默认温度)
ROUTING_TABLE: dict[str, tuple[str, float]] = {
    # ---- lite：结构化、高吞吐 ----
    "memory_extract": (TIER_LITE, 0.3),
    "canon_extract": (TIER_LITE, 0.3),
    "chapter_summary": (TIER_LITE, 0.3),
    "context_reconcile": (TIER_LITE, 0.1),  # 编排前上下文核对（轻量，可指派本地模型）
    "context_select": (TIER_LITE, 0.2),     # 写作前上下文策划（轻量，可指派本地模型）
    "intent_classify": (TIER_LITE, 0.0),    # 对话意图分类（修改/讨论；推荐指派本地小模型，见 task_models）
    # ---- standard：常规生成 ----
    "idea_expand": (TIER_STANDARD, 0.7),
    "idea_evaluate": (TIER_STANDARD, 0.2),
    "idea_merge": (TIER_STANDARD, 0.6),
    "world_skeleton": (TIER_STANDARD, 0.6),
    "world_entry_expand": (TIER_STANDARD, 0.6),
    "chapter_split": (TIER_STANDARD, 0.4),
    "plot_plan": (TIER_STANDARD, 0.5),   # 卷→情节线规划
    "chapter_plan": (TIER_PRO, 0.6),  # 整卷拆章：高价值规划任务
    "beat_generate": (TIER_STANDARD, 0.6),
    "draft_continue": (TIER_STANDARD, 0.9),
    "draft_branches": (TIER_STANDARD, 0.8),
    "draft_review": (TIER_STANDARD, 0.2),
    "draft_revise": (TIER_STANDARD, 0.7),
    "draft_polish": (TIER_STANDARD, 0.5),
    # ---- pro：高价值、需准确性 ----
    "outline_generate": (TIER_PRO, 0.5),
    "outline_evaluate": (TIER_STANDARD, 0.4),
    "chapter_pace_check": (TIER_STANDARD, 0.4),
    "character_dialogue": (TIER_STANDARD, 0.8),
    "character_relations": (TIER_STANDARD, 0.6),
    "character_parse": (TIER_LITE, 0.2),
    "world_parse": (TIER_LITE, 0.2),
    "handbook_extract": (TIER_STANDARD, 0.3),  # 教程提炼：低温保 JSON 稳定
    "draft_refined": (TIER_PRO, 0.8),
    "consistency_scan": (TIER_PRO, 0.1),
    "command_execute": (TIER_PRO, 0.1),
    "agent_step": (TIER_STANDARD, 0.3),  # 实体 Agent 执行步（高频调用，不用 pro 拖速度）
    # ---- chat：创作讨论 ----
    "chat": (TIER_STANDARD, 0.8),
}

DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 8192  # 全局默认（主流云端/本地服务都支持；本地服务上限低时会自动封顶）

# 输出较长的结构化任务需要更大的 max_tokens（否则 JSON 被截断）
MAX_TOKENS_TABLE: dict[str, int] = {
    "handbook_extract": 8192,  # 10~30 条要点（标题+分类+内容），4096 容易截断
    "chapter_plan": 8192,      # 整卷拆章：多章 × 完整细纲字段
    "agent_step": 8192,        # 实体 Agent：整章细纲 JSON 较长
    "command_execute": 8192,   # 编排分解：长任务列表
    "draft_continue": 8192,    # 正文写作：单拍正文可达数千字
    "draft_revise": 8192,      # 修正输出的是完整修正稿，4096 必截断（残稿会替换好稿）
    "draft_polish": 8192,      # 润色同理
    "chat": 8192,              # 对话讨论长设定/细纲时也容易顶到上限
    "intent_classify": 64,     # 意图分类只需输出一个词
}


@dataclass(frozen=True)
class TaskSpec:
    """一次 LLM 任务的描述。"""

    module: str                # idea/world/outline/chapter/draft/memory/canon/check/command
    prompt_key: str            # 对应 prompt_templates.key
    tier: str | None = None    # 单次覆盖档位
    provider: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str
    tier: str
    temperature: float
    max_tokens: int
    thinking: bool = False  # 思考模式（DeepSeek thinking.type=enabled / Qwen3 enable_thinking）


def _table_lookup(table: dict, prompt_key: str, default):
    """按 prompt_key 查表：兼容带不带「task.」前缀两种写法。"""
    return (table.get(prompt_key)
            or table.get(prompt_key.removeprefix("task."))
            or default)


def resolve_model(cfg: AppConfig, task: TaskSpec, override: str | None = None) -> ResolvedModel:
    """按覆盖优先级解析实际调用配置（设计文档 6.1.1）。

    档位优先级：界面/调用显式覆盖 > 任务表设计档位 > 配置默认档位 > standard。
    （任务表是「按任务难度选档」的核心设计，必须优先于全局默认值；
    想全局降档可在配置里把三档模型指向同一个便宜模型。）
    """
    table_tier, default_temp = _table_lookup(
        ROUTING_TABLE, task.prompt_key, (None, DEFAULT_TEMPERATURE))

    tier = override or task.tier or table_tier or cfg.routing.default_tier
    if tier not in (TIER_LITE, TIER_STANDARD, TIER_PRO):
        tier = TIER_STANDARD

    provider_name = task.provider or cfg.routing.default_provider
    provider = cfg.providers.get(provider_name)
    if provider is None:
        provider = cfg.default_provider
        provider_name = cfg.routing.default_provider

    model = provider.models.get(tier)
    thinking = False
    # 任务级指定（模型设置界面配置）：某 prompt_key 固定使用某厂商的某模型
    # 例：task.draft_continue → 本地 qwen3-14b 写正文
    spec = (getattr(cfg.routing, "task_models", None) or {}).get(task.prompt_key)
    if spec:
        pname = spec.get("provider")
        if pname and pname in cfg.providers:
            provider_name = pname
            provider = cfg.providers[pname]
            model = spec.get("model") or provider.models.get(tier)
        thinking = bool(spec.get("thinking"))  # 任务级思考模式开关

    return ResolvedModel(
        provider=provider_name,
        model=model,
        tier=tier,
        temperature=task.temperature if task.temperature is not None else default_temp,
        max_tokens=task.max_tokens or _table_lookup(MAX_TOKENS_TABLE, task.prompt_key, DEFAULT_MAX_TOKENS),
        thinking=thinking,
    )
