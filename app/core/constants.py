"""应用常量。"""
import sys
from pathlib import Path

APP_NAME = "长篇小说 AI 创作工作台"
APP_VERSION = "0.1.0"          # M0 骨架版
ORG_NAME = "NovelStudio"

# 路径（以项目根为准；PyInstaller 打包后以 exe 所在目录为用户数据目录）
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent   # exe 旁：config.toml / data
    _RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))  # 打包内置资源
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    _RESOURCE_ROOT = PROJECT_ROOT

CONFIG_FILE = PROJECT_ROOT / "config.toml"

# 数据库
DB_FILENAME = "novel.db"
WORKFLOW_STATE_DB = "workflow_state.db"   # LangGraph checkpoint 库（M3 启用）
BACKUP_DIR = "backups"
BACKUP_KEEP = 30

# 模型档位
TIER_LITE = "lite"
TIER_STANDARD = "standard"
TIER_PRO = "pro"
ALL_TIERS = (TIER_LITE, TIER_STANDARD, TIER_PRO)

# 默认生成配置（见设计文档 3.14）
DEFAULT_GEN_CONFIG = {
    "target_words": 3000,
    "word_tolerance": 0.10,
    "style": "",
    "pov": "third",               # first / third
    "pace": "medium",             # fast / medium / slow
    "dialogue_density": "medium", # high / medium / low
    "description_density": "medium",
    "title_style": "narrative",   # narrative / suspense / numbered
    "temperature": None,          # None=按任务类型
    "memory_strength": "standard",# strict / standard / loose
    "quality_mode": "draft",      # draft / refined
    "tier_override": None,        # lite / standard / pro / None=跟随路由
    "draft_mode": "beats",        # beats=逐节拍生成 / whole=整章单发（SFT 细纲→整章模型推荐）
}

# 内置 Prompt 模板文件（resources/prompts.json 加载后写入 prompt_templates 表）
PROMPTS_JSON = _RESOURCE_ROOT / "resources" / "prompts.json"

# 视觉资源（图标 / 主题样式表）
ICON_PNG = _RESOURCE_ROOT / "resources" / "icon.png"
ICON_ICO = _RESOURCE_ROOT / "resources" / "icon.ico"
STYLE_FILE = _RESOURCE_ROOT / "resources" / "style.qss"
TUTORIAL_TXT = _RESOURCE_ROOT / "resources" / "tutorial_raw.txt"
