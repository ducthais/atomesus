import Link from 'next/link';
import { CircleAlert, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { Drift, PipelineDrift } from '@/lib/catalog/types';
import { fmtDate } from './ui';

const STATUS: Record<PipelineDrift['status'], [string, string]> = {
  ok: ['Ổn định', 'good'], warning: ['Đổi kiểu', 'wait'], breaking: ['Sẽ lỗi', 'bad'], unknown: ['Không kiểm tra được', 'muted'],
};

export function DriftBadge({ status }: { status: PipelineDrift['status'] }) {
  const [label, tone] = STATUS[status];
  return <span className={`badge tone-${tone}`}><span className="dot" />{label}</span>;
}

/** Giải thích bằng lời các thay đổi schema ảnh hưởng tới một pipeline. */
export function driftLines(p: PipelineDrift): string[] {
  return p.sources.flatMap(s => {
    const where = `${s.sourceRef}.${s.table}`;
    if (s.status === 'unknown') return [`${where}: không kết nối được để kiểm tra (${s.reason ?? 'lỗi không rõ'}).`];
    if (s.missingTable) return [`Bảng ${where} không còn ở nguồn.`];
    return [
      ...s.missingColumns.map(c => `Cột ${where}.${c} không còn (bị xóa hoặc đổi tên) mà pipeline đang đọc.`),
      ...s.typeChanges.map(t => `Cột ${where}.${t.column} đổi kiểu ${t.before} → ${t.after}.`),
    ];
  });
}

/** Khung trên trang Hoạt động: schema nguồn của các pipeline đang chạy có đổi so với lúc duyệt không. */
export function DriftPanel({ drift, goldIds }: { drift: Drift; goldIds: Record<string, string> }) {
  if (!drift.available) return <div className="callout warn small"><CircleAlert size={15} aria-hidden /><span>Không kiểm tra được schema nguồn: {drift.reason}</span></div>;
  const affected = drift.pipelines.filter(p => p.status !== 'ok');
  return <section className={`panel ${affected.some(p => p.status === 'breaking') ? 'drift-bad' : ''}`}>
    <div className="panel-head"><h2>{affected.length ? <TriangleAlert size={16} aria-hidden /> : <ShieldCheck size={16} aria-hidden />} Schema nguồn của pipeline đang chạy</h2>
      <span className="muted small">Kết nối nguồn lúc {fmtDate(drift.checkedAt)} · so với lúc duyệt</span></div>
    {!drift.pipelines.length ? <p className="muted">Chưa có pipeline nào được duyệt.</p> : <ul className="drift-list">{drift.pipelines.map(p => {
      const lines = driftLines(p);
      const gold = goldIds[`${p.domain}:${p.model}`];
      return <li key={p.runId}>
        <div className="drift-head"><DriftBadge status={p.status} />{gold ? <Link href={`/datasets/${gold}`} className="mono">gold.{p.model}</Link> : <code>gold.{p.model}</code>}
          <span className="muted small">run <Link href={`/runs/${p.runId.slice(0, 8)}`} className="mono">{p.runId.slice(0, 8)}</Link>{!p.hasTypeBaseline && ' · chưa có mốc kiểu cột (duyệt trước khi có tính năng này)'}</span></div>
        {lines.length > 0 && <ul className="drift-lines">{lines.map(l => <li key={l}>{l}</li>)}</ul>}
      </li>;
    })}</ul>}
  </section>;
}
