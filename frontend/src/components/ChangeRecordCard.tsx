import { useState } from "react";
import { Card, Tag, Button, Textarea } from "@/components/ui";
import type { ChangeRecord } from "@/types";

const ACTION_LABELS: Record<string, string> = {
  add: "新增",
  update: "修改",
  delete: "删除",
};

const ENTITY_LABELS: Record<string, string> = {
  character: "角色",
  outline: "大纲",
  foreshadow: "伏笔",
  world: "世界观",
  plot: "剧情节点",
  chapter: "章节",
};

const STAGE_LABELS: Record<string, string> = {
  broad_outline: "总纲",
  plot_nodes: "剧情节点",
  assignment: "章节分配",
  chapter_outline: "章节细纲",
  chapter_text: "章节正文",
};

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v || "—";
  return JSON.stringify(v);
}

function diffFields(before: any, after: any): { key: string; before: string; after: string }[] {
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  return Array.from(keys)
    .filter((k) => JSON.stringify(before?.[k]) !== JSON.stringify(after?.[k]))
    .map((k) => ({
      key: k,
      before: formatValue(before?.[k]),
      after: formatValue(after?.[k]),
    }));
}

interface ChangeRecordCardProps {
  record: ChangeRecord;
  selected?: boolean;
  onToggle?: () => void;
  onConfirm?: () => void;
  onReject?: () => void;
  onEdit?: (recordId: string, updatedAfter: any) => void;
  onRegenerate?: (stage: string) => void;
}

export default function ChangeRecordCard({
  record,
  selected,
  onToggle,
  onConfirm,
  onReject,
  onEdit,
  onRegenerate,
}: ChangeRecordCardProps) {
  const action = ACTION_LABELS[record.action] || record.action;
  const entity = ENTITY_LABELS[record.entity_type] || record.entity_type;
  const stageLabel = record.stage ? STAGE_LABELS[record.stage] || record.stage : null;
  const diffs = diffFields(record.before, record.after);

  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState("");

  const editableStage = record.stage === "broad_outline" || record.stage === "chapter_outline";
  const editField = record.stage === "broad_outline" ? "content" : "detailed_outline";

  const startEdit = () => {
    const initial = (record.after?.[editField] as string) || "";
    setEditValue(initial);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditValue("");
  };

  const saveEdit = () => {
    if (onEdit) {
      onEdit(record.id, { ...record.after, [editField]: editValue });
    }
    setEditing(false);
    setEditValue("");
  };

  return (
    <Card className="p-3 text-sm">
      <div className="mb-2 flex items-center gap-2">
        {onToggle && (
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            className="h-4 w-4 accent-accent"
          />
        )}
        <Tag>{action}</Tag>
        <span className="font-medium text-ink">{entity}</span>
        {stageLabel && <Tag>{stageLabel}</Tag>}
        <span className="text-xs text-muted">{record.entity_id || "（新增）"}</span>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          {editableStage && onEdit && (
            <Button variant="subtle" className="px-2 py-0.5 text-xs" onClick={startEdit}>
              编辑
            </Button>
          )}
          {record.stage && onRegenerate && (
            <Button variant="subtle" className="px-2 py-0.5 text-xs" onClick={() => onRegenerate(record.stage!)}>
              重新生成
            </Button>
          )}
          {onConfirm && (
            <Button variant="subtle" className="px-2 py-0.5 text-xs" onClick={onConfirm}>接受</Button>
          )}
          {onReject && (
            <Button variant="subtle" className="px-2 py-0.5 text-xs text-red-700 hover:text-red-800" onClick={onReject}>拒绝</Button>
          )}
        </div>
      </div>

      {editing ? (
        <div className="space-y-2">
          <Textarea
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            rows={5}
            className="resize-none"
          />
          <div className="flex gap-2">
            <Button variant="primary" className="px-2 py-0.5 text-xs" onClick={saveEdit}>
              保存
            </Button>
            <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={cancelEdit}>
              取消
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-1">
          {diffs.length === 0 && (
            <div className="text-xs text-muted">无字段变化</div>
          )}
          {diffs.map((d) => (
            <div key={d.key} className="grid grid-cols-[80px_1fr] gap-2 text-xs">
              <span className="text-muted">{d.key}</span>
              <div className="space-y-0.5">
                {record.action !== "add" && (
                  <div className="line-through text-muted">{d.before}</div>
                )}
                {record.action !== "delete" && (
                  <div className="text-ink">{d.after}</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
