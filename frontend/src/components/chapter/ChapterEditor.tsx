import { useEffect, useState } from "react";
import { Button, Input, Textarea, Card, Empty } from "@/components/ui";
import { useAssistantSession } from "@/stores/useAssistantSession";
import type { Chapter } from "@/types";

interface ChapterEditorProps {
  chapter: Chapter | null;
  onSave: (id: string, data: Partial<Chapter>) => void;
  onUndo?: () => void;
  undoable?: boolean;
}

export function ChapterEditor({ chapter, onSave, onUndo, undoable }: ChapterEditorProps) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  useEffect(() => {
    if (chapter) {
      setTitle(chapter.title || "");
      setContent(chapter.content || "");
    }
  }, [chapter?.id]);

  if (!chapter) {
    return (
      <div className="flex h-full items-center justify-center">
        <Empty text="在左侧选择或新增一个章节以开始编辑" />
      </div>
    );
  }

  const handleSave = () => {
    onSave(chapter.id, { title, content });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleSave();
    }
  };

  const handleGenerate = (type: "outline" | "text") => {
    const chapterLabel = `第 ${chapter.order + 1} 章${chapter.title ? `《${chapter.title}》` : ""}`;
    const text =
      type === "outline"
        ? `生成${chapterLabel}细纲`
        : `生成${chapterLabel}正文`;
    const context = { entity_type: "chapter", entity_id: chapter.id };
    useAssistantSession.getState().openAssistant();
    useAssistantSession.getState().sendMessage(chapter.project_id, text, context);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center gap-3">
        <Input
          className="flex-1"
          placeholder="章节标题"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <Button variant="primary" onClick={handleSave}>
          保存
        </Button>
        <Button variant="ghost" onClick={() => handleGenerate("outline")}>
          生成细纲
        </Button>
        <Button variant="ghost" onClick={() => handleGenerate("text")}>
          生成正文
        </Button>
        {undoable && onUndo && (
          <Button variant="ghost" onClick={onUndo}>
            撤销生成
          </Button>
        )}
      </div>

      <div className="flex-1">
        <Textarea
          className="h-full min-h-[300px] resize-none"
          placeholder="开始写作…"
          rows={20}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>

      {chapter.detailed_outline && (
        <Card className="mt-3 p-3">
          <div className="mb-1 text-xs font-medium text-muted">细纲</div>
          <div className="max-h-40 overflow-auto whitespace-pre-wrap text-sm text-ink">
            {chapter.detailed_outline}
          </div>
        </Card>
      )}

      <div className="mt-3 flex items-center justify-between text-xs text-muted">
        <span>状态：{chapter.status || "draft"}</span>
        <span>顺序：{chapter.order}</span>
      </div>
    </div>
  );
}
