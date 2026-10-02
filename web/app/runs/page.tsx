import Link from 'next/link';
import { Activity, Plus } from 'lucide-react';
import { getCatalog, getDrift } from '@/lib/catalog/source';
import { DriftPanel } from '../_components/drift';
import { RUNNING, STAGES, shortId } from '@/lib/studio';
import type { CatalogRun } from '@/lib/catalog/types';
import { Empty, LayerBadge, PageHeader, Person, StatusBadge, fmtDate, statusTone } from '../_components/ui';

const FILTERS: { id: string; label: string; test: (r: CatalogRun) => boolean }[] = [
  { id: 'all', label: 'Tất cả', test: () => true },
  { id: 'review', label: 'Chờ duyệt', test: r => r.status === 'READY_FOR_REVIEW' },
  { id: 'blocked', label: 'Dừng / bị chặn', test: r => statusTone(r.status) === 'bad' },
  { id: 'active', label: 'Đang chạy', test: r => RUNNING.includes(r.status) },
  { id: 'done', label: 'Đã duyệt', test: r => ['APPROVED', 'DEPLOYED'].includes(r.status) },
];

export default async function RunsPage({ searchParams }: { searchParams: Promise<{ status?: string }> }) {
  const { status } = await searchParams;
  const [cat, drift] = await Promise.all([getCatalog(), getDrift()]);
  const goldIds = Object.fromEntries(cat.datasets.filter(d => d.layer === 'gold').map(d => [`${d.domain}:${d.name.split('.').at(-1)}`, d.id]));
  const [runs, policy] = [cat.runs, cat.policy];
  const filter = FILTERS.find(f => f.id === status) ?? FILTERS[0];
  const shown = runs.filter(filter.test);
  const today = new Date().toDateString();
  const spent = runs.filter(r => new Date(r.updatedAt).toDateString() === today).reduce((sum, r) => sum + r.usd, 0);
  const pct = Math.min(100, spent / policy.limits.max_usd_per_day * 100);

  return <>
    <PageHeader kicker="Data Catalog" title="Hoạt động pipeline" icon={<Activity size={22} />}
      actions={<Link href="/runs/new" className="button primary"><Plus size={15} aria-hidden /> Yêu cầu pipeline</Link>}>
      <p>Mỗi run là một lần agent lập kế hoạch, sinh code và chạy thử để tạo hoặc cập nhật dataset. Đọc từ sổ bằng chứng.</p>
    </PageHeader>
    <div className="budget"><span>Chi phí warehouse hôm nay</span><strong className="mono">${spent.toFixed(4)} / ${policy.limits.max_usd_per_day.toFixed(2)}</strong>
      <div className="meter" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Đã dùng so với trần ngày"><span style={{ width: `${Math.max(pct, 1)}%` }} /></div><small className="muted">Trần per_day.max_usd trong budgets.yml</small></div>
    <DriftPanel drift={drift} goldIds={goldIds} />
    <nav className="segmented" aria-label="Lọc theo trạng thái">{FILTERS.map(f => <Link key={f.id} href={f.id === 'all' ? '/runs' : `/runs?status=${f.id}`} aria-current={f === filter ? 'page' : undefined} scroll={false}>{f.label}<span className="count">{runs.filter(f.test).length}</span></Link>)}</nav>
    {shown.length ? <section className="panel flush"><div className="table-scroll"><table>
      <thead><tr><th>Yêu cầu</th><th>Dataset liên quan</th><th>Trạng thái</th><th>Phase dừng</th><th>Chi phí</th><th>Cập nhật</th></tr></thead>
      <tbody>{shown.map(run => {
        const datasets = cat.datasetsForRun(run);
        return <tr key={run.id}>
          <td><Link href={`/runs/${shortId(run.id)}`}><strong>{run.title}</strong></Link><br /><small className="muted"><span className="mono">{shortId(run.id)}</span> · v{run.version} · <Person name={cat.personName(run.owner)} /></small></td>
          <td>{datasets.length ? <div className="stack-xs">{datasets.slice(0, 3).map(d => <Link key={d.id} href={`/datasets/${d.id}`} className="dataset-link"><LayerBadge layer={d.layer} /><span className="mono">{d.name}</span></Link>)}{datasets.length > 3 && <small className="muted">+{datasets.length - 3} dataset</small>}</div> : <span className="muted">—</span>}</td>
          <td><StatusBadge status={run.status} /></td>
          <td>{STAGES[run.stage]}</td>
          <td className="mono">${run.usd.toFixed(4)}</td>
          <td>{fmtDate(run.updatedAt)}</td>
        </tr>;
      })}</tbody></table></div></section>
      : <Empty title="Không có run ở bộ lọc này"><Link href="/runs">Xem tất cả run</Link></Empty>}
  </>;
}
