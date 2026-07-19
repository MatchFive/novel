import { useState } from "react";
import { Button, Input } from "@/components/ui";
import type { Chapter } from "@/types";

interface ChapterListProps {
  items: Chapter[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAdd: (title: string) => void;
  onDelete: (id: string) => void;
  onMove: (id: string, direction: -1 | 1) => void;
  onGenerate?: (chapter: Chapter) => void;
  generating?: boolean;
}

export function ChapterList({
  items,
  selectedId,
  onSelect,
  onAdd,
  onDelete,
  onMove,
  onGenerate,
  generating,
}: ChapterListProps) {
  const sorted = [...items].sort((a, b) => a.order - b.order);
  const [newTitle, setNewTitle] = useState("");

  const handleAdd = () => {
    const title = newTitle.trim();
    if (!title) return;
    onAdd(title);
    setNewTitle("");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-auto">
        {sorted.map((it, index) => (
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
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-ink">
                  {it.title || "（无标题）"}
                </div>
                <div className="text-xs text-muted">
                  第 {index + 1} 章 · {it.status || "draft"}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button
                  variant="ghost"
                  disabled={index === 0}
                  onClick={(e) => {
                    e.stopPropagation();
                    onMove(it.id, -1);
                  }}
                >
                  上移
                </Button>
                <Button
                  variant="ghost"
                  disabled={index === sorted.length - 1}
                  onClick={(e) => {
                    e.stopPropagation();
                    onMove(it.id, 1);
                  }}
                >
                  下移
                </Button>
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
          <Button variant="primary" onClick={handleAdd}>
            新增
          </Button>
        </div>
      </div>
    </div>
  );
}
