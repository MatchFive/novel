import { create } from "zustand";
import { assistantApi } from "@/api/short";
import type { AssistantMessage, ChangeRecord } from "@/types";

interface AssistantSessionState {
  sessionId: string | null;
  messages: AssistantMessage[];
  busy: boolean;
  pendingRecords: ChangeRecord[];
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

  loadHistory: async (pid: string) => {
    const { data } = await assistantApi.history(pid);
    set({
      sessionId: data.session_id,
      messages: data.messages || [],
      pendingRecords: data.staged_changes || [],
    });
  },

  sendMessage: async (pid: string, text: string) => {
    set({ busy: true });
    try {
      const userMsg: AssistantMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      };
      set((s) => ({ messages: [...s.messages, userMsg] }));
      const { data } = await assistantApi.chat(pid, text);
      const assistantMsg: AssistantMessage = {
        id: data.message_id,
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
    } finally {
      set({ busy: false });
    }
  },

  stageChange: async (record: ChangeRecord) => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    await assistantApi.stage(sessionId, record);
    set((s) => ({
      pendingRecords: [...s.pendingRecords, record],
    }));
  },

  confirm: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true });
    try {
      const { data } = await assistantApi.confirm(sessionId);
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
    } finally {
      set({ busy: false });
    }
  },

  reject: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    set({ busy: true });
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
    } finally {
      set({ busy: false });
    }
  },
}));
