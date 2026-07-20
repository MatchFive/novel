import { useMemo, useState } from "react";
import { OutlineNode, OutlineType } from "@/types";

interface OutlineTreeProps {
  nodes: OutlineNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAddChild: (parent: OutlineNode) => void;
  search: string;
}

const TYPE_LABEL: Record<OutlineType, string> = { broad: "总纲", period: "时期", volume: "卷" };

function buildVisibleSet(nodes: OutlineNode[], search: string): Set<string> {
  const q = search.trim().toLowerCase();
  const hit = new Set<string>();
  if (!q) return hit;
  const walk = (list: OutlineNode[], ancestors: OutlineNode[]) => {
    for (const n of list) {
      const text = `${n.title} ${n.content}`.toLowerCase();
      if (text.includes(q)) {
        hit.add(n.id);
        ancestors.forEach((a) => hit.add(a.id));
      }
      if (n.children) walk(n.children, [...ancestors, n]);
    }
  };
  walk(nodes, []);
  return hit;
}

export function OutlineTree({ nodes, selectedId, onSelect, onAddChild, search }: OutlineTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const hitSet = useMemo(() => buildVisibleSet(nodes, search), [nodes, search]);

  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id); else next.add(id);
    setExpanded(next);
  };

  const renderNode = (n: OutlineNode, depth: number) => {
    const hasChildren = (n.children?.length || 0) > 0;
    const isExpanded = expanded.has(n.id) || (search.trim() && hitSet.has(n.id));
    const hidden = search.trim() && !hitSet.has(n.id);
    if (hidden) return null;
    return (
      <div key={n.id}>
        <div
          className={`flex cursor-pointer items-center border-b border-line px-2 py-2 hover:bg-surface-2 ${selectedId === n.id ? "bg-accent-soft" : ""}`}
          style={{ paddingLeft: `${12 + depth * 20}px` }}
          onClick={() => onSelect(n.id)}
        >
          {hasChildren && (
            <span className="mr-1 text-xs text-muted" onClick={(e) => { e.stopPropagation(); toggle(n.id); }}>
              {isExpanded ? "▼" : "▶"}
            </span>
          )}
          <span className="mr-2 rounded-sm border border-line px-1 text-[10px] text-muted">{TYPE_LABEL[n.type]}</span>
          <span className="flex-1 truncate text-sm text-ink">{n.title || "（无标题）"}</span>
          {n.type !== "volume" && (
            <button className="ml-2 text-xs text-accent" onClick={(e) => { e.stopPropagation(); onAddChild(n); }}>+</button>
          )}
        </div>
        {isExpanded && n.children?.map((c) => renderNode(c, depth + 1))}
      </div>
    );
  };

  return <div className="border border-line bg-surface">{nodes.map((n) => renderNode(n, 0))}</div>;
}
