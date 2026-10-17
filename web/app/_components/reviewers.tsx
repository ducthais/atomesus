'use client';

import Link from 'next/link';
import { useRef, useState, useTransition } from 'react';
import { CircleAlert, Copy, Link2, ListChecks, LoaderCircle, UserCheck, UserMinus, Users } from 'lucide-react';
import { INVITE_DAYS, inviteUrl, reviewerAccount, REVIEW_PATH, type LinkedReviewer } from '@/lib/invite';
import { acceptInvite, createInvite, removeReviewer, type ActionState } from '../actions';
import { Empty, fmtDate } from './ui';

/** Engineer tạo link mời; link chỉ hiện một lần (backend chỉ lưu hash), nên nhắc sao chép ngay. */
export function InviteCreator() {
  const [link, setLink] = useState<{ url: string; expiresAt: string } | null>(null);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const input = useRef<HTMLInputElement>(null);

  const create = () => start(async () => {
    const result = await createInvite();
    if ('error' in result) { setState({ error: result.error }); return; }
    setLink({ url: inviteUrl(window.location.origin, result.data), expiresAt: result.data.expires_at });
    setState(null);
  });
  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setState({ message: 'Đã sao chép link mời.' });
    } catch {
      input.current?.select(); // trang http hoặc trình duyệt chặn clipboard: chọn sẵn để người dùng tự sao chép
      setState({ error: 'Không sao chép tự động được. Link đã được chọn sẵn, hãy nhấn Ctrl+C (hoặc giữ để sao chép).' });
    }
  };

  return <section className="panel">
    <div className="panel-head"><h2><Link2 size={17} aria-hidden />Mời Reviewer</h2></div>
    <p className="muted">Gửi link cho đồng nghiệp: người chưa có tài khoản đăng ký Reviewer ngay trên link; người đã có tài khoản Reviewer đăng nhập rồi chấp nhận.</p>
    <div className="form-actions" style={{ justifyContent: 'flex-start' }}>
      <button type="button" className="button primary" disabled={pending} onClick={create}>
        {pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <Link2 size={15} aria-hidden />}{pending ? 'Đang tạo link…' : link ? 'Tạo link mới' : 'Tạo link mời'}
      </button>
    </div>
    {link && <div className="field" style={{ marginTop: 16 }}>
      <label htmlFor="invite-link">Link mời</label>
      <div className="form-actions" style={{ justifyContent: 'flex-start', marginTop: 0 }}>
        <input id="invite-link" ref={input} className="mono" readOnly value={link.url} onFocus={event => event.currentTarget.select()} style={{ flex: '1 1 240px' }} />
        <button type="button" className="button" onClick={copy}><Copy size={15} aria-hidden />Sao chép</button>
      </div>
      <small className="muted" style={{ fontWeight: 400 }}>Link hết hạn sau {INVITE_DAYS} ngày ({fmtDate(link.expiresAt)}) và chỉ dùng được một lần. Mỗi người nhận cần một link riêng.</small>
    </div>}
    {state?.error && <p className="form-error" role="alert"><CircleAlert size={14} aria-hidden /> {state.error}</p>}
    {state?.message && <p className="success-text" role="status">{state.message}</p>}
  </section>;
}

/** Danh sách Reviewer đã liên kết; gỡ cần xác nhận vì người đó mất quyền duyệt ngay. */
export function ReviewerList({ reviewers }: { reviewers: LinkedReviewer[] }) {
  const [state, setState] = useState<ActionState>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [pending, start] = useTransition();

  const remove = (reviewer: LinkedReviewer) => {
    const name = reviewer.name ?? reviewer.id;
    if (!window.confirm(`Gỡ ${name} khỏi danh sách Reviewer? Người này sẽ không xem và duyệt được run của bạn nữa.`)) return;
    setPendingId(reviewer.id);
    start(async () => {
      setState(await removeReviewer(reviewer.id));
      setPendingId(null);
    });
  };

  if (!reviewers.length) return <Empty icon={<Users size={20} aria-hidden />} title="Chưa có Reviewer nào">
    Engineer không tự duyệt run của mình: mỗi run cần một Reviewer khác xem bằng chứng và duyệt trước khi deploy. Tạo link mời ở trên và gửi cho đồng nghiệp.
  </Empty>;
  return <section className="panel flush">
    <div className="panel-head"><h2><UserCheck size={17} aria-hidden />Reviewer của bạn</h2></div>
    <div className="table-scroll"><table>
      <thead><tr><th>Họ và tên</th><th>Tài khoản</th><th>Liên kết từ</th><th>Quản lý</th></tr></thead>
      <tbody>{reviewers.map(reviewer => <tr key={reviewer.id}>
        <td><strong>{reviewer.name ?? reviewer.id}</strong></td>
        <td><span className={reviewer.username ? 'mono' : 'muted'}>{reviewerAccount(reviewer)}</span></td>
        <td>{reviewer.linked_at ? fmtDate(new Date(reviewer.linked_at * 1000).toISOString()) : <span className="muted">—</span>}</td>
        <td>{reviewer.demo ? <span className="muted small">Cặp demo, không gỡ được</span>
          : <button type="button" className="button danger" disabled={pending} aria-label={`Gỡ Reviewer ${reviewer.name ?? reviewer.id}`} onClick={() => remove(reviewer)}>
            {pending && pendingId === reviewer.id ? <LoaderCircle size={15} className="spin" aria-hidden /> : <UserMinus size={15} aria-hidden />}Gỡ
          </button>}</td>
      </tr>)}</tbody>
    </table></div>
    {(state?.error || state?.message) && <div style={{ padding: '0 20px 16px' }}>
      {state?.error && <p className="form-error" role="alert"><CircleAlert size={14} aria-hidden /> {state.error}</p>}
      {state?.message && <p className="success-text" role="status">{state.message}</p>}
    </div>}
  </section>;
}

/** Reviewer đã đăng nhập bấm chấp nhận; thành công thì dẫn tới trang duyệt run. */
export function InviteAccept({ token }: { token: string }) {
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  if (state?.message) return <div className="callout good"><UserCheck size={16} aria-hidden /><div>
    <strong>{state.message}</strong>
    <p><Link className="button primary" href={REVIEW_PATH}><ListChecks size={15} aria-hidden />Tới trang duyệt run</Link></p>
  </div></div>;
  return <>
    <button type="button" className="button primary" disabled={pending} onClick={() => start(async () => setState(await acceptInvite(token)))}>
      {pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <UserCheck size={15} aria-hidden />}{pending ? 'Đang chấp nhận…' : 'Chấp nhận lời mời'}
    </button>
    {state?.error && <p className="form-error" role="alert"><CircleAlert size={14} aria-hidden /> {state.error}</p>}
  </>;
}
