/// 写作进度条（§4.4.4）：draft_progress 按 (章 seq, phase) 聚合后的一条进度行。
/// 相位换标签 + 换颜色；宽度用 chars / chapter_start 的 target_words 估算（纯展示）。

export type DraftPhase = "draft" | "revising" | "deaify" | string;

const PHASE_STYLE: Record<string, { label: string; className: string }> = {
  draft: { label: "草稿", className: "phase-draft" },
  revising: { label: "修正", className: "phase-revising" },
  deaify: { label: "去味", className: "phase-deaify" },
};

export interface DraftProgressProps {
  chapterSeq: number;
  phase: DraftPhase;
  beat: number;
  chars: number;
  /** chapter_start 的目标字数；未知 → null（进度条不显示百分比）。 */
  targetWords: number | null;
}

export function DraftProgress({
  chapterSeq,
  phase,
  beat,
  chars,
  targetWords,
}: DraftProgressProps) {
  const style = PHASE_STYLE[phase] ?? { label: phase || "?", className: "phase-unknown" };
  const pct = targetWords && targetWords > 0
    ? Math.min(100, Math.round((chars / targetWords) * 100))
    : null;

  return (
    <div className="draft-progress">
      <span className={style.className}>ch {chapterSeq}</span>
      <span className="draft-label">
        {style.label} · beat {beat} · {chars} 字
      </span>
      <span className="progress-track">
        <span
          className={`progress-fill ${style.className}`}
          style={{ width: pct === null ? "4%" : `${Math.max(pct, 2)}%` }}
        />
      </span>
      {pct !== null && <span className="muted draft-pct">{pct}%</span>}
    </div>
  );
}
