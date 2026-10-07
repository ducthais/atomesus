'use client';

import { useState, useTransition } from 'react';
import { CircleAlert, LoaderCircle, XCircle } from 'lucide-react';
import { cancelRun, type ActionState } from '../actions';

export function CancelRunButton({ runId }: { runId: string }) {
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const cancel = () => {
    if (!window.confirm('Hủy run này? Bằng chứng đã có sẽ được giữ lại, nhưng agent không chạy phase tiếp theo.')) return;
    start(async () => setState(await cancelRun(runId)));
  };
  return <span className="run-cancel">
    <button type="button" className="button danger" disabled={pending} onClick={cancel}>
      {pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <XCircle size={15} aria-hidden />}
      {pending ? 'Đang hủy…' : 'Hủy run'}
    </button>
    {state?.error && <span className="action-error" role="alert"><CircleAlert size={13} />{state.error}</span>}
  </span>;
}
