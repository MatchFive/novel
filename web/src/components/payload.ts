/// 事件 payload 的宽容读取工具：payload 是 Record<string, unknown>，
/// 后端字段漂移时返回缺省值而不是渲染崩掉。

export function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

/** 参数摘要（§4.4.3）：args JSON 渲染成「键: 值」，长值截 60 字符、全文放 title。 */
export interface ArgSummaryItem {
  key: string;
  /** 截断后的展示值。 */
  display: string;
  /** 完整值（放 title 属性）。 */
  full: string;
  truncated: boolean;
}

const ARG_DISPLAY_MAX = 60;

export function summarizeArgs(
  args: Record<string, unknown>,
  maxKeys = 4,
): ArgSummaryItem[] {
  const items: ArgSummaryItem[] = [];
  for (const [key, value] of Object.entries(args).slice(0, maxKeys)) {
    let full: string;
    if (typeof value === "string") full = value;
    else {
      try {
        full = JSON.stringify(value, null, 0) ?? String(value);
      } catch {
        full = String(value);
      }
    }
    const truncated = full.length > ARG_DISPLAY_MAX;
    items.push({
      key,
      display: truncated ? `${full.slice(0, ARG_DISPLAY_MAX)}…` : full,
      full,
      truncated,
    });
  }
  return items;
}
