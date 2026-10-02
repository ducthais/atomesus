import test from 'node:test';
import assert from 'node:assert/strict';
import type { RunReport } from '../lib/postgres-api.ts';
import { buildSnapshot, type CatalogResponse } from '../lib/catalog/live.ts';
import { MOCK_SNAPSHOT } from '../lib/catalog/mock.ts';
import { Catalog } from '../lib/catalog/query.ts';
import { parseConnections } from '../lib/catalog/connections.ts';

const mock = new Catalog(MOCK_SNAPSHOT);

test('mock: mọi upstream, thuật ngữ và run tham chiếu đều tồn tại', () => {
  const ids = new Set(mock.datasets.map(d => d.id));
  const terms = new Set(mock.glossary.map(t => t.id));
  for (const d of mock.datasets) {
    for (const u of d.upstream) assert.ok(ids.has(u), `${d.id} → upstream ${u}`);
    for (const t of [...d.terms, ...d.columns.flatMap(c => c.terms ?? [])]) assert.ok(terms.has(t), `${d.id} → term ${t}`);
    for (const r of d.runs) assert.ok(mock.run(r), `${d.id} → run ${r}`);
  }
  for (const r of mock.relationships()) for (const id of [r.from, r.to, ...r.usedBy]) assert.ok(ids.has(id), `${r.id} → ${id}`);
});

test('lineage: gồm toàn bộ tổ tiên và hậu duệ, không lẫn nhánh khác', () => {
  const graph = mock.lineage('sales.staging.stg_orders');
  const ids = graph.nodes.map(n => n.id).sort();
  assert.deepEqual(ids, ['sales.bronze.raw_orders', 'sales.gold.fct_customer_revenue', 'sales.gold.fct_daily_revenue', 'sales.source.orders', 'sales.staging.stg_orders']);
  assert.ok(graph.edges.every(e => ids.includes(e.from) && ids.includes(e.to)));
  assert.equal(graph.focus, 'sales.staging.stg_orders');
  for (const d of mock.datasets) assert.ok(mock.lineage(d.id).nodes.some(n => n.id === d.id));
});

test('search: từ khóa khớp cả tên cột, facet đếm trên kết quả từ khóa', () => {
  assert.deepEqual(mock.search({ q: 'amount_vnd' }).items.map(d => d.id), ['sales.staging.stg_orders']);
  const gold = mock.search({ layer: 'gold' });
  assert.ok(gold.items.every(d => d.layer === 'gold'));
  assert.deepEqual(gold.facets.layer.map(f => f.value), ['source', 'bronze', 'staging', 'gold']);
  assert.equal(gold.facets.layer.reduce((s, f) => s + f.count, 0), mock.datasets.length);
});

test('run ↔ dataset: tra hai chiều theo id ngắn', () => {
  const run = mock.run('7c2e91a4');
  assert.ok(run);
  assert.ok(mock.datasetsForRun(run).some(d => d.id === 'sales.gold.fct_daily_revenue'));
  assert.equal(mock.runsForDataset(mock.dataset('sales.gold.fct_daily_revenue')!)[0].id, run.id);
});

test('glossary: thực thể trùng tên khác domain là xung đột, trừ khi đã conformed', () => {
  assert.deepEqual(mock.conflictsOf('sales.customer').map(t => t.id), ['marketing.customer']);
  assert.deepEqual(mock.conflictsOf('sales.order'), []);
  assert.deepEqual(new Catalog({ ...MOCK_SNAPSHOT, conformed: ['customer'] }).conflictsOf('sales.customer'), []);
  assert.equal(mock.datasetByName('sales_pg.orders')?.id, 'sales.source.orders');
  assert.equal(mock.datasetByName('sales_pg.accounts'), undefined);
});

// ── Snapshot dựng từ backend thật: hình dạng lấy từ GET /catalog và RunReport đang có trong sổ bằng chứng ──

const CATALOG: CatalogResponse = {
  domains: {
    sales: {
      steward: 'an@company.vn', currency: 'VND',
      sources: {
        sales_pg: { kind: 'postgres', connection_ref: 'sandbox_pg', location: 'sales_raw', available: true, tables: {
          orders: [{ name: 'order_id', type: 'integer' }, { name: 'customer_id', type: 'integer' }, { name: 'amount_text', type: 'text' }, { name: 'ordered_at_text', type: 'text' }],
          customers: [{ name: 'customer_id', type: 'integer' }, { name: 'customer_name', type: 'text' }],
        },
        primary_keys: { orders: ['order_id'], customers: ['customer_id'] },
        foreign_keys: [{ name: 'orders_customer_id_fkey', table: 'orders', columns: ['customer_id'], ref_schema: 'sales_raw', ref_table: 'customers', ref_columns: ['customer_id'] }] },
        sales_api: { kind: 'rest_api', connection_ref: 'mock_api', location: '/openapi.json', available: false, reason: 'Nguồn rest_api chưa hỗ trợ ở Sprint 1', tables: {} },
      },
      entities: {
        order: { canonical: true, definition: 'đơn hàng đã xác nhận', tables: ['sales_pg.orders'] },
        payment: { canonical: true, definition: 'khoản thanh toán', tables: ['sales_api.payments'] },
      },
    },
  },
  conformed_dimensions: [],
};
const agentRun = (id: string, status: string, created_at: string): RunReport => ({
  run_id: id, version: 1, domain: 'sales', created_by: 'minh', created_at, user_request: 'Doanh thu theo ngày', status, stage_reached: 'report',
  plan: { summary: 'Doanh thu và số đơn theo ngày đặt hàng.', model_name: 'fct_daily_sales', sources: [{ id: 'orders', source_ref: 'sales_pg', object: 'orders' }] },
  spec: {
    domain: 'sales',
    sources: [{ id: 'orders', kind: 'postgres', source_ref: 'sales_pg', table: 'orders', primary_key: ['order_id'], cursor_field: null, write_disposition: 'replace', target: { table: 'raw_orders' },
      staging: { columns: [{ from: 'order_id', as: 'order_id', type: 'integer' }, { from: 'amount_text', as: 'amount_vnd', type: 'decimal(18,2)' }, { from: 'ordered_at_text', as: 'ordered_at', type: 'date' }] } }],
    transform: { model_name: 'fct_daily_sales', grain: ['orders.ordered_at'], metrics: [{ name: 'total_revenue', expr: 'SUM(orders.amount_vnd)' }], filters: [] },
    schedule: { cron: '0 2 * * *' },
  },
  files: { 'dbt_project/models/gold/fct_daily_sales.sql': 'select 1' }, files_hash: 'sha256:abc',
  gates: [{ name: 'post_run', result: 'pass', detail: 'ok', observed: { gold_rows: 30, gold_samples: [{ ordered_at: '2026-01-01', total_revenue: '1000.00' }] } }],
  checks: [
    { name: 'row_reconcile', source_id: 'orders', result: 'pass', observed: { source: 40, bronze: 40 } },
    { name: 'staging_reconcile', source_id: 'orders', result: 'pass', observed: { bronze: 40, staging: 38, rejected: 2, reject_pct: 5 } },
    { name: 'dbt_test', source_id: null, result: 'pass', observed: { passed: 4, failed: 0 } },
  ],
  timing: { baseline_sec: null, optimized_sec: null }, error_trace: [], approval: null,
});
const cleaningRun: RunReport = {
  ...agentRun('c1eaa000-0000-4000-8000-000000000000', 'NEEDS_HUMAN', '2026-10-01T07:00:00Z'),
  spec: { title: 'Làm sạch', table: 'sales_raw.orders', columns: [{ from: 'order_id', as: 'order_id', type: 'integer' }] }, plan: null, gates: [], checks: [],
};
const POLICY = { files: { 'domains.yml': 'domains: {}' }, limits: { max_llm_calls: 6, max_spec_attempts: 2, max_sql_attempts: 2, max_usd_per_day: 2 } };
const USERS = [{ id: 'minh', name: 'Minh', role: 'Engineer' as const }, { id: 'an', name: 'An', role: 'Reviewer' as const }];

test('live: nguồn từ /catalog, bronze → staging → gold từ Spec, số đo từ RunReport', () => {
  const live = new Catalog(buildSnapshot(CATALOG, [
    agentRun('aaaa0000-0000-4000-8000-000000000001', 'DEPLOYED', '2026-10-01T08:00:00Z'),
    agentRun('bbbb0000-0000-4000-8000-000000000002', 'NEEDS_HUMAN', '2026-10-01T09:00:00Z'),
    cleaningRun,
  ], POLICY, USERS));
  const source = live.dataset('sales.source.sales_raw.orders')!;
  assert.deepEqual(source.columns.map(c => c.name), ['order_id', 'customer_id', 'amount_text', 'ordered_at_text']);
  assert.equal(source.columns[0].primaryKey, true);
  assert.equal(source.columns[1].references, 'customers.customer_id');
  assert.deepEqual(source.terms, ['sales.order']);
  assert.equal(source.rowCount, 40);
  assert.equal(live.runsForDataset(source).length, 3, 'run agent (kể cả dừng sớm) và run làm sạch đều gắn vào bảng nguồn');

  const gold = live.dataset('sales.gold.fct_daily_sales')!;
  assert.deepEqual(live.lineage(gold.id).nodes.map(n => n.layer), ['source', 'bronze', 'staging', 'gold']);
  assert.equal(gold.rowCount, 30);
  assert.deepEqual(gold.columns.map(c => [c.name, c.type]), [['ordered_at', 'date'], ['total_revenue', 'numeric']]);
  assert.ok(gold.tags.includes('đã deploy'));
  assert.deepEqual(gold.runs, ['aaaa0000-0000-4000-8000-000000000001'], 'run dừng sớm không tạo ra bảng gold');
  assert.deepEqual(gold.quality.map(q => q.id).sort(), ['dbt_test', 'gate:post_run']);

  const staging = live.dataset('sales.staging.stg_fct_daily_sales__orders')!;
  assert.equal(staging.rowCount, 38);
  assert.equal(staging.quality[0].state, 'pass');
  assert.equal(live.dataset('sales.bronze.raw_orders')?.rowCount, 40);

  const payments = live.dataset('sales.source.sales_api.payments')!;
  assert.match(payments.unavailable!, /chưa hỗ trợ/);
  assert.equal(live.datasetByName('sales_api.payments')?.id, payments.id);
  assert.equal(live.personName('an@company.vn'), 'An (steward)');

  assert.deepEqual(live.sourceTablesOf(gold.id), ['sales_pg.orders'], 'yêu cầu từ bảng gold chọn sẵn bảng nguồn của nó');
  assert.deepEqual(live.sourceTablesOf(source.id), ['sales_pg.orders']);
});

test('live: khóa ngoại từ nguồn và phép join của pipeline thành quan hệ giữa bảng', () => {
  const joinRun = agentRun('cccc0000-0000-4000-8000-000000000003', 'READY_FOR_REVIEW', '2026-10-01T10:00:00Z');
  const spec = joinRun.spec as import('../lib/postgres-api.ts').AgentSpec;
  joinRun.spec = { ...spec,
    sources: [...spec.sources, { ...spec.sources[0], id: 'customers', table: 'customers', target: { table: 'raw_customers' } }],
    transform: { ...spec.transform, model_name: 'fct_customer_orders', joins: [{ source: 'orders', type: 'inner', on: [['customers.customer_id', 'orders.customer_id']] }] } };
  const live = new Catalog(buildSnapshot(CATALOG, [joinRun], POLICY, USERS));
  const [fk, join] = [live.relationships().find(r => r.kind === 'foreign_key')!, live.relationships().find(r => r.kind === 'join')!];
  assert.deepEqual([fk.from, fk.fromColumns, fk.to, fk.toColumns], ['sales.source.sales_raw.orders', ['customer_id'], 'sales.source.sales_raw.customers', ['customer_id']]);
  assert.deepEqual([join.from, join.to, join.label], ['sales.source.sales_raw.customers', 'sales.source.sales_raw.orders', 'inner join']);
  assert.deepEqual(join.usedBy, ['sales.gold.fct_customer_orders']);
  assert.equal(live.relationsOf('sales.gold.fct_customer_orders').length, 1, 'bảng gold thấy phép join dựng nên nó');
  assert.equal(live.relationsOf('sales.source.sales_raw.orders').length, 2);
  assert.deepEqual(live.involvedTables(live.relationships('sales')).map(d => d.name).sort(), ['sales_raw.customers', 'sales_raw.orders']);
});

test('phiên kết nối: cookie chỉ nhận tên nguồn hợp lệ, không trùng', () => {
  assert.deepEqual(parseConnections('sales_pg,mkt_api,sales_pg'), ['sales_pg', 'mkt_api']);
  assert.deepEqual(parseConnections('sales_pg,<script>,,Bad'), ['sales_pg']);
  assert.deepEqual(parseConnections(undefined), []);
});

test('diễn giải run: các bước, chỉ số bằng lời, cảnh báo khi kết quả không gộp', async () => {
  const { runResult, describeExpr, resultWarnings, typeLabel } = await import('../lib/catalog/explain.ts');
  const result = runResult(agentRun('dddd0000-0000-4000-8000-000000000004', 'READY_FOR_REVIEW', '2026-10-01T11:00:00Z'))!;
  assert.equal(result.goldTable, 'gold.fct_daily_sales');
  assert.deepEqual(result.grain, ['ordered_at'], 'bỏ tiền tố alias "orders."');
  assert.deepEqual(result.sources[0].rows, { source: 40, bronze: 40, staging: 38, rejected: 2, duplicates: null });
  assert.equal(result.sample.length, 1);
  assert.deepEqual(resultWarnings(result), [], 'gộp theo ngày (date): 30 dòng từ 38 dòng nhỏ, không cảnh báo');
  assert.equal(runResult(cleaningRun), null, 'run làm sạch thủ công không có Spec agent');

  assert.equal(describeExpr('SUM(payment.amount_usd)'), 'tổng amount_usd');
  assert.equal(describeExpr('COUNT(DISTINCT orders.order_id)'), 'số order_id khác nhau');
  assert.equal(describeExpr('COUNT(*)'), 'số dòng');
  assert.equal(describeExpr('SUM(a) / COUNT(b)'), null, 'không nhận ra thì để giao diện hiện nguyên biểu thức');
  assert.equal(typeLabel('decimal(18,2)'), 'số thập phân');

  // Ca thật trên Pagila: "Tính tổng doanh thu" nhóm theo payment_date (timestamp) → 16.974 / 32.093 dòng.
  const byTimestamp = { ...result, grain: ['payment_date'], goldRows: 16974,
    sources: [{ ...result.sources[0], columns: [{ from: 'payment_date', as: 'payment_date', type: 'timestamp' }], rows: { ...result.sources[0].rows, staging: 32093 } }] };
  assert.match(resultWarnings(byTimestamp)[0], /chính xác đến từng giây/);
  const notAggregated = { ...byTimestamp, grain: ['payment_id'], sources: [{ ...byTimestamp.sources[0], columns: [{ from: 'payment_id', as: 'payment_id', type: 'integer' }] }] };
  assert.match(resultWarnings(notAggregated)[0], /gần như không được gộp/);
});
