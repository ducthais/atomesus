'use client';

import { useState, useTransition } from 'react';
import { CircleAlert, ClipboardCheck, LoaderCircle } from 'lucide-react';
import type { AgentPlan } from '@/lib/postgres-api';
import { confirmPlan, type ActionState } from '../actions';

const list = (items: string[] | undefined, empty: string) => items?.length ? items.map(i => <code key={i} className="block-code">{i}</code>) : empty;

/**
 * Cổng P2: agent đã lập kế hoạch, dừng chờ Engineer xác nhận. Chưa xác nhận thì backend không viết Spec,
 * không đọc dữ liệu nguồn (FR-03). Xác nhận chỉ qua nút này, chat không xác nhận thay.
 */
export function PlanConfirm({ runId, plan, canConfirm, reason, retryHref }: { runId: string; plan: AgentPlan; canConfirm: boolean; reason: string; retryHref: string }) {
  const [reviewMapping, setReviewMapping] = useState(true);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();

  return <div className="stack">
    <section className="panel">
      <div className="panel-head"><h2><ClipboardCheck size={16} aria-hidden /> Kế hoạch chờ xác nhận</h2></div>
      {plan.summary && <p>{plan.summary}</p>}
      <dl className="facts">
        <div><dt>Bảng nguồn</dt><dd>{plan.sources.map(s => <span key={s.id} className="block-code"><code>{s.source_ref}.{s.object}</code>{s.purpose && <span className="muted small"> · {s.purpose}</span>}</span>)}</dd></div>
        <div><dt>Cách ghép</dt><dd>{list(plan.joins?.map(j => `${j.left} ${j.type === 'inner' ? 'INNER' : 'LEFT'} JOIN ${j.right}`), 'một bảng, không ghép')}</dd></div>
        <div><dt>Gộp theo</dt><dd>{list(plan.grain, 'một dòng tổng')}</dd></div>
        <div><dt>Chỉ số</dt><dd>{list(plan.metrics?.map(m => m.definition ? `${m.name}: ${m.definition}` : m.name), 'không có')}</dd></div>
        <div><dt>Bộ lọc</dt><dd>{list(plan.filters, 'không có')}</dd></div>
        <div><dt>Bảng kết quả</dt><dd><code>gold.{plan.model_name ?? '—'}</code></dd></div>
        <div><dt>Lịch chạy</dt><dd><code>{plan.schedule ?? '—'}</code></dd></div>
      </dl>
    </section>

    {state?.error && <div className="callout bad" role="alert"><CircleAlert size={16} aria-hidden /><div><strong>Chưa xác nhận được</strong><p>{state.error}</p></div></div>}
    {state?.message && <div className="callout" role="status"><div><p>{state.message}</p></div></div>}
    {canConfirm && <label className="review-toggle"><input type="checkbox" checked={reviewMapping} disabled={pending} onChange={e => setReviewMapping(e.target.checked)} />
      <span><strong>Xem & chỉnh mapping trước khi chạy thử</strong><small className="muted">Agent dừng sau khi viết Spec để bạn kiểm tra cột nguồn → cột đầu ra, kiểu dữ liệu, luật loại dòng</small></span></label>}
    <div className="form-actions">
      <span className="muted small">{canConfirm ? <>Kế hoạch chưa đúng? <a href={retryHref}>Sửa & chạy lại</a> để viết lại yêu cầu hoặc chọn bảng khác.</> : reason}</span>
      <button className="button primary" disabled={!canConfirm || pending} onClick={() => start(async () => setState(await confirmPlan(runId, reviewMapping)))}>
        {pending ? <><LoaderCircle size={15} className="spin" aria-hidden /> Đang gửi…</> : <><ClipboardCheck size={15} aria-hidden /> Xác nhận kế hoạch</>}
      </button>
    </div>
  </div>;
}
