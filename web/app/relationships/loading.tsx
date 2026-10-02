import { LoaderCircle, Waypoints } from 'lucide-react';
import { PageHeader } from '../_components/ui';

/** Hiện trong lúc server kết nối tới nguồn để đọc bảng và khóa. */
export default function Loading() {
  return <>
    <PageHeader kicker="Data Catalog" title="Quan hệ giữa các bảng" icon={<Waypoints size={22} />} />
    <div className="callout info" role="status"><LoaderCircle size={16} className="spin" aria-hidden /><span>Đang kết nối tới nguồn và đọc bảng, khóa chính, khóa ngoại…</span></div>
  </>;
}
