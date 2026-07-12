import { create } from "zustand";
import { assistantApi } from "@/api/short";
import type { AssistantMessage, ChangeRecord } from "@/types";

interface AssistantSessionState {
  sessionId: string | null;
  messages: AssistantMessage[];
  busy: boolean;
  pendingRecords: ChangeRecord[];
  error: string | null;
  loadHistory: (pid: string) => Promise<void>;
  sendMessage: (pid: string, text: string) => Promise<void>;
  stageChange: (record: ChangeRecord) => Promise<void>;
  confirm: () => Promise<void>;
  reject: () => Promise<void>;
}

export const useAssistantSession = create<AssistantSessionState>((set, get) => ({
  sessionId: null,
  messages: [],
  busy: false,
  pendingRecords: [],
  error: null,

  loadHistory: async (pid: string) => {
    set({ error: null, messages: [], pendingRecords: [] });
    try {
      const { data } = await assistantApi.history(pid);
      set({
        sessionId: data.session_id,
        messages: data.messages || [],
        pendingRecords: data.staged_changes || [],
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "加载历史失败" });
    }
  },

  sendMessage: async (pid: string, text: string) => {
    set({ busy: true, error: null });
    const userMsg: AssistantMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    set((s) => ({ messages: [...s.messages, userMsg] }));
    try {
      const { data } = await assistantApi.chat(pid, text);
      const assistantMsg: AssistantMessage = {
        id: data.message_id || `local-assistant-${Date.now()}`,
        role: "assistant",
        content: data.summary,
        metadata: {
          intent: data.intent,
          change_record_ids: (data.change_records || []).map((r: ChangeRecord) => r.id),
        },
        created_at: new Date().toISOString(),
      };
      set((s) => ({
        sessionId: data.session_id,
        messages: [...s.messages, assistantMsg],
        pendingRecords: [...s.pendingRecords, ...(data.change_records || [])],
      }));
    } catch (err) {
      const errorText = err instanceof Error ? err.message : "发送失败";
      set((s) => ({
        error: errorText,
        messages: [
          ...s.messages.filter((m) => m.id !== userMsg.id),
          {
            id: `error-${Date.now()}`,
            role: "assistant",
            content: `发送失败：${errorText}`,
            created_at: new Date().toISOString(),
          },
        ],
      }));
    } finally {
      set({ busy: false });
    }
  },

  stageChange: async (record: ChangeRecord) => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ error: null });
    try {
      await assistantApi.stage(sessionId, record);
      set((s) => ({
        pendingRecords: [...s.pendingRecords, record],
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "暂存变更失败" });
    }
  },

  confirm: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true, error: null });
    try {
      const { data } = await assistantApi.confirm(sessionId);
      if (!data.ok) {
        const errorText = (data.errors || [])
          .map((e: any) => e.message || String(e))
          .join("；") || "应用失败";
        set({ error: errorText, pendingRecords: [] });
        return;
      }
      set((s) => {
        const messages = [...s.messages];
        let lastAssistant = -1;
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === "assistant") {
            lastAssistant = i;
            break;
          }
        }
        if (lastAssistant >= 0) {
          messages[lastAssistant] = {
            ...messages[lastAssistant],
            metadata: {
              ...messages[lastAssistant].metadata,
              status: "applied",
              applied_count: (data.applied || []).length,
            },
          };
        }
        return { messages, pendingRecords: [] };
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "应用失败" });
    } finally {
      set({ busy: false });
    }
  },

  reject: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true, error: null });
    try {
      const { data } = await assistantApi.reject(sessionId);
      set((s) => {
        const messages = [...s.messages];
        let lastAssistant = -1;
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === "assistant") {
            lastAssistant = i;
            break;
          }
        }
        if (lastAssistant >= 0) {
          messages[lastAssistant] = {
            ...messages[lastAssistant],
            metadata: {
              ...messages[lastAssistant].metadata,
              status: "rejected",
              rejected_count: data.rejected_count || 0,
            },
          };
        }
        return { messages, pendingRecords: [] };
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "拒绝失败" });
    } finally {
      set({ busy: false });
    }
  },
}));
