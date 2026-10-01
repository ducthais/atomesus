import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError, apiRequest, defaultColumns, pipelineRequest, runError, stagingOf, validateColumns, type RunReport } from '../lib/postgres-api.ts';

test('schema mapping uses actual names and selects decimal/date defaults', () => {
  const columns = defaultColumns({ table: 'sales_raw.orders', max_rows: 1000, columns: [
    { name: 'order_id', type: 'integer' }, { name: 'amount_text', type: 'text' }, { name: 'ordered_at_text', type: 'text' },
  ] });
  assert.deepEqual(columns, [
    { from: 'order_id', as: 'order_id', type: 'integer' },
    { from: 'amount_text', as: 'amount', type: 'decimal(18,2)' },
    { from: 'ordered_at_text', as: 'ordered_at', type: 'date' },
  ]);
  assert.equal(validateColumns(columns), null);
  assert.ok(validateColumns([columns[0], columns[0]]));
  assert.ok(validateColumns([{ ...columns[0], as: 'id;drop' }]));
  assert.ok(validateColumns([]));
});

test('API sends cookies through the same-origin backend proxy and returns actual report', async t => {
  const fixture = { run_id: 'actual-run', rows_written: 30 };
  t.mock.method(globalThis, 'fetch', async (url: string, options: RequestInit) => {
    assert.equal(url, '/api/v1/pipeline/runs');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.method, 'POST');
    assert.equal(options.cache, 'no-store');
    return Response.json(fixture, { status: 201 });
  });
  assert.deepEqual(await pipelineRequest('/runs', { method: 'POST', body: '{}' }), fixture);
});

test('API surfaces authorization and validation errors, without demo fallback', async t => {
  const mock = t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Login required' }, { status: 401 }));
  await assert.rejects(pipelineRequest('/runs'), (error: unknown) => error instanceof ApiError && error.status === 401);
  mock.mock.mockImplementation(async () => Response.json({ detail: [{ msg: 'Duplicate column' }] }, { status: 422 }));
  await assert.rejects(pipelineRequest('/runs'), /Duplicate column/);
  mock.mock.mockImplementation(async () => new Response('Proxy failed', { status: 502 }));
  await assert.rejects(pipelineRequest('/runs'), /Backend chưa sẵn sàng/);
  mock.mock.mockImplementation(async () => { throw new Error('Network down'); });
  await assert.rejects(pipelineRequest('/runs'), /Không kết nối được backend/);
});

test('run history comes from the shared evidence ledger and reads RunReport staging check', async t => {
  t.mock.method(globalThis, 'fetch', async (url: string) => {
    assert.equal(url, '/api/v1/runs');
    return Response.json([]);
  });
  assert.deepEqual(await apiRequest('/runs'), []);
  const observed = { bronze: 2, staging: 1, rejected: 1, reject_pct: 50, reject_reasons: { cast: 1 },
    reject_samples: [], output_schema: [], output_samples: [] };
  const report = { checks: [{ name: 'staging_reconcile', source_id: 'sales_raw.orders', result: 'fail', observed }],
    error_trace: [{ stage: 'el_run', error: 'Database offline' }] } as unknown as RunReport;
  assert.deepEqual(stagingOf(report), observed);
  assert.equal(runError(report), 'Database offline');
  assert.equal(stagingOf({ ...report, checks: [] }), null);
  assert.equal(runError({ ...report, error_trace: [] }), null);
});
