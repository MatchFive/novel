import api from "./client";
import type { OutlinePayload } from "@/types";

export const longApi = {
  outlines: (pid: string) => api.get(`/long/outlines/${pid}`),
  addOutline: (data: OutlinePayload) => api.post("/long/outlines", data),
  updateOutline: (id: string, data: OutlinePayload) => api.put(`/long/outlines/${id}`, data),
  deleteOutline: (id: string) => api.delete(`/long/outlines/${id}`),
  outlineHistory: (pid: string, id: string) => api.get(`/long/outlines/${pid}/history/${id}`),
  splitOutline: (project_id: string, entity_id: string, message: string) =>
    api.post("/assistant/chat", { project_id, message, context: { entity_type: "outline", entity_id } }),

  characters: (pid: string) => api.get(`/long/characters/${pid}`),
  addCharacter: (data: any) => api.post("/long/characters", data),
  updateCharacter: (id: string, data: any) => api.put(`/long/characters/${id}`, data),
  deleteCharacter: (id: string) => api.delete(`/long/characters/${id}`),

  foreshadows: (pid: string) => api.get(`/long/foreshadows/${pid}`),
  addForeshadow: (data: any) => api.post("/long/foreshadows", data),
  updateForeshadow: (id: string, data: any) => api.put(`/long/foreshadows/${id}`, data),
  deleteForeshadow: (id: string) => api.delete(`/long/foreshadows/${id}`),

  world: (pid: string) => api.get(`/long/world/${pid}`),
  addWorld: (data: any) => api.post("/long/world", data),
  updateWorld: (id: string, data: any) => api.put(`/long/world/${id}`, data),
  deleteWorld: (id: string) => api.delete(`/long/world/${id}`),

  plot: (pid: string) => api.get(`/long/plot/${pid}`),
  addPlot: (data: any) => api.post("/long/plot", data),
  updatePlot: (id: string, data: any) => api.put(`/long/plot/${id}`, data),
  deletePlot: (id: string) => api.delete(`/long/plot/${id}`),

  chapters: (pid: string) => api.get(`/long/chapters/${pid}`),
  getChapter: (id: string) => api.get(`/long/chapters/detail/${id}`),
  addChapter: (data: any) => api.post("/long/chapters", data),
  updateChapter: (id: string, data: any) => api.put(`/long/chapters/${id}`, data),
  deleteChapter: (id: string) => api.delete(`/long/chapters/${id}`),
  reorderChapters: (project_id: string, chapter_ids: string[]) =>
    api.post("/long/chapters/reorder", { project_id, chapter_ids }),
};
