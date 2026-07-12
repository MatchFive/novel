import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { longApi } from "@/api/long";
import { assistantApi } from "@/api/short";
import { graphApi } from "@/api/graph";
import type { ChangeRecord } from "@/types";
import { Button, Input, Textarea, Card, SectionTitle } from "@/components/ui";

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
          ["world", "世界观"], ["plot", "剧情节点"], ["chapter", "章节"], ["graph", "图谱"], ["assistant", "创作助手"],
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
        {tab === "outline" && <OutlinePanel pid={id!} />}
        {tab === "character" && <CrudPanel pid={id!} kind="character" label="角色" fields={[
          { key: "name", label: "名称" }, { key: "traits", label: "性格" }, { key: "ability", label: "能力" }, { key: "status", label: "状态" },
        ]} />}
        {tab === "foreshadow" && <CrudPanel pid={id!} kind="foreshadow" label="伏笔" fields={[
          { key: "title", label: "标题" }, { key: "content", label: "内容" }, { key: "state", label: "状态(pending/revealed/abandoned)" },
        ]} />}
        {tab === "world" && <CrudPanel pid={id!} kind="world" label="世界观" fields={[
          { key: "category", label: "分类" }, { key: "content", label: "内容" },
        ]} />}
        {tab === "plot" && <CrudPanel pid={id!} kind="plot" label="剧情节点" fields={[
          { key: "title", label: "标题" }, { key: "summary", label: "概要" }, { key: "timeline_pos", label: "时间位置" },
        ]} />}
        {tab === "chapter" && <ChapterPanel pid={id!} />}
        {tab === "graph" && <GraphPanel pid={id!} />}
        {tab === "assistant" && <AssistantPanel pid={id!} />}
      </div>
    </div>
  );
}

function OutlinePanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  const load = async () => {
    const { data } = await longApi.outlines(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid]);

  const add = async () => {
    if (!title.trim()) return;
    await longApi.addOutline({ project_id: pid, title, content });
    setTitle(""); setContent(""); load();
  };

  return (
    <div>
      <SectionTitle>大纲树</SectionTitle>
      <div className="mt-4 space-y-3">
        {items.map((it) => (
          <Card key={it.id} className="p-4">
            <div className="flex items-start gap-3">
              <div className="flex-1">
                <div className="text-sm font-medium text-ink">{it.title || "（无标题）"}</div>
                <div className="mt-1 whitespace-pre-wrap text-sm text-muted">{it.content}</div>
              </div>
              <Button variant="ghost" onClick={() => { setTitle(it.title); setContent(it.content); }}>复制为新版</Button>
              <Button variant="ghost" onClick={async () => { await longApi.deleteOutline(it.id); load(); }}>删</Button>
            </div>
          </Card>
        ))}
      </div>
      <Card className="mt-4 space-y-3 p-4">
        <Input placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea placeholder="内容" rows={4} value={content} onChange={(e) => setContent(e.target.value)} />
        <div><Button variant="primary" onClick={add}>+ 新增大纲</Button></div>
      </Card>
    </div>
  );
}

const KIND_API: any = {
  character: { list: longApi.characters, add: longApi.addCharacter, upd: longApi.updateCharacter, del: longApi.deleteCharacter },
  foreshadow: { list: longApi.foreshadows, add: longApi.addForeshadow, upd: longApi.updateForeshadow, del: longApi.deleteForeshadow },
  world: { list: longApi.world, add: longApi.addWorld, upd: longApi.updateWorld, del: longApi.deleteWorld },
  plot: { list: longApi.plot, add: longApi.addPlot, upd: longApi.updatePlot, del: longApi.deletePlot },
};

function CrudPanel({ pid, kind, label, fields }: { pid: string; kind: string; label: string; fields: { key: string; label: string }[] }) {
  const [items, setItems] = useState<any[]>([]);
  const [form, setForm] = useState<Record<string, string>>({});
  const api = KIND_API[kind];

  const load = async () => {
    const { data } = await api.list(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid, kind]);

  const add = async () => {
    const payload: any = { project_id: pid };
    fields.forEach((f) => (payload[f.key] = form[f.key] || ""));
    await api.add(payload);
    setForm({}); load();
  };

  return (
    <div>
      <SectionTitle>{label}</SectionTitle>
      <div className="mt-4 space-y-3">
        {items.map((it) => (
          <Card key={it.id} className="p-4">
            <div className="flex items-start gap-3">
              <div className="flex-1 text-sm">
                {fields.map((f) => (
                  <div key={f.key} className="mt-1">
                    <span className="text-muted">{f.label}：</span>
                    <span className="whitespace-pre-wrap text-ink">{it[f.key] || "—"}</span>
                  </div>
                ))}
              </div>
              <Button variant="ghost" onClick={async () => { await api.del(it.id); load(); }}>删</Button>
            </div>
          </Card>
        ))}
      </div>
      <Card className="mt-4 space-y-3 p-4">
        {fields.map((f) => (
          <div key={f.key}>
            <label className="mb-1 block text-xs text-muted">{f.label}</label>
            <Input value={form[f.key] || ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
          </div>
        ))}
        <div><Button variant="primary" onClick={add}>+ 新增{label}</Button></div>
      </Card>
    </div>
  );
}

function ChapterPanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  const load = async () => {
    const { data } = await longApi.chapters(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid]);

  const add = async () => {
    if (!title.trim()) return;
    await longApi.addChapter({ project_id: pid, title, content, order: items.length });
    setTitle(""); setContent(""); load();
  };
  const saveContent = async (it: any, val: string) => {
    await longApi.updateChapter(it.id, { content: val });
  };

  return (
    <div>
      <SectionTitle>章节</SectionTitle>
      <div className="mt-4 space-y-3">
        {items.map((it) => (
          <Card key={it.id} className="p-4">
            <div className="text-sm font-medium text-ink">{it.title}</div>
            <Textarea className="mt-2" rows={6} defaultValue={it.content} onBlur={(e) => saveContent(it, e.target.value)} />
          </Card>
        ))}
      </div>
      <Card className="mt-4 space-y-3 p-4">
        <Input placeholder="章节标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea placeholder="正文" rows={4} value={content} onChange={(e) => setContent(e.target.value)} />
        <div><Button variant="primary" onClick={add}>+ 新增章节</Button></div>
      </Card>
    </div>
  );
}

function AssistantPanel({ pid }: { pid: string }) {
  const [msg, setMsg] = useState("");
  const [records, setRecords] = useState<ChangeRecord[]>([]);
  const [summary, setSummary] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("");

  const send = async () => {
    if (!msg.trim()) return;
    setBusy(true); setLog("分析中…");
    try {
      const { data } = await assistantApi.chat(pid, msg);
      setRecords(data.change_records);
      setSummary(data.summary);
      setSessionId(data.session_id);
      setLog("完成，请确认变更。");
    } catch (e: any) {
      setLog("错误：" + e.message);
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      const { data } = await assistantApi.confirm(sessionId);
      setLog(data.ok ? `已应用 ${data.applied.length} 条变更` : "部分失败：" + JSON.stringify(data.errors));
      setRecords([]);
    } finally { setBusy(false); }
  };

  const reject = async () => {
    if (!sessionId) return;
    await assistantApi.reject(sessionId);
    setRecords([]); setLog("已拒绝。");
  };

  return (
    <div>
      <SectionTitle>创作助手</SectionTitle>
      <p className="mt-2 text-xs text-muted">Agent 仅读取真实数据 → 生成变更建议 → 你确认后才落库。</p>
      <div className="mt-4 flex gap-2">
        <Input placeholder="描述你的创作意图，例如：为主角增加一个宿敌角色" value={msg} onChange={(e) => setMsg(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
        <Button variant="primary" onClick={send} disabled={busy}>发送</Button>
      </div>

      {summary && (
        <Card className="mt-4 bg-surface-2 p-4 text-sm">
          <div className="mb-2 text-xs font-medium text-muted">摘要</div>
          <div className="whitespace-pre-wrap text-ink">{summary}</div>
        </Card>
      )}

      {records.length > 0 && (
        <div className="mt-4 space-y-3">
          <div className="text-sm font-medium text-ink">待确认变更（{records.length}）</div>
          {records.map((r) => (
            <Card key={r.id} className="p-3 text-xs">
              <div className="font-medium text-ink">{r.action} / {r.entity_type} {r.entity_id || "(新增)"}</div>
              <pre className="mt-1 overflow-auto whitespace-pre-wrap text-muted">{JSON.stringify(r.after, null, 2)}</pre>
            </Card>
          ))}
          <div className="flex gap-2">
            <Button variant="primary" onClick={confirm}>确认并应用</Button>
            <Button variant="ghost" onClick={reject}>拒绝</Button>
          </div>
        </div>
      )}

      {log && <div className="mt-4 text-xs text-muted">{log}</div>}
    </div>
  );
}

function GraphPanel({ pid }: { pid: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await graphApi.view(pid);
      setData(data);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [pid]);

  if (loading) return <div className="text-sm text-muted">加载中…</div>;
  if (!data) return <div className="text-sm text-muted">无数据</div>;

  const colorByType: any = { character: "#3a2c22", foreshadow: "#b07a3c" };

  return (
    <div>
      <div className="flex items-center justify-between">
        <SectionTitle>知识图谱</SectionTitle>
        <span className="text-xs text-muted">数据源：{data.source}</span>
      </div>
      <div className="mt-2 text-xs text-muted">节点 {data.nodes.length} · 关系 {data.edges.length}（点击角色可查看关系）</div>
      <div className="mt-4 grid grid-cols-2 gap-4">
        <Card className="p-4">
          <div className="mb-2 text-xs font-medium text-muted">节点</div>
          <div className="space-y-1">
            {data.nodes.map((n: any) => (
              <div key={n.id} className="flex items-center gap-2 text-sm">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: colorByType[n.type] || "#9a8c7b" }} />
                <span className="text-ink">{n.label}</span>
                {n.state && <span className="text-[11px] text-muted">（{n.state}）</span>}
              </div>
            ))}
          </div>
        </Card>
        <Card className="p-4">
          <div className="mb-2 text-xs font-medium text-muted">关系</div>
          <div className="space-y-1 text-sm">
            {data.edges.length === 0 && <div className="text-muted">暂无关系</div>}
            {data.edges.map((e: any, i: number) => (
              <div key={i} className="text-xs">
                <span className="font-medium text-ink">{e.from}</span> <span className="text-muted">—{e.label}→</span> <span className="font-medium text-ink">{e.to}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
