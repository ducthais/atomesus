import Link from 'next/link';
import { BookOpenText, TriangleAlert } from 'lucide-react';
import { getCatalog } from '@/lib/catalog/source';
import { LayerBadge, PageHeader, Person } from '../_components/ui';

export default async function GlossaryPage() {
  const cat = await getCatalog();
  const [terms, domains] = [cat.glossary, cat.domains];
  return <>
    <PageHeader kicker="Data Catalog" title="Thuật ngữ nghiệp vụ" icon={<BookOpenText size={22} />}>
      <p>Định nghĩa thực thể của từng domain, do steward sở hữu. Agent dùng các định nghĩa này để phát hiện xung đột ngữ nghĩa trước khi sinh code.</p>
    </PageHeader>
    {domains.map(domain => <section key={domain.id} className="glossary-group" aria-labelledby={`g-${domain.id}`}>
      <div className="group-head"><h2 id={`g-${domain.id}`}><Link href={`/domains/${domain.id}`}>{domain.name}</Link></h2><span className="muted">Steward: <Person name={cat.personName(domain.steward)} /></span></div>
      <div className="term-grid">{terms.filter(t => t.domain === domain.id).map(term => {
        const conflicts = cat.conflictsOf(term.id);
        const used = cat.datasetsForTerm(term.id);
        return <article key={term.id} id={term.id} className="card term-card">
          <div className="card-top"><h3>{term.name}</h3><code className="muted">{term.id}</code>{term.canonical ? <span className="badge tone-good"><span className="dot" />Canonical</span> : <span className="badge tone-muted"><span className="dot" />Không canonical</span>}</div>
          <p>{term.definition}</p>
          {conflicts.length > 0 && <div className="callout warn small"><TriangleAlert size={15} aria-hidden /><span>Trùng tên với {conflicts.map((c, i) => <span key={c.id}>{i > 0 && ', '}<a href={`#${c.id}`}>{c.id}</a> (“{c.definition}”)</span>)}. Chưa có trong <code>conformed_dimensions</code>, nên run gộp hai thực thể này bị chặn ở cổng ngữ nghĩa.</span></div>}
          {term.tables.length > 0 && <><h4>Bảng khai báo</h4><ul className="plain">{term.tables.map(t => { const d = cat.datasetByName(t); return <li key={t}>{d ? <Link href={`/datasets/${d.id}`} className="mono">{t}</Link> : <><code>{t}</code> <span className="muted">chưa có trong catalog</span></>}</li>; })}</ul></>}
          {used.length > 0 && <><h4>Dataset dùng thuật ngữ</h4><ul className="plain">{used.map(d => <li key={d.id}><Link href={`/datasets/${d.id}`} className="dataset-link"><LayerBadge layer={d.layer} /><span className="mono">{d.name}</span></Link></li>)}</ul></>}
        </article>;
      })}</div>
    </section>)}
  </>;
}
