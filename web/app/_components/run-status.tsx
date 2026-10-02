import { CalendarClock, CircleAlert, ExternalLink, PlayCircle } from 'lucide-react';
import type { AssetRunStatus, Orchestration } from '@/lib/catalog/types';
import { fmtAgo, fmtDate } from './ui';

const RUN_LABEL: Record<string, [string, string]> = {
  SUCCESS: ['Thành công', 'good'], FAILURE: ['Thất bại', 'bad'], CANCELED: ['Đã hủy', 'muted'], CANCELING: ['Đang hủy', 'wait'],
  STARTED: ['Đang chạy', 'wait'], STARTING: ['Đang khởi động', 'wait'], QUEUED: ['Đang chờ', 'wait'], NOT_STARTED: ['Chưa chạy', 'muted'],
};

export function RunStatusBadge({ status }: { status: string }) {
  const [label, tone] = RUN_LABEL[status] ?? [status, 'muted'];
  return <span className={`badge tone-${tone}`}><span className="dot" />{label}</span>;
}

function duration(start: string | null, end: string | null) {
  if (!start || !end) return null;
  const seconds = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000);
  return seconds < 60 ? `${seconds} giây` : `${Math.floor(seconds / 60)} phút ${seconds % 60} giây`;
}

/** Chip ngắn trên đầu trang dataset: trạng thái lần chạy cuối trên Dagster. */
export function DagsterChip({ status }: { status: AssetRunStatus | undefined }) {
  if (!status?.lastRun) return null;
  return <span className="meta-tag"><PlayCircle size={13} aria-hidden />Dagster: <RunStatusBadge status={status.lastRun.status} /> {fmtAgo(status.lastRun.endedAt ?? status.lastRun.startedAt)}</span>;
}

/** Panel "Vận hành": lần chạy cuối, dữ liệu cập nhật lúc nào, lịch và lần chạy kế tiếp, đọc từ Dagster. */
export function OperationsPanel({ orchestration, assetKey, status }: { orchestration: Orchestration; assetKey: string; status: AssetRunStatus | undefined }) {
  const link = (path: string, label: string) => orchestration.url
    ? <a href={`${orchestration.url}${path}`} target="_blank" rel="noreferrer" className="link-quiet ext-link">{label}<ExternalLink size={12} aria-hidden /></a> : null;
  return <section className="panel ops">
    <div className="panel-head"><h2><CalendarClock size={16} aria-hidden /> Vận hành · Dagster</h2>{orchestration.available && status && link(`/assets/${assetKey}`, 'Mở trong Dagster')}</div>
    {!orchestration.available ? <div className="callout warn small"><CircleAlert size={15} aria-hidden /><span>{orchestration.reason ?? 'Không đọc được trạng thái Dagster.'}</span></div>
      : !status ? <p className="muted">Pipeline của bảng này chưa được deploy lên Dagster. Reviewer duyệt run thì project được ghi vào <code>data/dagster_local/</code> và xuất hiện ở đây.</p>
      : <dl className="facts">
        <div><dt>Lần chạy cuối</dt><dd>{status.lastRun ? <span className="ops-run"><RunStatusBadge status={status.lastRun.status} />
          <span>{fmtDate(status.lastRun.startedAt)} <span className="muted">({fmtAgo(status.lastRun.startedAt)}{duration(status.lastRun.startedAt, status.lastRun.endedAt) ? ` · chạy ${duration(status.lastRun.startedAt, status.lastRun.endedAt)}` : ''})</span></span>
          {link(`/runs/${status.lastRun.runId}`, status.lastRun.runId.slice(0, 8))}</span> : <span className="muted">Chưa chạy lần nào trên Dagster</span>}</dd></div>
        <div><dt>Dữ liệu cập nhật</dt><dd>{status.lastMaterializedAt ? `${fmtDate(status.lastMaterializedAt)} (${fmtAgo(status.lastMaterializedAt)})` : <span className="muted">Chưa materialize</span>}</dd></div>
        <div><dt>Lịch chạy</dt><dd>{status.schedule ? <span className="ops-run"><code>{status.schedule.cron}</code><span className="muted">{status.schedule.timezone}</span>
          {status.schedule.running ? <span className="badge tone-good"><span className="dot" />Đang bật</span> : <span className="badge tone-muted"><span className="dot" />Đang tắt</span>}</span> : <span className="muted">Không có lịch</span>}</dd></div>
        <div><dt>Lần chạy kế tiếp</dt><dd>{status.schedule?.nextTickAt ? `${fmtDate(status.schedule.nextTickAt)}`
          : status.schedule ? <span className="muted">Lịch đang tắt, bật trong {link('/automation', 'Dagster UI')}</span> : '—'}</dd></div>
      </dl>}
  </section>;
}
