/// 章节视图：逐章表格（seq/title/status/plot_id/word_count/flavor_score/blocked_by）。

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../api/client";
import type { ChaptersResponse } from "../api/types";
import { ViewError } from "../components/ViewError";
import { useSession } from "../state/session";

export function Chapters() {
  const { projectId } = useSession();
  const [data, setData] = useState<ChaptersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setData(null);
    setError(null);
    apiGet<ChaptersResponse>(`/api/project/${encodeURIComponent(projectId)}/chapters`)
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
      <h2>章节</h2>
      {data.chapters.length === 0 && (
        <p className="muted empty-hint">
          （还没有章节——在大纲里规划情节后，起个 run 交付「写第 N 章」即可开写）
        </p>
      )}
      {data.chapters.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>标题</th>
              <th>状态</th>
              <th>情节</th>
              <th>字数</th>
              <th>去味分</th>
              <th>被拦（blocked_by）</th>
            </tr>
          </thead>
          <tbody>
            {data.chapters.map((c) => (
              <tr key={c.seq} className={c.blocked_by.length ? "row-blocked" : undefined}>
                <td>{c.seq}</td>
                <td>{c.title ?? "–"}</td>
                <td><span className={`badge status-${c.status ?? "none"}`}>{c.status ?? "–"}</span></td>
                <td>{c.plot_id ?? "–"}</td>
                <td>{c.word_count ?? "–"}</td>
                <td>{c.flavor_score ?? "–"}</td>
                <td>{c.blocked_by.length ? c.blocked_by.join("、") : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
