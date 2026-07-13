import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { projectsApi } from "@/api/projects";
import type { Project } from "@/types";
import { Button, Input, Textarea, Card } from "@/components/ui";

type Tab = "long" | "short";

export default function HomePage() {
  const [tab, setTab] = useState<Tab>("long");
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const nav = useNavigate();

  const load = async () => {
    const { data } = await projectsApi.list(tab);
    setProjects(data);
  };

  useEffect(() => { load(); }, [tab]);

  const create = async () => {
    if (!title.trim()) return;
    const { data } = await projectsApi.create(tab, title.trim(), description.trim());
    setCreating(false);
    setTitle("");
    setDescription("");
    nav(`/project/${tab}/${data.id}`);
  };

  const startEdit = (p: Project, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(p.id);
    setEditTitle(p.title);
    setEditDescription(p.description || "");
  };

  const saveEdit = async (id: string) => {
    if (!editTitle.trim()) return;
    await projectsApi.update(id, { title: editTitle.trim(), description: editDescription.trim() });
    setEditingId(null);
    load();
  };

  const remove = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm("确定删除该项目吗？此操作不可恢复。")) return;
    await projectsApi.remove(id);
    load();
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-serif text-3xl font-semibold tracking-wide text-ink">项目</h1>
          <p className="mt-2 text-sm text-muted">短篇 / 长篇小说项目管理</p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)} disabled={creating}>新建项目</Button>
      </div>

      <div className="mt-8 inline-flex rounded-full border border-line bg-surface p-1">
        {(["long", "short"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={
              "rounded-full px-5 py-1.5 text-sm font-medium transition-colors " +
              (tab === t ? "bg-accent-soft text-accent-strong" : "text-muted hover:text-ink")
            }
          >
            {t === "long" ? "长篇" : "短篇"}
          </button>
        ))}
      </div>

      {creating && (
        <Card className="mt-5 p-4">
          <div className="space-y-3">
            <Input
              placeholder={tab === "long" ? "长篇标题" : "短篇标题"}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && create()}
              autoFocus
            />
            <Textarea
              placeholder="简介（可选）"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
            <div className="flex gap-2">
              <Button variant="primary" onClick={create}>创建</Button>
              <Button variant="ghost" onClick={() => setCreating(false)}>取消</Button>
            </div>
          </div>
        </Card>
      )}

      <div className="mt-6 grid grid-cols-2 gap-4">
        {projects.length === 0 && (
          <div className="col-span-2 rounded-lg border border-dashed border-line bg-surface p-10 text-center text-sm text-muted">暂无项目</div>
        )}
        {projects.map((p) => (
          <Card
            key={p.id}
            className={"relative p-6 text-left " + (editingId !== p.id ? "cursor-pointer " : "")}
            onClick={() => {
              if (editingId !== p.id) nav(`/project/${p.type}/${p.id}`);
            }}
          >
            {editingId === p.id ? (
              <div className="space-y-3" onClick={(e) => e.stopPropagation()}>
                <Input
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  autoFocus
                />
                <Textarea
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                  rows={3}
                />
                <div className="flex gap-2">
                  <Button type="button" variant="primary" onClick={(e) => { e.stopPropagation(); saveEdit(p.id); }}>保存</Button>
                  <Button type="button" variant="ghost" onClick={(e) => { e.stopPropagation(); setEditingId(null); }}>取消</Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div className="font-serif text-lg font-medium text-ink">
                    {p.title}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button type="button" variant="subtle" className="px-2 py-1 text-xs" onClick={(e) => startEdit(p, e)}>
                      编辑
                    </Button>
                    <Button type="button" variant="subtle" className="px-2 py-1 text-xs text-red-700 hover:text-red-800" onClick={(e) => remove(p.id, e)}>
                      删除
                    </Button>
                  </div>
                </div>
                <div className="mt-2 line-clamp-2 text-sm text-muted">
                  {p.description || "（无简介）"}
                </div>
                <div className="mt-4 text-xs text-muted">
                  {p.updated_at ? new Date(p.updated_at).toLocaleString() : ""}
                </div>
              </>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
