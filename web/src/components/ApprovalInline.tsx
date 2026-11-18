/// 已答嵌入（§4.5.1）：琥珀占位被答掉后变成流里一行「✓ 已选 N · 备注」。
/// plan_rejected 用 ✗ 红调；labels 缺失（别处分支答掉、只有 cleared 信号）时退化为「已答」。

interface ApprovalInlineProps {
  tone?: "ok" | "rejected";
  /** 已选摘要，如 "已选 改、不改"；null → 显示「已答」。 */
  label: string | null;
  note?: string;
}

export function ApprovalInline({ tone = "ok", label, note }: ApprovalInlineProps) {
  return (
    <div
      className={`stream-line approval-inline ${
        tone === "rejected" ? "line-rejected" : "line-answer"
      }`}
    >
      {tone === "rejected" ? "✗" : "✓"} {label ?? "已答"}
      {note && <span className="muted"> · 备注 {note}</span>}
    </div>
  );
}
