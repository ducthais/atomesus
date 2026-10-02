/**
 * Diễn giải một run cho người không đọc code: pipeline đọc gì, làm sạch thế nào, bảng kết quả mỗi dòng là gì,
 * và các dấu hiệu kết quả khó dùng. Chỉ dựa trên Spec và số đo trong RunReport (hàm thuần, có test).
 */
import type { AgentSpec, RunReport, TimeUnit } from '../postgres-api.ts';

export type ResultSource = {
  id: string; table: string; sourceRef: string;
  columns: { from: string; as: string; type: string }[]; rejectIf: string[];
  rows: { source: number | null; bronze: number | null; staging: number | null; rejected: number | null; duplicates: number | null };
};
export type RunResult = {
  model: string; goldTable: string; goldRows: number | null; sample: Record<string, string | null>[];
  grain: string[]; timeGrain: { column: string; unit: TimeUnit } | null; metrics: { name: string; expr: string }[]; filters: string[];
  joins: { source: string; type: string; on: [string, string][] }[];
  sources: ResultSource[]; tests: { passed: number; failed: number } | null;
};

const num = (v: unknown) => typeof v === 'number' ? v : null;
const bare = (column: string) => column.split('.').at(-1)!;

/** RunReport → kết quả diễn giải được; null khi run dừng trước khi có Spec (không có gì để giải thích). */
export function runResult(report: RunReport): RunResult | null {
  const spec = report.spec;
  if (!spec || !('sources' in spec) || !('transform' in spec)) return null;
  const s = spec as AgentSpec;
  const post = report.gates.find(g => g.name === 'post_run');
  const dbt = report.checks.find(c => c.name === 'dbt_test');
  return {
    model: s.transform.model_name, goldTable: `gold.${s.transform.model_name}`,
    goldRows: num(post?.observed.gold_rows), sample: (post?.observed.gold_samples as Record<string, string | null>[] | undefined) ?? [],
    grain: s.transform.grain.map(bare), timeGrain: s.transform.time_grain ? { column: bare(s.transform.time_grain.column), unit: s.transform.time_grain.unit } : null, metrics: s.transform.metrics, filters: s.transform.filters, joins: s.transform.joins ?? [],
    tests: dbt ? { passed: Number(dbt.observed.passed ?? 0), failed: Number(dbt.observed.failed ?? 0) } : null,
    sources: s.sources.map(src => {
      const rows = report.checks.find(c => c.name === 'row_reconcile' && c.source_id === src.id)?.observed ?? {};
      const staged = report.checks.find(c => c.name === 'staging_reconcile' && c.source_id === src.id)?.observed ?? {};
      return {
        id: src.id, table: src.table ?? src.endpoint ?? src.path_pattern ?? src.id, sourceRef: src.source_ref,
        columns: src.staging?.columns ?? [], rejectIf: src.staging?.reject_if ?? [],
        rows: { source: num(rows.source), bronze: num(rows.bronze), staging: num(staged.staging), rejected: num(staged.rejected), duplicates: num(staged.duplicates) },
      };
    }),
  };
}

const TYPE_LABEL: Record<string, string> = {
  integer: 'số nguyên', bigint: 'số nguyên', double: 'số thực', varchar: 'chuỗi', boolean: 'đúng / sai', date: 'ngày', timestamp: 'thời điểm (ngày giờ)',
};
export const PERIOD_LABEL: Record<TimeUnit, string> = { day: 'ngày', week: 'tuần', month: 'tháng', quarter: 'quý', year: 'năm' };

export const typeLabel = (type: string) => type.startsWith('decimal') ? 'số thập phân' : TYPE_LABEL[type] ?? type;

/** Biểu thức chỉ số → tiếng Việt. Không nhận ra thì trả null để giao diện hiện nguyên biểu thức. */
export function describeExpr(expr: string): string | null {
  const e = expr.trim();
  const inner = (m: RegExpMatchArray) => bare(m[1].trim());
  let m: RegExpMatchArray | null;
  if (/^COUNT\(\s*\*\s*\)$/i.test(e)) return 'số dòng';
  if ((m = e.match(/^COUNT\(\s*DISTINCT\s+([\w.]+)\s*\)$/i))) return `số ${inner(m)} khác nhau`;
  if ((m = e.match(/^COUNT\(\s*([\w.]+)\s*\)$/i))) return `số dòng có ${inner(m)}`;
  if ((m = e.match(/^SUM\(\s*([\w.]+)\s*\)$/i))) return `tổng ${inner(m)}`;
  if ((m = e.match(/^AVG\(\s*([\w.]+)\s*\)$/i))) return `trung bình ${inner(m)}`;
  if ((m = e.match(/^MIN\(\s*([\w.]+)\s*\)$/i))) return `${inner(m)} nhỏ nhất`;
  if ((m = e.match(/^MAX\(\s*([\w.]+)\s*\)$/i))) return `${inner(m)} lớn nhất`;
  return null;
}

/** Dấu hiệu kết quả khó dùng, viết cho người đọc nghiệp vụ, kèm gợi ý sửa yêu cầu. */
export function resultWarnings(result: RunResult): string[] {
  const warnings: string[] = [];
  const types = new Map(result.sources.flatMap(s => s.columns.map(c => [c.as, c.type] as const)));
  const timestamps = result.grain.filter(g => types.get(g) === 'timestamp' && result.timeGrain?.column !== g);
  if (timestamps.length) {
    warnings.push(`Bảng kết quả đang gộp theo ${timestamps.join(', ')} là thời điểm chính xác đến từng giây, nên gần như mỗi giao dịch thành một dòng riêng. Muốn xem theo ngày hoặc tháng, hãy ghi rõ trong yêu cầu, ví dụ "doanh thu theo tháng".`);
  }
  const largest = Math.max(0, ...result.sources.map(s => s.rows.staging ?? s.rows.bronze ?? 0));
  if (!timestamps.length && result.grain.length && result.goldRows !== null && largest > 100 && result.goldRows >= largest * 0.5) {
    warnings.push(`Kết quả có ${result.goldRows.toLocaleString('vi-VN')} dòng từ ${largest.toLocaleString('vi-VN')} dòng gốc: dữ liệu gần như không được gộp lại. Kiểm tra cột dùng để nhóm (${result.grain.join(', ')}) có đúng ý không.`);
  }
  if (result.goldRows === 0) warnings.push('Bảng kết quả rỗng: bộ lọc có thể đã loại hết dữ liệu.');
  return warnings;
}
