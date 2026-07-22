/* 前端日志：静默上报到后端 /api/log，并捕获全局未处理异常。 */
import api from "@/api/client";

type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

function send(level: LogLevel, message: string, meta?: Record<string, unknown>) {
  try {
    api.post("/log", { level, message, source: "frontend", meta }).catch(() => {
      // 避免日志本身触发循环错误
    });
  } catch {
    // ignore
  }
}

export const logger = {
  debug: (message: string, meta?: Record<string, unknown>) => send("DEBUG", message, meta),
  info: (message: string, meta?: Record<string, unknown>) => send("INFO", message, meta),
  warn: (message: string, meta?: Record<string, unknown>) => send("WARNING", message, meta),
  error: (message: string, meta?: Record<string, unknown>) => send("ERROR", message, meta),
  critical: (message: string, meta?: Record<string, unknown>) => send("CRITICAL", message, meta),
};

function registerGlobalHandlers() {
  window.onerror = (message, source, lineno, colno, error) => {
    logger.error("未捕获的全局错误", {
      message: String(message),
      source,
      lineno,
      colno,
      stack: error?.stack,
    });
  };

  window.onunhandledrejection = (event) => {
    const reason = event.reason;
    logger.error("未处理的 Promise 拒绝", {
      reason: reason instanceof Error ? reason.message : String(reason),
      stack: reason instanceof Error ? reason.stack : undefined,
    });
  };
}

registerGlobalHandlers();

export default logger;
