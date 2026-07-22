import { useEffect, useMemo, useState } from "react";
import { useConfirm } from "@/hooks/useConfirm";
import { useAssistantSession } from "@/stores/useAssistantSession";
import { CharacterMemoryPanel } from "@/components/character/CharacterMemoryPanel";
import { Button, Input, Textarea, SectionTitle, Empty } from "@/components/ui";

export interface FieldDef {
  key: string;
  label: string;
  multiline?: boolean;
  options?: string[];
  /** 多行字段的固定行数（默认 4） */
  rows?: number;
  /** 多行字段撑满编辑器剩余高度（适用于大纲/世界观这类长文本主字段） */
  fill?: boolean;
}

export interface EntityWorkbenchConfig {
  kind: string;
  label: string;
  fields: FieldDef[];
  titleOf: (item: any) => string;
  subtitleOf?: (item: any) => string;
  groupBy?: (item: any) => string;
  groupOrder?: string[];
  searchKeys: string[];
  sortBy?: (a: any, b: any) => number;
}

interface EntityApi {
  list: (pid: string) => Promise<{ data: any[] }>;
  add: (data: any) => Promise<any>;
  upd: (id: string, data: any) => Promise<any>;
  del: (id: string) => Promise<any>;
}

interface EntityWorkbenchProps {
  pid: string;
  config: EntityWorkbenchConfig;
  api: EntityApi;
  /** 编辑器头部的额外操作（如大纲的"复制为新版"），仅在选中已有条目时渲染 */
  editorActions?: (item: any, reload: () => void) => React.ReactNode;
}

const selectClass =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30";

export function EntityWorkbench({ pid, config, api, editorActions }: EntityWorkbenchProps) {
  const [items, setItems] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"edit" | "memory">("edit");
  const { confirm, dialog } = useConfirm();
  const entitiesVersion = useAssistantSession((s) => s.entitiesVersion);

  const load = async () => {
    try {
      const { data } = await api.list(pid);
      setItems(data || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  };

  useEffect(() => {
    setSelectedId(null);
    setCreating(false);
    setForm({});
    setSearch("");
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, config.kind, api]);

  useEffect(() => {
    if (entitiesVersion > 0) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entitiesVersion]);

  const selected = useMemo(
    () => items.find((it) => it.id === selectedId) || null,
    [items, selectedId]
  );

  // 选中条目变化时载入表单（creating 时清空）
  useEffect(() => {
    if (creating) {
      setForm({});
      return;
    }
    if (selected) {
      const next: Record<string, string> = {};
      config.fields.forEach((f) => (next[f.key] = selected[f.key] ?? ""));
      setForm(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, creating, selected]);

  // 切换选中或新建时重置到编辑标签
  useEffect(() => {
    setActiveTab("edit");
  }, [selectedId, creating]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = items;
    if (q) {
      list = list.filter((it) =>
        config.searchKeys.some((k) => String(it[k] || "").toLowerCase().includes(q))
      );
    }
    if (config.sortBy) list = [...list].sort(config.sortBy);
    return list;
  }, [items, search, config]);

  const groups = useMemo(() => {
    if (!config.groupBy) return [{ name: "", items: visible }];
    const map = new Map<string, any[]>();
    visible.forEach((it) => {
      const g = config.groupBy!(it) || "未分组";
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(it);
    });
    const names = Array.from(map.keys());
    if (config.groupOrder) {
      const order = config.groupOrder;
      names.sort((a, b) => {
        const ia = order.indexOf(a);
        const ib = order.indexOf(b);
        return (ia === -1 ? order.length : ia) - (ib === -1 ? order.length : ib);
      });
    } else {
      names.sort();
    }
    return names.map((name) => ({ name, items: map.get(name)! }));
  }, [visible, config]);

  const handleSelect = (id: string) => {
    setCreating(false);
    setSelectedId(id);
    setError(null);
  };

  const handleNew = () => {
    setSelectedId(null);
    setCreating(true);
    setForm({});
    setError(null);
  };

  const handleSave = async () => {
    setError(null);
    const payload: any = {};
    config.fields.forEach((f) => (payload[f.key] = form[f.key] ?? ""));
    try {
      if (creating) {
        const res = await api.add({ ...payload, project_id: pid });
        const created = res?.data ?? res;
        await load();
        setCreating(false);
        if (created?.id) setSelectedId(created.id);
      } else if (selectedId) {
        await api.upd(selectedId, payload);
        await load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  };

  const handleDelete = async () => {
    if (!selectedId) return;
    if (!(await confirm(`删除${config.label}`, `确定删除该${config.label}吗？`))) return;
    try {
      await api.del(selectedId);
      setSelectedId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const editing = creating || selected !== null;

  return (
    <div className="flex h-full flex-col">
      {dialog}
      <SectionTitle>{config.label}</SectionTitle>
      <div className="mt-4 flex h-0 flex-1 gap-4">
        {/* 中栏：搜索 + 分组列表 */}
        <div className="flex w-72 shrink-0 flex-col border border-line bg-surface p-3">
          <div className="mb-3 flex gap-2">
            <Input
              placeholder={`搜索${config.label}…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Button variant="primary" className="shrink-0" onClick={handleNew}>
              新增
            </Button>
          </div>
          <div className="flex-1 overflow-auto">
            {visible.length === 0 && <Empty text={search ? "无匹配结果" : `暂无${config.label}，点击「新增」创建`} />}
            {groups.map((g) => (
              <div key={g.name || "_all"} className="mb-3">
                {g.name && (
                  <div className="mb-1 px-1 text-xs font-medium text-muted">
                    {g.name}（{g.items.length}）
                  </div>
                )}
                {g.items.map((it) => (
                  <div
                    key={it.id}
                    onClick={() => handleSelect(it.id)}
                    className={
                      "mb-1 cursor-pointer border border-line px-3 py-2 transition-colors " +
                      (selectedId === it.id
                        ? "border-accent bg-accent-soft"
                        : "bg-surface hover:bg-surface-2")
                    }
                  >
                    <div className="truncate text-sm font-medium text-ink">{config.titleOf(it)}</div>
                    {config.subtitleOf && (
                      <div className="truncate text-xs text-muted">{config.subtitleOf(it)}</div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* 右栏：编辑器 */}
        <div className="flex flex-1 flex-col overflow-auto border border-line bg-surface p-4">
          {error && (
            <div className="mb-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
          {!editing ? (
            <div className="flex h-full items-center justify-center">
              <Empty text="从左侧选择条目进行编辑，或点击「新增」创建" />
            </div>
          ) : (
            <>
              <div className="mb-3 flex items-center justify-between">
                {config.kind === "character" && selected && !creating ? (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setActiveTab("edit")}
                      className={`border px-2 py-1 text-xs ${
                        activeTab === "edit"
                          ? "border-accent bg-accent-soft text-accent-strong"
                          : "border-line text-muted"
                      }`}
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => setActiveTab("memory")}
                      className={`border px-2 py-1 text-xs ${
                        activeTab === "memory"
                          ? "border-accent bg-accent-soft text-accent-strong"
                          : "border-line text-muted"
                      }`}
                    >
                      记忆
                    </button>
                  </div>
                ) : (
                  <span className="text-sm font-medium text-ink">
                    {creating ? `新增${config.label}` : `编辑${config.label}`}
                  </span>
                )}
                {activeTab === "edit" && (
                  <div className="flex items-center gap-2">
                    {!creating && selected && editorActions?.(selected, load)}
                    <Button variant="primary" onClick={handleSave}>
                      保存
                    </Button>
                    {!creating && (
                      <Button variant="ghost" onClick={handleDelete}>
                        删除
                      </Button>
                    )}
                  </div>
                )}
              </div>
              {activeTab === "edit" && (
                <div className="flex min-h-0 flex-1 flex-col gap-3">
                  {config.fields.map((f) => (
                    <div key={f.key} className={f.fill ? "flex min-h-0 flex-1 flex-col" : undefined}>
                      <label className="mb-1 block text-xs text-muted">{f.label}</label>
                      {f.options ? (
                        <select
                          className={selectClass}
                          value={form[f.key] || ""}
                          onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                        >
                          <option value="">（未设置）</option>
                          {f.options.map((o) => (
                            <option key={o} value={o}>{o}</option>
                          ))}
                        </select>
                      ) : f.multiline ? (
                        <Textarea
                          rows={f.fill ? undefined : (f.rows ?? 4)}
                          className={f.fill ? "min-h-[12rem] flex-1 resize-none leading-relaxed" : "leading-relaxed"}
                          value={form[f.key] || ""}
                          onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                        />
                      ) : (
                        <Input
                          value={form[f.key] || ""}
                          onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}
              {activeTab === "memory" && selected && (
                <div className="flex-1 overflow-auto">
                  <CharacterMemoryPanel characterId={selected.id} />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
