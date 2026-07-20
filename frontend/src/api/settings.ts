import api from "./client";
import type { ModelConfig, UserSettings } from "@/types";

export interface ModelConfigPayload {
  name: string;
  base_url: string;
  model: string;
  api_key?: string;
  is_default?: boolean;
  level?: string;
  embedding_model?: string;
  embedding_dimension?: number;
}

export const settingsApi = {
  get: () => api.get<UserSettings>("/settings"),
  update: (data: Partial<UserSettings>) => api.put<UserSettings>("/settings", data),
  listModels: () => api.get<ModelConfig[]>("/settings/models"),
  createModel: (data: ModelConfigPayload) => api.post("/settings/models", data),
  updateModel: (id: string, data: Partial<ModelConfigPayload>) => api.put(`/settings/models/${id}`, data),
  deleteModel: (id: string) => api.delete(`/settings/models/${id}`),
  testModel: (data: Partial<ModelConfigPayload>) => api.post("/settings/models/test", data),
};
