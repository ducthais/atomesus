import Link from 'next/link';
import { FileCode2, Gauge, LockKeyhole, ShieldCheck } from 'lucide-react';
import { currentUser, getCatalog, getQuota } from '@/lib/catalog/source';
import { limitLines, type LimitLine } from '@/lib/limits';
import { Empty, PageHeader } from '../_components/ui';

export default async function PolicyPage({ searchParams }: { searchParams: Promise<{ file?: string }> }) {
  const [{ file }, cat, user] = await Promise.all([searchParams, getCatalog(), currentUser()]);
  if (user?.role !== 'Admin') return <UserLimits lines={limitLines(cat.policy.limits, await getQuota())} />;
  const policy = cat.policy;
  const active = policy.files.find(f => f.name === file) ?? policy.files[0];
  return <>
    <PageHeader kicker="Governance" title="Chính sách" icon={<ShieldCheck size={22} />} actions={<span className="badge tone-muted"><LockKeyhole size={12} aria-hidden /> Chỉ đọc</span>}>
      <p>Các file policy/*.yml mà backend áp dụng cho mọi run. Sửa qua pull request; steward sở hữu domains.yml.</p>
    </PageHeader>
    {active ? <div className="policy">
      <nav className="policy-files" aria-label="File chính sách">{policy.files.map(f => <Link key={f.name} href={`/policy?file=${f.name}`} aria-current={f === active ? 'page' : undefined} scroll={false}><FileCode2 size={15} aria-hidden /><span><strong>{f.name}</strong><small>{f.description}</small></span></Link>)}</nav>
      <section className="panel flush"><div className="editor-bar"><span className="mono">policy/{active.name}</span><span className="muted">YAML</span></div>
        <pre className="plain-code">{active.content.trimEnd().split('\n').map((line, i) => <div key={i}><span>{i + 1}</span><code>{line}</code></div>)}</pre></section>
    </div> : <Empty title="Chưa đọc được file chính sách">Backend không trả nội dung file nào.</Empty>}
  </>;
}

/** Engineer / Reviewer: các trần đang áp cho mình, bằng câu thường; không hiện nội dung hay tên file cấu hình. */
function UserLimits({ lines }: { lines: LimitLine[] }) {
  return <>
    <PageHeader kicker="Governance" title="Giới hạn" icon={<Gauge size={22} />}>
      <p>Hệ thống đặt trần chi phí và số lần thử để mỗi người dùng chung tài nguyên một cách công bằng. Chạm trần thì hệ thống dừng run hoặc từ chối tạo run mới cho tới khi hạn mức được đặt lại.</p>
    </PageHeader>
    <section className="panel" aria-labelledby="user-limits">
      <div className="panel-head"><h2 id="user-limits">Giới hạn của bạn</h2></div>
      <ul className="plain">{lines.map(line => <li key={line.id} className="stack-xs">
        <strong>{line.label}</strong>
        <span>{line.text}</span>
        {line.pct !== undefined && <div className="meter" role="meter" aria-valuenow={Math.round(line.pct)} aria-valuemin={0} aria-valuemax={100} aria-label={`${line.label}: đã dùng so với trần`}><span style={{ width: `${Math.max(line.pct, 1)}%` }} /></div>}
      </li>)}</ul>
    </section>
  </>;
}
