export type User = { id: string; name: string; role: 'Engineer' | 'Reviewer' };
export const COLUMN_TYPES = ['bigint', 'integer', 'decimal(18,2)', 'double', 'varchar', 'boolean', 'date', 'timestamp'] as const;
export type ColumnRule = { from: string; as: string; type: typeof COLUMN_TYPES[number] };
export type Source = { table: 'sales_raw.orders'; columns: { name: string; type: string }[]; max_rows: number };
export type RunRequest = { title: string; table: 'sales_raw.orders'; columns: ColumnRule[] };
export type RunSummary = {
  run_id: string; title: string; version: number; created_by: string;
  status: 'SUCCEEDED' | 'NEEDS_HUMAN' | 'FAILED'; started_at: string; finished_at: string;
  duration_ms: number; rows_read: number; rows_written: number; rows_rejected: number;
  output_schema: { name: string; type: string }[]; error: string | null; approval: null; spec_hash: string;
};
export type RunReport = RunSummary & {
  spec: RunRequest; output: Record<string, string | null>[];
  rejected: { row: number; reason: string; columns: string[]; values: Record<string, string | null> }[];
};
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
export async function pipelineRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1/pipeline${path}`, {
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
