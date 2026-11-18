/// 审批 API（Task 12 消费 Task 11 后端）：
///   GET  /api/project/{id}/approvals/pending → {"pending": false} | {pending: true, question, options, count, multi}
///   POST /api/project/{id}/approvals/answer  {indices: number[]（1 起）, note}
///     → 200 {"ok": true}；校验失败 400 {"error"}；已被答/不存在 409 {"error"}（ApiError.status 区分）。

import { apiGet, apiPost } from "./client";
import type { ApprovalPendingData } from "./types";

export type PendingResponse =
  | { pending: false }
  | ({ pending: true } & ApprovalPendingData);

export function fetchPending(projectId: string): Promise<PendingResponse> {
  return apiGet(`/api/project/${encodeURIComponent(projectId)}/approvals/pending`);
}

export function postAnswer(
  projectId: string,
  indices: number[],
  note: string,
): Promise<{ ok: true }> {
  return apiPost(`/api/project/${encodeURIComponent(projectId)}/approvals/answer`, {
    indices,
    note,
  });
}
