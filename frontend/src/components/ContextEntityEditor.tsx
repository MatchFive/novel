import { useState } from "react";
import { Button, Input, Textarea, Card } from "@/components/ui";
import type { ChangeRecord } from "@/types";

interface ContextEntityEditorProps {
  kind: string;
  item: any;
  onSave: (record: ChangeRecord) => void;
  onCancel: () => void;
}

const FIELD_CONFIG: Record<string, { key: string; label: string; multiline?: boolean }[]> = {
  character: [
    { key: "name", label: "名称" },
    { key: "traits", label: "性格", multiline: true },
    { key: "ability", label: "能力", multiline: true },
    { key: "status", label: "状态" },
  ],
  foreshadow: [
    { key: "title", label: "标题" },
    { key: "content", label: "内容", multiline: true },
    { key: "state", label: "状态" },
  ],
  world: [
    { key: "category", label: "分类" },
    { key: "content", label: "内容", multiline: true },
  ],
  plot: [
    { key: "title", label: "标题" },
    { key: "summary", label: "概要", multiline: true },
    { key: "timeline_pos", label: "时间位置" },
  ],
  outline: [
    { key: "title", label: "标题" },
    { key: "content", label: "内容", multiline: true },
  ],
  chapter: [
    { key: "title", label: "标题" },
    { key: "content", label: "内容", multiline: true },
  ],
};

const ENTITY_LABELS: Record<string, string> = {
  character: "角色",
  outline: "大纲",
  foreshadow: "伏笔",
  world: "世界观",
  plot: "剧情节点",
  chapter: "章节",
};

export default function ContextEntityEditor({ kind, item, onSave, onCancel }: ContextEntityEditorProps) {
  const fields = FIELD_CONFIG[kind] || [];
  const [after, setAfter] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    fields.forEach((f) => (initial[f.key] = item[f.key] || ""));
    return initial;
  });

  const handleSave = () => {
    const before: Record<string, any> = {};
    const afterClean: Record<string, any> = {};
    fields.forEach((f) => {
      before[f.key] = item[f.key] || "";
      afterClean[f.key] = after[f.key];
    });
    const record: ChangeRecord = {
      id: `manual-${Date.now()}`,
      project_id: item.project_id,
      action: "update",
      entity_type: kind,
      entity_id: item.id,
      before,
      after: afterClean,
      requires_confirmation: true,
    };
    onSave(record);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 p-4">
      <Card className="w-full max-w-lg p-4">
        <div className="mb-3 text-sm font-medium text-ink">编辑{ENTITY_LABELS[kind] || kind}</div>
        <div className="space-y-3">
          {fields.map((f) => (
            <div key={f.key}>
              <label className="mb-1 block text-xs text-muted">{f.label}</label>
              {f.multiline ? (
                <Textarea
                  value={after[f.key] || ""}
                  onChange={(e) => setAfter({ ...after, [f.key]: e.target.value })}
                  rows={4}
                />
              ) : (
                <Input
                  value={after[f.key] || ""}
                  onChange={(e) => setAfter({ ...after, [f.key]: e.target.value })}
                />
              )}
            </div>
          ))}
        </div>
        <div className="mt-4 flex gap-2">
          <Button variant="primary" onClick={handleSave}>
            保存为变更建议
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            取消
          </Button>
        </div>
      </Card>
    </div>
  );
}
