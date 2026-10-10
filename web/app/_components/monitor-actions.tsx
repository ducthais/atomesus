'use client';

import { useState, useTransition } from 'react';
import { Check, Gauge, LoaderCircle, RefreshCw } from 'lucide-react';
import { checkPipelines, collectRuns, resolveAlert, type ActionState } from '../actions';

/** Hai thao tác của màn theo dõi: thu số đo (chỉ đọc) và chạy luật suy giảm (có thể tạo run sửa). Chỉ Engineer. */
export function MonitorActions({ canCheck }: { canCheck: boolean }) {
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  return <div className="monitor-actions">
    <button className="button" disabled={pending || !canCheck} onClick={() => start(async () => setState(await collectRuns()))}
      title={canCheck ? 'Đọc các lần chạy theo lịch vừa xong từ Dagster, dbt và nguồn. Không thay đổi gì.' : 'Chỉ Engineer thu số đo.'}>
      {pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <RefreshCw size={15} aria-hidden />} Thu số đo
    </button>
    <button className="button primary" disabled={pending || !canCheck} onClick={() => start(async () => setState(await checkPipelines()))}
      title={canCheck ? 'Chạy 4 luật suy giảm. Luật kích hoạt thì tạo run sửa (chờ người duyệt, không tự deploy) hoặc cảnh báo.' : 'Chỉ Engineer chạy kiểm tra.'}>
      <Gauge size={15} aria-hidden /> Kiểm tra suy giảm
    </button>
    {state && <span className={`small ${state.error ? 'form-error' : 'muted'}`} role="status">{state.error ?? state.message}</span>}
  </div>;
}

export function ResolveAlertButton({ alertId, canResolve }: { alertId: number; canResolve: boolean }) {
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  return <>
    <button className="button small" disabled={pending || !canResolve} onClick={() => start(async () => setState(await resolveAlert(alertId)))}
      title={canResolve ? undefined : 'Chỉ Engineer xử lý cảnh báo.'}>
      {pending ? <LoaderCircle size={14} className="spin" aria-hidden /> : <Check size={14} aria-hidden />} Đã xử lý
    </button>
    {state?.error && <span className="form-error small" role="alert">{state.error}</span>}
  </>;
}
