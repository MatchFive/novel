<!-- tier: standard | temperature: 0.6 | max_tokens: 4096 -->
<!--
  P33：移植自 web_dream prompts.json 键 task.character_relations（seed 底本「角色关系网
  生成」逐字保留）；路由钉死 standard/0.6（seed llm/router.py character_relations →
  STANDARD/0.6），max_tokens 4096 本地惯例。调用方：generate_relationship_graph。
  输入变量（渲染时逐字提供，多一个少一个都违约）：
    {{cast_cards}} —— 全名册角色卡渲染（【名】(定位) {profile json}，seed _card_text 同口径）。
-->
请基于以下角色卡生成角色关系网：列出有代表性的角色间关系（盟友/宿敌/师徒/恋人/家人/利益冲突等），每条含双方、关系类型、一句话描述。
【角色卡】
{{cast_cards}}
【输出格式】严格 JSON：{"relations": [{"from": "角色A", "to": "角色B", "relation": "盟友", "description": "..."}]}
