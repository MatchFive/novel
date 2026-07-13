import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui";
import ChangeRecordCard from "./ChangeRecordCard";
import type { AssistantMessage, ChangeRecord } from "@/types";

interface AssistantChatProps {
  messages: AssistantMessage[];
  pendingRecords: ChangeRecord[];
  busy: boolean;
  error: string | null;
  onConfirm: (changeIds?: string[]) => void;
  onReject: (changeIds?: string[]) => void;
  onSend?: (text: string) => void;
  onStageChange?: (record: ChangeRecord) => void;
}

const REGENERATE_TEXTS: Record<string, string> = {
  broad_outline: "重新生成总纲",
  plot_nodes: "重新生成剧情节点",
  assignment: "重新分配剧情节点到章节",
  chapter_outline: "重新生成当前章节细纲",
  chapter_text: "重新生成当前章节正文",
};

const QUICK_ACTIONS = [
  { stage: "broad_outline", label: "重新生成总纲" },
  { stage: "plot_nodes", label: "重新生成剧情节点" },
  { stage: "assignment", label: "重新分配章节" },
  { stage: "chapter_outline", label: "重新生成细纲" },
  { stage: "chapter_text", label: "重新生成正文" },
];

function StatusBadge({ metadata }: { metadata?: AssistantMessage["metadata"] }) {
  if (!metadata?.status) return null;
  if (metadata.status === "applied") {
    return <span className="text-xs text-accent">✓ 已应用 {metadata.applied_count || 0} 条</span>;
  }
  if (metadata.status === "partial") {
    return (
      <span className="text-xs text-accent">
        ⚠ 部分应用：已应用 {metadata.applied_count || 0} 条，失败 {metadata.error_count || 0} 条
      </span>
    );
  }
  return <span className="text-xs text-muted">✗ 已拒绝 {metadata.rejected_count || 0} 条</span>;
}

export default function AssistantChat({
  messages,
  pendingRecords,
  busy,
  error,
  onConfirm,
  onReject,
  onSend,
  onStageChange,
}: AssistantChatProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingRecords]);

  // pendingRecords 变化时（如新消息或应用后），清理已不存在的选中
  useEffect(() => {
    setSelectedIds((prev) => {
      const valid = new Set(pendingRecords.map((r) => r.id));
      const next = new Set<string>();
      prev.forEach((id) => valid.has(id) && next.add(id));
      return next;
    });
  }, [pendingRecords]);

  const showActions = pendingRecords.length > 0;
  const allSelected = pendingRecords.length > 0 && selectedIds.size === pendingRecords.length;

  const toggle = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(pendingRecords.map((r) => r.id)));
    }
  };

  const handleConfirm = () => {
    if (selectedIds.size > 0) onConfirm(Array.from(selectedIds));
    else onConfirm();
  };

  const handleReject = () => {
    if (selectedIds.size > 0) onReject(Array.from(selectedIds));
    else onReject();
  };

  const handleEdit = (recordId: string, updatedAfter: any) => {
    if (!onStageChange) return;
    const original = pendingRecords.find((r) => r.id === recordId);
    if (!original) return;
    const newRecord: ChangeRecord = {
      ...original,
      after: updatedAfter,
    };
    onStageChange(newRecord);
  };

  const handleRegenerate = (stage: string) => {
    if (!onSend) return;
    const text = REGENERATE_TEXTS[stage];
    if (text) onSend(text);
  };

  return (
    <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
      {error && (
        <div className="rounded-none border border-accent bg-surface p-3 text-xs text-accent">
          出错了：{error}
        </div>
      )}

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
            <div className="markdown-body select-text">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
            </div>
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
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium text-ink">待确认变更（{pendingRecords.length}）</div>
            <button
              onClick={toggleAll}
              className="text-xs text-accent hover:text-accent-strong"
            >
              {allSelected ? "取消全选" : "全选"}
            </button>
          </div>

          <div className="flex flex-wrap gap-2 rounded-none border border-line bg-paper p-2">
            {QUICK_ACTIONS.map((action) => (
              <Button
                key={action.stage}
                variant="subtle"
                className="px-2 py-0.5 text-xs"
                disabled={busy || !onSend}
                onClick={() => handleRegenerate(action.stage)}
              >
                {action.label}
              </Button>
            ))}
          </div>

          <div className="space-y-2">
            {pendingRecords.map((r) => (
              <ChangeRecordCard
                key={r.id}
                record={r}
                selected={selectedIds.has(r.id)}
                onToggle={() => toggle(r.id)}
                onConfirm={() => onConfirm([r.id])}
                onReject={() => onReject([r.id])}
                onEdit={onStageChange ? handleEdit : undefined}
                onRegenerate={onSend ? handleRegenerate : undefined}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={handleConfirm} disabled={busy}>
              {selectedIds.size > 0 ? "确认选中" : "确认全部"}
            </Button>
            <Button variant="ghost" onClick={handleReject} disabled={busy}>
              {selectedIds.size > 0 ? "拒绝选中" : "拒绝全部"}
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
