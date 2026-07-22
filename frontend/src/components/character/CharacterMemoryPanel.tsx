import { useEffect, useState } from "react";
import { Button, Textarea, Card } from "@/components/ui";
import { longApi } from "@/api/long";
import type { CharacterMemory } from "@/types";

interface CharacterMemoryPanelProps {
  characterId: string;
}

const IMPORTANCE_LABELS: Record<CharacterMemory["importance"], string> = {
  core: "核心",
  major: "重要",
  minor: "次要",
};

const TTL_LABELS: Record<CharacterMemory["ttl"], string> = {
  permanent: "永久",
  long: "长期",
  arc: "剧情弧",
  scene: "场景",
};

export function CharacterMemoryPanel({ characterId }: CharacterMemoryPanelProps) {
  const [memories, setMemories] = useState<CharacterMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newContent, setNewContent] = useState("");
  const [newImportance, setNewImportance] = useState<CharacterMemory["importance"]>("major");
  const [newTtl, setNewTtl] = useState<CharacterMemory["ttl"]>("long");

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editImportance, setEditImportance] = useState<CharacterMemory["importance"]>("major");
  const [editTtl, setEditTtl] = useState<CharacterMemory["ttl"]>("long");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await longApi.characterMemories(characterId);
      setMemories(res.data.memories || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [characterId]);

  const resetNew = () => {
    setNewContent("");
    setNewImportance("major");
    setNewTtl("long");
  };

  const handleAdd = async () => {
    if (!newContent.trim()) return;
    setError(null);
    try {
      await longApi.addCharacterMemory(characterId, {
        content: newContent,
        importance: newImportance,
        ttl: newTtl,
      });
      resetNew();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失败");
    }
  };

  const startEdit = (m: CharacterMemory) => {
    setEditingId(m.id);
    setEditContent(m.content);
    setEditImportance(m.importance);
    setEditTtl(m.ttl);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditContent("");
    setEditImportance("major");
    setEditTtl("long");
  };

  const handleUpdate = async (memoryId: string) => {
    if (!editContent.trim()) return;
    setError(null);
    try {
      await longApi.updateCharacterMemory(characterId, memoryId, {
        content: editContent,
        importance: editImportance,
        ttl: editTtl,
      });
      cancelEdit();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "更新失败");
    }
  };

  const handleDelete = async (memoryId: string) => {
    setError(null);
    try {
      await longApi.deleteCharacterMemory(characterId, memoryId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  return (
    <div className="space-y-4">
      {error && (
        <div className="border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
      )}

      <Card className="p-3">
        <div className="mb-2 text-sm font-medium">手动新增记忆</div>
        <Textarea
          className="mb-2 h-20 resize-none"
          placeholder="记忆内容..."
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
        />
        <div className="mb-2 flex gap-2">
          <select
            className="border border-line bg-paper px-2 py-1 text-sm"
            value={newImportance}
            onChange={(e) => setNewImportance(e.target.value as CharacterMemory["importance"])}
          >
            <option value="core">{IMPORTANCE_LABELS.core}</option>
            <option value="major">{IMPORTANCE_LABELS.major}</option>
            <option value="minor">{IMPORTANCE_LABELS.minor}</option>
          </select>
          <select
            className="border border-line bg-paper px-2 py-1 text-sm"
            value={newTtl}
            onChange={(e) => setNewTtl(e.target.value as CharacterMemory["ttl"])}
          >
            <option value="permanent">{TTL_LABELS.permanent}</option>
            <option value="long">{TTL_LABELS.long}</option>
            <option value="arc">{TTL_LABELS.arc}</option>
            <option value="scene">{TTL_LABELS.scene}</option>
          </select>
        </div>
        <Button variant="primary" onClick={handleAdd}>新增</Button>
      </Card>

      {loading && <div className="text-sm text-muted">加载中...</div>}

      <div className="space-y-2">
        {memories.map((m) => (
          <Card key={m.id} className="p-3">
            {editingId === m.id ? (
              <div className="space-y-2">
                <Textarea
                  className="h-20 resize-none"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                />
                <div className="flex gap-2">
                  <select
                    className="border border-line bg-paper px-2 py-1 text-sm"
                    value={editImportance}
                    onChange={(e) => setEditImportance(e.target.value as CharacterMemory["importance"])}
                  >
                    <option value="core">{IMPORTANCE_LABELS.core}</option>
                    <option value="major">{IMPORTANCE_LABELS.major}</option>
                    <option value="minor">{IMPORTANCE_LABELS.minor}</option>
                  </select>
                  <select
                    className="border border-line bg-paper px-2 py-1 text-sm"
                    value={editTtl}
                    onChange={(e) => setEditTtl(e.target.value as CharacterMemory["ttl"])}
                  >
                    <option value="permanent">{TTL_LABELS.permanent}</option>
                    <option value="long">{TTL_LABELS.long}</option>
                    <option value="arc">{TTL_LABELS.arc}</option>
                    <option value="scene">{TTL_LABELS.scene}</option>
                  </select>
                </div>
                <div className="flex gap-2">
                  <Button variant="primary" onClick={() => handleUpdate(m.id)}>保存</Button>
                  <Button variant="ghost" onClick={cancelEdit}>取消</Button>
                </div>
              </div>
            ) : (
              <>
                <div className="text-sm leading-relaxed">{m.content}</div>
                <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                  <span>{IMPORTANCE_LABELS[m.importance]}</span>
                  <span>{TTL_LABELS[m.ttl]}</span>
                  <span>
                    {m.source_type === "auto"
                      ? `第 ${m.source_chapter_id?.slice(0, 6)} 章自动提取`
                      : "用户手动修改"}
                  </span>
                </div>
                <div className="mt-2 flex gap-2">
                  <Button variant="ghost" onClick={() => startEdit(m)}>编辑</Button>
                  <Button variant="ghost" onClick={() => handleDelete(m.id)}>删除</Button>
                </div>
              </>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
