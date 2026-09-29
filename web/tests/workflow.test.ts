import test from 'node:test';
import assert from 'node:assert/strict';
import { canApprove, canDeploy, revision } from '../lib/workflow.ts';
import { hasDemoFixture, initialRuns } from '../lib/demo.ts';

test('approval requires a ready run and a different reviewer', () => {
  const run = initialRuns[0];
  assert.equal(canApprove(run, 'Engineer', 'an'), false);
  assert.equal(canApprove(run, 'Reviewer', 'minh'), false);
  assert.equal(canApprove(run, 'Reviewer', 'an'), true);
  assert.equal(canApprove(initialRuns[1], 'Reviewer', 'an'), false);
});

test('deployment rejects missing and stale approval hashes', () => {
  const run = { ...initialRuns[0], status: 'APPROVED' as const };
  assert.equal(canDeploy(run), false);
  assert.equal(canDeploy({ ...run, approvalHash: 'old-hash' }), false);
  assert.equal(canDeploy({ ...run, approvalHash: run.hash }), true);
  assert.equal(canDeploy({ ...run, status: 'REJECTED', approvalHash: run.hash }), false);
});

test('revision invalidates approval and resets validation', () => {
  const run = { ...initialRuns[0], status: 'APPROVED' as const, approvalHash: initialRuns[0].hash };
  const next = { ...run, ...revision(run, 'new-version-hash') };
  assert.equal(next.version, run.version + 1);
  assert.equal(next.status, 'VALIDATING');
  assert.equal(next.approvalHash, undefined);
  assert.equal(next.evidence, undefined);
  assert.equal(next.attempts?.spec, 0);
  assert.equal(next.attempts?.llm, run.attempts?.llm);
  assert.equal(canDeploy(next), false);
});

test('demo evidence is limited to the daily revenue fixture', () => {
  assert.equal(hasDemoFixture(initialRuns[0]), true);
  assert.equal(hasDemoFixture(initialRuns[2]), false);
  assert.equal(hasDemoFixture({...initialRuns[0], description:'Tính tồn kho theo giờ'}), false);
});
