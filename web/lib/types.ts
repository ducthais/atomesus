// Hợp đồng với FastAPI (api/main.py). Giữ đúng tên trường của RunReport (SPEC_1.md mục 8.2).

export const STAGE_IDS = [
  'intake', 'plan', 'confirm', 'resolve', 'propose', 'arbitrate', 'render', 'static', 'budget',
  'el_run', 'el_validate', 't_run', 't_validate', 'optimize', 'report', 'review', 'deploy', 'persist',
] as const;
export type StageId = (typeof STAGE_IDS)[number];

export type EventStatus = 'running' | 'pass' | 'fail' | 'escalate' | 'blocked';

export type RunStatus =
  | 'DRAFT' | 'PLAN_CONFIRMED' | 'GENERATING' | 'VALIDATING' | 'READY_FOR_REVIEW' | 'APPROVED' | 'DEPLOYED'
  | 'NEEDS_HUMAN' | 'BLOCKED_SEMANTIC_CONFLICT' | 'BLOCKED_BUDGET' | 'BLOCKED_AST' | 'REJECTED'
  | 'INFRA_ERROR' | 'INTERNAL_ERROR' | 'DEPLOY_FAILED';

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export interface GateResult {
  name: string;
  result: 'pass' | 'fail' | 'escalate';
  detail: string;
  observed: JsonObject;
}

export interface RunEvent {
  run_id: string;
  stage: StageId;
  status: EventStatus;
  message: string;
  payload: JsonObject;
  seq: number;
  at: string;
}

export interface PlanMetric { name: string; formula: string }
export interface Plan {
  source_table: string | null;
  clarifying_question: string | null;
  schedule?: string;
  grain?: string[];
  metrics?: PlanMetric[];
  dedupe_key?: string[];
  cursor_field?: string | null;
  write_mode?: string;
}

export interface Attempts { spec: number; sql: number; el: number }
export interface Caps { spec: number; sql: number; llm: number }

export interface Cost {
  estimated_bytes: number | null;
  actual_bytes: number | null;
  usd: number | null;
  is_estimate: boolean;
  engine: 'duckdb' | 'bigquery';
}

export interface OptimizerEntry {
  rule: string;
  applied: boolean;
  before_sec: number | null;
  after_sec: number | null;
  reason: string;
}

export interface ErrorEntry { stage: StageId; kind: string; message: string; at: string; route: string }
export interface Approval { reviewer: string; hash: string; approved_at: string; comment: string }
export interface Decision { action: 'approve' | 'reject' | 'request_changes'; actor: string; comment: string; at: string; version: number }

export interface RunView {
  run_id: string;
  created_at: string;
  updated_at: string;
  created_by: string;
  domain: string;
  user_request: string;
  status: RunStatus;
  stage_reached: StageId;
  version: number;
  plan: Plan | null;
  spec: JsonObject | null;
  spec_history: JsonObject[];
  files: Record<string, string>;
  files_hash: string | null;
  gates: GateResult[];
  checks: GateResult[];
  cost: Cost;
  timing: { baseline_sec: number | null; optimized_sec: number | null };
  optimizer: OptimizerEntry[];
  arbitration: JsonObject | null;
  attempts: Attempts;
  llm_calls: number;
  llm_provider: string;
  error_trace: ErrorEntry[];
  approval: Approval | null;
  decisions: Decision[];
  provenance: { template_id: number | null; template_reviewer: string | null };
  gold_preview: JsonObject[];
  deployed_paths: string[];
  ticket: JsonObject | null;
  caps: Caps;
  awaiting: 'confirm_plan' | 'answer' | null;
  terminal: boolean;
}

export interface RunSummary {
  run_id: string;
  title: string;
  request: string;
  domain: string;
  created_by: string;
  status: RunStatus;
  stage_reached: StageId;
  version: number;
  updated_at: string;
  created_at: string;
  usd: number | null;
  reviewer: string | null;
}

export interface Explanation {
  title: string;
  summary: string;
  bullets: string[];
  action: { kind: string; label: string } | null;
}

export interface PolicyResponse { files: Record<string, string>; limits: Caps }
export interface FilesResponse { version: number; files: Record<string, string>; previous_version: number | null; previous: Record<string, string> }
export interface BudgetToday { spent_usd: number; max_usd: number; is_estimate: boolean }
export interface CatalogTable { table: string; domain: string | null; description: string; rows: number }

export type Role = 'Engineer' | 'Reviewer';
export interface Viewer { role: Role; user: string }
