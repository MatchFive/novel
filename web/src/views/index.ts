/// 视图注册表：ViewKey + 视图组件映射（无 react-router，切换 = useState + location.hash）。

import type { ComponentType } from "react";
import { EventStream } from "./EventStream";
import { Outline } from "./Outline";
import { Chapters } from "./Chapters";
import { Prose } from "./Prose";
import { Characters } from "./Characters";
import { Memory } from "./Memory";
import { Issues } from "./Issues";

export type ViewKey =
  | "events"
  | "outline"
  | "chapters"
  | "prose"
  | "characters"
  | "memory"
  | "issues";

export const VIEWS: Record<ViewKey, ComponentType> = {
  events: EventStream,
  outline: Outline,
  chapters: Chapters,
  prose: Prose,
  characters: Characters,
  memory: Memory,
  issues: Issues,
};

export const VIEW_KEYS = Object.keys(VIEWS) as ViewKey[];

export function viewFromHash(hash: string): ViewKey {
  const key = hash.replace(/^#/, "");
  return (VIEW_KEYS as string[]).includes(key) ? (key as ViewKey) : "events";
}
