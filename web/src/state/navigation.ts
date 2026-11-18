/// 视图间导航（Task 15 跨视图联动）：navigate(view, jump?) + App 层 pendingJump。
/// pendingJump 由 Prose 视图取走（consumeJump）后本地持有，直到用户点角标返回或手动关闭。

import { createContext, useContext } from "react";
import type { ViewKey } from "../views";

/** 引文跳转参数（§4.8）：目标章 + 引文原文 + 来源 issue id。 */
export interface ProseJump {
  seq: number;
  quote: string;
  issueId: string;
}

export interface NavigationValue {
  navigate: (view: ViewKey, jump?: ProseJump) => void;
  /** 待消费的引文跳转；非 null 时 Prose 应取走并 consumeJump（§4.8）。 */
  pendingJump: ProseJump | null;
  consumeJump: () => void;
}

export const NavigationContext = createContext<NavigationValue | null>(null);

export function useNavigation(): NavigationValue {
  const ctx = useContext(NavigationContext);
  if (ctx === null) {
    throw new Error("useNavigation 必须在 NavigationContext.Provider 内使用");
  }
  return ctx;
}
