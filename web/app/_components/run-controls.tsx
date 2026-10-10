'use client';

import { useState, useTransition } from 'react';
import { CircleAlert, GitPullRequest, LoaderCircle, Sparkles, XCircle } from 'lucide-react';
import { cancelRun, deployRun, optimizePipeline, type ActionState } from '../actions';

export function DeployRunButton({ runId, reason }: { runId: string; reason: string | null }) {
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  return <div className="deploy-controls" aria-busy={pending}>
    <button type="button" className="button primary" disabled={!!reason || pending} aria-describedby={reason ? 'deploy-reason' : undefined}
      onClick={() => start(async () => setState(await deployRun(runId)))}>
      {pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <GitPullRequest size={15} aria-hidden />}
      {pending ? 'Đang mở pull request…' : 'Deploy'}
    </button>
    {reason && <p id="deploy-reason" className="small">{reason}</p>}
    {pending && <p className="small" role="status">Đang gửi Deploy để mở pull request…</p>}
    {state && !pending && <div className={`callout ${state.error ? 'bad' : 'info'}`} role={state.error ? 'alert' : 'status'}>{state.error ?? state.message}</div>}
  </div>;
}

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

export function OptimizeRunButton({ model }: { model: string }) {
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const optimize = () => {
    if (!window.confirm(`Tối ưu pipeline ${model}? Hệ thống sẽ tạo một run mới và chỉ chuyển duyệt khi bản đề xuất tốt hơn.`)) return;
    start(async () => setState(await optimizePipeline(model)));
  };
  return <span className="run-cancel">
    <button type="button" className="button primary" disabled={pending} onClick={optimize}>
      {pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <Sparkles size={15} aria-hidden />}
      {pending ? 'Đang tối ưu…' : 'Tối ưu'}
    </button>
    {state?.error && <span className="action-error" role="alert"><CircleAlert size={13} />{state.error}</span>}
    {state?.message && <span className="action-success" role="status">{state.message}</span>}
  </span>;
}
