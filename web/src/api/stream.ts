/// SSE 客户端（Task 9 帧协议）：
///   event: hello  data: {"project","last_seq"}            → onHello
///   event: event  data: {seq,kind,payload,ts}             → onEvent
///   event: approval_pending data: {question,options,...}  → onApprovalPending
///   event: approval_cleared data: {}                       → onApprovalCleared（占位切「已答」）
///   ": ping" 为注释帧，EventSource 不冒泡，无需处理。
///
/// 断线：不用浏览器 Last-Event-ID——error 时自行 close + 延时重建 URL，
/// after = 已收最大 seq。hello 的 last_seq 不参与校准：服务端重连只补发
/// seq>after，把水位抬进 after 会跳过断线前尚未送达的历史（见 Task 10 修复）。

import type { ApprovalPendingData, HelloData, StreamEvent } from "./types";

export interface StreamCallbacks {
  onEvent: (event: StreamEvent) => void;
  onHello: (hello: HelloData) => void;
  /** approval_pending：pending.md 出现/换了一个 → 弹窗 + 琥珀占位。 */
  onApprovalPending: (pending: ApprovalPendingData) => void;
  /** approval_cleared：pending 已被答（pending.md 消失）→ 占位切「已答」。 */
  onApprovalCleared: () => void;
  /** 连接状态（Task 20）：false = 断线（即将 1s 后重连），true = 已连上（hello 到达）。 */
  onConnectionChange?: (connected: boolean) => void;
}

const RETRY_MS = 1000;

function parseJson(raw: unknown): unknown {
  if (typeof raw !== "string") return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function asStreamEvent(data: unknown): StreamEvent | null {
  if (typeof data !== "object" || data === null) return null;
  const obj = data as Record<string, unknown>;
  const seq = obj.seq;
  const kind = obj.kind;
  if (typeof seq !== "number" || typeof kind !== "string") return null;
  const payload = typeof obj.payload === "object" && obj.payload !== null
    ? (obj.payload as Record<string, unknown>)
    : {};
  return { seq, kind, payload, ts: typeof obj.ts === "string" ? obj.ts : "" };
}

/** 打开 SSE 流；返回关闭函数（组件卸载 / 切项目时调用）。 */
export function openStream(
  projectId: string,
  after: number,
  callbacks: StreamCallbacks,
): () => void {
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: number | null = null;
  // 已收最大 seq：重连 URL 的 after 只按实收事件推进。hello 的 last_seq
  // 只是服务端水位，抬它会让重连漏掉断线前没送到的历史（seq<=last_seq 不再补发）。
  let maxSeq = after;

  const scheduleReconnect = () => {
    if (closed || retryTimer !== null) return;
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      if (!closed) connect();
    }, RETRY_MS);
  };

  const connect = () => {
    if (closed) return;
    const url = `/api/project/${encodeURIComponent(projectId)}/stream?after=${maxSeq}`;
    const es = new EventSource(url);
    source = es;

    es.addEventListener("hello", (ev) => {
      const data = parseJson((ev as MessageEvent).data);
      if (typeof data !== "object" || data === null) return;
      const hello = data as Record<string, unknown>;
      callbacks.onHello({
        project: typeof hello.project === "string" ? hello.project : "",
        last_seq: typeof hello.last_seq === "number" ? hello.last_seq : 0,
      });
      callbacks.onConnectionChange?.(true);
    });

    es.addEventListener("event", (ev) => {
      const event = asStreamEvent(parseJson((ev as MessageEvent).data));
      if (event === null) return;
      if (event.seq > maxSeq) maxSeq = event.seq;
      callbacks.onEvent(event);
    });

    es.addEventListener("approval_pending", (ev) => {
      const data = parseJson((ev as MessageEvent).data);
      if (typeof data !== "object" || data === null) return;
      const obj = data as Record<string, unknown>;
      callbacks.onApprovalPending({
        question: typeof obj.question === "string" ? obj.question : "",
        options: Array.isArray(obj.options)
          ? obj.options.filter((o): o is string => typeof o === "string")
          : [],
        count: typeof obj.count === "number" ? obj.count : 0,
        multi: obj.multi === true,
      });
    });

    es.addEventListener("approval_cleared", () => {
      callbacks.onApprovalCleared();
    });

    es.onerror = () => {
      // 自己重建连接（after = maxSeq），不依赖浏览器自动重连 / Last-Event-ID。
      es.close();
      if (source === es) source = null;
      callbacks.onConnectionChange?.(false);  // 断流可见：界面提示「离线，重连中」
      scheduleReconnect();
    };
  };

  connect();

  return () => {
    closed = true;
    source?.close();
    source = null;
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer);
      retryTimer = null;
    }
  };
}
