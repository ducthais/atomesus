import Link from 'next/link';
import { FileCode2, LockKeyhole, ShieldCheck } from 'lucide-react';
import { getCatalog } from '@/lib/catalog/source';
import { PageHeader } from '../_components/ui';

export default async function PolicyPage({ searchParams }: { searchParams: Promise<{ file?: string }> }) {
  const [{ file }, cat] = await Promise.all([searchParams, getCatalog()]);
  const policy = cat.policy;
  const active = policy.files.find(f => f.name === file) ?? policy.files[0];
  return <>
    <PageHeader kicker="Governance" title="Chính sách" icon={<ShieldCheck size={22} />} actions={<span className="badge tone-muted"><LockKeyhole size={12} aria-hidden /> Chỉ đọc</span>}>
      <p>Các file policy/*.yml mà backend áp dụng cho mọi run. Sửa qua pull request; steward sở hữu domains.yml.</p>
    </PageHeader>
    <div className="policy">
      <nav className="policy-files" aria-label="File chính sách">{policy.files.map(f => <Link key={f.name} href={`/policy?file=${f.name}`} aria-current={f === active ? 'page' : undefined} scroll={false}><FileCode2 size={15} aria-hidden /><span><strong>{f.name}</strong><small>{f.description}</small></span></Link>)}</nav>
      <section className="panel flush"><div className="editor-bar"><span className="mono">policy/{active.name}</span><span className="muted">YAML</span></div>
        <pre className="plain-code">{active.content.trimEnd().split('\n').map((line, i) => <div key={i}><span>{i + 1}</span><code>{line}</code></div>)}</pre></section>
    </div>
  </>;
}
