/// 应用骨架：书架选择 → 侧栏 + 七视图；视图切换 useState<ViewKey> + location.hash（Q6）。
/// Task 15：navigate(view, jump?) 支持跨视图引文跳转（pendingJump 由 Prose 消费，§4.8）。

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, ApiError } from "./api/client";
import type { ProjectRef, StateResponse } from "./api/types";
import { SessionContext } from "./state/session";
import type { Counts } from "./state/session";
import { NavigationContext } from "./state/navigation";
import type { ProseJump } from "./state/navigation";
import { Sidebar } from "./components/Sidebar";
import { VIEWS, viewFromHash } from "./views";
import type { ViewKey } from "./views";

const PROJECT_STORAGE_KEY = "novel-harness.project";
const POLL_MS = 2000;

export default function App() {
  const [projects, setProjects] = useState<ProjectRef[]>([]);
  const [projectId, setProjectId] = useState<string | null>(
    () => localStorage.getItem(PROJECT_STORAGE_KEY),
  );
  const [view, setView] = useState<ViewKey>(() => viewFromHash(window.location.hash));
  const [counts, setCounts] = useState<Counts | null>(null);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [bookshelfHint, setBookshelfHint] = useState<string | null>(null);
  const [bookshelfError, setBookshelfError] = useState<string | null>(null);
  const [pendingJump, setPendingJump] = useState<ProseJump | null>(null);

  // 视图切换 = useState + hash；刷新回原视图。
  useEffect(() => {
    const onHash = () => setView(viewFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((next: ViewKey, jump?: ProseJump) => {
    if (jump && next === "prose") setPendingJump(jump);
    window.location.hash = next;
    setView(next);
  }, []);

  const consumeJump = useCallback(() => setPendingJump(null), []);

  const navigation = useMemo(
    () => ({ navigate, pendingJump, consumeJump }),
    [navigate, pendingJump, consumeJump],
  );

  const loadProjects = useCallback(async () => {
    const resp = await apiGet<{ projects: ProjectRef[] }>("/api/projects");
    setProjects(resp.projects);
    setBookshelfError(null);
    return resp.projects;
  }, []);

  /** 多项目切换（Task 20）：换书即重置——视图回事件流、清跳转态；流/展开集由
   *  EventStream 的 projectId effect 重置，计数/运行态由 /state 轮询 effect 重置。 */
  const selectProject = useCallback((id: string) => {
    setPendingJump(null);
    setView("events");
    window.location.hash = "events";
    setProjectId(id);
  }, []);

  // 首次加载书架；选中项若已不存在则回退到第一本。
  useEffect(() => {
    void loadProjects().then((list) => {
      if (list.length === 0) {
        setProjectId(null);
        return;
      }
      if (!projectId || !list.some((p) => p.id === projectId)) {
        setProjectId(list[0].id);
      }
    }).catch((err) => {
      // 书架都拉不下来：错误态不白屏——文案 + 重试（下方 Bookshelf 渲染重试按钮）。
      setBookshelfError(err instanceof Error ? err.message : String(err));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 项目持久化。
  useEffect(() => {
    if (projectId) localStorage.setItem(PROJECT_STORAGE_KEY, projectId);
    else localStorage.removeItem(PROJECT_STORAGE_KEY);
  }, [projectId]);

  const refreshCounts = useCallback(async () => {
    if (!projectId) return;
    try {
      const state = await apiGet<StateResponse>(
        `/api/project/${encodeURIComponent(projectId)}/state`,
      );
      setCounts(state.counts);
      setRunning(state.running);
      setStatus(state.status);
    } catch {
      // counts 拉取失败不打断界面（视图自身有错误展示）。
    }
  }, [projectId]);

  // /state 轮询：驱动侧栏计数与运行状态区。
  useEffect(() => {
    if (!projectId) return;
    setCounts(null);
    setRunning(false);
    setStatus(null);
    void refreshCounts();
    const timer = window.setInterval(() => void refreshCounts(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [projectId, refreshCounts]);

  const projectTitle = useMemo(
    () => projects.find((p) => p.id === projectId)?.title ?? projectId ?? "",
    [projects, projectId],
  );

  const session = useMemo(
    () =>
      projectId === null
        ? null
        : { projectId, projectTitle, counts, refreshCounts },
    [projectId, projectTitle, counts, refreshCounts],
  );

  async function createProject(title: string) {
    setBookshelfHint(null);
    try {
      const created = await apiPost<ProjectRef>("/api/projects", { title });
      await loadProjects();
      setProjectId(created.id);
    } catch (err) {
      setBookshelfHint(
        err instanceof ApiError ? `创建失败：${err.message}` : String(err),
      );
    }
  }

  // 书架屏（未选项目 / 无项目）。
  if (!session) {
    return (
      <Bookshelf
        projects={projects}
        hint={bookshelfHint}
        loadError={bookshelfError}
        onRetryLoad={() => void loadProjects().catch((err) => {
          setBookshelfError(err instanceof Error ? err.message : String(err));
        })}
        onSelect={selectProject}
        onCreate={createProject}
      />
    );
  }

  const ActiveView = VIEWS[view];

  return (
    <SessionContext.Provider value={session}>
      <NavigationContext.Provider value={navigation}>
        <div className="layout">
          <Sidebar
            view={view}
            onNavigate={navigate}
            projects={projects}
            onSelectProject={selectProject}
            running={running}
            status={status}
            onStateChange={(r, s) => {
              setRunning(r);
              setStatus(s);
            }}
          />
          <main className="main">
            <ActiveView />
          </main>
        </div>
      </NavigationContext.Provider>
    </SessionContext.Provider>
  );
}

function Bookshelf({
  projects,
  hint,
  loadError,
  onRetryLoad,
  onSelect,
  onCreate,
}: {
  projects: ProjectRef[];
  hint: string | null;
  loadError: string | null;
  onRetryLoad: () => void;
  onSelect: (id: string) => void;
  onCreate: (title: string) => void;
}) {
  const [title, setTitle] = useState("");

  return (
    <div className="bookshelf-screen">
      <h1>novel-harness · 书架</h1>
      {loadError ? (
        <div className="view-error">
          <p className="error-text">书架加载失败：{loadError}</p>
          <button type="button" className="btn-ghost" onClick={onRetryLoad}>重试</button>
        </div>
      ) : (
        <>
          {projects.length === 0 && (
            <p className="muted empty-hint">（书架为空——在下方输入标题，创建第一本书开始）</p>
          )}
          <ul className="bookshelf-list">
            {projects.map((p) => (
              <li key={p.id}>
                <button className="book-item" onClick={() => onSelect(p.id)}>{p.title}</button>
              </li>
            ))}
          </ul>
        </>
      )}
      <form
        className="bookshelf-create"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) onCreate(title.trim());
        }}
      >
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="新书标题"
        />
        <button className="btn-primary" type="submit" disabled={!title.trim()}>
          创建
        </button>
      </form>
      {hint && <p className="error-text">{hint}</p>}
    </div>
  );
}
