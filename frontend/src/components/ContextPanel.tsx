import { useEffect, useState } from "react";
import { longApi } from "@/api/long";
import { Button, Card } from "@/components/ui";

import type { ChangeRecord } from "@/types";

const CATEGORIES = [
  { key: "outline", label: "大纲", list: longApi.outlines, nameField: "title" },
  { key: "character", label: "角色", list: longApi.characters, nameField: "name" },
  { key: "foreshadow", label: "伏笔", list: longApi.foreshadows, nameField: "title" },
  { key: "world", label: "世界观", list: longApi.world, nameField: "category" },
  { key: "plot", label: "剧情节点", list: longApi.plot, nameField: "title" },
  { key: "chapter", label: "章节", list: longApi.chapters, nameField: "title" },
];

interface ContextPanelProps {
  pid: string;
  open: boolean;
  onToggle: () => void;
  onQuote: (kind: string, name: string) => void;
  onEdit: (kind: string, item: any) => void;
  pendingRecords: ChangeRecord[];
}

export default function ContextPanel({ pid, open, onToggle, onQuote, onEdit, pendingRecords }: ContextPanelProps) {
  const [active, setActive] = useState("character");
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const category = CATEGORIES.find((c) => c.key === active);
    if (!category) return;
    setError(null);
    category
      .list(pid)
      .then(({ data }) => setItems(data || []))
      .catch((err) => {
        setError(err instanceof Error ? err.message : "加载失败");
        setItems([]);
      });
  }, [pid, active, open]);

  const activeCategory = CATEGORIES.find((c) => c.key === active)!;

  return (
    <div className="border-b border-line bg-surface">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-4 py-2 text-sm font-medium text-ink hover:bg-surface-2"
      >
        <span>上下文 {open ? "▼" : "▶"}</span>
        <span className="text-xs text-muted">大纲 · 角色 · 伏笔 · 世界观 · 剧情节点 · 章节</span>
      </button>

      {open && (
        <div className="border-t border-line p-4">
          <div className="mb-3 flex gap-2 overflow-x-auto">
            {CATEGORIES.map((c) => (
              <button
                key={c.key}
                onClick={() => setActive(c.key)}
                className={`whitespace-nowrap border px-2 py-1 text-xs ${
                  active === c.key ? "border-accent bg-accent-soft text-accent-strong" : "border-line text-muted"
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>

          <div className="grid max-h-48 grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
            {error && (
              <div className="col-span-full text-xs text-accent">加载失败：{error}</div>
            )}
            {!error && items.map((it) => {
              const kind = activeCategory.key;
              const name = it[activeCategory.nameField] || "（未命名）";
              const isPending = pendingRecords.some(
                (r) => r.entity_type === kind && r.entity_id === it.id
              );
              return (
                <Card key={it.id} className="p-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium text-ink">{name}</span>
                    <div className="flex shrink-0 gap-1">
                      <Button
                        variant="ghost"
                        className="h-6 px-1.5 text-[11px]"
                        onClick={() => onQuote(kind, name)}
                      >
                        引用
                      </Button>
                      <Button
                        variant="ghost"
                        className="h-6 px-1.5 text-[11px]"
                        onClick={() => onEdit(kind, it)}
                        disabled={isPending}
                      >
                        编辑
                      </Button>
                    </div>
                  </div>
                </Card>
              );
            })}
            {!error && items.length === 0 && (
              <div className="col-span-full text-xs text-muted">暂无{activeCategory.label}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
