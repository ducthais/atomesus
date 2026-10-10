/**
 * Mô hình hiển thị của Data Catalog. Dataset là thực thể trung tâm; run/pipeline là hoạt động trên dataset.
 * Trang đọc qua `getCatalog()` (source.ts): dữ liệu mock (mock.ts) hoặc dựng từ backend thật (live.ts).
 */
import type { Limits, Run } from '../studio.ts';
import type { RunResult } from './explain.ts';
import type { AgentPlan } from '../postgres-api.ts';

export type Layer = 'source' | 'bronze' | 'staging' | 'gold';
export type Platform = 'postgres' | 'mongodb' | 'rest_api' | 'filesystem' | 'duckdb';
export type Role = 'Engineer' | 'Reviewer' | 'Admin' | 'Steward';

export type Person = { id: string; name: string; email: string; role: Role };

export type Column = {
  name: string; type: string; description?: string;
  primaryKey?: boolean; nullable?: boolean;
  /** Khóa ngoại: cột đích dạng `<bảng>.<cột>`. */
  references?: string;
  /** Id thuật ngữ (glossary) mà cột này biểu diễn. */
  terms?: string[];
  tags?: string[];
};

export type QualityState = 'pass' | 'fail' | 'unmeasured';
export type QualityCheck = { id: string; name: string; state: QualityState; value: string; detail: string; at: string; runId?: string };

export type Dataset = {
  /** Định danh ổn định: `<domain>.<layer>.<tên>` */
  id: string;
  /** Tên vật lý, ví dụ `sales_raw.orders` hoặc `gold.fct_daily_revenue`. */
  name: string;
  layer: Layer; platform: Platform; domain: string;
  /** Nguồn khai báo trong policy/domains.yml (chỉ dataset lớp source). */
  sourceRef?: string;
  description: string; owner: string; tags: string[]; terms: string[];
  columns: Column[];
  rowCount: number | null;
  /** Lần cập nhật dữ liệu gần nhất (ISO), null khi chưa từng nạp. */
  updatedAt: string | null;
  /** Lịch chạy (cron) của pipeline sinh ra dataset. */
  schedule?: string;
  upstream: string[];
  quality: QualityCheck[];
  sample: Record<string, string | null>[];
  /** Run đã tạo hoặc cập nhật dataset này, mới nhất trước. */
  runs: string[];
  /** Nguồn chưa đọc được ở Sprint hiện tại. */
  unavailable?: string;
};

export type Source = { ref: string; kind: Platform; connection: string; location: string; available: boolean; note?: string };

export type GlossaryTerm = {
  id: string; name: string; domain: string; definition: string; canonical: boolean;
  /** Bảng nguồn khai báo trong domains.yml, dạng `<source_ref>.<bảng>`. */
  tables: string[];
};

export type Domain = {
  id: string; name: string; description: string; steward: string;
  datasets: string[]; sources: Source[]; currency: string;
};

export type Activity = { id: string; at: string; actor: string; action: string; target: string; href: string };

export type PolicyFile = { name: string; description: string; content: string };

/** `files`: đường dẫn → nội dung. Mock có sẵn; backend để trống, đọc nội dung qua `runFiles()` (GET /runs/{id}/files). */
export type CatalogRun = Run & { files: Record<string, string>; result?: RunResult | null; spec?: unknown; agentPlan?: AgentPlan | null; timeline?: { event: string; at: string; actor?: string | null }[] };

export type LineageGraph = { nodes: Dataset[]; edges: { from: string; to: string }[]; focus?: string };

export type Facet = { value: string; label: string; count: number };
export type SearchResult = {
  items: Dataset[]; total: number;
  facets: { domain: Facet[]; layer: Facet[]; platform: Facet[]; tag: Facet[]; owner: Facet[] };
};
export type SearchQuery = { q?: string; domain?: string; layer?: string; platform?: string; tag?: string; owner?: string };

/** Toàn bộ dữ liệu catalog ở một thời điểm; `Catalog` (query.ts) truy vấn trên đây. Mock hay backend thật đều ra cùng hình dạng. */
export type Snapshot = {
  datasets: Dataset[]; domains: Domain[]; glossary: GlossaryTerm[]; runs: CatalogRun[]; people: Person[];
  policy: { files: PolicyFile[]; limits: Limits };
  relationships: Relationship[];
  /** Thực thể đã công nhận dùng chung giữa các domain (domains.yml conformed_dimensions). */
  conformed: string[];
  live: boolean;
};

/** Kết nối khai báo trong domains.yml (GET /catalog/sources). `supported` = backend có connector cho loại nguồn này. */
export type DeclaredSource = { ref: string; domain: string; kind: Platform; location: string | null; supported: boolean };
/** Một bảng (Postgres) hoặc collection (MongoDB) đọc được lúc kết nối tới nguồn. */
export type SourceObject = { name: string; kind: 'table' | 'collection' | 'endpoint' | 'folder'; columns: { name: string; type: string }[]; primaryKey: string[]; rowEstimate: number | null };
/** Khóa ngoại đọc được lúc kết nối: `table.columns` → `refSchema.refTable.refColumns`. */
export type DiscoveredForeignKey = { name: string; table: string; columns: string[]; refSchema: string; refTable: string; refColumns: string[] };
export type Discovery = { objects: SourceObject[]; foreignKeys: DiscoveredForeignKey[]; discoveredAt: string } | { error: string };

/**
 * Quan hệ giữa hai bảng: khóa ngoại khai báo trong nguồn (`foreign_key`), hoặc phép join mà pipeline dùng (`join`).
 * `from`/`to` là id dataset; cột ghép theo thứ tự (fromColumns[i] = toColumns[i]).
 */
export type Relationship = {
  id: string; kind: 'foreign_key' | 'join';
  from: string; fromColumns: string[]; to: string; toColumns: string[];
  /** Tên constraint, hoặc kiểu join (inner / left). */
  label: string;
  /** Bảng gold được dựng bằng phép join này. */
  usedBy: string[];
};

/** Trạng thái vận hành của một asset trên Dagster (GET /orchestration/status). Khóa asset: `gold/fct_daily_sales`. */
export type AssetRunStatus = {
  jobs: string[];
  lastMaterializedAt: string | null; lastMaterializedRunId: string | null;
  lastRun: { runId: string; job: string; status: string; startedAt: string | null; endedAt: string | null } | null;
  schedule: { name: string; cron: string; timezone: string; running: boolean; nextTickAt: string | null } | null;
};
export type Orchestration = { available: boolean; url: string; reason?: string; assets: Record<string, AssetRunStatus> };

/** Một file sinh ra so giữa hai phiên bản (GET /runs/{id}/compare). */
export type FileChange = { path: string; status: 'added' | 'removed' | 'changed' | 'same'; addedLines?: number; removedLines?: number; before?: string | null; after?: string | null };
/** So sánh run với một phiên bản khác của cùng pipeline, kèm ảnh hưởng nếu duyệt. */
export type Comparison = { available: false; reason: string } | {
  available: true;
  base: { runId: string; status: string; createdAt: string; userRequest: string; reason: string };
  spec: { field: string; before: string[]; after: string[] }[];
  results: {
    rows: { before: number | null; after: number | null };
    columns: { added: string[]; removed: string[]; typeChanged: { name: string; before: string; after: string }[] };
    metrics: { name: string; before: string | null; after: string | null; deltaPct: number | null }[];
    checks: { id: string; before: string | null; after: string | null }[];
    profiled: boolean;
  };
  files: FileChange[];
  impact: { replaces: { runId: string; model: string; approvedAt: string } | null; sharedBronze: { model: string; runId: string; bronze: string[] }[] };
};

/** Schema nguồn của một pipeline đang chạy so với lúc duyệt (GET /catalog/drift). */
export type PipelineDrift = {
  runId: string; model: string; domain: string; approvedAt: string | null; hasTypeBaseline: boolean;
  status: 'ok' | 'warning' | 'breaking' | 'unknown';
  sources: { sourceId: string; sourceRef: string; table: string; status: PipelineDrift['status']; reason?: string; missingTable?: boolean; missingColumns: string[]; typeChanges: { column: string; before: string; after: string }[] }[];
};
export type Drift = { available: true; checkedAt: string; pipelines: PipelineDrift[] } | { available: false; reason: string };
