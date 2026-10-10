/**
 * Nguồn dữ liệu phía server cho mọi trang. Mặc định gọi backend FastAPI; đặt CATALOG_MOCK=1 để dùng dữ liệu mock.
 * Cookie phiên của backend có Path=/api/v1 nên trang không nhận được; khi đăng nhập (actions.ts) ta lưu lại
 * token với Path=/ và gửi kèm mọi lời gọi backend từ server.
 */
import { cache } from 'react';
import { cookies } from 'next/headers';
import type { RunReport, User } from '../postgres-api.ts';
import type { ChatMessage } from '../studio.ts';
import { buildSnapshot, type CatalogResponse, type PolicyResponse } from './live.ts';
import { DATASETS, DOMAINS, MOCK_ORCHESTRATION, MOCK_SNAPSHOT, PEOPLE } from './mock.ts';
import { CONNECTIONS_COOKIE, parseConnections } from './connections.ts';
import { Catalog } from './query.ts';
import type { AssetRunStatus, CatalogRun, Comparison, DeclaredSource, Discovery, Drift, Orchestration, Person, Platform } from './types.ts';
import type { Alert, MonitoredPipeline } from '../monitor.ts';

export const MOCK = process.env.CATALOG_MOCK === '1';
export const BACKEND = (process.env.PIPELINE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
export const SESSION_COOKIE = 'pipeline_session';
export const MOCK_USER_COOKIE = 'catalog_user';

export async function rememberedConnections(): Promise<string[]> {
  return parseConnections((await cookies()).get(CONNECTIONS_COOKIE)?.value);
}

export class BackendError extends Error {
  readonly status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

/** Gọi backend từ server, kèm cookie phiên. Lỗi trả về BackendError với `detail` của FastAPI. */
export async function backend<T>(path: string, init?: RequestInit): Promise<T> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  let response: Response;
  try {
    response = await fetch(`${BACKEND}/api/v1${path}`, {
      ...init, cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(token ? { Cookie: `${SESSION_COOKIE}=${token}` } : {}), ...init?.headers },
    });
  } catch {
    throw new BackendError(0, `Không kết nối được backend ở ${BACKEND}. Chạy: uvicorn src.main:app --reload --port 8000`);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    throw new BackendError(response.status, typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join('; ') : `Backend trả lỗi ${response.status}.`);
  }
  return body as T;
}

export const listUsers = cache(async (): Promise<Person[]> => {
  if (MOCK) return PEOPLE;
  const users = await backend<User[]>('/pipeline/users');
  return users.map(u => ({ id: u.id, name: u.name, email: u.email ?? u.username ?? u.id, role: u.role }));
});

/** Danh sách tài khoản cho màn quản trị; `managed=false` là tài khoản hệ thống không được đổi quyền. */
export const listAccounts = cache(async (): Promise<User[]> => {
  if (MOCK) return PEOPLE.filter(person => person.role !== 'Steward').map(person => ({
    id: person.id, username: person.email, name: person.name,
    role: person.role as User['role'], managed: false,
  }));
  return backend<User[]>('/pipeline/users');
});

/** Người dùng đang đăng nhập; null khi chưa đăng nhập hoặc phiên hết hạn. */
export const currentUser = cache(async (): Promise<Person | null> => {
  if (MOCK) {
    const id = (await cookies()).get(MOCK_USER_COOKIE)?.value;
    return PEOPLE.find(p => p.id === id) ?? null;
  }
  try {
    const user = await backend<User>('/pipeline/session');
    return { id: user.id, name: user.name, email: user.email ?? user.username ?? user.id, role: user.role };
  } catch (err) {
    if (err instanceof BackendError && err.status === 401) return null;
    throw err;
  }
});

/** Nội dung các file agent sinh ra cho version hiện tại của run. */
export async function runFiles(run: CatalogRun): Promise<Record<string, string>> {
  if (MOCK || !run.fileCount) return run.files;
  return (await backend<{ files: Record<string, string> }>(`/runs/${encodeURIComponent(run.id)}/files?version=${run.version}`)).files;
}

/** Lịch sử hỏi đáp của một run (GET /runs/{id}/messages). Mock chưa có chat nên trả rỗng. */
export async function runMessages(runId: string): Promise<ChatMessage[]> {
  if (MOCK) return [];
  return backend<ChatMessage[]>(`/runs/${encodeURIComponent(runId)}/messages`);
}

const EMPTY = new Catalog({ ...MOCK_SNAPSHOT, datasets: [], domains: [], glossary: [], runs: [], live: true });

/** Catalog của request hiện tại (gọi backend một lần mỗi request). Chưa đăng nhập thì trả catalog rỗng; layout hiện màn đăng nhập. */
export const getCatalog = cache(async (): Promise<Catalog> => {
  if (MOCK) return new Catalog(MOCK_SNAPSHOT);
  if (!(await currentUser())) return EMPTY;
  const [catalog, reports, policy, users] = await Promise.all([
    backend<CatalogResponse>('/catalog'), backend<RunReport[]>('/runs'), backend<PolicyResponse>('/policy'), backend<User[]>('/pipeline/users'),
  ]);
  return new Catalog(buildSnapshot(catalog, reports, policy, users));
});

/** Kết nối khai báo trong domains.yml; chưa kết nối tới nguồn nào. */
export async function listDeclaredSources(): Promise<DeclaredSource[]> {
  if (MOCK) return DOMAINS.flatMap(d => d.sources.map(s => ({ ref: s.ref, domain: d.id, kind: s.kind, location: s.location, supported: s.kind === 'postgres' })));
  return backend<DeclaredSource[]>('/catalog/sources');
}

type ObjectsResponse = {
  discovered_at: string;
  objects: { name: string; kind: 'table' | 'collection' | 'endpoint' | 'folder'; columns: { name: string; type: string }[]; primary_key: string[]; row_estimate: number | null }[];
  foreign_keys: { name: string; table: string; columns: string[]; ref_schema: string; ref_table: string; ref_columns: string[] }[];
};

/** Kết nối tới nguồn ngay lúc gọi và lấy danh sách bảng / collection đang có. */
export async function discoverObjects(ref: string): Promise<Discovery> {
  if (MOCK) {
    await new Promise(resolve => setTimeout(resolve, 500));
    const decl = DOMAINS.flatMap(d => d.sources).find(s => s.ref === ref);
    if (!decl) return { error: `Nguồn '${ref}' không có trong domains.yml.` };
    if (decl.kind !== 'postgres') return { error: `Chưa có connector cho nguồn ${decl.kind as Platform}.` };
    const objects = DATASETS.filter(d => d.layer === 'source' && d.sourceRef === ref && !d.unavailable).map(d => ({
      name: d.name.split('.').at(-1)!, kind: 'table' as const, columns: d.columns.map(c => ({ name: c.name, type: c.type })),
      primaryKey: d.columns.filter(c => c.primaryKey).map(c => c.name), rowEstimate: d.rowCount,
    }));
    const tableOf = (id: string) => DATASETS.find(d => d.id === id)?.name.split('.') ?? [];
    const foreignKeys = MOCK_SNAPSHOT.relationships.filter(r => r.kind === 'foreign_key' && DATASETS.find(d => d.id === r.from)?.sourceRef === ref)
      .map(r => ({ name: r.label, table: tableOf(r.from)[1], columns: r.fromColumns, refSchema: tableOf(r.to)[0], refTable: tableOf(r.to)[1], refColumns: r.toColumns }));
    return { objects, foreignKeys, discoveredAt: new Date().toISOString() };
  }
  try {
    const found = await backend<ObjectsResponse>(`/catalog/sources/${encodeURIComponent(ref)}/objects`);
    return {
      discoveredAt: found.discovered_at,
      objects: found.objects.map(o => ({ name: o.name, kind: o.kind, columns: o.columns, primaryKey: o.primary_key, rowEstimate: o.row_estimate })),
      foreignKeys: (found.foreign_keys ?? []).map(f => ({ name: f.name, table: f.table, columns: f.columns, refSchema: f.ref_schema, refTable: f.ref_table, refColumns: f.ref_columns })),
    };
  } catch (err) { return { error: err instanceof Error ? err.message : 'Không kết nối được nguồn.' }; }
}

type StatusResponse = {
  available: boolean; url: string; reason?: string;
  assets?: Record<string, {
    jobs: string[]; last_materialized_at: string | null; last_materialized_run_id: string | null;
    last_run: { run_id: string; job: string; status: string; started_at: string | null; ended_at: string | null } | null;
    schedule: { name: string; cron: string; timezone: string; running: boolean; next_tick_at: string | null } | null;
  }>;
};

/** Trạng thái run trên Dagster của mọi asset đã deploy. Lỗi gọi backend cũng trả `available: false`, không làm hỏng trang. */
export const getOrchestration = cache(async (): Promise<Orchestration> => {
  if (MOCK) return MOCK_ORCHESTRATION;
  let body: StatusResponse;
  try { body = await backend<StatusResponse>('/orchestration/status'); }
  catch (err) { return { available: false, url: '', reason: err instanceof Error ? err.message : 'Không đọc được trạng thái Dagster.', assets: {} }; }
  const assets: Record<string, AssetRunStatus> = {};
  for (const [key, a] of Object.entries(body.assets ?? {})) {
    assets[key] = {
      jobs: a.jobs, lastMaterializedAt: a.last_materialized_at, lastMaterializedRunId: a.last_materialized_run_id,
      lastRun: a.last_run && { runId: a.last_run.run_id, job: a.last_run.job, status: a.last_run.status, startedAt: a.last_run.started_at, endedAt: a.last_run.ended_at },
      schedule: a.schedule && { name: a.schedule.name, cron: a.schedule.cron, timezone: a.schedule.timezone, running: a.schedule.running, nextTickAt: a.schedule.next_tick_at },
    };
  }
  return { available: body.available, url: body.url, reason: body.reason, assets };
});

/** Khóa asset Dagster của một dataset: `gold.fct_daily_sales` → `gold/fct_daily_sales`. */
export const assetKey = (datasetName: string) => datasetName.replace('.', '/');

type CompareResponse = { available: false; reason: string } | {
  available: true;
  base: { run_id: string; status: string; created_at: string; user_request: string; reason: string };
  spec: { field: string; before: string[]; after: string[] }[];
  results: {
    rows: { before: number | null; after: number | null };
    columns: { added: string[]; removed: string[]; type_changed: { name: string; before: string; after: string }[] };
    metrics: { name: string; before: string | null; after: string | null; delta_pct: number | null }[];
    checks: { id: string; before: string | null; after: string | null }[];
    profiled: boolean;
  };
  files: { path: string; status: 'added' | 'removed' | 'changed' | 'same'; added_lines?: number; removed_lines?: number; before?: string | null; after?: string | null }[];
  impact: { replaces: { run_id: string; model: string; approved_at: string } | null; shared_bronze: { model: string; run_id: string; bronze: string[] }[] };
};

/** So sánh run với phiên bản khác của cùng pipeline (mặc định: run gốc → bản đang chạy → bản trước). */
export async function getComparison(runId: string, base?: string): Promise<Comparison> {
  if (MOCK) return { available: false, reason: 'Chế độ mock chưa có dữ liệu so sánh phiên bản.' };
  let c: CompareResponse;
  try { c = await backend<CompareResponse>(`/runs/${encodeURIComponent(runId)}/compare${base ? `?base=${encodeURIComponent(base)}` : ''}`); }
  catch (err) { return { available: false, reason: err instanceof Error ? err.message : 'Không so sánh được.' }; }
  if (!c.available) return c;
  return {
    available: true,
    base: { runId: c.base.run_id, status: c.base.status, createdAt: c.base.created_at, userRequest: c.base.user_request, reason: c.base.reason },
    spec: c.spec,
    results: {
      rows: c.results.rows,
      columns: { added: c.results.columns.added, removed: c.results.columns.removed, typeChanged: c.results.columns.type_changed },
      metrics: c.results.metrics.map(m => ({ name: m.name, before: m.before, after: m.after, deltaPct: m.delta_pct })),
      checks: c.results.checks, profiled: c.results.profiled,
    },
    files: c.files.map(f => ({ path: f.path, status: f.status, addedLines: f.added_lines, removedLines: f.removed_lines, before: f.before, after: f.after })),
    impact: {
      replaces: c.impact.replaces && { runId: c.impact.replaces.run_id, model: c.impact.replaces.model, approvedAt: c.impact.replaces.approved_at },
      sharedBronze: c.impact.shared_bronze.map(s => ({ model: s.model, runId: s.run_id, bronze: s.bronze })),
    },
  };
}

type DriftResponse = {
  checked_at: string;
  pipelines: { run_id: string; model: string; domain: string; approved_at: string | null; has_type_baseline: boolean; status: 'ok' | 'warning' | 'breaking' | 'unknown';
    sources: { source_id: string; source_ref: string; table: string; status: 'ok' | 'warning' | 'breaking' | 'unknown'; reason?: string; missing_table?: boolean; missing_columns?: string[]; type_changes?: { column: string; before: string; after: string }[] }[] }[];
};

/** Kết nối nguồn ngay lúc gọi và so với các cột pipeline đang chạy dùng (cache theo request). */
export const getDrift = cache(async (): Promise<Drift> => {
  if (MOCK) return { available: true, checkedAt: new Date().toISOString(), pipelines: [] };
  try {
    const d = await backend<DriftResponse>('/catalog/drift');
    return {
      available: true, checkedAt: d.checked_at,
      pipelines: d.pipelines.map(p => ({
        runId: p.run_id, model: p.model, domain: p.domain, approvedAt: p.approved_at, hasTypeBaseline: p.has_type_baseline, status: p.status,
        sources: p.sources.map(s => ({ sourceId: s.source_id, sourceRef: s.source_ref, table: s.table, status: s.status, reason: s.reason, missingTable: s.missing_table, missingColumns: s.missing_columns ?? [], typeChanges: s.type_changes ?? [] })),
      })),
    };
  } catch (err) { return { available: false, reason: err instanceof Error ? err.message : 'Không kiểm tra được.' }; }
});

/** Màn theo dõi (Day 2): pipeline đã deploy kèm luật suy giảm đang kích hoạt, và cảnh báo còn mở. */
export async function getMonitor(): Promise<{ pipelines: MonitoredPipeline[]; alerts: Alert[] } | { error: string }> {
  if (MOCK) return { pipelines: [], alerts: [] };
  try {
    const [pipelines, alerts] = await Promise.all([
      backend<MonitoredPipeline[]>('/monitor/pipelines'),
      backend<Alert[]>('/monitor/alerts'),
    ]);
    return { pipelines, alerts };
  } catch (err) {
    return { error: err instanceof Error ? err.message : 'Không đọc được số đo từ backend.' };
  }
}
