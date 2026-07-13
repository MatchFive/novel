import { useEffect, useState } from "react";
import { useAssistantSession } from "@/stores/useAssistantSession";
import { Button, Textarea } from "@/components/ui";
import AssistantChat from "./AssistantChat";
import ContextPanel from "./ContextPanel";
import ContextEntityEditor from "./ContextEntityEditor";
import AssistantSessionSidebar from "./AssistantSessionSidebar";
import type { ChangeRecord } from "@/types";

export default function AssistantStudio({ pid }: { pid: string }) {
  const [input, setInput] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [editing, setEditing] = useState<{ kind: string; item: any } | null>(null);

  const {
    messages,
    pendingRecords,
    busy,
    sessionId,
    sessions,
    error,
    loadHistory,
    loadSessions,
    sendMessage,
    createSession,
    switchSession,
    stageChange,
    confirm,
    reject,
  } = useAssistantSession();

  useEffect(() => {
    loadSessions(pid);
    loadHistory(pid);
  }, [pid, loadHistory, loadSessions]);

  const handleSend = async () => {
    if (!input.trim()) return;
    const text = input;
    try {
      await sendMessage(pid, text);
      setInput("");
    } catch {
      // error is already surfaced by the store
    }
  };

  const handleQuote = (kind: string, name: string) => {
    const prefix = kind === "character" ? "@" : "#";
    setInput((prev) => `${prev}${prefix}${name} `);
  };

  const handleEditSave = async (record: ChangeRecord) => {
    await stageChange(record);
    setEditing(null);
  };

  return (
    <div className="flex h-full">
      <AssistantSessionSidebar
        pid={pid}
        sessions={sessions}
        activeId={sessionId}
        onCreate={() => createSession(pid)}
        onSwitch={(id) => switchSession(id, pid)}
      />
      <div className="flex flex-1 flex-col">
        <ContextPanel
          pid={pid}
          open={contextOpen}
          onToggle={() => setContextOpen((v) => !v)}
          onQuote={handleQuote}
          onEdit={(kind, item) => setEditing({ kind, item })}
          pendingRecords={pendingRecords}
        />

        <AssistantChat
          messages={messages}
          pendingRecords={pendingRecords}
          busy={busy}
          error={error}
          onConfirm={confirm}
          onReject={reject}
        />

        <div className="shrink-0 border-t border-line bg-surface p-4">
          <div className="flex items-end gap-2">
            <Textarea
              placeholder="描述创作意图，Enter 发送，Shift+Enter 换行"
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
            <Button variant="primary" onClick={handleSend} disabled={busy || !input.trim()}>
              发送
            </Button>
          </div>
        </div>

        {editing && (
          <ContextEntityEditor
            kind={editing.kind}
            item={editing.item}
            onSave={handleEditSave}
            onCancel={() => setEditing(null)}
          />
        )}
      </div>
    </div>
  );
}
