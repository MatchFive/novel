import { useEffect, useState } from "react";
import api from "@/api/client";
import { settingsApi } from "@/api/settings";
import type { ModelConfigPayload } from "@/api/settings";
import type { ModelConfig, UserSettings } from "@/types";
import { Button, Input, Card } from "@/components/ui";

function getModelKind(m: ModelConfig): "chat" | "embedding" {
  return m.level === "embedding" || m.embedding_model ? "embedding" : "chat";
}

function ModelCard({ m, onChange, onError }: { m: ModelConfig; onChange: () => void; onError: (msg: string) => void }) {
  const [editing, setEditing] = useState(false);
  const kind = getModelKind(m);
  const [form, setForm] = useState({
    name: m.name,
    base_url: m.base_url,
    api_key: "",
    model: m.model,
    temperature: m.temperature ?? 0.7,
    embedding_dimension: m.embedding_dimension ?? 1536,
  });

  useEffect(() => {
    if (editing) {
      setForm({
        name: m.name,
        base_url: m.base_url,
        api_key: "",
        model: m.model,
        temperature: m.temperature ?? 0.7,
        embedding_dimension: m.embedding_dimension ?? 1536,
      });
    }
  }, [editing, m.id, m.name, m.base_url, m.model, m.temperature, m.embedding_dimension]);

  const save = async () => {
    try {
      const payload: Partial<ModelConfigPayload> = {
        name: form.name,
        base_url: form.base_url,
        model: form.model,
        temperature: kind === "chat" ? form.temperature : undefined,
        embedding_dimension: kind === "embedding" ? form.embedding_dimension : undefined,
      };
      if (form.api_key) payload.api_key = form.api_key;
      await settingsApi.updateModel(m.id, payload);
      setEditing(false);
      onChange();
    } catch (e) {
      onError("保存失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const setDefault = async () => {
    try {
      await settingsApi.updateModel(m.id, { is_default: true });
      onChange();
    } catch (e) {
      onError("设为默认失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const deleteModel = async () => {
    try {
      await settingsApi.deleteModel(m.id);
      onChange();
    } catch (e) {
      onError("删除失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  if (editing) {
    return (
      <div className="rounded-lg border border-line p-3 space-y-2">
        <Input placeholder="名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Input placeholder="base_url" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
        <Input placeholder="api_key（留空则保持原值）" type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
        <Input placeholder="模型" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
        {kind === "chat" && (
          <Input type="number" step={0.1} min={0} max={2} value={form.temperature} onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })} />
        )}
        {kind === "embedding" && (
          <Input type="number" value={form.embedding_dimension} onChange={(e) => setForm({ ...form, embedding_dimension: Number(e.target.value) })} />
        )}
        <div className="flex gap-2">
          <Button variant="primary" onClick={save}>保存</Button>
          <Button variant="ghost" onClick={() => setEditing(false)}>取消</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex items-center justify-between">
        <div>
          <span className="font-medium text-ink">{m.name}</span>
          {m.is_default && <span className="ml-2 rounded bg-accent px-2 py-0.5 text-xs text-white">默认</span>}
          <div className="text-xs text-muted">{m.model}{kind === "chat" && m.temperature !== undefined ? ` · 温度 ${m.temperature}` : ""}{kind === "embedding" ? ` · dim ${m.embedding_dimension}` : ""}</div>
          <div className="text-xs text-muted">{m.base_url}</div>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => setEditing(true)}>编辑</Button>
          <Button variant="ghost" onClick={setDefault}>设为默认</Button>
          <Button variant="ghost" onClick={deleteModel}>删</Button>
        </div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({
    name: "",
    base_url: "",
    api_key: "",
    model: "",
    kind: "chat" as "chat" | "embedding",
    temperature: 0.7,
    embedding_dimension: 1536,
  });
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [testMsg, setTestMsg] = useState("");

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

  const exportLogs = async () => {
    try {
      const r = await api.get("/log/export", { responseType: "blob" });
      const blob = new Blob([r.data], { type: "application/zip" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      const ts = new Date().toISOString().slice(0, 19).replace(/:/g, "");
      a.href = url;
      a.download = `novel-studio-logs-${ts}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setTestMsg("导出日志失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const getAssistantSetting = (key: keyof UserSettings): number => settings[key] as number;
  const updateAssistantSetting = (key: keyof UserSettings, value: number) =>
    saveSettings({ [key]: value } as Partial<UserSettings>);

  const chatModels = models.filter((m) => getModelKind(m) === "chat");
  const embeddingModels = models.filter((m) => getModelKind(m) === "embedding");

  const fetchAvailable = async () => {
    try {
      const r = await settingsApi.fetchModels(addForm.base_url, addForm.api_key);
      if (r.data.ok && r.data.models) {
        setAvailableModels(r.data.models);
      } else {
        setTestMsg("拉取失败：" + (r.data.error || "未知错误"));
      }
    } catch (e) {
      setTestMsg("拉取失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const testModel = async () => {
    try {
      const r = await settingsApi.testModel({
        base_url: addForm.base_url,
        api_key: addForm.api_key,
        model: addForm.model,
        kind: addForm.kind,
        embedding_dimension: addForm.embedding_dimension,
      });
      setTestMsg(
        r.data.ok
          ? addForm.kind === "embedding"
            ? `维度校验通过：${r.data.dimension ?? "未知"}`
            : "连接成功"
          : "失败：" + r.data.error
      );
    } catch (e) {
      setTestMsg("测试失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const saveNewModel = async () => {
    if (!addForm.name || !addForm.base_url || !addForm.model) {
      setTestMsg("请填写名称、base_url 和模型");
      return;
    }
    try {
      const payload: ModelConfigPayload = {
        name: addForm.name,
        base_url: addForm.base_url,
        model: addForm.model,
        api_key: addForm.api_key,
        is_default: models.length === 0,
        level: addForm.kind === "embedding" ? "embedding" : undefined,
        embedding_model: addForm.kind === "embedding" ? addForm.model : undefined,
        embedding_dimension: addForm.kind === "embedding" ? addForm.embedding_dimension : undefined,
        temperature: addForm.kind === "chat" ? addForm.temperature : undefined,
      };
      await settingsApi.createModel(payload);
      setAddForm({ ...addForm, name: "", base_url: "", api_key: "", model: "" });
      setAvailableModels([]);
      setShowAdd(false);
      setTestMsg("");
      await load();
    } catch (e) {
      setTestMsg("保存失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const assistantItems: { key: keyof UserSettings; label: string; min: number; max: number }[] = [
    { key: "assistant_summary_threshold", label: "压缩阈值（轮）", min: 1, max: 100 },
    { key: "assistant_max_summaries", label: "最大保留摘要数", min: 0, max: 20 },
    { key: "assistant_summary_max_length", label: "单条摘要最大长度（字符）", min: 100, max: 4000 },
    { key: "assistant_history_recent_messages", label: "最近加载消息数", min: 1, max: 100 },
    { key: "assistant_history_top_k", label: "检索相似对话对数", min: 0, max: 20 },
  ];

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
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">章节生成</div>
        <div className="space-y-4 p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink">每章目标字数</span>
            <Input
              type="number"
              min={1000}
              max={8000}
              value={settings.chapter_target_words}
              onChange={(e) => saveSettings({ chapter_target_words: Number(e.target.value) })}
              className="w-24"
            />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink">内容尺度等级</span>
            <select
              className={selectClass + " w-40"}
              value={settings.content_rating}
              onChange={(e) => saveSettings({ content_rating: e.target.value })}
            >
              <option value="loose">宽松</option>
              <option value="standard">标准</option>
              <option value="strict">严格</option>
            </select>
          </div>
          <div className="text-xs text-muted">
            目标字数用于章节拆分与正文分段生成；尺度等级决定正文生成后的自动检查与改写强度。
          </div>
        </div>
      </Card>

      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">模型配置</div>
        <div className="space-y-3 p-4">
          <div className="text-sm font-medium text-ink">文本模型</div>
          {chatModels.map((m) => (
            <ModelCard key={m.id} m={m} onChange={load} onError={setTestMsg} />
          ))}
          <div className="mt-4 text-sm font-medium text-ink">向量模型</div>
          {embeddingModels.map((m) => (
            <ModelCard key={m.id} m={m} onChange={load} onError={setTestMsg} />
          ))}

          {!showAdd ? (
            <Button variant="primary" className="w-full" onClick={() => setShowAdd(true)}>+ 新增模型</Button>
          ) : (
            <div className="space-y-2 rounded-lg border border-line p-3">
              <select value={addForm.kind} onChange={(e) => setAddForm({ ...addForm, kind: e.target.value as "chat" | "embedding" })} className={selectClass}>
                <option value="chat">文本模型</option>
                <option value="embedding">向量模型</option>
              </select>
              <Input placeholder="名称" value={addForm.name} onChange={(e) => setAddForm({ ...addForm, name: e.target.value })} />
              <Input placeholder="base_url" value={addForm.base_url} onChange={(e) => setAddForm({ ...addForm, base_url: e.target.value })} />
              <Input placeholder="api_key" type="password" value={addForm.api_key} onChange={(e) => setAddForm({ ...addForm, api_key: e.target.value })} />
              <Button variant="ghost" onClick={fetchAvailable}>刷新模型列表</Button>
              <select value={addForm.model} onChange={(e) => setAddForm({ ...addForm, model: e.target.value })} className={selectClass}>
                <option value="">选择模型...</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
              {addForm.kind === "chat" && (
                <Input type="number" step={0.1} min={0} max={2} placeholder="温度" value={addForm.temperature} onChange={(e) => setAddForm({ ...addForm, temperature: Number(e.target.value) })} />
              )}
              {addForm.kind === "embedding" && (
                <Input type="number" placeholder="向量维度" value={addForm.embedding_dimension} onChange={(e) => setAddForm({ ...addForm, embedding_dimension: Number(e.target.value) })} />
              )}
              <div className="flex gap-2">
                <Button variant="ghost" onClick={testModel}>测试连接</Button>
                <Button variant="primary" onClick={saveNewModel}>保存</Button>
                <Button variant="ghost" onClick={() => { setShowAdd(false); setAvailableModels([]); setTestMsg(""); }}>取消</Button>
              </div>
              {testMsg && <div className="text-xs text-muted">{testMsg}</div>}
            </div>
          )}
        </div>
      </Card>
      <Card className="mt-6">
        <div className="border-b border-line px-4 py-3 font-serif text-sm font-medium text-ink">诊断日志</div>
        <div className="flex items-center justify-between p-4">
          <span className="text-sm text-ink">导出后端、前端与启动器日志（.zip）</span>
          <Button variant="primary" onClick={exportLogs}>导出日志</Button>
        </div>
      </Card>
    </div>
  );
}
