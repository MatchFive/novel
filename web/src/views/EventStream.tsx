/// 对话式事件流主屏（DeepSeek-harness 风格交互）：
/// SSE 消费 + 事件 → **对话气泡**映射——run_started = 用户气泡（右），
/// agent_step + 紧随其后的 tool_result = 助理回合（左，思考/工具调用折叠在体内），
/// 其余 kind = 居中细行/横幅。输入框（TaskBar）钉在底部，新消息自动吸底。
/// 展开状态只存内存（§7.2），环形缓冲 useReducer 上限 5000 条丢最旧（§7.3）。

import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { fetchPending } from "../api/approvals";
import { openStream } from "../api/stream";
import type { ApprovalPendingData, HelloData, StreamEvent } from "../api/types";
import { ApprovalDialog } from "../components/ApprovalDialog";
import { ApprovalInline } from "../components/ApprovalInline";
import { DraftProgress } from "../components/DraftProgress";
import { GateNotice, isGateObservation } from "../components/GateNotice";
import { ThoughtCard } from "../components/ThoughtCard";
import { ToolResultCard } from "../components/ToolResultCard";
import { bool, num, str } from "../components/payload";
import { TaskBar } from "../components/TaskBar";
import { useExpansion } from "../state/expansion";
import { useSession } from "../state/session";

// ---------------------------------------------------------------- 环形缓冲（§7.3）

const RING_CAP = 5000;

type RingAction = { type: "append"; events: StreamEvent[] } | { type: "reset" };

function ringReducer(state: StreamEvent[], action: RingAction): StreamEvent[] {
  if (action.type === "reset") return [];
  if (action.events.length === 0) return state;
  const merged = state.length === 0 ? action.events : [...state, ...action.events];
  return merged.length > RING_CAP ? merged.slice(merged.length - RING_CAP) : merged;
}

// ---------------------------------------------------------------- 展示项：draft_progress 聚合（§4.4.4）

interface DraftGroup {
  /** 组首事件 seq（行 key 用）。 */
  rowKey: number;
  chapterSeq: number;
  phase: string;
  beat: number;
  chars: number;
}

type DisplayItem =
  | { type: "event"; event: StreamEvent }
  | { type: "draft"; group: DraftGroup };

/** draft_progress 连续同 (章 seq, phase) 归一行为一条（相位换组，beat/chars 取最新一拍）。 */
function toDisplayItems(events: StreamEvent[]): DisplayItem[] {
  const items: DisplayItem[] = [];
  let run: DraftGroup | null = null;
  const flush = () => {
    if (run !== null) items.push({ type: "draft", group: run });
    run = null;
  };
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.kind === "draft_progress") {
      const chapterSeq = num(e.payload.seq);
      const phase = str(e.payload.phase, "draft");
      if (run !== null && run.chapterSeq === chapterSeq && run.phase === phase) {
        run.beat = num(e.payload.beat, run.beat);
        run.chars = num(e.payload.chars, run.chars);
      } else {
        flush();
        run = {
          rowKey: e.seq,
          chapterSeq,
          phase,
          beat: num(e.payload.beat),
          chars: num(e.payload.chars),
        };
      }
      continue;
    }
    flush();
    items.push({ type: "event", event: e });
  }
  flush();
  return items;
}

// ---------------------------------------------------------------- 对话分组

/**
 * 事件 → 对话项：
 * - run_started = 用户气泡（一次 run 一条，重开 run 另起一条）；
 * - agent_step = 助理回合起点，紧随其后的 tool_result 折进同一回合（Claude Code 式）；
 * - 其余（含 draft 聚合组）= 系统项，沿用原细行/横幅渲染。
 */
type ChatItem =
  | { type: "user"; key: number; raw: string; deliverables: string[] }
  | { type: "assistant"; key: number; event: StreamEvent; tools: StreamEvent[] }
  | { type: "system"; key: number; item: DisplayItem };

function toChatItems(displayItems: DisplayItem[]): ChatItem[] {
  const items: ChatItem[] = [];
  let cur: Extract<ChatItem, { type: "assistant" }> | null = null;
  const flush = () => {
    if (cur !== null) {
      items.push(cur);
      cur = null;
    }
  };
  for (const d of displayItems) {
    if (d.type === "draft") {
      flush();
      items.push({ type: "system", key: d.group.rowKey, item: d });
      continue;
    }
    const e = d.event;
    if (e.kind === "run_started") {
      flush();
      const del = Array.isArray(e.payload.deliverables)
        ? e.payload.deliverables.filter((v): v is string => typeof v === "string")
        : [];
      items.push({ type: "user", key: e.seq, raw: str(e.payload.raw), deliverables: del });
      continue;
    }
    if (e.kind === "agent_step") {
      flush();
      cur = { type: "assistant", key: e.seq, event: e, tools: [] };
      continue;
    }
    if (e.kind === "tool_result" && cur !== null) {
      cur.tools.push(e);
      continue;
    }
    flush();
    items.push({ type: "system", key: e.seq, item: d });
  }
  flush();
  return items;
}

// ---------------------------------------------------------------- 审批状态机（§4.5）

/**
 * 待审批卡状态（§4.5.1 两种形态）：未答 → 琥珀占位 + 弹窗；已答 → 占位变 ApprovalInline。
 * labels/note 在本地提交或 user_choice 事件到达时拿到；只有 cleared 信号时退化为「已答」。
 */
interface PendingState {
  data: ApprovalPendingData;
  answered: boolean;
  rejected: boolean;
  labels: string[] | null;
  note: string;
}

function pendingFrom(data: ApprovalPendingData): PendingState {
  return { data, answered: false, rejected: false, labels: null, note: "" };
}

/** 已答定案：保留原 data，切 answered；labels 缺省沿用（本地提交先于事件到达时）。 */
function answeredOf(
  prev: PendingState,
  patch: { labels?: string[] | null; note?: string; rejected?: boolean },
): PendingState {
  return {
    ...prev,
    answered: true,
    rejected: patch.rejected ?? prev.rejected,
    labels: patch.labels === undefined ? prev.labels : patch.labels,
    note: patch.note ?? prev.note,
  };
}

// ---------------------------------------------------------------- 视图

export function EventStream() {
  const { projectId } = useSession();
  const [events, dispatch] = useReducer(ringReducer, []);
  const [hello, setHello] = useState<HelloData | null>(null);
  const [expanded, { toggle, expandAll, collapseAll, reset }] = useExpansion();
  const [pending, setPending] = useState<PendingState | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  // 连接状态（Task 20）：null = 尚未连上（首连中），true = 已连接，false = 断线重连中。
  const [connected, setConnected] = useState<boolean | null>(null);
  const maxSeqRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  /** 吸底闩：用户滚离底部 ≥80px 后不再自动吸底（回底后重新吸）。 */
  const stickRef = useRef(true);

  useEffect(() => {
    dispatch({ type: "reset" });
    maxSeqRef.current = 0;
    setHello(null);
    setPending(null);
    setDialogOpen(false);
    setConnected(null);
    stickRef.current = true;
    reset();  // 展开集随项目重置（Task 20 多项目切换）

    // §4.5.3 页面重开：pending 还挂着（run 无限等）→ GET pending 有值 → 重新弹窗。
    // SSE 重连也会补推 approval_pending（新连接 announced 复位），这里覆盖连上之前的空窗。
    fetchPending(projectId)
      .then((resp) => {
        if (resp.pending) {
          setPending((prev) => prev ?? pendingFrom(resp));
          setDialogOpen(true);
        }
      })
      .catch(() => {
        // pending 探测失败不阻塞事件流。
      });

    const close = openStream(projectId, 0, {
      onEvent: (event) => {
        // 重放/并发防御：seq 不递增的直接丢（seq 契约见 run/events.py）。
        if (event.seq <= maxSeqRef.current) return;
        maxSeqRef.current = event.seq;
        dispatch({ type: "append", events: [event] });
        // §4.4.1：答案事件到达 → 弹窗关、占位立即切「已答」嵌入。
        if (event.kind === "user_choice") {
          const picked = str(event.payload.picked);
          setPending((prev) =>
            prev && !prev.answered
              ? answeredOf(prev, { labels: picked ? [picked] : null, note: str(event.payload.note) })
              : prev,
          );
          setDialogOpen(false);
        } else if (event.kind === "plan_approved") {
          setPending((prev) => (prev && !prev.answered ? answeredOf(prev, { labels: ["规划已确认"] }) : prev));
          setDialogOpen(false);
        } else if (event.kind === "plan_rejected") {
          setPending((prev) =>
            prev && !prev.answered ? answeredOf(prev, { labels: ["规划已驳回"], rejected: true }) : prev,
          );
          setDialogOpen(false);
        }
      },
      onHello: (h) => {
        setHello(h);
        // 不抬 maxSeqRef：hello.last_seq 是服务端全量水位，首连时紧随其后的
        // backlog 重放（seq<=last_seq）会被 onEvent 的递增判重全部误杀——首屏历史
        // 全丢。重连防重放不需要 hello 校准：重连补发的只能是 seq>after 的事件，
        // 而已渲染事件的 seq 必 <= maxSeqRef，onEvent 判重天然拦截重复。
      },
      onApprovalPending: (data) => {
        // pending.md 出现（或换了一个）→ 琥珀占位 + 弹窗。不设超时。
        setPending(pendingFrom(data));
        setDialogOpen(true);
      },
      onApprovalCleared: () => {
        // run 消费答案清盘 → 占位切「已答」；labels 未知时 ApprovalInline 显示「已答」。
        setPending((prev) => (prev && !prev.answered ? answeredOf(prev, { labels: null }) : prev));
        setDialogOpen(false);
      },
      onConnectionChange: setConnected,
    });
    return close;
    // dispatch/setState 引用稳定，只随项目重建流。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const displayItems = useMemo(() => toDisplayItems(events), [events]);
  const chatItems = useMemo(() => toChatItems(displayItems), [displayItems]);

  /** 新对话项到达时吸底（用户贴底才吸；上翻阅读时不打断）。 */
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [chatItems, pending]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  /** chapter_start 的 target_words（draft 进度条宽度用）。 */
  const targetByChapter = useMemo(() => {
    const map = new Map<number, number>();
    for (const e of events) {
      if (e.kind === "chapter_start") map.set(num(e.payload.seq), num(e.payload.target_words));
    }
    return map;
  }, [events]);

  /** 工具卡参数 / 耗时：紧邻上一条 agent_step 的 args 与 ts 差。 */
  const stepContext = useMemo(() => {
    const map = new Map<number, { args: Record<string, unknown>; durationMs: number | null }>();
    for (let i = 0; i < events.length; i++) {
      if (events[i].kind !== "tool_result") continue;
      let prev: StreamEvent | null = null;
      for (let j = i - 1; j >= 0; j--) {
        if (events[j].kind === "agent_step") {
          prev = events[j];
          break;
        }
      }
      const tsMs = Date.parse(events[i].ts);
      const prevTsMs = prev ? Date.parse(prev.ts) : NaN;
      const prevArgs = prev?.payload.args;
      map.set(events[i].seq, {
        args:
          typeof prevArgs === "object" && prevArgs !== null
            ? (prevArgs as Record<string, unknown>)
            : {},
        durationMs:
          prev && Number.isFinite(tsMs) && Number.isFinite(prevTsMs) && tsMs >= prevTsMs
            ? tsMs - prevTsMs
            : null,
      });
    }
    return map;
  }, [events]);

  /** 「全展开/全折叠」只作用于可折叠卡（思考 + 工具结果）。 */
  const foldableSeqs = useMemo(
    () => events.filter((e) => e.kind === "agent_step" || e.kind === "tool_result").map((e) => e.seq),
    [events],
  );

  /** 409（已被答）：刷新 pending 现值——还挂着（换了新问题）→ 重弹；没了 → 切已答。 */
  const refreshAfterConflict = () => {
    fetchPending(projectId)
      .then((resp) => {
        if (resp.pending) {
          setPending(pendingFrom(resp));
          setDialogOpen(true);
        } else {
          setPending((prev) => (prev ? answeredOf(prev, { labels: null }) : prev));
          setDialogOpen(false);
        }
      })
      .catch(() => {
        setPending((prev) => (prev ? answeredOf(prev, { labels: null }) : prev));
        setDialogOpen(false);
      });
  };

  return (
    <div className="view view-chat">
      <div className="chat-toolbar">
        <button
          type="button"
          className="btn-ghost"
          onClick={() => expandAll(foldableSeqs)}
          disabled={foldableSeqs.length === 0}
        >
          全展开
        </button>
        <button type="button" className="btn-ghost" onClick={collapseAll}>
          全折叠
        </button>
        {hello && (
          <span className="muted stream-hello">
            已连接 · {hello.project} · 缓冲 {events.length} 条
          </span>
        )}
        {/* 断流可见（Task 7 ④ / Task 10 ③）：断连/首连中时给状态提示，不静默。 */}
        {connected === false && (
          <span className="stream-offline" role="status">
            ⚠ 离线，重连中…（新事件暂时收不到）
          </span>
        )}
        {connected === null && (
          <span className="muted stream-hello">连接中…</span>
        )}
      </div>
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        {chatItems.length === 0 && (
          <p className="muted">（暂无消息——在下方输入任务，员工会在这里逐步汇报进展）</p>
        )}
        {chatItems.map((item) => {
          if (item.type === "user") {
            return (
              <div className="chat-row user" key={item.key}>
                <div className="chat-bubble user">
                  <div className="chat-raw">{item.raw}</div>
                  {item.deliverables.length > 0 && (
                    <div className="chat-deliverables">交付物：{item.deliverables.join(" / ")}</div>
                  )}
                </div>
              </div>
            );
          }
          if (item.type === "assistant") {
            const seq = item.event.seq;
            return (
              <div className="chat-row assistant" key={item.key}>
                <div className="chat-bubble assistant">
                  <ThoughtCard
                    seq={seq}
                    payload={item.event.payload}
                    expanded={expanded.has(seq)}
                    onToggle={() => toggle(seq)}
                  />
                  {item.tools.map((t) => (
                    <ToolResultCard
                      key={t.seq}
                      seq={t.seq}
                      tool={str(t.payload.tool, "?")}
                      observation={str(t.payload.observation)}
                      args={stepContext.get(t.seq)?.args ?? {}}
                      durationMs={stepContext.get(t.seq)?.durationMs ?? null}
                      expanded={expanded.has(t.seq)}
                      onToggle={() => toggle(t.seq)}
                    />
                  ))}
                </div>
              </div>
            );
          }
          return (
            <div className="chat-system" key={item.key}>
              <SystemItem
                item={item.item}
                expanded={expanded}
                onToggle={toggle}
                toolCtx={stepContext}
                targetByChapter={targetByChapter}
              />
            </div>
          );
        })}
        {/* §4.5.1 审批两种形态：未答 → 琥珀占位（可点重开弹窗）；已答 → 一行嵌入。 */}
        {pending && !pending.answered && (
          <button
            type="button"
            className="approval-pending-line"
            onClick={() => setDialogOpen(true)}
            title="点击作答"
          >
            ⏳ 待审批 · {truncate(pending.data.question, 80)} · 点击作答
          </button>
        )}
        {pending && pending.answered && (
          <ApprovalInline
            tone={pending.rejected ? "rejected" : "ok"}
            label={pending.labels ? `已选 ${pending.labels.join("、")}` : null}
            note={pending.note}
          />
        )}
      </div>
      <TaskBar />
      {/* 弹窗盖主区；「稍后再说」只关弹窗，琥珀占位留着。 */}
      {/* key=question：换问题时强制重挂载，清空上一次的 selected/note，防静默错答 */}
      {dialogOpen && pending && !pending.answered && (
        <ApprovalDialog
          key={pending.data.question}
          projectId={projectId}
          pending={pending.data}
          onClose={() => setDialogOpen(false)}
          onAnswered={(labels, note) => {
            setPending((prev) => (prev ? answeredOf(prev, { labels, note }) : prev));
            setDialogOpen(false);
          }}
          onConflict={refreshAfterConflict}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 系统项渲染（原 20 种 kind 映射，§4.4.1）

function SystemItem({
  item,
  expanded,
  onToggle,
  toolCtx,
  targetByChapter,
}: {
  item: DisplayItem;
  expanded: Set<number>;
  onToggle: (seq: number) => void;
  toolCtx: Map<number, { args: Record<string, unknown>; durationMs: number | null }>;
  targetByChapter: Map<number, number>;
}) {
  if (item.type === "draft") {
    return (
      <DraftProgress
        chapterSeq={item.group.chapterSeq}
        phase={item.group.phase}
        beat={item.group.beat}
        chars={item.group.chars}
        targetWords={targetByChapter.get(item.group.chapterSeq) ?? null}
      />
    );
  }
  return (
    <EventCard
      event={item.event}
      expanded={expanded.has(item.event.seq)}
      onToggle={() => onToggle(item.event.seq)}
      toolCtx={toolCtx.get(item.event.seq)}
    />
  );
}

// ---------------------------------------------------------------- 卡片映射（§4.4.1，20 种 kind）
// 对话式布局下，agent_step / tool_result / run_started 由 ChatItem 分组逻辑消费，
// 这里保留其余 kind 的渲染（含防御性兜底——分组遗漏时也能渲染）。

function EventCard({
  event,
  expanded,
  onToggle,
  toolCtx,
}: {
  event: StreamEvent;
  expanded: boolean;
  onToggle: () => void;
  toolCtx?: { args: Record<string, unknown>; durationMs: number | null };
}) {
  const { seq, kind, payload } = event;

  switch (kind) {
    // 思考卡 + say 行（assistant 回合内已由 ChatItem 直接渲染；此为兜底）
    case "agent_step":
      return (
        <ThoughtCard seq={seq} payload={payload} expanded={expanded} onToggle={onToggle} />
      );

    // 工具结果卡（写作闸硬拦 → 琥珀大卡片 GateNotice，§4.7）
    case "tool_result": {
      const observation = str(payload.observation);
      if (isGateObservation(observation)) {
        return <GateNotice observation={observation} seq={seq} />;
      }
      return (
        <ToolResultCard
          seq={seq}
          tool={str(payload.tool, "?")}
          observation={observation}
          args={toolCtx?.args ?? {}}
          durationMs={toolCtx?.durationMs ?? null}
          expanded={expanded}
          onToggle={onToggle}
        />
      );
    }

    // 顶部横幅（一次 run 一条；对话式下 run_started 由用户气泡渲染，此为兜底）
    case "run_started":
      return (
        <div className="stream-banner banner-start">
          ▶ run 开始 · 交付物：{formatList(payload.deliverables)}
          <span className="banner-raw muted" title={str(payload.raw)}>
            {truncate(str(payload.raw), 120)}
          </span>
        </div>
      );

    // 章节卡头
    case "chapter_start":
      return (
        <div className="chapter-head">
          ✎ 第 {num(payload.seq)} 章「{str(payload.title)}」开始 · 目标 {num(payload.target_words)} 字
          <span className="muted"> · seq {seq}</span>
        </div>
      );

    // 章节卡尾（chapter_revised 载荷无 title/memory_status——按需渲染）
    case "chapter_done":
    case "chapter_revised": {
      const revised = kind === "chapter_revised";
      const title = str(payload.title);
      return (
        <div className={revised ? "chapter-foot chapter-revised" : "chapter-foot"}>
          {revised ? "↻ 修正完成" : "✓ 完成"} · 第 {num(payload.seq)} 章
          {title && <>「{title}」</>}
          {" · "}
          {num(payload.words)} 字 · 审核 {bool(payload.pass_review) ? "通过" : "未过"} · AI味{" "}
          {num(payload.flavor_score, 0).toFixed(1)}
          {revised && <> · {num(payload.revise_count)} 轮</>}
          {"memory_status" in payload && (
            <span className="muted">
              {" "}· 记忆 {str(payload.memory_status)}
            </span>
          )}
          <span className="muted"> · seq {seq}</span>
        </div>
      );
    }

    // 验收小结卡
    case "check_report":
      return (
        <div className="stream-card">
          <div className="stream-card-title">
            验收小结 · verdict {str(payload.verdict) || "—"} · 得分 {num(payload.score)}
            <span className="muted"> · seq {seq}</span>
          </div>
          <div className="kv">
            <span className="kv-key">意见 / 丢弃</span>
            <span className="kv-value">
              {num(payload.issues)} 条 / {num(payload.discarded)} 条
            </span>
          </div>
          {"checklist_failed" in payload && (
            <div className="kv">
              <span className="kv-key">清单未过</span>
              <span className="kv-value">
                {num(payload.checklist_failed)} / {num(payload.checklist)}
              </span>
            </div>
          )}
        </div>
      );

    // 审计小结卡
    case "audit_done":
      return (
        <div className="stream-card">
          <div className="stream-card-title">
            审计小结<span className="muted"> · seq {seq}</span>
          </div>
          <div className="kv">
            <span className="kv-key">确定 / LLM 复核 / 丢弃</span>
            <span className="kv-value">
              {num(payload.deterministic)} / {num(payload.llm)} / {num(payload.discarded)}
            </span>
          </div>
        </div>
      );

    // 已答审批（嵌在流里）
    case "user_choice":
      return (
        <div className="stream-line line-answer">
          ✓ 已选 {str(payload.picked) || "—"} · {truncate(str(payload.question), 60)}
          {str(payload.note) && <span className="muted">（备注：{truncate(str(payload.note), 40)}）</span>}
        </div>
      );
    case "plan_approved":
      return (
        <div className="stream-line line-answer">
          ✓ 规划已确认 · {truncate(str(payload.question), 60)}
        </div>
      );
    case "plan_rejected":
      return (
        <div className="stream-line line-rejected">
          ✗ 规划被驳回 · {truncate(str(payload.question), 60)}
        </div>
      );

    // 状态横幅
    case "paused":
      return <StatusBanner tone="pause" icon="⏸" label="已暂停" detail={detailText(payload)} seq={seq} />;
    case "done":
      return <StatusBanner tone="done" icon="■" label="run 完成" detail={str(payload.summary)} seq={seq} />;
    case "aborted":
      return <StatusBanner tone="abort" icon="✗" label="已终止" detail={str(payload.reason)} seq={seq} />;
    case "budget_exceeded":
      return (
        <StatusBanner
          tone="abort"
          icon="⚠"
          label="预算超限"
          detail={`已用 ${num(payload.token_used)} / 预算 ${num(payload.budget)} tokens`}
          seq={seq}
        />
      );

    // 细行类
    case "plan_arc":
      return (
        <div className="stream-line">
          📐 卷 {num(payload.vol)}「{str(payload.title)}」规划完成 · {num(payload.done)}/{num(payload.total)} 卷
          {Array.isArray(payload.plots) && (
            <span className="muted">（{payload.plots.length} 个情节）</span>
          )}
        </div>
      );
    case "planning_progress":
      return (
        <div className="stream-line">
          ▸ {str(payload.stage, "planning")} · 卷 {num(payload.vol)} · 进度 {num(payload.done)}/{num(payload.total)}
          <span className="muted">
            （成功 {num(payload.ok)} / 失败 {num(payload.failed)}）
          </span>
        </div>
      );
    case "chapters_planned":
      return (
        <div className="stream-line">
          ▤ 情节 {str(payload.plot_id)}「{truncate(str(payload.plot_title), 24)}」·{" "}
          {chaptersPlannedLabel(payload)}
        </div>
      );
    case "real_ref_checked":
      return (
        <div className="stream-line">
          ⌗ 现实参照核查 · {num(payload.count)} 条结论
        </div>
      );

    // 未知 kind（附录 E 的未来事件）：朴素细行兜底，不崩。
    default:
      return (
        <div className="stream-line muted">
          ? {kind} · seq {seq} · {truncate(JSON.stringify(payload), 100)}
        </div>
      );
  }
}

function StatusBanner({
  tone,
  icon,
  label,
  detail,
  seq,
}: {
  tone: "pause" | "done" | "abort";
  icon: string;
  label: string;
  detail: string;
  seq: number;
}) {
  return (
    <div className={`stream-banner banner-${tone}`}>
      {icon} {label}
      {detail && <span className="banner-raw"> · {truncate(detail, 140)}</span>}
      <span className="muted"> · seq {seq}</span>
    </div>
  );
}

// ---------------------------------------------------------------- 小工具

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function formatList(value: unknown): string {
  if (Array.isArray(value)) {
    return value.filter((v): v is string => typeof v === "string").join(", ") || "—";
  }
  return typeof value === "string" ? value : "—";
}

function detailText(payload: Record<string, unknown>): string {
  const reason = str(payload.reason);
  const detail = str(payload.detail);
  return detail ? `${reason} · ${detail}` : reason;
}

function chaptersPlannedLabel(payload: Record<string, unknown>): string {
  switch (str(payload.phase)) {
    case "units":
      return `${num(payload.units)} 个剧情单元`;
    case "beats":
      return `章规划完成 ${num(payload.chapters)} 章（成功 ${num(payload.ok)}）`;
    case "selfcheck":
      return `自查：问题 ${num(payload.issues)} 处，修正 ${num(payload.fixed)} 条`;
    default:
      return str(payload.phase) || "?";
  }
}
