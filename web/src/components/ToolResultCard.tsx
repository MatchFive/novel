/// ⚙ 工具结果卡（§4.4.3）：
/// 收起态 `⚙ 工具名 · 参数摘要 · 耗时`（参数渲染「键: 值」，长值截 60 字符给 title）；
/// 展开显示 observation 全文（服务端 OBS_MAX 已截过一次，前端不再二次截断）。

import { summarizeArgs } from "./payload";
import type { ArgSummaryItem } from "./payload";

export function ToolResultCard({
  seq,
  tool,
  observation,
  args,
  durationMs,
  expanded,
  onToggle,
}: {
  seq: number;
  tool: string;
  observation: string;
  args: Record<string, unknown>;
  /** 与紧邻上一条 agent_step 的 ts 差；无则 null（不显示耗时）。 */
  durationMs: number | null;
  expanded: boolean;
  onToggle: () => void;
}) {
  const argItems: ArgSummaryItem[] = summarizeArgs(args);

  return (
    <div className="tool-block">
      <button
        type="button"
        className={expanded ? "fold-head expanded" : "fold-head"}
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="fold-marker">{expanded ? "▼" : "▶"}</span>
        <span className="fold-title">
          ⚙ {tool}
          {argItems.length > 0 && (
            <span className="fold-args">
              {" · "}
              {argItems.map((item) => (
                <span key={item.key} className="arg-kv" title={item.truncated ? item.full : undefined}>
                  {item.key}: {item.display}
                </span>
              ))}
            </span>
          )}
          {durationMs !== null && <span className="fold-duration"> · {formatDuration(durationMs)}</span>}
          <span className="muted"> · seq {seq}</span>
        </span>
      </button>
      {expanded && <pre className="fold-body obs-text">{observation}</pre>}
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
