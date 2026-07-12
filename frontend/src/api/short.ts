import api from "./client";

export const shortApi = {
  progress: (pid: string) => api.get(`/short/${pid}/progress`),
  update: (pid: string, data: any) => api.put(`/short/${pid}`, data),
  setHook: (pid: string, hook: string) => api.post(`/short/${pid}/hook`, { hook }),
  genPlans: (pid: string) => api.post(`/short/${pid}/plans`),
  selectPlan: (pid: string, index: number) => api.post(`/short/${pid}/plans/select`, { index }),
  genDetail: (pid: string) => api.post(`/short/${pid}/detail`),
  genChapters: (pid: string) => api.post(`/short/${pid}/chapters`),
  writeChapter: (pid: string, index: number) => api.post(`/short/${pid}/chapters/${index}/write`),
  integrate: (pid: string) => api.post(`/short/${pid}/integrate`),
};

export const hotspotApi = {
  fetch: (pid: string, url?: string) => api.post("/hotspots/fetch", { project_id: pid, source_url: url }),
  analyze: (pid: string, ids?: string[]) => api.post("/hotspots/analyze", { project_id: pid, hotspot_ids: ids }),
  stored: (pid: string) => api.get(`/hotspots/${pid}/stored`),
};

export const assistantApi = {
  chat: (pid: string, message: string) => api.post("/assistant/chat", { project_id: pid, message }),
  session: (pid: string) => api.get(`/assistant/session/${pid}`),
  confirm: (sessionId: string) => api.post("/assistant/confirm", { session_id: sessionId }),
  reject: (sessionId: string) => api.post("/assistant/reject", { session_id: sessionId }),
};
