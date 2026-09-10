# 长篇小说 AI 创作工作台

按 `设计文档.md`（v1.6）实现的单机桌面应用：从点子到世界观、角色、大纲、章节、正文的全流程 AI 创作工具。

---

## 架构与 Agent 流程（给其他会话/开发者的快速导览）

### 整体分层

```
PySide6 UI（视图/对话框/工作台）
    ↓ 只调用 Service
Service 业务服务（Idea/World/Character/Chapter/Memory/Command/Draft/Check/Canon/Handbook/Chat）
    ↓ 只调用 Repository（见下方编码规范）
Repository 仓储层（app/db/repositories/）★ 唯一数据库访问点
    ↓
SQLite（SQLAlchemy ORM + FTS5）

LLM 服务层（app/llm/）
    ├─ 客户端抽象（OpenAI 兼容，async 流式/结构化）
    ├─ 模型路由（lite/standard/pro 三档，按任务分配）
    ├─ 上下文组装器（检索注入 + 记忆注入 + 预算裁剪）
    └─ 留痕（generation_logs + data/logs/llm/YYYY-MM-DD.jsonl）

LangGraph 工作流层（app/workflows/）
    ├─ agent_orchestrator.py  AI 指令编排
    └─ draft_workflow.py      正文生成工作流
```

### LLM 调用链（所有 AI 功能统一入口）

```
视图触发 → LLMService（app/services/llm_service.py）
  → 模板加载（prompt_templates，三级 scope 覆盖）
  → 模型路由（app/llm/router.py：任务 → lite/standard/pro 档位 + 温度）
  → 上下文组装（设定库/角色记忆/知识状态 检索注入 + 预算裁剪）
  → OpenAI 兼容调用（流式或结构化 JSON）
  → 留痕（generation_logs + 文件日志，含 caller/耗时/token/错误）
```

### Agent 流程一：AI 指令编排（LangGraph，`app/workflows/agent_orchestrator.py`）

用户在对话里说"把…改成/给…加个/删掉…"时：

```
用户指令
  ↓
orchestrate（主 Agent：意图识别 + 任务分解）
  ├─ 分解为多实体任务 {entity: world/character/chapter, action: add/update/delete, order}
  └─ 对话历史 + 项目实体上下文注入（理解"主角=刘修"等指代）
  ↓
execute（实体 Agent 按 order 顺序执行）
  ├─ WorldAgent / CharacterAgent / ChapterAgent
  ├─ 每个 Agent 可读其他实体数据作为上下文
  └─ 后续 Agent 能看到前面 Agent 已生成的变更（结果传递，保持一致）
  ↓
summarize（汇总为统一修改计划 plan）
  ↓
confirm（★ interrupt：图暂停，计划暴露给用户确认）
  ↓
条件边：用户确认 → apply（执行写库） / 取消 → cancel
```

**LangGraph 三条核心能力已落地**：
- **interrupt**：confirm 节点暂停等用户勾选确认，`resume(thread_id, True/False)` 续跑
- **checkpointer**（SQLite `workflow_state.db`）：图状态落盘、断点续跑、`get_history` 历史回放
- **条件边**：`orchestrate → clarification?END : execute`、`confirm → 确认?apply : cancel`

### Agent 流程二：章节正文生成工作流（LangGraph，`app/workflows/draft_workflow.py`）

替代单次 LLM 调用的多步骤流水线：

```
collect_context（上下文构造）
  └─ 角色记忆（POV 经历档案）/ 世界观（FTS 检索）/ 知识状态（谁知道什么）/
     未回收承诺 / 相关历史事件 / 前情摘要 / 本章细纲 / 最近正文
     （ContextAssembler + MemoryInjector，按预算裁剪）
  ↓
write_draft（正文写作，流式）
  ↓
review（逻辑与内容审核：设定冲突/角色记忆不符/知识泄漏/逻辑矛盾 四类）
  ↓ 条件边
  通过 → END（返回正文）
  不通过 → revise（按审核意见修正）→ 回到 review（限 2 次防死循环）
```

### 三层记忆体系（数据流，解决长篇小说"角色失忆"）

```
章节定稿 → 记忆提取（memory_svc）
  ├─ 剧情事件日志（story_events，全知视角）
  ├─ 角色经历档案（character_events，限知视角，quotes 台词原文保真）
  ├─ 信息项与知晓状态（info_items / character_knowledge，谁知道什么）
  └─ 承诺/伏笔（promises，flag 状态跟踪）
    ↓ 入库（可修正）
正文生成 → MemoryInjector 按 POV 注入上述记忆 + 三条记忆铁律
    （角色言行连续 / 不未卜先知 / 台词用原文）
修订 → 六类一致性扫描（时间线/设定/角色/记忆冲突/知识泄漏/承诺回收）
```

### 创作数据流（端到端）

```
AI 对话（聊点子→直接入库角色/设定）→ 点子（扩展/评估/合并）
  → 世界观（AI 骨架/沉淀）→ 角色（角色卡/关系图/对话模拟）
  → 大纲（AI 生成/爽点评估）→ 章节（拆章/细纲/节奏检查/批量初稿）
  → 正文（正文工作流生成/续写/多分支）→ 记忆提取/修正
  → 修订检查（六类扫描/设定沉淀）→ AI 指令（自然语言改实体）
```

---

## 环境要求

- Python 3.11+
- 一个 OpenAI 兼容 API 的 Key（DeepSeek / 通义 / OpenAI / 智谱 / Kimi 均可）

## 安装与运行

```powershell
# 1. 安装依赖
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple PySide6 openai qasync markdown-it-py "langgraph>=1.0" langgraph-checkpoint-sqlite aiosqlite

# 2. 配置 API Key（编辑 config.toml，填入你的 key）
#    [llm.providers.deepseek]  api_key = "sk-..."

# 3. 启动（首次出现项目选择窗口）
python main.py
```

## 打包 exe（可选）

```powershell
pip install pyinstaller pillow
python scripts/gen_icon.py   # 生成图标（resources/icon.ico）
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 产物：dist/NovelStudio/NovelStudio.exe（带图标与主题；**打包保留 data/ 用户数据与 config.toml**）
```

### ⚠️ 运行 exe 被 Windows SmartScreen 阻止（正常现象，不是病毒）

PyInstaller 打包的 exe **没有数字签名**，Windows"智能应用控制"会以"无法验证发布者"为由阻止运行。这是未签名应用的普遍现象，并非安全问题。解决方法（任选其一）：

1. **弹窗点「仍要运行」**：SmartScreen 弹窗点「详细信息」→「仍要运行」（部分版本直接显示"仍要运行"按钮）；
2. **右键解除锁定**：右键 `NovelStudio.exe` →「属性」→ 勾选下方「解除锁定(K)」→「确定」→ 再运行；
3. **关闭 SmartScreen 对该文件的拦截**：在弹窗中选择"从 Microsoft Store 获得应用"以外的选项，或在 Windows 安全中心 → 应用和浏览器控制 → 智能应用控制设置 中调整（不推荐全局关闭）；
4. **（开发者）代码签名**：如需分发，可用代码签名证书对 exe 签名（`signtool sign`），消除拦截。

> 本会话构建的 exe 与其他会话构建的在功能上完全一致，差异只在于运行环境是否已将该文件标记为"已允许"。

## 启动流程

首次启动出现**项目选择窗口**（最近项目 / 新建 / 打开）；进入工作台后共 11 个模块（仪表盘/点子/世界观/角色/大纲/章节/记忆库/教程库/提示词/修订检查/日志）。

## 当前进度

| 里程碑 | 状态 | 内容 |
|---|---|---|
| M0 技术验证 | ✅ 完成 | PySide6 骨架、LLM 流式客户端、SQLite 建库、模型路由 |
| M1 MVP | ✅ 完成 | 项目/点子/世界观/角色/大纲/章节 CRUD + AI 生成 + 正文编辑器 + 生成配置 + 流式 AI 续写 |
| M2 上下文与体验 | ✅ 核心完成 | FTS5 全文检索（trigram 中文）、上下文组装器（预算/裁剪/检索注入）、记忆注入器、章节摘要、注入预览 |
| M3 记忆体系 | ✅ 核心完成 | 记忆提取流水线（事件/角色经历/信息项/承诺 分层提交，台词原文保真）、记忆库视图 + 修正面板、POV 记忆注入激活 |
| M4 修订与打磨 | ✅ 核心完成 | 一致性扫描（六类）、设定沉淀、AI 指令驱动编辑、教程知识库、提示词管理、仪表盘、批量操作、Token 用量面板、备份恢复 |
| Agent 编排 | ✅ 完成 | LangGraph AI 指令编排（interrupt/checkpointer/条件边）、正文生成工作流（上下文构造→写作→审核→修正） |

## 目录结构（核心）

```
main.py                        # 入口
config.toml                    # 模型配置（Provider / 档位 / API Key）
app/
├── core/                      # 配置、日志、常量
├── db/                        # ★ 数据库唯一访问点（见设计文档 4.4）
│   ├── models.py              # 全部表结构
│   ├── session.py             # Session 工厂
│   ├── unit_of_work.py        # 事务边界
│   ├── migrations.py          # 建表 + 种子（唯一允许裸 SQL）
│   └── repositories/          # 仓储层（唯一数据库访问点）
├── llm/                       # 客户端抽象、模型路由、上下文组装器、记忆注入器、模板渲染
├── workflows/                 # ★ LangGraph：agent_orchestrator（指令编排）、draft_workflow（正文工作流）
├── services/                  # 业务服务（只调用仓储）
├── ui/                        # PySide6 界面
│   ├── main_window.py         # 主窗口
│   ├── workspace.py           # 项目工作台（导航 + 模块视图）
│   ├── views/                 # 各模块视图（点子/世界观/...）
│   └── dialogs/               # 对话框
└── utils/
resources/prompts.json         # 内置 Prompt 模板（首次启动写入库）
tests/                         # 冒烟测试
```

## 编码规范（强制）

- **数据库访问**：所有 SQL/ORM 操作只允许出现在 `app/db/repositories/`；其他层一律经仓储方法（设计文档 4.4）。
- **LLM 调用**：统一走 `app/services/llm_service.py`（模板加载 → 路由 → 调用 → 留痕）。
- **模型档位**：`config.toml` 中按 `lite / standard / pro` 三档配置，路由表见 `app/llm/router.py`。
- **LangGraph 封装**：业务层只面向 `run_workflow / Orchestrator.run / DraftWorkflow.run` 接口，不直接依赖 LangGraph API（见 6.7.4）。

## 测试

```powershell
python tests/test_views_smoke.py   # 点子/世界观视图 CRUD
python tests/test_m1_views.py      # 角色/大纲/章节/生成配置视图 CRUD
python tests/test_assembler.py     # M2 上下文组装器 / FTS5 检索
python tests/test_memory.py        # M3 记忆分层提交 / 注入激活
python tests/test_m4.py            # M4 一致性扫描问题入库 / 设定沉淀
python tests/test_m4b.py           # M4 AI 指令执行 / 教程库 / 提示词管理
python tests/test_dialogs.py       # 对话框回归（新建/打开项目）
python tests/test_chat.py          # AI 创作对话（上下文构建/面板绑定）
python tests/test_features.py      # 角色关系/对话模拟/点子合并/大纲评估/节奏检查
python tests/test_backup_usage.py  # 备份恢复 / Token 用量统计
python tests/test_orchestrator.py  # LangGraph Agent 编排（任务分解/顺序执行/汇总）
python tests/test_langgraph_features.py  # LangGraph 三条核心能力（interrupt/checkpointer/条件边）
python tests/test_draft_workflow.py  # 正文生成工作流（上下文构造→写作→审核→修正）
python tests/test_command_exec.py  # 指令执行（新增/删除/模糊匹配/跳过原因）
python tests/test_dedupe.py        # 角色去重（防重复创建/清理重名）
python tests/test_logging.py       # 日志系统（调用留痕/迁移补列/日志视图）
python tests/test_handbook.py      # 教程库（停用显示/本项目优先/查看内容）
```

## 日志系统

- **LLM 调用日志文件**：`data/logs/llm/YYYY-MM-DD.jsonl`（按天一个，详细 JSON 记录：caller/模型/输入输出摘要/token/耗时/错误），**不增大数据库负载**
- **「日志」页**：LLM 调用历史（选日期 + 状态过滤 + 点击查看完整输入/输出/错误详情）+ 应用日志（可只看错误）
- 数据库 `generation_logs` 仅留轻量记录供 Token 用量统计；详细日志走文件
- 所有异步任务异常自动记入 `data/logs/app.log`（含 traceback）

## 指令执行说明

对话里说修改意图（把…改成/给…加个/删掉…）会走 LangGraph 编排生成修改计划，**在对话面板卡片里勾选确认后执行**。支持：
- **操作类型**：修改（update）/ 新增（add）/ 删除（delete）——说"新增/添加/设定一个"会创建新实体，说"改成/调整"会更新已有实体
- **实体域**：世界观设定 / 角色卡 / 章节标题目标 / 细纲节拍
- **模糊匹配**：目标名不精确也能命中（双向包含/去空格/FTS 检索/『第N章』写法）
- **跳过提示**：找不到对应实体时给出具体原因并建议改用"新增"表述

## 使用流程（当前可用）

0. **AI 对话**（随时）：右侧 AI 助手面板 →「对话」页。
   - **聊点子/设定/剧情**（携带项目上下文，**对话长期保存**：可切换历史会话恢复上下文）
   - **直接修改项目**：说"把…改成/给…加个/删掉…"等 → **LangGraph 编排**（意图识别 → 任务分解为各实体 Agent → 按依赖顺序执行）→ 生成修改计划 → 在对话面板内**勾选确认执行**（不打断对话；需要澄清时直接在对话里追问补充）
   - AI 整理的档案可「存为点子/角色/设定」入库
1. `python main.py` → 文件 → 新建项目（填书名/类型/梗概）
2. **点子**：记录灵感 → AI 扩展/评估；选中 2~3 个 → **AI 合并**成统一点子
3. **世界观**：AI 生成骨架 → 逐条审阅编辑
4. **角色**：AI 生成角色卡；**关系图**页 AI 生成关系网（图形化展示）/手动添加；**对话模拟**页选两个角色模拟对话校验"对味"
5. **大纲**：AI 生成卷大纲 → **AI 评估爽点**（各卷强度 + 调整建议）
6. **章节**：AI 拆章（选卷）→ AI 生成细纲 → 打开章节 → 光标处「AI 续写」或「多分支」（2~3 种走向任选）→ **LangGraph 正文工作流（上下文构造[角色记忆/世界观/知识状态] → 写作 → 逻辑与内容审核 → 按审核意见修正，限 2 次）** → 审阅后「应用草稿」→ 保存正文；**节奏检查**（连续章节冲突/爽点密度）
7. **记忆**（M3）：对已写章节点「记忆提取」→ 三层记忆入库 → 后续正文生成自动注入角色经历/知识状态/承诺 → 「记忆库」页审阅事件日志、角色经历时间线、承诺清单
8. **修订**（M4）：「修订检查」页 → 一致性扫描（六类问题，双击复制建议/标记状态）；设定沉淀（从章节提取新设定候选 → 接受入库）
9. **教程库 / 提示词**（M4）：导入写作教程（AI 提炼要点/原文切片，生成时注入）；提示词模板界面化编辑（版本历史/恢复默认）；教程库**查看内容**（原文+切片预览）
10. **仪表盘 / 批量**（M4）：项目进度总览 + 一键跳转；**Token 用量面板**（按厂商/档位汇总）；章节页批量摘要/批量记忆提取/批量初稿
11. **备份恢复**（M4）：文件 → 备份数据库（Ctrl+B，自动保留 30 份）；恢复数据库…（选择备份覆盖，重启后生效）
12. **日志**（新增）：「日志」页查看 LLM 调用历史与应用日志
