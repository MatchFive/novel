"""两级 TOML 配置加载：全局 ``~/.harness/config.toml`` + 项目级 ``.harness/config.toml``。

按 key 浅合并，项目级赢。文件不存在 → 全默认值。
REPL（Task 9）与 LLM 客户端（Task 7）消费本模块的产物。
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_TIER = "standard"
_DEFAULT_WEB_VERIFY = True

_PROVIDER_FIELDS = ("base_url", "api_key", "lite", "standard", "pro")

# [search] 段的字段（联网核查，P5 Task 9）。字段裁剪自 seed 的 web 配置：
# include_content/max_queries/near_dup_* 不搬（本地不做嵌入近义合并，见 P4 裁定 3）。
_SEARCH_FIELDS = ("provider", "base_url", "api_key", "limit", "timeout_seconds",
                  "followup_rounds")


@dataclass
class ProviderConfig:
    base_url: str
    api_key: str
    lite: str
    standard: str
    pro: str


@dataclass
class SearchSettings:
    """联网核查配置（``[search]`` 段，缺省段 → ``Settings.search is None`` → web 工具回绝）。

    段缺省时**不建**配置对象（None）——「未配置」与「配了但错」是两种状态，前者零 HTTP
    零 LLM 直接回绝。
    """

    provider: str = "kimi_api"
    base_url: str = "https://api.moonshot.cn/v1"
    api_key: str = ""
    limit: int = 5
    timeout_seconds: int = 15
    followup_rounds: int = 2


@dataclass
class Settings:
    library_root: Path
    providers: dict[str, ProviderConfig]
    default_provider: str
    default_tier: str
    web_verify: bool
    # 联网核查配置（可选，**放最后带默认值**——既有手工构造 Settings 的调用点不破）
    search: SearchSettings | None = None


def _read_toml(path: Path) -> dict:
    """读 TOML；文件不存在返回空 dict，其余解析错误照常抛出。"""
    if path is None or not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings(
    global_path: Path | None = None,
    project_path: Path | None = None,
) -> Settings:
    """加载配置。global_path 缺省为 ``~/.harness/config.toml``（调用期求值）。

    两级数据按 key 浅合并，项目级赢。
    """
    if global_path is None:
        global_path = Path.home() / ".harness" / "config.toml"
    g = _read_toml(global_path)
    p = _read_toml(project_path)
    merged = {**g, **p}

    providers: dict[str, ProviderConfig] = {}
    for name, table in merged.get("providers", {}).items():
        missing = [k for k in _PROVIDER_FIELDS if k not in table]
        if missing:
            raise ValueError(
                f"provider {name!r} 缺字段: {', '.join(missing)}"
            )
        providers[name] = ProviderConfig(**{k: table[k] for k in _PROVIDER_FIELDS})

    search = None
    raw_search = merged.get("search")
    if isinstance(raw_search, dict):
        unknown = [k for k in raw_search if k not in _SEARCH_FIELDS]
        if unknown:
            raise ValueError(f"[search] 段含未知字段: {', '.join(sorted(unknown))}")
        search = SearchSettings(**{k: v for k, v in raw_search.items()})

    # 缺省 library_root **调用期**求值 Path.home()（模块级常量会在 import 时钉死
    # 真实 home——测试隔离 patch Path.home 后默认值仍是旧 home，P26）。
    return Settings(
        library_root=Path(merged.get("library_root") or (Path.home() / "novels")),
        providers=providers,
        default_provider=merged.get("default_provider", ""),
        default_tier=merged.get("default_tier", _DEFAULT_TIER),
        web_verify=merged.get("web_verify", _DEFAULT_WEB_VERIFY),
        search=search,
    )
