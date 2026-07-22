import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAssistantSession } from "@/stores/useAssistantSession";
import { Button, Textarea } from "@/components/ui";
import { useResizable, type ResizeDirection } from "@/hooks/useResizable";
import AssistantChat from "./AssistantChat";

export default function FloatingAssistant() {
  const [input, setInput] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [lastContext, setLastContext] = useState<Record<string, any> | undefined>(undefined);
  const [isMaximized, setIsMaximized] = useState(false);
  const location = useLocation();

  const { size, setSize, startResize } = useResizable({
    initial: { width: 400, height: 560 },
    min: { width: 320, height: 400 },
    max: { width: 1200, height: 900 },
    storageKey: "novel-assistant-panel-size",
  });

  const {
    messages,
    pendingRecords,
    busy,
    error,
    assistantOpen,
    sessions,
    sessionId,
    setAssistantOpen,
    loadHistory,
    loadSessions,
    sendMessage,
    createSession,
    switchSession,
    confirm,
    reject,
    stageChange,
    undoAuto,
    reset,
  } = useAssistantSession();

  useEffect(() => {
    const match = location.pathname.match(/\/project\/(?:long|short)\/([^/]+)/);
    const nextId = match?.[1] || null;
    const pid = nextId || "global";
    setProjectId(pid);

    loadSessions(pid);
    loadHistory(pid);
  }, [location.pathname, loadHistory, loadSessions]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || busy) return;

    const context: Record<string, any> = { page_path: location.pathname };
    if (projectId && projectId !== "global") {
      context.project_id = projectId;
    }
    setLastContext(context);

    try {
      await sendMessage(projectId === "global" ? null : projectId, text, context);
      setInput("");
    } catch {
      // error is surfaced by the store
    }
  };

  const handleNewSession = async () => {
    if (!projectId) return;
    await createSession(projectId);
  };

  const handleSwitch = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const sid = e.target.value;
    if (!sid || !projectId) return;
    await switchSession(sid, projectId);
  };

  const toggleMaximize = () => {
    setIsMaximized((prev) => !prev);
  };

  if (!assistantOpen) {
    return (
      <button
        onClick={() => setAssistantOpen(true)}
        className="fixed bottom-4 right-4 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-accent text-paper shadow-lg transition-colors hover:bg-accent-strong"
        aria-label="打开创作助手"
      >
        <span className="text-sm font-semibold">AI</span>
      </button>
    );
  }

  const panelStyle: React.CSSProperties = isMaximized
    ? { width: "calc(100vw - 2rem)", height: "calc(100vh - 2rem)" }
    : { width: size.width, height: size.height };

  const resizeZones: { dir: ResizeDirection; className: string; cursor: string }[] = [
    { dir: "n", className: "absolute left-2 right-2 top-0 h-1.5", cursor: "ns-resize" },
    { dir: "s", className: "absolute bottom-0 left-2 right-2 h-1.5", cursor: "ns-resize" },
    { dir: "w", className: "absolute bottom-2 left-0 top-2 w-1.5", cursor: "ew-resize" },
    { dir: "e", className: "absolute bottom-2 right-0 top-2 w-1.5", cursor: "ew-resize" },
    { dir: "nw", className: "absolute left-0 top-0 h-3 w-3", cursor: "nwse-resize" },
    { dir: "ne", className: "absolute right-0 top-0 h-3 w-3", cursor: "nesw-resize" },
    { dir: "sw", className: "absolute bottom-0 left-0 h-3 w-3", cursor: "nesw-resize" },
    { dir: "se", className: "absolute bottom-0 right-0 h-3 w-3", cursor: "nwse-resize" },
  ];

  return (
    <div
      className="fixed bottom-4 right-4 z-50 flex flex-col rounded-lg border border-line bg-paper shadow-xl"
      style={panelStyle}
    >
      <div className="flex items-center justify-between rounded-t-lg border-b border-line bg-surface px-3 py-2">
        <div className="flex items-center gap-2 overflow-hidden pl-4">
          <span className="text-sm font-semibold text-ink">创作助手</span>
          {projectId === "global" && (
            <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] text-accent-strong">
              通用
            </span>
          )}
          {sessions.length > 0 && (
            <select
              value={sessionId || ""}
              onChange={handleSwitch}
              className="ml-1 max-w-[140px] truncate rounded border border-line bg-paper px-1 py-0.5 text-xs text-ink outline-none"
              title="切换对话"
            >
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={handleNewSession}
            disabled={busy}
            className="flex h-6 w-6 items-center justify-center rounded border border-line text-xs text-ink transition-colors hover:bg-accent hover:text-paper disabled:opacity-50"
            title="新建对话"
            aria-label="新建对话"
          >
            +
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={toggleMaximize}
            className="px-1 text-xs text-muted transition-colors hover:text-ink"
            title={isMaximized ? "还原" : "最大化"}
            aria-label={isMaximized ? "还原" : "最大化"}
          >
            {isMaximized ? "🗗" : "🗖"}
          </button>
          <button
            onClick={() => setAssistantOpen(false)}
            className="px-1 text-muted transition-colors hover:text-ink"
            aria-label="收起创作助手"
          >
            ✕
          </button>
        </div>
      </div>

      <div className="flex flex-1 flex-col overflow-hidden">
        <AssistantChat
          messages={messages}
          pendingRecords={projectId && projectId !== "global" ? pendingRecords : []}
          busy={busy}
          error={error}
          onConfirm={confirm}
          onReject={reject}
          onSend={(text, ctx) =>
            sendMessage(projectId === "global" ? null : projectId, text, ctx)
          }
          onStageChange={stageChange}
          onUndo={(item) => {
            if (projectId && projectId !== "global") {
              undoAuto(projectId, item.entity_type, item.entity_id);
            }
          }}
          sendContext={lastContext}
        />
      </div>

      <div className="shrink-0 rounded-b-lg border-t border-line bg-surface p-3">
        <div className="flex items-end gap-2">
          <Textarea
            placeholder={
              projectId && projectId !== "global"
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

      {!isMaximized && (
        <>
          {resizeZones.map((z) => (
            <div
              key={z.dir}
              onMouseDown={(e) => startResize(e, z.dir)}
              className={`z-10 ${z.className}`}
              style={{ cursor: z.cursor }}
            />
          ))}
          {/* 可见手柄：左上角（面板锚定右下，向左上拖拽即放大） */}
          <div
            onMouseDown={(e) => startResize(e, "nw")}
            className="absolute left-0 top-0 z-20 flex h-5 w-5 cursor-nwse-resize items-center justify-center"
            title="拖拽缩放"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="text-muted">
              <path d="M4 0V4H0" stroke="currentColor" strokeWidth="1" />
              <path d="M8 0V8H0" stroke="currentColor" strokeWidth="1" />
            </svg>
          </div>
        </>
      )}
    </div>
  );
}
