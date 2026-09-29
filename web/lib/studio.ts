export const STAGES = [
  'Tiếp nhận yêu cầu', 'Lập kế hoạch', 'Xác nhận phạm vi', 'Phân giải ngữ cảnh',
  'Sinh đặc tả', 'Kiểm tra đặc tả', 'Render mã nguồn', 'Kiểm tra SQL',
  'Ước tính ngân sách', 'Chạy ingestion', 'Đối soát dữ liệu', 'Chạy dbt',
  'Kiểm tra kết quả', 'Tối ưu hiệu năng', 'Tổng hợp báo cáo',
] as const;

export const LIMITS = { specRetries: 2, sqlRetries: 3, llmCalls: 8, maxBytes: 5_000_000_000, maxUsd: 0.05, dailyUsd: 2 };
export type Status = 'VALIDATING' | 'READY_FOR_REVIEW' | 'BLOCKED_SEMANTIC_CONFLICT' | 'BLOCKED_BUDGET' | 'BLOCKED_AST' | 'NEEDS_HUMAN' | 'APPROVED' | 'REJECTED' | 'DEPLOYED' | 'CANCELLED';
export type Scenario = 'success' | 'semantic' | 'budget' | 'sql' | 'ast' | 'metric';
export type CheckState = 'unmeasured' | 'running' | 'pass' | 'fail';
export type Check = { id: string; name: string; state: CheckState; value: string; detail: string };
export type Log = { id: string; stage: number; level: 'info' | 'success' | 'error' | 'warning'; message: string; detail?: string; at: string };
export type Plan = { sources: string[]; tables: string[]; primaryKey: string; cursor: string; metric: string; schedule: string; writeMode: string; model: string; partitionFilter?: string };
export type FileArtifact = { path: string; language: string; before: string; after: string };
export type Run = {
  id: string; title: string; domain: 'Sales' | 'Marketing'; owner: 'minh' | 'an';
  request: string; version: number; filesHash: string; approvedHash?: string;
  status: Status; stage: number; scenario: Scenario; plan: Plan; checks: Check[];
  logs: Log[]; specRetries: number; sqlRetries: number; llmCalls: number;
  updatedAt: string; reviewComment?: string; artifactPath?: string;
};

export const STATUS_LABEL: Record<Status, string> = {
  VALIDATING: 'Đang chạy', READY_FOR_REVIEW: 'Chờ duyệt',
  BLOCKED_SEMANTIC_CONFLICT: 'Chặn ngữ nghĩa', BLOCKED_BUDGET: 'Vượt ngân sách',
  BLOCKED_AST: 'SQL bị chặn', NEEDS_HUMAN: 'Cần kỹ sư', APPROVED: 'Đã duyệt',
  REJECTED: 'Đã từ chối', DEPLOYED: 'Đã xuất artifact', CANCELLED: 'Đã hủy',
};

export const CHECK_NAMES = [
  ['row_reconcile', 'Đối soát dòng'], ['idempotency', 'Idempotent'],
  ['dbt_tests', 'dbt tests'], ['query_cost', 'Byte quét & chi phí'],
  ['performance', 'Thời gian chạy'], ['metric_reconcile', 'Đúng doanh thu'],
] as const;
export const emptyChecks = (): Check[] => CHECK_NAMES.map(([id, name]) => ({ id, name, state: 'unmeasured', value: '—', detail: 'Chưa đo' }));

export const defaultPlan: Plan = {
  sources: ['Postgres', 'REST API'], tables: ['sales_raw.orders', 'sales_api.payments'],
  primaryKey: 'order_id', cursor: 'updated_at', metric: "SUM(CASE WHEN currency = 'USD' THEN amount * 25400 ELSE amount END)",
  schedule: '0 * * * *', writeMode: 'merge', model: 'fct_daily_revenue', partitionFilter: '',
};

export const EXAMPLES: { label: string; request: string; scenario: Scenario; domain: 'Sales' | 'Marketing' }[] = [
  { label: 'Doanh thu ngày', request: 'Gộp đơn hàng từ Postgres và REST API, ghi merge theo order_id, tính doanh thu mỗi ngày và chạy hàng giờ.', scenario: 'success', domain: 'Sales' },
  { label: 'Xung đột customer', request: 'Hợp nhất customer của Sales và Marketing thành một bảng chung.', scenario: 'semantic', domain: 'Sales' },
  { label: 'Vượt ngân sách', request: 'Đọc toàn bộ lịch sử chiến dịch Marketing để tính hiệu quả mỗi giờ.', scenario: 'budget', domain: 'Marketing' },
  { label: 'Sai tên cột', request: 'Tính doanh thu ngày từ cột total_amount của bảng orders.', scenario: 'sql', domain: 'Sales' },
  { label: 'SQL bị chặn', request: 'Dùng SELECT * từ bảng gold để xuất toàn bộ dữ liệu.', scenario: 'ast', domain: 'Sales' },
  { label: 'Sai doanh thu', request: 'Tính doanh thu ngày và đối soát với sổ cái, giữ lại sai lệch để kiểm tra.', scenario: 'metric', domain: 'Sales' },
];

export function makeRun(request: string, domain: 'Sales' | 'Marketing', scenario: Scenario, plan: Plan, index = 0): Run {
  const stamp = Date.now() + index;
  return {
    id: `RUN-${String(stamp).slice(-6)}`, title: scenario === 'semantic' ? 'Hợp nhất customer' : scenario === 'budget' ? 'Hiệu quả chiến dịch' : plan.model.replaceAll('_', ' '),
    domain, owner: 'minh', request, version: 1, filesHash: stamp.toString(16).slice(-8).padStart(8, '0'),
    status: 'VALIDATING', stage: 0, scenario, plan, checks: emptyChecks(), logs: [],
    specRetries: 0, sqlRetries: 0, llmCalls: 0, updatedAt: new Date().toISOString(),
  };
}

export function demoRuns(): Run[] {
  const ready = makeRun(EXAMPLES[0].request, 'Sales', 'success', defaultPlan);
  ready.id = 'RUN-024'; ready.title = 'Doanh thu theo ngày'; ready.version = 3;
  ready.filesHash = 'a7f3c902'; ready.status = 'READY_FOR_REVIEW'; ready.stage = 14;
  ready.checks = passedChecks(); ready.llmCalls = 5; ready.specRetries = 1; ready.sqlRetries = 2;
  ready.logs = [{ id: 'demo-ready', stage: 14, level: 'success', message: 'RunReport đạt, sẵn sàng review phiên bản 3.', at: ready.updatedAt }];
  const blocked = makeRun(EXAMPLES[1].request, 'Sales', 'semantic', defaultPlan, 1);
  blocked.id = 'RUN-023'; blocked.title = 'Hợp nhất customer'; blocked.status = 'BLOCKED_SEMANTIC_CONFLICT'; blocked.stage = 3;
  blocked.logs = [{ id: 'demo-semantic', stage: 3, level: 'error', message: 'Sales.customer và Marketing.customer có định nghĩa khác nhau.', at: blocked.updatedAt }];
  const budget = makeRun(EXAMPLES[2].request, 'Marketing', 'budget', { ...defaultPlan, sources: ['REST API'], tables: ['mkt_raw.campaigns'], model: 'fct_campaign_performance' }, 2);
  budget.id = 'RUN-022'; budget.title = 'Hiệu quả chiến dịch'; budget.status = 'BLOCKED_BUDGET'; budget.stage = 8;
  budget.logs = [{ id: 'demo-budget', stage: 8, level: 'error', message: 'Ước tính quét 8,2 GB vượt trần 5 GB.', at: budget.updatedAt }];
  return [ready, blocked, budget];
}

export function passedChecks(): Check[] {
  return [
    { id: 'row_reconcile', name: 'Đối soát dòng', state: 'pass', value: '12.480 = 12.480', detail: 'Nguồn = bronze · minh họa' },
    { id: 'idempotency', name: 'Idempotent', state: 'pass', value: '12.480 dòng', detail: 'Không đổi sau lần chạy thứ hai' },
    { id: 'dbt_tests', name: 'dbt tests', state: 'pass', value: '6 / 6 đạt', detail: 'Schema + giá trị · minh họa' },
    { id: 'query_cost', name: 'Byte quét & chi phí', state: 'pass', value: '1,2 GB · $0,0075', detail: 'Chi phí ước tính · minh họa' },
    { id: 'performance', name: 'Thời gian chạy', state: 'pass', value: '4,2 → 0,9 s', detail: 'Baseline → optimized · minh họa' },
    { id: 'metric_reconcile', name: 'Đúng doanh thu', state: 'pass', value: 'Khớp', detail: 'Sai lệch 0 VND · minh họa' },
  ];
}

export function canReview(run: Run, role: string, user: string): boolean {
  return role === 'Reviewer' && run.owner !== user && run.status === 'READY_FOR_REVIEW' && run.checks.every(c => c.state === 'pass');
}
export function canExport(run: Run): boolean { return run.status === 'APPROVED' && run.approvedHash === run.filesHash; }
export function newVersion(run: Run, scenario: Scenario = 'success'): Run {
  return { ...run, version: run.version + 1, filesHash: Math.random().toString(16).slice(2, 10).padEnd(8, '0'),
    approvedHash: undefined, status: 'VALIDATING', stage: 0, scenario, checks: emptyChecks(), logs: [],
    specRetries: 0, sqlRetries: 0, llmCalls: 0, updatedAt: new Date().toISOString(), artifactPath: undefined };
}

export const POLICIES: Record<string, string> = {
  'domains.yml': `domains:\n  sales:\n    customer: "Tài khoản đã ký hợp đồng"\n    tables: [sales_raw.customers]\n  marketing:\n    customer: "Người để lại email"\n    tables: [mkt_raw.leads]\nconformed_dimensions: []`,
  'budgets.yml': `per_run:\n  max_bytes_scanned: 5000000000\n  max_usd: 0.05\n  max_llm_calls: 8\n  spec_retries: 2\n  sql_retries: 3\nper_day:\n  max_usd: 2.00`,
  'objectives.yml': `sales:\n  freshness_minutes: 60\n  prefer: cheapest_feasible\nmarketing:\n  freshness_minutes: 1440\n  prefer: cheapest_feasible`,
};

export function filesForRun(run: Run): FileArtifact[] {
  const model = run.plan.model || 'fct_daily_revenue';
  return [
    { path: 'ingestion/orders_pipeline.py', language: 'python', before: 'import dlt\n\n@dlt.resource(write_disposition="replace")\ndef orders(rows):\n    yield from rows\n', after: `import dlt\n\n@dlt.resource(primary_key="${run.plan.primaryKey}", write_disposition="${run.plan.writeMode}")\ndef orders(rows):\n    yield from rows  # Postgres sales_raw.orders\n\n@dlt.resource(primary_key="payment_id", write_disposition="merge")\ndef payments(rows):\n    yield from rows  # REST API sales_api.payments\n\n# Both resources use ${run.plan.cursor} as incremental cursor.\n# Connection settings are supplied by the sandbox backend.\n` },
    { path: `models/${model}.sql`, language: 'sql', before: `{{ config(materialized='table') }}\nselect order_date, sum(amount) as revenue\nfrom {{ source('bronze', 'orders') }}\ngroup by 1\n`, after: `{{ config(materialized='incremental', unique_key='order_date') }}\nwith payment_totals as (\n  select order_id, sum(amount) as paid_amount\n  from {{ source('bronze', 'payments') }}\n  group by order_id\n), normalized as (\n  select cast(o.order_date as date) as order_date,\n    case when o.currency = 'USD'\n      then coalesce(p.paid_amount, o.amount) * 25400\n      else coalesce(p.paid_amount, o.amount) end as amount_vnd\n  from {{ source('bronze', 'orders') }} o\n  left join payment_totals p on p.order_id = o.order_id\n  where o.status <> 'refunded'${run.plan.partitionFilter ? `\n    and ${run.plan.partitionFilter}` : ''}\n)\nselect order_date, sum(amount_vnd) as revenue_vnd\nfrom normalized\ngroup by order_date\n` },
    { path: `models/${model}.yml`, language: 'yaml', before: `version: 2\nmodels:\n  - name: ${model}\n`, after: `version: 2\nmodels:\n  - name: ${model}\n    columns:\n      - name: order_date\n        tests: [not_null, unique]\n      - name: revenue_vnd\n        tests: [not_null]\n` },
    { path: `dagster/${model}_asset.py`, language: 'python', before: '# Chưa có asset\n', after: `from dagster import asset, AssetExecutionContext\n\n@asset(group_name="sales", compute_kind="dbt")\ndef ${model}(context: AssetExecutionContext):\n    context.log.info("Run dlt ingestion then dbt model")\n    # Demo artifact: chưa kết nối Dagster runtime.\n` },
  ];
}
