# P1 Backlog — P0+P1 终审 deferred minors 与决策记录

> 来源：2026-11-12 P0 骨架计划 + 2026-11-13 P1 员工主循环计划（均 SDD）的最终全分支终审 deferred minors 清单与控制器裁定。
> P2 计划编写时逐项过一遍，「defer」不等于「丢弃」。

## P1 已落档的架构决策（勿重新发现）

- **async 工具接缝**：`plan_arcs`/`edit_by_command` 是模块级协程，loop 内**直接 await**（不经 registry.execute——registry handler 是同步签名内部 asyncio.run，事件循环内调用会响亮 RuntimeError）。这是 P1 的硬约束，P2 新增 async 工具照此办理。
- **stream close 归属**：注入 loop 的共享 LLM client **由调用方关闭**——loop 的 finally 调 `llm.aclose()`（失败降级 warning）。工具严禁关闭共享 client；client API 面有 `aclose()`/`__aenter__`/`__aexit__`。
- **complete() 的 mock 形态**：openai SDK 1.79 只在流式路径解析 SSE——`complete()`（stream=False）的测试 mock 一律 JSON body；SSE 线格只属于 `stream_text`。
- **fail-closed 为结构性保证**：白名单 map 值全部由 TOOLS_ALL 宇宙构造（测试钉住子集关系）；parallel 路径预滤 registered+read_only+allowed，registry 拒串不可达；`_parallel_calls` 与单动作门共用同一冷却拒判语义。
- **trim/O(n) 合同**：`trim_observation` 一遍扫描+一遍排序、`_iter_json_objects` 单遍栈扫描——**禁止 while 循环重扫**（生产死锁事故 + O(n²) 冻死两起先例，代码内有防"修复"注释）。
- **复制传播教训**：deferred bug 会被逐字复制（sort-lambda 实例）。任何可复制的容错/排序/索引模式，发现即双站点同修或抽 helper；store 层索引一律 `isinstance(x, int) and not isinstance(x, bool)`（upsert_arcs True==1 已按此修）。
- **run 起点轮转语义**：`rotate_if_needed` 在每次 run 开始调用；轮转会归档删除活动 events.log、seq 归 1、清空调下一次 resume 的回放记忆——既定代价，P2 若做事件审计消费需重估。
- **world deliverable 排除 plan_arcs**：纯建设 run 不规划卷骨架，刻意设计（设计附录 A 已回写注明）。
- **`load()` 坏档语义**：state.json 损坏 → 返回 None 视同缺失（恢复锚点不抛裸异常）；但 valid-JSON-wrong-shape 仍会 KeyError（见 defer 清单）。

## defer-to-P2（按主题归并）

### loop / agent
- **fail_streak 全局计数**：A-fail+B-fail 会冷却 B（spec 精神是"同工具连续失败≥2"）；保护功能仍生效（冷却 3 步自愈、noop_streak 兜底），P2 改按动作分桶。
- **CancelledError 吞**：loop 转 paused 不 re-raise；今天唯一取消源是 asyncio.run 关停，**P2 引入真任务取消时与 awaiting_* 持久化一并处理**（届时从 except 摘掉）。
- **cooled["llm"] 死键**：LLM 调用失败置 cooled["llm"] 但无 action 匹配；无害记账，下次动熔断器时顺手改名 `last_failed_action`。
- **ApprovalCancelled → noop**：用户取消提问后 loop 继续干活直到 noop_streak 暂停；spec E.1 的 `awaiting_*` 状态 P1 未持久化。P2 落 awaiting_user 挂起语义。
- **cancelled edit_by_command 观测分类 "ok"**：零变更成功当有效步计数；行为可辩护但更诚实的分类是 noop——P2 有意识决定。
- **预算记账缺口**：plan_arcs/edit_by_command 的内部 LLM usage 不进 `token_used`（需改工具返回形状）；2M 上限是粗粒度暂停阈值，低估只是延迟触发。
- **缺失 spec 事件**：`tool_cooldown`/`resumed`/`step_plan` 从不发射，`auto_reserves` 从不自增——机制在但无留痕；P2 建事件审计消费方时一并补，勿现在回填。
- **`_last_seq` 每 append 全文读 O(n)**：单次写者本地规模可接受，轮转接线后文件有上限；P2 随事件审计工作重估。
- **decided 层 24 事件回放窗内更早事件的丢失**：回放深度 plan 钉死（tail(WORK_MEMORY_REPLAY*2)），改深度是设计决定非 bug。

### store / repl
- **`_cmd_characters` 直 glob 改道 store.list_characters()**：终审评 fix-now，控制器裁 defer——手动调试命令表面路径、零功能影响，随 P2 store 工作成批改。（终审裁定与控制器裁定不一致，以此为准。）
- **state.py `load()` valid-JSON-wrong-shape 炸 KeyError/TypeError**：与 tail() 孔同容错类（tail 已修），--resume 路径 ~3 行 shape 检查。
- **新 brief 在 paused run 上静默覆盖 state.json**：加一行警告（"当前有未完的 run，/resume 或 /abort 后再发新 brief"）。
- **`read_world()` FileFormatError 未捕获**：arcs/chapters/characters 全有防护唯独它漏，坏 world.md 以"LLM 调用失败"死 6 次都不点名文件；一个 try/except。

### 审批 / 工具
- **T9 ↑↓ 方向键**：编号输入全功能可用，raw-mode 后续可加。
- **T11 `_run_sync` 嵌套循环 RuntimeError 无自动测试**：响亮报错为设计意图，loop 永不路由到那里。
- **T11 `_snapshot` 无界**：prompt 尺寸防护属 digest↔预算大工作。
- **T8 `_load_one` valid-JSON-wrong-shape 炸 list()**：手造腐败才触发，响亮崩非冻结。
- **digest/list_characters/read_character 解析重复**：与 tolerant-arcs helper 抽取同属 P4 角色卡深化。
- **T4 闭包目标未校验成员**：未来扩表时 allowed_for_run 响亮失败，无当前暴露。

### P0 遗留（带入 P2）
- **plot_seq 全局 vs 按卷编号**：仍未裁定，**检索建立之前必须决定**。
- `slugify("!!!")` 空 slug → 项目文件夹 `-2`；`write_chapter` 同 seq 双文件静默删一；plots 奇形续行——均手造边界，P2 情节编辑落地时压测试。
- 流式提前异常不显式关闭：aclose/loop-finally 已覆盖主路径，随 P2 流式工作复查。

### 无人值守 / 事件
- **非 TTY 无人值守不可达**：TTY 门在 _make_components，任何非 TTY 都不起 loop、无人轮询 pending.answer。裁 P2；届时加 `--non-interactive` 把 ApprovalUI 接到 store.root/.harness。

## defer-to-P3/P4
- P3：build_world/build_characters 落地（whitelist 扩表注释已标注）；edit_by_command 角色卡 name 与路径 stem 不一致应报错而非新建文件。
- P4：角色卡深化时统一 list_characters/read_character/tolerant-arcs 解析重复。

## leave（永久接受，勿再提出）
- decided 永不淘汰/render 无界：设计决定（decided 意为不驱逐；回放窗实际约束输入）。
- T7 pairs sort O(k log k)；T10 execute keyword-only 与未注册名行为不对称；T11 planned_volume_count 宽松转型；T8 id 过 9999 排序破序（理论边界）。
- T9 文件模式 note 丢弃（_ask_via_files 已有 fallback 闭环）；T12 _FALLBACK_SIG 漂移（flow action 签名稳定）；loop.py 四职（结构注记非缺陷）。
- `_is_transient` 文案兜底误标（良性，预算多试一次）；多段摘要漂移（E.4 限 ≤80 字单段）；repl 双读/渲染重复（cosmetic）。

## 工程卫生
- git identity 占位 `harness-dev@localhost`：**首次 push 前必须换真身份**（全部提交都用它）。
- `.gitignore` 可加 `.ruff_cache/`（cosmetic）。
- pytest-asyncio deprecation warning：环境噪音，随依赖升级自然消失。

## 已验证的验收基线

- **P0**（2026-11-13，HEAD c959e88）：53 tests passed，ruff clean。
- **P1**（2026-11-13，HEAD 12588f1）：**190 tests passed**（53 旧 + 137 新），`ruff check src tests` 零违规。13/13 任务 + 终审 7 Important 全闭环；三验收场景（规划落盘/replay 续跑/轻量修改合并）对真实 store+events+state 断言。
