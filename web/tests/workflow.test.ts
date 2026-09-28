import test from 'node:test';
import assert from 'node:assert/strict';
import { canApprove, canCreate, canDeploy, canReject, canRequestChanges } from '../lib/workflow.ts';
import { GROUPS, groupState, stageStates } from '../lib/phases.ts';
import type { RunEvent, RunStatus } from '../lib/types.ts';

const engineer = { role: 'Engineer' as const, user: 'minh' };
const reviewer = { role: 'Reviewer' as const, user: 'an' };
const ready = { status: 'READY_FOR_REVIEW' as RunStatus, created_by: 'minh', files_hash: 'sha256:aaa', approval: null };

test('chỉ reviewer khác người tạo mới duyệt được', () => {
  assert.equal(canApprove(ready, engineer), false);
  assert.equal(canApprove(ready, reviewer), true);
  assert.equal(canApprove({ ...ready, created_by: 'an' }, reviewer), false, 'không tự duyệt run của mình');
  assert.equal(canApprove({ ...ready, status: 'VALIDATING' }, reviewer), false);
  assert.equal(canApprove({ ...ready, files_hash: null }, reviewer), false);
  assert.equal(canReject(ready, reviewer), true);
});

test('deploy cần phê duyệt khớp files_hash hiện tại', () => {
  const approved = { ...ready, status: 'APPROVED' as RunStatus, approval: { hash: 'sha256:aaa' } };
  assert.equal(canDeploy(approved, reviewer), true);
  assert.equal(canDeploy({ ...approved, approval: { hash: 'sha256:old' } }, reviewer), false);
  assert.equal(canDeploy({ ...approved, approval: null }, reviewer), false);
  assert.equal(canDeploy(approved, engineer), false);
  assert.equal(canDeploy({ ...ready, approval: { hash: 'sha256:aaa' } }, reviewer), false, 'chưa APPROVED');
});

test('góp ý: reviewer ở P15, engineer khi agent đã dừng', () => {
  assert.equal(canRequestChanges(ready, reviewer), true);
  assert.equal(canRequestChanges(ready, engineer), false);
  assert.equal(canRequestChanges({ ...ready, status: 'BLOCKED_BUDGET' }, engineer), true);
  assert.equal(canRequestChanges({ ...ready, status: 'BLOCKED_SEMANTIC_CONFLICT' }, engineer), false);
  assert.equal(canCreate(engineer), true);
  assert.equal(canCreate(reviewer), false);
});

const ev = (seq: number, stage: RunEvent['stage'], status: RunEvent['status']): RunEvent =>
  ({ run_id: 'r', stage, status, message: '', payload: {}, seq, at: '' });

test('trạng thái phase lấy từ sự kiện cuối, cổng người hiện "chờ"', () => {
  const events = [ev(1, 'intake', 'pass'), ev(2, 'plan', 'pass'), ev(3, 'confirm', 'running')];
  const s = stageStates(events, 'DRAFT');
  assert.equal(s.intake, 'pass');
  assert.equal(s.confirm, 'waiting');
  assert.equal(s.resolve, 'idle');
  assert.equal(groupState(s, GROUPS[0]), 'waiting');
});

test('vòng SQL: P7 fail rồi pass thì nhóm Kiểm hiện đạt', () => {
  const events = [ev(1, 'static', 'fail'), ev(2, 'render', 'pass'), ev(3, 'static', 'pass'), ev(4, 'budget', 'pass')];
  const s = stageStates(events, 'VALIDATING');
  assert.equal(groupState(s, GROUPS[3]), 'pass');
});

test('run bị chặn: phase đang chạy dở thành lỗi, nhóm hiện bị chặn', () => {
  const events = [ev(1, 'resolve', 'running'), ev(2, 'resolve', 'blocked')];
  const s = stageStates(events, 'BLOCKED_SEMANTIC_CONFLICT');
  assert.equal(groupState(s, GROUPS[1]), 'blocked');
  const t = stageStates([ev(1, 'el_run', 'running')], 'INFRA_ERROR');
  assert.equal(t.el_run, 'fail');
});
