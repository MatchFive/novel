import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { projectsApi } from "@/api/projects";
import type { Project } from "@/types";
import { Button, Input, Card } from "@/components/ui";

type Tab = "long" | "short";

export default function HomePage() {
  const [tab, setTab] = useState<Tab>("long");
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const nav = useNavigate();

  const load = async () => {
    const { data } = await projectsApi.list(tab);
    setProjects(data);
  };

  useEffect(() => { load(); }, [tab]);

  const create = async () => {
    if (!title.trim()) return;
    const { data } = await projectsApi.create(tab, title.trim());
    setCreating(false);
    setTitle("");
    nav(`/project/${tab}/${data.id}`);
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
          <div className="flex items-center gap-2">
            <Input
              placeholder={tab === "long" ? "长篇标题" : "短篇标题"}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && create()}
              autoFocus
            />
            <Button variant="primary" onClick={create}>创建</Button>
            <Button variant="ghost" onClick={() => setCreating(false)}>取消</Button>
          </div>
        </Card>
      )}

      <div className="mt-6 grid grid-cols-2 gap-4">
        {projects.length === 0 && (
          <div className="col-span-2 rounded-lg border border-dashed border-line bg-surface p-10 text-center text-sm text-muted">暂无项目</div>
        )}
        {projects.map((p) => (
          <button
            key={p.id}
            onClick={() => nav(`/project/${p.type}/${p.id}`)}
            className="rounded-lg border border-line bg-surface p-6 text-left shadow-soft transition-all hover:-translate-y-0.5 hover:shadow-card-hover"
          >
            <div className="font-serif text-lg font-medium text-ink">{p.title}</div>
            <div className="mt-2 line-clamp-2 text-sm text-muted">{p.description || "（无简介）"}</div>
            <div className="mt-4 text-xs text-muted">
              {p.updated_at ? new Date(p.updated_at).toLocaleString() : ""}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
