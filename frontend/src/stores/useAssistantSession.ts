import { create } from "zustand";
import { assistantApi } from "@/api/short";
import type { AssistantMessage, AssistantSession, ChangeRecord } from "@/types";

interface AssistantSessionState {
  sessionId: string | null;
  sessions: AssistantSession[];
  messages: AssistantMessage[];
  busy: boolean;
  pendingRecords: ChangeRecord[];
  error: string | null;
  assistantOpen: boolean;
  reset: () => void;
  loadHistory: (pid: string | null) => Promise<void>;
  loadSessions: (pid: string | null) => Promise<void>;
  sendMessage: (pid: string | null, text: string, context?: Record<string, any>) => Promise<void>;
  createSession: (pid: string) => Promise<void>;
  switchSession: (sessionId: string, pid: string) => Promise<void>;
  stageChange: (record: ChangeRecord) => Promise<void>;
  confirm: (changeIds?: string[]) => Promise<void>;
  reject: (changeIds?: string[]) => Promise<void>;
  setAssistantOpen: (open: boolean) => void;
  openAssistant: () => void;
}

export const useAssistantSession = create<AssistantSessionState>((set, get) => ({
  sessionId: null,
  sessions: [],
  messages: [],
  busy: false,
  pendingRecords: [],
  error: null,
  assistantOpen: false,

  reset: () => {
    set({ sessionId: null, sessions: [], messages: [], pendingRecords: [], error: null, assistantOpen: false });
  },

  loadHistory: async (pid: string | null) => {
    set({ error: null, messages: [], pendingRecords: [] });
    if (!pid) {
      set({ sessionId: null });
      return;
    }
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

  loadSessions: async (pid: string | null) => {
    if (!pid) {
      set({ sessions: [] });
      return;
    }
    try {
      const { data } = await assistantApi.sessions(pid);
      set({ sessions: data.sessions || [] });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "加载会话列表失败" });
    }
  },

  createSession: async (pid: string) => {
    set({ busy: true, error: null });
    try {
      const { data } = await assistantApi.createSession(pid);
      await get().loadSessions(pid);
      await get().loadHistory(pid);
      set({ sessionId: data.session.id });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "新建对话失败" });
    } finally {
      set({ busy: false });
    }
  },

  switchSession: async (sessionId: string, pid: string) => {
    set({ busy: true, error: null });
    try {
      await assistantApi.switchSession(sessionId);
      await get().loadSessions(pid);
      await get().loadHistory(pid);
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "切换对话失败" });
    } finally {
      set({ busy: false });
    }
  },

  sendMessage: async (pid: string | null, text: string, context?: Record<string, any>) => {
    set({ busy: true, error: null, assistantOpen: true });
    const userMsg: AssistantMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    set((s) => ({ messages: [...s.messages, userMsg] }));
    try {
      const { data } = await assistantApi.chat(pid, text, context);
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
      set({ error: errorText });
      throw err;
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
      set((s) => {
        const messages = [...s.messages];
        const latestAssistant = messages.slice().reverse().find((m) => m.role === "assistant");
        if (!latestAssistant || latestAssistant.metadata?.status) {
          messages.push({
            id: `local-staged-${Date.now()}`,
            role: "assistant",
            content: "以下是你手动编辑的变更建议，请确认。",
            created_at: new Date().toISOString(),
          });
        }
        return {
          messages,
          pendingRecords: [...s.pendingRecords, record],
        };
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "暂存变更失败" });
    }
  },

  confirm: async (changeIds?: string[]) => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true, error: null });
    try {
      const { data } = await assistantApi.confirm(sessionId, changeIds);
      const messages = [...get().messages];
      let lastAssistant = -1;
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === "assistant") {
          lastAssistant = i;
          break;
        }
      }
      if (!data.ok) {
        const errorText = (data.errors || [])
          .map((e: any) => e.message || String(e))
          .join("；") || "应用失败";
        if (lastAssistant >= 0) {
          messages[lastAssistant] = {
            ...messages[lastAssistant],
            metadata: {
              ...messages[lastAssistant].metadata,
              status: "partial",
              applied_count: (data.applied || []).length,
              error_count: (data.errors || []).length,
            },
          };
        }
        // 仅移除已成功应用的记录，未成功的保留在待确认列表
        const appliedIds = new Set((data.applied || []).map((a: any) => a.change_id || a.entity_id));
        set({
          error: errorText,
          messages,
          pendingRecords: get().pendingRecords.filter((r) => !appliedIds.has(r.id)),
        });
        return;
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
      // 如果是指定确认，移除对应记录；如果是全部确认，后端已清空
      const confirmedIds = changeIds
        ? new Set(changeIds)
        : new Set(get().pendingRecords.map((r) => r.id));
      set({
        messages,
        pendingRecords: get().pendingRecords.filter((r) => !confirmedIds.has(r.id)),
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "应用失败" });
    } finally {
      set({ busy: false });
    }
  },

  reject: async (changeIds?: string[]) => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true, error: null });
    try {
      const { data } = await assistantApi.reject(sessionId, changeIds);
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
        const rejectedIds = changeIds ? new Set(changeIds) : new Set(s.pendingRecords.map((r) => r.id));
        return {
          messages,
          pendingRecords: s.pendingRecords.filter((r) => !rejectedIds.has(r.id)),
        };
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "拒绝失败" });
    } finally {
      set({ busy: false });
    }
  },

  setAssistantOpen: (open: boolean) => set({ assistantOpen: open }),
  openAssistant: () => set({ assistantOpen: true }),
}));
