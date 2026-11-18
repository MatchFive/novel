/// 问题台账：按来源分栏（分节）表格；标题展开 IssueDetail（Task 14：全文 + quote + 处置）。
/// Task 15：quote + 章号 → 「跳到正文」navigate("prose", {seq, quote, issueId})（§4.8）。

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../api/client";
import type { Issue } from "../api/types";
import { IssueDetail } from "../components/IssueDetail";
import { ViewError } from "../components/ViewError";
import { useSession } from "../state/session";
import { useNavigation } from "../state/navigation";

export function Issues() {
  const { projectId } = useSession();
  const { navigate } = useNavigation();
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const reload = useCallback(() => {
    setIssues(null);
    setError(null);
    apiGet<Issue[]>(`/api/project/${encodeURIComponent(projectId)}/issues`)
      .then(setIssues)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (error) return <ViewError message={error} onRetry={reload} />;
  if (!issues) return <div className="view"><p className="muted">加载中…</p></div>;

  const filtered = statusFilter === "all" ? issues : issues.filter((i) => i.status === statusFilter);
  const bySource = new Map<string, Issue[]>();
  for (const issue of filtered) {
    const list = bySource.get(issue.source) ?? [];
    list.push(issue);
    bySource.set(issue.source, list);
  }

  return (
    <div className="view">
      <h2>
        问题台账
        <select
          className="filter-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="pending">pending</option>
          <option value="resolved">resolved</option>
          <option value="ignored">ignored</option>
        </select>
      </h2>
      {filtered.length === 0 && (
        <p className="muted empty-hint">
          {issues.length === 0
            ? "（没有问题记录——验收 / 审计 / 现实参照核查发现的问题会汇到这里）"
            : "（当前过滤条件下没有问题）"}
        </p>
      )}
      {[...bySource.entries()].map(([source, list]) => (
        <section key={source}>
          <h3>来源：{source}（{list.length}）</h3>
          <table className="data-table">
            <thead>
              <tr><th>id</th><th>标题</th><th>状态</th><th>章</th><th>创建时间</th></tr>
            </thead>
            <tbody>
              {list.map((i) => (
                <tr key={i.id}>
                  <td>{i.id}</td>
                  <td>
                    <details>
                      <summary>{i.title}</summary>
                      <IssueDetail
                        projectId={projectId}
                        issue={i}
                        onChanged={reload}
                        onJumpToQuote={
                          i.chapter != null && i.quote
                            ? () =>
                                navigate("prose", {
                                  seq: i.chapter as number,
                                  quote: i.quote as string,
                                  issueId: i.id,
                                })
                            : undefined
                        }
                      />
                    </details>
                  </td>
                  <td><span className={`badge status-${i.status}`}>{i.status}</span></td>
                  <td>{i.chapter ?? "–"}</td>
                  <td className="muted">{i.created_at.slice(0, 19).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
