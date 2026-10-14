import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowRight, CircleAlert, KeyRound, Table2, Workflow } from 'lucide-react';
import { LAYER_LABEL, PLATFORM_LABEL } from '@/lib/catalog/query';
import { assetKey, currentUser, getCatalog, getDrift, getOrchestration } from '@/lib/catalog/source';
import { DriftBadge, driftLines } from '../../_components/drift';
import { ReconcileButton } from '../../_components/reconcile-button';
import type { Dataset } from '@/lib/catalog/types';
import { shortId } from '@/lib/studio';
import { Lineage } from '../../_components/lineage';
import { ErDiagram, ErLegend, RelationshipTable } from '../../_components/er-diagram';
import { CleanForm } from '../../_components/clean-form';
import { DagsterChip, OperationsPanel } from '../../_components/run-status';
import { Empty, Facts, LayerBadge, PageHeader, Panel, Person, PlatformTag, QualityIcon, QualitySummary, StatusBadge, Tabs, fmtAgo, fmtDate, fmtNum } from '../../_components/ui';

function DatasetLink({ d }: { d: Dataset }) {
  return <Link href={`/datasets/${d.id}`} className="dataset-link"><LayerBadge layer={d.layer} /><span className="mono">{d.name}</span></Link>;
}

function SampleTable({ rows }: { rows: Record<string, string | null>[] }) {
  const columns = Object.keys(rows[0]);
  return <div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c} className="mono">{c}</th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{columns.map(c => <td key={c} className="mono">{row[c] ?? <span className="null">NULL</span>}</td>)}</tr>)}</tbody></table></div>;
}

export default async function DatasetPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string }> }) {
  const [{ id }, { tab: rawTab }] = await Promise.all([params, searchParams]);
  const [cat, user, orchestration, drift] = await Promise.all([getCatalog(), currentUser(), getOrchestration(), getDrift()]);
  const dataset = cat.dataset(decodeURIComponent(id));
  if (!dataset || !user) notFound();
  const lineage = cat.lineage(dataset.id);
  const managed = dataset.layer !== 'source';
  const key = assetKey(dataset.name);
  const runStatus = orchestration.assets[key];
  // Pipeline đang chạy bị ảnh hưởng vì schema nguồn đổi: bảng gold của pipeline đó, hoặc bảng nguồn nó đọc.
  const table = dataset.name.split('.').at(-1);
  const drifted = drift.available ? drift.pipelines.filter(p => p.status !== 'ok' && p.domain === dataset.domain && (
    (dataset.layer === 'gold' && p.model === table) || (dataset.layer === 'staging' && table?.startsWith(`stg_${p.model}__`))
    || (dataset.layer === 'source' && p.sources.some(s => s.sourceRef === dataset.sourceRef && s.table === table)))) : [];
  const relations = cat.relationsOf(dataset.id);
  const runs = cat.runsForDataset(dataset);
  const upstream = dataset.upstream.map(u => cat.dataset(u)).filter(d => d !== null);
  const downstream = cat.downstreamOf(dataset.id);
  const termById = (t: string) => cat.term(t);
  // Backend hiện chỉ cho làm sạch sales_raw.orders (RunRequest.table).
  const cleanable = dataset.layer === 'source' && dataset.platform === 'postgres' && !dataset.unavailable && (!cat.live || dataset.name === 'sales_raw.orders');
  const base = `/datasets/${dataset.id}`;
  const tabs = [
    { id: 'overview', label: 'Tổng quan' },
    { id: 'schema', label: 'Schema', count: dataset.columns.length },
    { id: 'lineage', label: 'Lineage', count: lineage.nodes.length - 1 },
    ...(relations.length ? [{ id: 'relations', label: 'Quan hệ', count: relations.length }] : []),
    { id: 'quality', label: 'Chất lượng', count: dataset.quality.length },
    { id: 'runs', label: 'Pipeline & run', count: runs.length },
    ...(dataset.sample.length ? [{ id: 'sample', label: 'Dữ liệu mẫu' }] : []),
    ...(cleanable ? [{ id: 'clean', label: 'Làm sạch' }] : []),
  ];
  const tab = tabs.some(t => t.id === rawTab) ? rawTab! : 'overview';
  const latest = runs[0];

  return <>
    <nav className="breadcrumbs" aria-label="Vị trí"><Link href="/">Khám phá</Link><span>/</span><Link href={`/domains/${dataset.domain}`}>{dataset.domain}</Link><span>/</span><Link href={`/?layer=${dataset.layer}&domain=${dataset.domain}`}>{LAYER_LABEL[dataset.layer]}</Link></nav>
    <PageHeader icon={<Table2 size={22} />} kicker={<><LayerBadge layer={dataset.layer} /><PlatformTag platform={dataset.platform} /><QualitySummary checks={dataset.quality} />{managed && <DagsterChip status={runStatus} />}</>}
      title={<span className="mono">{dataset.name}</span>}
      actions={<>
        {dataset.layer === 'gold' && table && <Link className="button" href={`/results/${encodeURIComponent(table)}`}><Table2 size={15} aria-hidden /> Xem kết quả</Link>}
        <Link className="button" href={`/runs/new?dataset=${dataset.id}`}><Workflow size={15} aria-hidden /> Yêu cầu pipeline từ đây</Link>
      </>}>
      <p>{dataset.description}</p>
    </PageHeader>
    {dataset.unavailable && <div className="callout warn"><CircleAlert size={16} aria-hidden /><span>{dataset.unavailable} Dataset vẫn có trong catalog vì được khai báo trong <Link href="/policy">domains.yml</Link>.</span></div>}

    {drifted.map(p => <div key={p.runId} className={`callout ${p.status === 'breaking' ? 'bad' : 'warn'}`} role="alert"><CircleAlert size={16} aria-hidden />
      <div><strong>Schema nguồn đổi · pipeline gold.{p.model}</strong> <DriftBadge status={p.status} /><ul className="drift-lines">{driftLines(p).map(l => <li key={l}>{l}</li>)}</ul>
        <Link href="/runs" className="small">Xem tất cả ở trang Hoạt động →</Link></div></div>)}
    <Tabs base={base} active={tab} tabs={tabs} />

    {tab === 'overview' && <><div className="detail-grid">
      <div className="stack">
        <Panel title="Thông tin">
          <Facts items={[
            ['Owner', <Person key="o" name={cat.personName(dataset.owner)} />],
            ['Domain', <Link key="d" href={`/domains/${dataset.domain}`}>{dataset.domain}</Link>],
            ['Lớp', LAYER_LABEL[dataset.layer]],
            ['Nền tảng', PLATFORM_LABEL[dataset.platform]],
            ...(dataset.sourceRef ? [['Nguồn khai báo', <code key="s">{dataset.sourceRef}</code>] as [string, React.ReactNode]] : []),
            ['Số dòng', fmtNum(dataset.rowCount)],
            ['Cập nhật', dataset.updatedAt ? `${fmtDate(dataset.updatedAt)} (${fmtAgo(dataset.updatedAt)})` : 'Chưa nạp'],
            ['Lịch chạy', dataset.schedule ? <code key="c">{dataset.schedule}</code> : '—'],
          ]} />
        </Panel>
        <Panel title="Thuật ngữ nghiệp vụ">
          {dataset.terms.length ? <ul className="term-list">{dataset.terms.map(t => { const term = termById(t); return term && <li key={t}><Link href={`/glossary#${t}`}><strong>{term.name}</strong><span className="muted">{cat.domainName(term.domain)}</span></Link><p>{term.definition}</p></li>; })}</ul> : <p className="muted">Chưa gắn thuật ngữ.</p>}
          {dataset.tags.length > 0 && <div className="tag-row">{dataset.tags.map(t => <Link key={t} href={`/?tag=${encodeURIComponent(t)}`} className="tag">{t}</Link>)}</div>}
        </Panel>
      </div>
      <div className="stack">
        {managed && <OperationsPanel orchestration={orchestration} assetKey={key} status={runStatus} />}
        <Panel title="Lineage trực tiếp" actions={<Link href={`${base}?tab=lineage`} className="link-quiet">Xem sơ đồ →</Link>}>
          <div className="flow">
            <div><h3>Thượng nguồn</h3>{upstream.length ? upstream.map(d => <DatasetLink key={d.id} d={d} />) : <p className="muted">Không có, đây là dữ liệu gốc.</p>}</div>
            <ArrowRight size={16} className="flow-arrow" aria-hidden />
            <div><h3>Hạ nguồn</h3>{downstream.length ? downstream.map(d => <DatasetLink key={d.id} d={d} />) : <p className="muted">Chưa có dataset nào dùng.</p>}</div>
          </div>
        </Panel>
        <Panel title="Run gần nhất">
          {latest ? <Link href={`/runs/${shortId(latest.id)}`} className="run-card"><StatusBadge status={latest.status} /><strong>{latest.title}</strong><small>{shortId(latest.id)} · v{latest.version} · <Person name={cat.personName(latest.owner)} /> · {fmtAgo(latest.updatedAt)}</small></Link> : <p className="muted">Chưa có run nào chạm tới dataset này.</p>}
        </Panel>
      </div>
    </div>
    {managed && dataset.layer === 'gold' && table && <Panel title="Đối chiếu với nguồn"><ReconcileButton domain={dataset.domain} model={table} canRun={user.role === 'Engineer'} /></Panel>}
    </>}

    {tab === 'schema' && (dataset.columns.length ? <Panel flush><div className="table-scroll"><table>
      <thead><tr><th>#</th><th>Cột</th><th>Kiểu</th><th>Mô tả</th><th>Thuật ngữ</th><th>Tag</th></tr></thead>
      <tbody>{dataset.columns.map((c, i) => <tr key={c.name}>
        <td className="muted mono">{i + 1}</td>
        <td><span className="col-name mono">{c.primaryKey && <KeyRound size={12} aria-label="Khóa chính" />}{c.name}</span>{c.nullable === false && <small className="muted"> NOT NULL</small>}</td>
        <td className="mono">{c.type}</td>
        <td>{c.description ?? <span className="muted">—</span>}</td>
        <td>{c.terms?.map(t => <Link key={t} href={`/glossary#${t}`} className="tag term">{termById(t)?.name ?? t}</Link>)}</td>
        <td>{c.tags?.map(t => <span key={t} className={`tag ${t === 'PII' ? 'pii' : ''}`}>{t}</span>)}</td>
      </tr>)}</tbody></table></div></Panel>
      : <Empty icon={<Table2 size={22} />} title="Chưa có schema">Nguồn này chưa đọc được nên agent chưa lấy được danh sách cột.</Empty>)}

    {tab === 'relations' && <>
      <Panel title={dataset.layer === 'gold' ? 'Phép join dựng nên bảng này' : 'Bảng liên quan'} actions={<Link href={`/relationships?domain=${dataset.domain}`} className="link-quiet">Toàn bộ quan hệ của domain →</Link>}>
        <ErLegend />
        <ErDiagram tables={cat.involvedTables(relations)} relationships={relations} focus={dataset.id} />
      </Panel>
      <Panel title="Danh sách quan hệ" flush><RelationshipTable cat={cat} relationships={relations} /></Panel>
    </>}

    {tab === 'lineage' && <Panel title="Sơ đồ lineage" actions={<span className="muted">Bấm vào một dataset để mở</span>}><Lineage graph={lineage} /></Panel>}

    {tab === 'quality' && (dataset.quality.length ? <Panel flush><div className="table-scroll"><table>
      <thead><tr><th>Kết quả</th><th>Kiểm tra</th><th>Giá trị đo</th><th>Chi tiết</th><th>Lúc</th><th>Run</th></tr></thead>
      <tbody>{dataset.quality.map(q => <tr key={q.id}><td><QualityIcon state={q.state} /></td><td><strong>{q.name}</strong><br /><code className="muted">{q.id}</code></td><td className="mono">{q.value}</td><td>{q.detail}</td><td>{fmtDate(q.at)}</td><td>{q.runId ? <Link href={`/runs/${q.runId}`} className="mono">{q.runId}</Link> : '—'}</td></tr>)}</tbody>
    </table></div></Panel> : <Empty title="Chưa có kiểm tra chất lượng">{dataset.layer === 'source' ? 'Dữ liệu nguồn không có kiểm tra; đối soát chạy ở bronze và staging.' : 'Kiểm tra sinh ra khi pipeline chạy thử.'}</Empty>)}

    {tab === 'runs' && (runs.length ? <Panel flush><div className="table-scroll"><table>
      <thead><tr><th>Run</th><th>Trạng thái</th><th>Version</th><th>Người tạo</th><th>Cập nhật</th></tr></thead>
      <tbody>{runs.map(r => <tr key={r.id}><td><Link href={`/runs/${shortId(r.id)}`}><strong>{r.title}</strong></Link><br /><small className="mono muted">{shortId(r.id)}</small></td><td><StatusBadge status={r.status} /></td><td className="mono">v{r.version}</td><td><Person name={cat.personName(r.owner)} /></td><td>{fmtDate(r.updatedAt)}</td></tr>)}</tbody>
    </table></div></Panel> : <Empty title="Chưa có run nào">Tạo yêu cầu pipeline để agent sinh code từ dataset này.</Empty>)}

    {tab === 'sample' && <Panel title={`Dữ liệu mẫu · ${dataset.sample.length} dòng`} actions={<span className="muted">Sổ bằng chứng lưu tối đa 20 dòng mỗi run; cột PII đã che</span>} flush><SampleTable rows={dataset.sample} /></Panel>}

    {tab === 'clean' && <CleanForm dataset={dataset} canRun={user.role === 'Engineer'} />}
  </>;
}
