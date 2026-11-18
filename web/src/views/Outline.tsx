/// 大纲视图：卷骨架（arcs）+ 按卷分组的情节；planned_volume_count 顶栏提示。

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../api/client";
import type { OutlineResponse } from "../api/types";
import { ViewError } from "../components/ViewError";
import { useSession } from "../state/session";

export function Outline() {
  const { projectId } = useSession();
  const [data, setData] = useState<OutlineResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setData(null);
    setError(null);
    apiGet<OutlineResponse>(`/api/project/${encodeURIComponent(projectId)}/outline`)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (error) return <ViewError message={error} onRetry={reload} />;
  if (!data) return <div className="view"><p className="muted">加载中…</p></div>;

  return (
    <div className="view">
      <h2>
        大纲
        {data.planned_volume_count != null && (
          <span className="muted">（预计 {data.planned_volume_count} 卷）</span>
        )}
      </h2>

      <section>
        <h3>卷骨架</h3>
        {data.arcs.length === 0 && (
          <p className="muted empty-hint">（还没有卷骨架——起个 run 交付「规划大纲」，或在 outline/arcs.md 里手维护）</p>
        )}
        {data.arcs.map((arc, i) => (
          <div key={i} className="card">
            {Object.entries(arc).map(([k, v]) => (
              <div key={k} className="kv">
                <span className="kv-key">{k}</span>
                <span className="kv-value">{formatValue(v)}</span>
              </div>
            ))}
          </div>
        ))}
      </section>

      <section>
        <h3>情节</h3>
        {data.volumes.length === 0 && (
          <p className="muted empty-hint">（还没有情节——卷骨架定了之后，起 run 交付「第 N 卷情节」逐步填满）</p>
        )}
        {data.volumes.map((v) => (
          <div key={v.vol} className="card">
            <h4>第 {v.vol} 卷</h4>
            {v.plots.length === 0 && <p className="muted">（本卷暂无情节）</p>}
            {v.plots.map((p) => (
              <div key={p.seq} className="plot-block">
                <div className="plot-head">
                  <span className="plot-seq">P{p.seq}</span>
                  <span className="plot-title">{p.title}</span>
                  <span className={`badge status-${p.status}`}>{p.status}</span>
                </div>
                {p.summary && <p className="plot-summary">{p.summary}</p>}
              </div>
            ))}
          </div>
        ))}
      </section>
    </div>
  );
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}
