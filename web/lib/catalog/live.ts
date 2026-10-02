/**
 * Dựng Snapshot của catalog từ backend thật: GET /catalog (domain → nguồn → bảng/cột thật, thực thể),
 * GET /runs (RunReport) và GET /policy. Dataset bronze/staging/gold suy ra từ Spec của các run đã chạy thử
 * thành công; số dòng, kiểm tra chất lượng và mẫu dữ liệu chỉ lấy từ phép đo trong RunReport (NFR-08).
 */
import type { AgentSpec, RunReport, User } from '../postgres-api.ts';
import { toStudioRun, type Check, type Limits } from '../studio.ts';
import { runResult } from './explain.ts';
import type { CatalogRun, Column, Dataset, Domain, GlossaryTerm, Person, Platform, PolicyFile, QualityCheck, Relationship, Snapshot } from './types.ts';

export type CatalogSource = {
  kind: 'postgres' | 'rest_api' | 'filesystem'; connection_ref: string; location: string | null;
  available: boolean; reason?: string; tables: Record<string, { name: string; type: string }[]>;
  primary_keys?: Record<string, string[]>;
  foreign_keys?: { name: string; table: string; columns: string[]; ref_schema: string; ref_table: string; ref_columns: string[] }[];
};
export type CatalogResponse = {
  domains: Record<string, {
    steward: string; currency: string; sources: Record<string, CatalogSource>;
    entities: Record<string, { canonical: boolean; definition: string; tables: string[] }>;
  }>;
  conformed_dimensions: string[];
};
export type PolicyResponse = { files: Record<string, string>; limits: Limits };

/** Run đã chạy thử xong trong sandbox: bảng của nó thật sự tồn tại nên được đưa vào catalog. */
const MATERIALIZED = ['READY_FOR_REVIEW', 'APPROVED', 'DEPLOYED'];
const POLICY_DESCRIPTION: Record<string, string> = {
  'domains.yml': 'Domain, nguồn được phép đọc, thực thể và steward.',
  'budgets.yml': 'Trần chi phí, số lần gọi LLM và luật SQL.',
  'objectives.yml': 'Ngưỡng chất lượng, độ tươi và suy giảm theo domain.',
};

const isAgentSpec = (spec: RunReport['spec']): spec is AgentSpec => !!spec && 'sources' in spec && 'transform' in spec;
const num = (v: unknown) => typeof v === 'number' ? v : null;
const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const stripAlias = (col: string) => col.split('.').at(-1)!;

function quality(checks: Check[], ids: (id: string) => boolean, report: RunReport): QualityCheck[] {
  return checks.filter(c => ids(c.id)).map(c => ({
    id: c.id, name: c.name, state: c.state === 'running' ? 'unmeasured' : c.state, value: c.value, detail: c.detail,
    at: report.created_at, runId: report.run_id.slice(0, 8),
  }));
}

export function buildSnapshot(catalog: CatalogResponse, reports: RunReport[], policy: PolicyResponse, users: User[]): Snapshot {
  const datasets = new Map<string, Dataset>();
  const add = (d: Dataset) => { if (!datasets.has(d.id)) datasets.set(d.id, d); return datasets.get(d.id)!; };
  const link = (d: Dataset, runId: string) => { if (!d.runs.includes(runId)) d.runs.push(runId); };

  // ── Nguồn: bảng thật đọc qua information_schema; nguồn chưa hỗ trợ lấy từ bảng khai báo trong thực thể ──
  const glossary: GlossaryTerm[] = [];
  const sourceIds = new Map<string, string>(); // "<source_ref>.<bảng>" → id dataset
  for (const [domain, decl] of Object.entries(catalog.domains)) {
    const termsOf = (ref: string) => Object.entries(decl.entities).filter(([, e]) => e.tables.includes(ref)).map(([key]) => `${domain}.${key}`);
    for (const [key, e] of Object.entries(decl.entities)) glossary.push({ id: `${domain}.${key}`, name: key, domain, definition: e.definition, canonical: e.canonical, tables: e.tables });
    for (const [ref, src] of Object.entries(decl.sources)) {
      const objects = src.available ? Object.keys(src.tables)
        : Object.values(decl.entities).flatMap(e => e.tables).filter(t => t.startsWith(`${ref}.`)).map(t => t.slice(ref.length + 1));
      for (const table of new Set(objects)) {
        const name = src.kind === 'postgres' && src.location ? `${src.location}.${table}` : `${ref}.${table}`;
        const id = `${domain}.source.${name}`;
        sourceIds.set(`${ref}.${table}`, id);
        add({
          id, name, layer: 'source', platform: src.kind, domain, sourceRef: ref,
          description: src.available ? `Bảng ${name} trong nguồn ${ref} (${src.kind}), đọc qua tài khoản chỉ đọc.` : `Khai báo trong domains.yml (${ref}).`,
          owner: decl.steward, tags: ['nguồn gốc'], terms: termsOf(`${ref}.${table}`),
          columns: (src.tables[table] ?? []).map(c => {
            const fk = src.foreign_keys?.find(f => f.table === table && f.columns.includes(c.name));
            return { name: c.name, type: c.type, primaryKey: src.primary_keys?.[table]?.includes(c.name),
              references: fk ? `${fk.ref_table}.${fk.ref_columns[fk.columns.indexOf(c.name)]}` : undefined };
          }),
          rowCount: null, updatedAt: null, upstream: [], quality: [], sample: [], runs: [],
          unavailable: src.available ? undefined : `${src.reason ?? 'Nguồn chưa đọc được.'}`,
        });
      }
    }
  }

  // ── Quan hệ khai báo trong nguồn: khóa ngoại Postgres giữa các bảng nguồn ──
  const relationships = new Map<string, Relationship>();
  for (const [domain, decl] of Object.entries(catalog.domains)) {
    for (const [ref, src] of Object.entries(decl.sources)) {
      for (const fk of src.foreign_keys ?? []) {
        const from = sourceIds.get(`${ref}.${fk.table}`);
        const to = datasets.get(`${domain}.source.${fk.ref_schema}.${fk.ref_table}`)?.id;
        if (from && to) relationships.set(`fk:${ref}.${fk.name}`, { id: `fk:${ref}.${fk.name}`, kind: 'foreign_key', from, fromColumns: fk.columns, to, toColumns: fk.ref_columns, label: fk.name, usedBy: [] });
      }
    }
  }

  // ── Bronze / staging / gold từ Spec của run; run mới nhất quyết định metadata, mọi run được liên kết ──
  // RunReport.files chỉ là sha256 của từng file; nội dung đọc riêng qua GET /runs/{id}/files khi mở tab Code.
  const runs: CatalogRun[] = reports.map(r => ({ ...toStudioRun(r), files: {}, result: runResult(r) }));
  const ordered = [...reports].sort((a, b) => b.created_at.localeCompare(a.created_at));
  for (const report of ordered) {
    const run = runs.find(r => r.id === report.run_id)!;
    const spec = report.spec;
    // Run làm sạch thủ công (/pipeline/runs) chỉ chạm tới bảng nguồn.
    if (spec && 'table' in spec && !isAgentSpec(spec)) {
      const source = [...datasets.values()].find(d => d.layer === 'source' && d.name === spec.table);
      if (source) link(source, report.run_id);
      continue;
    }
    if (!isAgentSpec(spec)) {
      for (const s of report.plan?.sources ?? []) { const id = sourceIds.get(`${s.source_ref}.${s.object}`); if (id) link(datasets.get(id)!, report.run_id); }
      continue;
    }
    const ok = MATERIALIZED.includes(report.status);
    const model = spec.transform.model_name;
    // Phép join của pipeline: "alias.cột" → bảng nguồn tương ứng với sources[].id.
    const aliasTable = new Map(spec.sources.map(s => [s.id, sourceIds.get(`${s.source_ref}.${s.table ?? s.id}`)]));
    for (const join of spec.transform.joins ?? []) {
      const pairs = join.on.map(([a, b]) => [a.split('.'), b.split('.')] as const);
      const from = aliasTable.get(pairs[0][0][0]), to = aliasTable.get(pairs[0][1][0]);
      if (!from || !to || from === to) continue;
      const fromColumns = pairs.map(([a]) => a[1]), toColumns = pairs.map(([, b]) => b[1]);
      const id = `join:${[`${from}(${fromColumns})`, `${to}(${toColumns})`].sort().join('=')}`;
      const rel = relationships.get(id) ?? { id, kind: 'join' as const, from, fromColumns, to, toColumns, label: `${join.type} join`, usedBy: [] };
      if (ok && !rel.usedBy.includes(`${spec.domain}.gold.${model}`)) rel.usedBy.push(`${spec.domain}.gold.${model}`);
      relationships.set(id, rel);
    }
    const tags = report.status === 'DEPLOYED' ? ['đã deploy'] : report.status === 'READY_FOR_REVIEW' ? ['chờ duyệt'] : [];
    const stagingIds: string[] = [];
    const typeOf = new Map<string, string>();
    const terms = new Set<string>();
    for (const src of spec.sources) {
      const table = src.table ?? src.endpoint ?? src.path_pattern ?? src.id;
      const sourceId = sourceIds.get(`${src.source_ref}.${table}`);
      const source = sourceId ? datasets.get(sourceId)! : null;
      if (source) {
        link(source, report.run_id);
        source.terms.forEach(t => terms.add(t));
        const rows = report.checks.find(c => c.name === 'row_reconcile' && c.source_id === src.id);
        if (source.rowCount === null && rows) { source.rowCount = num(rows.observed.source); source.updatedAt = report.created_at; }
      }
      if (!ok) continue;
      const rowCheck = report.checks.find(c => c.name === 'row_reconcile' && c.source_id === src.id);
      const bronze = add({
        id: `${spec.domain}.bronze.${src.target.table}`, name: `bronze.${src.target.table}`, layer: 'bronze', platform: 'duckdb', domain: spec.domain,
        description: `Bản sao nguyên trạng của ${source?.name ?? table} do dlt nạp (${src.write_disposition}).`, owner: report.created_by,
        tags: ['dlt'], terms: source?.terms ?? [], columns: source?.columns ?? [], rowCount: num(rowCheck?.observed.bronze), updatedAt: report.created_at,
        schedule: spec.schedule.cron, upstream: source ? [source.id] : [], sample: [], runs: [],
        quality: quality(run.checks, id => id === `row_reconcile:${src.id}` || id === `idempotent:${src.id}` || id === `hash_reconcile:${src.id}`, report),
      });
      link(bronze, report.run_id);
      const stagedColumns: Column[] = src.staging?.columns.map(c => ({ name: c.as, type: c.type, description: c.from === c.as ? undefined : `Từ ${c.from}`, primaryKey: src.primary_key.includes(c.as) })) ?? bronze.columns;
      stagedColumns.forEach(c => typeOf.set(c.name, c.type));
      const stagedCheck = report.checks.find(c => c.name === 'staging_reconcile' && c.source_id === src.id);
      const staging = add({
        id: `${spec.domain}.staging.stg_${model}__${src.id}`, name: `staging.stg_${model}__${src.id}`, layer: 'staging', platform: 'duckdb', domain: spec.domain,
        description: `Làm sạch ${source?.name ?? table}: ép kiểu ${stagedColumns.length} cột, khử trùng và tách dòng lỗi sang rejected.`, owner: report.created_by,
        tags: ['dbt', ...tags], terms: source?.terms ?? [], columns: stagedColumns, rowCount: num(stagedCheck?.observed.staging), updatedAt: report.created_at,
        schedule: spec.schedule.cron, upstream: [bronze.id], sample: [], runs: [],
        quality: quality(run.checks, id => id === `staging_reconcile:${src.id}`, report),
      });
      link(staging, report.run_id);
      stagingIds.push(staging.id);
    }
    if (!ok) continue;
    const post = report.gates.find(g => g.name === 'post_run');
    const sample = (post?.observed.gold_samples as Record<string, string | null>[] | undefined) ?? [];
    const metricNames = new Set(spec.transform.metrics.map(m => m.name));
    const columnNames = sample.length ? Object.keys(sample[0]) : [...spec.transform.grain.map(stripAlias), ...metricNames];
    const gold = add({
      id: `${spec.domain}.gold.${model}`, name: `gold.${model}`, layer: 'gold', platform: 'duckdb', domain: spec.domain,
      description: report.plan?.summary || report.user_request, owner: report.created_by, tags: ['dbt', ...tags], terms: [...terms],
      columns: columnNames.map(name => {
        const metric = spec.transform.metrics.find(m => m.name === name);
        return { name, type: metric ? 'numeric' : typeOf.get(name) ?? '—', description: metric?.expr, primaryKey: spec.transform.grain.map(stripAlias).includes(name) };
      }),
      rowCount: num(post?.observed.gold_rows), updatedAt: report.created_at, schedule: spec.schedule.cron, upstream: stagingIds, sample, runs: [],
      quality: quality(run.checks, id => !id.includes(':') || id === 'gate:post_run', report),
    });
    link(gold, report.run_id);
  }

  const people: Person[] = [
    ...users.map(u => ({ id: u.id, name: u.name, email: u.id, role: u.role })),
    ...[...new Set(Object.values(catalog.domains).map(d => d.steward))].map(email => ({ id: email, name: title(email.split('@')[0]) + ' (steward)', email, role: 'Steward' as const })),
  ];
  const list = [...datasets.values()];
  const domains: Domain[] = Object.entries(catalog.domains).map(([id, d]) => {
    const sources = Object.entries(d.sources).map(([ref, s]) => ({ ref, kind: s.kind as Platform, connection: s.connection_ref, location: s.location ?? '—', available: s.available, note: s.reason }));
    return {
      id, name: title(id), steward: d.steward, currency: d.currency, sources, datasets: list.filter(x => x.domain === id).map(x => x.id),
      description: `${sources.length} nguồn khai báo, ${sources.filter(s => s.available).length} đọc được; ${Object.keys(d.entities).length} thực thể nghiệp vụ.`,
    };
  });
  const files: PolicyFile[] = Object.entries(policy.files).map(([name, content]) => ({ name, content, description: POLICY_DESCRIPTION[name] ?? '' }));
  return { datasets: list, domains, glossary, runs, people, policy: { files, limits: policy.limits }, relationships: [...relationships.values()], conformed: catalog.conformed_dimensions, live: true };
}
