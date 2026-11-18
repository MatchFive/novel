# P2 Backlog — P2 规划链路终审 deferred minors 与决策记录

> 来源：2026-11-13 P2 规划链路计划（SDD）最终全分支终审（9155896..6424a7c）的裁定。
> P3 计划编写时逐项过一遍，「defer」不等于「丢弃」。P0/P1 遗留见 p1-backlog.md（同目录）。

## P2 已落档的架构决策（勿重新发现）

- **工具↔loop 分类契约（P2 终审 Important 的固化）**：工具失败观测一律 P1 词表 `工具名 失败（类别）：…`（类别 = 参数/无骨架/无情节/无产出/配置等确定性语义，**禁含 truncated/starved 瞬时词**）；逐步 ok 当且仅当 ≥1 条目落库，逐项行用 `第N卷：未成功（reason）`（刻意不匹配 `失败（` 词表）；瞬时词命中优先于一切（既有设计，per-item 原因含瞬时词时整步判 transient）。新工具必须双方向补 loop 级分类测试。
- **async 工具形态**：`(store, llm, prompts, settings, emit, args) -> ToolResult`，loop 按名路由 await 分支（先于 registry.has）；registry 同步 handler 走 base._run_sync 且 emit=no-op（docstring 注明）。P1 的 plan_arcs/edit_by_command 仍返回 str——P3 统一 ToolResult。
- **重试层级**：`complete_structured` 内层阶梯（恰一次扩额：empty 顶 CAP、starved/解析失败 ×2）× `with_shrink` 外层两轮 = 全坏 JSON 2×2 四次调用（seed 同构，勿"优化"掉一层）；部分败 usage 照常入账（含失败卷半截）。
- **预算合计口径**：step 决策 usage + 工具 usage 同账 `stats["token_used"]`，步末累加后 `_check_budget` 判合计（一次 helper 两处调用，口径不可漂移）。
- **units 回写**：compact JSON 单线 KV 回写情节卡，**必须全 dict 合并**（store upsert_plots 整块替换语义，字面 `{seq, units}` 会冲掉概要）——units 目前无 reader（续走读 chapter meta），P3 写作链路上线后成为源材料。
- **并发纪律**：Semaphore(3) gather 内只共享只读状态 + 每任务私有 dict；写盘一律 gather 后串行；store threading.Lock 体内**不得有 await**（终审死锁面验证过的合同）。
- **模板↔组装边界**：builtin 模板 `{{var}}` 集合与 context.py 返回键双向相等（test_p2_prompts 反向校验），改任一侧必须同步。

## defer-to-P3（终审裁定）

- **planned_volume_count 持久化**：plan_arcs 解析出该值但不落库 → master_plan 的 arcs_planned 只能用 len(arcs)。需 plan_arcs 侧先持久化（arcs meta 或 state），跨任务改。
- **master_plan 重跑守卫**：现重跑会重编 seq 1..N、丢 units 回写、留 stale seq>N。爆炸半径今天无害（units 无 reader、既有章不可重拆）。P3 加卷级守卫：`plots 已有章节且非 args.force 的卷跳过`。
- **plan_arcs/edit_by_command 迁 ToolResult**：与 P2 新工具统一返回形状，complete_structured 阶梯全工具共用（P2 只服务新工具）。
- **headers_vars 的 story 接线已在本期修复**；若 P3 加 intake，story_settings 来源换成 brief。
- EventLog append O(n²)（每次全文读）：P2 只每步多几条，文件 MB 级；P3 事件审计消费方落地时改 seq 内存化。

## leave（永久接受，勿再提出）

- character_digest 无卡数上限——单调用点 [:20] 补偿（context.py:259），verified。
- selfcheck applied 计数宽松（seed 同构）；步末 budget 检查对无 usage 步冗余（安全网）；`paused` 变量名观感（RunState|None）；_meta_int 绕弯（cosmetic）。
- need_web 保留至 P5 联网链路（解析忽略非拒绝）；plot_units 反套路锁三关键词为 seed 分层原样。
- E.2 的 chapters_planned `check` phase 并入 selfcheck emission（still_repeat payload，确定性复核无独立 LLM 阶段）——E.2 回写时注一句。

## 已验证的验收基线

- **P2**（2026-11-13，HEAD 6424a7c）：**370 tests passed**（P1 基线 190 + P2 新增 180），`ruff check src tests` 零违规。8/8 任务 + 终审 1 Important + 3 Minor 全闭环，全程仅一次终审修复轮（任务级零 fix round）。
- 三验收场景：全规划链（章数=内容量推出）/幂等续拆（零 LLM 再调）/断点续跑（真 replay 路径只补 beats）。
