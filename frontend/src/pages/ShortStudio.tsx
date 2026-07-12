import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { shortApi, hotspotApi } from "@/api/short";
import { Button, Input, Textarea, Card } from "@/components/ui";

const STEPS = ["爽点", "方案", "详细规划", "章节规划", "写作", "整合"];

export default function ShortStudio() {
  const { id } = useParams();
  const nav = useNavigate();
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("");

  const load = async () => {
    const { data } = await shortApi.progress(id!);
    setData(data);
  };
  useEffect(() => { load(); }, [id]);

  if (!data) return <div className="p-10 text-sm text-muted">加载中…</div>;

  const step = data.step ?? 0;
  const run = async (fn: () => Promise<any>, okMsg: string) => {
    setBusy(true); setLog("生成中…");
    try { const { data: r } = await fn(); setData(r); setLog(okMsg); }
    catch (e: any) { setLog("错误：" + e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="flex h-full">
      <aside className="w-52 shrink-0 border-r border-line bg-surface p-4">
        <Button variant="subtle" className="mb-4 w-full justify-start" onClick={() => nav("/")}>← 返回</Button>
        {STEPS.map((s, i) => (
          <div key={i} className={"px-3 py-2 text-sm " + (i < step ? "text-accent" : i === step ? "font-medium text-ink" : "text-muted")}>
            {i + 1}. {s}{i < step ? " ✓" : ""}
          </div>
        ))}
      </aside>

      <div className="flex-1 overflow-auto p-8">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="font-serif text-xl font-medium text-ink">短篇六步法</h2>
          <Button variant="ghost" onClick={load}>刷新</Button>
        </div>
        {log && <div className="mb-4 text-xs text-muted">{log}</div>}

        {/* Step 1: 爽点 */}
        <Section title="1. 爽点 / 核心设定">
          <Textarea rows={3} value={data.core_hook || ""} onChange={(e) => setData({ ...data, core_hook: e.target.value })} placeholder="描述这个故事的爽点…" />
          <Button variant="primary" className="mt-2" disabled={busy || !data.core_hook} onClick={() => run(() => shortApi.setHook(id!, data.core_hook), "已保存爽点")}>保存爽点</Button>
        </Section>

        {/* Step 2: 方案 */}
        <Section title="2. 剧情方案">
          <Button variant="primary" disabled={busy} onClick={() => run(() => shortApi.genPlans(id!), "已生成方案")}>生成方案</Button>
          <div className="mt-3 space-y-3">
            {(data.plans || []).map((p: any, i: number) => (
              <Card key={i} className="p-4 text-sm">
                <div className="font-medium text-ink">{p.name}</div>
                <div className="mt-1 text-xs text-muted">{p.direction} / {p.conflict}</div>
                <Button variant="ghost" className="mt-2" disabled={busy} onClick={() => run(() => shortApi.selectPlan(id!, i), "已选定方案")}>选定此方案</Button>
              </Card>
            ))}
          </div>
        </Section>

        {/* Step 3: 详细规划 */}
        <Section title="3. 详细规划">
          <Button variant="primary" disabled={busy || !data.selected_plan} onClick={() => run(() => shortApi.genDetail(id!), "已生成详细规划")}>生成详细规划</Button>
          <Textarea className="mt-2" rows={6} value={data.detail_plan || ""} onChange={(e) => setData({ ...data, detail_plan: e.target.value })} />
        </Section>

        {/* Step 4: 章节规划 */}
        <Section title="4. 章节规划">
          <Button variant="primary" disabled={busy || !data.detail_plan} onClick={() => run(() => shortApi.genChapters(id!), "已生成章节规划")}>生成章节规划</Button>
          <div className="mt-2 space-y-1">
            {(data.chapters_plan || []).map((c: any, i: number) => (
              <div key={i} className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink">{i + 1}. {c.title} — {c.summary}</div>
            ))}
          </div>
        </Section>

        {/* Step 5: 写作 */}
        <Section title="5. 写作">
          <div className="space-y-3">
            {(data.chapters_plan || []).map((c: any, i: number) => {
              const written = (data.writing || [])[i];
              return (
                <Card key={i} className="p-4">
                  <div className="text-sm font-medium text-ink">{i + 1}. {c.title}</div>
                  {written ? (
                    <div className="mt-1 whitespace-pre-wrap text-xs text-ink">{written.content}</div>
                  ) : (
                    <Button variant="ghost" className="mt-2" disabled={busy} onClick={() => run(() => shortApi.writeChapter(id!, i), `已写作第${i + 1}章`)}>写本章</Button>
                  )}
                </Card>
              );
            })}
          </div>
        </Section>

        {/* Step 6: 整合 */}
        <Section title="6. 整合">
          <Button variant="primary" disabled={busy || (data.writing || []).length === 0} onClick={() => run(() => shortApi.integrate(id!), "已整合")}>整合全文</Button>
          <Textarea className="mt-2" rows={10} value={data.integration || ""} onChange={(e) => setData({ ...data, integration: e.target.value })} placeholder="整合结果…" />
        </Section>

        <HotspotPanel pid={id!} />
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="mb-6 p-5">
      <div className="mb-3 text-sm font-medium text-ink">{title}</div>
      {children}
    </Card>
  );
}

function HotspotPanel({ pid }: { pid: string }) {
  const [items, setItems] = useState<any[]>([]);
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    const { data } = await hotspotApi.stored(pid);
    setItems(data);
  };
  useEffect(() => { load(); }, [pid]);

  const fetch = async () => {
    setLoading(true);
    try { await hotspotApi.fetch(pid, url || undefined); await load(); }
    finally { setLoading(false); }
  };
  const analyze = async () => {
    setLoading(true);
    try { await hotspotApi.analyze(pid); await load(); }
    finally { setLoading(false); }
  };

  return (
    <Card className="mb-6 p-5">
      <div className="mb-3 text-sm font-medium text-ink">热搜辅助</div>
      <div className="flex gap-2">
        <Input placeholder="热搜源 URL（留空用设置中的源）" value={url} onChange={(e) => setUrl(e.target.value)} />
        <Button variant="ghost" disabled={loading} onClick={fetch}>抓取</Button>
        <Button variant="ghost" disabled={loading} onClick={analyze}>LLM 分析</Button>
      </div>
      <div className="mt-3 space-y-2">
        {items.map((h) => (
          <Card key={h.id} className="p-3 text-sm">
            <div className="text-ink">{h.title}</div>
            {h.analysis?.advice && <div className="mt-1 text-xs text-muted">建议：{h.analysis.advice}</div>}
          </Card>
        ))}
      </div>
    </Card>
  );
}
