import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "/api",
  timeout: 120000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const data = error?.response?.data;
    const msg = (data && data.message) || error.message || "请求失败";
    return Promise.reject(new Error(msg));
  }
);

export default api;
