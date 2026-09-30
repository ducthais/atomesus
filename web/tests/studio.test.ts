import test from 'node:test';
import assert from 'node:assert/strict';
import { canExport, canReview, defaultPlan, demoRuns, makeRun, newVersion } from '../lib/studio.ts';
import { applyRunEvent, mockPipelineApi } from '../lib/studio-api.ts';

test('review needs a different reviewer and all six passing checks', () => {
  const ready = demoRuns()[0];
  assert.equal(canReview(ready, 'Reviewer', 'an'), true);
  assert.equal(canReview(ready, 'Reviewer', 'minh'), false);
  assert.equal(canReview({ ...ready, checks: ready.checks.map(c => c.id === 'metric_reconcile' ? { ...c, state: 'fail' } : c) }, 'Reviewer', 'an'), false);
});

test('new version invalidates approval, evidence and artifact export', () => {
  const approved = { ...demoRuns()[0], status: 'APPROVED' as const, approvedHash: 'a7f3c902' };
  assert.equal(canExport(approved), true);
  const revised = newVersion(approved);
  assert.equal(revised.version, approved.version + 1);
  assert.equal(revised.approvedHash, undefined);
  assert.equal(revised.checks.every(c => c.state === 'unmeasured'), true);
  assert.equal(canExport(revised), false);
});

test('SQL repair creates a new version and keeps the error trace', () => {
  const run = makeRun('total_amount', 'Sales', 'sql', defaultPlan);
  const error = { id: 'error', stage: 7, level: 'error' as const, message: 'Cột không tồn tại', at: new Date().toISOString() };
  const withError = applyRunEvent(run, { type: 'log', log: error });
  const repaired = applyRunEvent(withError, { type: 'repair', log: { ...error, id: 'repair', level: 'success', message: 'Đã sửa' } });
  assert.equal(repaired.version, run.version + 1);
  assert.equal(repaired.scenario, 'success');
  assert.equal(repaired.sqlRetries, 1);
  assert.equal(repaired.logs.length, 2);
});

test('cancelled subscription emits no further events', async () => {
  const run = makeRun('Đơn hàng', 'Sales', 'success', defaultPlan);
  let events = 0;
  const subscription = mockPipelineApi.subscribeToRun(run, () => { events++; });
  subscription.cancel();
  await new Promise(resolve => setTimeout(resolve, 400));
  assert.equal(events, 0);
});

test('demo scenarios emit the intended terminal gates', async () => {
  const scenarios = [
    ['success', 'READY_FOR_REVIEW'],
    ['semantic', 'BLOCKED_SEMANTIC_CONFLICT'],
    ['budget', 'BLOCKED_BUDGET'],
    ['sql', 'repair'],
    ['ast', 'BLOCKED_AST'],
    ['metric', 'NEEDS_HUMAN'],
  ] as const;
  const terminal = await Promise.all(scenarios.map(([scenario]) => new Promise<string>((resolve, reject) => {
    const run = makeRun(scenario, 'Sales', scenario, defaultPlan);
    const timeout = setTimeout(() => { subscription.cancel(); reject(new Error(`${scenario} did not finish`)); }, 5500);
    const subscription = mockPipelineApi.subscribeToRun(run, event => {
      if (event.type === 'repair' || event.type === 'status') {
        clearTimeout(timeout);
        subscription.cancel();
        resolve(event.type === 'repair' ? 'repair' : event.status!);
      }
    });
  })));
  assert.deepEqual(terminal, scenarios.map(([, expected]) => expected));
});
