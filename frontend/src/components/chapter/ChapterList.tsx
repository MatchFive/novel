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
        {sorted.map((it) => (
          <div
            key={it.id}
            onClick={() => onSelect(it.id)}
            className={
              "mb-2 cursor-pointer border border-line p-3 transition-colors " +
              (selectedId === it.id
                ? "border-accent bg-accent-soft"
                : "bg-surface hover:bg-surface-2")
            }
          >
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={1}
                value={it.order + 1}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (!Number.isNaN(v) && v >= 1) {
                    onSave(it.id, { order: v - 1 });
                  }
                }}
                className="w-16 shrink-0 text-center"
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-ink">
                  {it.title || "（无标题）"}
                </div>
                <div className="text-xs text-muted">
                  {it.status === "generated" ? "已生成" : it.status === "reviewed" ? "已细纲" : "草稿"}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                {onGenerate && (
                  <Button
                    variant="ghost"
                    disabled={generating}
                    onClick={(e) => {
                      e.stopPropagation();
                      onGenerate(it);
                    }}
                  >
                    {it.detailed_outline ? "生成正文" : "生成细纲"}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(it.id);
                  }}
                >
                  删除
                </Button>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="border-t border-line pt-3">
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
            placeholder="序号（留空排最后）"
            value={newOrder}
            onChange={(e) => setNewOrder(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            className="w-32 shrink-0"
          />
          <Button variant="primary" onClick={handleAdd}>
            新增
          </Button>
        </div>
      </div>
    </div>
  );
}
