import { useEffect, useRef } from "react";
import { Button } from "@/components/ui";
import ChangeRecordCard from "./ChangeRecordCard";
import type { AssistantMessage, ChangeRecord } from "@/types";

interface AssistantChatProps {
  messages: AssistantMessage[];
  pendingRecords: ChangeRecord[];
  busy: boolean;
  onConfirm: () => void;
  onReject: () => void;
}

function StatusBadge({ metadata }: { metadata?: AssistantMessage["metadata"] }) {
  if (!metadata?.status) return null;
  if (metadata.status === "applied") {
    return <span className="text-xs text-accent">✓ 已应用 {metadata.applied_count || 0} 条</span>;
  }
  return <span className="text-xs text-muted">✗ 已拒绝</span>;
}

export default function AssistantChat({
  messages,
  pendingRecords,
  busy,
  onConfirm,
  onReject,
}: AssistantChatProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingRecords]);

  let lastAssistantIndex = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      lastAssistantIndex = i;
      break;
    }
  }
  const lastAssistant = lastAssistantIndex >= 0 ? messages[lastAssistantIndex] : undefined;
  const showActions = pendingRecords.length > 0 && lastAssistant && !lastAssistant.metadata?.status;

  return (
    <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
      {messages.length === 0 && !busy && (
        <div className="py-10 text-center text-sm text-muted">
          描述你的创作意图，例如“为主角增加一个宿敌角色”。
        </div>
      )}

      {messages.map((m) => (
        <div
          key={m.id}
          className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`max-w-[80%] space-y-2 ${
              m.role === "user" ? "bg-accent text-paper" : "bg-surface border border-line"
            } p-3 text-sm`}
          >
            <div className="whitespace-pre-wrap">{m.content}</div>
            {m.role === "assistant" && (
              <div className="flex items-center justify-between gap-4 pt-1">
                <StatusBadge metadata={m.metadata} />
              </div>
            )}
          </div>
        </div>
      ))}

      {showActions && (
        <div className="space-y-3 rounded-none border border-line bg-surface p-4">
          <div className="text-sm font-medium text-ink">待确认变更（{pendingRecords.length}）</div>
          <div className="space-y-2">
            {pendingRecords.map((r) => (
              <ChangeRecordCard key={r.id} record={r} />
            ))}
          </div>
          <div className="flex gap-2">
            <Button variant="primary" onClick={onConfirm} disabled={busy}>
              确认应用
            </Button>
            <Button variant="ghost" onClick={onReject} disabled={busy}>
              拒绝
            </Button>
          </div>
        </div>
      )}

      {busy && (
        <div className="text-xs text-muted">助手思考中…</div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
