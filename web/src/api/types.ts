/// 七视图共用的 API JSON 形状（以 harness.web.readers / app.py 为准，逐字同形）。

// ---------------------------------------------------------------- 书架 / state

export interface ProjectRef {
  id: string;
  title: string;
}

export interface StateResponse {
  status: string | null;
  brief: string | null;
  counts: import("../state/session").Counts;
  running: boolean;
}

// ---------------------------------------------------------------- 事件流

export interface EventItem {
  seq: number;
  type?: string;
  [key: string]: unknown;
}

export interface EventsResponse {
  events: EventItem[];
  last_seq: number;
}

// ---------------------------------------------------------------- SSE 流

/** events.log 一行的完整形状（run/events.py append 口径：seq/kind/payload/ts）。 */
export interface StreamEvent {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  ts: string;
}

/** 首帧 event: hello 的 data（src/harness/web/stream.py 口径）。 */
export interface HelloData {
  project: string;
  last_seq: number;
}

/** event: approval_pending 的 data（spec §6.3；W3 接线，本任务仅占位）。 */
export interface ApprovalPendingData {
  question: string;
  options: string[];
  count: number;
  multi: boolean;
}

// ---------------------------------------------------------------- 大纲

export interface OutlineArc {
  vol?: number;
  title?: string;
  [key: string]: unknown;
}

export interface PlotBlock {
  seq: number;
  title: string;
  status: string;
  summary?: string;
  content?: string;
  [key: string]: unknown;
}

export interface OutlineVolume {
  vol: number;
  plots: PlotBlock[];
}

export interface OutlineResponse {
  arcs: OutlineArc[];
  planned_volume_count: number | null;
  volumes: OutlineVolume[];
}

// ---------------------------------------------------------------- 章节 / 正文

export interface ChapterRow {
  seq: number;
  title: string | null;
  status: string | null;
  plot_id: string | null;
  word_count: number | null;
  flavor_score: number | null;
  blocked_by: string[];
}

export interface ChaptersResponse {
  chapters: ChapterRow[];
}

export interface ChapterDetail {
  seq: number;
  meta: Record<string, unknown>;
  body: string;
  path: string;
  review: Record<string, unknown> | null;
}

/** PUT /chapters/{seq} 的应答：只改正文段，meta 由后端原样保留（§4.8）。 */
export interface ChapterSaveResponse {
  seq: number;
  word_count: number;
  status: string | null;
}

// ---------------------------------------------------------------- 角色

export interface CastMember {
  name: string;
  beats: number;
  chapters: number;
  first_chapter: number;
  share: number;
}

export interface CastMemberWithCard extends CastMember {
  /** 角色卡（list_characters 的 meta dict）；无卡 → null。 */
  card: Record<string, unknown> | null;
}

export interface CharactersResponse {
  protagonists: CastMemberWithCard[];
  candidates: CastMemberWithCard[];
  walk_ons: CastMember[];
  counts: { carded: number; pending: number; walk_on: number };
}

// ---------------------------------------------------------------- 记忆

export interface MemoryResponse {
  state: {
    characters: Record<string, unknown>;
    world: Record<string, unknown>; // 键为 "kind|key"（WORLD_KEY_SEP="|"）
  };
  state_text: string | null;
  digests: { vol: number; path: string }[];
  chapter_memories: number[];
}

export interface ChapterMemory extends Record<string, unknown> {
  empty_dimensions: string[];
}

// ---------------------------------------------------------------- 问题台账

export interface Issue {
  id: string;
  source: string;
  title: string;
  detail: string;
  quote: string | null;
  status: string;
  created_at: string;
  chapter: number | null;
}
