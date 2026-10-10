export type User = { id: string; username?: string; email?: string | null; name: string; role: 'Engineer' | 'Reviewer' | 'Admin'; managed?: boolean };
export const COLUMN_TYPES = ['bigint', 'integer', 'decimal(18,2)', 'double', 'varchar', 'boolean', 'date', 'timestamp'] as const;
/** `date_formats`: định dạng strptime thử lần lượt cho cột ngày ghi nhiều kiểu; không có thì ép kiểu nghiêm. */
export type ColumnRule = { from: string; as: string; type: typeof COLUMN_TYPES[number]; date_formats?: string[] };

/** Định dạng ngày gợi ý từ dòng mẫu. DD/MM trước MM/DD theo cách ghi ở Việt Nam (MM/DD không gợi ý). */
export const DATE_FORMATS = ['%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y', '%Y-%m-%d %H:%M:%S'] as const;
const DATE_FORMAT_TEXT = /^[%A-Za-z0-9 :/\-.,T_]{2,40}$/;

/** Giá trị có đọc được theo định dạng strptime không (%Y %m %d %H %M %S), cùng luật với backend: bỏ khoảng trắng hai đầu. */
export function matchesDateFormat(value: string, format: string): boolean {
  const parts: string[] = [];
  const pattern = format.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    .replace(/%([YmdHMS])/g, (_, d: string) => { parts.push(d); return d === 'Y' ? '(\\d{4})' : '(\\d{1,2})'; });
  const match = new RegExp(`^${pattern}$`).exec(value.trim());
  if (!match) return false;
  const get = (d: string, missing: number) => { const i = parts.indexOf(d); return i < 0 ? missing : Number(match[i + 1]); };
  const [y, m, d] = [get('Y', 2000), get('m', 1), get('d', 1)];
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d
    && get('H', 0) < 24 && get('M', 0) < 60 && get('S', 0) < 60;
}

/** Các định dạng trong DATE_FORMATS khớp ít nhất một giá trị mẫu, theo thứ tự gợi ý. */
export function detectDateFormats(values: (string | null | undefined)[]): string[] {
  const present = values.filter((v): v is string => typeof v === 'string' && v.trim() !== '');
  return DATE_FORMATS.filter(format => present.some(value => matchesDateFormat(value, format)));
}
export type Source = { table: 'sales_raw.orders'; columns: { name: string; type: string }[]; max_rows: number };
export type RunRequest = { title: string; table: 'sales_raw.orders'; columns: ColumnRule[] };
/** Bản rút gọn của `RunReport` (src/run_report/models.py) — hợp đồng của sổ bằng chứng. */
export type RejectedRow = { row: number; reason: string; columns: string[]; values: Record<string, string | null> };
export type StagingObserved = {
  bronze: number; staging: number; duplicates?: number; rejected: number; reject_pct: number; max_reject_pct?: number;
  reject_reasons: Record<string, number>;
  // Run làm sạch Postgres có output_*; run do agent tạo có mẫu gold ở gate post_run và reject_samples dạng {_reject_reason, ...giá trị thô}.
  reject_samples: (RejectedRow | Record<string, unknown>)[]; output_schema?: { name: string; type: string }[]; output_samples?: Record<string, string | null>[];
};
/** Spec do agent sinh (src/spec/models.py); chỉ khai báo các trường giao diện đọc. */
export type AgentSpec = {
  domain: string;
  sources: { id: string; kind: string; source_ref: string; table?: string; endpoint?: string; path_pattern?: string;
    primary_key: string[]; cursor_field: string | null; write_disposition: string; target: { table: string };
    staging?: { columns: { from: string; as: string; type: string }[]; reject_if?: string[] } }[];
  transform: { model_name: string; grain: string[]; metrics: { name: string; expr: string }[]; filters: string[];
    joins?: { source: string; type: string; on: [string, string][] }[];
    unique_key?: string[]; partition_by?: string | null; tests?: { column: string; test: string }[];
    time_grain?: { column: string; unit: TimeUnit } | null };
  schedule: { cron: string };
};
export type TimeUnit = 'day' | 'week' | 'month' | 'quarter' | 'year';

const strings = (v: unknown) => Array.isArray(v) && v.every(x => typeof x === 'string');
/** Spec đủ các trường giao diện đọc. Run dừng ở cổng kiểm tra (NEEDS_HUMAN, BLOCKED_*) có thể lưu Spec dở dang của
 *  LLM, ví dụ nguồn thiếu primary_key: khi đó giao diện dùng kế hoạch (`plan`) thay vì Spec. */
export function isAgentSpec(spec: unknown): spec is AgentSpec {
  if (!spec || typeof spec !== 'object' || !('sources' in spec) || !('transform' in spec)) return false;
  const { sources, transform: t, schedule } = spec as Partial<AgentSpec>;
  return Array.isArray(sources) && sources.length > 0
    && sources.every(s => !!s && typeof s.id === 'string' && typeof s.source_ref === 'string' && strings(s.primary_key) && typeof s.write_disposition === 'string')
    && !!t && typeof t.model_name === 'string' && strings(t.grain) && strings(t.filters)
    && Array.isArray(t.metrics) && t.metrics.every(m => !!m && typeof m.name === 'string' && typeof m.expr === 'string')
    && typeof schedule?.cron === 'string';
}
export type AgentPlan = {
  domain?: string; summary?: string; model_name?: string; schedule?: string; question?: string | null;
  sources: { id: string; source_ref: string; object: string; purpose?: string }[]; metrics?: { name: string; definition?: string }[];
  joins?: { left: string; right: string; type: 'left' | 'inner' }[]; grain?: string[]; filters?: string[];
  concepts?: { term: string; column?: string | null }[];
};
export type Approval = { decision: string; reviewer: string; files_hash: string; version: number; at: string; comment: string };
/** Pull request và tiến độ do webhook / deployer ghi (src/run_report/models.py). */
export type Deployment = {
  repo: string; branch: string; pr_number: number; pr_url: string; head_sha: string;
  state: 'open' | 'merged' | 'closed'; opened_at: string;
  ci_conclusion?: string | null; ci_url?: string | null; ci_at?: string | null;
  merged_at?: string | null; merge_commit_sha?: string | null; closed_at?: string | null;
  deployed_commit_sha?: string | null; deployed_at?: string | null;
};
export type OptimizationVerdict = {
  improved: boolean; reason?: string; label?: string;
  comparison?: { rule: string; before?: unknown; after?: unknown; better: boolean; gain_pct?: number; detail?: string }[];
};
export type RunReport = {
  run_id: string; version: number; domain: string; created_by: string; created_at: string; user_request: string;
  trigger?: 'user_request' | 'manual_optimize' | 'regression'; parent_run_id?: string | null;
  status: string; stage_reached: string;
  // Run làm sạch thủ công lưu body RunRequest; run do agent tạo lưu Spec đầy đủ.
  spec: RunRequest | AgentSpec | null; plan?: AgentPlan | null;
  // spec_history cũng là dict tự do: agent ghi {attempt, errors}, run mẫu cũ ghi {version, reason}.
  spec_history?: { attempt?: number; errors?: string[]; version?: number; reason?: string; deployment?: Deployment | null }[];
  files?: Record<string, string>; files_hash?: string | null;
  gates: { name: string; result: 'pass' | 'fail'; detail: string; observed: Record<string, unknown> }[];
  checks: { name: string; source_id: string | null; result: 'pass' | 'fail'; observed: Record<string, unknown> }[];
  cost?: { usd: number; llm_usd?: number }; attempts?: { spec: number; sql: number }; llm_calls?: number;
  // Mỗi lần gọi LLM: token nhà cung cấp báo, usd theo budgets.yml (null = model chưa có giá).
  llm_usage?: { stage: string; tier: string; model: string | null; input_tokens: number | null; output_tokens: number | null; usd: number | null }[];
  timing: { baseline_sec: number | null; optimized_sec: number | null };
  // error_trace là dict tự do trong hợp đồng: agent ghi `error`, run mẫu cũ ghi `message`.
  error_trace: { stage: string; error?: string; message?: string; source_id?: string; question?: string; need_tables?: string[] }[];
  /** Nguồn và bảng người dùng chọn khi tạo run; null = agent chọn trong mọi nguồn. */
  source_scope?: { source_ref: string; tables: string[] }[] | null; approval: Approval | null;
  deployment?: Deployment | null;
  regression?: { verdict?: OptimizationVerdict; [key: string]: unknown } | null;
  review_history?: Approval[];
  timeline?: { event: string; at: string; actor?: string | null }[];
};
export function stagingOf(report: RunReport): StagingObserved | null {
  const check = report.checks.find(item => item.name === 'staging_reconcile');
  return check ? check.observed as StagingObserved : null;
}
export function goldPreview(report: RunReport): Record<string, string | null>[] {
  const gate = report.gates.find(item => item.name === 'post_run');
  return (gate?.observed.gold_samples as Record<string, string | null>[] | undefined) ?? [];
}
export function runError(report: RunReport): string | null {
  const last = report.error_trace.at(-1);
  return last ? last.error ?? last.message ?? null : null;
}
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
export function pipelineRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return apiRequest<T>(`/pipeline${path}`, init);
}
export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init, credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch { throw new ApiError(0, 'Không kết nối được backend. Kiểm tra server rồi thử lại.'); }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    throw new ApiError(response.status, typeof detail === 'string' ? detail :
      Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('; ') : 'Backend chưa sẵn sàng. Vui lòng thử lại.');
  }
  if (body === null) throw new ApiError(response.status, 'Backend trả về dữ liệu không hợp lệ.');
  return body as T;
}
export function defaultColumns(source: Source): ColumnRule[] {
  return source.columns.map(column => ({
    from: column.name,
    as: column.name === 'amount_text' ? 'amount' : column.name === 'ordered_at_text' ? 'ordered_at' : column.name,
    type: column.name === 'amount_text' ? 'decimal(18,2)' : column.name === 'ordered_at_text' ? 'date' :
      column.type === 'integer' ? 'integer' : 'varchar',
  }));
}
export function validateColumns(columns: ColumnRule[]): string | null {
  if (!columns.length) return 'Chọn ít nhất một cột.';
  if (columns.some(column => !/^[a-z][a-z0-9_]{0,62}$/.test(column.as)))
    return 'Tên cột phải bắt đầu bằng chữ thường, chỉ gồm chữ thường, số và dấu gạch dưới (tối đa 63 ký tự).';
  if (new Set(columns.map(column => column.as)).size !== columns.length) return 'Tên cột đầu ra không được trùng.';
  for (const column of columns) {
    if (!column.date_formats?.length) continue;
    if (column.type !== 'date' && column.type !== 'timestamp') return `Cột ${column.as}: định dạng ngày chỉ dùng cho date / timestamp.`;
    if (column.date_formats.some(f => !f.includes('%') || !DATE_FORMAT_TEXT.test(f)))
      return `Cột ${column.as}: định dạng ngày phải có chỉ thị như %Y, %m, %d và chỉ gồm chữ, số, dấu cách / - . : ,`;
  }
  return null;
}
