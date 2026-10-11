import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, stripTypeScriptTypes } from 'node:module';
import { createElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as deploy from '../lib/deploy.ts';
import { APPROVAL_MESSAGE, deploymentLink, deploymentReworks, deploymentSteps, deployReason, requestDeployment } from '../lib/deploy.ts';
import { ApiError, apiRequest, type Deployment, type RunReport } from '../lib/postgres-api.ts';
import { STATUS_LABEL, toStudioRun, type Run } from '../lib/studio.ts';
import { MOCK_SNAPSHOT } from '../lib/catalog/mock.ts';

const approval = { decision: 'approved', reviewer: 'an', files_hash: 'sha256:a', version: 1, at: '2026-10-10T08:00:00Z', comment: '' };
const deployment: Deployment = { repo: 'atomesus/pipelines', branch: 'pipeline/revenue/v1', pr_number: 12,
  pr_url: 'https://github.com/atomesus/pipelines/pull/12', head_sha: 'a'.repeat(40), state: 'open', opened_at: '2026-10-10T08:01:00Z' };
const report: RunReport = { run_id: 'run-12', version: 1, domain: 'sales', created_by: 'minh', created_at: approval.at,
  user_request: 'Doanh thu', status: 'APPROVED', stage_reached: 'review', spec: null, checks: [], gates: [],
  timing: { baseline_sec: null, optimized_sec: null }, error_trace: [], approval, files_hash: approval.files_hash };
const steps = (changes: Partial<RunReport> = {}) => deploymentSteps(toStudioRun({ ...report, ...changes }));

test('triển khai: run cũ không có deployment không suy diễn PR, CI hay merge', () => {
  for (const absent of [undefined, null]) {
    assert.deepEqual(steps({ deployment: absent }).map(s => s.state), ['pass', 'pending', 'pending', 'pending', 'pending']);
    assert.deepEqual(steps({ status: 'READY_FOR_REVIEW', approval: null, deployment: absent }).map(s => s.state), Array(5).fill('pending'));
    const legacy = steps({ status: 'DEPLOYED', deployment: absent });
    assert.equal(legacy[4].label, 'Đã lên production');
    assert.equal(legacy[4].at, undefined);
    assert.match(legacy[4].detail, /Run cũ/);
  }
});

test('triển khai: PR mở, CI chạy / xanh / đỏ và PR đóng', () => {
  const open = steps({ deployment });
  assert.deepEqual(open.map(s => s.state), ['pass', 'pass', 'running', 'pending', 'pending']);
  assert.equal(open[1].href, deployment.pr_url);
  assert.equal(open[1].at, deployment.opened_at);
  assert.equal(open[2].label, 'CI đang chạy');
  for (const conclusion of ['success', 'failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure', 'neutral', 'skipped']) {
    const ci = steps({ deployment: { ...deployment, ci_conclusion: conclusion, ci_url: 'https://github.com/ci/12', ci_at: approval.at } })[2];
    assert.equal(ci.state, conclusion === 'success' ? 'pass' : ['neutral', 'skipped'].includes(conclusion) ? 'neutral' : 'fail');
    if (conclusion === 'neutral') assert.equal(ci.label, 'CI trung lập');
    if (conclusion === 'skipped') assert.equal(ci.label, 'CI đã bỏ qua');
    assert.equal(ci.href, 'https://github.com/ci/12');
    assert.equal(ci.at, approval.at);
  }
  const closed = steps({ deployment: { ...deployment, state: 'closed', closed_at: approval.at } });
  assert.equal(closed[1].label, 'PR đã đóng');
  assert.equal(closed[1].at, approval.at);
  assert.equal(closed[3].state, 'pending');
  assert.match(closed[3].detail, /không merge/);
});

test('triển khai: lỗi mở PR hiện khi APPROVED chưa có PR, lỗi cũ ẩn khi đã có PR hoặc lên production', () => {
  const error_trace = [{ stage: 'deploy', error: 'Lỗi deploy của lần trước.' }];
  for (const status of ['APPROVED', 'DEPLOYED']) {
    for (const d of [undefined, deployment, { ...deployment, deployed_at: approval.at }]) {
      const run = toStudioRun({ ...report, status, deployment: d, error_trace });
      assert.equal(run.deployError, error_trace[0].error);
      const production = deploymentSteps(run)[4];
      assert.equal(production.state, status === 'DEPLOYED' || d?.deployed_at ? 'pass' : d ? 'pending' : 'fail');
      if (status === 'APPROVED' && !d) assert.equal(production.detail, error_trace[0].error);
      else assert.doesNotMatch(production.detail, /Lỗi deploy của lần trước/);
    }
  }
  const current = steps({ status: 'DEPLOY_FAILED', deployment, error_trace: [
    ...error_trace, { stage: 'deploy', message: 'Lỗi deploy hiện tại.' }, { stage: 'persist', error: 'Lỗi khác.' },
  ] })[4];
  assert.equal(current.state, 'fail');
  assert.equal(current.detail, 'Lỗi deploy hiện tại.');
});

// Biên dịch TSX bằng SWC của Next để kiểm HTML của component thật, không cần chạy server.
const require = createRequire(import.meta.url);
const { transformSync } = require('next/dist/build/swc');
function component(path: string, modules: Record<string, unknown>) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { code } = transformSync(source, {
    filename: path, module: { type: 'commonjs' },
    jsc: { parser: { syntax: 'typescript', tsx: true }, transform: { react: { runtime: 'automatic' } } },
  });
  const exports: Record<string, unknown> = {};
  new Function('require', 'exports', code)((name: string) => name in modules ? modules[name] : require(name), exports);
  return exports;
}
const controls = component('../app/_components/run-controls.tsx', { '../actions': {} });
const { DeployPanel } = component('../app/_components/deploy-panel.tsx', {
  '@/lib/deploy': deploy, './run-controls': controls,
  './ui': { Panel: ({ children }: { children: ReactNode }) => createElement('section', null, children), fmtDate: (at: string) => at },
}) as { DeployPanel: (props: { run: Run; role: string }) => ReactNode };
const panel = (changes: Partial<RunReport> = {}, role = 'Reviewer') => renderToStaticMarkup(createElement(DeployPanel, { run: toStudioRun({ ...report, ...changes }), role }));

test('khối Triển khai hiện lỗi mở PR hiện tại khi APPROVED và ẩn sau DEPLOYED', () => {
  const error_trace = [{ stage: 'deploy', error: 'Không mở được pull request; thử Deploy lại.' }];
  const failed = panel({ error_trace });
  assert.match(failed, /Triển khai lỗi/);
  assert.match(failed, /Không mở được pull request; thử Deploy lại/);
  assert.match(failed, /lucide-circle-alert/);
  assert.match(failed, /<button[^>]*>.*Deploy<\/button>/);
  const deployed = panel({ status: 'DEPLOYED', error_trace });
  assert.match(deployed, /Đã lên production/);
  assert.doesNotMatch(deployed, /Triển khai lỗi|Không mở được pull request/);
});

test('khối Triển khai chỉ hiện cho run đã duyệt / deploy hoặc có deployment hiện tại', () => {
  for (const status of Object.keys(STATUS_LABEL)) {
    const visible = ['APPROVED', 'DEPLOYED', 'DEPLOY_FAILED'].includes(status);
    const html = panel({ status, approval: null });
    assert.equal(html.includes('deployment-panel'), visible, status);
    if (!visible) assert.equal(html, '');
    assert.match(panel({ status, approval: null, deployment }), /deployment-panel/);
  }
  assert.equal(panel({ status: 'GENERATING', approval: null, spec_history: [{ version: 1, deployment }] }), '');
});

test('PR đã có: ẩn nút Deploy, giữ liên kết an toàn và giải thích cho mọi vai trò', () => {
  assert.match(panel(), /<button[^>]*>.*Deploy<\/button>/);
  assert.match(panel({}, 'Engineer'), /<button[^>]*disabled=""/);
  for (const role of ['Reviewer', 'Engineer', 'Admin']) {
    for (const state of ['open', 'merged', 'closed'] as const) {
      const html = panel({ deployment: { ...deployment, state } }, role);
      assert.doesNotMatch(html, /<button/);
      assert.match(html, /đã có pull request; xem liên kết và tiến độ/);
      assert.ok(html.includes(`href="${deployment.pr_url}"`));
      assert.match(html, /rel="noopener noreferrer"/);
    }
  }
  const unsafe = panel({ deployment: { ...deployment, pr_url: 'javascript:alert(1)' } });
  assert.doesNotMatch(unsafe, /<button|javascript:/);
});

test('CI neutral / skipped hiện biểu tượng tĩnh, CI chưa kết thúc vẫn quay', () => {
  for (const conclusion of ['neutral', 'skipped']) {
    const html = panel({ deployment: { ...deployment, ci_conclusion: conclusion } });
    assert.match(html, /lucide-minus/);
    assert.doesNotMatch(html, /spin|lucide-loader-circle/);
    assert.ok(html.includes(`Kết luận: ${conclusion}`));
  }
  assert.match(panel({ deployment }), /lucide-loader-circle[^\"]*spin/);
  assert.doesNotMatch(panel({ deployment: { ...deployment, state: 'closed' } }), /spin/);
});

test('triển khai: merge chưa phải production, callback ghi thời điểm và lỗi stage deploy', () => {
  const merged = { ...deployment, state: 'merged' as const, ci_conclusion: 'success', merged_at: approval.at, merge_commit_sha: 'b'.repeat(40) };
  assert.deepEqual(steps({ deployment: merged }).map(s => s.state), ['pass', 'pass', 'pass', 'pass', 'running']);
  const deployed = steps({ status: 'DEPLOYED', deployment: { ...merged, deployed_commit_sha: merged.merge_commit_sha, deployed_at: approval.at } });
  assert.equal(deployed[4].state, 'pass');
  assert.equal(deployed[4].at, approval.at);
  const failed = steps({ status: 'DEPLOY_FAILED', deployment: merged,
    error_trace: [{ stage: 'deploy', error: 'Không nạp được pipeline.' }, { stage: 'persist', error: 'Lỗi khác.' }] });
  assert.equal(failed[4].label, 'Triển khai lỗi');
  assert.equal(failed[4].detail, 'Không nạp được pipeline.');
  assert.equal(steps({ status: 'DEPLOY_FAILED' })[4].state, 'fail');
  assert.equal(toStudioRun({ ...report, status: 'DEPLOY_FAILED', stage_reached: 'deploy' }).stage, 14);
});

test('triển khai: giữ PR cũ khi backend tạo version mới, không dùng chữ ký cũ cho version hiện tại', () => {
  for (const d of [{ ...deployment, state: 'closed' as const }, { ...deployment, ci_conclusion: 'failure' }]) {
    const run = toStudioRun({ ...report, version: 2, status: 'GENERATING', approval: null,
      spec_history: [{ version: 1, reason: 'Sửa theo PR', deployment: d }] });
    const history = deploymentReworks(run);
    assert.equal(history[0].version, 1);
    assert.equal(history[0].deployment.pr_url, deployment.pr_url);
    assert.match(history[0].message, /đã tạo version mới/);
    assert.equal(deploymentSteps(run)[0].state, 'pending');
  }
  assert.deepEqual(deploymentReworks(toStudioRun(report)), []);
});

test('Deploy chỉ dành cho Reviewer khi APPROVED, không mở lại PR đã có', () => {
  const run = toStudioRun(report);
  assert.equal(deployReason(run, 'Reviewer'), null);
  for (const role of ['Engineer', 'Admin']) assert.match(deployReason(run, role)!, /Chỉ Reviewer/);
  for (const status of Object.keys(STATUS_LABEL).filter(s => s !== 'APPROVED')) {
    assert.match(deployReason(toStudioRun({ ...report, status }), 'Reviewer')!, /APPROVED/);
  }
  for (const state of ['open', 'merged', 'closed'] as const) {
    assert.match(deployReason({ ...run, deployment: { ...deployment, state } }, 'Reviewer')!, /đã có pull request/);
  }
});

test('link triển khai chỉ chấp nhận HTTP(S)', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,abc', '/relative', 'not a url', null, undefined]) assert.equal(deploymentLink(url), undefined);
  assert.equal(deploymentLink(deployment.pr_url), deployment.pr_url);
});

// Chạy Server Actions với backend / phiên / refresh giả để kiểm hợp đồng mà không cần Next server.
const actionSource = stripTypeScriptTypes(readFileSync(new URL('../app/actions.ts', import.meta.url), 'utf8'));
const actionCode = actionSource.replace(/import \{([^}]+)\} from '([^']+)';/g, 'const {$1} = require(\'$2\');')
  .replace(/export async function /g, 'async function ')
  + [...actionSource.matchAll(/export async function (\w+)/g)].map(match => `\nexports.${match[1]} = ${match[1]};`).join('');
function actions(request: typeof apiRequest, refresh: () => void, mock = false) {
  const exports: Record<string, (...args: unknown[]) => Promise<unknown>> = {};
  const modules: Record<string, unknown> = {
    'next/headers': {}, 'next/navigation': {}, 'next/cache': { refresh }, '@/lib/studio': {},
    '@/lib/deploy': { APPROVAL_MESSAGE, requestDeployment }, '@/lib/catalog/source': { backend: request, BackendError: ApiError, MOCK: mock },
    '@/lib/catalog/connections': {}, '@/lib/catalog/explain': {},
  };
  new Function('require', 'exports', actionCode)((name: string) => {
    assert.ok(name in modules, `Module chưa giả lập: ${name}`); return modules[name];
  }, exports);
  return exports;
}

test('Server Action Deploy gửi POST riêng, làm mới trang và giữ nguyên lỗi tiếng Việt 403/409/502/503', async () => {
  const previous = globalThis.fetch;
  let refreshed = 0;
  let requests = 0;
  const actor = actions(apiRequest, () => refreshed++);
  try {
    globalThis.fetch = async (input, init) => {
      requests++;
      assert.equal(input, '/api/v1/runs/run-12/deploy');
      assert.equal(init?.method, 'POST');
      assert.equal(init?.credentials, 'same-origin');
      return Response.json({ ...report, deployment });
    };
    assert.match(String((await actor.deployRun('run-12') as { message: string }).message), /pull request/);
    assert.equal(refreshed, 1);
    assert.deepEqual(await actor.deployRun('../bad'), { error: 'Mã run không hợp lệ.' });
    assert.equal(requests, 1);
    for (const status of [403, 409, 502, 503]) {
      const detail = `Lỗi ${status}: không mở được pull request.`;
      globalThis.fetch = async () => Response.json({ detail }, { status });
      assert.deepEqual(await actor.deployRun('run-12'), { error: detail });
    }
    assert.equal(refreshed, 2);
    assert.match(String((await actions(apiRequest, () => {}, true).deployRun('run-12') as { message: string }).message), /Mock/);
  } finally { globalThis.fetch = previous; }
});

test('Server Actions duyệt đơn / hàng loạt hướng dẫn Deploy và giữ thông báo lỗi từng run', async () => {
  const previous = globalThis.fetch;
  const actor = actions(apiRequest, () => {});
  try {
    globalThis.fetch = async () => Response.json(report);
    assert.deepEqual(await actor.reviewRun('run-12', 'approve', approval.files_hash, ''), { message: APPROVAL_MESSAGE });
    globalThis.fetch = async () => Response.json({ results: [
      { run_id: 'run-12', ok: true, status: 'APPROVED', message: 'Đã ghi quyết định.' },
      { run_id: 'run-13', ok: false, message: 'Không tự duyệt run của mình.' },
    ] });
    const batch = await actor.reviewRuns([{ runId: 'run-12', filesHash: approval.files_hash }], 'approve', '') as { results: { message: string }[] };
    assert.equal(batch.results[0].message, APPROVAL_MESSAGE);
    assert.equal(batch.results[1].message, 'Không tự duyệt run của mình.');
    assert.equal(STATUS_LABEL.APPROVED, 'Đã duyệt, chờ deploy');
    assert.equal(STATUS_LABEL.DEPLOYED, 'Đã lên production');
  } finally { globalThis.fetch = previous; }
});

test('mock có APPROVED với PR mở và DEPLOYED với bằng chứng production', () => {
  assert.ok(MOCK_SNAPSHOT.runs.some(r => r.status === 'APPROVED' && r.deployment?.state === 'open'));
  assert.ok(MOCK_SNAPSHOT.runs.some(r => r.status === 'DEPLOYED' && r.deployment?.deployed_at));
});
