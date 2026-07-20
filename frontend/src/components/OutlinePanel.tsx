import { useEffect, useMemo, useState } from "react";
import { Button, Input, Textarea, SectionTitle, Empty } from "@/components/ui";
import { OutlineTree } from "./OutlineTree";
import { longApi } from "@/api/long";
import { OutlineNode, OutlineType } from "@/types";

function buildTree(outlines: OutlineNode[]): OutlineNode[] {
  const byId = new Map(outlines.map((o) => [o.id, { ...o, children: [] as OutlineNode[] }]));
  const roots: OutlineNode[] = [];
  for (const o of byId.values()) {
    const parent = o.parent_id ? byId.get(o.parent_id) : null;
    if (parent) parent.children!.push(o);
    else roots.push(o);
  }
  return roots.sort((a, b) => a.order - b.order);
}

const TYPE_LABEL: Record<OutlineType, string> = { broad: "总纲", period: "时期", volume: "卷" };

export function OutlinePanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<OutlineNode[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<Partial<OutlineNode>>({});
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const { data } = await longApi.outlines(pid);
    setItems(data || []);
  };

  useEffect(() => { load(); }, [pid]);

  const tree = useMemo(() => buildTree(items), [items]);
  const selected = useMemo(() => items.find((i) => i.id === selectedId), [items, selectedId]);

  useEffect(() => {
    if (selected) setForm({ ...selected });
    else setForm({});
  }, [selected]);

  const handleSave = async () => {
    setError(null);
    if (!selectedId) return;
    try {
      await longApi.updateOutline(selectedId, {
        title: form.title || "",
        content: form.content || "",
        chapter_start: form.type === "volume" ? form.chapter_start ?? null : null,
        chapter_end: form.type === "volume" ? form.chapter_end ?? null : null,
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  };

  const handleDelete = async () => {
    if (!selectedId || !window.confirm("确定删除吗？")) return;
    try {
      await longApi.deleteOutline(selectedId);
      setSelectedId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleAddRoot = async () => {
    setError(null);
    try {
      const res = await longApi.addOutline({ project_id: pid, title: "新总纲", type: "broad", order: 0 });
      await load();
      setSelectedId(res?.data?.id || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失败");
    }
  };

  const handleAddChild = async (parent: OutlineNode) => {
    setError(null);
    const childType: OutlineType = parent.type === "broad" ? "period" : "volume";
    try {
      const res = await longApi.addOutline({
        project_id: pid,
        title: childType === "period" ? "新时期" : "新卷",
        type: childType,
        parent_id: parent.id,
        order: (parent.children?.length || 0),
      });
      await load();
      setSelectedId(res?.data?.id || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失败");
    }
  };

  const handleSplit = async () => {
    if (!selectedId) return;
    const msg = window.prompt("请输入拆分要求", `将《${selected?.title}》拆分为多卷`);
    if (!msg) return;
    try {
      await longApi.splitOutline(pid, selectedId, msg);
      // AI 拆分进 staged_changes，提示用户去 assistant 面板确认
      alert("拆分建议已生成，请打开右下角 AI 助手面板确认");
    } catch (e) {
      setError(e instanceof Error ? e.message : "拆分失败");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <SectionTitle>大纲树</SectionTitle>
      <div className="mt-4 flex h-0 flex-1 gap-4">
        <div className="flex w-80 shrink-0 flex-col border border-line bg-surface p-3">
          <div className="mb-3 flex gap-2">
            <Input placeholder="搜索大纲…" value={search} onChange={(e) => setSearch(e.target.value)} />
            <Button variant="primary" className="shrink-0" onClick={handleAddRoot}>新增</Button>
          </div>
          <div className="flex-1 overflow-auto">
            {tree.length === 0 ? <Empty text="暂无大纲，点击新增创建总纲" /> : (
              <OutlineTree nodes={tree} selectedId={selectedId} onSelect={setSelectedId} onAddChild={handleAddChild} search={search} />
            )}
          </div>
        </div>
        <div className="flex flex-1 flex-col overflow-auto border border-line bg-surface p-4">
          {error && <div className="mb-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          {!selected ? (
            <div className="flex h-full items-center justify-center"><Empty text="从左侧选择节点编辑，或点击新增" /></div>
          ) : (
            <>
              <div className="mb-3 flex items-center justify-between">
                <span className="text-sm font-medium text-ink">编辑{TYPE_LABEL[form.type || "broad"]}</span>
                <div className="flex items-center gap-2">
                  <Button variant="ghost" onClick={handleSplit} disabled={form.type === "volume"}>AI 拆分</Button>
                  <Button variant="primary" onClick={handleSave}>保存</Button>
                  <Button variant="ghost" onClick={handleDelete}>删除</Button>
                </div>
              </div>
              <div className="flex min-h-0 flex-1 flex-col gap-3">
                <div>
                  <label className="mb-1 block text-xs text-muted">标题</label>
                  <Input value={form.title || ""} onChange={(e) => setForm({ ...form, title: e.target.value })} />
                </div>
                <div className="flex min-h-0 flex-1 flex-col">
                  <label className="mb-1 block text-xs text-muted">内容</label>
                  <Textarea className="min-h-[12rem] flex-1 resize-none leading-relaxed" value={form.content || ""} onChange={(e) => setForm({ ...form, content: e.target.value })} />
                </div>
                {form.type === "volume" && (
                  <div className="flex gap-4">
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-muted">起始章</label>
                      <Input type="number" value={form.chapter_start ?? ""} onChange={(e) => setForm({ ...form, chapter_start: e.target.value ? Number(e.target.value) : null })} />
                    </div>
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-muted">结束章</label>
                      <Input type="number" value={form.chapter_end ?? ""} onChange={(e) => setForm({ ...form, chapter_end: e.target.value ? Number(e.target.value) : null })} />
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
