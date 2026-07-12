export interface Project {
  id: string;
  type: "long" | "short";
  title: string;
  description: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelConfig {
  id: string;
  name: string;
  base_url: string;
  model: string;
  is_default: boolean;
}

export interface UserSettings {
  recursive_limit: number;
  hotspot_sources: { url: string; name?: string; adapter?: any }[];
  theme: string;
}

export interface ChangeRecord {
  id: string;
  project_id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before: any;
  after: any;
  requires_confirmation: boolean;
}

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  metadata?: {
    intent?: string;
    change_record_ids?: string[];
    status?: "applied" | "rejected";
    applied_count?: number;
    rejected_count?: number;
  };
  created_at: string | null;
}
