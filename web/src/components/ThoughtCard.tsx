/// 💭 思考卡（§4.4.1 / §4.4.2）：
/// 收起态 `💭 思考 · agent_step · seq N · M 字` + ▶；展开显示 thought 原文（逐字不截断）。
/// agent_step 事件还带一行 say（有值才渲染），跟在思考卡后面。

import { str } from "./payload";

export function ThoughtCard({
  seq,
  payload,
  expanded,
  onToggle,
}: {
  seq: number;
  payload: Record<string, unknown>;
  expanded: boolean;
  onToggle: () => void;
}) {
  const thought = str(payload.thought);
  const say = str(payload.say);
  const action = str(payload.action);

  return (
    <div className="thought-block">
      <button
        type="button"
        className={expanded ? "fold-head expanded" : "fold-head"}
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="fold-marker">{expanded ? "▼" : "▶"}</span>
        <span className="fold-title">
          💭 思考 · agent_step · seq {seq} · {thought.length} 字
        </span>
      </button>
      {expanded && (
        <div className="fold-body">
          <p className="thought-text">{thought}</p>
          {action && <p className="thought-action muted">→ 动作：{action}</p>}
        </div>
      )}
      {say && <div className="say-line">💬 {say}</div>}
    </div>
  );
}
