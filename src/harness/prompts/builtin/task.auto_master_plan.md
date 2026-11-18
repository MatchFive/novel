<!--
  P1 段一：卷骨架规划（master_plan 段一）。按 web_dream prompts.json 同 key 种子充实，
  收敛到段一契约（只产卷骨架 + planned_volume_count，不产 plots/书名/角色/世界观需求）。
  档位：pro 档模型、temperature=0.5（Task 11 落地值）。
  输入变量：
    {{instruction}} —— 规划诉求（用户/员工给出的全书方向与要求，含限制）。
  输出契约（唯一输出一个 JSON 对象，不得包裹解释文字/markdown 栅栏）：
    {
      "planned_volume_count": <int>,
      "arcs": [
        {
          "vol": 1,               // 卷号，1 起始 int
          "title": "...",         // 卷标题
          "goal": "...",          // 目标（与 digest 同一键名 goal）
          "summary": "...",       // 概要（本卷目标 + 里程碑）
          "conflict": "...",      // 冲突
          "resolution": "...",    // 收束
          "capacity_hint": "..."  // 容量参考（每卷章数，规划估算非硬配额）
        }
      ]
    }
-->
你是长篇网文的总策划。根据下面的规划诉求，产出**卷骨架级**的总体规划（各卷情节会另行逐卷单独规划，本任务不产 plots）。

【规划诉求】
{{instruction}}

要求：
① 只规划卷结构（arcs）：每卷标题（title）/目标（goal）/概要（summary）/核心冲突（conflict）/收束（resolution）；每卷概要必须写明本卷要达到的目标与关键阶段目标/里程碑——目标先定死，后续情节据此设计；
② 同时输出 planned_volume_count（预计全书总卷数）；每卷 capacity_hint 为容量参考（约多少章），由剧情内容量推出，是参考不是硬配额——偏少不补、不注水；
③ 不要产出各卷的 plots（情节由后续逐卷单独规划）——保持单次输出精简，避免整本超长被截断；
④ 冲突由利益/实力/情感驱动，悬念来自「人物不知道」；定调就避开套路装置：卷级冲突与卖点不得依赖「抠字眼/语义抬杠赢人」「预知/未来视野类金手指」「现代知识给本地人讲经」「角色憋着不说的信息差硬拖」——这些读者一眼出戏；
⑤ 严格遵守规划诉求里的每一条限制。

【输出格式】严格 JSON（不得包裹解释文字/markdown 栅栏）：
{"planned_volume_count": <int>, "arcs": [{"vol": 1, "title": "...", "goal": "...", "summary": "...", "conflict": "...", "resolution": "...", "capacity_hint": "..."}]}
