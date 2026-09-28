// Chế độ demo: phát lại các run thật đã ghi bằng scripts/record_demo.py, không cần backend.
// Cùng hợp đồng với client thật (lib/api.ts) để giao diện không phân biệt hai chế độ.
// Trạng thái chỉ nằm trong bộ nhớ trang (không dùng localStorage — CLAUDE.md).

import recording from './recording.json';
import type {
  Approval, BudgetToday, CatalogTable, Cost, Decision, Explanation, FilesResponse, JsonObject, PolicyResponse, RunEvent,
  RunStatus, RunSummary, RunView, StageId,
} from '../types.ts';

export class DemoError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// --------------------------------------------------------------------------- dữ liệu ghi

interface LightState {
  status: RunStatus;
  stage_reached: StageId;
  attempts: { spec: number; sql: number; el: number };
  llm_calls: number;
  version: number;
  files_hash: string | null;
  error_trace_len: number;
}
interface Step { event: RunEvent; state: LightState }
interface Segment { steps: Step[]; view: RunView; last_seq: number }
interface Scenario {
  run_id: string;
  request: string;
  domain: string;
  created_by: string;
  main: Segment;
  approve?: Segment;
  deploy?: Segment;
  explain?: Explanation;
  files?: Record<string, string>;
  explain_block?: Record<string, Explanation>;
}
type ScenarioName = 'golden' | 'clarify' | 'semantic' | 'scoped' | 'budget';
interface Recording { recorded_at: string; scenarios: Record<ScenarioName, Scenario>; policy: PolicyResponse }

const REC = recording as unknown as Recording;
export const RECORDED_AT = REC.recorded_at;

/** Chọn kịch bản theo từ khoá — như LLM giả lập của backend (agent/llm.py). */
function pickScenario(request: string): ScenarioName {
  const t = request.toLowerCase();
  if (/khách hàng|customer/.test(t)) return 'semantic';
  if (/web_events|truy cập|sự kiện|clickstream/.test(t)) return 'budget';
  if (/đơn hàng|doanh thu|orders|don hang/.test(t)) return 'golden';
  return 'clarify';
}

// Phase mà mỗi cổng / phép kiểm tra thuộc về: chỉ hiện khi run đã đi tới phase đó.
const GATE_STAGE: Record<string, StageId> = {
  semantic: 'resolve', spec_schema: 'static', ast_guard: 'static', schema_real: 'static', compile: 'static', budget: 'budget',
  row_reconcile: 'el_validate', hash_reconcile: 'el_validate', idempotent: 'el_validate',
  dbt_test: 't_validate', metric_reconcile: 't_validate',
};
const TERMINAL: RunStatus[] = [
  'DEPLOYED', 'NEEDS_HUMAN', 'BLOCKED_SEMANTIC_CONFLICT', 'BLOCKED_BUDGET', 'BLOCKED_AST', 'REJECTED', 'INFRA_ERROR',
  'INTERNAL_ERROR', 'DEPLOY_FAILED',
];

// --------------------------------------------------------------------------- run demo

interface DemoRun {
  id: string;
  scenario: ScenarioName;
  request: string;
  domain: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  final: RunView;
  queue: Step[];
  events: RunEvent[];
  state: LightState;
  paused: 'confirm' | 'answer' | null;
  versionOffset: number;
  approval: Approval | null;
  decisions: Decision[];
  deployed_paths: string[];
  ticket: JsonObject | null;
  statusOverride: RunStatus | null;
  timer: ReturnType<typeof setTimeout> | null;
}

const runs = new Map<string, DemoRun>();
const listeners = new Map<string, Set<(e: RunEvent) => void>>();
let counter = 0;

const now = () => new Date().toISOString();
const newId = () => {
  counter += 1;
  const hex = (Date.now() + counter).toString(16).padStart(12, '0');
  return `demo${counter.toString(16).padStart(4, '0')}-${hex.slice(0, 4)}-4${hex.slice(4, 7)}-8${hex.slice(7, 10)}-${hex.padEnd(12, '0').slice(0, 12)}`;
};
const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

function rewrite(run: DemoRun, text: string): string {
  const src = REC.scenarios[run.scenario];
  return text
    .split(src.run_id).join(run.id)
    .replace(/\/\S*\/data\/code_location/g, 'code location sandbox')
    .replace(/\/\S*\/data\/sandboxes\/\S+/g, `sandbox/${run.id.slice(0, 8)}`);
}

function hashFor(run: DemoRun, recorded: string | null): string | null {
  if (!recorded) return null;
  if (!run.versionOffset) return recorded;
  const tag = run.versionOffset.toString(16).padStart(4, '0');
  return `${recorded.slice(0, recorded.length - 4)}${tag}`;
}

function emit(run: DemoRun, step: Step): void {
  const e: RunEvent = {
    ...clone(step.event),
    run_id: run.id,
    seq: run.events.length + 1,
    at: now(),
    message: rewrite(run, step.event.message),
  };
  if (typeof e.payload.sandbox === 'string') e.payload.sandbox = `sandbox/${run.id.slice(0, 8)}`;
  run.events.push(e);
  run.state = { ...step.state };
  run.updated_at = e.at;
  listeners.get(run.id)?.forEach((cb) => cb(e));
}

/** Nhịp phát lại: mỗi sự kiện cách nhau vài trăm mili giây; dừng ở hai cổng người như backend thật. */
function tick(run: DemoRun): void {
  run.timer = null;
  if (run.paused) return;
  const step = run.queue.shift();
  if (!step) return;
  emit(run, step);
  const e = step.event;
  if (e.stage === 'confirm' && e.status === 'running') { run.paused = 'confirm'; return; }
  if (e.stage === 'plan' && e.status === 'escalate' && run.queue.length > 0) { run.paused = 'answer'; return; }
  const slow = e.status === 'running' && ['el_run', 't_run', 'plan', 'propose'].includes(e.stage);
  run.timer = setTimeout(() => tick(run), slow ? 900 : 380);
}

function start(run: DemoRun): void {
  if (!run.timer) run.timer = setTimeout(() => tick(run), 300);
}

function reached(run: DemoRun, stage: StageId): boolean {
  return run.events.some((e) => e.stage === stage && e.status !== 'running');
}

function derivedStatus(run: DemoRun): RunStatus {
  if (run.statusOverride) return run.statusOverride;
  if (run.queue.length === 0 && !run.paused) return run.final.status;
  if (!run.events.some((e) => e.stage === 'confirm' && e.status === 'pass')) return 'DRAFT';
  if (reached(run, 'budget') || run.events.some((e) => e.stage === 'el_run')) return 'VALIDATING';
  return 'GENERATING';
}

function view(run: DemoRun): RunView {
  const f = run.final;
  const status = derivedStatus(run);
  const last = run.events[run.events.length - 1];
  const has = (s: StageId) => reached(run, s);
  const emptyCost: Cost = { estimated_bytes: null, actual_bytes: null, usd: null, is_estimate: true, engine: 'duckdb' };
  const done = run.queue.length === 0 && !run.paused;
  return {
    ...clone(f),
    run_id: run.id,
    created_at: run.created_at,
    updated_at: run.updated_at,
    created_by: run.created_by,
    domain: run.domain,
    user_request: run.request,
    status,
    stage_reached: last?.stage ?? 'intake',
    version: (done ? f.version : run.state.version) + run.versionOffset,
    plan: has('plan') ? clone(f.plan) : null,
    spec: has('propose') ? clone(f.spec) : null,
    spec_history: has('propose') ? clone(f.spec_history) : [],
    files: has('render') ? clone(f.files) : {},
    files_hash: done || has('report') ? hashFor(run, f.files_hash) : null,
    gates: f.gates.filter((g) => has(GATE_STAGE[g.name] ?? 'report')),
    checks: f.checks.filter((g) => has(GATE_STAGE[g.name] ?? 'report')),
    cost: has('budget') ? clone(f.cost) : emptyCost,
    timing: has('optimize') ? clone(f.timing) : { baseline_sec: null, optimized_sec: null },
    optimizer: has('optimize') ? clone(f.optimizer) : [],
    arbitration: has('arbitrate') ? clone(f.arbitration) : null,
    attempts: done ? clone(f.attempts) : { ...run.state.attempts },
    llm_calls: done ? f.llm_calls : run.state.llm_calls,
    error_trace: f.error_trace.slice(0, done ? f.error_trace.length : run.state.error_trace_len),
    gold_preview: has('t_run') ? clone(f.gold_preview) : [],
    provenance: has('propose') ? clone(f.provenance) : { template_id: null, template_reviewer: null },
    approval: run.approval,
    decisions: clone(run.decisions),
    deployed_paths: [...run.deployed_paths],
    ticket: run.ticket,
    llm_provider: 'demo (phát lại LLM giả lập)',
    awaiting: run.paused === 'confirm' ? 'confirm_plan' : run.paused === 'answer' ? 'answer' : null,
    terminal: TERMINAL.includes(status),
  };
}

function make(scenario: ScenarioName, request: string, domain: string, createdBy: string, createdAt = now()): DemoRun {
  const sc = REC.scenarios[scenario];
  const run: DemoRun = {
    id: newId(), scenario, request, domain, created_by: createdBy, created_at: createdAt, updated_at: createdAt,
    final: clone(sc.main.view), queue: clone(sc.main.steps), events: [],
    state: clone(sc.main.steps[0].state), paused: null, versionOffset: 0, approval: null, decisions: [],
    deployed_paths: [], ticket: null, statusOverride: null, timer: null,
  };
  runs.set(run.id, run);
  return run;
}

/** Phát ngay toàn bộ sự kiện (cho các run có sẵn trong lịch sử), bỏ qua điểm dừng ở cổng người. */
function fastForward(run: DemoRun, at: string): void {
  while (run.queue.length) {
    const step = run.queue.shift()!;
    emit(run, step);
    run.events[run.events.length - 1].at = at;
  }
  run.updated_at = at;
}

function synth(run: DemoRun, stage: StageId, status: RunEvent['status'], message: string): void {
  emit(run, { event: { run_id: run.id, stage, status, message, payload: {}, seq: 0, at: '' }, state: run.state });
}

function get(id: string): DemoRun {
  ensureSeed();
  const run = runs.get(id);
  if (!run) throw new DemoError(404, `Không có run ${id}`);
  return run;
}

// Lịch sử ban đầu: vài run đã chạy xong để danh sách không trống. Chỉ dựng khi demo thật sự được dùng.
let seeded = false;
function ensureSeed(): void {
  if (seeded) return;
  seeded = true;
  const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
  const g = REC.scenarios.golden;
  const deployed = make('golden', g.request, 'sales', 'minh', ago(125));
  fastForward(deployed, ago(118));
  const approveStep = g.approve?.steps[0];
  deployed.approval = { reviewer: 'an', hash: deployed.final.files_hash ?? '', approved_at: ago(95), comment: '' };
  deployed.decisions = [{ action: 'approve', actor: 'an', comment: '', at: ago(95), version: 1 }];
  if (approveStep) emit(deployed, approveStep);
  for (const s of g.deploy?.steps ?? []) emit(deployed, s);
  deployed.deployed_paths = g.deploy?.view.deployed_paths ?? [];
  deployed.statusOverride = 'DEPLOYED';
  deployed.events.slice(-6).forEach((e) => { e.at = ago(94); });
  deployed.updated_at = ago(94);

  const b = REC.scenarios.budget;
  fastForward(make('budget', b.request, 'sales', 'minh', ago(48)), ago(46));
  const s = REC.scenarios.semantic;
  const sem = make('semantic', s.request, 'sales', 'minh', ago(31));
  fastForward(sem, ago(31));
}

// --------------------------------------------------------------------------- API giả lập

function summary(run: DemoRun): RunSummary {
  const v = view(run);
  const model = (v.spec?.transform as { model_name?: string } | undefined)?.model_name;
  return {
    run_id: v.run_id, title: model ?? v.user_request.slice(0, 60), request: v.user_request, domain: v.domain,
    created_by: v.created_by, status: v.status, stage_reached: v.stage_reached, version: v.version,
    updated_at: v.updated_at, created_at: v.created_at, usd: v.cost.usd, reviewer: v.approval?.reviewer ?? null,
  };
}

const ok = <T>(x: T) => Promise.resolve(x);

function fail(status: number, message: string): Promise<never> {
  return Promise.reject(new DemoError(status, message));
}

export const demoApi = {
  health: () => ok({ status: 'ok', llm: 'demo' as string | null }),
  runs: () => {
    ensureSeed();
    return ok([...runs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(summary));
  },
  run: (id: string) => {
    ensureSeed();
    return runs.has(id) ? ok(view(get(id))) : fail(404, `Không có run ${id}`);
  },
  events: (id: string, after = 0) => ok(clone(get(id).events.filter((e) => e.seq > after))),
  files: (id: string, version?: number): Promise<FilesResponse> => {
    const run = get(id);
    const files = REC.scenarios[run.scenario].files ?? REC.scenarios.golden.files ?? {};
    const v = version ?? view(run).version;
    return ok({ version: v, files, previous_version: v > 1 ? v - 1 : null, previous: v > 1 ? files : {} });
  },
  policy: () => ok(clone(REC.policy)),
  catalog: (): Promise<CatalogTable[]> => ok([]),
  budgetToday: (): Promise<BudgetToday> => {
    const today = new Date().toISOString().slice(0, 10);
    const spent = [...runs.values()].filter((r) => r.created_at.startsWith(today)).reduce((a, r) => a + (view(r).cost.usd ?? 0), 0);
    const max = Number(/per_day:\s*\n\s*max_usd:\s*([\d.]+)/.exec(REC.policy.files['policy/budgets.yml'] ?? '')?.[1] ?? 2);
    return ok({ spent_usd: Math.round(spent * 1e6) / 1e6, max_usd: max, is_estimate: true });
  },
  explain: (id: string): Promise<Explanation> => {
    const run = get(id);
    const v = view(run);
    const recorded = REC.scenarios[run.scenario].explain;
    if (recorded && v.terminal && v.status !== 'DEPLOYED' && v.status !== 'REJECTED') return ok(clone(recorded));
    if (v.status === 'NEEDS_HUMAN') {
      return ok({ title: 'Vì sao cần người xử lý', summary: 'Run đã dừng.', bullets: v.error_trace.map((e) => `[${e.stage}] ${e.message}`), action: null });
    }
    return ok({ title: 'Không bị chặn', summary: `Run đang ở trạng thái ${v.status}.`, bullets: [], action: null });
  },
  explainBlock: (id: string, path: string, start: number, end: number): Promise<Explanation> => {
    const run = get(id);
    const ex = REC.scenarios[run.scenario].explain_block?.[path] ?? REC.scenarios.golden.explain_block?.[path];
    if (!ex) return fail(404, `Không có file ${path}`);
    return ok({ ...clone(ex), title: `Giải thích ${path}:${start}-${end}` });
  },

  createRun: (request: string, domain: string, createdBy: string) => {
    ensureSeed();
    const run = make(pickScenario(request), request, domain, createdBy);
    start(run);
    return ok({ run_id: run.id });
  },
  confirmPlan: (id: string) => {
    const run = get(id);
    if (run.paused !== 'confirm') return fail(409, `Không thể xác nhận kế hoạch ở trạng thái ${view(run).status}`);
    run.paused = null;
    start(run);
    return ok({ accepted: true });
  },
  answer: (id: string) => {
    const run = get(id);
    if (run.paused !== 'answer') return fail(409, 'Chỉ trả lời câu hỏi làm rõ khi run ở DRAFT');
    run.paused = null;
    start(run);
    return ok({ accepted: true });
  },
  approve: (id: string, filesHash: string, reviewer: string, comment: string) => {
    const run = get(id);
    const v = view(run);
    if (v.status !== 'READY_FOR_REVIEW') return fail(409, `Chỉ duyệt được run READY_FOR_REVIEW (hiện ${v.status})`);
    if (reviewer === run.created_by) return fail(409, 'Không được duyệt run của chính mình');
    if (filesHash !== v.files_hash) return fail(409, 'files_hash không khớp phiên bản hiện tại');
    run.approval = { reviewer, hash: filesHash, approved_at: now(), comment };
    run.decisions.push({ action: 'approve', actor: reviewer, comment, at: now(), version: v.version });
    run.statusOverride = 'APPROVED';
    synth(run, 'review', 'pass', `${reviewer} duyệt v${v.version} (${filesHash.slice(7, 15)})`);
    return ok(view(run));
  },
  reject: (id: string, reason: string, actor: string) => {
    const run = get(id);
    if (!reason.trim()) return fail(422, 'Từ chối bắt buộc có lý do');
    if (actor === run.created_by) return fail(409, 'Không được quyết định run của chính mình');
    run.decisions.push({ action: 'reject', actor, comment: reason, at: now(), version: view(run).version });
    run.statusOverride = 'REJECTED';
    synth(run, 'review', 'blocked', `${actor} từ chối: ${reason}`);
    return ok(view(run));
  },
  requestChanges: (id: string, feedback: string, actor: string) => {
    const run = get(id);
    const v = view(run);
    if (!feedback.trim()) return fail(422, 'Yêu cầu sửa bắt buộc có nhận xét');
    if (!['READY_FOR_REVIEW', 'NEEDS_HUMAN', 'BLOCKED_BUDGET'].includes(v.status)) {
      return fail(409, `Không thể yêu cầu sửa ở trạng thái ${v.status}`);
    }
    // Vòng người: quay về P4, đặt lại attempts.spec, phê duyệt cũ mất hiệu lực, phiên bản mới.
    run.decisions.push({ action: 'request_changes', actor, comment: feedback, at: now(), version: v.version });
    run.approval = null;
    run.statusOverride = null;
    run.versionOffset += 1;
    const steps = clone(REC.scenarios[run.scenario].main.steps);
    const from = steps.findIndex((s) => s.event.stage === 'propose');
    run.queue = steps.slice(from < 0 ? 0 : from).map((s) => ({ ...s, state: { ...s.state, attempts: { ...s.state.attempts, spec: 0 } } }));
    synth(run, 'review', 'running', `${actor} yêu cầu sửa: ${feedback}`);
    start(run);
    return ok({ accepted: true });
  },
  deploy: (id: string, actor: string) => {
    const run = get(id);
    const v = view(run);
    if (v.status !== 'APPROVED' || !run.approval || run.approval.hash !== v.files_hash) {
      synth(run, 'deploy', 'blocked', 'Deploy bị chặn: phê duyệt không còn hiệu lực');
      return fail(409, 'Deploy chỉ chạy khi run APPROVED và approval.hash == files_hash');
    }
    const n = Object.keys(v.files).length;
    synth(run, 'deploy', 'running', `Ghi ${n} file vào code location`);
    synth(run, 'deploy', 'pass', `Đã ghi ${n} file vào code location sandbox`);
    synth(run, 'persist', 'running', 'Lưu (yêu cầu, spec) đã duyệt làm few-shot');
    synth(run, 'persist', 'pass', 'Lưu mẫu đã duyệt');
    run.deployed_paths = Object.keys(v.files);
    run.statusOverride = 'DEPLOYED';
    return ok({ ...view(run), code_location: `code location sandbox (deploy bởi ${actor})` });
  },
  cancel: (id: string) => {
    const run = get(id);
    if (!view(run).terminal && run.queue.length) {
      if (run.timer) clearTimeout(run.timer);
      run.queue = [];
      run.paused = null;
      run.statusOverride = 'NEEDS_HUMAN';
      synth(run, run.events[run.events.length - 1]?.stage ?? 'intake', 'fail', 'Run bị huỷ');
    }
    return ok(view(run));
  },
  rerun: (id: string, actor: string) => {
    const old = get(id);
    return demoApi.createRun(old.request, old.domain, actor || old.created_by);
  },
  retryScoped: (id: string, domain: string, actor: string) => {
    const old = get(id);
    const run = make('scoped', old.request, domain, actor || old.created_by);
    start(run);
    return ok({ run_id: run.id });
  },
  ticket: (id: string, kind: 'steward_review' | 'budget_increase', body: string, actor: string) => {
    const run = get(id);
    const t = { id: `T-${String(runs.size).padStart(3, '0')}`, kind, body, actor, status: 'open' };
    run.ticket = t;
    return ok({ id: t.id });
  },
};

export function demoSubscribe(id: string, after: number, onEvent: (e: RunEvent) => void): () => void {
  const run = runs.get(id);
  const set = listeners.get(id) ?? new Set();
  listeners.set(id, set);
  set.add(onEvent);
  if (run) queueMicrotask(() => run.events.filter((e) => e.seq > after).forEach((e) => onEvent(clone(e))));
  return () => { set.delete(onEvent); };
}
