import { useEffect, useState } from "react";
import { projectsApi } from "@/api/projects";
import { Button, Input } from "@/components/ui";
import type { Project, WritingStyle, GenerationConfig } from "@/types";

const PERSPECTIVE_OPTIONS = ["第一人称", "第三人称限知", "第三人称全知", "多视角"];
const LANGUAGE_OPTIONS = ["现代白话", "古风文言", "翻译腔", "口语化", "华丽繁复", "简洁克制"];
const PACE_OPTIONS = ["舒缓", "适中", "紧凑", "快节奏"];
const TONE_OPTIONS = ["轻松", "沉重", "热血", "悬疑", "温情", "冷峻"];
const RATING_OPTIONS = [
  { value: "loose", label: "宽松" },
  { value: "standard", label: "标准" },
  { value: "strict", label: "严格" },
];

interface Props {
  project: Project;
  open: boolean;
  initialTab?: "style" | "generation";
  onClose: () => void;
  onSaved?: (p: Project) => void;
}

export default function ProjectSettingsDialog({ project, open, initialTab = "style", onClose, onSaved }: Props) {
  const [tab, setTab] = useState(initialTab);
  const [style, setStyle] = useState<WritingStyle>(project.writing_style || {});
  const [gen, setGen] = useState<GenerationConfig>(project.generation_config || {});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStyle(project.writing_style || {});
    setGen(project.generation_config || {});
    setTab(initialTab);
    setError(null);
  }, [project, initialTab, open]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const targetWords =
        gen.chapter_target_words === undefined
          ? 2500
          : Math.min(8000, Math.max(1000, gen.chapter_target_words));
      const { data } = await projectsApi.update(project.id, {
        writing_style: style,
        generation_config: {
          ...gen,
          chapter_target_words: targetWords,
        },
      });
      onSaved?.(data);
      onClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "保存失败，请重试";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50">
      <div className="w-full max-w-lg rounded-lg border border-line bg-surface p-6 shadow-soft">
        <h2 className="mb-4 font-serif text-lg font-semibold text-ink">项目设置</h2>
        <div className="mb-4 flex gap-4 border-b border-line pb-2">
          <button className={tab === "style" ? "text-ink" : "text-muted"} onClick={() => { setTab("style"); setError(null); }}>文风</button>
          <button className={tab === "generation" ? "text-ink" : "text-muted"} onClick={() => { setTab("generation"); setError(null); }}>章节生成</button>
        </div>

        {tab === "style" ? (
          <div className="space-y-3">
            <SelectRow label="叙事视角" value={style.perspective || ""} options={PERSPECTIVE_OPTIONS} onChange={(v) => setStyle({ ...style, perspective: v })} />
            <SelectRow label="语言风格" value={style.language_style || ""} options={LANGUAGE_OPTIONS} onChange={(v) => setStyle({ ...style, language_style: v })} />
            <SelectRow label="节奏" value={style.pace || ""} options={PACE_OPTIONS} onChange={(v) => setStyle({ ...style, pace: v })} />
            <SelectRow label="情感基调" value={style.tone || ""} options={TONE_OPTIONS} onChange={(v) => setStyle({ ...style, tone: v })} />
            <div>
              <label className="mb-1 block text-sm text-ink">自定义补充</label>
              <textarea
                className="h-24 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30"
                value={style.custom_note || ""}
                onChange={(e) => setStyle({ ...style, custom_note: e.target.value })}
                placeholder="补充任何关于文风的自由描述..."
              />
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink">每章目标字数</span>
              <Input type="number" min={1000} max={8000} value={gen.chapter_target_words ?? 2500} onChange={(e) => setGen({ ...gen, chapter_target_words: Number(e.target.value) })} className="w-24" />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink">内容尺度等级</span>
              <select
                className="w-40 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30"
                value={gen.content_rating || "standard"}
                onChange={(e) => setGen({ ...gen, content_rating: e.target.value })}
              >
                {RATING_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        {error && (
          <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant="primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</Button>
        </div>
      </div>
    </div>
  );
}

function SelectRow({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-ink">{label}</span>
      <select
        className="w-48 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">未指定</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </div>
  );
}
