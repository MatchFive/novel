"""角色记忆提取 Prompt 模板。"""
from __future__ import annotations

import json
from string import Template


JSON_RULES = """JSON 输出规则（必须遵守）：
1. 只返回纯 JSON，不要 markdown 代码块，不要解释。
2. 所有字符串字段必须是合法 JSON 字符串。
3. action 只能是 add / update / delete。
"""


MEMORY_EXTRACTION_PROMPT_TEMPLATE = Template("""你是小说角色记忆管理员。请根据本章正文，为每个出场角色维护其"已知信息"记忆库。

${json_rules}

输出格式：
{
  "memories": [
    {
      "action": "add|update|delete",
      "memory_id": "现有记忆id或null",
      "content": "记忆文本",
      "importance": "core|major|minor",
      "ttl": "permanent|long|arc|scene",
      "related_character_ids": ["uuid"],
      "related_foreshadow_ids": ["uuid"]
    }
  ]
}

业务规则：
- 只提取角色在本章中实际获得、确认或经历的信息，不要加入角色不可能知道的内容。
- 区分事实与角色推断，统一以自由文本表达。
- importance 标注：
  - core：影响角色核心动机、身份、长期目标
  - major：重要事件或情报
  - minor：细节补充
- ttl 标注：
  - permanent：永久有效（如身世、核心关系）
  - long：长期有效（如阶段任务、重要情报）
  - arc：当前剧情弧/副本/战斗内有效
  - scene：仅当前场景有效
- 识别关联角色（通过角色 id）和关联伏笔（通过伏笔 id）。
- 如果某条现有记忆被本章内容推翻或需要更新，输出 update 并填写 memory_id。
- 如果某条现有记忆已过时且不再需要，输出 delete 并填写 memory_id。
- 若本章没有让某角色获得新信息，则不要为该角色输出任何条目。

输入数据：
【本章正文】
$chapter_text

【目标角色】
$character

【角色现有记忆】
$existing_memories

【项目全部角色】
$characters

【项目全部伏笔】
$foreshadows
""")


def memory_extraction_prompt(
    chapter_text: str,
    character: dict,
    existing_memories: list[dict],
    characters: list[dict],
    foreshadows: list[dict],
) -> str:
    return MEMORY_EXTRACTION_PROMPT_TEMPLATE.substitute(
        json_rules=JSON_RULES,
        chapter_text=chapter_text,
        character=json.dumps(character, ensure_ascii=False, indent=2),
        existing_memories=json.dumps(existing_memories, ensure_ascii=False, indent=2),
        characters=json.dumps(characters, ensure_ascii=False, indent=2),
        foreshadows=json.dumps(foreshadows, ensure_ascii=False, indent=2),
    )
