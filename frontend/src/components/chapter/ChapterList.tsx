import { useState } from "react";
import { Button, Input } from "@/components/ui";
import type { Chapter } from "@/types";

interface ChapterListProps {
  items: Chapter[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAdd: (title: string, order?: number) => void;
  onSave: (id: string, data: Partial<Chapter>) => void;
  onDelete: (id: string) => void;
  onGenerate?: (chapter: Chapter) => void;
  generating?: boolean;
}

function statusBadge(ch: Chapter) {
  if (ch.content && ch.content.trim().length > 50) {
    return <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent-strong">正文</span>;
  }
  if (ch.detailed_outline && ch.detailed_outline.trim()) {
    return <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-ink-soft">细纲</span>;
  }
  return <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-muted">草稿</span>;
}

export function ChapterList({
  items,
  selectedId,
  onSelect,
  onAdd,
  onSave,
  onDelete,
  onGenerate,
  generating,
}: ChapterListProps) {
  const sorted = [...items].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const [newTitle, setNewTitle] = useState("");
  const [newOrder, setNewOrder] = useState("");

  const handleAdd = () => {
    const title = newTitle.trim();
    if (!title) return;
    const order = newOrder.trim() ? Number(newOrder.trim()) : undefined;
    onAdd(title, order);
    setNewTitle("");
    setNewOrder("");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-auto">
        {sorted.length === 0 && (
          <div className="px-2 py-8 text-center text-sm text-muted">暂无章节，点击下方新增</div>
        )}
        {sorted.map((it) => {
          const selected = selectedId === it.id;
          return (
            <div
              key={it.id}
              onClick={() => onSelect(it.id)}
              className={
                "group relative flex cursor-pointer items-center gap-3 border-b border-line px-3 py-2.5 transition-colors " +
                (selected ? "bg-accent-soft" : "bg-surface hover:bg-surface-2")
              }
            >
              <div
                className={
                  "flex h-9 w-9 shrink-0 items-center justify-center border font-serif text-base " +
                  (selected ? "border-accent bg-paper text-accent" : "border-line bg-surface-2 text-muted")
                }
              >
                {it.order + 1}
              </div>

              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-ink">
                  {it.title || "（未命名章节）"}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  {statusBadge(it)}
                </div>
              </div>

              <div className="flex shrink-0 items-center gap-1">
                {onGenerate && (
                  <Button
                    variant="ghost"
                    disabled={generating}
                    onClick={(e: React.MouseEvent) => {
                      e.stopPropagation();
                      onGenerate(it);
                    }}
                    className="px-2 py-1 text-xs"
                  >
                    {it.detailed_outline ? "正文" : "细纲"}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    onDelete(it.id);
                  }}
                  className="px-2 py-1 text-xs opacity-0 transition-opacity group-hover:opacity-100"
                >
                  删除
                </Button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="border-t border-line bg-surface p-3">
        <div className="flex gap-2">
          <Input
            placeholder="新章节标题"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          />
          <Input
            type="number"
            min={1}
            placeholder="序号"
            value={newOrder}
            onChange={(e) => setNewOrder(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            className="w-20 shrink-0"
          />
          <Button variant="primary" onClick={handleAdd} className="shrink-0">
            新增
          </Button>
        </div>
      </div>
    </div>
  );
}
