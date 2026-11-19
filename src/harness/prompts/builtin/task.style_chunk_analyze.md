<!-- tier: lite | temperature: 0.2 | max_tokens: 512 -->
<!-- P39：seed prompts.json task.style_chunk_analyze 逐字；文风采集流水线逐片标注。
     vars：{chunk}（单片原文）。输出严格 JSON：{category, note, keep}；
     keep=false 或 note 空的片不入库（stylecollect.analyze_chunks 判）。 -->

你在为一部长篇小说的「文风样本」做逐片采样。只分析这段文字的**写法**（遣词、句式、节奏、对话处理），不要概括剧情、不要评价好坏、不要摘抄情节。
【片段原文】
{{chunk}}
【要求】
① category 从这五类里选最贴切的一类：叙事（以叙述/描写为主）、对话（以人物对白为主）、心理（以内心活动为主）、动作（以动作/打斗为主）、环境（以景物/场景铺陈为主）；
② note 用不超过 40 字点明这段最值得模仿的写法特征（如「短句为主，句末用句号收束，少用形容词」「对话不用引号，靠动作与停顿区分说话人」）；
③ keep：这段是不是可作样本的正文？前言/目录/作者的话/注释/菜单式罗列等非正文内容填 false。
【输出格式】严格 JSON：{"category": "叙事|对话|心理|动作|环境", "note": "不超过40字的写法要点", "keep": true}
