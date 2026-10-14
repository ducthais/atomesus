import { notFound } from 'next/navigation';
import { CircleAlert, ShieldCheck, UserCheck } from 'lucide-react';
import { currentUser, listLinkedReviewers } from '@/lib/catalog/source';
import { InviteCreator, ReviewerList } from '../_components/reviewers';
import { PageHeader } from '../_components/ui';

/** Engineer mời Reviewer bằng link và quản lý ai được xem, duyệt run của mình (USER-SCOPE). */
export default async function ReviewersPage() {
  const user = await currentUser();
  if (user?.role !== 'Engineer') notFound();
  const reviewers = await listLinkedReviewers();

  return <>
    <PageHeader kicker="Engineer" title="Reviewer của tôi" icon={<UserCheck size={22} />}>
      <p>Mỗi người chỉ thấy run của mình. Reviewer bạn mời sẽ xem và duyệt được run của bạn; gỡ liên kết thì quyền đó mất ngay.</p>
    </PageHeader>
    <div className="callout info"><ShieldCheck size={16} aria-hidden /><div>
      <strong>Vì sao cần Reviewer</strong>
      <p>Engineer không tự duyệt run của mình. Run chỉ được deploy sau khi một Reviewer khác xem bằng chứng và duyệt.</p>
    </div></div>
    <InviteCreator />
    {'error' in reviewers
      ? <div className="callout bad" role="alert"><CircleAlert size={16} aria-hidden /><div><strong>Không đọc được danh sách Reviewer</strong><p>{reviewers.error}</p></div></div>
      : <ReviewerList reviewers={reviewers} />}
  </>;
}
