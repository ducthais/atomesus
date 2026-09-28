// Nhãn hiển thị cho P0–P17 và 6 nhóm (SPEC_1.md mục 9, 9.0). Nhóm chỉ là khái niệm hiển thị.

import { STAGE_IDS, type EventStatus, type RunEvent, type RunStatus, type StageId } from './types.ts';

export type PhaseKind = 'Tất định' | 'LLM' | 'Người' | 'Hỗn hợp';

export const PHASES: Record<StageId, { label: string; kind: PhaseKind }> = {
  intake: { label: 'Tiếp nhận', kind: 'Tất định' },
  plan: { label: 'Lập kế hoạch', kind: 'LLM' },
  confirm: { label: 'Xác nhận kế hoạch', kind: 'Người' },
  resolve: { label: 'Phân giải schema', kind: 'Tất định' },
  propose: { label: 'Sinh spec', kind: 'LLM' },
  arbitrate: { label: 'Phân xử', kind: 'Tất định' },
  render: { label: 'Render', kind: 'Tất định' },
  static: { label: 'Kiểm tĩnh', kind: 'Tất định' },
  budget: { label: 'Ngân sách', kind: 'Tất định' },
  el_run: { label: 'Chạy dlt', kind: 'Tất định' },
  el_validate: { label: 'Thẩm định E-L', kind: 'Tất định' },
  t_run: { label: 'Chạy dbt', kind: 'Tất định' },
  t_validate: { label: 'Thẩm định T', kind: 'Tất định' },
  optimize: { label: 'Tối ưu', kind: 'Hỗn hợp' },
  report: { label: 'Báo cáo', kind: 'Tất định' },
  review: { label: 'Review', kind: 'Người' },
  deploy: { label: 'Deploy', kind: 'Tất định' },
  persist: { label: 'Lưu mẫu', kind: 'Tất định' },
};

export const code = (s: StageId) => `P${STAGE_IDS.indexOf(s)}`;

export interface Group {
  id: string;
  label: string;
  caption: string;
  stages: StageId[];
  tone: string;
}

export const GROUPS: Group[] = [
  { id: 'G1', label: 'Tiếp nhận', caption: 'Yêu cầu, kế hoạch, xác nhận', stages: ['intake', 'plan', 'confirm'], tone: 'g1' },
  { id: 'G2', label: 'Phân giải', caption: 'Schema thật, dò trùng thực thể', stages: ['resolve'], tone: 'g2' },
  { id: 'G3', label: 'Sinh', caption: 'Spec JSON, phân xử, render 4 file', stages: ['propose', 'arbitrate', 'render'], tone: 'g3' },
  { id: 'G4', label: 'Kiểm', caption: 'AST, schema, ngân sách', stages: ['static', 'budget'], tone: 'g4' },
  { id: 'G5', label: 'Chạy', caption: 'dlt, dbt, đối soát, tối ưu', stages: ['el_run', 'el_validate', 't_run', 't_validate', 'optimize'], tone: 'g5' },
  { id: 'G6', label: 'Quyết định', caption: 'Bằng chứng, review, deploy', stages: ['report', 'review', 'deploy', 'persist'], tone: 'g6' },
];

export type NodeState = 'idle' | 'running' | 'pass' | 'fail' | 'blocked' | 'waiting';

/** Trạng thái cuối của từng phase, lấy từ sự kiện cuối cùng của phase đó. */
export function stageStates(events: RunEvent[], status: RunStatus | undefined): Record<StageId, NodeState> {
  const out = Object.fromEntries(STAGE_IDS.map((s) => [s, 'idle'])) as Record<StageId, NodeState>;
  const last = new Map<StageId, EventStatus>();
  for (const e of events) last.set(e.stage, e.status);
  for (const [stage, st] of last) {
    out[stage] = st === 'escalate' ? 'blocked' : st;
  }
  // Hai cổng người: sự kiện "running" nghĩa là đang chờ người, không phải đang chạy.
  if (out.confirm === 'running') out.confirm = 'waiting';
  if (status === 'DRAFT' && out.plan === 'blocked') out.plan = 'waiting'; // câu hỏi làm rõ đang chờ người trả lời
  if (status === 'READY_FOR_REVIEW') out.review = 'waiting';
  if (status === 'APPROVED') { out.review = 'pass'; out.deploy = 'waiting'; }
  if (status === 'REJECTED') out.review = 'fail';
  // Run đã dừng thì không có phase nào còn "đang chạy".
  if (status && isTerminal(status)) {
    for (const s of STAGE_IDS) if (out[s] === 'running') out[s] = 'fail';
  }
  return out;
}

export function groupState(states: Record<StageId, NodeState>, g: Group): NodeState {
  const list = g.stages.map((s) => states[s]);
  for (const s of ['blocked', 'fail', 'waiting', 'running'] as const) if (list.includes(s)) return s;
  if (list.every((s) => s === 'pass')) return 'pass';
  if (list.some((s) => s === 'pass')) return 'running';
  return 'idle';
}

export const STATE_LABEL: Record<NodeState, string> = {
  idle: 'Chưa chạy', running: 'Đang chạy', pass: 'Đạt', fail: 'Lỗi', blocked: 'Bị chặn', waiting: 'Chờ người',
};

const TERMINAL: RunStatus[] = [
  'DEPLOYED', 'NEEDS_HUMAN', 'BLOCKED_SEMANTIC_CONFLICT', 'BLOCKED_BUDGET', 'BLOCKED_AST', 'REJECTED',
  'INFRA_ERROR', 'INTERNAL_ERROR', 'DEPLOY_FAILED',
];
export const isTerminal = (s: RunStatus) => TERMINAL.includes(s);

export const STATUS_LABEL: Record<RunStatus, string> = {
  DRAFT: 'Nháp', PLAN_CONFIRMED: 'Đã xác nhận', GENERATING: 'Đang sinh', VALIDATING: 'Đang thẩm định',
  READY_FOR_REVIEW: 'Chờ duyệt', APPROVED: 'Đã duyệt', DEPLOYED: 'Đã deploy', NEEDS_HUMAN: 'Cần người xử lý',
  BLOCKED_SEMANTIC_CONFLICT: 'Chặn ngữ nghĩa', BLOCKED_BUDGET: 'Vượt ngân sách', BLOCKED_AST: 'Chặn SQL',
  REJECTED: 'Bị từ chối', INFRA_ERROR: 'Lỗi hạ tầng', INTERNAL_ERROR: 'Lỗi hệ thống', DEPLOY_FAILED: 'Deploy lỗi',
};

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'muted';
export function statusTone(s: RunStatus): Tone {
  if (s === 'DEPLOYED' || s === 'APPROVED') return 'ok';
  if (s === 'READY_FOR_REVIEW' || s === 'DRAFT') return 'warn';
  if (s === 'GENERATING' || s === 'VALIDATING' || s === 'PLAN_CONFIRMED') return 'info';
  return 'bad';
}
