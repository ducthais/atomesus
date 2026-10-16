import Link from 'next/link';
import { Compass, Search, X } from 'lucide-react';
import { getCatalog } from '@/lib/catalog/source';
import type { SearchQuery } from '@/lib/catalog/types';
import { Empty, LayerBadge, PageHeader, PlatformTag, QualitySummary, fmtAgo, fmtNum } from '../_components/ui';

const KEYS = ['q', 'domain', 'layer', 'platform', 'tag', 'owner'] as const;

function hrefWith(query: SearchQuery, key: keyof SearchQuery, value?: string) {
  const params = new URLSearchParams();
  for (const k of KEYS) { const v = k === key ? value : query[k]; if (v) params.set(k, v); }
  const s = params.toString();
  return s ? `/explore?${s}` : '/explore';
}

export default async function ExplorePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const raw = await searchParams;
  const query: SearchQuery = Object.fromEntries(KEYS.map(k => [k, typeof raw[k] === 'string' ? raw[k] : undefined]));
  const cat = await getCatalog();
  const [result, activity] = [cat.search(query), cat.activity(6)];
  const personName = cat.personName;
  const filtered = KEYS.some(k => query[k]);

  return <>
    <PageHeader kicker="Data Catalog" title="Khám phá dữ liệu" icon={<Compass size={22} />}>
      <p>Mọi dataset từ nguồn đến gold, kèm owner, thuật ngữ, lineage và kết quả kiểm tra chất lượng.</p>
    </PageHeader>


    <div className="explore">
      <section className="results" aria-label="Kết quả">
        <div className="results-head">
          <span><strong>{result.items.length}</strong> / {result.total} dataset{query.q && <> cho “{query.q}”</>}</span>
          {filtered && <span className="chips">{KEYS.filter(k => query[k]).map(k => <Link key={k} href={hrefWith(query, k)} className="chip" scroll={false}>{k === 'q' ? `“${query[k]}”` : k === 'owner' ? personName(query[k]!) : query[k]}<X size={12} aria-label="Bỏ lọc" /></Link>)}<Link href="/explore" className="link-quiet">Xóa tất cả</Link></span>}
        </div>
        {result.items.length ? <ul className="result-list">{result.items.map(d => <li key={d.id}>
          <Link href={`/datasets/${d.id}`} className="result">
            <div className="result-top"><LayerBadge layer={d.layer} /><strong className="mono">{d.name}</strong>{d.unavailable && <span className="badge tone-muted">Chưa đọc được</span>}</div>
            <p>{d.description}</p>
            <div className="result-meta">
              <PlatformTag platform={d.platform} />
              <span className="meta-tag">Domain: {d.domain}</span>
              <span className="meta-tag">Owner: {personName(d.owner)}</span>
              {d.rowCount !== null && <span className="meta-tag">{fmtNum(d.rowCount)} dòng</span>}
              <span className="meta-tag">Cập nhật {fmtAgo(d.updatedAt)}</span>
              <QualitySummary checks={d.quality} />
            </div>
            {d.tags.length > 0 && <div className="tag-row">{d.tags.map(t => <span key={t} className="tag">{t}</span>)}</div>}
          </Link>
        </li>)}</ul> : <Empty icon={<Search size={22} />} title="Không có dataset phù hợp">Thử từ khóa khác, hoặc <Link href="/explore">xóa bộ lọc</Link>.</Empty>}
      </section>

      <aside className="explore-side" aria-label="Hoạt động gần đây">
        <h2>Hoạt động gần đây</h2>
        <ol className="activity">{activity.map(a => <li key={a.id}><Link href={a.href}><span><strong>{personName(a.actor)}</strong> {a.action}</span><small>{a.target}</small><time dateTime={a.at}>{fmtAgo(a.at)}</time></Link></li>)}</ol>
        <Link href="/runs" className="link-quiet">Xem tất cả hoạt động →</Link>
      </aside>
    </div>
  </>;
}
