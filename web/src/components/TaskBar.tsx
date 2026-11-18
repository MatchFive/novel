/// TaskBar：事件流视图顶部的任务输入框（Task 5 移交的前端部分）。
/// POST /api/project/{id}/task，body {"raw", "deliverables"}；deliverables 缺省 ["outline"]。

import { useState } from "react";
import { apiPost, ApiError } from "../api/client";
import { useSession } from "../state/session";

const DELIVERABLE_HINT = "可用交付物：outline（大纲）/ chapters（章节规划）/ prose（正文）/ 等，逗号分隔；缺省 outline";

export function TaskBar() {
  const { projectId, refreshCounts } = useSession();
  const [raw, setRaw] = useState("");
  const [deliverables, setDeliverables] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);

  async function submit() {
    if (!raw.trim() || busy) return;
    setBusy(true);
    setMessage(null);
    const parsed = deliverables
      .split(/[,，、\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const resp = await apiPost<{ status: string }>(
        `/api/project/${encodeURIComponent(projectId)}/task`,
        { raw, deliverables: parsed.length ? parsed : ["outline"] },
      );
      setIsError(false);
      setMessage(`已起 run（status=${resp.status}）`);
      setRaw("");
      setDeliverables("");
      void refreshCounts();
    } catch (err) {
      setIsError(true);
      if (err instanceof ApiError && err.status === 409) {
        setMessage(err.message.includes("busy") ? "已有 run 在进行中（busy）" : err.message);
      } else {
        setMessage(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="taskbar">
      <div className="taskbar-row">
        <textarea
          className="taskbar-input"
          rows={3}
          placeholder="向 harness 描述本次写作任务……"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
        />
      </div>
      <div className="taskbar-row taskbar-actions">
        <input
          className="taskbar-deliverables"
          placeholder={DELIVERABLE_HINT}
          value={deliverables}
          onChange={(e) => setDeliverables(e.target.value)}
        />
        <button className="btn-primary" disabled={busy || !raw.trim()} onClick={() => void submit()}>
          {busy ? "提交中…" : "起 run"}
        </button>
      </div>
      {message && <p className={isError ? "taskbar-msg error" : "taskbar-msg"}>{message}</p>}
    </div>
  );
}
