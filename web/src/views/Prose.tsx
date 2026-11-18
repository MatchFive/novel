/// 正文视图：章节树两栏 + 阅读区（限宽 720px / 衬线 / 行高 1.95~2.1）。
/// 左栏列章节（done 优先可选），右栏渲染单章正文（/chapters/{seq}）。
/// 正文编辑（Task 13）：顶栏「⌨ 编辑」进编辑态（textarea 初值 body）→
/// 保存 PUT /chapters/{seq}（后端只改正文段，doc.path 写盘）→ 退出编辑态刷新；
/// 取消退出不保存。切换章节自动退出编辑态（draft 属于当前章）。
/// 跨视图引文跳转（Task 15，§4.8）：pendingJump 取走后本地持有——body.indexOf(quote)
/// 定位段落包黄底 span + 角标「← 问题台账 #NNNN」；顶部黄条说明来源；
/// 点角标返回问题台账；quote 因编辑漂移找不到时黄条提示「原文未找到」，不崩。

import { useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPut } from "../api/client";
import type { ChapterDetail, ChapterSaveResponse, ChaptersResponse } from "../api/types";
import { useSession } from "../state/session";
import { useNavigation } from "../state/navigation";
import type { ProseJump } from "../state/navigation";

/** 高亮定位结果：quote 命中某段落（段落内相对区间）或未找到。 */
type QuoteHit =
  | { found: true; paraIndex: number; start: number; end: number }
  | { found: false };

/** 在正文中定位 quote 所在段落与段内区间（段落切分口径与渲染一致：/\r?\n/）。 */
function locateQuote(body: string, quote: string): QuoteHit {
  const bodyIdx = body.indexOf(quote);
  if (bodyIdx < 0) return { found: false };
  const paras = body.split(/\r?\n/);
  let offset = 0;
  for (let i = 0; i < paras.length; i++) {
    const start = offset;
    const end = offset + paras[i].length;
    if (bodyIdx >= start && bodyIdx < end) {
      return { found: true, paraIndex: i, start: bodyIdx - start, end: bodyIdx - start + quote.length };
    }
    offset = end;
    // 跳过分隔符：\r\n 两字符、\n 一字符；文末无分隔符。
    if (offset < body.length) offset += body[offset] === "\r" ? 2 : 1;
  }
  return { found: false };
}

export function Prose() {
  const { projectId, refreshCounts } = useSession();
  const { pendingJump, consumeJump, navigate } = useNavigation();
  const [chapters, setChapters] = useState<ChaptersResponse["chapters"]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<ChapterDetail | null>(null);
  const [detailVersion, setDetailVersion] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 重试计数：错误态「重试」按钮 +1，驱动两个加载 effect 重跑（Task 20 错误态）。
  const [reloadTick, setReloadTick] = useState(0);
  // 跳转状态本地持有：pendingJump 一经取走即 consume，避免重渲染反复触发。
  const [jump, setJump] = useState<ProseJump | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  // 取走 App 层的 pendingJump（§4.8）。
  useEffect(() => {
    if (pendingJump) {
      setJump(pendingJump);
      consumeJump();
    }
  }, [pendingJump, consumeJump]);

  // 跳转目标章不是当前章 → 切过去（章节列表加载完后才能选）。
  useEffect(() => {
    if (jump && selected !== jump.seq && chapters.some((c) => c.seq === jump.seq)) {
      setSelected(jump.seq);
    }
  }, [jump, selected, chapters]);

  useEffect(() => {
    setChapters([]);
    setSelected(null);
    setDetail(null);
    setError(null);
    apiGet<ChaptersResponse>(`/api/project/${encodeURIComponent(projectId)}/chapters`)
      .then((resp) => {
        setChapters(resp.chapters);
        const firstDone = resp.chapters.find((c) => c.status === "done") ?? resp.chapters[0];
        if (firstDone) setSelected(firstDone.seq);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId, reloadTick]);

  useEffect(() => {
    if (selected === null) return;
    setDetail(null);
    setEditing(false);   // draft 属于当前章：换章即退出编辑态（不保存）
    setSaveError(null);
    apiGet<ChapterDetail>(
      `/api/project/${encodeURIComponent(projectId)}/chapters/${selected}`,
    )
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId, selected, detailVersion, reloadTick]);

  // 引文高亮定位（quote 漂移找不到 → found:false，黄条提示，不崩）。
  const hit = useMemo<QuoteHit | null>(() => {
    if (!detail || !jump) return null;
    return locateQuote(detail.body, jump.quote);
  }, [detail, jump]);

  // 命中后滚动定位到高亮段。
  useEffect(() => {
    if (!hit?.found) return;
    const el = bodyRef.current?.querySelector(".quote-hit");
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [hit]);

  function enterEdit() {
    if (!detail) return;
    setDraft(detail.body);
    setSaveError(null);
    setEditing(true);
  }

  function cancelEdit() {
    if (saving) return;
    setEditing(false);
    setSaveError(null);
  }

  async function save() {
    if (selected === null || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await apiPut<ChapterSaveResponse>(
        `/api/project/${encodeURIComponent(projectId)}/chapters/${selected}`,
        { body: draft },
      );
      setEditing(false);
      setDetailVersion((v) => v + 1);  // 重取本章 detail（正文/字数已变）
      void refreshCounts();  // sidebar 的 prose 计数 = status 为 done 的章数（app.py _counts），非 word_count
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  if (error) {
    return (
      <div className="view">
        <p className="error-text">加载失败：{error}</p>
        <button type="button" className="btn-ghost" onClick={() => {
          setError(null);
          setReloadTick((t) => t + 1);
        }}>
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="view prose-view">
      <aside className="chapter-tree">
        <h2>正文</h2>
        {chapters.length === 0 && (
          <p className="muted empty-hint">（还没有章节——起个 run 交付写作任务，写成的章会出现在这里）</p>
        )}
        <ul>
          {chapters.map((c) => (
            <li key={c.seq}>
              <button
                className={c.seq === selected ? "chapter-item active" : "chapter-item"}
                onClick={() => {
                  setSelected(c.seq);
                  setJump(null);  // Task 15 ④：手动切章即弃跳转态，防「原文未找到」滞留误导
                }}
              >
                <span className="chapter-seq">{c.seq}</span>
                <span className="chapter-title">{c.title ?? "（无标题）"}</span>
                <span className={`badge status-${c.status ?? "none"}`}>{c.status ?? "?"}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <article className="reading-pane">
        {selected === null && <p className="muted">从左侧选择一章开始阅读。</p>}
        {selected !== null && !detail && <p className="muted">加载中…</p>}
        {detail && (
          <>
            {jump && (
              <div className="jump-banner">
                <span>
                  来自问题台账 #{jump.issueId.padStart(4, "0")} 的引文定位
                  {hit && !hit.found && "：原文未找到（可能已被编辑改动）"}
                </span>
                <button type="button" className="btn-ghost" onClick={() => setJump(null)}>
                  ×
                </button>
              </div>
            )}
            <div className="reading-toolbar">
              {!editing && (
                <button className="btn-ghost" onClick={enterEdit}>⌨ 编辑</button>
              )}
            </div>
            <h2 className="reading-title">
              第 {detail.seq} 章 · {String(detail.meta.title ?? "（无标题）")}
            </h2>
            {editing ? (
              <div className="reading-edit">
                <textarea
                  className="edit-textarea"
                  rows={18}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
                <div className="reading-edit-actions">
                  <button
                    className="btn-primary"
                    disabled={saving}
                    onClick={() => void save()}
                  >
                    {saving ? "保存中…" : "保存"}
                  </button>
                  <button className="btn-ghost" disabled={saving} onClick={cancelEdit}>
                    取消
                  </button>
                  {saveError && <span className="error-text">保存失败：{saveError}</span>}
                </div>
              </div>
            ) : (
              <div className="reading-body" ref={bodyRef}>
                {detail.body.split(/\r?\n/).map((para, i) => {
                  if (!para.trim()) return null;
                  const h = hit?.found && hit.paraIndex === i ? hit : null;
                  if (!h) return <p key={i}>{para}</p>;
                  const issueNo = jump?.issueId.padStart(4, "0") ?? "";
                  return (
                    <p key={i} className="quote-hit-wrap">
                      {para.slice(0, h.start)}
                      <span className="quote-hit">{para.slice(h.start, h.end)}</span>
                      {para.slice(h.end)}
                      <button
                        type="button"
                        className="jump-badge"
                        title="返回问题台账"
                        onClick={() => {
                          setJump(null);
                          navigate("issues");
                        }}
                      >
                        ← 问题台账 #{issueNo}
                      </button>
                    </p>
                  );
                })}
              </div>
            )}
          </>
        )}
      </article>
    </div>
  );
}
