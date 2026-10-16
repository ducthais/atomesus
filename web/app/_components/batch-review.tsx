'use client';

import { useState, useTransition } from 'react';
import Link from 'next/link';
import { Check, CircleAlert, LoaderCircle, X } from 'lucide-react';
import { reviewRuns, type BatchReviewResult } from '../actions';

export type BatchRow = {
  id: string; shortId: string; title: string; domain: string; owner: string; version: number; filesHash: string;
  checks: string; usd: string; llmUsd: string; updatedAt: string; eligible: boolean; reason: string | null;
};

/** Reviewer chọn nhiều run chờ duyệt rồi duyệt / từ chối một lượt; mỗi run vẫn kiểm files_hash riêng ở backend. */
export function BatchReview({ rows }: { rows: BatchRow[] }) {
  const eligible = rows.filter(r => r.eligible);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [comment, setComment] = useState('');
  const [result, setResult] = useState<BatchReviewResult | null>(null);
  const [pending, start] = useTransition();
  const toggle = (id: string) => setPicked(prev => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const all = eligible.length > 0 && eligible.every(r => picked.has(r.id));
  const chosen = rows.filter(r => picked.has(r.id));
  const decide = (decision: 'approve' | 'reject') => {
    const verb = decision === 'approve' ? 'Duyệt' : 'Từ chối';
    if (!window.confirm(`${verb} ${chosen.length} run đã chọn? Quyết định sẽ được ghi riêng vào từng run.`)) return;
    start(async () => {
      const response = await reviewRuns(chosen.map(r => ({ runId: r.id, filesHash: r.filesHash })), decision, comment);
      setResult(response);
      if ('results' in response) {
        const failed = new Set(response.results.filter(item => !item.ok).map(item => item.runId));
        setPicked(failed);
        if (!failed.size) setComment('');
      }
    });
  };

  return <div className="stack">
    <section className="panel flush"><div className="table-scroll"><table>
      <thead><tr>
        <th><input type="checkbox" aria-label="Chọn mọi run duyệt được" checked={all} disabled={!eligible.length || pending}
          onChange={() => setPicked(all ? new Set() : new Set(eligible.map(r => r.id)))} /></th>
        <th>Run</th><th>Domain</th><th>Người tạo</th><th>Kiểm tra</th><th>Chi phí</th><th>Cập nhật</th>
      </tr></thead>
      <tbody>{rows.map(r => <tr key={r.id} className={r.eligible ? '' : 'row-muted'}>
        <td><input type="checkbox" aria-label={`Chọn run ${r.shortId}`} checked={picked.has(r.id)} disabled={!r.eligible || pending} onChange={() => toggle(r.id)} /></td>
        <td><Link href={`/runs/${r.shortId}`}><strong>{r.title}</strong></Link><br /><small className="muted mono">{r.shortId} · v{r.version}</small>
          {r.reason && <><br /><small className="form-error">{r.reason}</small></>}</td>
        <td>{r.domain}</td><td>{r.owner}</td><td className="mono">{r.checks}</td>
        <td className="mono small">LLM {r.llmUsd}<br />quét {r.usd}</td><td className="small">{r.updatedAt}</td>
      </tr>)}</tbody>
    </table></div></section>

    <section className="panel">
      <label className="field">Ghi chú (bắt buộc khi từ chối)
        <textarea rows={2} maxLength={500} value={comment} disabled={pending} onChange={e => setComment(e.target.value)} placeholder="Áp dụng cho mọi run đã chọn" />
      </label>
      <div className="form-actions">
        <span className="muted small">Đã chọn {chosen.length}/{eligible.length} run duyệt được. Duyệt chỉ ghi chữ ký; mở từng run và bấm Deploy để đưa lên production.</span>
        <button className="button danger" disabled={!chosen.length || pending || !comment.trim()} onClick={() => decide('reject')}><X size={15} aria-hidden /> Từ chối {chosen.length || ''}</button>
        <button className="button primary" disabled={!chosen.length || pending} onClick={() => decide('approve')}>
          {pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <Check size={15} aria-hidden />} Duyệt {chosen.length || ''}
        </button>
      </div>
      {result && ('error' in result
        ? <div className="callout bad" role="alert"><CircleAlert size={16} aria-hidden /><div><p>{result.error}</p></div></div>
        : <div className={`callout ${result.results.some(item => !item.ok) ? 'warn' : 'good'}`} role="status"><div>
          <strong>{result.results.filter(item => item.ok).length}/{result.results.length} run xử lý thành công</strong>
          {result.results.some(item => !item.ok) && <p>Các run lỗi vẫn được giữ lựa chọn để bạn kiểm tra và thử lại.</p>}
          <ul className="plain small">{result.results.map(r => <li key={r.runId} className={r.ok ? '' : 'form-error'}>
            {r.ok ? <Check size={13} aria-hidden /> : <CircleAlert size={13} aria-hidden />} <Link href={`/runs/${r.runId.slice(0, 8)}`} className="mono">{r.runId.slice(0, 8)}</Link> · {r.message}</li>)}</ul>
        </div></div>)}
    </section>
  </div>;
}
