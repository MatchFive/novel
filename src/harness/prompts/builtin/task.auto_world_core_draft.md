<!-- tier: standard | temperature: 1.0 | max_tokens: 8192 -->
<!--
  P37（C3 世界坐标）：移植自 web_dream prompts.json 键 task.auto_world_core_draft
  （seed 底本「Autopilot·世界内核·多候选生成」逐字保留）；路由钉死 standard/1.0
  （seed llm/router.py auto_world_core_draft → STANDARD/1.0），max_tokens 8192
  （seed MAX_TOKENS 同值：6 套内核候选，思考模型会把推理也算进这个额度，给足）。
  调用方：world_variation.draft_candidates。
  输入变量（渲染时逐字提供，多一个少一个都违约）：
    {{n}} —— 候选套数（_CORE_CANDIDATES=6）
    {{premise}} / {{constraints}} —— 需求/限制
    {{used}} —— 作者其它项目已用过的内核（跨项目避重）
    {{banned}} —— 本次禁用套路（默认黑名单 + 项目 banned_tropes.json）
-->
你是世界观创意总监。请为下面这本书一次产出 {{n}} 套**彼此差异极大**的「世界内核」候选，供另一位评审挑选。注意：这一步只出**内核关键词**，不要展开成设定条目。
【需求】{{premise}}
【限制】{{constraints}}
【作者已用过的内核（**不得重复、不得变个说法再来一遍**）】
{{used}}
【本次禁用套路（**一个都不许出现**）】{{banned}}
要求：
① 每套必须是一个**自洽的意象系统**（不是四个互不相干的词拼在一起）；
② 每套给出：core（一句话内核命题，≤30 字，含主角处境与核心张力）、metaphor（世界本质的取材意象）、medium（超凡力量的介质；故事没有超凡就填「无超凡」）、godstate（神明的存在状态；没有神明就填「不涉及」）、cost（全书核心代价/张力）、why（一句话说明它为什么既避开套路又契合需求）；
③ **严禁**再写财务—宗教套路（借债／还债／赎罪券／按揭／什一税／账目／债契／税赋），也严禁元素等级制、几柱神系、通用创世神话；
④ 每套都要从**本书特有的处境**倒推；落笔前自问「把它换到另一本书里还成立吗？成立就是太通用，重写」；
⑤ 候选之间必须换一个**完全不同的意象世界**，不要只换同义词、同源词。
【输出格式】严格 JSON：{"candidates": [{"core": "...", "metaphor": "...", "medium": "...", "godstate": "...", "cost": "...", "why": "..."}]}
