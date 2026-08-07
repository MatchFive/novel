export interface WritingStyle {
  perspective?: string;
  language_style?: string;
  pace?: string;
  tone?: string;
  custom_note?: string;
}

export interface GenerationConfig {
  chapter_target_words?: number;
  content_rating?: string;
}

export interface Project {
  id: string;
  type: "long";
  title: string;
  description: string;
  writing_style?: WritingStyle;
  generation_config?: GenerationConfig;
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelConfig {
  id: string;
  name: string;
  base_url: string;
  model: string;
  is_default: boolean;
  level?: string;
  embedding_model?: string;
  embedding_dimension?: number;
}

export interface UserSettings {
  recursive_limit: number;
  theme: string;
  assistant_summary_threshold: number;
  assistant_max_summaries: number;
  assistant_summary_max_length: number;
  assistant_history_recent_messages: number;
  assistant_history_top_k: number;
  content_rating: string;
  chapter_target_words: number;
}

export interface AssistantSession {
  id: string;
  project_id: string | null;
  title: string;
  is_active: boolean;
  staged_changes: any[];
  summaries: any[];
  message_count: number;
  updated_at: string | null;
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
  stage?: string;
}

export interface AutoAppliedItem {
  entity_id: string;
  entity_type: string;
  fields: string[];
  notes: string[];
}

export interface Chapter {
  id: string;
  project_id: string;
  title: string;
  content: string;
  order: number;
  detailed_outline?: string;
  status?: string;
}

export interface CharacterMemory {
  id: string;
  project_id: string;
  character_id: string;
  content: string;
  importance: "core" | "major" | "minor";
  ttl: "permanent" | "long" | "arc" | "scene";
  source_chapter_id: string | null;
  source_type: "auto" | "manual";
  related_character_ids: string[];
  related_foreshadow_ids: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface CharacterMemoryDraft {
  id: string;
  project_id: string;
  chapter_id: string;
  character_id: string;
  action: "add" | "update" | "delete";
  target_memory_id: string | null;
  content: string;
  importance: "core" | "major" | "minor";
  ttl: "permanent" | "long" | "arc" | "scene";
  related_character_ids: string[];
  related_foreshadow_ids: string[];
  created_at: string | null;
}

export type OutlineType = "broad" | "period" | "volume";

export interface OutlinePayloadBase {
  project_id?: string;
  title?: string;
  content?: string;
  type?: OutlineType;
  parent_id?: string | null;
  order?: number;
  chapter_start?: number | null;
  chapter_end?: number | null;
  version_chain?: string | null;
}

export interface CreateOutlinePayload extends OutlinePayloadBase {
  project_id: string;
}

export interface UpdateOutlinePayload extends OutlinePayloadBase {
  project_id?: string;
}

export interface OutlineNode {
  id: string;
  project_id: string;
  parent_id: string | null;
  type: OutlineType;
  title: string;
  content: string;
  order: number;
  chapter_start?: number | null;
  chapter_end?: number | null;
  version_chain?: string | null;
  children?: OutlineNode[];
}

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  metadata?: {
    intent?: string;
    change_record_ids?: string[];
    auto_applied?: AutoAppliedItem[];
    status?: "applied" | "rejected" | "partial";
    applied_count?: number;
    rejected_count?: number;
    error_count?: number;
  };
  created_at: string | null;
}
