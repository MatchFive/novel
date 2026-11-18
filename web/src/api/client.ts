/// API 薄封装：fetch + JSON，错误统一抛 ApiError（带 HTTP status）。
/// 形状契约以 src/harness/web/app.py 与 readers.py 为准（唯一权威）。

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`.trim();
    try {
      const data: unknown = await resp.json();
      const obj = data as Record<string, unknown>;
      if (typeof obj?.detail === "string") detail = obj.detail;
      else if (typeof obj?.error === "string") detail = obj.error;
      else detail = JSON.stringify(data);
    } catch {
      // 非 JSON 错误体：保留 status/statusText。
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
}
