import Link from 'next/link';
import { ArrowRight, GitCompare, Info, Layers, TriangleAlert } from 'lucide-react';
import type { Comparison } from '@/lib/catalog/types';
import { shortId } from '@/lib/studio';
import { FileDiffViewer } from './file-diff';
import { Empty, Panel, fmtDate } from './ui';

const num = (v: number | string | null | undefined) => v === null || v === undefined ? '—' : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: 2 });

/** Những điều Reviewer cần biết trước khi duyệt, rút ra từ so sánh. */
export function impactNotes(c: Extract<Comparison, { available: true }>): string[] {
  const notes: string[] = [];
  if (c.impact.replaces) notes.push(`Duyệt sẽ thay thế bản đang chạy trên Dagster (run ${shortId(c.impact.replaces.runId)}).`);
  for (const s of c.impact.sharedBronze) notes.push(`Pipeline ${s.model} đang chạy dùng chung ${s.bronze.join(', ')}: dữ liệu bronze của nó sẽ bị ghi lại khi bản này chạy.`);
  const { rows, columns } = c.results;
  if (columns.removed.length) notes.push(`Bảng kết quả mất cột ${columns.removed.join(', ')}: nơi đang đọc các cột này sẽ lỗi.`);
  if (rows.before && rows.after !== null && Math.abs(rows.after - rows.before) / rows.before > 0.1) {
    notes.push(`Số dòng kết quả đổi ${num(rows.before)} → ${num(rows.after)} (${rows.after > rows.before ? '+' : ''}${Math.round((rows.after - rows.before) / rows.before * 100)}%).`);
  }
  for (const m of c.results.metrics) if (m.deltaPct !== null && Math.abs(m.deltaPct) >= 0.01) notes.push(`Tổng ${m.name} đổi ${num(m.before)} → ${num(m.after)} (${m.deltaPct > 0 ? '+' : ''}${m.deltaPct.toFixed(2)}%).`);
  return notes;
}

export function RunCompareView({ comparison, runId, versions }: { comparison: Comparison; runId: string; versions: { id: string; label: string }[] }) {
  if (!comparison.available) return <Empty icon={<GitCompare size={22} />} title="Chưa có gì để so sánh">{comparison.reason}</Empty>;
  const c = comparison;
  const notes = impactNotes(c);
  const changed = c.files.filter(f => f.status !== 'same');
  return <div className="stack">
    <div className="callout info compare-head"><GitCompare size={16} aria-hidden />
      <div><strong>So với run <Link href={`/runs/${shortId(c.base.runId)}`} className="mono">{shortId(c.base.runId)}</Link></strong> · {c.base.reason} · {fmtDate(c.base.createdAt)}
        <p className="muted small">“{c.base.userRequest}”</p>
        {versions.length > 1 && <p className="small">So với bản khác: {versions.filter(v => v.id !== c.base.runId).map((v, i) => <span key={v.id}>{i > 0 && ' · '}<Link href={`/runs/${shortId(runId)}?tab=compare&base=${v.id}`} scroll={false}>{v.label}</Link></span>)}</p>}
      </div>
    </div>

    <section className={`panel ${notes.length ? 'impact-warn' : ''}`}>
      <div className="panel-head"><h2><Layers size={16} aria-hidden /> Ảnh hưởng nếu duyệt bản này</h2></div>
      {notes.length ? <ul className="impact-list">{notes.map(n => <li key={n}><TriangleAlert size={14} aria-hidden />{n}</li>)}</ul>
        : <p className="muted">Không thay thế bản nào đang chạy, không dùng chung bảng với pipeline khác, kết quả không đổi đáng kể.</p>}
    </section>

    <Panel title="Thay đổi Spec" actions={<span className="muted small"><code className="diff-old">đỏ</code> bị bỏ · <code className="diff-new">xanh</code> mới thêm · còn lại giữ nguyên</span>} flush>
      {c.spec.length ? <div className="table-scroll"><table><thead><tr><th>Mục</th><th>Bản trước</th><th /><th>Bản này</th></tr></thead>
        <tbody>{c.spec.map(s => <tr key={s.field}><td><strong>{s.field}</strong></td>
          <td>{s.before.length ? s.before.map(v => <code key={v} className={`block-code ${s.after.includes(v) ? '' : 'diff-old'}`}>{v}</code>) : <span className="muted">(không có)</span>}</td>
          <td className="muted"><ArrowRight size={14} aria-hidden /></td>
          <td>{s.after.length ? s.after.map(v => <code key={v} className={`block-code ${s.before.includes(v) ? '' : 'diff-new'}`}>{v}</code>) : <span className="muted">(không có)</span>}</td></tr>)}</tbody></table></div>
        : <p className="muted pad">Spec giống hệt bản trước.</p>}
    </Panel>

    <Panel title="Thay đổi kết quả">
      <div className="compare-stats">
        <div><span>Số dòng</span><strong className="mono">{num(c.results.rows.before)} → {num(c.results.rows.after)}</strong></div>
        <div><span>Cột thêm</span><strong>{c.results.columns.added.length ? c.results.columns.added.join(', ') : '—'}</strong></div>
        <div><span>Cột bớt</span><strong className={c.results.columns.removed.length ? 'bad-text' : ''}>{c.results.columns.removed.length ? c.results.columns.removed.join(', ') : '—'}</strong></div>
        <div><span>Cột đổi kiểu</span><strong>{c.results.columns.typeChanged.length ? c.results.columns.typeChanged.map(t => `${t.name}: ${t.before} → ${t.after}`).join(', ') : '—'}</strong></div>
      </div>
      {c.results.metrics.length > 0 && <div className="table-scroll"><table><thead><tr><th>Chỉ số (tổng cả bảng)</th><th>Bản trước</th><th>Bản này</th><th>Chênh lệch</th></tr></thead>
        <tbody>{c.results.metrics.map(m => <tr key={m.name}><td className="mono">{m.name}</td><td className="mono">{num(m.before)}</td><td className="mono">{num(m.after)}</td>
          <td className={m.deltaPct ? (Math.abs(m.deltaPct) >= 0.01 ? 'bad-text' : '') : 'muted'}>{m.deltaPct === null ? '—' : `${m.deltaPct > 0 ? '+' : ''}${m.deltaPct.toFixed(2)}%`}</td></tr>)}</tbody></table></div>}
      {!c.results.profiled && <p className="muted small"><Info size={12} aria-hidden /> Một trong hai bản chạy trước khi hệ thống lưu hồ sơ cột, nên chưa so được tổng chỉ số; các run mới sẽ có.</p>}
      {c.results.checks.length > 0 && <p className="small">Kiểm tra đổi kết quả: {c.results.checks.map(k => `${k.id.replace(/:$/, '')} ${k.before ?? '—'} → ${k.after ?? '—'}`).join(' · ')}</p>}
    </Panel>

    <Panel title={`Thay đổi code · ${changed.length} file`} flush>
      {changed.length ? <FileDiffViewer files={changed} /> : <p className="muted pad">Code giống hệt bản trước ({c.files.length} file).</p>}
    </Panel>
  </div>;
}
