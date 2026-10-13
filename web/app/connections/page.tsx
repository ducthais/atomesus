import { Database } from 'lucide-react';
import { currentUser } from '@/lib/catalog/source';
import { listUserConnections } from '../actions';
import { ConnectionForm, ConnectionList } from '../_components/connection-form';
import { PageHeader } from '../_components/ui';

export default async function ConnectionsPage() {
  const user = await currentUser();
  if (!user) return null;
  const engineer = user.role === 'Engineer';
  const result = await listUserConnections();
  return <div className="user-connections">
    <PageHeader kicker={user.role} title="Nguồn dữ liệu" icon={<Database size={22} aria-hidden />}>
      <p>{engineer ? 'Kết nối Postgres của bạn. Kiểm tra quyền đọc, chọn schema và các bảng để tạo pipeline.' : 'Danh sách tên kết nối chỉ đọc. Chỉ Engineer được quản lý kết nối và chọn bảng.'}</p>
    </PageHeader>
    <div className={engineer ? 'detail-grid' : undefined}>
      {'error' in result ? <p className="callout bad" role="alert">{result.error}</p> : <ConnectionList connections={result.data} engineer={engineer} />}
      {engineer && <ConnectionForm egressIp={process.env.NEXT_PUBLIC_EGRESS_IP?.trim() || undefined} />}
    </div>
  </div>;
}
