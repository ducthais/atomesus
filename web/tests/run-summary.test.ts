import test from 'node:test';
import assert from 'node:assert/strict';
import { describeCron, runSummary } from '../lib/catalog/explain.ts';
import { buildSnapshot, type CatalogResponse } from '../lib/catalog/live.ts';
import type { RunReport } from '../lib/postgres-api.ts';

const full: RunReport = {
  run_id: 'run-1', version: 1, domain: 'sales', created_by: 'minh', created_at: '2026-10-01T08:00:00Z', user_request: 'Doanh thu theo ngày',
  status: 'READY_FOR_REVIEW', stage_reached: 'report',
  plan: { model_name: 'plan_model', schedule: '0 9 * * *', sources: [{ id: 'p', source_ref: 'sales_pg', object: 'plan_table' }] },
  spec: {
    domain: 'sales',
    sources: [
      { id: 'orders', kind: 'postgres', source_ref: 'sales_pg', table: 'orders', primary_key: ['order_id'], cursor_field: null, write_disposition: 'replace', target: { table: 'raw_orders' } },
      { id: 'customers', kind: 'postgres', source_ref: 'sales_pg', table: 'customers', primary_key: ['customer_id'], cursor_field: null, write_disposition: 'replace', target: { table: 'raw_customers' } },
    ],
    transform: { model_name: 'fct_daily_sales', grain: ['orders.ordered_at'], metrics: [{ name: 'revenue', expr: 'SUM(orders.amount)' }], filters: [] },
    schedule: { cron: '0 6 * * *' },
  },
  gates: [
    { name: 'budget', result: 'pass', detail: 'ok', observed: { bytes: 1000, usd: 0.0031 } },
    { name: 'post_run', result: 'pass', detail: 'ok', observed: { gold_rows: 1234 } },
    { name: 'budget', result: 'pass', detail: 'bản optimizer giữ lại', observed: { bytes: 800, usd: 0.0025 } },
  ],
  checks: [], timing: { baseline_sec: 2, optimized_sec: 1.5 }, error_trace: [], approval: null,
  optimizer: [
    { rule: 'incremental', applied: true, before_sec: 0.2155, after_sec: 0.1405, reason: 'nhanh hơn 34.8%' },
    { rule: 'partition', applied: true, before_sec: 0.2155, after_sec: 0.18, reason: 'nhanh hơn 16.5%' },
    { rule: 'other', applied: false, before_sec: 0.2155, after_sec: 0.1, reason: 'bảng gold khác bản gốc' },
  ],
};

test('tóm tắt: đủ dữ liệu ra đủ năm câu, đúng số trong RunReport', () => {
  assert.deepEqual(runSummary(full), [
    'Đọc 2 bảng: orders, customers',
    'Ra bảng fct_daily_sales khoảng 1.234 dòng',
    'Chạy theo lịch mỗi ngày lúc 6:00',
    'Chi phí ước tính ~$0.002500 mỗi lần chạy',
    'Optimizer: nhanh hơn 34,8% (0,2155 s → 0,1405 s)',
  ]);
});

test('tóm tắt: thiếu phần nào thì bỏ câu đó, không bịa', () => {
  const noRows = runSummary({ ...full, gates: full.gates.filter(g => g.name !== 'post_run') });
  assert.equal(noRows[1], 'Ra bảng fct_daily_sales', 'không có gold_rows thì không nêu số dòng');

  const noBudget = runSummary({ ...full, gates: full.gates.filter(g => g.name !== 'budget'), cost: { usd: 0.5 } });
  assert.ok(!noBudget.some(line => line.startsWith('Chi phí')), 'chưa qua cổng budget thì không có ước tính mỗi lần chạy');

  assert.ok(!runSummary({ ...full, optimizer: undefined }).some(line => line.startsWith('Optimizer')));
  assert.ok(!runSummary({ ...full, optimizer: full.optimizer!.map(s => ({ ...s, applied: false })) }).some(line => line.startsWith('Optimizer')), 'không bước nào được giữ');
  assert.ok(!runSummary({ ...full, optimizer: [{ rule: 'x', applied: true, before_sec: null, after_sec: null }] }).some(line => line.startsWith('Optimizer')), 'thiếu thời gian đo');

  // Spec chưa hợp lệ (run dừng ở cổng): dùng kế hoạch, cũng chỉ là dữ liệu có trong report.
  assert.deepEqual(runSummary({ ...full, spec: null, gates: [], optimizer: [] }), ['Đọc 1 bảng: plan_table', 'Ra bảng plan_model', 'Chạy theo lịch mỗi ngày lúc 9:00']);
  assert.deepEqual(runSummary({ ...full, spec: null, plan: null, gates: [], optimizer: [] }), []);
  assert.deepEqual(runSummary({ ...full, spec: null, plan: { sources: [] }, gates: [], optimizer: [] }), []);
});

test('tóm tắt: lịch cron phổ biến diễn giải được, dạng lạ giữ nguyên cron', () => {
  const cases: [string, string][] = [
    ['0 6 * * *', 'mỗi ngày lúc 6:00'], ['30 18 * * *', 'mỗi ngày lúc 18:30'], ['0 2 * * *', 'mỗi ngày lúc 2:00'],
    ['0 6,18 * * *', 'mỗi ngày lúc 6:00, 18:00'], ['0 * * * *', 'mỗi giờ'], ['15 * * * *', 'mỗi giờ vào phút 15'],
    ['*/15 * * * *', 'mỗi 15 phút'], ['* * * * *', 'mỗi phút'], ['0 */3 * * *', 'mỗi 3 giờ'], ['0 */1 * * *', 'mỗi giờ'],
    ['0 7 * * 1', 'mỗi thứ Hai lúc 7:00'], ['0 7 * * 0', 'mỗi Chủ nhật lúc 7:00'], ['0 7 * * 1,3', 'mỗi thứ Hai, thứ Tư lúc 7:00'],
    ['0 8 * * 1-5', 'từ thứ Hai đến thứ Sáu lúc 8:00'], ['0 0 1 * *', 'ngày 1 hằng tháng lúc 0:00'],
    ['@daily', 'mỗi ngày lúc 0:00'], ['@hourly', 'mỗi giờ'], ['  0 6 * * *  ', 'mỗi ngày lúc 6:00'],
  ];
  for (const [cron, text] of cases) assert.equal(describeCron(cron), text, cron);
  for (const cron of ['0 6 * 1 *', '0 25 * * *', '61 * * * *', '0 6 1 * 1', '0 6 * * 9', '0 6 * *', 'abc', '0 6 L * *', '0-30 * * * *']) {
    assert.equal(describeCron(cron), null, cron);
  }
  const custom = runSummary({ ...full, spec: { ...(full.spec as object), schedule: { cron: '0 6 * 1 *' } } as RunReport['spec'] });
  assert.equal(custom[2], 'Chạy theo lịch cron 0 6 * 1 *');
  assert.ok(!runSummary({ ...full, spec: { ...(full.spec as object), schedule: { cron: ' ' } } as RunReport['spec'] }).some(line => line.startsWith('Chạy theo lịch')));
});

test('tóm tắt: catalog live gắn câu tóm tắt vào run cho trang chi tiết', () => {
  const catalog: CatalogResponse = { domains: {}, conformed_dimensions: [] };
  const snapshot = buildSnapshot(catalog, [full], { files: {}, limits: { max_llm_calls: 6, max_spec_attempts: 2, max_sql_attempts: 2, max_chat_calls: 20, max_usd_per_day: 2 } }, []);
  assert.deepEqual(snapshot.runs[0].brief, runSummary(full));
});
