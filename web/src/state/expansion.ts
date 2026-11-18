/// 思考卡 / 工具结果卡的展开集合（§4.4.2 / §7.2）：
/// useState<Set<number>>（存事件 seq），**只存内存**——不落盘、不发服务端、
/// 不进全局 store、不写 localStorage；组件卸载 / 刷新即回默认折叠。

import { useCallback, useState } from "react";

export interface ExpansionControls {
  toggle: (seq: number) => void;
  expandAll: (seqs: number[]) => void;
  collapseAll: () => void;
  /** 清空展开集（Task 20：切项目时重置，防上一本书的展开态滞留）。 */
  reset: () => void;
}

export function useExpansion(): [Set<number>, ExpansionControls] {
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set<number>());

  const toggle = useCallback((seq: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });
  }, []);

  const expandAll = useCallback((seqs: number[]) => {
    setExpanded(new Set(seqs));
  }, []);

  const collapseAll = useCallback(() => {
    setExpanded(new Set());
  }, []);

  const reset = useCallback(() => {
    setExpanded(new Set());
  }, []);

  return [expanded, { toggle, expandAll, collapseAll, reset }];
}
