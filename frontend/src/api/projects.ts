import api from "./client";
import type { Project } from "@/types";

export const projectsApi = {
  list: (type?: string) => api.get<Project[]>("/projects", { params: { type } }),
  get: (id: string) => api.get<Project>(`/projects/${id}`),
  create: (type: string, title: string, description = "") =>
    api.post<Project>("/projects", { type, title, description }),
  update: (id: string, data: Partial<Project>) => api.put(`/projects/${id}`, data),
  remove: (id: string) => api.delete(`/projects/${id}`),
};
