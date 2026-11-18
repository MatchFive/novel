/// 单条 issue 的展开详情（Task 14）：意见全文 + quote 原文 + 处置操作。
/// §4.9：无 quote 的全局类意见没有可定位的原文片段，只能已解决/忽略，
/// 「交给 AI 修」禁用；有 quote 才能交给 AI 修（diff 确认卡走审批弹窗，零额外接线）。
/// Task 15：有 quote 且有章号 → 「跳到正文」走跨视图引文跳转（§4.8）。

import { useState } from "react";
import { apiPost } from "../api/client";
import type { Issue } from "../api/types";

interface IssueDetailProps {
  projectId: string;
  issue: Issue;
  /** 处置成功后由父视图重新拉列表。 */
  onChanged: () => void;
  /** 「跳到正文」：由父视图接 navigate("prose", {seq, quote, issueId})（§4.8）。 */
  onJumpToQuote?: () => void;
}

export function IssueDetail({ projectId, issue, onChanged, onJumpToQuote }: IssueDetailProps) {
  const [busy, setBusy] = useState<"resolve" | "ignore" | "fix" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const base =
    `/api/project/${encodeURIComponent(projectId)}/issues/${encodeURIComponent(issue.id)}`;

  const settle = async (status: "resolved" | "ignored") => {
    if (busy) return;
    setBusy(status === "resolved" ? "resolve" : "ignore");
    setError(null);
    setNotice(null);
    try {
      await apiPost<{ id: string; status: string }>(`${base}/resolve`, { status });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const fix = async () => {
    if (busy) return;
    setBusy("fix");
    setError(null);
    setNotice(null);
    try {
      await apiPost<{ accepted: boolean }>(`${base}/fix`);
      setNotice("已交给 AI 修复：进度见事件流；diff 确认卡会出现在审批弹窗里。");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  // 全局类意见（quote 为空/纯空白）无从定位改写 → 只能终裁，fix 禁用（§4.9）。
  const globalNote = !(issue.quote ?? "").trim();

  return (
    <div className="issue-detail">
      <p className="issue-detail-text">{issue.detail}</p>
      {issue.quote ? (
        <>
          <blockquote className="issue-quote">{issue.quote}</blockquote>
          {issue.chapter != null && onJumpToQuote && (
            <div className="issue-actions">
              <button type="button" className="btn-ghost" onClick={onJumpToQuote}>
                跳到正文
              </button>
            </div>
          )}
        </>
      ) : (
        <p className="muted">全局类意见，无原文片段，只能已解决/忽略</p>
      )}
      {issue.status === "pending" && (
        <div className="issue-actions">
          <button
            type="button"
            className="btn-primary"
            disabled={busy !== null}
            onClick={() => void settle("resolved")}
          >
            {busy === "resolve" ? "提交中…" : "已解决"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy !== null}
            onClick={() => void settle("ignored")}
          >
            {busy === "ignore" ? "提交中…" : "忽略"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy !== null || globalNote}
            title={globalNote ? "全局类意见无原文片段，无法定位改写" : undefined}
            onClick={() => void fix()}
          >
            {busy === "fix" ? "提交中…" : "交给 AI 修"}
          </button>
        </div>
      )}
      {notice && <p className="muted">{notice}</p>}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
