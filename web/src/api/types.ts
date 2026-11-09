export interface Settings {
  model_provider: string;
  embedding_provider: string;
  persona_id: string;
  spontaneous_probability: number;
  spontaneous_limit: number;
  learning_enabled: boolean;
  learning_hours: number;
  learning_min_messages: number;
  learning_batch_size: number;
  collect_enabled: boolean;
  collect_groups: string[];
  meme_enabled: boolean;
  meme_cooldown: number;
  meme_repeat_cooldown: number;
  recall_count: number;
  context_budget: number;
  memory_budget: number;
  digest_enabled: boolean;
  verify_enabled: boolean;
  glossary_search: boolean;
  glossary_per_run: number;
  split_probability: number;
  stale_seconds: number;
  debounce_enabled: boolean;
  debounce_seconds: number;
  debounce_max_seconds: number;
  engaged_seconds: number;
  skills_enabled: boolean;
  sandbox_url: string;
}
export interface Providers { chat: string[]; embedding: string[] }
export interface Configuration { settings: Settings; providers: Providers }

export interface PersonaVersion { id: number; text: string; reason: string; evidence: string; created: number }
export interface Persona { fixed: string; learned: string; active: number; versions: PersonaVersion[] }

export interface LearningDiagnostics {
  mode: 'preview' | 'apply' | 'legacy';
  decision: string;
  reason: string;
  base_version?: number;
  original_style?: string;
  candidate?: string | null;
  expressions_eligible?: number;
  expressions_added?: number;
  expressions_skipped?: number;
  sync?: string;
  new_version?: number | null;
  evidence?: unknown[];
}
export interface LearningRun {
  id: number; status: 'success' | 'failed' | 'running' | 'interrupted';
  started: number; finished: number | null; sample_count: number; detail: string;
  diagnostics: LearningDiagnostics;
}

export interface RunStats {
  messages?: number;
  accepted?: Record<string, number>;
  rejected?: Record<string, number>;
  outcomes?: Record<string, number>;
  [key: string]: unknown;
}
export interface Run {
  id: number; kind: string; source: string; status: string; started: number;
  finished: number | null; stats: RunStats; detail: string; undone: number;
  changes?: { tbl: string; action: string; n: number }[];
}

export interface Status {
  counts: Record<string, number>;
  last_message: number | null; last_memory: number | null; last_learning: number | null;
  last_digest: number | null; last_consolidation: number | null;
  last_collection: number | null; last_persona_update: number | null;
  next_learning_check: number | null;
  pending_samples: number; undigested: number;
  learning_running: boolean; digest_running: boolean; consolidation_running: boolean;
  active_persona: number | null;
  persona_sync: { status: string; version?: number; at: number } | null;
  last_error: { stage: string; type: string; at: number } | null;
  runs: (Omit<Run, 'stats'> & { stats: string })[];
  learning_runs: LearningRun[];
}

export interface Nickname {
  id: number; user_id: string; name: string; scope: string; origin: string;
  confidence: number; status: string; verified: number; evidence: string; updated: number;
}
export interface Person {
  id: string; account: string; name: string; aliases: string; kind: 'human' | 'bot';
  first_seen: number; updated: number; memory_count?: number; last_seen?: number | null;
  nicknames?: Nickname[];
}
export interface Profile { subject: string; kind: string; summary: string; relation: string; verified: number; updated: number }
export interface PersonDetail {
  person: Person; nicknames: Nickname[]; profile: Profile | null;
  sources: { source: string; n: number; last: number }[];
  interactions: { replies: number; last: number | null };
  memories: Memory[];
}
export interface Group { source: string; name: string; n: number; last: number; profile: Profile | null }

export interface Memory {
  id: number; kind: string; subject: string; term: string; source: string;
  text: string; quote: string; evidence: string; participants: string; importance: number;
  confidence: number; certainty: string; status: string; verified: number;
  valid_until: number | null; origin: string; run: string; superseded_by: number | null;
  recalls: number; last_recalled: number | null; created: number; updated: number;
}

export interface ChatLine { id: string; speaker: string; source: string; group: string; created: number; text: string; cited: boolean; gap: boolean }
export interface ReviewPerson { id: string; name: string; account: string; names: string[]; bot: boolean }
export interface ReviewMemory { id: number; text: string; origin: string; verified: number; status: string; updated: number }
export interface ReviewItem {
  id: number; kind: string; target: string; reason: string; text: string | null; created: number;
  person: ReviewPerson | null; chat: ChatLine[]; uses: ChatLine[]; memories: ReviewMemory[];
  links: string[]; quote: string; old: string;
}

export interface TeachSession { id: number; title: string; created: number; updated: number; pending?: number }
export interface TeachMessage { id: number; session_id: number; role: 'admin' | 'assistant'; text: string; created: number }
export interface Proposal {
  id: number; session_id: number; kind: string; status: 'pending' | 'accepted' | 'rejected' | 'invalid';
  result: string; created: number; payload: Record<string, unknown>;
}
export interface TeachThread { session: TeachSession; messages: TeachMessage[]; proposals: Proposal[] }

export interface Expression { id: number; scope: string; situation: string; phrase: string; enabled: number; uses: number; updated: number }
export interface Digest { id: number; source: string; start: number; end: number; count: number; summary: string; run: string }

export interface Meme { id: number; title: string; tags: string; source: string; enabled: number | boolean }
export type Editing = { kind: 'memory'; row: Memory } | { kind: 'meme'; row: Meme };

export interface ToolStep { name: string; args: string; result: string; at: number }
export interface Episode {
  id: number; source: string; user_id: string; user_name: string; account: string;
  message_id: string; question: string; tools: ToolStep[]; answer: string;
  outcome: 'ok' | 'timeout' | 'error'; skills: number[]; duration: number;
  verdict: string; lesson: string; reviewed: number; created: number; follow_up: string;
}
export interface Skill {
  id: number; title: string; trigger: string; steps: string; status: 'active' | 'retired';
  version: number; uses: number; wins: number; losses: number; last_used: number | null;
  created: number; updated: number;
}
export interface SkillVersion {
  id: number; skill_id: number; version: number; title: string; trigger: string; steps: string;
  status: string; reason: string; episodes: number[]; origin: string; created: number;
}
export interface SkillDetail extends Skill { versions: SkillVersion[]; episodes: Episode[] }

export interface PromptEntry {
  key: string; group: string; title: string; note: string; text: string; default: string;
  placeholders: string[]; modified: boolean; updated: number | null;
}
export interface PromptVersion { id: number; text: string; note: string; created: number; reset: boolean }
export interface PromptTrialReply { text: string; form?: string; reason?: string; seconds?: number; error?: string }
export interface PromptTrialRow {
  time: number; speaker: string; said: string; original: string[];
  saved: PromptTrialReply; draft: PromptTrialReply;
}
