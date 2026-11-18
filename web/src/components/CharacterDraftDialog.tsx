/// 角色起草确认卡（§4.6.2）：字段表（name/role_type/aliases/profile 六键/arc.stages）
/// 左标签右可编辑；底部「起草依据」（member 的 beats/chapters/first_chapter）。
/// 流程：「agent 起草」→ POST /characters/draft → 回填可改 → 「保存并落卡」→ PUT /characters/{name}。
/// role_type 词表 = protagonist/major/supporting/daily（Q8）；profile 必须嵌套 dict（六键，§4.6.2）。
/// 端点 W5 才落地（Task 17/18）：404/405 → dialog 内提示「端点未就绪（W5 落地）」，不崩、可继续手动编辑。

import { useState } from "react";
import { apiPost, apiPut, ApiError } from "../api/client";
import type { CastMember } from "../api/types";

const ROLE_TYPES = ["protagonist", "major", "supporting", "daily"] as const;
type RoleType = (typeof ROLE_TYPES)[number];

/** profile 六键（顺序即字段表行序，§4.6.2）。 */
const PROFILE_KEYS = [
  ["personality", "性格"],
  ["motivation", "动机"],
  ["background", "背景"],
  ["appearance", "外貌"],
  ["speech_style", "说话方式"],
  ["ability", "能力"],
] as const;
type ProfileKey = (typeof PROFILE_KEYS)[number][0];

/** 卡 dict（§4.6.2 schema；与 Task 17 draft 输出 / Task 18 PUT body 同形）。 */
export interface CharacterCardData {
  name: string;
  role_type: RoleType;
  aliases: string[];
  profile: Record<ProfileKey, string>;
  arc: { stages: string[] };
}

function emptyCard(name: string): CharacterCardData {
  return {
    name,
    role_type: "supporting",
    aliases: [],
    profile: {
      personality: "",
      motivation: "",
      background: "",
      appearance: "",
      speech_style: "",
      ability: "",
    },
    arc: { stages: [] },
  };
}

/// 规整 agent 草稿（Task 17 契约：缺键补 ""、profile 强制 dict、name 强制入参名）。
/// 防御：profile 回字符串 / 缺键 / aliases 非数组 / stages 非数组 → 全部兜成合法形状。
function normalizeCard(raw: unknown, fallbackName: string, fallbackRole: RoleType): CharacterCardData {
  const obj = (raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {}) as Record<string, unknown>;

  const profileRaw = obj.profile;
  const profileSrc =
    profileRaw && typeof profileRaw === "object" && !Array.isArray(profileRaw)
      ? (profileRaw as Record<string, unknown>)
      : {};
  const profile = {} as Record<ProfileKey, string>;
  for (const [key] of PROFILE_KEYS) {
    const v = profileSrc[key];
    profile[key] = typeof v === "string" ? v : "";
  }

  const arcRaw = obj.arc;
  const stagesRaw =
    arcRaw && typeof arcRaw === "object" && !Array.isArray(arcRaw)
      ? (arcRaw as Record<string, unknown>).stages
      : undefined;
  const stages = Array.isArray(stagesRaw)
    ? stagesRaw.map((s) => (typeof s === "string" ? s : JSON.stringify(s))).filter((s) => s !== "")
    : [];

  const aliases = Array.isArray(obj.aliases) ? obj.aliases.map((a) => String(a)) : [];

  const role = typeof obj.role_type === "string" && (ROLE_TYPES as readonly string[]).includes(obj.role_type)
    ? (obj.role_type as RoleType)
    : fallbackRole;

  const name = typeof obj.name === "string" && obj.name.trim() ? obj.name : fallbackName;

  return { name, role_type: role, aliases, profile, arc: { stages } };
}

function endpointNotReady(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405);
}

interface CharacterDraftDialogProps {
  projectId: string;
  /** 待建卡 member（起草依据 + 初始 name）。 */
  member: CastMember;
  onClose: () => void;
  /** 落卡成功后由父视图刷新列表。 */
  onSaved: () => void;
}

export function CharacterDraftDialog({ projectId, member, onClose, onSaved }: CharacterDraftDialogProps) {
  const [card, setCard] = useState<CharacterCardData>(() => emptyCard(member.name));
  const [busy, setBusy] = useState<"draft" | "save" | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  // 落卡响应里的重提取章号列表（Task 15 ①）：成功提示按它如实措辞。
  const [reextractSeqs, setReextractSeqs] = useState<number[] | null>(null);

  const base = `/api/project/${encodeURIComponent(projectId)}/characters`;

  function patch(partial: Partial<CharacterCardData>) {
    setCard((c) => ({ ...c, ...partial }));
  }

  function patchProfile(key: ProfileKey, value: string) {
    setCard((c) => ({ ...c, profile: { ...c.profile, [key]: value } }));
  }

  async function draft() {
    if (busy) return;
    setBusy("draft");
    setHint(null);
    setError(null);
    try {
      // Task 17 契约：POST body {name, role_type, requirement?} → {"card": ...}
      const resp = await apiPost<{ card?: unknown }>(`${base}/draft`, {
        name: member.name,
        role_type: card.role_type,
      });
      setCard(normalizeCard(resp.card, member.name, card.role_type));
      setHint("已按 agent 草稿回填，可逐字段修改后再落卡。");
    } catch (err) {
      if (endpointNotReady(err)) {
        setHint("端点未就绪（W5 落地）：可先手动填写，保存同样等端点就绪后生效。");
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (busy) return;
    if (!card.name.trim()) {
      setError("名字不能为空");
      return;
    }
    setBusy("save");
    setHint(null);
    setError(null);
    try {
      // Task 18 契约：PUT body = 卡 dict → 202 {saved, path, reextract_started}
      const resp = await apiPut<{ saved?: boolean; path?: string; reextract_started?: number[] }>(
        `${base}/${encodeURIComponent(card.name)}`,
        card,
      );
      setSavedPath(resp.path ?? "");
      setReextractSeqs(Array.isArray(resp.reextract_started) ? resp.reextract_started : []);
      onSaved();
    } catch (err) {
      if (endpointNotReady(err)) {
        setHint("端点未就绪（W5 落地）：卡已备好但暂不能落盘，可先关闭，端点上线后再来保存。");
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="approval-overlay" onClick={onClose}>
      <div
        className="approval-dialog draft-dialog"
        role="dialog"
        aria-label="角色起草确认卡"
        onClick={(e) => e.stopPropagation()}
      >
        <h3>角色起草确认卡 · {member.name}</h3>

        <div className="draft-fields">
          <label className="draft-label" htmlFor="dcf-name">name</label>
          <input
            id="dcf-name"
            className="draft-input"
            value={card.name}
            disabled={savedPath !== null}
            onChange={(e) => patch({ name: e.target.value })}
          />

          <label className="draft-label" htmlFor="dcf-role">role_type</label>
          <select
            id="dcf-role"
            className="draft-input"
            value={card.role_type}
            disabled={savedPath !== null || busy === "draft"}
            onChange={(e) => patch({ role_type: e.target.value as RoleType })}
          >
            {ROLE_TYPES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>

          <label className="draft-label" htmlFor="dcf-aliases">aliases</label>
          <input
            id="dcf-aliases"
            className="draft-input"
            placeholder="别名，用逗号分隔"
            value={card.aliases.join(", ")}
            disabled={savedPath !== null}
            onChange={(e) =>
              patch({
                aliases: e.target.value
                  .split(/[,，、]/)
                  .map((s) => s.trim())
                  .filter((s) => s !== ""),
              })
            }
          />

          {PROFILE_KEYS.map(([key, label]) => (
            <DraftProfileField
              key={key}
              fieldKey={key}
              label={label}
              value={card.profile[key]}
              disabled={savedPath !== null}
              onChange={patchProfile}
            />
          ))}

          <label className="draft-label" htmlFor="dcf-stages">arc.stages</label>
          <textarea
            id="dcf-stages"
            className="draft-textarea"
            rows={3}
            placeholder="成长弧阶段，每行一条"
            value={card.arc.stages.join("\n")}
            disabled={savedPath !== null}
            onChange={(e) =>
              patch({ arc: { stages: e.target.value.split("\n").map((s) => s.trim()).filter((s) => s !== "") } })
            }
          />
        </div>

        <div className="draft-basis">
          <h4>起草依据</h4>
          <div className="kv"><span className="kv-key">units</span><span className="kv-value">{member.beats}</span></div>
          <div className="kv"><span className="kv-key">章数</span><span className="kv-value">{member.chapters}</span></div>
          <div className="kv">
            <span className="kv-key">首现章</span>
            <span className="kv-value">第 {member.first_chapter} 章</span>
          </div>
          <div className="kv">
            <span className="kv-key">存在感</span>
            <span className="kv-value">{(member.share * 100).toFixed(1)}%</span>
          </div>
        </div>

        {hint && <p className="muted draft-hint">{hint}</p>}
        {error && <p className="error-text draft-hint">失败：{error}</p>}
        {savedPath !== null && (
          <p className="muted draft-hint">
            已落卡：{savedPath}（
            {reextractSeqs !== null && reextractSeqs.length > 0
              ? `记忆重提取已触发：第 ${reextractSeqs.join("、")} 章，进度见事件流`
              : "无已写章，无需重提取"}
            ）
          </p>
        )}

        <div className="approval-actions">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {savedPath !== null ? "关闭" : "取消"}
          </button>
          <span className="draft-actions-right">
            <button
              type="button"
              className="btn-ghost"
              disabled={busy !== null || savedPath !== null}
              onClick={() => void draft()}
            >
              {busy === "draft" ? "起草中…" : "agent 起草"}
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={busy !== null || savedPath !== null}
              onClick={() => void save()}
            >
              {busy === "save" ? "落卡中…" : "保存并落卡"}
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}

function DraftProfileField({
  fieldKey,
  label,
  value,
  disabled,
  onChange,
}: {
  fieldKey: ProfileKey;
  label: string;
  value: string;
  disabled: boolean;
  onChange: (key: ProfileKey, value: string) => void;
}) {
  return (
    <>
      <label className="draft-label" htmlFor={`dcf-${fieldKey}`}>profile.{fieldKey}（{label}）</label>
      <textarea
        id={`dcf-${fieldKey}`}
        className="draft-textarea"
        rows={2}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(fieldKey, e.target.value)}
      />
    </>
  );
}
