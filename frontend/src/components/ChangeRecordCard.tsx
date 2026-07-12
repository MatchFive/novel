import { Card, Tag } from "@/components/ui";
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

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v || "—";
  return JSON.stringify(v);
}

function diffFields(before: any, after: any): { key: string; before: string; after: string }[] {
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  return Array.from(keys)
    .filter((k) => before?.[k] !== after?.[k])
    .map((k) => ({
      key: k,
      before: formatValue(before?.[k]),
      after: formatValue(after?.[k]),
    }));
}

export default function ChangeRecordCard({ record }: { record: ChangeRecord }) {
  const action = ACTION_LABELS[record.action] || record.action;
  const entity = ENTITY_LABELS[record.entity_type] || record.entity_type;
  const diffs = diffFields(record.before, record.after);

  return (
    <Card className="p-3 text-sm">
      <div className="mb-2 flex items-center gap-2">
        <Tag>{action}</Tag>
        <span className="font-medium text-ink">{entity}</span>
        <span className="text-xs text-muted">{record.entity_id || "（新增）"}</span>
      </div>
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
    </Card>
  );
}
