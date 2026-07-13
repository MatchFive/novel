import { useEffect, useState } from "react";
import { settingsApi } from "@/api/settings";
import type { ModelConfig, UserSettings } from "@/types";
import { Button, Input, Card, Tag } from "@/components/ui";

const EMPTY_MODEL = { name: "", base_url: "", api_key: "", model: "", is_default: false, level: "", embedding_model: "" };

const LEVEL_OPTIONS = [
  { value: "", label: "通用" },
  { value: "low", label: "low" },
  { value: "medium", label: "medium" },
  { value: "high", label: "high" },
  { value: "embedding", label: "embedding" },
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [newModel, setNewModel] = useState({ ...EMPTY_MODEL });
  const [testMsg, setTestMsg] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ ...EMPTY_MODEL });

  const load = async () => {
    const s = await settingsApi.get();
    setSettings(s.data);
    const m = await settingsApi.listModels();
    setModels(m.data);
  };
  useEffect(() => { load(); }, []);

  if (!settings) return <div className="p-10 text-sm text-muted">加载中…</div>;

  const saveSettings = async (patch: Partial<UserSettings>) => {
    const { data } = await settingsApi.update(patch);
    setSettings(data);
  };

  const getAssistantSetting = (key: keyof UserSettings): number => settings[key] as number;
  const updateAssistantSetting = (key: keyof UserSettings, value: number) =>
    saveSettings({ [key]: value } as Partial<UserSettings>);

  const addHotspot = () => {
    const src = [...(settings.hotspot_sources || []), { url: "", name: "" }];
    saveSettings({ hotspot_sources: src });
  };
  const updHotspot = (i: number, key: string, val: string) => {
    const src = settings.hotspot_sources.map((s, idx) => (idx === i ? { ...s, [key]: val } : s));
    saveSettings({ hotspot_sources: src });
  };
  const delHotspot = (i: number) => {
    saveSettings({ hotspot_sources: settings.hotspot_sources.filter((_, idx) => idx !== i) });
  };

  const startEdit = (m: ModelConfig) => {
    setEditingId(m.id);
    setEditForm({
      name: m.name,
      base_url: m.base_url,
      model: m.model,
      api_key: "",
      is_default: m.is_default,
      level: m.level || "",
      embedding_model: m.embedding_model || "",
    });
  };

  const saveEdit = async (id: string) => {
    await settingsApi.updateModel(id, editForm);
    setEditingId(null);
    await load();
  };

  const addModel = async () => {
    if (!newModel.name || !newModel.base_url || !newModel.model) return;
    await settingsApi.createModel(newModel);
    setNewModel({ ...EMPTY_MODEL });
    setTestMsg("");
    await load();
  };

  const testNewModel = async () => {
    const r = await settingsApi.testModel(newModel);
    setTestMsg(r.data.ok ? "连接成功：" + (r.data.reply || "").slice(0, 50) : "失败：" + r.data.error);
  };

  const assistantItems: { key: keyof UserSettings; label: string; min: number; max: number }[] = [
    { key: "assistant_summary_threshold", label: "压缩阈值（轮）", min: 1, max: 100 },
    { key: "assistant_max_summaries", label: "最大保留摘要数", min: 0, max: 20 },
    { key: "assistant_summary_max_length", label: "单条摘要最大长度（字符）", min: 100, max: 4000 },
  ];

  const hasDefault = models.some((m) => m.is_default);
  const configuredLevels = new Set(models.map((m) => m.level).filter(Boolean));
  const missingLevels = hasDefault ? [] : ["low", "medium", "high"].filter((l) => !configuredLevels.has(l));
  const hasEmbeddingSource = models.some(
    (m) => m.level === "embedding" || (m.embedding_model && m.embedding_model.trim() !== "")
  );

  const selectClass =
    "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30";

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="font-serif text-2xl font-semibold tracking-wide text-ink">设置</h1>

      <Card className="mt-8">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">递归调用上限（Agent 取数）</div>
        <div className="flex items-center gap-4 px-4 py-4">
          <input
            type="range" min={1} max={30}
            value={settings.recursive_limit}
            onChange={(e) => saveSettings({ recursive_limit: Number(e.target.value) })}
            className="accent-accent"
          />
          <span className="text-sm tabular-nums text-ink">{settings.recursive_limit}</span>
        </div>
      </Card>

      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">热搜源（请求 URL + 适配器）</div>
        <div className="space-y-2 p-4">
          {settings.hotspot_sources.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input placeholder="名称" value={s.name || ""} onChange={(e) => updHotspot(i, "name", e.target.value)} />
              <Input placeholder="https://..." value={s.url || ""} onChange={(e) => updHotspot(i, "url", e.target.value)} />
              <Button variant="ghost" onClick={() => delHotspot(i)}>删</Button>
            </div>
          ))}
          <Button variant="ghost" onClick={addHotspot}>+ 添加热搜源</Button>
        </div>
      </Card>

      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">助手对话</div>
        <div className="space-y-4 p-4">
          {assistantItems.map((item) => (
            <div key={item.key} className="flex items-center justify-between">
              <span className="text-sm text-ink">{item.label}</span>
              <Input
                type="number"
                min={item.min}
                max={item.max}
                value={getAssistantSetting(item.key)}
                onChange={(e) => updateAssistantSetting(item.key, Number(e.target.value))}
                className="w-24"
              />
            </div>
          ))}
        </div>
      </Card>

      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">模型配置</div>
        <div className="space-y-3 p-4">
          {missingLevels.length > 0 && (
            <div className="rounded-lg border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
              重要度 [{missingLevels.join(", ")}] 未配置模型；若该 level 的任务未命中专用配置，将回退到 .env 默认。建议添加全局默认模型。
            </div>
          )}
          {!hasEmbeddingSource && (
            <div className="rounded-lg border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
              未指定 embedding 模型。
            </div>
          )}

          {models.length === 0 && <div className="text-sm text-muted">暂无模型配置，点击下方预设或手动添加。</div>}
          {models.map((m) => (
            <Card key={m.id} className="p-3 text-sm">
              {editingId === m.id ? (
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <Input placeholder="名称" value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} />
                    <Input placeholder="model" value={editForm.model} onChange={(e) => setEditForm({ ...editForm, model: e.target.value })} />
                    <Input placeholder="base_url" value={editForm.base_url} onChange={(e) => setEditForm({ ...editForm, base_url: e.target.value })} />
                    <Input placeholder="api_key（留空则保持原值）" type="password" value={editForm.api_key} onChange={(e) => setEditForm({ ...editForm, api_key: e.target.value })} />
                    <select
                      className={selectClass}
                      value={editForm.level}
                      onChange={(e) => setEditForm({ ...editForm, level: e.target.value })}
                    >
                      {LEVEL_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                    <Input
                      placeholder="embedding_model"
                      value={editForm.embedding_model}
                      onChange={(e) => setEditForm({ ...editForm, embedding_model: e.target.value })}
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button variant="primary" onClick={() => saveEdit(m.id)}>保存</Button>
                    <Button variant="ghost" onClick={() => setEditingId(null)}>取消</Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-between">
                  <div>
                    <span className="font-medium text-ink">{m.name}</span>
                    <span className="ml-2 text-muted">{m.model}</span>
                    {m.is_default && <Tag className="ml-2">默认</Tag>}
                    {m.level && <Tag className="ml-2">{m.level}</Tag>}
                    {m.embedding_model && <span className="ml-2 text-xs text-muted">embedding: {m.embedding_model}</span>}
                    <div className="text-xs text-muted">{m.base_url}</div>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="ghost" onClick={() => startEdit(m)}>编辑</Button>
                    <Button variant="ghost" onClick={async () => { await settingsApi.updateModel(m.id, { is_default: true }); load(); }}>设为默认</Button>
                    <Button variant="ghost" onClick={async () => { await settingsApi.deleteModel(m.id); load(); }}>删</Button>
                  </div>
                </div>
              )}
            </Card>
          ))}

          <div className="border-t border-line pt-4">
            <div className="mb-2 text-sm font-medium text-ink">新增模型</div>
            <div className="grid grid-cols-2 gap-2">
              <Input placeholder="名称" value={newModel.name} onChange={(e) => setNewModel({ ...newModel, name: e.target.value })} />
              <Input placeholder="model" value={newModel.model} onChange={(e) => setNewModel({ ...newModel, model: e.target.value })} />
              <Input placeholder="base_url" value={newModel.base_url} onChange={(e) => setNewModel({ ...newModel, base_url: e.target.value })} />
              <Input placeholder="api_key" type="password" value={newModel.api_key} onChange={(e) => setNewModel({ ...newModel, api_key: e.target.value })} />
              <select
                className={selectClass}
                value={newModel.level}
                onChange={(e) => setNewModel({ ...newModel, level: e.target.value })}
              >
                {LEVEL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <Input
                placeholder="embedding_model，例如 text-embedding-3-small"
                value={newModel.embedding_model}
                onChange={(e) => setNewModel({ ...newModel, embedding_model: e.target.value })}
              />
            </div>
            <div className="mt-2 flex items-center gap-2">
              <Button variant="primary" onClick={addModel}>+ 新增模型</Button>
              <Button variant="ghost" onClick={testNewModel}>测试连接</Button>
              <span className="text-xs text-muted">{testMsg}</span>
            </div>
            <div className="mt-1 text-xs text-muted">测试连接不会保存配置，只有「新增模型」或「保存」后才会持久化。</div>
          </div>
        </div>
      </Card>
    </div>
  );
}
