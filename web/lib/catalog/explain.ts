/**
 * Diễn giải một run cho người không đọc code: pipeline đọc gì, làm sạch thế nào, bảng kết quả mỗi dòng là gì,
 * và các dấu hiệu kết quả khó dùng. Chỉ dựa trên Spec và số đo trong RunReport (hàm thuần, có test).
 */
import { isAgentSpec, type RunReport, type TimeUnit } from '../postgres-api.ts';

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
  /** Đối chiếu độc lập với nguồn (check metric_reconcile); null khi chưa chạy, kèm lý do nếu không đối chiếu được. */
  reconcile: Reconcile | null; reconcileSkipped: string | null;
  /** Múi giờ phiên DuckDB lúc chạy thử (cấu hình PIPELINE_WAREHOUSE_TIMEZONE). */
  warehouseTimezone: string | null;
};

type Key = Record<string, string | null>;
export type Reconcile = {
  matched: boolean; rows: { gold: number; source: number }; matchedRows: number;
  mismatchCount: number; missingCount: number; extraCount: number;
  mismatched: { key: Key; metric: string; gold: string | null; source: string | null }[];
  missingInGold: Key[]; extraInGold: Key[]; totals: Record<string, { gold: string; source: string }>; sql: string;
  /** Mọi dòng lệch đều là cùng thời điểm bị dịch đúng N giờ (dấu hiệu lệch múi giờ). */
  shiftHours: number | null;
};

/** observed của check metric_reconcile (snake_case từ backend) → Reconcile. */
export function toReconcile(o: Record<string, unknown>, matched: boolean): Reconcile {
  const rows = (o.rows ?? {}) as { gold?: number; source?: number };
  return {
    matched, rows: { gold: rows.gold ?? 0, source: rows.source ?? 0 }, matchedRows: Number(o.matched ?? 0),
    mismatchCount: Number(o.mismatch_count ?? 0), missingCount: Number(o.missing_count ?? 0), extraCount: Number(o.extra_count ?? 0),
    mismatched: (o.mismatched as Reconcile['mismatched'] | undefined) ?? [],
    missingInGold: (o.missing_in_gold as Key[] | undefined) ?? [], extraInGold: (o.extra_in_gold as Key[] | undefined) ?? [],
    totals: (o.totals as Reconcile['totals'] | undefined) ?? {}, sql: String(o.sql ?? ''),
    shiftHours: typeof o.shift_hours === 'number' ? o.shift_hours : null,
  };
}

const num = (v: unknown) => typeof v === 'number' ? v : null;
const bare = (column: string) => column.split('.').at(-1)!;

/** RunReport → kết quả diễn giải được; null khi run dừng trước khi có Spec (không có gì để giải thích). */
export function runResult(report: RunReport): RunResult | null {
  const spec = report.spec;
  if (!isAgentSpec(spec)) return null;
  const s = spec;
  const post = report.gates.find(g => g.name === 'post_run');
  const dbt = report.checks.find(c => c.name === 'dbt_test');
  const reconcile = report.checks.find(c => c.name === 'metric_reconcile');
  return {
    model: s.transform.model_name, goldTable: `gold.${s.transform.model_name}`,
    goldRows: num(post?.observed.gold_rows), sample: (post?.observed.gold_samples as Record<string, string | null>[] | undefined) ?? [],
    grain: s.transform.grain.map(bare), timeGrain: s.transform.time_grain ? { column: bare(s.transform.time_grain.column), unit: s.transform.time_grain.unit } : null, metrics: s.transform.metrics, filters: s.transform.filters, joins: s.transform.joins ?? [],
    tests: dbt ? { passed: Number(dbt.observed.passed ?? 0), failed: Number(dbt.observed.failed ?? 0) } : null,
    reconcile: reconcile ? toReconcile(reconcile.observed, reconcile.result === 'pass') : null,
    reconcileSkipped: typeof post?.observed.reconcile_skipped === 'string' ? post.observed.reconcile_skipped : null,
    warehouseTimezone: typeof post?.observed.warehouse_timezone === 'string' ? post.observed.warehouse_timezone : null,
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

const WEEKDAY = ['Chủ nhật', 'thứ Hai', 'thứ Ba', 'thứ Tư', 'thứ Năm', 'thứ Sáu', 'thứ Bảy', 'Chủ nhật'];
const CRON_MACRO: Record<string, string> = {
  '@hourly': '0 * * * *', '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@weekly': '0 0 * * 0',
  '@monthly': '0 0 1 * *', '@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *',
};

/** Cron 5 trường → tiếng Việt ("mỗi ngày lúc 6:00", "mỗi giờ"...). Dạng chưa nhận ra thì trả null, không đoán. */
export function describeCron(cron: string): string | null {
  const fields = (CRON_MACRO[cron.trim()] ?? cron).trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute, hour, day, month, weekday] = fields;
  const int = (v: string, min: number, max: number) => /^\d+$/.test(v) && Number(v) >= min && Number(v) <= max ? Number(v) : null;
  const ints = (v: string, min: number, max: number) => { const all = v.split(',').map(x => int(x, min, max)); return all.every(x => x !== null) ? all as number[] : null; };
  // */n đếm lại từ 0 ở mỗi giờ / mỗi ngày (*/45 → phút 0 và 45): chỉ gọi "mỗi n phút/giờ" khi n chia hết 60 / 24,
  // không thì liệt kê các mốc chạy thật.
  const step = (v: string, period: number) => {
    const n = Number(v.match(/^\*\/(\d+)$/)?.[1] ?? 0);
    return n > 0 ? { n, values: Array.from({ length: Math.ceil(period / n) }, (_, i) => i * n), even: n < period && period % n === 0 } : null;
  };
  const minuteStep = step(minute, 60), hourStep = step(hour, 24);
  const mins = minute === '*' ? null : minuteStep?.values ?? ints(minute, 0, 59);
  const m = mins?.length === 1 ? mins[0] : null;
  const hours = hourStep?.values ?? ints(hour, 0, 23);
  const at = m !== null && hours ? `lúc ${hours.map(h => `${h}:${String(m).padStart(2, '0')}`).join(', ')}` : null;
  if (month !== '*') return null;
  if (day !== '*') {
    const d = int(day, 1, 31);
    return d !== null && weekday === '*' && at ? `ngày ${d} hằng tháng ${at}` : null;
  }
  if (weekday !== '*') {
    if (!at) return null;
    const range = weekday.match(/^(\d)-(\d)$/);
    if (range) {
      const [from, to] = [int(range[1], 0, 7), int(range[2], 0, 7)];
      return from !== null && to !== null && from < to ? `từ ${WEEKDAY[from]} đến ${WEEKDAY[to]} ${at}` : null;
    }
    const days = ints(weekday, 0, 7);
    return days ? `mỗi ${[...new Set(days.map(d => WEEKDAY[d]))].join(', ')} ${at}` : null;
  }
  if (hour === '*') {
    if (minute === '*' || minuteStep?.n === 1) return 'mỗi phút';
    if (minuteStep?.even) return `mỗi ${minuteStep.n} phút`;
    if (!mins) return null;
    return m === 0 ? 'mỗi giờ' : `mỗi giờ vào phút ${mins.join(', ')}`;
  }
  if (m !== null && hourStep?.even) return hourStep.n === 1 ? (m === 0 ? 'mỗi giờ' : `mỗi giờ vào phút ${m}`) : `mỗi ${hourStep.n} giờ${m ? ` vào phút ${m}` : ''}`;
  return at ? `mỗi ngày ${at}` : null;
}

const vi = (n: number, digits: number) => n.toLocaleString('vi-VN', { maximumFractionDigits: digits });

/**
 * Tóm tắt pipeline bằng câu thường cho người duyệt không đọc diff: đọc bảng nào, ra bảng gì, chạy khi nào, tốn bao nhiêu,
 * optimizer có nhanh hơn không. Chỉ dùng số đã có trong RunReport; thiếu dữ liệu thì bỏ câu đó.
 */
export function runSummary(report: RunReport): string[] {
  const spec = isAgentSpec(report.spec) ? report.spec : null;
  const plan = report.plan ?? null;
  const lines: string[] = [];

  // Một bảng = kết nối + tên: orders ở hai kết nối là hai bảng, và ghi kèm kết nối để người đọc phân biệt.
  const read = spec ? spec.sources.map(s => ({ ref: s.source_ref, name: s.table ?? s.endpoint ?? s.path_pattern ?? s.id }))
    : (plan?.sources ?? []).map(s => ({ ref: s.source_ref, name: s.object }));
  const tables = [...new Map(read.map(t => [`${t.ref}\u0000${t.name}`, t])).values()];
  const label = (t: { ref: string; name: string }) => tables.filter(o => o.name === t.name).length > 1 ? `${t.ref}.${t.name}` : t.name;
  if (tables.length) lines.push(`Đọc ${tables.length} bảng: ${tables.map(label).join(', ')}`);

  const model = spec?.transform.model_name ?? plan?.model_name;
  const goldRows = num(report.gates.find(g => g.name === 'post_run')?.observed.gold_rows);
  if (model) lines.push(`Ra bảng ${model}${goldRows !== null ? ` khoảng ${goldRows.toLocaleString('vi-VN')} dòng` : ''}`);

  const cron = (spec?.schedule.cron ?? plan?.schedule ?? '').trim();
  if (cron) lines.push(`Chạy theo lịch ${describeCron(cron) ?? `cron ${cron}`}`);

  // Cổng budget ghi ước tính của một lần chạy; bản optimizer giữ lại thêm một cổng budget mới nên lấy cổng cuối.
  const usd = num(report.gates.findLast(g => g.name === 'budget')?.observed.usd);
  if (usd !== null && usd >= 0) lines.push(`Chi phí ước tính ~$${usd.toFixed(6)} mỗi lần chạy`);

  // Mọi luật P13 đo so với cùng bản gốc; bản được giữ là bước applied nhanh nhất.
  const best = (report.optimizer ?? [])
    .filter(s => s.applied && typeof s.before_sec === 'number' && s.before_sec > 0 && typeof s.after_sec === 'number')
    .sort((a, b) => a.after_sec! - b.after_sec!)[0];
  if (best) {
    const [before, after] = [best.before_sec!, best.after_sec!];
    lines.push(`Optimizer: nhanh hơn ${vi(Math.round((before - after) * 1000 / before) / 10, 1)}% (${vi(before, 4)} s → ${vi(after, 4)} s)`);
  }
  return lines;
}
