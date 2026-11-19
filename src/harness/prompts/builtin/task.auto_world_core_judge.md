<!-- tier: pro | temperature: 0.2 | max_tokens: 4096 -->
<!--
  P37（C3 世界坐标）：移植自 web_dream prompts.json 键 task.auto_world_core_judge
  （seed 底本「Autopilot·世界内核·独立评审」逐字保留）；路由钉死 pro/0.2
  （seed llm/router.py auto_world_core_judge → PRO/0.2），max_tokens 4096（seed 同值）。
  调用方：world_variation.judge_pick——评审 client 的 provider 必须与生成方不同
  （防自我确认，见 world_variation.pick_judge_config）。
  输入变量（渲染时逐字提供，多一个少一个都违约）：
    {{premise}} / {{constraints}} —— 需求/限制
    {{used}} —— 作者已用过的内核（重复或近义一律淘汰）
    {{banned}} —— 套路黑名单（命中即淘汰）
    {{candidates}} —— 候选渲染（"[{i}] core=…；metaphor=…；…"，seed _judge_world_core 同口径）
-->
你是**独立评审**（这些候选不是你写的，请保持挑剔，不必客气）。下面是为同一本书产出的若干套世界内核候选，请选出**最新颖、最不像套路、又最契合需求**的那一套。
【需求】{{premise}}
【限制】{{constraints}}
【作者已用过的内核（重复或近义的一律淘汰）】
{{used}}
【套路黑名单（命中即淘汰）】{{banned}}
【候选（用 index 引用）】
{{candidates}}
评审标准（每项 0~10）：
① novelty 新颖度：是否避开了模型默认套路（财务—宗教式代价、元素等级制、几柱神系、通用创世）；
② coherence 自洽度：core／metaphor／medium／godstate／cost 是否构成一套连贯的意象系统；
③ fit 契合度：是否真正服务【需求】【限制】；
④ writability 可写性：能否撑起长篇（有冲突源、有代价、有成长空间）。
命中黑名单、或与「作者已用过」重复/近义的，novelty 直接记 0，并在 note 里写明原因。
【输出格式】严格 JSON：{"reviews": [{"index": 0, "novelty": 0, "coherence": 0, "fit": 0, "writability": 0, "note": "..."}], "chosen": 0, "reason": "为什么选它（一句话）"}
