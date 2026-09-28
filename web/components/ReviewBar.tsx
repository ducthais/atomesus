'use client';

import { useState } from 'react';
import { Check, MessageSquareText, Rocket, ShieldCheck, X } from 'lucide-react';
import { api } from '@/lib/api';
import { shortHash } from '@/lib/format';
import type { RunView, Viewer } from '@/lib/types';
import { canApprove, canDeploy, canReject, canRequestChanges } from '@/lib/workflow';

interface Props {
  view: RunView;
  viewer: Viewer;
  onDone: () => void;
  onToast: (msg: string, bad?: boolean) => void;
  onAskChanges: () => void;
}

/** Cổng HITL 2 (P15) và deploy D1 (P16). Backend vẫn là nơi cưỡng chế từng điều kiện. */
export function ReviewBar({ view, viewer, onDone, onToast, onAskChanges }: Props) {
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try { await fn(); onToast(ok); setComment(''); onDone(); } catch (e) { onToast(e instanceof Error ? e.message : String(e), true); } finally { setBusy(false); }
  }

  if (view.status === 'DEPLOYED') {
    return (
      <div className="review ok">
        <ShieldCheck size={16} color="var(--ok)" />
        <span className="grow">Đã deploy mức D1 vào code location sandbox · duyệt bởi <b>{view.approval?.reviewer}</b> · hash <code>{shortHash(view.approval?.hash)}</code>. Nạp lại Dagster để thấy asset.</span>
      </div>
    );
  }

  if (view.status === 'APPROVED') {
    return (
      <div className="review ok">
        <Check size={16} color="var(--ok)" />
        <span className="grow">{view.approval?.reviewer} đã duyệt v{view.version} · hash <code>{shortHash(view.approval?.hash)}</code> khớp files_hash hiện tại.</span>
        <button className="btn primary" disabled={busy || !canDeploy(view, viewer)} title={viewer.role !== 'Reviewer' ? 'Chỉ Reviewer kích hoạt deploy' : undefined}
          onClick={() => act(() => api.deploy(view.run_id, viewer.user), 'Đã ghi 4 file vào code location')}>
          <Rocket size={14} />Deploy D1
        </button>
      </div>
    );
  }

  if (view.status !== 'READY_FOR_REVIEW') return null;

  const own = view.created_by === viewer.user;
  const note = viewer.role !== 'Reviewer'
    ? 'Chuyển sang vai trò Reviewer để quyết định.'
    : own ? 'Bạn là người tạo run này — không được tự duyệt.' : null;

  return (
    <div className="review">
      <ShieldCheck size={16} color="var(--warn)" />
      <span className="grow">
        <b>Chờ duyệt v{view.version}</b> · files_hash <code>{shortHash(view.files_hash)}</code> · tạo bởi {view.created_by}
        {note && <> · <i>{note}</i></>}
      </span>
      <input placeholder="Nhận xét (bắt buộc khi từ chối)" value={comment} onChange={(e) => setComment(e.target.value)} disabled={!canApprove(view, viewer)} />
      <button className="btn primary" disabled={busy || !canApprove(view, viewer)}
        onClick={() => act(() => api.approve(view.run_id, view.files_hash ?? '', viewer.user, comment), `Đã duyệt v${view.version}`)}>
        <Check size={14} />Duyệt
      </button>
      <button className="btn danger" disabled={busy || !canReject(view, viewer) || !comment.trim()} title={!comment.trim() ? 'Từ chối cần lý do' : undefined}
        onClick={() => act(() => api.reject(view.run_id, comment, viewer.user), 'Đã từ chối')}>
        <X size={14} />Từ chối
      </button>
      <button className="btn" disabled={busy || !canRequestChanges(view, viewer)} onClick={onAskChanges}>
        <MessageSquareText size={14} />Yêu cầu sửa
      </button>
    </div>
  );
}
