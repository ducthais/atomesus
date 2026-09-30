import type { Run } from './demo';

export function canApprove(run: Run, role: string, user: string) {
  return run.status === 'READY_FOR_REVIEW' && role === 'Reviewer' && run.owner !== user;
}

export function canDeploy(run: Run) {
  return run.status === 'APPROVED' && !!run.approvalHash && run.approvalHash === run.hash;
}

export function revision(run: Run, hash: string): Partial<Run> {
  return { status: 'VALIDATING', phase: 4, version: run.version + 1, hash, approvalHash: undefined, evidence: undefined, attempts:{spec:0,sql:run.attempts?.sql || 0,llm:run.attempts?.llm || 0} };
}
