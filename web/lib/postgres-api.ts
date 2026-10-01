export type User = { id: string; name: string; role: 'Engineer' | 'Reviewer' };
export const COLUMN_TYPES = ['bigint', 'integer', 'decimal(18,2)', 'double', 'varchar', 'boolean', 'date', 'timestamp'] as const;
export type ColumnRule = { from: string; as: string; type: typeof COLUMN_TYPES[number] };
export type Source = { table: 'sales_raw.orders'; columns: { name: string; type: string }[]; max_rows: number };
export type RunRequest = { title: string; table: 'sales_raw.orders'; columns: ColumnRule[] };
/** Bản rút gọn của `RunReport` (src/run_report/models.py) — hợp đồng của sổ bằng chứng. */
export type RejectedRow = { row: number; reason: string; columns: string[]; values: Record<string, string | null> };
export type StagingObserved = {
  bronze: number; staging: number; rejected: number; reject_pct: number; reject_reasons: Record<string, number>;
  // Run làm sạch Postgres có output_*; run do agent tạo có mẫu gold ở gate post_run và reject_samples dạng {_reject_reason, ...giá trị thô}.
  reject_samples: (RejectedRow | Record<string, unknown>)[]; output_schema?: { name: string; type: string }[]; output_samples?: Record<string, string | null>[];
};
export type RunReport = {
  run_id: string; version: number; domain: string; created_by: string; created_at: string; user_request: string;
  status: string; stage_reached: string; spec: RunRequest | null;
  gates: { name: string; result: 'pass' | 'fail'; detail: string; observed: Record<string, unknown> }[];
  checks: { name: string; source_id: string | null; result: 'pass' | 'fail'; observed: Record<string, unknown> }[];
  timing: { baseline_sec: number | null; optimized_sec: number | null };
  error_trace: { stage: string; error: string; source_id?: string }[]; approval: unknown | null;
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
  return report.error_trace.at(-1)?.error ?? null;
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
  return null;
}
