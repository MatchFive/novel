import type { ChangeRecord } from "@/types";
import api from "./client";

export const assistantApi = {
  chat: (pid: string | null, message: string, context?: Record<string, any>) =>
    api.post("/assistant/chat", { project_id: pid, message, context }, { timeout: 600000 }),
  session: (pid: string) => api.get(`/assistant/session/${pid}`),
  history: (pid: string) => api.get(`/assistant/session/${pid}/history`),
  sessions: (pid: string) => api.get(`/assistant/sessions/${pid}`),
  createSession: (pid: string) => api.post(`/assistant/session/${pid}`),
  switchSession: (sessionId: string) => api.post(`/assistant/session/${sessionId}/switch`),
  stage: (sessionId: string, record: ChangeRecord) =>
    api.post("/assistant/stage", { session_id: sessionId, change_record: record }),
  confirm: (sessionId: string, changeIds?: string[]) =>
    api.post("/assistant/confirm", { session_id: sessionId, change_ids: changeIds }),
  reject: (sessionId: string, changeIds?: string[]) =>
    api.post("/assistant/reject", { session_id: sessionId, change_ids: changeIds }),
  undo: (projectId: string, entityType: string, entityId: string) =>
    api.post("/assistant/undo", { project_id: projectId, entity_type: entityType, entity_id: entityId }),
  undoable: (chapterId: string) => api.get(`/assistant/undoable/${chapterId}`),
};
