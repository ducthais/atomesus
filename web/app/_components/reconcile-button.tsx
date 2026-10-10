'use client';

import { useState, useTransition } from 'react';
import { CircleAlert, LoaderCircle, Scale } from 'lucide-react';
import { reconcileDataset, type ReconcileState } from '../actions';
import { ReconcileCard } from './reconcile';
import { fmtDate } from './ui';

/** Bảng gold Dagster đang giữ: bấm để đối chiếu lại với nguồn ngay lúc này (chỉ Engineer). */
export function ReconcileButton({ domain, model, canRun }: { domain: string; model: string; canRun: boolean }) {
  const [state, setState] = useState<ReconcileState | null>(null);
  const [pending, start] = useTransition();
  return <div className="stack">
    <div className="reconcile-action">
      <p className="muted">Tính lại <code>gold.{model}</code> thẳng trên Postgres nguồn và so từng dòng với bảng Dagster đang giữ.
        {!canRun && ' Chỉ Engineer được đối chiếu.'}</p>
      <button className="button" disabled={pending || !canRun} onClick={() => start(async () => setState(await reconcileDataset(domain, model)))}>
        {pending ? <><LoaderCircle size={15} className="spin" aria-hidden /> Đang đối chiếu…</> : <><Scale size={15} aria-hidden /> Đối chiếu lại</>}
      </button>
    </div>
    {state && !state.available && <div className="callout warn"><CircleAlert size={16} aria-hidden /><span>{state.reason}</span></div>}
    {state?.available && <>
      <p className="muted small">Đối chiếu lúc {fmtDate(state.checkedAt)} · Spec của run <code>{state.runId.slice(0, 8)}</code></p>
      <ReconcileCard r={state.reconcile} subject={`Bảng gold.${model} trong warehouse Dagster`} />
    </>}
  </div>;
}
