import { useEffect, useState } from "react";
import { Button, Input, Textarea, Card, Empty } from "@/components/ui";
import { useAssistantSession } from "@/stores/useAssistantSession";
import { longApi } from "@/api/long";
import type { Chapter, CharacterMemoryDraft } from "@/types";

interface ChapterEditorProps {
  chapter: Chapter | null;
  onSave: (id: string, data: Partial<Chapter>) => void;
  onUndo?: () => void;
  undoable?: boolean;
}

export function ChapterEditor({ chapter, onSave, onUndo, undoable }: ChapterEditorProps) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [order, setOrder] = useState(1);
  const [showOutline, setShowOutline] = useState(true);
  const [drafts, setDrafts] = useState<CharacterMemoryDraft[]>([]);
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);

  useEffect(() => {
    if (chapter) {
      setTitle(chapter.title || "");
      setContent(chapter.content || "");
      setOrder((chapter.order ?? 0) + 1);
    }
  }, [chapter?.id]);

  if (!chapter) {
    return (
      <div className="flex h-full items-center justify-center">
        <Empty text="在左侧目录选择或新增一个章节开始编辑" />
      </div>
    );
  }

  const handleSave = () => {
    onSave(chapter.id, { title, content, order: order - 1 });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleSave();
    }
  };

  const handleGenerate = (type: "outline" | "text") => {
    const chapterLabel = `第 ${chapter.order + 1} 章${chapter.title ? `《${chapter.title}》` : ""}`;
    const text = type === "outline" ? `生成${chapterLabel}细纲` : `生成${chapterLabel}正文`;
    const context = { entity_type: "chapter", entity_id: chapter.id };
    useAssistantSession.getState().openAssistant();
    useAssistantSession.getState().sendMessage(chapter.project_id, text, context);
  };

  const handleExtractMemory = async () => {
    if (!chapter) return;
    setDraftLoading(true);
    try {
      const res = await longApi.extractMemory(chapter.id);
      if (res.data.skipped) {
        const ok = window.confirm(res.data.message || "本章记忆已是最新，是否重新提取？");
        if (!ok) return;
        return;
      }
      setDrafts(res.data.drafts || []);
      setDraftsOpen(true);
    } finally {
      setDraftLoading(false);
    }
  };

  const handleApplyDrafts = async () => {
    if (!chapter) return;
    await longApi.applyMemoryDrafts(chapter.id);
    setDrafts([]);
    setDraftsOpen(false);
  };

  const handleDiscardDrafts = async () => {
    if (!chapter) return;
    await longApi.discardMemoryDrafts(chapter.id);
    setDrafts([]);
    setDraftsOpen(false);
  };

  return (
    <div className="flex h-full flex-col">
      {/* 顶部工具栏 */}
      <div className="mb-3 flex flex-wrap items-center gap-3 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted">第</span>
          <Input
            type="number"
            min={1}
            value={order}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (!Number.isNaN(v) && v >= 1) setOrder(v);
            }}
            className="!w-9 px-1 text-center"
          />
          <span className="text-sm text-muted">章</span>
        </div>

        <Input
          className="min-w-0 flex-1 font-serif text-base"
          placeholder="章节标题"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />

        <div className="flex shrink-0 items-center gap-1">
          <Button variant="primary" onClick={handleSave}>保存</Button>
          <Button variant="ghost" onClick={() => handleGenerate("outline")}>生成细纲</Button>
          <Button variant="ghost" onClick={() => handleGenerate("text")}>生成正文</Button>
          <Button variant="ghost" onClick={handleExtractMemory} disabled={draftLoading}>
            {draftLoading ? "提取中..." : "更新记忆"}
          </Button>
          {undoable && onUndo && (
            <Button variant="ghost" onClick={onUndo}>撤销</Button>
          )}
        </div>
      </div>

      {/* 正文区 */}
      <div className="min-h-0 flex-1">
        <Textarea
          className="h-full resize-none bg-paper leading-relaxed"
          placeholder="在此书写正文…"
          rows={20}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>

      {/* 细纲折叠面板 */}
      {chapter.detailed_outline && (
        <Card className="mt-3 overflow-hidden">
          <button
            onClick={() => setShowOutline((s) => !s)}
            className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-ink hover:bg-surface-2"
          >
            <span>本章细纲</span>
            <span className="text-xs text-muted">{showOutline ? "收起" : "展开"}</span>
          </button>
          {showOutline && (
            <div className="max-h-48 overflow-auto border-t border-line px-3 py-2">
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink-soft">
                {chapter.detailed_outline}
              </div>
            </div>
          )}
        </Card>
      )}

      <div className="mt-2 flex items-center justify-between text-xs text-muted">
        <span>字数：{content.length}</span>
        <span>状态：{chapter.status === "generated" ? "已生成" : chapter.status === "reviewed" ? "已细纲" : "草稿"}</span>
      </div>

      {draftsOpen && (
        <div className="mt-4 border border-line bg-paper p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">本章记忆候选</span>
            <div className="flex gap-2">
              <Button variant="primary" onClick={handleApplyDrafts}>确认应用</Button>
              <Button variant="ghost" onClick={handleDiscardDrafts}>取消</Button>
            </div>
          </div>
          {drafts.length === 0 ? (
            <div className="text-sm text-muted">没有候选记忆</div>
          ) : (
            <div className="space-y-3">
              {Object.entries(
                drafts.reduce((acc, d) => {
                  (acc[d.character_id] = acc[d.character_id] || []).push(d);
                  return acc;
                }, {} as Record<string, CharacterMemoryDraft[]>)
              ).map(([characterId, items]) => (
                <div key={characterId} className="border-t border-line pt-2">
                  <div className="mb-1 text-sm font-medium">角色 ID: {characterId}</div>
                  <ul className="space-y-1">
                    {items.map((d) => (
                      <li key={d.id} className="text-sm text-ink-soft">
                        <span className="font-medium">[{d.action}]</span> {d.content}
                        <span className="ml-2 text-xs text-muted">({d.importance}, {d.ttl})</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
