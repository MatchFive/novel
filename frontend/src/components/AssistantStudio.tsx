import { useEffect, useState } from "react";
import { useAssistantSession } from "@/stores/useAssistantSession";
import { Button, Input } from "@/components/ui";
import AssistantChat from "./AssistantChat";
import ContextPanel from "./ContextPanel";
import ContextEntityEditor from "./ContextEntityEditor";
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
    error,
    loadHistory,
    sendMessage,
    stageChange,
    confirm,
    reject,
  } = useAssistantSession();

  useEffect(() => {
    loadHistory(pid);
  }, [pid, loadHistory]);

  const handleSend = async () => {
    if (!input.trim()) return;
    const text = input;
    await sendMessage(pid, text);
    setInput("");
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
    <div className="flex h-full flex-col">
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
        <div className="flex gap-2">
          <Input
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
  );
}
