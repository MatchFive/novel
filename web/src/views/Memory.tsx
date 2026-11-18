/// 记忆视图：三节——现状（characters/world 投影）、卷级 digest、章记忆。
/// 章记忆点选后渲染六维度；空维度显示诚实文案（reader 给出 empty_dimensions）。

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../api/client";
import type { ChapterMemory, MemoryResponse } from "../api/types";
import { ViewError } from "../components/ViewError";
import { useSession } from "../state/session";

const DIMENSIONS: { key: string; label: string }[] = [
  { key: "events", label: "事件" },
  { key: "character_events", label: "角色经历" },
  { key: "info_updates", label: "信息项" },
  { key: "knowledge", label: "知晓关系" },
  { key: "promise_updates", label: "承诺与伏笔" },
  { key: "state_updates", label: "状态更新" },
];

export function Memory() {
  const { projectId } = useSession();
  const [data, setData] = useState<MemoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setData(null);
    setError(null);
    apiGet<MemoryResponse>(`/api/project/${encodeURIComponent(projectId)}/memory`)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (error) return <ViewError message={error} onRetry={reload} />;
  if (!data) return <div className="view"><p className="muted">加载中…</p></div>;

  return (
    <div className="view">
      <h2>记忆</h2>

      <section>
        <h3>现状（state）</h3>
        <h4>角色</h4>
        {Object.keys(data.state.characters).length === 0 && (
          <p className="muted">（还没有角色现状）</p>
        )}
        {Object.entries(data.state.characters).map(([name, v]) => (
          <div key={name} className="kv">
            <span className="kv-key">{name}</span>
            <span className="kv-value">{JSON.stringify(v)}</span>
          </div>
        ))}
        <h4>世界（kind|key）</h4>
        {Object.keys(data.state.world).length === 0 && (
          <p className="muted">（还没有世界条目）</p>
        )}
        {Object.entries(data.state.world).map(([k, v]) => (
          <div key={k} className="kv">
            <span className="kv-key">{k}</span>
            <span className="kv-value">{JSON.stringify(v)}</span>
          </div>
        ))}
      </section>

      <section>
        <h3>卷级 digest</h3>
        {data.digests.length === 0 && <p className="muted">（还没有卷级 digest）</p>}
        <ul className="plain-list">
          {data.digests.map((d) => (
            <li key={d.vol}>第 {d.vol} 卷 — {d.path}</li>
          ))}
        </ul>
      </section>

      <section>
        <h3>章记忆（{data.chapter_memories.length}）</h3>
        {data.chapter_memories.length === 0 && <p className="muted">（还没有章记忆）</p>}
        <ChapterMemoryList seqs={data.chapter_memories} />
      </section>
    </div>
  );
}

function ChapterMemoryList({ seqs }: { seqs: number[] }) {
  const { projectId } = useSession();
  const [selected, setSelected] = useState<number | null>(seqs[0] ?? null);
  const [detail, setDetail] = useState<ChapterMemory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setSelected(seqs[0] ?? null), [seqs]);

  useEffect(() => {
    if (selected === null) return;
    setDetail(null);
    setError(null);
    apiGet<ChapterMemory>(
      `/api/project/${encodeURIComponent(projectId)}/memory/${selected}`,
    )
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [projectId, selected]);

  return (
    <div className="memory-split">
      <ul className="plain-list memory-seq-list">
        {seqs.map((seq) => (
          <li key={seq}>
            <button
              className={seq === selected ? "chapter-item active" : "chapter-item"}
              onClick={() => setSelected(seq)}
            >
              第 {seq} 章
            </button>
          </li>
        ))}
      </ul>
      <div className="memory-detail">
        {error && <p className="error-text">加载失败：{error}</p>}
        {!detail && !error && selected !== null && <p className="muted">加载中…</p>}
        {detail && (
          <>
            {DIMENSIONS.map(({ key, label }) => {
              const value = detail[key];
              const isEmpty = detail.empty_dimensions.includes(label);
              return (
                <div key={key} className="memory-dimension">
                  <h4>{label}</h4>
                  {isEmpty ? (
                    <p className="muted">
                      （空——本章没有产生「{label}」类记录，可能因本章无相关事件/角色/信息变化）
                    </p>
                  ) : (
                    <pre className="dimension-json">{JSON.stringify(value, null, 2)}</pre>
                  )}
                </div>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}
