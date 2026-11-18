<!-- tier: standard | temperature: 0.7 | max_tokens: 8192 -->
<!--
  P3 写作链：按意见修正（draft_revise）。web_dream prompts.json 同 key 种子逐字摘录（正文一字不改）。
  输入变量：
    {{cast_cards}} —— 见模板正文用法。
    {{draft}} —— 见模板正文用法。
    {{flavor_hits}} —— 见模板正文用法。
    {{issues}} —— 见模板正文用法。
    {{knowledge_state}} —— 见模板正文用法。
    {{outline}} —— 见模板正文用法。
    {{pov_memories}} —— 见模板正文用法。
    {{world_entries}} —— 见模板正文用法。
-->
请按以下审核意见修正章节正文：保持原有风格、视角与情节走向，只修正指出的问题，不要大改或重写无关部分。
【审核意见】
{{issues}}
【AI 套路（一并消除，逐句改掉下面这些）】
{{flavor_hits}}
【本章细纲（对齐基准）】
{{outline}}
【世界观设定】
{{world_entries}}
【出场角色卡】
{{cast_cards}}
【角色记忆】
{{pov_memories}}
【知识状态】
{{knowledge_state}}
【待修正正文】
{{draft}}
输出修正后的完整正文。
