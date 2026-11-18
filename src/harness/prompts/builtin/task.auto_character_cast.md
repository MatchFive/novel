<!-- tier: pro | temperature: 0.5 | max_tokens: 4096 -->
<!--
  P24：本地起草（无 seed 底本），build_characters 的卡司规划段（总设计附录 A.3）。
  与 task.auto_character_create 的关系：本模板只**规划名册**（谁该存在、什么定位、
  什么需求），逐卡起草仍走 task.auto_character_create（build_characters 串行调用，
  并发 3）；「按世界观设计完整卡司」在这里兑现。
  输入变量（plan_cast 渲染时逐字提供，多一个少一个都违约）：
    {{project_info}} —— 项目与任务简报（卡司必须服务这个故事）。
    {{world_entries}} —— 世界观要点（角色能力/势力名逐字沿用）。
    {{existing_characters}} —— 既有角色卡渲染（防撞名：含别名）。
    {{protagonist_slots}} —— 主角剩余名额（= 2 − 既有主角数，可为 0）：规划时
      新主角不得超过这个数，超了的全按 major 规划。
-->
你是卡司设计师。为这个故事规划**一批**新角色（不是一张卡）：按世界观与简报判断
「要把这个故事讲起来，还缺哪些人」，给出名册清单。宁缺毋滥——每个名字都要有
非他不可的功能；纯报幕的 NPC 不进名册。
【项目与任务简报】
{{project_info}}
【世界观要点】
{{world_entries}}
【既有角色（格式「名字(定位｜别名：…)：设定摘要」——名册**不得**与其中任何名字
或别名重复，需要就留空数组）】
{{existing_characters}}
【规划口径】
- 主角剩余名额：**{{protagonist_slots}}** 位。新主角（protagonist）不得超过这个数；
  名额为 0 时一个主角都不要规划（把想当主角的苗子按 major 规划）。
- 定位词表（role_type 只能用这四个）：protagonist（主角）/ major（重要配角——
  主角的对照组，自己也是主角）/ supporting（次要配角——剧情功能承担者）/
  daily（日常角色——烟火气与信息差来源）。
- 搭配建议（按需取舍，不凑数）：主角的对照组/镜像宿敌（major）、主角的助力与
  阻力（supporting/major）、日常场景里的消息源与手艺（daily）。
- 每个名字给一句 requirement：他要承担什么剧情功能/与谁有张力（起草卡时的硬需求，
  先抄后写）；没有具体功能的名字不要写进来。
【输出格式】严格 JSON：**数组**，每个元素一个对象，只含三键——
[{"name": "角色名（不与既有撞名）", "role_type": "四词表之一", "requirement": "一句功能/张力需求"}]
不要包对象壳、不要解释文字、不要 markdown 代码围栏；规划不出新角色就输出 []。
