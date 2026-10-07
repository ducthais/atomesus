'use client';

import { useMemo, useState, useTransition } from 'react';
import { CircleAlert, ClipboardCheck, LoaderCircle, Plus, Trash2 } from 'lucide-react';
import type { AgentPlan } from '@/lib/postgres-api';
import { confirmPlan, type ActionState } from '../actions';

const lines = (value: string) => value.split('\n').map(item => item.trim()).filter(Boolean);

/** Cổng P2: Engineer có thể sửa kế hoạch, backend kiểm tra lại bằng catalog thật rồi mới viết Spec. */
export function PlanConfirm({ runId, plan, canConfirm, reason, retryHref }: { runId: string; plan: AgentPlan; canConfirm: boolean; reason: string; retryHref: string }) {
  const [edited, setEdited] = useState<AgentPlan>(() => ({
    ...plan,
    sources: plan.sources.map(source => ({ ...source })), joins: (plan.joins ?? []).map(join => ({ ...join })),
    metrics: (plan.metrics ?? []).map(metric => ({ ...metric })), grain: [...(plan.grain ?? [])], filters: [...(plan.filters ?? [])],
    concepts: (plan.concepts ?? []).map(concept => ({ ...concept })),
  }));
  const [reviewMapping, setReviewMapping] = useState(true);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const errors = useMemo(() => {
    const found: string[] = [];
    if (!/^[a-z][a-z0-9_]{0,62}$/.test(edited.model_name ?? '')) found.push('Tên bảng kết quả phải là snake_case.');
    if (!edited.sources.length || edited.sources.some(source => !source.object.trim())) found.push('Mỗi nguồn phải có tên bảng/đối tượng.');
    if ((edited.metrics ?? []).some(metric => !metric.name.trim())) found.push('Tên chỉ số không được để trống.');
    if (!(edited.schedule ?? '').trim()) found.push('Lịch chạy không được để trống.');
    return found;
  }, [edited]);
  const change = (patch: Partial<AgentPlan>) => { setState(null); setEdited(current => ({ ...current, ...patch })); };

  return <div className="stack">
    <div className="callout info"><CircleAlert size={16} aria-hidden /><span>Bạn có thể sửa kế hoạch ngay tại đây. Backend sẽ kiểm tra lại nguồn, bảng và cột join trước khi agent viết Spec.</span></div>
    <section className="panel plan-editor">
      <div className="panel-head"><h2><ClipboardCheck size={16} aria-hidden /> Kế hoạch chờ xác nhận</h2><span className="muted small">Chưa đọc dữ liệu và chưa sinh code</span></div>
      <label className="field">Tóm tắt<textarea rows={2} value={edited.summary ?? ''} disabled={!canConfirm || pending} onChange={event => change({ summary: event.target.value })} /></label>
      <div className="form-grid">
        <label className="field">Bảng kết quả<input value={edited.model_name ?? ''} disabled={!canConfirm || pending} onChange={event => change({ model_name: event.target.value })} /></label>
        <label className="field">Lịch chạy (cron)<input className="mono" value={edited.schedule ?? ''} disabled={!canConfirm || pending} onChange={event => change({ schedule: event.target.value })} /></label>
      </div>

      <h3>Nguồn và bảng</h3>
      <div className="editable-list">{edited.sources.map((source, index) => <div className="editable-row source-row" key={source.id}>
        <code>{source.source_ref}</code><input aria-label={`Bảng nguồn ${source.id}`} value={source.object} disabled={!canConfirm || pending}
          onChange={event => change({ sources: edited.sources.map((item, i) => i === index ? { ...item, object: event.target.value } : item) })} />
        <input aria-label={`Mục đích nguồn ${source.id}`} placeholder="Mục đích" value={source.purpose ?? ''} disabled={!canConfirm || pending}
          onChange={event => change({ sources: edited.sources.map((item, i) => i === index ? { ...item, purpose: event.target.value } : item) })} />
      </div>)}</div>

      <div className="panel-head subhead"><h3>Cách ghép</h3>{canConfirm && <button type="button" className="button small" disabled={pending} onClick={() => change({ joins: [...(edited.joins ?? []), { left: '', right: '', type: 'left' }] })}><Plus size={13} /> Thêm</button>}</div>
      {(edited.joins ?? []).length ? <div className="editable-list">{(edited.joins ?? []).map((join, index) => <div className="editable-row join-row" key={index}>
        <input className="mono" aria-label={`Vế trái join ${index + 1}`} placeholder="orders.customer_id" value={join.left} disabled={!canConfirm || pending}
          onChange={event => change({ joins: edited.joins!.map((item, i) => i === index ? { ...item, left: event.target.value } : item) })} />
        <select aria-label={`Kiểu join ${index + 1}`} value={join.type} disabled={!canConfirm || pending}
          onChange={event => change({ joins: edited.joins!.map((item, i) => i === index ? { ...item, type: event.target.value as 'left' | 'inner' } : item) })}><option value="left">LEFT</option><option value="inner">INNER</option></select>
        <input className="mono" aria-label={`Vế phải join ${index + 1}`} placeholder="customers.customer_id" value={join.right} disabled={!canConfirm || pending}
          onChange={event => change({ joins: edited.joins!.map((item, i) => i === index ? { ...item, right: event.target.value } : item) })} />
        {canConfirm && <button type="button" className="icon-button danger" aria-label={`Xóa join ${index + 1}`} onClick={() => change({ joins: edited.joins!.filter((_, i) => i !== index) })}><Trash2 size={14} /></button>}
      </div>)}</div> : <p className="muted">Một bảng, không ghép.</p>}

      <div className="panel-head subhead"><h3>Chỉ số</h3>{canConfirm && <button type="button" className="button small" disabled={pending} onClick={() => change({ metrics: [...(edited.metrics ?? []), { name: '', definition: '' }] })}><Plus size={13} /> Thêm</button>}</div>
      <div className="editable-list">{(edited.metrics ?? []).map((metric, index) => <div className="editable-row metric-row" key={index}>
        <input className="mono" aria-label={`Tên chỉ số ${index + 1}`} placeholder="revenue" value={metric.name} disabled={!canConfirm || pending}
          onChange={event => change({ metrics: edited.metrics!.map((item, i) => i === index ? { ...item, name: event.target.value } : item) })} />
        <input aria-label={`Định nghĩa chỉ số ${index + 1}`} placeholder="Tổng số tiền" value={metric.definition ?? ''} disabled={!canConfirm || pending}
          onChange={event => change({ metrics: edited.metrics!.map((item, i) => i === index ? { ...item, definition: event.target.value } : item) })} />
        {canConfirm && <button type="button" className="icon-button danger" aria-label={`Xóa chỉ số ${index + 1}`} onClick={() => change({ metrics: edited.metrics!.filter((_, i) => i !== index) })}><Trash2 size={14} /></button>}
      </div>)}</div>

      <div className="form-grid plan-textareas">
        <label className="field">Gộp theo <span className="muted small">mỗi dòng một cột</span><textarea className="mono" rows={3} value={(edited.grain ?? []).join('\n')} disabled={!canConfirm || pending} onChange={event => change({ grain: lines(event.target.value) })} /></label>
        <label className="field">Bộ lọc <span className="muted small">mỗi dòng một điều kiện</span><textarea className="mono" rows={3} value={(edited.filters ?? []).join('\n')} disabled={!canConfirm || pending} onChange={event => change({ filters: lines(event.target.value) })} /></label>
      </div>
    </section>

    {errors.length > 0 && <div className="callout warn" role="alert"><CircleAlert size={16} /><div>{errors.map(error => <p key={error}>{error}</p>)}</div></div>}
    {state?.error && <div className="callout bad" role="alert"><CircleAlert size={16} aria-hidden /><div><strong>Chưa xác nhận được</strong><p>{state.error}</p></div></div>}
    {state?.message && <div className="callout" role="status"><p>{state.message}</p></div>}
    {canConfirm && <label className="review-toggle"><input type="checkbox" checked={reviewMapping} disabled={pending} onChange={event => setReviewMapping(event.target.checked)} />
      <span><strong>Xem & chỉnh mapping trước khi chạy thử</strong><small className="muted">Agent dừng sau khi viết Spec để bạn kiểm tra cột nguồn → cột đầu ra, kiểu dữ liệu, luật loại dòng</small></span></label>}
    <div className="form-actions">
      <span className="muted small">{canConfirm ? <>Muốn đổi toàn bộ yêu cầu hoặc phạm vi bảng? <a href={retryHref}>Tạo run mới</a>.</> : reason}</span>
      <button className="button primary" disabled={!canConfirm || pending || errors.length > 0} onClick={() => start(async () => setState(await confirmPlan(runId, reviewMapping, edited)))}>
        {pending ? <><LoaderCircle size={15} className="spin" aria-hidden /> Đang gửi…</> : <><ClipboardCheck size={15} aria-hidden /> Lưu và xác nhận kế hoạch</>}
      </button>
    </div>
  </div>;
}
