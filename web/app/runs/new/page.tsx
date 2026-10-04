import Link from 'next/link';
import { Sparkles, Workflow } from 'lucide-react';
import { currentUser, getCatalog, listDeclaredSources, rememberedConnections } from '@/lib/catalog/source';
import { RequestForm } from '../../_components/run-client';
import { LayerBadge, PageHeader, Panel } from '../../_components/ui';

/** Yêu cầu chạy được với nguồn hiện có (Postgres sales_raw: orders, customers). */
const EXAMPLES = [
  { label: 'Doanh thu ngày', request: 'Tính doanh thu và số đơn hàng theo từng ngày đặt hàng' },
  { label: 'Doanh thu khách hàng', request: 'Tổng doanh thu và số đơn hàng của từng khách hàng, kèm tên khách hàng' },
  { label: 'Nguồn chưa hỗ trợ', request: 'Ghép đơn hàng Postgres với thanh toán từ REST API, tính doanh thu theo ngày' },
];
const STEPS = ['Kết nối nguồn, liệt kê bảng đang có', 'Lập kế hoạch chỉ trên các bảng bạn chọn', 'Đọc schema + 20 dòng mẫu, viết Spec', 'Kiểm tra Spec; sai thì tự sửa (trần trong budgets.yml)', 'Sinh dlt, dbt, Dagster từ template', 'Chạy dlt → dbt trong sandbox, đối soát'];

export default async function NewRequestPage({ searchParams }: { searchParams: Promise<{ dataset?: string; request?: string; parent?: string; tables?: string }> }) {
  const { dataset: datasetId, request, parent: parentId, tables } = await searchParams;
  const [user, cat, sources, connected] = await Promise.all([currentUser(), getCatalog(), listDeclaredSources(), rememberedConnections()]);
  const dataset = datasetId ? cat.dataset(datasetId) : null;
  const parent = parentId ? cat.run(parentId) : null;
  const initial = request ?? (dataset ? `Từ ${dataset.name}: ` : '');
  // Bắt đầu từ một dataset: chọn sẵn các bảng nguồn phía thượng nguồn của nó (form tự kết nối các nguồn đó để kiểm tra bảng còn tồn tại).
  // Sửa & chạy lại: ?tables=nguồn.bảng,... (phạm vi run cũ + bảng kế hoạch cần thêm).
  const initialTables = tables ? tables.split(',').filter(t => /^[\w-]+\.[\w-]+$/.test(t)) : dataset ? cat.sourceTablesOf(dataset.id) : [];
  return <>
    <nav className="breadcrumbs" aria-label="Vị trí"><Link href="/runs">Hoạt động</Link><span>/</span><span>Yêu cầu mới</span></nav>
    <PageHeader icon={<Workflow size={22} />} kicker="Agent" title="Yêu cầu pipeline mới">
      <p>Chọn nguồn dữ liệu rồi mô tả nhu cầu bằng lời. Agent chỉ dùng bảng và cột có thật trong các nguồn đã chọn, sinh code từ template và chạy thử trong sandbox.</p>
    </PageHeader>
    <div className="detail-grid wide-left">
      <RequestForm key={initial} initial={initial} canRun={user?.role === 'Engineer'} examples={EXAMPLES} sources={sources} initialTables={initialTables} connected={connected} parent={parent?.id} />
      <div className="stack">
        {parent && <Panel title="Sửa từ run trước"><Link href={`/runs/${parent.id.slice(0, 8)}`} className="mono">{parent.id.slice(0, 8)}</Link><p className="muted small">“{parent.request}”. Run mới sẽ ghi lại run gốc này; tab <strong>So sánh</strong> của run mới cho thấy khác gì so với bản cũ.</p></Panel>}
        {dataset && <Panel title="Bắt đầu từ dataset"><Link href={`/datasets/${dataset.id}`} className="dataset-link"><LayerBadge layer={dataset.layer} /><span className="mono">{dataset.name}</span></Link><p className="muted small">{dataset.columns.length} cột · {dataset.description}</p></Panel>}
        <Panel title={<><Sparkles size={15} aria-hidden /> Agent sẽ làm gì</>}><ol className="steps">{STEPS.map(s => <li key={s}>{s}</li>)}</ol></Panel>
      </div>
    </div>
  </>;
}
