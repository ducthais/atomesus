/**
 * Sửa mapping của bản nháp (DRAFT) trước khi chạy thử: đổi tên / kiểu / định dạng ngày / thêm bớt cột staging,
 * và luật loại dòng. Đổi tên cột đầu ra thì cập nhật luôn các chỗ transform tham chiếu tới nó.
 * Hàm thuần (có test); backend vẫn kiểm tra lại toàn bộ Spec bằng các cổng của agent.
 */
import type { AgentSpec } from '../postgres-api.ts';

export type MappingColumn = { from: string; as: string; type: string; date_formats?: string[] | null; currency?: unknown; keep: boolean };
export type SourceMapping = { id: string; columns: MappingColumn[]; rejectIf: string[] };

const OUTPUT = /^[a-z][a-z0-9_]{0,62}$/;
export const COLUMN_TYPE = /^(bigint|integer|double|varchar|boolean|date|timestamp|decimal\(\d{1,2},\d{1,2}\))$/;

/** Gợi ý tên và kiểu đầu ra cho một cột nguồn mới thêm. */
export function suggestColumn(name: string, sourceType: string): MappingColumn {
  const t = sourceType.toLowerCase();
  const type = /^(integer|smallint|int)/.test(t) ? 'integer' : t.startsWith('bigint') ? 'bigint'
    : /^(numeric|decimal|real|double|money)/.test(t) ? 'decimal(18,2)' : t.startsWith('date') ? 'date'
    : t.startsWith('timestamp') ? 'timestamp' : t.startsWith('bool') ? 'boolean' : 'varchar';
  const as = name.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^[^a-z]+/, '') || 'cot';
  return { from: name, as, type, keep: true };
}

/** Lỗi nhập liệu trước khi gửi (tên đầu ra, trùng tên, kiểu, còn ít nhất một cột). */
export function mappingErrors(sources: SourceMapping[]): string[] {
  const errors: string[] = [];
  for (const s of sources) {
    const kept = s.columns.filter(c => c.keep);
    if (!kept.length) errors.push(`Nguồn ${s.id}: phải giữ ít nhất một cột.`);
    const seen = new Set<string>();
    for (const c of kept) {
      if (!OUTPUT.test(c.as)) errors.push(`Nguồn ${s.id}, cột ${c.from}: tên đầu ra "${c.as}" chỉ gồm chữ thường, số, gạch dưới và bắt đầu bằng chữ.`);
      if (seen.has(c.as)) errors.push(`Nguồn ${s.id}: tên đầu ra "${c.as}" bị trùng.`);
      seen.add(c.as);
      if (!COLUMN_TYPE.test(c.type)) errors.push(`Nguồn ${s.id}, cột ${c.as}: kiểu "${c.type}" không hợp lệ.`);
      if (c.date_formats?.length && !['date', 'timestamp'].includes(c.type)) errors.push(`Nguồn ${s.id}, cột ${c.as}: định dạng ngày chỉ dùng cho kiểu date / timestamp.`);
    }
  }
  return errors;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Đổi `alias.old` thành `alias.new` (và `old` đứng một mình khi Spec chỉ có một nguồn) trong một chuỗi biểu thức. */
function rename(text: string, alias: string, from: string, to: string, single: boolean): string {
  let out = text.replace(new RegExp(`(?<![\\w.])${escape(alias)}\\.${escape(from)}(?!\\w)`, 'g'), `${alias}.${to}`);
  if (single) out = out.replace(new RegExp(`(?<![\\w.])${escape(from)}(?![\\w.])`, 'g'), to);
  return out;
}

/** Áp mapping đã sửa vào Spec: cột staging, luật loại dòng, và đổi tên trong transform (grain, chỉ số, lọc, join). */
export function applyMapping(spec: AgentSpec, sources: SourceMapping[]): AgentSpec {
  const next: AgentSpec = JSON.parse(JSON.stringify(spec));
  const single = next.sources.length === 1;
  const renames: [string, string, string][] = [];
  for (const edit of sources) {
    const src = next.sources.find(s => s.id === edit.id);
    if (!src) continue;
    const original = new Map((src.staging?.columns ?? []).map(c => [c.from, c.as]));
    for (const c of edit.columns) {
      const before = original.get(c.from);
      if (c.keep && before && before !== c.as) renames.push([edit.id, before, c.as]);
    }
    src.staging = {
      ...(src.staging ?? { columns: [] }),
      columns: edit.columns.filter(c => c.keep).map(({ keep: _keep, ...c }) => {
        const out: Record<string, unknown> = { from: c.from, as: c.as, type: c.type };
        if (c.date_formats?.length) out.date_formats = c.date_formats;
        if (c.currency) out.currency = c.currency;
        return out as { from: string; as: string; type: string };
      }),
      reject_if: edit.rejectIf.map(r => r.trim()).filter(Boolean),
    };
  }
  const t = next.transform;
  for (const [alias, from, to] of renames) {
    const fix = (s: string) => rename(s, alias, from, to, single);
    for (const src of next.sources) if (src.id === alias && src.staging?.reject_if) src.staging.reject_if = src.staging.reject_if.map(fix);
    t.grain = t.grain.map(fix);
    if (t.time_grain) t.time_grain = { ...t.time_grain, column: fix(t.time_grain.column) };
    t.metrics = t.metrics.map(m => ({ ...m, expr: fix(m.expr) }));
    t.filters = t.filters.map(fix);
    if (t.joins) t.joins = t.joins.map(j => ({ ...j, on: j.on.map(([a, b]) => [fix(a), fix(b)] as [string, string]) }));
    // Cột gộp theo xuất hiện ở gold đúng bằng tên staging, nên khóa / phân vùng / test trên gold cũng đổi theo (trừ tên chỉ số).
    if (t.metrics.some(m => m.name === from)) continue;
    const gold = (c: string) => c === from ? to : c;
    if (t.unique_key) t.unique_key = t.unique_key.map(gold);
    if (t.partition_by) t.partition_by = gold(t.partition_by);
    if (t.tests) t.tests = t.tests.map(x => ({ ...x, column: gold(x.column) }));
  }
  return next;
}
