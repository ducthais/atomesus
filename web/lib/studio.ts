import { isAgentSpec, type AgentSpec, type Approval, type RunReport } from './postgres-api.ts';

/** Phase P0–P14 theo SPEC §9, cùng thứ tự với `stage_reached` của RunReport. */
export const STAGE_IDS = [
  'intake', 'plan', 'confirm', 'resolve', 'propose', 'arbitrate', 'render', 'static',
  'budget', 'el_run', 'el_validate', 't_run', 't_validate', 'optimize', 'report',
] as const;
export const STAGES = [
  'Tiếp nhận yêu cầu', 'Lập kế hoạch', 'Xác nhận kế hoạch', 'Phân giải ngữ cảnh',
  'Sinh đặc tả', 'Đối chiếu mục tiêu', 'Render mã nguồn', 'Kiểm tra đặc tả',
  'Ước tính ngân sách', 'Chạy ingestion', 'Đối soát dữ liệu', 'Chạy dbt',
  'Kiểm tra kết quả', 'Tối ưu hiệu năng', 'Tổng hợp báo cáo',
] as const;

/** Trần đọc từ policy/budgets.yml qua GET /api/v1/policy. */
export type Limits = { max_llm_calls: number; max_spec_attempts: number; max_sql_attempts: number; max_chat_calls: number; max_usd_per_day: number };
export type Status = 'DRAFT' | 'GENERATING' | 'VALIDATING' | 'READY_FOR_REVIEW' | 'APPROVED' | 'DEPLOYED'
  | 'NEEDS_HUMAN' | 'BLOCKED_SEMANTIC_CONFLICT' | 'BLOCKED_BUDGET' | 'BLOCKED_AST' | 'REJECTED'
  | 'INFRA_ERROR' | 'INTERNAL_ERROR' | 'DEPLOY_FAILED' | 'CANCELLED' | 'NO_IMPROVEMENT' | 'PLAN_CONFIRMED';
export type CheckState = 'unmeasured' | 'running' | 'pass' | 'fail';
export type Check = { id: string; name: string; state: CheckState; value: string; detail: string };
export type Log = { id: string; stage: number; level: 'info' | 'success' | 'error' | 'warning'; message: string; detail?: string; at: string };
export type Plan = { sources: string[]; tables: string[]; primaryKey: string; cursor: string; metric: string; schedule: string; writeMode: string; model: string; filters: string };
export type FileArtifact = { path: string; language: string; content: string };
export type Asset = { name: string; layer: string; kind: string; rows: string; state: string };
export type RejectSample = { reason: string; values: Record<string, unknown> };
export type StagingEvidence = {
  sourceId: string; state: 'pass' | 'fail'; bronze: number; duplicates: number; rejected: number; staging: number;
  rejectPct: number; maxRejectPct: number | null; reasons: { reason: string; count: number }[]; samples: RejectSample[];
};
export type Run = {
  id: string; title: string; domain: string; owner: string; request: string; version: number;
  filesHash: string; status: Status; stage: number; plan: Plan; summary: string; checks: Check[]; logs: Log[];
  specRetries: number; sqlRetries: number; llmCalls: number; updatedAt: string; usd: number;
  /** Tiền gọi LLM (USD), tổng token, và số lần gọi chưa tính được tiền (model chưa có giá trong budgets.yml). */
  llmUsd: number; llmTokens: number; llmUnpriced: number;
  fileCount: number; assets: Asset[]; approval: Approval | null; error: string | null; question: string | null;
  staging?: StagingEvidence[]; reviewHistory?: Approval[];
  /** Bảng `nguồn.bảng` tích sẵn khi bấm "Sửa & chạy lại": phạm vi đã chọn + bảng kế hoạch cần mà chưa chọn. */
  retryTables?: string[];
  /** Run đang chờ backend trả về (POST /agent/runs chạy đồng bộ ~30 giây), chưa có trong sổ bằng chứng. */
  pending?: boolean;
};

export const STATUS_LABEL: Record<Status, string> = {
  DRAFT: 'Nháp', PLAN_CONFIRMED: 'Đã xác nhận kế hoạch', GENERATING: 'Đang chạy', VALIDATING: 'Đang kiểm tra',
  READY_FOR_REVIEW: 'Chờ duyệt', APPROVED: 'Đã duyệt', DEPLOYED: 'Đã deploy', NEEDS_HUMAN: 'Cần kỹ sư',
  BLOCKED_SEMANTIC_CONFLICT: 'Chặn ngữ nghĩa', BLOCKED_BUDGET: 'Vượt ngân sách', BLOCKED_AST: 'SQL bị chặn',
  REJECTED: 'Đã từ chối', INFRA_ERROR: 'Lỗi hạ tầng', INTERNAL_ERROR: 'Lỗi hệ thống', DEPLOY_FAILED: 'Deploy lỗi',
  CANCELLED: 'Đã hủy', NO_IMPROVEMENT: 'Không có bản tốt hơn',
};
export const RUNNING: Status[] = ['DRAFT', 'PLAN_CONFIRMED', 'GENERATING', 'VALIDATING'];
const GATE_LABEL: Record<string, string> = {
  spec_schema: 'Spec hợp lệ', schema_real: 'Cột có thật ở nguồn', currency: 'Quy đổi tiền tệ', join_fanout: 'Không cộng trùng khi ghép', time_grain: 'Gộp đúng kỳ yêu cầu', semantic: 'Ngữ nghĩa',
  ast_guard: 'Biểu thức an toàn', budget: 'Ngân sách', arbitrate: 'Chọn lịch chạy (P5)', post_run: 'Tổng kết chạy thử',
};
const CHECK_LABEL: Record<string, string> = {
  row_reconcile: 'Đối soát dòng', hash_reconcile: 'Đối soát nội dung', idempotent: 'Idempotent',
  staging_reconcile: 'Làm sạch staging', dbt_test: 'dbt tests', metric_reconcile: 'Đối chiếu độc lập với nguồn',
};

export const shortId = (id: string) => id.startsWith('pending-') ? 'đang tạo' : id.slice(0, 8);
export const stageIndex = (stage: string) => Math.max(0, STAGE_IDS.indexOf(stage as typeof STAGE_IDS[number]));
const traceText = (item: RunReport['error_trace'][number]) => item.error ?? item.message ?? JSON.stringify(item);
const num = (value: unknown) => typeof value === 'number' ? value.toLocaleString('vi-VN') : '—';

function toCheck(check: RunReport['checks'][number]): Check {
  const o = check.observed;
  const where = check.source_id ? ` · ${check.source_id}` : '';
  const base = { id: `${check.name}${check.source_id ? `:${check.source_id}` : ''}`, name: (CHECK_LABEL[check.name] ?? check.name) + where, state: check.result };
  if (check.name === 'row_reconcile') return { ...base, value: `${num(o.source)} → ${num(o.bronze)}`, detail: 'Số dòng nguồn → bronze' };
  if (check.name === 'staging_reconcile') return { ...base, value: `${num(o.staging)} sạch · ${num(o.rejected)} rác`,
    detail: `${o.reject_pct ?? 0}% rác${o.max_reject_pct !== undefined ? ` (ngưỡng ${o.max_reject_pct}%)` : ''}${o.duplicates ? ` · ${o.duplicates} trùng` : ''}` };
  if (check.name === 'metric_reconcile') {
    const rows = (o.rows ?? {}) as { source?: number };
    return { ...base, value: check.result === 'pass' ? `${num(o.matched)} / ${num(rows.source)} dòng khớp` : `${num(o.mismatch_count)} khác · ${num(o.missing_count)} thiếu · ${num(o.extra_count)} thừa`,
      detail: 'Pipeline có thực hiện đúng Spec không: tính lại gold thẳng trên Postgres nguồn (không qua dlt / DuckDB / dbt), so từng dòng. Không kiểm tra Spec có đúng nhu cầu.' };
  }
  if (check.name === 'dbt_test') return { ...base, value: `${num(o.passed)} / ${num(Number(o.passed ?? 0) + Number(o.failed ?? 0))} đạt`, detail: 'dbt build trong sandbox' };
  return { ...base, value: check.result === 'pass' ? 'Đạt' : 'Không đạt', detail: JSON.stringify(o).slice(0, 120) };
}

function toPlan(report: RunReport): Plan {
  const spec = report.spec;
  if (isAgentSpec(spec)) {
    const t = spec.transform;
    return {
      sources: spec.sources.map(s => `${s.kind} · ${s.source_ref}`), tables: spec.sources.map(s => s.table ?? s.endpoint ?? s.path_pattern ?? s.id),
      primaryKey: spec.sources.map(s => s.primary_key.join(', ') || '—').join(' | '),
      cursor: spec.sources.map(s => s.cursor_field ?? 'không có').join(' | '),
      metric: t.metrics.map(m => `${m.name} = ${m.expr}`).join('; '), schedule: spec.schedule.cron,
      writeMode: spec.sources.map(s => s.write_disposition).join(' | '), model: t.model_name, filters: t.filters.join(' AND '),
    };
  }
  const plan = report.plan;
  return {
    sources: plan?.sources.map(s => s.source_ref) ?? [], tables: plan?.sources.map(s => s.object) ?? [],
    primaryKey: '—', cursor: '—', metric: plan?.metrics?.map(m => `${m.name}: ${m.definition ?? ''}`).join('; ') || '—',
    schedule: plan?.schedule ?? '—', writeMode: '—', model: plan?.model_name ?? '—', filters: '',
  };
}

function toLogs(report: RunReport, stage: number): Log[] {
  const at = report.created_at;
  const logs: Log[] = (report.spec_history ?? []).map((item, index) => {
    const id = `${report.run_id}-spec-${index}`;
    if (item.reason) return { id, stage: stageIndex('propose'), level: 'info' as const, message: `Version ${item.version ?? index + 1}: ${item.reason}`, at };
    const errors = item.errors ?? [];
    return errors.length
      ? { id, stage: stageIndex('propose'), level: 'warning' as const, message: `Spec lần ${item.attempt ?? index + 1} bị từ chối, gửi lỗi lại cho LLM`, detail: errors.join('\n'), at }
      : { id, stage: stageIndex('propose'), level: 'success' as const, message: `Spec lần ${item.attempt ?? index + 1} hợp lệ`, at };
  });
  report.error_trace.forEach((item, index) => logs.push({
    id: `${report.run_id}-error-${index}`, stage: stageIndex(item.stage), level: 'error',
    message: item.question ? `Cần làm rõ: ${item.question}` : traceText(item).split('\n')[0].slice(0, 200),
    detail: [traceText(item), item.source_id && `Nguồn: ${item.source_id}`].filter(Boolean).join('\n'), at,
  }));
  if (report.status === 'DRAFT' && report.stage_reached === 'confirm') logs.push({ id: `${report.run_id}-plan`, stage, level: 'info', message: 'Kế hoạch xong. Chờ Engineer xác nhận trước khi agent viết Spec.', at });
  if (report.status === 'READY_FOR_REVIEW') logs.push({ id: `${report.run_id}-ready`, stage, level: 'success', message: 'Mọi kiểm tra đạt. Chờ Reviewer duyệt.', at });
  return logs;
}

function toAssets(report: RunReport): Asset[] {
  const spec = isAgentSpec(report.spec) ? report.spec : null;
  const checks = report.checks;
  const assets: Asset[] = [];
  for (const source of spec?.sources ?? []) {
    const rows = checks.find(c => c.name === 'row_reconcile' && c.source_id === source.id);
    const staged = checks.find(c => c.name === 'staging_reconcile' && c.source_id === source.id);
    assets.push({ name: `bronze.${source.target.table}`, layer: 'Bronze', kind: `${source.kind} · ${source.source_ref}`, rows: num(rows?.observed.bronze), state: rows ? rows.result === 'pass' ? 'Khớp nguồn' : 'Lệch nguồn' : 'Chưa chạy' });
    assets.push({ name: `staging.stg_${spec!.transform.model_name}__${source.id}`, layer: 'Staging', kind: 'Model dbt', rows: num(staged?.observed.staging), state: staged ? `${num(staged.observed.rejected)} dòng rác` : 'Chưa chạy' });
  }
  if (spec) {
    const post = report.gates.find(g => g.name === 'post_run');
    assets.push({ name: `gold.${spec.transform.model_name}`, layer: 'Gold', kind: 'Model dbt', rows: num(post?.observed.gold_rows), state: post ? post.result === 'pass' ? 'Đạt' : 'Không đạt' : 'Chưa chạy' });
  }
  return assets;
}

function toStaging(report: RunReport): StagingEvidence[] {
  return report.checks.filter(check => check.name === 'staging_reconcile').map(check => {
    const o = check.observed;
    const rawSamples = Array.isArray(o.reject_samples) ? o.reject_samples as Record<string, unknown>[] : [];
    const samples = rawSamples.map(sample => {
      if (sample.values && typeof sample.values === 'object' && !Array.isArray(sample.values)) {
        return { reason: String(sample.reason ?? 'không rõ'), values: sample.values as Record<string, unknown> };
      }
      const { _reject_reason, ...values } = sample;
      return { reason: String(_reject_reason ?? sample.reason ?? 'không rõ'), values };
    });
    const reasons = o.reject_reasons && typeof o.reject_reasons === 'object' && !Array.isArray(o.reject_reasons)
      ? Object.entries(o.reject_reasons as Record<string, unknown>).map(([reason, count]) => ({ reason, count: Number(count) || 0 }))
      : [];
    return {
      sourceId: check.source_id ?? 'nguồn', state: check.result,
      bronze: Number(o.bronze) || 0, duplicates: Number(o.duplicates) || 0,
      rejected: Number(o.rejected) || 0, staging: Number(o.staging) || 0,
      rejectPct: Number(o.reject_pct) || 0,
      maxRejectPct: o.max_reject_pct === undefined ? null : Number(o.max_reject_pct),
      reasons, samples,
    };
  });
}

/** RunReport (sổ bằng chứng) → mô hình hiển thị của Studio. Không thêm số liệu nào không có trong report. */
export function toStudioRun(report: RunReport): Run {
  const status = (report.status in STATUS_LABEL ? report.status : 'INTERNAL_ERROR') as Status;
  const stage = ['READY_FOR_REVIEW', 'APPROVED', 'DEPLOYED'].includes(status) ? stageIndex('report') : stageIndex(report.stage_reached);
  const last = report.error_trace.at(-1);
  return {
    id: report.run_id, title: report.user_request.length > 60 ? report.user_request.slice(0, 57) + '…' : report.user_request,
    domain: report.domain, owner: report.created_by, request: report.user_request, version: report.version,
    filesHash: report.files_hash ?? '', status, stage, plan: toPlan(report), summary: report.plan?.summary ?? '',
    checks: [...report.checks.map(toCheck), ...report.gates.map(g => ({ id: `gate:${g.name}`, name: GATE_LABEL[g.name] ?? g.name, state: g.result, value: g.result === 'pass' ? 'Đạt' : 'Không đạt', detail: g.detail }))],
    logs: toLogs(report, stage), specRetries: report.attempts?.spec ?? 0, sqlRetries: report.attempts?.sql ?? 0,
    llmCalls: report.llm_calls ?? 0, updatedAt: report.created_at, usd: report.cost?.usd ?? 0,
    llmUsd: report.cost?.llm_usd ?? 0,
    llmTokens: (report.llm_usage ?? []).reduce((n, c) => n + (c.input_tokens ?? 0) + (c.output_tokens ?? 0), 0),
    llmUnpriced: (report.llm_usage ?? []).filter(c => c.usd === null).length,
    fileCount: Object.keys(report.files ?? {}).length, assets: toAssets(report), approval: report.approval,
    staging: toStaging(report), reviewHistory: report.review_history ?? [],
    error: last ? (last.question ? `Cần làm rõ: ${last.question}` : traceText(last)) : null, question: last?.question ?? null,
    retryTables: [...new Set([...(report.source_scope ?? []).flatMap(s => s.tables.map(t => `${s.source_ref}.${t}`)), ...(last?.need_tables ?? [])])],
  };
}

/** Run tạm hiển thị trong lúc chờ agent (planner → codegen → render → test_run). Không giả lập tiến độ. */
export function pendingRun(request: string, owner: string): Run {
  const now = new Date().toISOString();
  return {
    id: `pending-${Date.now()}`, title: request.length > 60 ? request.slice(0, 57) + '…' : request, domain: '—', owner, request,
    version: 1, filesHash: '', status: 'GENERATING', stage: stageIndex('plan'), summary: '',
    plan: { sources: [], tables: [], primaryKey: '—', cursor: '—', metric: '—', schedule: '—', writeMode: '—', model: '—', filters: '' },
    checks: [], logs: [{ id: 'pending', stage: stageIndex('plan'), level: 'info', message: 'Agent đang lập kế hoạch; xong sẽ dừng chờ bạn xác nhận rồi mới viết Spec và chạy thử.', at: now }],
    specRetries: 0, sqlRetries: 0, llmCalls: 0, updatedAt: now, usd: 0, llmUsd: 0, llmTokens: 0, llmUnpriced: 0, fileCount: 0, assets: [], approval: null, error: null, question: null, pending: true,
  };
}

const LANGUAGE: Record<string, string> = { py: 'python', sql: 'sql', yml: 'yaml', yaml: 'yaml' };
export function toArtifacts(files: Record<string, string>): FileArtifact[] {
  return Object.entries(files).sort(([a], [b]) => a.localeCompare(b)).map(([path, content]) => ({ path, content, language: LANGUAGE[path.split('.').pop() ?? ''] ?? 'plaintext' }));
}

export function canReview(run: Run, role: string, user: string): boolean {
  return role === 'Reviewer' && run.owner !== user && run.status === 'READY_FOR_REVIEW' && run.checks.every(c => c.state === 'pass');
}

/** Một dòng của màn duyệt nhiều run: run chờ duyệt kèm lý do không chọn được (cùng luật với `canReview`). */
export type BatchItem = { run: Run; eligible: boolean; reason: string | null };

export function batchReviewItems(runs: Run[], role: string, user: string): BatchItem[] {
  return runs.filter(run => run.status === 'READY_FOR_REVIEW').map(run => {
    const failed = run.checks.filter(c => c.state !== 'pass').length;
    const reason = role !== 'Reviewer' ? 'Chỉ Reviewer duyệt'
      : run.owner === user ? 'Run của bạn: không tự duyệt'
      : failed ? `${failed} kiểm tra chưa đạt` : null;
    return { run, eligible: reason === null, reason };
  });
}

/** Một dòng của GET /runs/{id}/messages (bảng run_messages). */
export type ChatMessage = { id: number; run_id: string; role: 'user' | 'assistant'; author: string; intent: string; content: string; at: string };
export type ChatResponse = { intent: 'new_request' | 'ask'; reply: string; run_id: string; report: RunReport };
export type ChatMode = { mode: 'ask' | 'new_request' | null; hint: string };

/** Ô chat hỏi về run đang mở, hoặc tạo run mới khi chưa mở run nào (chỉ Engineer). */
export function chatMode(run: Run | undefined, role: string): ChatMode {
  if (run?.pending) return { mode: null, hint: 'Agent đang chạy; hỏi sau khi có RunReport.' };
  if (run) return { mode: 'ask', hint: 'Câu trả lời lấy từ RunReport của run này, không đổi trạng thái run.' };
  if (role === 'Engineer') return { mode: 'new_request', hint: 'Mô tả nhu cầu dữ liệu; agent lập kế hoạch, viết Spec và chạy thử.' };
  return { mode: null, hint: 'Chỉ Engineer tạo pipeline. Mở một run để hỏi về nó.' };
}

/** Body cho POST /chat; null khi không gửi được. */
export function chatPayload(message: string, mode: ChatMode['mode'], runId?: string): { message: string; run_id?: string } | null {
  const text = message.trim();
  if (!text || !mode) return null;
  if (mode === 'ask') return runId ? { message: text, run_id: runId } : null;
  return { message: text };
}
