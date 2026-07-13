import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAssistantSession } from "@/stores/useAssistantSession";
import { Button, Textarea } from "@/components/ui";
import AssistantChat from "./AssistantChat";

export default function FloatingAssistant() {
  const [isOpen, setIsOpen] = useState(false);
  const [input, setInput] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  const location = useLocation();

  const {
    messages,
    pendingRecords,
    busy,
    error,
    loadHistory,
    loadSessions,
    sendMessage,
    confirm,
    reject,
    reset,
  } = useAssistantSession();

  useEffect(() => {
    const match = location.pathname.match(/\/project\/(?:long|short)\/([^/]+)/);
    const nextId = match?.[1] || null;
    setProjectId(nextId);

    if (nextId) {
      loadSessions(nextId);
      loadHistory(nextId);
    } else {
      reset();
    }
  }, [location.pathname, loadHistory, loadSessions, reset]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || busy) return;

    const context: Record<string, any> = { page_path: location.pathname };
    if (projectId) {
      context.project_id = projectId;
    }

    try {
      await sendMessage(projectId, text, context);
      setInput("");
    } catch {
      // error is surfaced by the store
    }
  };

  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="fixed bottom-4 right-4 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-accent text-paper shadow-lg transition-colors hover:bg-accent-strong"
        aria-label="打开创作助手"
      >
        <span className="text-sm font-semibold">AI</span>
      </button>
    );
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 flex h-[560px] w-[400px] flex-col rounded-lg border border-line bg-paper shadow-xl">
      <div className="flex items-center justify-between rounded-t-lg border-b border-line bg-surface px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-ink">创作助手</span>
          {!projectId && (
            <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] text-accent-strong">
              通用
            </span>
          )}
        </div>
        <button
          onClick={() => setIsOpen(false)}
          className="text-muted transition-colors hover:text-ink"
          aria-label="收起创作助手"
        >
          ✕
        </button>
      </div>

      <div className="flex flex-1 flex-col overflow-hidden">
        <AssistantChat
          messages={messages}
          pendingRecords={projectId ? pendingRecords : []}
          busy={busy}
          error={error}
          onConfirm={confirm}
          onReject={reject}
        />
      </div>

      <div className="shrink-0 rounded-b-lg border-t border-line bg-surface p-3">
        <div className="flex items-end gap-2">
          <Textarea
            placeholder={
              projectId
                ? "描述创作意图，Enter 发送，Shift+Enter 换行"
                : "输入通用问题，Enter 发送，Shift+Enter 换行"
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            disabled={busy}
            rows={3}
            className="resize-none"
          />
          <Button
            variant="primary"
            onClick={handleSend}
            disabled={busy || !input.trim()}
            className="shrink-0"
          >
            发送
          </Button>
        </div>
      </div>
    </div>
  );
}
