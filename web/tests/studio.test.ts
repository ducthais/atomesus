import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { canReview, pendingRun, stageIndex, toArtifacts, toStudioRun, STAGES } from '../lib/studio.ts';
import type { RunReport } from '../lib/postgres-api.ts';

const FIXTURES = new URL('../../fixtures/run_reports/', import.meta.url);
const fixture = (name: string) => JSON.parse(readFileSync(new URL(name, FIXTURES), 'utf-8')) as RunReport;

const agentReport: RunReport = {
  run_id: '3f1c2b7e-0000-4000-8000-000000000001', version: 1, domain: 'sales', created_by: 'minh', trigger: 'user_request',
  created_at: '2026-10-01T08:00:00Z', user_request: 'Tính doanh thu theo ngày', status: 'READY_FOR_REVIEW', stage_reached: 't_validate',
  plan: { summary: 'Doanh thu theo ngày', model_name: 'fct_daily_orders', schedule: '0 2 * * *', sources: [{ id: 'orders', source_ref: 'sales_pg', object: 'orders' }] },
  spec: {
    domain: 'sales',
    sources: [{ id: 'orders', kind: 'postgres', source_ref: 'sales_pg', table: 'orders', primary_key: ['order_id'], cursor_field: null, write_disposition: 'replace', target: { table: 'raw_orders' } }],
    transform: { model_name: 'fct_daily_orders', grain: ['order_date'], metrics: [{ name: 'revenue', expr: 'SUM(amount_vnd)' }], filters: ["status <> 'cancelled'"] },
    schedule: { cron: '0 2 * * *' },
  },
  spec_history: [{ attempt: 1, errors: ["cột 'amount' không có ở nguồn"] }, { attempt: 2, errors: [] }],
  files: { 'ingestion/fct_daily_orders_pipeline.py': 'sha256:a', 'dbt_project/models/gold/fct_daily_orders.sql': 'sha256:b' },
  files_hash: 'sha256:0123456789abcdef',
  gates: [{ name: 'spec_schema', result: 'pass', detail: 'Spec hợp lệ', observed: {} }, { name: 'post_run', result: 'pass', detail: '3/3 phép kiểm tra đạt', observed: { gold_rows: 30 } }],
  checks: [
    { name: 'row_reconcile', source_id: 'orders', result: 'pass', observed: { source: 30, bronze: 30 } },
    { name: 'staging_reconcile', source_id: 'orders', result: 'pass', observed: { bronze: 30, staging: 29, rejected: 1, reject_pct: 3.33, max_reject_pct: 5, duplicates: 0 } },
    { name: 'dbt_test', source_id: null, result: 'pass', observed: { passed: 5, failed: 0 } },
  ],
  cost: { usd: 0 }, attempts: { spec: 1, sql: 0 }, llm_calls: 3,
  timing: { baseline_sec: 11, optimized_sec: null }, error_trace: [], approval: null,
} as RunReport & { trigger: string };

test('agent RunReport maps to Studio run without invented numbers', () => {
  const run = toStudioRun(agentReport);
  assert.equal(run.status, 'READY_FOR_REVIEW');
  assert.equal(run.stage, STAGES.length - 1);
  assert.equal(run.llmCalls, 3);
  assert.equal(run.specRetries, 1);
  assert.equal(run.fileCount, 2);
  assert.deepEqual(run.checks.map(c => [c.id, c.state, c.value]), [
    ['row_reconcile:orders', 'pass', '30 → 30'],
    ['staging_reconcile:orders', 'pass', '29 sạch · 1 rác'],
    ['dbt_test', 'pass', '5 / 5 đạt'],
    ['gate:spec_schema', 'pass', 'Đạt'],
    ['gate:post_run', 'pass', 'Đạt'],
  ]);
  assert.deepEqual(run.assets.map(a => [a.name, a.rows]), [
    ['bronze.raw_orders', '30'], ['staging.stg_fct_daily_orders__orders', '29'], ['gold.fct_daily_orders', '30'],
  ]);
  assert.equal(run.plan.model, 'fct_daily_orders');
  assert.equal(run.plan.filters, "status <> 'cancelled'");
  assert.deepEqual(run.logs.map(l => l.level), ['warning', 'success', 'success']);
});

test('stopped run exposes the recorded reason and stage', () => {
  const run = toStudioRun({
    ...agentReport, status: 'NEEDS_HUMAN', stage_reached: 'plan', spec: null, files: {}, files_hash: null, checks: [], gates: [],
    error_trace: [{ stage: 'plan', error: 'Cần làm rõ yêu cầu', question: 'Doanh thu trước hay sau hoàn tiền?' }],
  });
  assert.equal(run.stage, stageIndex('plan'));
  assert.equal(run.question, 'Doanh thu trước hay sau hoàn tiền?');
  assert.equal(run.error, 'Cần làm rõ: Doanh thu trước hay sau hoàn tiền?');
  assert.equal(run.fileCount, 0);
  assert.deepEqual(run.assets, []);
  assert.equal(run.plan.model, 'fct_daily_orders');
});

test('every backend RunReport fixture maps cleanly', () => {
  for (const name of readdirSync(FIXTURES).filter(file => file.endsWith('.json'))) {
    const report = fixture(name);
    const run = toStudioRun(report);
    assert.equal(run.id, report.run_id);
    assert.equal(run.status, report.status);
    assert.equal(run.checks.length, report.checks.length + report.gates.length);
    assert.equal(run.approval?.reviewer, report.approval?.reviewer);
  }
  assert.equal(toStudioRun(fixture('blocked_semantic.json')).error, 'Thực thể customer chưa có định nghĩa chuẩn giữa sales và marketing');
});

test('review needs a different reviewer and every check passing', () => {
  const run = toStudioRun(agentReport);
  assert.equal(canReview(run, 'Reviewer', 'an'), true);
  assert.equal(canReview(run, 'Reviewer', 'minh'), false);
  assert.equal(canReview(run, 'Engineer', 'an'), false);
  assert.equal(canReview({ ...run, checks: run.checks.map(c => c.id === 'dbt_test' ? { ...c, state: 'fail' } : c) }, 'Reviewer', 'an'), false);
});

test('pending run is marked and holds no evidence', () => {
  const run = pendingRun('Doanh thu', 'minh');
  assert.equal(run.pending, true);
  assert.equal(run.status, 'GENERATING');
  assert.deepEqual(run.checks, []);
  assert.equal(run.id.startsWith('pending-'), true);
});

test('generated files become sorted read-only artifacts with a language', () => {
  const artifacts = toArtifacts({ 'ingestion/x.py': 'import dlt', 'dbt_project/models/gold/x.yml': 'version: 2', 'dbt_project/models/gold/x.sql': 'select 1' });
  assert.deepEqual(artifacts.map(a => [a.path, a.language]), [
    ['dbt_project/models/gold/x.sql', 'sql'], ['dbt_project/models/gold/x.yml', 'yaml'], ['ingestion/x.py', 'python'],
  ]);
});
