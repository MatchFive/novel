/// 会话上下文：本任务钉死的状态约定（后续任务依赖，勿改形状）。
///   SessionContext = {projectId, projectTitle, counts, refreshCounts()}

import { createContext, useContext } from "react";

/** /api/project/{id}/state 的 counts 字段（app.py _counts 口径）。 */
export interface Counts {
  volumes: number;
  plots: number;
  chapters: number;
  prose: number;
  characters_pending: number;
  memory: number;
  issues_pending: number;
  events_last_seq: number;
}

export interface SessionContextValue {
  projectId: string;
  projectTitle: string;
  counts: Counts | null;
  /** 重新拉 /state 的 counts（提交任务 / 命令后可手动触发刷新）。 */
  refreshCounts: () => Promise<void>;
}

export const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (ctx === null) {
    throw new Error("useSession 必须在 SessionContext.Provider 内使用");
  }
  return ctx;
}
