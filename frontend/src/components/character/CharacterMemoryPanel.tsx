import { useEffect, useState } from "react";
import { Button, Textarea, Card } from "@/components/ui";
import { longApi } from "@/api/long";
import type { CharacterMemory } from "@/types";

interface CharacterMemoryPanelProps {
  characterId: string;
}

export function CharacterMemoryPanel({ characterId }: CharacterMemoryPanelProps) {
  const [memories, setMemories] = useState<CharacterMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newImportance, setNewImportance] = useState<CharacterMemory["importance"]>("major");
  const [newTtl, setNewTtl] = useState<CharacterMemory["ttl"]>("long");

  const load = async () => {
    setLoading(true);
    try {
      const res = await longApi.characterMemories(characterId);
      setMemories(res.data.memories || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [characterId]);

  const handleAdd = async () => {
    if (!newContent.trim()) return;
    await longApi.addCharacterMemory(characterId, {
      content: newContent,
      importance: newImportance,
      ttl: newTtl,
    });
    setNewContent("");
    await load();
  };

  const handleDelete = async (memoryId: string) => {
    await longApi.deleteCharacterMemory(characterId, memoryId);
    await load();
  };

  return (
    <div className="space-y-4">
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
            <option value="core">核心</option>
            <option value="major">重要</option>
            <option value="minor">次要</option>
          </select>
          <select
            className="border border-line bg-paper px-2 py-1 text-sm"
            value={newTtl}
            onChange={(e) => setNewTtl(e.target.value as CharacterMemory["ttl"])}
          >
            <option value="permanent">永久</option>
            <option value="long">长期</option>
            <option value="arc">剧情弧</option>
            <option value="scene">场景</option>
          </select>
        </div>
        <Button variant="primary" onClick={handleAdd}>新增</Button>
      </Card>

      {loading && <div className="text-sm text-muted">加载中...</div>}

      <div className="space-y-2">
        {memories.map((m) => (
          <Card key={m.id} className="p-3">
            <div className="text-sm leading-relaxed">{m.content}</div>
            <div className="mt-1 flex items-center gap-2 text-xs text-muted">
              <span>{m.importance}</span>
              <span>{m.ttl}</span>
              <span>{m.source_type === "auto" ? `第 ${m.source_chapter_id?.slice(0, 6)} 章自动提取` : "用户手动修改"}</span>
            </div>
            <div className="mt-2">
              <Button variant="ghost" className="text-xs" onClick={() => handleDelete(m.id)}>删除</Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
