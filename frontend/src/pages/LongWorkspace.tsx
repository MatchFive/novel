import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { longApi } from "@/api/long";
import { graphApi } from "@/api/graph";
import { ChapterList } from "@/components/chapter/ChapterList";
import { ChapterEditor } from "@/components/chapter/ChapterEditor";
import { EntityWorkbench, type EntityWorkbenchConfig } from "@/components/EntityWorkbench";
import { Button, Card, SectionTitle } from "@/components/ui";
import type { Chapter } from "@/types";

export default function LongWorkspace() {
  const { id } = useParams();
  const nav = useNavigate();
  const [tab, setTab] = useState("outline");

  return (
    <div className="flex h-full">
      <aside className="w-52 shrink-0 border-r border-line bg-surface p-4">
        <Button variant="subtle" className="mb-4 w-full justify-start" onClick={() => nav("/")}>← 返回</Button>
        {[
          ["outline", "大纲树"], ["character", "角色"], ["foreshadow", "伏笔"],
          ["world", "世界观"], ["plot", "剧情节点"], ["chapter", "章节"], ["graph", "图谱"],
        ].map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={
              "mb-1 block w-full rounded-lg border-l-2 px-3 py-2 text-left text-sm transition-colors " +
              (tab === k ? "border-accent bg-accent-soft text-ink" : "border-transparent text-muted hover:bg-surface-2 hover:text-ink")
            }
          >
            {label}
          </button>
        ))}
      </aside>
      <div className="flex-1 overflow-auto p-8">
        {(tab === "outline" || tab === "character" || tab === "foreshadow" || tab === "world" || tab === "plot") && (
          <EntityWorkbench
            pid={id!}
            config={WORKBENCH_CONFIGS[tab]}
            api={KIND_API[tab]}
            editorActions={tab === "outline" ? makeOutlineActions(id!) : undefined}
          />
        )}
        {tab === "chapter" && <ChapterPanel pid={id!} />}
        {tab === "graph" && <GraphPanel pid={id!} />}
      </div>
    </div>
  );
}

const KIND_API: Record<string, any> = {
  outline: { list: longApi.outlines, add: longApi.addOutline, upd: longApi.updateOutline, del: longApi.deleteOutline },
  character: { list: longApi.characters, add: longApi.addCharacter, upd: longApi.updateCharacter, del: longApi.deleteCharacter },
  foreshadow: { list: longApi.foreshadows, add: longApi.addForeshadow, upd: longApi.updateForeshadow, del: longApi.deleteForeshadow },
  world: { list: longApi.world, add: longApi.addWorld, upd: longApi.updateWorld, del: longApi.deleteWorld },
  plot: { list: longApi.plot, add: longApi.addPlot, upd: longApi.updatePlot, del: longApi.deletePlot },
};

const WORKBENCH_CONFIGS: Record<string, EntityWorkbenchConfig> = {
  outline: {
    kind: "outline",
    label: "大纲",
    fields: [
      { key: "title", label: "标题" },
      { key: "content", label: "内容", multiline: true },
    ],
    titleOf: (it) => it.title || "（无标题）",
    subtitleOf: (it) => (it.content || "").slice(0, 40),
    searchKeys: ["title", "content"],
    sortBy: (a, b) => (a.order || 0) - (b.order || 0),
  },
  character: {
    kind: "character",
    label: "角色",
    fields: [
      { key: "name", label: "名称" },
      { key: "traits", label: "性格", multiline: true },
      { key: "ability", label: "能力", multiline: true },
      { key: "status", label: "状态" },
    ],
    titleOf: (it) => it.name || "（未命名）",
    subtitleOf: (it) => (it.traits || "").slice(0, 30),
    groupBy: (it) => it.status || "未知",
    searchKeys: ["name", "traits"],
  },
  foreshadow: {
    kind: "foreshadow",
    label: "伏笔",
    fields: [
      { key: "title", label: "标题" },
      { key: "content", label: "内容", multiline: true },
      { key: "state", label: "状态", options: ["pending", "revealed", "abandoned"] },
    ],
    titleOf: (it) => it.title || "（无标题）",
    subtitleOf: (it) => (it.content || "").slice(0, 30),
    groupBy: (it) => it.state || "pending",
    groupOrder: ["pending", "revealed", "abandoned"],
    searchKeys: ["title", "content"],
  },
  world: {
    kind: "world",
    label: "世界观",
    fields: [
      { key: "category", label: "分类" },
      { key: "content", label: "内容", multiline: true },
    ],
    titleOf: (it) => it.category || "未分类",
    subtitleOf: (it) => (it.content || "").slice(0, 40),
    groupBy: (it) => it.category || "未分类",
    searchKeys: ["category", "content"],
  },
  plot: {
    kind: "plot",
    label: "剧情节点",
    fields: [
      { key: "title", label: "标题" },
      { key: "summary", label: "概要", multiline: true },
      { key: "timeline_pos", label: "时间位置" },
    ],
    titleOf: (it) => it.title || "（无标题）",
    subtitleOf: (it) => (it.summary || "").slice(0, 30),
    searchKeys: ["title", "summary"],
    sortBy: (a, b) =>
      String(a.timeline_pos || "").localeCompare(String(b.timeline_pos || ""), "zh") ||
      (a.order || 0) - (b.order || 0),
  },
};

function makeOutlineActions(pid: string) {
  return (item: any, reload: () => void) => (
    <Button
      variant="ghost"
      onClick={async () => {
        await longApi.addOutline({
          project_id: pid,
          title: item.title,
          content: item.content,
          version_chain: item.id,
        });
        reload();
      }}
    >
      复制为新版
    </Button>
  );
}

function ChapterPanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<Chapter[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedChapter, setSelectedChapter] = useState<Chapter | null>(null);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detailReqIdRef = useRef<number>(0);

  const showError = (e: unknown) => {
    const msg = e instanceof Error ? e.message : "操作失败";
    console.error(e);
    setError(msg);
  };

  const clearError = () => setError(null);

  const loadItems = async () => {
    setItemsLoading(true);
    clearError();
    try {
      const { data } = await longApi.chapters(pid);
      setItems(data || []);
    } catch (e) {
      showError(e);
    } finally {
      setItemsLoading(false);
    }
  };

  const loadDetail = async (id: string) => {
    const reqId = ++detailReqIdRef.current;
    setDetailLoading(true);
    clearError();
    try {
      const { data } = await longApi.getChapter(id);
      if (reqId === detailReqIdRef.current) {
        setSelectedChapter(data);
      }
    } catch (e) {
      if (reqId === detailReqIdRef.current) {
        showError(e);
      }
    } finally {
      if (reqId === detailReqIdRef.current) {
        setDetailLoading(false);
      }
    }
  };

  useEffect(() => {
    loadItems();
  }, [pid]);

  useEffect(() => {
    setSelectedId(null);
    setSelectedChapter(null);
    setError(null);
  }, [pid]);

  useEffect(() => {
    if (selectedId) {
      loadDetail(selectedId);
    } else {
      setSelectedChapter(null);
    }
  }, [selectedId]);

  const handleSelect = (id: string) => {
    clearError();
    setSelectedId(id);
  };

  const handleAdd = async (title: string) => {
    clearError();
    try {
      const { data } = await longApi.addChapter({
        project_id: pid,
        title,
        content: "",
        order: items.length,
      });
      await loadItems();
      setSelectedId(data.id);
    } catch (e) {
      showError(e);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm("确定删除该章节吗？")) return;
    clearError();
    try {
      await longApi.deleteChapter(id);
      if (selectedId === id) setSelectedId(null);
      await loadItems();
    } catch (e) {
      showError(e);
    }
  };

  const handleMove = async (id: string, direction: -1 | 1) => {
    clearError();
    const sortedItems = [...items].sort((a, b) => a.order - b.order);
    const index = sortedItems.findIndex((c) => c.id === id);
    if (index === -1) return;
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= sortedItems.length) return;
    const [moved] = sortedItems.splice(index, 1);
    sortedItems.splice(newIndex, 0, moved);
    const newIds = sortedItems.map((c) => c.id);
    try {
      await longApi.reorderChapters(pid, newIds);
      await loadItems();
    } catch (e) {
      showError(e);
    }
  };

  const handleSave = async (id: string, data: Partial<Chapter>) => {
    clearError();
    try {
      await longApi.updateChapter(id, data);
      await loadItems();
      if (selectedId === id) {
        await loadDetail(id);
      }
    } catch (e) {
      showError(e);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <SectionTitle>章节</SectionTitle>
      <div className="mt-4 flex h-0 flex-1 gap-4">
        <div className="flex w-72 flex-col border border-line bg-surface p-3">
          {itemsLoading ? (
            <div className="text-sm text-muted">加载中…</div>
          ) : (
            <ChapterList
              items={items}
              selectedId={selectedId}
              onSelect={handleSelect}
              onAdd={handleAdd}
              onDelete={handleDelete}
              onMove={handleMove}
            />
          )}
        </div>
        <div className="relative flex flex-1 flex-col border border-line bg-surface p-3">
          {error && (
            <div className="mb-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
          {detailLoading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-surface/80">
              <span className="text-sm text-muted">加载中…</span>
            </div>
          )}
          <ChapterEditor chapter={selectedChapter} onSave={handleSave} />
        </div>
      </div>
    </div>
  );
}

function GraphPanel({ pid }: { pid: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<any>(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await graphApi.view(pid);
      setData(data);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, [pid]);

  if (loading) return <div className="text-sm text-muted">加载中…</div>;
  if (!data) return <div className="text-sm text-muted">无数据</div>;

  const colorByType: Record<string, string> = {
    character: "#3e2723",
    outline: "#1b5e20",
    foreshadow: "#e65100",
    world: "#0d47a1",
    plot: "#4a148c",
    chapter: "#b71c1c",
  };

  const nodeMap = Object.fromEntries(data.nodes.map((n: any) => [n.id, n]));

  return (
    <div>
      <div className="flex items-center justify-between">
        <SectionTitle>知识图谱</SectionTitle>
        <span className="text-xs text-muted">数据源：{data.source}</span>
      </div>
      <div className="mt-2 text-xs text-muted">
        节点 {data.nodes.length} · 关系 {data.edges.length}
      </div>

      <div className="mt-4 overflow-hidden rounded border border-line bg-surface">
        <svg
          viewBox={`0 0 ${data.width} ${data.height}`}
          preserveAspectRatio="xMidYMid meet"
          className="h-auto w-full"
        >
          {data.edges.map((e: any, i: number) => {
            const from = nodeMap[e.from];
            const to = nodeMap[e.to];
            if (!from || !to) return null;
            return (
              <line
                key={`edge-${i}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke="var(--line)"
                strokeWidth="1.5"
              />
            );
          })}
          {data.nodes.map((n: any) => (
            <g
              key={n.id}
              transform={`translate(${n.x}, ${n.y})`}
              className="cursor-pointer"
              onClick={() => setSelected(n)}
            >
              <circle
                r="7"
                fill={colorByType[n.type] || "#9a8c7b"}
                stroke="var(--surface)"
                strokeWidth="2"
              />
              <text
                y="19"
                textAnchor="middle"
                style={{
                  fill: "var(--ink)",
                  fontSize: "11px",
                  fontWeight: 500,
                  textShadow: "0 1px 2px var(--surface)",
                }}
              >
                {n.label}
              </text>
            </g>
          ))}
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap gap-3 text-xs">
        {Object.entries({
          角色: colorByType.character,
          大纲: colorByType.outline,
          伏笔: colorByType.foreshadow,
          世界观: colorByType.world,
          剧情节点: colorByType.plot,
          章节: colorByType.chapter,
        }).map(([label, color]) => (
          <div key={label} className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
            <span className="text-muted">{label}</span>
          </div>
        ))}
      </div>

      {selected && (
        <Card className="mt-4 p-3 text-sm">
          <div className="font-medium text-ink">{selected.label}</div>
          <div className="text-xs text-muted">{selected.type_label || selected.type}</div>
          <div className="mt-2 text-xs text-muted">
            关联：
            {data.edges
              .filter((e: any) => e.from === selected.id || e.to === selected.id)
              .map((e: any, i: number) => {
                const other = nodeMap[e.from === selected.id ? e.to : e.from];
                return (
                  <span key={i} className="mr-2 inline-block">
                    {e.label} → {other?.label || "?"}
                  </span>
                );
              })}
          </div>
        </Card>
      )}
    </div>
  );
}
