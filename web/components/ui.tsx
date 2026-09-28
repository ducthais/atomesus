import { STATE_LABEL, STATUS_LABEL, statusTone, type NodeState } from '@/lib/phases';
import type { RunStatus } from '@/lib/types';

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge ${statusTone(status)}`}>{STATUS_LABEL[status]}</span>;
}

/** Màu luôn đi kèm chữ trạng thái (NFR-10). */
export function StateText({ state }: { state: NodeState }) {
  return (
    <span className={`state ${state}`}>
      <i className={`dot ${state}`} />
      {STATE_LABEL[state]}
    </span>
  );
}
