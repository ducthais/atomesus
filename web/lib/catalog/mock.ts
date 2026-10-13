/**
 * Dữ liệu mock cho giao diện Data Catalog. Mô phỏng đúng những gì backend sẽ trả:
 * domain/nguồn/thuật ngữ từ policy/domains.yml, schema thật từ Postgres (resolve.catalog),
 * bảng bronze/staging/gold và số đo từ RunReport. Thay bằng API thật trong `api.ts`.
 */
import type { Check, Log, Plan } from '../studio.ts';
import type { ConnectionSchema, ConnectionTable, UserConnection } from '../user-connections.ts';
import type { CatalogRun, Dataset, Domain, GlossaryTerm, Orchestration, Person, PolicyFile, Relationship, Snapshot } from './types.ts';

export const PEOPLE: Person[] = [
  { id: 'minh', name: 'Minh Trần', email: 'minh@company.vn', role: 'Engineer' },
  { id: 'lan', name: 'Lan Phạm', email: 'lan@company.vn', role: 'Reviewer' },
  { id: 'admin', name: 'Quản trị viên', email: 'admin', role: 'Admin' },
  { id: 'an', name: 'An Nguyễn', email: 'an@company.vn', role: 'Steward' },
  { id: 'binh', name: 'Bình Lê', email: 'binh@company.vn', role: 'Steward' },
];

export const USER_CONNECTIONS: UserConnection[] = [
  { id: 'a12bc34567894def901234567890abcd', owner_id: 'minh', name: 'Postgres bán hàng', host: 'sales.example.vn', port: 5432,
    database: 'sales', user: 'reader', sslmode: 'require', created_at: 1790902800, selection: { schema: 'public', tables: ['orders', 'customers'] } },
  { id: 'b98de76543214abc901234567890abcd', owner_id: 'minh', name: 'Kho dữ liệu báo cáo', host: 'reports.example.vn', port: 5432,
    database: 'reports', user: 'readonly', sslmode: 'require', created_at: 1790989200, selection: null },
];
export const USER_CONNECTION_SCHEMAS: ConnectionSchema[] = [{ name: 'public', table_count: 2 }, { name: 'archive', table_count: 1 }];
export const USER_CONNECTION_TABLES: Record<string, ConnectionTable[]> = {
  public: [
    { name: 'orders', columns: [{ name: 'order_id', type: 'integer' }, { name: 'customer_id', type: 'integer' },
      { name: 'amount', type: 'numeric(18,2)' }, { name: 'ordered_at', type: 'timestamp' }], primary_key: ['order_id'], row_estimate: 12480 },
    { name: 'customers', columns: [{ name: 'customer_id', type: 'integer' }, { name: 'full_name', type: 'text' }], primary_key: ['customer_id'], row_estimate: 3150 },
  ],
  archive: [{ name: 'orders_2025', columns: [{ name: 'order_id', type: 'integer' }, { name: 'amount', type: 'numeric(18,2)' }], primary_key: ['order_id'], row_estimate: null }],
};

export const DOMAINS: Domain[] = [
  {
    id: 'sales', name: 'Sales', steward: 'an', currency: 'VND',
    description: 'Đơn hàng, khách hàng đã ký hợp đồng, thanh toán và hoàn tiền. Nguồn chính là Postgres sales_raw.',
    datasets: [],
    sources: [
      { ref: 'sales_pg', kind: 'postgres', connection: 'sandbox_pg', location: 'schema sales_raw', available: true },
      { ref: 'sales_api', kind: 'rest_api', connection: 'mock_api', location: '/openapi.json', available: false, note: 'REST API chưa hỗ trợ ở Sprint 1' },
      { ref: 'landing_sales', kind: 'filesystem', connection: 'landing_sales', location: 'infra/landing/sales', available: false, note: 'Filesystem chưa hỗ trợ ở Sprint 1' },
    ],
  },
  {
    id: 'marketing', name: 'Marketing', steward: 'binh', currency: 'VND',
    description: 'Lead và contact thu từ chiến dịch. Chưa có nguồn nào đọc được ở Sprint 1.',
    datasets: [],
    sources: [
      { ref: 'mkt_api', kind: 'rest_api', connection: 'mock_api', location: '/openapi.json', available: false, note: 'REST API chưa hỗ trợ ở Sprint 1' },
    ],
  },
];

export const GLOSSARY: GlossaryTerm[] = [
  { id: 'sales.order', name: 'Đơn hàng', domain: 'sales', canonical: true, definition: 'Đơn hàng đã xác nhận.', tables: ['sales_pg.orders'] },
  { id: 'sales.customer', name: 'Khách hàng', domain: 'sales', canonical: false, definition: 'Tài khoản đã ký hợp đồng.', tables: ['sales_pg.customers', 'sales_pg.accounts'] },
  { id: 'sales.payment', name: 'Thanh toán', domain: 'sales', canonical: true, definition: 'Khoản thanh toán đã ghi nhận cho một đơn hàng.', tables: ['sales_api.payments'] },
  { id: 'sales.refund', name: 'Hoàn tiền', domain: 'sales', canonical: true, definition: 'Khoản hoàn tiền đã duyệt.', tables: ['landing_sales.refunds'] },
  { id: 'sales.revenue', name: 'Doanh thu', domain: 'sales', canonical: true, definition: 'Tổng giá trị đơn hàng đã xác nhận, quy về VND theo tỷ giá trong domains.yml.', tables: [] },
  { id: 'marketing.customer', name: 'Khách hàng', domain: 'marketing', canonical: false, definition: 'Người để lại email.', tables: ['mkt_api.leads', 'mkt_api.contacts'] },
];

const ORDERS_SAMPLE = [
  { order_id: '10001', customer_id: '501', amount_text: '1.250.000', ordered_at_text: '2026-09-28', status: 'paid' },
  { order_id: '10002', customer_id: '502', amount_text: '89.000', ordered_at_text: '2026-09-28', status: 'paid' },
  { order_id: '10003', customer_id: '517', amount_text: '420000', ordered_at_text: '28/09/2026', status: 'paid' },
  { order_id: '10004', customer_id: '501', amount_text: 'N/A', ordered_at_text: '2026-09-29', status: 'paid' },
  { order_id: '10005', customer_id: '533', amount_text: '2.100.000', ordered_at_text: '2026-09-29', status: 'paid' },
];
const STG_ORDERS_SAMPLE = [
  { order_id: '10001', customer_id: '501', amount_vnd: '1250000.00', order_date: '2026-09-28', status: 'paid' },
  { order_id: '10002', customer_id: '502', amount_vnd: '89000.00', order_date: '2026-09-28', status: 'paid' },
  { order_id: '10003', customer_id: '517', amount_vnd: '420000.00', order_date: '2026-09-28', status: 'paid' },
  { order_id: '10005', customer_id: '533', amount_vnd: '2100000.00', order_date: '2026-09-29', status: 'paid' },
];

const ORDER_COLUMNS = {
  source: [
    { name: 'order_id', type: 'integer', primaryKey: true, nullable: false, terms: ['sales.order'], description: 'Mã đơn hàng' },
    { name: 'customer_id', type: 'integer', nullable: false, terms: ['sales.customer'], description: 'Khách đặt đơn', references: 'customers.customer_id' },
    { name: 'amount_text', type: 'varchar', description: 'Giá trị đơn dạng chữ, nhiều định dạng số', tags: ['cần ép kiểu'] },
    { name: 'ordered_at_text', type: 'varchar', description: 'Ngày đặt dạng chữ (yyyy-mm-dd hoặc dd/mm/yyyy)', tags: ['cần ép kiểu'] },
    { name: 'status', type: 'varchar', description: 'Trạng thái đơn' },
  ],
  staged: [
    { name: 'order_id', type: 'bigint', primaryKey: true, nullable: false, terms: ['sales.order'] },
    { name: 'customer_id', type: 'bigint', nullable: false, terms: ['sales.customer'] },
    { name: 'amount_vnd', type: 'decimal(18,2)', description: 'amount_text ép kiểu, quy về VND', terms: ['sales.revenue'] },
    { name: 'order_date', type: 'date', description: 'ordered_at_text ép kiểu ngày' },
    { name: 'status', type: 'varchar' },
  ],
};
const CUSTOMER_COLUMNS = [
  { name: 'customer_id', type: 'integer', primaryKey: true, nullable: false, terms: ['sales.customer'] },
  { name: 'full_name', type: 'varchar', tags: ['PII'] },
  { name: 'email', type: 'varchar', tags: ['PII'] },
  { name: 'city', type: 'varchar' },
  { name: 'signed_at', type: 'date', description: 'Ngày ký hợp đồng' },
];

const q = (id: string, name: string, state: 'pass' | 'fail' | 'unmeasured', value: string, detail: string, at: string, runId?: string) => ({ id, name, state, value, detail, at, runId });

export const DATASETS: Dataset[] = [
  // ── Nguồn (đọc từ Postgres qua information_schema) ──
  {
    id: 'sales.source.orders', name: 'sales_raw.orders', layer: 'source', platform: 'postgres', domain: 'sales', sourceRef: 'sales_pg',
    description: 'Bảng đơn hàng gốc của hệ thống bán hàng. Giá trị và ngày lưu dạng chữ nên phải làm sạch trước khi dùng.',
    owner: 'an', tags: ['nguồn gốc', 'partition bắt buộc'], terms: ['sales.order'], columns: ORDER_COLUMNS.source,
    rowCount: 12480, updatedAt: '2026-10-02T01:55:00Z', upstream: [], quality: [], sample: ORDERS_SAMPLE, runs: ['a0c4d7e3', '7c2e91a4', 'b41f07d2'],
  },
  {
    id: 'sales.source.customers', name: 'sales_raw.customers', layer: 'source', platform: 'postgres', domain: 'sales', sourceRef: 'sales_pg',
    description: 'Khách hàng đã ký hợp đồng. Chứa thông tin cá nhân (PII).',
    owner: 'an', tags: ['nguồn gốc', 'PII'], terms: ['sales.customer'], columns: CUSTOMER_COLUMNS,
    rowCount: 3150, updatedAt: '2026-10-01T22:10:00Z', upstream: [], quality: [], runs: ['b41f07d2'],
    sample: [
      { customer_id: '501', full_name: 'Nguyễn ***', email: 'n***@mail.vn', city: 'Hà Nội', signed_at: '2025-11-03' },
      { customer_id: '502', full_name: 'Trần ***', email: 't***@mail.vn', city: 'TP.HCM', signed_at: '2026-01-14' },
    ],
  },
  {
    id: 'sales.source.payments', name: 'sales_api.payments', layer: 'source', platform: 'rest_api', domain: 'sales', sourceRef: 'sales_api',
    description: 'Thanh toán ghi nhận cho đơn hàng, lấy qua REST API.', owner: 'an', tags: ['nguồn gốc'], terms: ['sales.payment'],
    columns: [], rowCount: null, updatedAt: null, upstream: [], quality: [], sample: [], runs: ['e9a3c518'],
    unavailable: 'REST API chưa hỗ trợ ở Sprint 1: agent chưa đọc được schema và dữ liệu mẫu.',
  },
  {
    id: 'sales.source.refunds', name: 'landing_sales.refunds', layer: 'source', platform: 'filesystem', domain: 'sales', sourceRef: 'landing_sales',
    description: 'File hoàn tiền thả vào thư mục landing.', owner: 'an', tags: ['nguồn gốc'], terms: ['sales.refund'],
    columns: [], rowCount: null, updatedAt: null, upstream: [], quality: [], sample: [], runs: [],
    unavailable: 'Filesystem chưa hỗ trợ ở Sprint 1.',
  },
  {
    id: 'marketing.source.leads', name: 'mkt_api.leads', layer: 'source', platform: 'rest_api', domain: 'marketing', sourceRef: 'mkt_api',
    description: 'Lead thu từ form chiến dịch.', owner: 'binh', tags: ['nguồn gốc', 'PII'], terms: ['marketing.customer'],
    columns: [], rowCount: null, updatedAt: null, upstream: [], quality: [], sample: [], runs: ['5d8b2f60'],
    unavailable: 'REST API chưa hỗ trợ ở Sprint 1.',
  },
  {
    id: 'marketing.source.contacts', name: 'mkt_api.contacts', layer: 'source', platform: 'rest_api', domain: 'marketing', sourceRef: 'mkt_api',
    description: 'Contact đã xác thực email.', owner: 'binh', tags: ['nguồn gốc', 'PII'], terms: ['marketing.customer'],
    columns: [], rowCount: null, updatedAt: null, upstream: [], quality: [], sample: [], runs: [],
    unavailable: 'REST API chưa hỗ trợ ở Sprint 1.',
  },
  // ── Bronze (dlt nạp nguyên trạng) ──
  {
    id: 'sales.bronze.raw_orders', name: 'bronze.raw_orders', layer: 'bronze', platform: 'duckdb', domain: 'sales',
    description: 'Bản sao nguyên trạng của sales_raw.orders do dlt nạp, ghi đè mỗi lần chạy.', owner: 'minh', tags: ['dlt'], terms: ['sales.order'],
    columns: ORDER_COLUMNS.source, rowCount: 12480, updatedAt: '2026-10-02T02:00:41Z', schedule: '0 2 * * *', upstream: ['sales.source.orders'],
    quality: [q('row_reconcile:orders', 'Đối soát dòng', 'pass', '12.480 → 12.480', 'Số dòng nguồn → bronze khớp', '2026-10-02T02:00:41Z', '7c2e91a4'),
      q('idempotent:orders', 'Idempotent', 'pass', '0 dòng mới', 'Chạy lần 2 không phát sinh dòng mới', '2026-10-02T02:01:10Z', '7c2e91a4')],
    sample: ORDERS_SAMPLE, runs: ['7c2e91a4', 'b41f07d2', 'a0c4d7e3'],
  },
  {
    id: 'sales.bronze.raw_customers', name: 'bronze.raw_customers', layer: 'bronze', platform: 'duckdb', domain: 'sales',
    description: 'Bản sao nguyên trạng của sales_raw.customers.', owner: 'minh', tags: ['dlt', 'PII'], terms: ['sales.customer'],
    columns: CUSTOMER_COLUMNS, rowCount: 3150, updatedAt: '2026-10-01T09:12:05Z', upstream: ['sales.source.customers'],
    quality: [q('row_reconcile:customers', 'Đối soát dòng', 'pass', '3.150 → 3.150', 'Số dòng nguồn → bronze khớp', '2026-10-01T09:12:05Z', 'b41f07d2')],
    sample: [], runs: ['b41f07d2'],
  },
  // ── Staging (dbt làm sạch, ép kiểu, tách dòng rác) ──
  {
    id: 'sales.staging.stg_orders', name: 'staging.stg_daily_revenue__orders', layer: 'staging', platform: 'duckdb', domain: 'sales',
    description: 'Đơn hàng đã ép kiểu số tiền và ngày; dòng ép kiểu lỗi tách sang rejected.', owner: 'minh', tags: ['dbt'], terms: ['sales.order', 'sales.revenue'],
    columns: ORDER_COLUMNS.staged, rowCount: 12391, updatedAt: '2026-10-02T02:01:02Z', schedule: '0 2 * * *', upstream: ['sales.bronze.raw_orders'],
    quality: [q('staging_reconcile:orders', 'Làm sạch staging', 'pass', '12.391 sạch · 89 rác', '0,71% rác (ngưỡng 1%)', '2026-10-02T02:01:02Z', '7c2e91a4'),
      q('dbt_test:stg_orders', 'dbt tests', 'pass', '4 / 4 đạt', 'unique + not_null order_id, not_null amount_vnd, order_date', '2026-10-02T02:01:05Z', '7c2e91a4')],
    sample: STG_ORDERS_SAMPLE, runs: ['7c2e91a4', 'b41f07d2', 'a0c4d7e3'],
  },
  {
    id: 'sales.staging.stg_customers', name: 'staging.stg_customer_revenue__customers', layer: 'staging', platform: 'duckdb', domain: 'sales',
    description: 'Khách hàng đã chuẩn hóa khóa và ngày ký.', owner: 'minh', tags: ['dbt', 'PII'], terms: ['sales.customer'],
    columns: CUSTOMER_COLUMNS.map(c => c.name === 'customer_id' ? { ...c, type: 'bigint' } : c), rowCount: 3150, updatedAt: '2026-10-01T09:12:30Z', upstream: ['sales.bronze.raw_customers'],
    quality: [q('staging_reconcile:customers', 'Làm sạch staging', 'pass', '3.150 sạch · 0 rác', '0% rác (ngưỡng 1%)', '2026-10-01T09:12:30Z', 'b41f07d2')],
    sample: [], runs: ['b41f07d2'],
  },
  // ── Gold (model nghiệp vụ) ──
  {
    id: 'sales.gold.fct_daily_revenue', name: 'gold.fct_daily_revenue', layer: 'gold', platform: 'duckdb', domain: 'sales',
    description: 'Doanh thu và số đơn hàng theo từng ngày đặt hàng. Đã duyệt và deploy lên Dagster local.', owner: 'minh', tags: ['dbt', 'đã deploy'], terms: ['sales.revenue', 'sales.order'],
    columns: [
      { name: 'order_date', type: 'date', primaryKey: true, nullable: false, description: 'Grain: một dòng mỗi ngày' },
      { name: 'revenue', type: 'decimal(18,2)', description: 'SUM(amount_vnd)', terms: ['sales.revenue'] },
      { name: 'order_count', type: 'bigint', description: 'COUNT(DISTINCT order_id)', terms: ['sales.order'] },
    ],
    rowCount: 365, updatedAt: '2026-10-02T02:01:20Z', schedule: '0 2 * * *', upstream: ['sales.staging.stg_orders'],
    quality: [q('metric_reconcile:revenue', 'Đúng chỉ số', 'pass', '15,82 tỷ = 15,82 tỷ', 'Tổng revenue gold khớp tổng amount_vnd staging', '2026-10-02T02:01:20Z', '7c2e91a4'),
      q('dbt_test:fct_daily_revenue', 'dbt tests', 'pass', '2 / 2 đạt', 'unique + not_null order_date', '2026-10-02T02:01:20Z', '7c2e91a4'),
      q('freshness:fct_daily_revenue', 'Độ tươi', 'pass', '12 phút', 'Ngưỡng 60 phút (objectives.yml)', '2026-10-02T02:13:00Z')],
    sample: [
      { order_date: '2026-09-28', revenue: '48250000.00', order_count: '41' },
      { order_date: '2026-09-29', revenue: '51730000.00', order_count: '44' },
      { order_date: '2026-09-30', revenue: '39980000.00', order_count: '35' },
    ],
    runs: ['7c2e91a4'],
  },
  {
    id: 'sales.gold.fct_customer_revenue', name: 'gold.fct_customer_revenue', layer: 'gold', platform: 'duckdb', domain: 'sales',
    description: 'Tổng doanh thu và số đơn hàng của từng khách hàng, kèm tên. Đang chờ Reviewer duyệt.', owner: 'minh', tags: ['dbt', 'PII', 'chờ duyệt'], terms: ['sales.revenue', 'sales.customer'],
    columns: [
      { name: 'customer_id', type: 'bigint', primaryKey: true, nullable: false, terms: ['sales.customer'] },
      { name: 'full_name', type: 'varchar', tags: ['PII'] },
      { name: 'revenue', type: 'decimal(18,2)', terms: ['sales.revenue'] },
      { name: 'order_count', type: 'bigint', terms: ['sales.order'] },
    ],
    rowCount: 3102, updatedAt: '2026-10-01T09:13:02Z', upstream: ['sales.staging.stg_orders', 'sales.staging.stg_customers'],
    quality: [q('metric_reconcile:revenue', 'Đúng chỉ số', 'pass', '15,82 tỷ = 15,82 tỷ', 'Tổng revenue gold khớp staging', '2026-10-01T09:13:02Z', 'b41f07d2'),
      q('dbt_test:fct_customer_revenue', 'dbt tests', 'pass', '3 / 3 đạt', 'unique + not_null customer_id, relationships', '2026-10-01T09:13:02Z', 'b41f07d2'),
      q('freshness:fct_customer_revenue', 'Độ tươi', 'unmeasured', 'Chưa deploy', 'Đo sau khi chạy theo lịch', '2026-10-01T09:13:02Z')],
    sample: [
      { customer_id: '501', full_name: 'Nguyễn ***', revenue: '18450000.00', order_count: '12' },
      { customer_id: '533', full_name: 'Đỗ ***', revenue: '9200000.00', order_count: '5' },
    ],
    runs: ['b41f07d2'],
  },
];
for (const domain of DOMAINS) domain.datasets = DATASETS.filter(d => d.domain === domain.id).map(d => d.id);

// ── Run (cùng mô hình `Run` của lib/studio.ts để sau này nối thẳng toStudioRun) ──

const plan = (p: Partial<Plan>): Plan => ({ sources: [], tables: [], primaryKey: '—', cursor: '—', metric: '—', schedule: '—', writeMode: '—', model: '—', filters: '', ...p });
const check = (id: string, name: string, state: Check['state'], value: string, detail: string): Check => ({ id, name, state, value, detail });
const log = (id: string, stage: number, level: Log['level'], message: string, at: string, detail?: string): Log => ({ id, stage, level, message, at, detail });

const dagster = (model: string) => `from dagster import AssetExecutionContext, Definitions, ScheduleDefinition, define_asset_job
from dagster_dbt import DbtCliResource, dbt_assets

from .ingest import ${model}_source


@dbt_assets(manifest="dbt/target/manifest.json")
def ${model}_dbt(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


job = define_asset_job("${model}_job", selection=[${model}_source, ${model}_dbt])
defs = Definitions(
    assets=[${model}_source, ${model}_dbt],
    schedules=[ScheduleDefinition(job=job, cron_schedule="0 2 * * *")],
    resources={"dbt": DbtCliResource(project_dir="dbt")},
)
`;
const ingest = (model: string, tables: string[]) => `import dlt
from dlt.sources.sql_database import sql_table


@dlt.source(name="${model}_source")
def ${model}_source():
${tables.map(t => `    yield sql_table(schema="sales_raw", table="${t}", write_disposition="replace")`).join('\n')}


pipeline = dlt.pipeline(pipeline_name="${model}", destination="duckdb", dataset_name="bronze")
`;
const STG_ORDERS_SQL = `-- Sinh từ templates/dbt/staging.sql.j2 theo Spec. Không sửa tay.
with source as (
    select * from {{ source('bronze', 'raw_orders') }}
),
typed as (
    select
        cast(order_id as bigint) as order_id,
        cast(customer_id as bigint) as customer_id,
        try_cast(replace(amount_text, '.', '') as decimal(18, 2)) as amount_vnd,
        coalesce(try_strptime(ordered_at_text, '%Y-%m-%d'), try_strptime(ordered_at_text, '%d/%m/%Y'))::date as order_date,
        status
    from source
)
select * from typed
where amount_vnd is not null and order_date is not null
`;

export const RUNS: CatalogRun[] = [
  {
    id: '7c2e91a4-3b0d-4f61-9a52-0e1d7f3c8b10', title: 'Tính doanh thu và số đơn hàng theo từng ngày đặt hàng', domain: 'sales', owner: 'minh',
    request: 'Tính doanh thu và số đơn hàng theo từng ngày đặt hàng', version: 1, filesHash: 'sha256:5f2a9c81d04be7a3c6e1', status: 'DEPLOYED', stage: 14,
    plan: plan({ sources: ['postgres · sales_pg'], tables: ['orders'], primaryKey: 'order_id', cursor: 'không có', metric: 'revenue = SUM(amount_vnd); order_count = COUNT(DISTINCT order_id)', schedule: '0 2 * * *', writeMode: 'replace', model: 'fct_daily_revenue' }),
    summary: 'Đọc sales_raw.orders, ép kiểu amount_text và ordered_at_text ở staging, gộp theo order_date ở gold.',
    checks: [
      check('row_reconcile:orders', 'Đối soát dòng · orders', 'pass', '12.480 → 12.480', 'Số dòng nguồn → bronze'),
      check('idempotent:orders', 'Idempotent · orders', 'pass', '0 dòng mới', 'Chạy lần 2 không phát sinh dòng mới'),
      check('staging_reconcile:orders', 'Làm sạch staging · orders', 'pass', '12.391 sạch · 89 rác', '0,71% rác (ngưỡng 1%)'),
      check('dbt_test', 'dbt tests', 'pass', '6 / 6 đạt', 'dbt build trong sandbox'),
      check('metric_reconcile', 'Đúng chỉ số', 'pass', 'Khớp', 'revenue gold = SUM(amount_vnd) staging'),
      check('gate:spec_schema', 'Spec hợp lệ', 'pass', 'Đạt', 'Spec khớp JSON schema'),
      check('gate:schema_real', 'Cột có thật ở nguồn', 'pass', 'Đạt', '5/5 cột tồn tại ở sales_raw.orders'),
      check('gate:ast_guard', 'Biểu thức an toàn', 'pass', 'Đạt', 'Chỉ dùng hàm trong danh sách cho phép'),
      check('gate:budget', 'Ngân sách', 'pass', 'Đạt', '0,0031 USD ước tính / trần 0,05 USD'),
      check('gate:post_run', 'Tổng kết chạy thử', 'pass', 'Đạt', 'gold 365 dòng'),
    ],
    logs: [
      log('l1', 1, 'info', 'Kế hoạch: 1 nguồn (sales_pg.orders), model fct_daily_revenue', '2026-10-01T07:58:02Z'),
      log('l2', 4, 'warning', 'Spec lần 1 bị từ chối, gửi lỗi lại cho LLM', '2026-10-01T07:58:09Z', "cột 'amount' không có ở nguồn; dùng amount_text"),
      log('l3', 4, 'success', 'Spec lần 2 hợp lệ', '2026-10-01T07:58:14Z'),
      log('l4', 9, 'success', 'dlt nạp 12.480 dòng vào bronze.raw_orders', '2026-10-01T07:58:21Z'),
      log('l5', 11, 'success', 'dbt build: 6 model/test đạt', '2026-10-01T07:58:30Z'),
      log('l6', 14, 'success', 'Lan Phạm duyệt; PR #12 đã merge, deployer xác nhận đã lên production.', '2026-10-01T10:28:00Z'),
    ],
    specRetries: 1, sqlRetries: 0, llmCalls: 3, updatedAt: '2026-10-01T10:20:00Z', usd: 0.0031, llmUsd: 0, llmTokens: 0, llmUnpriced: 0, fileCount: 5,
    assets: [
      { name: 'bronze.raw_orders', layer: 'Bronze', kind: 'postgres · sales_pg', rows: '12.480', state: 'Khớp nguồn' },
      { name: 'staging.stg_daily_revenue__orders', layer: 'Staging', kind: 'Model dbt', rows: '12.391', state: '89 dòng rác' },
      { name: 'gold.fct_daily_revenue', layer: 'Gold', kind: 'Model dbt', rows: '365', state: 'Đạt' },
    ],
    approval: { decision: 'approved', reviewer: 'lan', files_hash: 'sha256:5f2a9c81d04be7a3c6e1', version: 1, at: '2026-10-01T10:20:00Z', comment: 'Số khớp, ok deploy.' },
    deployment: { repo: 'atomesus/pipelines', branch: 'pipeline/fct_daily_revenue/v1', pr_number: 12,
      pr_url: 'https://github.com/atomesus/pipelines/pull/12', head_sha: 'a'.repeat(40), state: 'merged', opened_at: '2026-10-01T10:21:00Z',
      ci_conclusion: 'success', ci_url: 'https://github.com/atomesus/pipelines/actions/runs/120', ci_at: '2026-10-01T10:24:00Z',
      merged_at: '2026-10-01T10:26:00Z', merge_commit_sha: 'b'.repeat(40), deployed_commit_sha: 'b'.repeat(40), deployed_at: '2026-10-01T10:28:00Z' },
    error: null, question: null,
    files: {
      'dagster/definitions.py': dagster('fct_daily_revenue'),
      'dagster/ingest.py': ingest('fct_daily_revenue', ['orders']),
      'dbt/models/staging/stg_daily_revenue__orders.sql': STG_ORDERS_SQL,
      'dbt/models/gold/fct_daily_revenue.sql': `-- Sinh từ templates/dbt/gold.sql.j2 theo Spec. Không sửa tay.
select
    order_date,
    sum(amount_vnd) as revenue,
    count(distinct order_id) as order_count
from {{ ref('stg_daily_revenue__orders') }}
group by order_date
`,
      'dbt/models/schema.yml': `version: 2
models:
  - name: stg_daily_revenue__orders
    columns:
      - name: order_id
        tests: [unique, not_null]
      - name: amount_vnd
        tests: [not_null]
      - name: order_date
        tests: [not_null]
  - name: fct_daily_revenue
    columns:
      - name: order_date
        tests: [unique, not_null]
`,
    },
  },
  {
    id: 'b41f07d2-9e6a-4c38-8d15-6a2f0b9e4c27', title: 'Tổng doanh thu và số đơn hàng của từng khách hàng, kèm tên', domain: 'sales', owner: 'minh',
    request: 'Tổng doanh thu và số đơn hàng của từng khách hàng, kèm tên khách hàng', version: 1, filesHash: 'sha256:a91e04c7f3d25b8e0c44', status: 'READY_FOR_REVIEW', stage: 14,
    plan: plan({ sources: ['postgres · sales_pg', 'postgres · sales_pg'], tables: ['orders', 'customers'], primaryKey: 'order_id | customer_id', cursor: 'không có | không có', metric: 'revenue = SUM(amount_vnd); order_count = COUNT(DISTINCT order_id)', schedule: '0 3 * * *', writeMode: 'replace | replace', model: 'fct_customer_revenue' }),
    summary: 'Join stg orders với stg customers theo customer_id, gộp theo khách hàng.',
    checks: [
      check('row_reconcile:orders', 'Đối soát dòng · orders', 'pass', '12.480 → 12.480', 'Số dòng nguồn → bronze'),
      check('row_reconcile:customers', 'Đối soát dòng · customers', 'pass', '3.150 → 3.150', 'Số dòng nguồn → bronze'),
      check('staging_reconcile:orders', 'Làm sạch staging · orders', 'pass', '12.391 sạch · 89 rác', '0,71% rác (ngưỡng 1%)'),
      check('staging_reconcile:customers', 'Làm sạch staging · customers', 'pass', '3.150 sạch · 0 rác', '0% rác (ngưỡng 1%)'),
      check('dbt_test', 'dbt tests', 'pass', '7 / 7 đạt', 'dbt build trong sandbox'),
      check('gate:spec_schema', 'Spec hợp lệ', 'pass', 'Đạt', 'Spec khớp JSON schema'),
      check('gate:schema_real', 'Cột có thật ở nguồn', 'pass', 'Đạt', '7/7 cột tồn tại'),
      check('gate:ast_guard', 'Biểu thức an toàn', 'pass', 'Đạt', 'Chỉ dùng hàm cho phép'),
      check('gate:budget', 'Ngân sách', 'pass', 'Đạt', '0,0044 USD ước tính / trần 0,05 USD'),
      check('gate:post_run', 'Tổng kết chạy thử', 'pass', 'Đạt', 'gold 3.102 dòng'),
    ],
    logs: [
      log('m1', 1, 'info', 'Kế hoạch: 2 nguồn (orders, customers), model fct_customer_revenue', '2026-10-01T09:11:40Z'),
      log('m2', 4, 'success', 'Spec lần 1 hợp lệ', '2026-10-01T09:11:52Z'),
      log('m3', 9, 'success', 'dlt nạp 12.480 + 3.150 dòng', '2026-10-01T09:12:05Z'),
      log('m4', 14, 'success', 'Mọi kiểm tra đạt. Chờ Reviewer duyệt.', '2026-10-01T09:13:02Z'),
    ],
    specRetries: 0, sqlRetries: 0, llmCalls: 2, updatedAt: '2026-10-01T09:13:02Z', usd: 0.0044, llmUsd: 0, llmTokens: 0, llmUnpriced: 0, fileCount: 4,
    assets: [
      { name: 'bronze.raw_orders', layer: 'Bronze', kind: 'postgres · sales_pg', rows: '12.480', state: 'Khớp nguồn' },
      { name: 'bronze.raw_customers', layer: 'Bronze', kind: 'postgres · sales_pg', rows: '3.150', state: 'Khớp nguồn' },
      { name: 'staging.stg_customer_revenue__customers', layer: 'Staging', kind: 'Model dbt', rows: '3.150', state: '0 dòng rác' },
      { name: 'gold.fct_customer_revenue', layer: 'Gold', kind: 'Model dbt', rows: '3.102', state: 'Đạt' },
    ],
    approval: null, error: null, question: null,
    files: {
      'dagster/definitions.py': dagster('fct_customer_revenue'),
      'dagster/ingest.py': ingest('fct_customer_revenue', ['orders', 'customers']),
      'dbt/models/staging/stg_customer_revenue__orders.sql': STG_ORDERS_SQL,
      'dbt/models/gold/fct_customer_revenue.sql': `-- Sinh từ templates/dbt/gold.sql.j2 theo Spec. Không sửa tay.
select
    c.customer_id,
    c.full_name,
    sum(o.amount_vnd) as revenue,
    count(distinct o.order_id) as order_count
from {{ ref('stg_customer_revenue__orders') }} o
join {{ ref('stg_customer_revenue__customers') }} c using (customer_id)
group by c.customer_id, c.full_name
`,
    },
  },
  {
    id: 'e9a3c518-04f7-4b2e-b6d9-31c85a7e0f42', title: 'Ghép đơn hàng Postgres với thanh toán từ REST API, tính doanh thu…', domain: 'sales', owner: 'minh',
    request: 'Ghép đơn hàng Postgres với thanh toán từ REST API, tính doanh thu theo ngày', version: 1, filesHash: '', status: 'NEEDS_HUMAN', stage: 3,
    plan: plan({ sources: ['sales_pg', 'sales_api'], tables: ['orders', 'payments'], model: 'fct_daily_paid_revenue', schedule: '0 2 * * *' }),
    summary: 'Cần sales_api.payments, nguồn REST API chưa đọc được.',
    checks: [], logs: [
      log('n1', 1, 'info', 'Kế hoạch: 2 nguồn (sales_pg.orders, sales_api.payments)', '2026-10-02T03:40:10Z'),
      log('n2', 3, 'error', 'chưa hỗ trợ đọc nguồn rest_api \'sales_api\' ở Sprint 1', '2026-10-02T03:40:12Z', 'Nguồn: sales_api'),
    ],
    specRetries: 0, sqlRetries: 0, llmCalls: 1, updatedAt: '2026-10-02T03:40:12Z', usd: 0, llmUsd: 0, llmTokens: 0, llmUnpriced: 0, fileCount: 0, assets: [], approval: null,
    error: "chưa hỗ trợ đọc nguồn rest_api 'sales_api' ở Sprint 1", question: null, files: {},
  },
  {
    id: '5d8b2f60-7a1c-4e93-a0b4-c2f9e81d3a65', title: 'Đếm khách hàng mới theo tháng từ sales và marketing', domain: 'sales', owner: 'minh',
    request: 'Đếm khách hàng mới theo tháng, gộp khách hàng của sales và marketing', version: 1, filesHash: '', status: 'BLOCKED_SEMANTIC_CONFLICT', stage: 5,
    plan: plan({ sources: ['sales_pg', 'mkt_api'], tables: ['customers', 'leads'], model: 'fct_new_customers_monthly' }),
    summary: 'Gộp thực thể customer ở hai domain.',
    checks: [check('gate:semantic', 'Ngữ nghĩa', 'fail', 'Không đạt', "'customer' có định nghĩa khác nhau ở sales và marketing; conformed_dimensions rỗng")],
    logs: [log('p1', 5, 'error', "Thực thể 'customer' trùng tên ở sales và marketing", '2026-10-02T04:05:31Z', 'sales: tài khoản đã ký hợp đồng\nmarketing: người để lại email')],
    specRetries: 0, sqlRetries: 0, llmCalls: 2, updatedAt: '2026-10-02T04:05:31Z', usd: 0, llmUsd: 0, llmTokens: 0, llmUnpriced: 0, fileCount: 0, assets: [], approval: null,
    error: "Thực thể 'customer' trùng tên ở sales (tài khoản đã ký hợp đồng) và marketing (người để lại email); conformed_dimensions chưa công nhận.", question: null, files: {},
  },
  {
    id: 'a0c4d7e3-6f25-4b81-9e07-d5b3a2c8f190', title: 'Làm sạch đơn hàng Postgres', domain: 'sales', owner: 'minh',
    request: 'Làm sạch đơn hàng Postgres', version: 1, filesHash: '', status: 'NEEDS_HUMAN', stage: 10,
    plan: plan({ sources: ['postgres · sales_pg'], tables: ['orders'], model: 'stg_orders_manual' }),
    summary: 'Run làm sạch thủ công: đổi tên cột và ép kiểu, không dùng LLM.',
    checks: [check('staging_reconcile:orders', 'Làm sạch staging · orders', 'fail', '11.951 sạch · 529 rác', '4,24% rác (ngưỡng 1%)')],
    logs: [log('c1', 10, 'warning', '529 dòng ép kiểu lỗi (amount_text dạng "1.250.000")', '2026-09-30T15:22:10Z', 'decimal(18,2): 529 dòng\ndate: 0 dòng')],
    specRetries: 0, sqlRetries: 0, llmCalls: 0, updatedAt: '2026-09-30T15:22:10Z', usd: 0, llmUsd: 0, llmTokens: 0, llmUnpriced: 0, fileCount: 0, assets: [], approval: null,
    error: 'Tỷ lệ dòng bị tách 4,24% vượt ngưỡng 1% (objectives.yml sales.quality.max_reject_pct).', question: null, files: {},
  },
];

/** Giữ run chờ duyệt, thêm một version đã duyệt có PR đang mở để kiểm tra luồng Deploy. */
RUNS.push({
  ...RUNS[1], id: 'c28f4a90-6b1d-4e2c-9012-a3456789b012', status: 'APPROVED',
  approval: { decision: 'approved', reviewer: 'lan', files_hash: RUNS[1].filesHash, version: 1, at: '2026-10-02T09:20:00Z', comment: 'Đã đối chiếu, mở PR.' },
  deployment: { repo: 'atomesus/pipelines', branch: 'pipeline/fct_customer_revenue/v1', pr_number: 13,
    pr_url: 'https://github.com/atomesus/pipelines/pull/13', head_sha: 'c'.repeat(40), state: 'open', opened_at: '2026-10-02T09:21:00Z',
    ci_url: 'https://github.com/atomesus/pipelines/actions/runs/130' },
  logs: [...RUNS[1].logs, log('m5', 14, 'success', 'Đã duyệt; PR #13 đang mở, chờ CI và merge.', '2026-10-02T09:21:00Z')],
  updatedAt: '2026-10-02T09:21:00Z',
});

export const POLICY: PolicyFile[] = [
  { name: 'domains.yml', description: 'Domain, nguồn được phép đọc, thực thể và steward.', content: `# Domain, nguồn và thực thể (SPEC §8.4). Steward sở hữu file này; sửa qua pull request.
domains:
  sales:
    datasets: [sales_raw]
    steward: an@company.vn
    currency:
      base: VND
      rates: { VND: 1, USD: 25400 }
    sources:
      sales_pg:      { kind: postgres,   connection_ref: sandbox_pg,    schema: sales_raw }
      sales_api:     { kind: rest_api,   connection_ref: mock_api,      openapi: /openapi.json }
      landing_sales: { kind: filesystem, connection_ref: landing_sales, root: infra/landing/sales }
    entities:
      customer: { canonical: false, definition: "tài khoản đã ký hợp đồng", tables: [sales_pg.customers, sales_pg.accounts] }
      order:    { canonical: true,  definition: "đơn hàng đã xác nhận", tables: [sales_pg.orders] }
      payment:  { canonical: true,  definition: "khoản thanh toán đã ghi nhận cho một đơn hàng", tables: [sales_api.payments] }
      refund:   { canonical: true,  definition: "khoản hoàn tiền đã duyệt", tables: [landing_sales.refunds] }
  marketing:
    datasets: [mkt_raw]
    steward: binh@company.vn
    sources:
      mkt_api: { kind: rest_api, connection_ref: mock_api, openapi: /openapi.json }
    entities:
      customer: { canonical: false, definition: "người để lại email", tables: [mkt_api.leads, mkt_api.contacts] }

conformed_dimensions: []   # rỗng nghĩa là chưa có thực thể nào được công nhận dùng chung
` },
  { name: 'budgets.yml', description: 'Trần chi phí, số lần gọi LLM và luật SQL.', content: `# Trần chi phí và số lần thử (SPEC §8.4). Mọi trần trong code đọc từ đây (NFR-04).
pricing:
  usd_per_tb: 6.25
per_run:
  max_bytes_scanned: 5_000_000_000
  max_usd: 0.05
  max_llm_calls: 6
  max_chat_calls: 20
  max_spec_attempts: 2
  max_sql_attempts: 2
  phase_timeout_sec: 300
per_day:
  max_usd: 2.00
rules:
  - require_partition_filter_on: [sales_pg.orders]
  - forbid_select_star_in: [gold]
  - max_tables_joined: 6
` },
  { name: 'objectives.yml', description: 'Ngưỡng chất lượng, độ tươi và suy giảm theo domain.', content: `# Hàm mục tiêu và ngưỡng theo domain (SPEC §8.4).
sales:
  hard: { freshness_minutes: 60 }
  soft: { cost_usd_per_day: 1.0 }
  prefer: cheapest_feasible
  quality: { max_reject_pct: 1.0 }
  regression: { window_runs: 7, runtime_increase_pct: 50, cost_increase_pct: 40, test_fail_rate_pct: 5, cooldown_hours: 24 }
marketing:
  hard: { freshness_minutes: 1440 }
  soft: { cost_usd_per_day: 0.5 }
  prefer: cheapest_feasible
  quality: { max_reject_pct: 2.0 }
  regression: { window_runs: 7, runtime_increase_pct: 50, cost_increase_pct: 40, test_fail_rate_pct: 5, cooldown_hours: 24 }
` },
];

/** Trần đọc từ budgets.yml, cùng hình dạng `Limits` của GET /policy. */
export const LIMITS = { max_llm_calls: 6, max_spec_attempts: 2, max_sql_attempts: 2, max_chat_calls: 20, max_usd_per_day: 2 };

export const RELATIONSHIPS: Relationship[] = [
  { id: 'fk:sales_pg.orders_customer_id_fkey', kind: 'foreign_key', from: 'sales.source.orders', fromColumns: ['customer_id'], to: 'sales.source.customers', toColumns: ['customer_id'], label: 'orders_customer_id_fkey', usedBy: [] },
  { id: 'join:orders-customers', kind: 'join', from: 'sales.source.orders', fromColumns: ['customer_id'], to: 'sales.source.customers', toColumns: ['customer_id'], label: 'inner join', usedBy: ['sales.gold.fct_customer_revenue'] },
];

export const MOCK_SNAPSHOT: Snapshot = {
  datasets: DATASETS, domains: DOMAINS, glossary: GLOSSARY, runs: RUNS, people: PEOPLE,
  policy: { files: POLICY, limits: LIMITS }, relationships: RELATIONSHIPS, conformed: [], live: false,
};

/** Pipeline đã deploy (fct_daily_revenue) đang chạy theo lịch trên Dagster. */
const DAGSTER_RUN = { runId: '5b1e9c02-7d4a-4f13-a6e8-2c90f1d3b7aa', job: 'fct_daily_revenue_job', status: 'SUCCESS', startedAt: '2026-10-02T02:00:03+07:00', endedAt: '2026-10-02T02:01:21+07:00' };
const DAGSTER_SCHEDULE = { name: 'fct_daily_revenue_job_schedule', cron: '0 2 * * *', timezone: 'Asia/Ho_Chi_Minh', running: true, nextTickAt: '2026-10-03T02:00:00+07:00' };
export const MOCK_ORCHESTRATION: Orchestration = {
  available: true, url: 'http://127.0.0.1:3070',
  assets: Object.fromEntries(['bronze/raw_orders', 'staging/stg_daily_revenue__orders', 'gold/fct_daily_revenue'].map(key => [key, {
    jobs: ['fct_daily_revenue_job'], lastMaterializedAt: '2026-10-02T02:01:20+07:00', lastMaterializedRunId: DAGSTER_RUN.runId, lastRun: DAGSTER_RUN, schedule: DAGSTER_SCHEDULE,
  }])),
};
