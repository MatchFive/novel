/// 侧栏（spec §4.2）：236px；视图导航 + 计数 + 书架 + 运行状态区。
/// 运行状态区按 /state 的 running（bool）与 status 显隐 pause/resume/abort 三按钮；
/// status 为 null（无 run）时三按钮全 disabled，不报错。

import { useState } from "react";
import { apiPost } from "../api/client";
import type { ProjectRef } from "../api/types";
import { useSession } from "../state/session";
import type { ViewKey } from "../views";

interface NavItem {
  key: ViewKey;
  label: string;
  /** 窄屏图标轨（§4.1.1）用的图标；宽屏模式下隐藏。 */
  icon: string;
  count: (c: import("../state/session").Counts) => number;
}

const NAV: NavItem[] = [
  { key: "events", label: "对话", icon: "💬", count: (c) => c.events_last_seq },
  { key: "outline", label: "大纲", icon: "🧭", count: (c) => c.volumes },
  { key: "chapters", label: "章节", icon: "📑", count: (c) => c.chapters },
  { key: "prose", label: "正文", icon: "📖", count: (c) => c.prose },
  { key: "characters", label: "角色", icon: "👤", count: (c) => c.characters_pending },
  { key: "memory", label: "记忆", icon: "🧠", count: (c) => c.memory },
  { key: "issues", label: "问题", icon: "🛠", count: (c) => c.issues_pending },
];

const STATUS_LABEL: Record<string, string> = {
  planning: "规划中",
  writing: "写作中",
  paused: "已暂停",
  done: "已完成",
  finished: "已完成",
  aborted: "已终止",
  failed: "失败",
};

interface SidebarProps {
  view: ViewKey;
  onNavigate: (view: ViewKey) => void;
  projects: ProjectRef[];
  onSelectProject: (id: string) => void;
  running: boolean;
  status: string | null;
  onStateChange: (running: boolean, status: string | null) => void;
}

export function Sidebar({ view, onNavigate, projects, onSelectProject, running, status, onStateChange }: SidebarProps) {
  const { projectId, projectTitle, counts } = useSession();
  const [commandError, setCommandError] = useState<string | null>(null);

  async function command(name: "pause" | "resume" | "abort") {
    setCommandError(null);
    try {
      const resp = await apiPost<{ status: string | null }>(
        `/api/project/${encodeURIComponent(projectId)}/commands/${name}`,
      );
      // 后端如实回当前状态；null（无 run）→ 视为按钮回到全 disabled，不算错误。
      // abort 后驱动任务结束 → running=false；pause/resume 任务仍活着 → running 不变。
      onStateChange(name === "abort" ? false : running, resp.status);
    } catch (err) {
      setCommandError(err instanceof Error ? err.message : String(err));
    }
  }

  const paused = status === "paused";

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">novel-harness</div>

      <nav className="sidebar-nav">
        {NAV.map((item) => (
          <button
            key={item.key}
            className={item.key === view ? "nav-item active" : "nav-item"}
            title={item.label}
            onClick={() => onNavigate(item.key)}
          >
            <span className="nav-icon" aria-hidden="true">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
            <span className="nav-count">{counts ? item.count(counts) : "–"}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-run">
        <div className="run-status">
          <span className={running ? "dot running" : "dot"} />
          <span className="run-status-text">{status ? (STATUS_LABEL[status] ?? status) : "无 run"}</span>
        </div>
        <div className="run-actions">
          <button disabled={!running || paused} title="暂停" onClick={() => void command("pause")}>暂停</button>
          <button disabled={!paused} title="继续" onClick={() => void command("resume")}>继续</button>
          <button disabled={!running} title="终止" onClick={() => void command("abort")}>终止</button>
        </div>
        {commandError && <p className="run-error">{commandError}</p>}
      </div>

      <div className="sidebar-books">
        <div className="books-title">书架</div>
        {projects.length === 0 && <div className="books-empty">（空）</div>}
        <ul className="books-list">
          {projects.map((p) => (
            <li key={p.id}>
              <button
                className={p.id === projectId ? "book-item active" : "book-item"}
                title={p.title}
                onClick={() => onSelectProject(p.id)}
              >
                {p.title}
              </button>
            </li>
          ))}
        </ul>
        <div className="books-current" title={projectId}>{projectTitle}</div>
      </div>
    </aside>
  );
}
