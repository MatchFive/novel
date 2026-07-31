import api from "./client";

export const workflowApi = {
  list: () => api.get("/workflow/list"),

  extractMemory: (chapterId: string, autoApply = false) =>
    api.post(`/workflow/chapter/${chapterId}/memory`, { auto_apply: autoApply }),

  generateChapter: (projectId: string, chapterId: string) =>
    api.post(`/workflow/project/${projectId}/generate-chapter`, { chapter_id: chapterId }),

  auditForeshadows: (projectId: string) =>
    api.post(`/workflow/project/${projectId}/audit-foreshadows`),

  checkWorldConsistency: (projectId: string) =>
    api.post(`/workflow/project/${projectId}/check-world`),
};
