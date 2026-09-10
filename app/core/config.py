"""配置加载：读取 config.toml，提供类型化访问。"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from app.core.constants import CONFIG_FILE, DEFAULT_GEN_CONFIG


@dataclass(frozen=True)
class ModelSpec:
    """单个 Provider 的三档模型映射。"""
    lite: str = "deepseek-chat"
    standard: str = "deepseek-chat"
    pro: str = "deepseek-reasoner"

    def get(self, tier: str) -> str:
        return getattr(self, tier, self.standard)


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    models: ModelSpec


@dataclass(frozen=True)
class RoutingConfig:
    default_provider: str = "deepseek"
    default_tier: str = "standard"
    auto_downgrade: bool = True
    # 任务级指定：{prompt_key: {"provider": 厂商名, "model": 模型名}}
    # 例：正文续写用本地部署的 qwen3-14b → {"task.draft_continue": {"provider": "local", "model": "qwen3-14b"}}
    task_models: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingConfig:
    """语义检索嵌入（上下文组装用）：本地嵌入服务的 OpenAI 兼容 /v1/embeddings。

    provider 指向某个厂商（如 local）；model 为该服务上的嵌入模型名
    （如 all-MiniLM-L6-v2；中文内容建议 paraphrase-multilingual-MiniLM-L12-v2 / bge-small-zh-v1.5）。
    model 为空 = 不启用语义检索（回退关键词/FTS）。
    """
    provider: str = ""
    model: str = ""


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    theme: str = "system"
    gen_config: dict = field(default_factory=lambda: dict(DEFAULT_GEN_CONFIG))

    @property
    def default_provider(self) -> ProviderConfig:
        return self.providers[self.routing.default_provider]


def load_config(path: Path | None = None) -> AppConfig:
    """加载并解析 config.toml；文件缺失或字段不全时使用默认值。"""
    path = path or CONFIG_FILE
    raw: dict = {}
    if path.exists():
        with path.open("rb") as f:
            raw = tomllib.load(f)

    data_dir = Path(raw.get("app", {}).get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = path.parent / data_dir

    providers: dict[str, ProviderConfig] = {}
    for name, p in raw.get("llm", {}).get("providers", {}).items():
        models_raw = p.get("models", {})
        providers[name] = ProviderConfig(
            base_url=p.get("base_url", ""),
            api_key=p.get("api_key", ""),
            models=ModelSpec(
                lite=models_raw.get("lite", "deepseek-chat"),
                standard=models_raw.get("standard", "deepseek-chat"),
                pro=models_raw.get("pro", "deepseek-reasoner"),
            ),
        )
    if not providers:
        providers["deepseek"] = ProviderConfig(
            base_url="https://api.deepseek.com/v1",
            api_key="",
            models=ModelSpec(),
        )

    routing_raw = raw.get("llm", {}).get("routing", {})
    task_models: dict[str, dict] = {}
    for key, spec in routing_raw.get("task_models", {}).items():
        if isinstance(spec, dict) and spec.get("provider"):
            task_models[str(key)] = {
                "provider": str(spec["provider"]),
                "model": str(spec.get("model") or ""),
                "thinking": bool(spec.get("thinking", False)),
            }
    routing = RoutingConfig(
        default_provider=routing_raw.get("default_provider", "deepseek"),
        default_tier=routing_raw.get("default_tier", "standard"),
        auto_downgrade=routing_raw.get("auto_downgrade", True),
        task_models=task_models,
    )
    if routing.default_provider not in providers:
        routing = RoutingConfig(
            default_provider=next(iter(providers)),
            default_tier=routing.default_tier,
            auto_downgrade=routing.auto_downgrade,
            task_models=task_models,
        )

    gen_cfg = dict(DEFAULT_GEN_CONFIG)
    gen_cfg.update(raw.get("gen", {}).get("defaults", {}))

    emb_raw = raw.get("llm", {}).get("embedding", {})
    embedding = EmbeddingConfig(
        provider=str(emb_raw.get("provider", "")),
        model=str(emb_raw.get("model", "")),
    )

    return AppConfig(
        data_dir=data_dir,
        providers=providers,
        routing=routing,
        embedding=embedding,
        theme=raw.get("ui", {}).get("theme", "system"),
        gen_config=gen_cfg,
    )


# ---------------- 写回（GUI 模型设置保存用） ----------------
def _t(value: str) -> str:
    """TOML 字符串字面量。"""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return _t(v)


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    """把当前配置写回 config.toml。保留 data_dir/theme/gen 配置，重写 llm 段。"""
    path = path or CONFIG_FILE
    lines: list[str] = [
        "# 长篇小说 AI 创作工作台 —— 应用配置（可由「AI 工具 → 模型与路由设置」界面维护）",
        "# API Key 仅保存在本机，请勿提交到版本库",
        "",
        "[app]",
    ]
    try:
        rel = cfg.data_dir.relative_to(path.parent)
        data_dir_str = str(rel).replace("\\", "/")
    except ValueError:
        data_dir_str = str(cfg.data_dir).replace("\\", "/")
    lines.append(f"data_dir = {_t(data_dir_str)}")
    lines.append("")

    for name, p in cfg.providers.items():
        lines.append(f"[llm.providers.{_t(name)}]")
        lines.append(f"base_url = {_t(p.base_url)}")
        lines.append(f"api_key = {_t(p.api_key)}")
        lines.append(
            "models = { lite = " + _t(p.models.lite)
            + ", standard = " + _t(p.models.standard)
            + ", pro = " + _t(p.models.pro) + " }"
        )
        lines.append("")

    lines.append("[llm.routing]")
    lines.append(f"default_provider = {_t(cfg.routing.default_provider)}")
    lines.append(f"default_tier     = {_t(cfg.routing.default_tier)}")
    lines.append(f"auto_downgrade   = {_toml_value(bool(cfg.routing.auto_downgrade))}")
    lines.append("")

    if cfg.embedding and (cfg.embedding.provider or cfg.embedding.model):
        lines.append("[llm.embedding]")
        lines.append(f"provider = {_t(cfg.embedding.provider)}")
        lines.append(f"model    = {_t(cfg.embedding.model)}")
        lines.append("")

    for key, spec in (cfg.routing.task_models or {}).items():
        lines.append(f"[llm.routing.task_models.{_t(key)}]")
        lines.append(f"provider = {_t(spec.get('provider', ''))}")
        if spec.get("model"):
            lines.append(f"model = {_t(spec['model'])}")
        if spec.get("thinking"):
            lines.append("thinking = true")
        lines.append("")

    lines.append("[ui]")
    lines.append(f"theme = {_t(cfg.theme)}")
    lines.append("")

    lines.append("[gen.defaults]")
    for k, v in (cfg.gen_config or {}).items():
        if v is None:
            continue
        lines.append(f"{k} = {_toml_value(v)}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
