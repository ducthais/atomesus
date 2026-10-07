import assert from 'node:assert/strict';
import test from 'node:test';
import { findingBadges, newRecordsText, versusBaseline, type Finding, type MonitorRun } from '../lib/monitor.ts';

const run = (over: Partial<MonitorRun> = {}): MonitorRun => ({
  run_id: 'r1', status: 'SUCCESS', started_at: '2026-10-07T16:12:37Z', ended_at: '2026-10-07T16:13:21Z', duration_sec: 44.3,
  gold_rows: 30, usd: 1e-7, tests: 4, tests_failed: 0, source_records: { orders: 30 }, new_records: { orders: 0 }, ...over,
});

test('theo dõi: nhãn luật, nguồn ngừng cấp tách riêng', () => {
  const slow: Finding = { rule: 'runtime_regression', observed: 66, threshold: 15.6, baseline: 10.4, detail: 'chậm hơn 62%', stalled_sources: [] };
  const stalled: Finding = { rule: 'freshness_breach', observed: 300, threshold: 60, baseline: null, detail: 'payments không có bản ghi mới', stalled_sources: ['payments'] };
  assert.deepEqual(findingBadges([slow, stalled]).map(b => [b.label, b.tone]), [['Đang chậm', 'bad'], ['Nguồn ngừng cấp: payments', 'amber']]);
});

test('theo dõi: so với đường cơ sở và bản ghi mới', () => {
  assert.equal(versusBaseline(44.31, 10.402), '+326%');
  assert.equal(versusBaseline(8, 10), '-20%');
  assert.equal(versusBaseline(null, 10), null);
  assert.equal(newRecordsText(run()), 'orders +0');
  assert.equal(newRecordsText(run({ new_records: null })), 'lần đầu, chưa có mốc so');
  assert.match(newRecordsText(run({ source_records: { error: 'RuntimeError: API nguồn không trả lời' } })), /Không đọc được nguồn/);
  assert.equal(newRecordsText(null), '—');
});
