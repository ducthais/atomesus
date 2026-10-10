import { notFound } from 'next/navigation';
import { ShieldCheck, Users } from 'lucide-react';
import { currentUser, listAccounts } from '@/lib/catalog/source';
import { AdminUsers } from '../../_components/admin-users';
import { PageHeader } from '../../_components/ui';

export default async function AdminUsersPage() {
  const user = await currentUser();
  if (user?.role !== 'Admin') notFound();
  const users = await listAccounts();
  const registered = users.filter(item => item.managed);

  return <>
    <PageHeader kicker="Admin" title="Quản lý tài khoản" icon={<Users size={22} />}>
      <p>Admin cấp hoặc thu hồi quyền Reviewer cho tài khoản đã đăng ký. Người dùng không thể tự nâng quyền và các tài khoản hệ thống được khóa.</p>
    </PageHeader>
    <div className="callout info"><ShieldCheck size={16} aria-hidden /><div>
      <strong>Nguyên tắc phân quyền</strong>
      <p>Đăng ký mới luôn là Engineer. Chỉ Admin đổi được Engineer ↔ Reviewer; API kiểm tra quyền lại ở mỗi lần cập nhật.</p>
    </div></div>
    <p className="muted section-note">{users.length} tài khoản · {registered.length} tài khoản có thể quản lý · {users.filter(item => item.role === 'Reviewer').length} Reviewer</p>
    <AdminUsers users={users} />
  </>;
}
