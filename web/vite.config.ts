import { defineConfig } from "vite";

// 开发模式：/api 代理到 harness web 的 uvicorn（默认 127.0.0.1:8848）。
// 生产模式：后端 create_app 在 web/dist 存在时直接托管构建产物，无需代理。
export default defineConfig({
  esbuild: {
    // 不用 @vitejs/plugin-react：JSX 走 esbuild automatic 转换即可（无热更新需求差异）。
    jsx: "automatic",
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8848",
    },
  },
});
