# Worker 语义相关上下文检索设计

## 背景

助手 Worker（CharacterWorker / OutlineWorker / PlotWorker / ForeshadowWorker / WorldWorker）在生成变更建议时，需要了解项目里与当前用户目标**语义相关**的现有内容，避免：

1. 创建重复或冲突的实体（如重复创建同名角色）。
2. 输出与已有设定不一致的内容（如给角色添加与现有世界观矛盾的设定）。
3. 忽略角色关系、伏笔回收、剧情节点之间的关联。

## 目标

为全部 Worker 提供统一的「语义相关上下文检索」能力：

- **覆盖范围**：全部 Worker。
- **判定方式**：关键词粗筛 + LLM 精选。
- **数量**：每类实体最多 5 条。
- **内容粒度**：返回完整字段内容。

## 总体方案

新增 `ContextBuilder` 模块，所有 Worker 通过统一接口获取相关上下文，再拼接到各自的 prompt 中。

```
User Goal
    │
    ▼
┌─────────────────┐
│ ContextBuilder  │
│  1. 拉取项目实体 │
│  2. 关键词粗筛   │
│  3. LLM 精选    │
│  4. 格式化输出  │
└────────┬────────┘
         │ related_context: str
         ▼
┌─────────────────┐
│ CharacterWorker │
│ OutlineWorker   │
│ PlotWorker      │
│ ForeshadowWorker│
│ WorldWorker     │
└─────────────────┘
```

## 组件设计

### `app/agents/harness/context_builder.py`

#### `ContextBuilder`

```python
class ContextBuilder:
    def __init__(self, db: AsyncSession, llm: LLMClient):
        self.db = db
        self.llm = llm

    async def build(
        self,
        project_id: str,
        query: str,
        focus_entity_type: str | None = None,
    ) -> str:
        ...
```

**参数说明**

| 参数 | 说明 |
|---|---|
| `project_id` | 当前项目 id。 |
| `query` | 用户目标文本，作为相关性判断的查询。 |
| `focus_entity_type` | 当前 Worker 聚焦的实体类型（如 `character`），仅用于提示 LLM 重点，不排除其他类型。 |

**返回**：一段 Markdown 格式的文本，可直接插入 prompt。若未找到相关条目，返回空字符串。

### 检索流程

#### 1. 拉取项目实体

通过 `repositories` 一次性拉取项目下所有实体：

- `characters`
- `outlines`
- `plot_nodes`
- `foreshadows`
- `world_settings`

#### 2. 关键词粗筛

对每个实体类型独立处理：

1. 从 `query` 提取关键词：
   - 按中文标点、空格、换行切分。
   - 过滤掉常见停用词（的、了、是、我、你 等）。
   - 保留长度 >= 2 的词条以及所有英文/数字 token。
2. 计算每个实体与关键词的匹配得分：
   - 名称/标题/分类命中：+3
   - 内容命中：+1
   - 命中次数累加。
3. 每类取得分最高的前 `COARSE_TOP_N`（默认 15）条作为候选。

> 未来可替换为 embedding 相似度，接口保持不变。

#### 3. LLM 精选

把粗筛候选按实体类型分组，构造 prompt 发给 LLM：

```
你是小说创作助手的内容检索器。

用户目标：<query>
当前关注实体类型：<focus_entity_type>

下面是从项目中粗筛出的候选条目，按类型分组，每条包含 id 和完整内容。
请为每个实体类型选出与用户目标最相关的最多 5 个条目 id。

相关标准：
- 用户目标中明确提到或可能引用该条目。
- 该条目的内容会影响当前变更决策。
- 保持世界观、角色关系、剧情逻辑一致需要参考该条目。

返回严格 JSON，不要解释：
{
  "character": ["id1", "id2"],
  "outline": [],
  "plot": ["id3"],
  "foreshadow": [],
  "world": ["id4"]
}
```

解析返回 JSON，按 id 取出对应实体。

#### 4. 格式化输出

把 LLM 选中的实体按类型格式化为 Markdown：

```markdown
## 相关角色
- [c1] 刘修
  性格：...
  能力：...
  状态：...

## 相关大纲
- [o1] 开篇：穿越
  内容：...

...
```

## Worker 集成

每个 Worker 的 `run` 方法中：

1. 实例化 `ContextBuilder`。
2. 调用 `related = await builder.build(project_id, goal, worker_name)`。
3. 把 `related` 拼入 system 或 user prompt。

以 `CharacterWorker` 为例：

```python
async def run(self, goal: str, context: dict, history_context: list[dict] | None = None) -> dict:
    from app.agents.harness.context_builder import ContextBuilder

    project_id = context.get("project_id")
    related = ""
    if project_id:
        builder = ContextBuilder(self.db, self.llm)
        related = await builder.build(project_id, goal, "character")

    chars = context.get("characters") or []
    chars_desc = "\n".join(
        f"- {c.get('name')} (id={c.get('id')})"
        for c in chars
    ) or "暂无现有角色。"

    system = (
        "你是角色设计师。基于用户目标设计或调整角色，最终以 JSON 返回建议变更："
        '{"changes": [{"action":"add|update", "entity_id":null或id, '
        '"fields": {"name":"", "traits":"", "ability":"", "status":"", "relations":[], "importance":0}}]}\n\n'
        "重要规则：\n"
        "1. 若用户目标中的角色 name 与现有角色 name 完全相同，必须返回 action='update'。\n"
        "2. 只有 name 不存在时，才返回 action='add'。\n"
        "3. 参考【相关上下文】保持与现有设定一致，不要创建重复角色。\n"
    )
    user_prompt = f"【现有角色】\n{chars_desc}\n\n【相关上下文】\n{related}\n\n【用户目标】\n{goal}"
    return await self._tool_loop(system, user_prompt, history_context=history_context)
```

## 错误处理与降级

| 场景 | 处理 |
|---|---|
| LLM 返回非法 JSON | 回退到粗筛 Top-5。 |
| LLM 返回空相关列表 | 返回空字符串，不影响 Worker 继续执行。 |
| 某类实体粗筛为空 | 该类不向 LLM 展示，最终结果中省略该类。 |
| 关键词提取为空 | 对所有实体按更新时间取最近 5 条作为候选。 |

## 性能与成本

- 每次 Worker 调用增加 1 次 LLM 调用（精选阶段）。
- 粗筛在本地完成，无额外 LLM 成本。
- 后续若成本敏感，可缓存 embedding 或关键词索引；本版不做。

## 测试策略

1. **单元测试 `ContextBuilder`**：
   - 用 mock LLM 验证精选逻辑。
   - 验证关键词提取与粗筛得分。
   - 验证 LLM 失败时的回退行为。
2. **Worker 集成测试**：
   - 验证 `CharacterWorker` prompt 中包含相关上下文。
3. **端到端测试**：
   - 在测试项目中先创建一个角色，再请求修改该角色，确认返回 `action='update'` 而非 `add`。

## 后续可扩展

- 用 embedding 替代关键词粗筛。
- 按实体关系图（如角色 relations、大纲 parent_id）做显式关联召回。
- 对超长内容做摘要后再放入上下文。

## 依赖

- 复用现有 `app.repositories` 和 `app.core.llm_client.LLMClient`。
- 新增一个中文停用词常量列表，无需外部库。
