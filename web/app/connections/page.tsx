import { Database } from 'lucide-react';
import { connection } from 'next/server';
import { MOCK, currentUser } from '@/lib/catalog/source';
import { getUploadLimits, listUserConnections, listUserUploads } from '../actions';
import { UploadPanel } from './upload-panel';
import { ConnectionForm, ConnectionList } from '../_components/connection-form';
import { PageHeader } from '../_components/ui';

export default async function ConnectionsPage() {
  await connection();
  const user = await currentUser();
  if (!user) return null;
  const engineer = user.role === 'Engineer';
  const result = await listUserConnections();
  const [uploads, limits] = engineer ? await Promise.all([listUserUploads(), getUploadLimits()]) : [null, null];
  const connectionList = 'error' in result
    ? <p className="callout bad" role="alert">{result.error}</p>
    : <ConnectionList connections={result.data} engineer={engineer} />;
  const egressIp = process.env.PIPELINE_EGRESS_IP?.trim() || process.env.NEXT_PUBLIC_EGRESS_IP?.trim() || undefined;

  return <div className="user-connections">
    <PageHeader kicker={user.role} title="Nguồn dữ liệu" icon={<Database size={22} aria-hidden />}>
      <p>{engineer ? 'Tải file CSV/XLSX hoặc kết nối Postgres. Chọn các bảng để tạo pipeline.' : 'Danh sách tên kết nối chỉ đọc. Chỉ Engineer được quản lý kết nối và chọn bảng.'}</p>
    </PageHeader>
    {engineer && uploads && limits ? <div className="source-import-grid">
      <UploadPanel initialUploads={uploads} limits={limits} mock={MOCK} />
      <div className="source-import-saved">{connectionList}</div>
      <div className="source-import-create"><ConnectionForm egressIp={egressIp} /></div>
    </div> : connectionList}
  </div>;
}
