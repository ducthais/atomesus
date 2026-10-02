import Link from 'next/link';
import { Network } from 'lucide-react';
import { LAYERS, LAYER_LABEL } from '@/lib/catalog/query';
import { getCatalog } from '@/lib/catalog/source';
import { PageHeader, Person } from '../_components/ui';

export default async function DomainsPage() {
  const cat = await getCatalog();
  return <>
    <PageHeader kicker="Data Catalog" title="Domain" icon={<Network size={22} />}>
      <p>Mỗi domain có một steward, danh sách nguồn được phép đọc và các thực thể nghiệp vụ. Khai báo trong <Link href="/policy">policy/domains.yml</Link>.</p>
    </PageHeader>
    <div className="card-grid">{cat.domains.map(domain => {
      const datasets = cat.domainDatasets(domain.id);
      const terms = cat.glossary.filter(t => t.domain === domain.id);
      return <Link key={domain.id} href={`/domains/${domain.id}`} className="card domain-card">
        <div className="card-top"><h2>{domain.name}</h2><span className="muted mono">{domain.id}</span></div>
        <p>{domain.description}</p>
        <div className="layer-bar" aria-label="Số dataset theo lớp">{LAYERS.map(layer => { const n = datasets.filter(d => d.layer === layer).length; return <span key={layer} className={`layer-${layer} ${n ? '' : 'zero'}`}><strong>{n}</strong>{LAYER_LABEL[layer]}</span>; })}</div>
        <div className="result-meta">
          <span className="meta-tag">Steward: <Person name={cat.personName(domain.steward)} /></span>
          <span className="meta-tag">{domain.sources.filter(s => s.available).length}/{domain.sources.length} nguồn đọc được</span>
          <span className="meta-tag">{terms.length} thuật ngữ</span>
        </div>
      </Link>;
    })}</div>
  </>;
}
