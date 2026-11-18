/// 角色视图：计数顶栏（carded/pending/walk_on）+ 主角 / 候选 / 龙套三段。
/// Task 15：待建卡 member → 「起草确认卡」打开 CharacterDraftDialog（§4.6.2）。

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../api/client";
import type { CastMember, CastMemberWithCard, CharactersResponse } from "../api/types";
import { CharacterDraftDialog } from "../components/CharacterDraftDialog";
import { ViewError } from "../components/ViewError";
import { useSession } from "../state/session";

export function Characters() {
  const { projectId } = useSession();
  const [data, setData] = useState<CharactersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafting, setDrafting] = useState<CastMember | null>(null);

  const reload = useCallback(() => {
    setData(null);
    setError(null);
    apiGet<CharactersResponse>(`/api/project/${encodeURIComponent(projectId)}/characters`)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (error) return <ViewError message={error} onRetry={reload} />;
  if (!data) return <div className="view"><p className="muted">加载中…</p></div>;

  const totallyEmpty =
    data.protagonists.length === 0 && data.candidates.length === 0 && data.walk_ons.length === 0;

  return (
    <div className="view">
      <h2>角色</h2>
      <div className="count-bar">
        <span className="count-chip">有卡 {data.counts.carded}</span>
        <span className="count-chip">待建卡 {data.counts.pending}</span>
        <span className="count-chip">龙套 {data.counts.walk_on}</span>
      </div>
      {totallyEmpty && (
        <p className="muted empty-hint">
          （还没有角色——写作 run 跑过若干章后，系统会从正文戏份里统计出主角 / 候选 / 龙套）
        </p>
      )}

      <section>
        <h3>主角（{data.protagonists.length}）</h3>
        {data.protagonists.length === 0 && <p className="muted">（暂无主角）</p>}
        <div className="card-grid">
          {data.protagonists.map((m) => <MemberCard key={m.name} member={m} onDraft={() => setDrafting(m)} />)}
        </div>
      </section>

      <section>
        <h3>候选（{data.candidates.length}）</h3>
        {data.candidates.length === 0 && <p className="muted">（暂无候选）</p>}
        <div className="card-grid">
          {data.candidates.map((m) => <MemberCard key={m.name} member={m} onDraft={() => setDrafting(m)} />)}
        </div>
      </section>

      <section>
        <h3>龙套（{data.walk_ons.length}）</h3>
        {data.walk_ons.length === 0 && <p className="muted">（暂无龙套）</p>}
        <table className="data-table">
          <thead>
            <tr><th>名字</th><th>units</th><th>章数</th><th>首现章</th><th>存在感</th></tr>
          </thead>
          <tbody>
            {data.walk_ons.map((m) => (
              <tr key={m.name}><MemberCells member={m} /></tr>
            ))}
          </tbody>
        </table>
      </section>
      {drafting && (
        <CharacterDraftDialog
          projectId={projectId}
          member={drafting}
          onClose={() => setDrafting(null)}
          onSaved={reload}
        />
      )}
    </div>
  );
}

function MemberCells({ member }: { member: CastMember }) {
  return (
    <>
      <td>{member.name}</td>
      <td>{member.beats}</td>
      <td>{member.chapters}</td>
      <td>{member.first_chapter}</td>
      <td>{(member.share * 100).toFixed(1)}%</td>
    </>
  );
}

function MemberCard({ member, onDraft }: { member: CastMemberWithCard; onDraft: () => void }) {
  return (
    <div className="card member-card">
      <div className="member-head">
        <strong>{member.name}</strong>
        <span className={member.card ? "badge status-done" : "badge status-pending"}>
          {member.card ? "有卡" : "待建卡"}
        </span>
      </div>
      <div className="kv"><span className="kv-key">units</span><span className="kv-value">{member.beats}</span></div>
      <div className="kv"><span className="kv-key">章数</span><span className="kv-value">{member.chapters}</span></div>
      <div className="kv"><span className="kv-key">首现章</span><span className="kv-value">{member.first_chapter}</span></div>
      <div className="kv"><span className="kv-key">存在感</span><span className="kv-value">{(member.share * 100).toFixed(1)}%</span></div>
      {!member.card && (
        <div className="member-actions">
          <button type="button" className="btn-ghost" onClick={onDraft}>
            起草确认卡
          </button>
        </div>
      )}
      {member.card && (
        <details className="card-detail">
          <summary>角色卡</summary>
          {Object.entries(member.card).map(([k, v]) => (
            <div key={k} className="kv">
              <span className="kv-key">{k}</span>
              <span className="kv-value">{typeof v === "string" ? v : JSON.stringify(v)}</span>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}
