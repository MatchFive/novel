# novel-harness

终端小说写作 harness —— AI 员工在本地 Markdown 书库里写长篇小说（规划 → 写作 → 记忆 → 验收 → 修订）。

## 快速开始

```bash
pip install -e ".[dev]"      # 安装（含 pytest / ruff 开发依赖）

harness                       # 交互式项目选择器（dsh 式：新建 / 选书）
harness <项目路径>             # 直接打开指定项目，进入只读速览 REPL
harness --library <库路径>     # 指定项目库根（默认取配置 library_root）
```

建项目流程示例：

```bash
harness            # 在选择器里选「新建」，输入书名「大道朝天」
# 或以库路径调用：
python -c "from pathlib import Path; from harness.projects import create_project; create_project(Path.home() / 'novels', '大道朝天')"
```

REPL 内用斜杠命令浏览（`/state` 看进度、`/quit` 退出）；章节正文就是
`chapters/NNNN-标题.md` 文件，可用任何编辑器手改，store 重读时合并保留未触碰区间。

## 配置

全局配置 `~/.harness/config.toml`（项目级 `.harness/config.toml` 按 key 浅合并，项目级赢）：

```toml
library_root = "~/novels"        # 项目库根
web_verify = true                # 联网校验开关
default_provider = "deepseek"    # 默认 LLM provider
default_tier = "standard"        # 默认档位：lite / standard / pro

[providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key = "sk-..."
lite = "deepseek-chat"           # 三档模型：轻量活（摘要/校对）
standard = "deepseek-chat"       # 标准活（初稿）
pro = "deepseek-reasoner"        # 重活（结构审查/大改）
```

每个 provider 必须五字段齐全（`base_url` / `api_key` / `lite` / `standard` / `pro`）。

## 目录契约速览

项目即文件夹，人可全程手改；完整契约见设计文档 §2.2。速览：

```
<书名>/
  README.md            # 项目卡（书名/题材/进度）
  world.md             # 世界观条目
  characters/          # 角色卡（名字.md，如 characters/陆昭.md）
  outline/arcs.md      # 主线
  outline/plots/vol-NN.md  # 卷级情节
  chapters/NNNN-标题.md    # 章节正文（front-matter + Markdown）
  memory/state.md      # 员工记忆
  .harness/            # config.toml / prompts / skills
```

## 当前状态

**P0–P11 已落地**（逐期细节见设计文档 §3 与 `d:/project/local_dream/docs/p*-backlog.md`）：

| 阶段 | 能力 |
|---|---|
| P0 | 目录契约脚手架 + 项目库扫描 + 中文 slug；front-matter 容错编解码；`ProjectStore` 章/世界观/角色/卷/情节读写；LLM 客户端（OpenAI 兼容：think 剥离 / 空流抛错 / 瞬时分类 / `llm.log`）+ max_tokens 策略；提示词两级覆盖加载 + 严格变量渲染；项目选择器 + REPL |
| P1 | 员工 ReAct 主循环（白名单 + 工作记忆 + 进度摘要 + 终端选项卡审批门 + 轻量修改路径 + `/issues` + state.json/events.log 续跑） |
| P2 | 规划链：master_plan 两段式（卷骨架 + 逐卷并发情节）+ 拆章三步 + 章节自查 |
| P3 | 写作流水线：逐拍写 + 审核 + 三级修正 + 去味 + AI 味检测 |
| P4 | 记忆 + 检索：三层记忆、现状快照、BM25、正文回指检索、卷级 digest |
| P5 | 打磨：`/audit` 一致性审计（确定性四规则——R1 数值单调已于 P18 复活：章记忆第 5 节「状态更新」给出多点历史 + LLM 复核）、两级 skills + 定向注入、联网核查与现实参照预检 |
| P6 | 验收链：`check_plots` / `global_check`（`editor_mode` 责编视角）、分层抽样、quote 回验、落 issues |
| P7 | 验收清单：来源层（内置 + 用户级/项目级 md）+ 清单进槽 + fail 落 issues + 按标题 supersede |
| P8 | 按章聚合修订：`/fix`（一章一张确认卡 + 一次写盘 + 逐条 resolved） |
| P9 | 清单的**生成阶段**注入（先定标准再写；与验收侧同一份清单） |
| P10 | 需求解析：`task.task_intake` 逐字移植 + 结构化 brief 落 `state.json`（验收的 goals/constraints 槽有真内容） |
| P11 | 范围与前提接线：`plan_arcs` 限卷 + 范围块注入规划提示词（模板不动） |

max_tokens 扩额重试由 worker 层拥有（web_dream 先例：重试/升级在服务层）：
`policy.py` 提供 `resolve_max_tokens` / `StarvedAtCapError`，client 保持纯库（`max_retries=0`，
不内建重试 helper）。

常用命令：`make test`（pytest -v）、`make lint`（ruff check src tests）。
