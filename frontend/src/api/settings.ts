import api from "./client";
import type { ModelConfig, UserSettings } from "@/types";

export const settingsApi = {
  get: () => api.get<UserSettings>("/settings"),
  update: (data: Partial<UserSettings>) => api.put<UserSettings>("/settings", data),
  listModels: () => api.get<ModelConfig[]>("/settings/models"),
  createModel: (data: any) => api.post("/settings/models", data),
  updateModel: (id: string, data: any) => api.put(`/settings/models/${id}`, data),
  deleteModel: (id: string) => api.delete(`/settings/models/${id}`),
  testModel: (data: any) => api.post("/settings/models/test", data),
};
