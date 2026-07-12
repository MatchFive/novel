import api from "./client";

export const graphApi = {
  view: (pid: string) => api.get(`/graph/${pid}`),
};
