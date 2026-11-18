/// 审批弹窗（§4.5.1）：盖主区。问题正文 + 选项列表（单选 radio / 多选 checkbox）
/// + 可选备注 + 底部「稍后再说」（只关弹窗，琥珀占位保留）+「提交」。
/// 无超时（§4.5.3：run 无限等，浏览器关了 run 也继续等）。
/// 提交语义：未选不提交（本地校验）；服务端 400 显示其 error；409（已被答）→
/// 交给上层当已答处理（刷新 pending 状态）。

import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { postAnswer } from "../api/approvals";
import type { ApprovalPendingData } from "../api/types";

interface ApprovalDialogProps {
  projectId: string;
  pending: ApprovalPendingData;
  /** 「稍后再说」：只关弹窗。 */
  onClose: () => void;
  /** 200：提交成功（labels = 选中项文本，note = 备注）→ 占位切已答嵌入。 */
  onAnswered: (labels: string[], note: string) => void;
  /** 409：pending 已被答/不存在 → 视为已答，刷新 pending 状态。 */
  onConflict: () => void;
}

export function ApprovalDialog({
  projectId,
  pending,
  onClose,
  onAnswered,
  onConflict,
}: ApprovalDialogProps) {
  // 选项号从 1 起（与 pending.md / write_answer 协议一致）。
  const [selected, setSelected] = useState<number[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // ESC = 「稍后再说」的键盘等价物（Task 12 ②）：只关弹窗，琥珀占位保留。
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggle = (index: number) => {
    setError(null);
    setSelected((prev) =>
      pending.multi
        ? prev.includes(index)
          ? prev.filter((i) => i !== index)
          : [...prev, index]
        : [index],
    );
  };

  const submit = async () => {
    if (submitting) return;
    if (selected.length === 0) {
      setError("请先选择一个选项");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await postAnswer(projectId, selected, note);
      const labels = selected.map((i) => pending.options[i - 1] ?? String(i));
      onAnswered(labels, note);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        onConflict();
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("提交失败，请稍后重试");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="approval-overlay">
      <div className="approval-dialog" role="dialog" aria-modal="true" aria-label="待审批">
        <h3>待审批</h3>
        <p className="approval-question">{pending.question}</p>
        <div className="approval-options">
          {pending.options.map((option, i) => {
            const index = i + 1;
            return (
              <label key={index} className="approval-option">
                <input
                  type={pending.multi ? "checkbox" : "radio"}
                  name="approval-option"
                  checked={selected.includes(index)}
                  onChange={() => toggle(index)}
                />
                <span>
                  {index}. {option}
                </span>
              </label>
            );
          })}
        </div>
        <input
          className="approval-note"
          type="text"
          placeholder="备注（可选）"
          value={note}
          onChange={(ev) => setNote(ev.target.value)}
        />
        {error && <p className="error-text approval-error">{error}</p>}
        <div className="approval-actions">
          <button type="button" className="btn-ghost" onClick={onClose}>
            稍后再说
          </button>
          <button type="button" className="btn-primary" onClick={() => void submit()} disabled={submitting}>
            {submitting ? "提交中…" : "提交"}
          </button>
        </div>
      </div>
    </div>
  );
}
