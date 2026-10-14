import Link from 'next/link';
import { CircleAlert, MailPlus } from 'lucide-react';
import { currentUser } from '@/lib/catalog/source';
import { INVITE_DAYS, INVITE_ENGINEER,INVITE_INVALID, INVITE_OTHER_ROLE, inviteView } from '@/lib/invite';
import { InviteAccept } from '../../_components/reviewers';
import { PageHeader } from '../../_components/ui';

/**
 * Link mời Reviewer. Chưa đăng nhập thì layout hiện màn đăng ký Reviewer (LoginScreen nhận ra /invite/<token>);
 * trang này chỉ phục vụ người đã đăng nhập.
 */
export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const user = await currentUser();
  if (!user) return null;
  const { token } = await params;
  const view = inviteView(user.role, token);
  const problem = view === 'invalid' ? INVITE_INVALID : view === 'engineer' ? INVITE_ENGINEER : view === 'other' ? INVITE_OTHER_ROLE : null;

  return <>
    <PageHeader kicker="Lời mời" title="Lời mời làm Reviewer" icon={<MailPlus size={22} />}>
      <p>Chấp nhận để xem và duyệt run pipeline của Engineer đã gửi link này. Link chỉ dùng được một lần và hết hạn sau {INVITE_DAYS} ngày.</p>
    </PageHeader>
    {problem
      ? <div className="callout bad" role="alert"><CircleAlert size={16} aria-hidden /><div>
        <strong>{view === 'invalid' ? 'Link mời không dùng được' : 'Tài khoản này không nhận được lời mời'}</strong>
        <p>{problem}</p>
        <p><Link className="button" href="/">Về trang chủ</Link></p>
      </div></div>
      : <section className="panel">
        <p className="section-note">Bạn đang đăng nhập là <strong>{user.name}</strong> (Reviewer).</p>
        <InviteAccept token={token} />
      </section>}
  </>;
}
