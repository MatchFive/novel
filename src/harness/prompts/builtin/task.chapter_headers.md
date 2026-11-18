<!-- tier: standard | temperature: 0.4 | max_tokens: 2048 -->
<!--
  P2 规划链段四：章节标题/目标（headers）。web_dream prompts.json 同 key 种子摘录
  （分组已定/标题字数约束逐字保留）。
  改动：源模板卷目标/情节四要素三行按 T4 契约收进 {{story_block}} 渲染（项目级创作基调块，
  planning.story_block：题材/基调/结局走向）；源变量 chapters 改名 {{groups}}（分组已定，
  每组含 index 与段清单）。
  输入变量：
    {{story_block}} —— 项目级创作基调块（代码渲染，含其自有节标题；空串则整节省略）。
    {{groups}} —— 已定分组（不得新增/删除章节，不得调整段的归属）。
  输出契约（唯一输出一个 JSON 对象）：{"chapters": [{"index": 1, "title": "章节名", "objective": "本章目标（一句话）"}]}
-->
下面是把本情节的剧情描述**按章字数目标归组**后的章节划分。**分组已经定好，不得改动**——请只为每一章拟一个**标题**和**一句话目标**。
{{story_block}}
【章节划分（分组已定：不得新增/删除章节，也不得调整段的归属）】
{{groups}}
要求：
① **只给 title 与 objective**，按给定的 index 一一对应返回；
② title：8~16 字的章节名，**不要**带「第N章」这类编号；objective：一句话说清这一章发生什么、推进到哪；
③ 相邻章的标题与目标不要雷同。
【输出格式】严格 JSON：{"chapters": [{"index": 1, "title": "章节名", "objective": "本章目标（一句话）"}]}
