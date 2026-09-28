// Điều kiện hiển thị nút review/deploy. Backend vẫn là nơi cưỡng chế (control_plane/deploy.py);
// UI chỉ ẩn nút để người dùng không bấm vào hành động chắc chắn bị từ chối.

import type { RunStatus, Viewer } from './types';

interface ReviewableRun {
  status: RunStatus;
  created_by: string;
  files_hash: string | null;
  approval: { hash: string } | null;
}

export function canApprove(run: ReviewableRun, viewer: Viewer): boolean {
  return run.status === 'READY_FOR_REVIEW' && viewer.role === 'Reviewer' && run.created_by !== viewer.user && !!run.files_hash;
}

export function canReject(run: ReviewableRun, viewer: Viewer): boolean {
  return canApprove(run, viewer);
}

/** Reviewer góp ý ở P15; engineer góp ý cho run của mình khi agent đã dừng (hết lượt, vượt ngân sách). */
export function canRequestChanges(run: ReviewableRun, viewer: Viewer): boolean {
  if (run.status === 'READY_FOR_REVIEW') return viewer.role === 'Reviewer' && run.created_by !== viewer.user;
  return run.status === 'NEEDS_HUMAN' || run.status === 'BLOCKED_BUDGET';
}

export function canDeploy(run: ReviewableRun, viewer: Viewer): boolean {
  return viewer.role === 'Reviewer' && run.status === 'APPROVED' && !!run.approval && run.approval.hash === run.files_hash;
}

export function canCreate(viewer: Viewer): boolean {
  return viewer.role === 'Engineer';
}
