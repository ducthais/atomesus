import { createServer } from 'node:http';

const port = Number(process.env.E2E_BACKEND_PORT || 4011);
const users = {
  engineer: { id: 'minh', username: 'engineer', name: 'Minh', role: 'Engineer', managed: false },
  reviewer: { id: 'an', username: 'reviewer', name: 'An', role: 'Reviewer', managed: false },
  admin: { id: 'admin', username: 'admin', name: 'Quản trị viên', role: 'Admin', managed: false },
};
const passwords = { engineer: 'Engineer@123', reviewer: 'Reviewer@123', admin: 'Admin@123' };
const reports = [];

const now = () => new Date().toISOString();
const tokenUser = request => {
  const token = /(?:^|;\s*)pipeline_session=([^;]+)/.exec(request.headers.cookie || '')?.[1];
  return token?.startsWith('token-') ? users[token.slice(6)] : undefined;
};
const json = (response, status, body, headers = {}) => {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', ...headers });
  response.end(JSON.stringify(body));
};
const readJson = request => new Promise((resolve, reject) => {
  let raw = '';
  request.setEncoding('utf8');
  request.on('data', chunk => { raw += chunk; });
  request.on('end', () => {
    try { resolve(raw ? JSON.parse(raw) : {}); }
    catch (error) { reject(error); }
  });
  request.on('error', reject);
});

const catalog = {
  domains: {
    sales: {
      steward: 'data@atomesus.local', currency: 'VND',
      sources: {
        sales_pg: {
          kind: 'postgres', connection_ref: 'e2e', location: 'sales_raw', available: true,
          tables: { orders: [
            { name: 'order_id', type: 'integer' },
            { name: 'amount_text', type: 'text' },
            { name: 'ordered_at_text', type: 'text' },
          ] },
          primary_keys: { orders: ['order_id'] }, foreign_keys: [],
        },
      },
      entities: { order: { canonical: true, definition: 'Đơn hàng bán', tables: ['sales_pg.orders'] } },
    },
  },
  conformed_dimensions: [],
};
const discovery = {
  ref: 'sales_pg', domain: 'sales', kind: 'postgres', location: 'sales_raw', discovered_at: now(),
  objects: [{
    name: 'orders', kind: 'table', primary_key: ['order_id'], row_estimate: 1200,
    columns: [
      { name: 'order_id', type: 'integer' },
      { name: 'amount_text', type: 'text' },
      { name: 'ordered_at_text', type: 'text' },
    ],
  }],
  foreign_keys: [],
};
const policy = {
  files: {
    'domains.yml': 'domains:\n  sales: {}\n',
    'budgets.yml': 'per_day:\n  max_usd: 2.0\n  max_batch_runs: 20\n',
    'objectives.yml': 'domains:\n  sales: {}\n',
  },
  limits: { max_llm_calls: 6, max_spec_attempts: 2, max_sql_attempts: 2, max_chat_calls: 20, max_usd_per_day: 2 },
};

function readyReport(index, request) {
  const id = `batch${String(index).padStart(3, '0')}-0000-4000-8000-000000000000`;
  const model = `fct_batch_${index}`;
  const createdAt = now();
  return {
    run_id: id, version: 1, domain: 'sales', created_by: 'minh', trigger: 'user_request', parent_run_id: null,
    source_scope: [{ source_ref: 'sales_pg', tables: ['orders'] }], regression: null, created_at: createdAt,
    user_request: request, status: 'READY_FOR_REVIEW', stage_reached: 'report',
    plan: {
      domain: 'sales', summary: `Pipeline ${request}`, model_name: model,
      sources: [{ id: 'orders', source_ref: 'sales_pg', object: 'orders', purpose: 'đơn hàng' }],
      grain: ['ordered_at'], metrics: [{ name: 'revenue', definition: 'Tổng doanh thu' }],
      schedule: '0 2 * * *', question: null,
    },
    spec: {
      domain: 'sales',
      sources: [{
        id: 'orders', kind: 'postgres', source_ref: 'sales_pg', table: 'orders', primary_key: ['order_id'],
        cursor_field: null, write_disposition: 'replace', target: { table: `${model}__orders` },
        staging: { columns: [
          { from: 'order_id', as: 'order_id', type: 'integer' },
          { from: 'amount_text', as: 'amount', type: 'decimal(18,2)' },
          { from: 'ordered_at_text', as: 'ordered_at', type: 'date' },
        ], reject_if: [] },
      }],
      transform: {
        model_name: model, grain: ['orders.ordered_at'], metrics: [{ name: 'revenue', expr: 'sum(orders.amount)' }],
        filters: [], joins: [], tests: [],
      },
      schedule: { cron: '0 2 * * *' },
    },
    spec_history: [],
    files: { [`dbt_project/models/gold/${model}.sql`]: `sha256:e2e${index}` },
    files_hash: `sha256:e2e-batch-${index}`,
    gates: [
      { name: 'spec_schema', result: 'pass', detail: 'Spec hợp lệ', observed: {} },
      { name: 'budget', result: 'pass', detail: 'Trong ngân sách', observed: { bytes: 1000, usd: 0.000001 } },
      { name: 'post_run', result: 'pass', detail: 'Chạy thử đạt', observed: { gold_rows: 2, gold_samples: [{ ordered_at: '2026-10-13', revenue: '120000' }] } },
    ],
    checks: [
      { name: 'row_reconcile', source_id: 'orders', result: 'pass', observed: { source: 2, bronze: 2 } },
      { name: 'hash_reconcile', source_id: 'orders', result: 'pass', observed: { match: true } },
      { name: 'idempotent', source_id: 'orders', result: 'pass', observed: { run1: 2, run2: 2 } },
      { name: 'staging_reconcile', source_id: 'orders', result: 'pass', observed: { bronze: 2, staging: 2, duplicates: 0, rejected: 0, reject_pct: 0, reject_reasons: {}, reject_samples: [] } },
      { name: 'dbt_test', source_id: null, result: 'pass', observed: { passed: 2, failed: 0 } },
      { name: 'metric_reconcile', source_id: null, result: 'pass', observed: { matched: 2, rows: { source: 2 } } },
    ],
    cost: { estimated_bytes: 1000, actual_bytes: 1000, usd: 0.000001, llm_usd: 0.0005 },
    llm_usage: [{ stage: 'plan', tier: 'cheap', model: 'fixture', input_tokens: 10, output_tokens: 10, usd: 0.0005 }],
    timing: { baseline_sec: 0.2, optimized_sec: null }, attempts: { spec: 0, sql: 0 }, llm_calls: 1,
    error_trace: [], approval: null, review_history: [],
    timeline: [{ event: 'submitted', at: createdAt, actor: 'minh' }, { event: 'finished', at: createdAt, actor: null }],
  };
}

// Run có sẵn cho màn duyệt: tóm tắt đủ dữ liệu với tên dài và bảng cùng tên ở hai kết nối, và một run thiếu dữ liệu.
// Để riêng khỏi `reports` để số thứ tự run của mẻ (batch001…) không đổi.
const LONG_MODEL = 'fct_doanh_thu_theo_ngay_cua_tung_cua_hang_khu_vuc_mien_nam_va_mien_bac_sau_khi_tru_hoan_tien';
const seeded = [
  (() => {
    const report = readyReport(0, 'Doanh thu theo ngày của từng cửa hàng hai miền');
    const [orders] = report.spec.sources;
    const sources = ['kho_ban_hang_mien_dong_postgres', 'kho_ban_hang_mien_tay_postgres'].map((ref, i) => ({
      ...orders, id: `orders_${i}`, source_ref: ref, table: 'don_hang_da_xac_nhan_kem_thanh_toan', target: { table: `${LONG_MODEL}__orders_${i}` },
    }));
    return {
      ...report, run_id: 'summary1-0000-4000-8000-000000000000', source_scope: null,
      plan: { ...report.plan, model_name: LONG_MODEL, schedule: '*/45 * * * *' },
      spec: { ...report.spec, sources, transform: { ...report.spec.transform, model_name: LONG_MODEL }, schedule: { cron: '*/45 * * * *' } },
      gates: report.gates.map(g => g.name === 'post_run' ? { ...g, observed: { ...g.observed, gold_rows: 1234567 } } : g),
      checks: report.checks.filter(c => c.source_id === null),
      optimizer: [{ rule: 'incremental', applied: true, before_sec: 0.2155, after_sec: 0.1405, reason: 'nhanh hơn' }],
    };
  })(),
  {
    ...readyReport(0, 'Đếm khách hàng mới theo tháng'), run_id: 'summary2-0000-4000-8000-000000000000',
    status: 'NEEDS_HUMAN', stage_reached: 'plan', spec: null, files: {}, files_hash: null, gates: [], checks: [], cost: undefined,
    plan: { domain: 'sales', model_name: 'fct_new_customers_monthly', sources: [{ id: 'customers', source_ref: 'sales_pg', object: 'customers' }] },
    error_trace: [{ stage: 'plan', error: 'Thiếu bảng customers trong nguồn.' }],
  },
];

function findReport(rawId) {
  const id = decodeURIComponent(rawId);
  return [...seeded, ...reports].find(report => report.run_id === id || report.run_id.startsWith(id));
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || '/', `http://127.0.0.1:${port}`);
  const path = url.pathname;
  if (path === '/health') return json(response, 200, { status: 'ok' });

  if (path === '/api/v1/pipeline/session' && request.method === 'POST') {
    const body = await readJson(request);
    const user = users[body.username];
    if (!user || passwords[body.username] !== body.password) return json(response, 401, { detail: 'Sai tài khoản.' });
    return json(response, 200, user, { 'Set-Cookie': `pipeline_session=token-${body.username}; Path=/api/v1; HttpOnly; SameSite=Strict` });
  }
  if (path === '/api/v1/pipeline/session' && request.method === 'DELETE') return json(response, 200, { ok: true });
  if (path === '/api/v1/pipeline/session' && request.method === 'GET') {
    const user = tokenUser(request);
    return user ? json(response, 200, user) : json(response, 401, { detail: 'Vui lòng đăng nhập.' });
  }

  const user = tokenUser(request);
  if (!user) return json(response, 401, { detail: 'Vui lòng đăng nhập.' });
  if (path === '/api/v1/pipeline/users' && request.method === 'GET') return json(response, 200, Object.values(users));
  if (path === '/api/v1/catalog' && request.method === 'GET') return json(response, 200, catalog);
  if (path === '/api/v1/catalog/sources' && request.method === 'GET') {
    return json(response, 200, [{ ref: 'sales_pg', domain: 'sales', kind: 'postgres', connection_ref: 'e2e', location: 'sales_raw', supported: true }]);
  }
  if (path === '/api/v1/catalog/sources/sales_pg/objects' && request.method === 'GET') return json(response, 200, discovery);
  if (path === '/api/v1/policy' && request.method === 'GET') return json(response, 200, policy);
  if (path === '/api/v1/agent/runs/batch/budget' && request.method === 'GET') {
    return json(response, 200, {
      run_count: 0, reservation_usd_per_run: 0.05, estimated_usd: 0, spent_today_usd: 0,
      remaining_usd: 2, remaining_after_usd: 2, max_usd_per_day: 2, max_batch_runs: 20, allowed: true,
    });
  }
  if (path === '/api/v1/agent/runs/batch' && request.method === 'POST') {
    if (user.role !== 'Engineer') return json(response, 403, { detail: 'Chỉ Engineer được tạo pipeline.' });
    const body = await readJson(request);
    const created = body.requests.map((item, offset) => readyReport(reports.length + offset + 1, item.request));
    reports.push(...created);
    return json(response, 201, {
      runs: created,
      budget: {
        run_count: created.length, reservation_usd_per_run: 0.05, estimated_usd: created.length * 0.05,
        spent_today_usd: 0, remaining_usd: 2, remaining_after_usd: 2 - created.length * 0.05,
        max_usd_per_day: 2, max_batch_runs: 20, allowed: true,
      },
    });
  }
  if (path === '/api/v1/runs' && request.method === 'GET') return json(response, 200, [...seeded, ...reports]);

  const filesMatch = /^\/api\/v1\/runs\/([^/]+)\/files$/.exec(path);
  if (filesMatch && request.method === 'GET') {
    const report = findReport(filesMatch[1]);
    if (!report) return json(response, 404, { detail: 'Không tìm thấy run.' });
    return json(response, 200, { run_id: report.run_id, version: report.version, files: Object.fromEntries(Object.keys(report.files).map(name => [name, 'select ordered_at, sum(amount) as revenue from staging group by 1'])) });
  }
  const approveMatch = /^\/api\/v1\/runs\/([^/]+)\/approve$/.exec(path);
  if (approveMatch && request.method === 'POST') {
    const report = findReport(approveMatch[1]);
    if (!report) return json(response, 404, { detail: 'Không tìm thấy run.' });
    const body = await readJson(request);
    report.status = 'APPROVED';
    report.stage_reached = 'review';
    report.approval = { decision: 'approved', reviewer: user.id, files_hash: report.files_hash, version: report.version, at: now(), comment: body.comment || '' };
    report.review_history.push(report.approval);
    return json(response, 200, report);
  }
  const deployMatch = /^\/api\/v1\/runs\/([^/]+)\/deploy$/.exec(path);
  if (deployMatch && request.method === 'POST') {
    const report = findReport(deployMatch[1]);
    if (!report) return json(response, 404, { detail: 'Không tìm thấy run.' });
    report.stage_reached = 'deploy';
    report.deployment = {
      repo: 'atomesus/pipelines', branch: `agent/${report.run_id}`, pr_number: 42,
      pr_url: 'https://example.test/pull/42', head_sha: 'e2e-head', state: 'open', opened_at: now(),
    };
    return json(response, 200, report);
  }

  const runMatch = /^\/api\/v1\/runs\/([^/]+)$/.exec(path);
  if (runMatch && request.method === 'GET') {
    const report = findReport(runMatch[1]);
    return report ? json(response, 200, report) : json(response, 404, { detail: 'Không tìm thấy run.' });
  }
  return json(response, 404, { detail: `Fixture chưa hỗ trợ ${request.method} ${path}` });
});

server.listen(port, '127.0.0.1');
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)));
