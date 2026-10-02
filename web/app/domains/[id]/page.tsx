import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Network } from 'lucide-react';
import { LAYERS, LAYER_LABEL } from '@/lib/catalog/query';
import { getCatalog } from '@/lib/catalog/source';
import { Lineage } from '../../_components/lineage';
import { Empty, Facts, LayerBadge, PageHeader, Panel, Person, PlatformTag, QualitySummary, Tabs, fmtAgo, fmtNum } from '../../_components/ui';

export default async function DomainPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string }> }) {
  const [{ id }, { tab: rawTab }] = await Promise.all([params, searchParams]);
  const cat = await getCatalog();
  const domain = cat.domain(id);
  if (!domain) notFound();
  const datasets = cat.domainDatasets(domain.id);
  const lineage = cat.lineage(undefined, domain.id);
  const terms = cat.glossary.filter(t => t.domain === domain.id);
  const tabs = [
    { id: 'datasets', label: 'Dataset', count: datasets.length },
    { id: 'lineage', label: 'Lineage' },
    { id: 'sources', label: 'Nguồn', count: domain.sources.length },
    { id: 'terms', label: 'Thuật ngữ', count: terms.length },
  ];
  const tab = tabs.some(t => t.id === rawTab) ? rawTab! : 'datasets';
  const base = `/domains/${domain.id}`;

  return <>
    <nav className="breadcrumbs" aria-label="Vị trí"><Link href="/domains">Domain</Link><span>/</span><span>{domain.name}</span></nav>
    <PageHeader icon={<Network size={22} />} kicker="Domain" title={domain.name}
      actions={<Link className="button" href={`/?domain=${domain.id}`}>Tìm trong domain</Link>}>
      <p>{domain.description}</p>
      <Facts items={[['Steward', <Person key="s" name={cat.personName(domain.steward)} withRole="Steward" />], ['Tiền tệ gốc', domain.currency], ['Dataset', String(datasets.length)]]} />
    </PageHeader>
    <Tabs base={base} active={tab} tabs={tabs} />

    {tab === 'datasets' && LAYERS.map(layer => {
      const items = datasets.filter(d => d.layer === layer);
      return items.length > 0 && <Panel key={layer} title={<><LayerBadge layer={layer} /> {items.length} dataset</>} flush>
        <div className="table-scroll"><table><thead><tr><th>Dataset</th><th>Nền tảng</th><th>Owner</th><th>Số dòng</th><th>Cập nhật</th><th>Chất lượng</th></tr></thead>
          <tbody>{items.map(d => <tr key={d.id}><td><Link href={`/datasets/${d.id}`} className="mono"><strong>{d.name}</strong></Link><br /><small className="muted">{d.description}</small></td><td><PlatformTag platform={d.platform} /></td><td><Person name={cat.personName(d.owner)} /></td><td className="mono">{fmtNum(d.rowCount)}</td><td>{fmtAgo(d.updatedAt)}</td><td><QualitySummary checks={d.quality} /></td></tr>)}</tbody></table></div>
      </Panel>;
    })}
    {tab === 'datasets' && !datasets.length && <Empty title="Domain chưa có dataset" />}

    {tab === 'lineage' && <Panel title={`Lineage toàn domain · ${LAYERS.filter(l => datasets.some(d => d.layer === l)).map(l => LAYER_LABEL[l]).join(' → ')}`}><Lineage graph={lineage} /></Panel>}

    {tab === 'sources' && <Panel flush><div className="table-scroll"><table><thead><tr><th>Nguồn</th><th>Loại</th><th>Kết nối</th><th>Vị trí</th><th>Trạng thái</th></tr></thead>
      <tbody>{domain.sources.map(s => <tr key={s.ref}><td><code>{s.ref}</code></td><td><PlatformTag platform={s.kind} /></td><td><code>{s.connection}</code></td><td className="mono">{s.location}</td><td>{s.available ? <span className="badge tone-good"><span className="dot" />Đọc được</span> : <span className="badge tone-muted" title={s.note}><span className="dot" />{s.note}</span>}</td></tr>)}</tbody></table></div></Panel>}

    {tab === 'terms' && <Panel flush><div className="table-scroll"><table><thead><tr><th>Thuật ngữ</th><th>Định nghĩa</th><th>Canonical</th><th>Bảng khai báo</th></tr></thead>
      <tbody>{terms.map(t => <tr key={t.id}><td><Link href={`/glossary#${t.id}`}><strong>{t.name}</strong></Link><br /><code className="muted">{t.id}</code></td><td>{t.definition}</td><td>{t.canonical ? 'Có' : 'Không'}</td><td>{t.tables.map(x => <code key={x} className="block-code">{x}</code>)}</td></tr>)}</tbody></table></div></Panel>}
  </>;
}
