import { useEffect } from "react";
import { Button } from "@/components/ui";
import type { AssistantSession } from "@/types";

interface Props {
  pid: string;
  sessions: AssistantSession[];
  activeId: string | null;
  onCreate: () => void;
  onSwitch: (id: string) => void;
}

export default function AssistantSessionSidebar({ pid, sessions, activeId, onCreate, onSwitch }: Props) {
  useEffect(() => {
    // 首次挂载由父组件 load
  }, [pid]);

  return (
    <div className="flex h-full w-52 shrink-0 flex-col border-r border-line bg-surface">
      <div className="border-b border-line p-3">
        <Button variant="primary" className="w-full" onClick={onCreate}>
          + 新建对话
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {sessions.length === 0 && <div className="p-2 text-xs text-muted">暂无对话</div>}
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => onSwitch(s.id)}
            className={
              "mb-1 w-full rounded border px-3 py-2 text-left text-sm " +
              (s.id === activeId
                ? "border-accent bg-accent-soft text-ink"
                : "border-transparent text-muted hover:bg-surface-2 hover:text-ink")
            }
          >
            <div className="truncate font-medium">{s.title}</div>
            <div className="text-[11px] opacity-70">
              {s.updated_at ? new Date(s.updated_at).toLocaleString() : "--"}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
