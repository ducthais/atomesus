import Link from 'next/link';
import { BellRing, CircleAlert, Gauge } from 'lucide-react';
import { currentUser, getMonitor } from '@/lib/catalog/source';
import { ALERT_LABEL, findingBadges, newRecordsText, versusBaseline } from '@/lib/monitor';
import { shortId } from '@/lib/studio';
import { MonitorActions, ResolveAlertButton } from '../_components/monitor-actions';
import { Empty, PageHeader, Panel, fmtAgo, fmtDate, fmtNum } from '../_components/ui';

const seconds = (s: number | null | undefined) => s == null ? '—' : `${s.toLocaleString('vi-VN', { maximumFractionDigits: 1 })} giây`;

/** Day 2: pipeline đã deploy, lần chạy theo lịch gần nhất so với lúc deploy, luật suy giảm đang kích hoạt, cảnh báo. */
export default async function MonitorPage() {
  const [found, user] = await Promise.all([getMonitor(), currentUser()]);
  return <>
    <PageHeader kicker="Day 2" title="Theo dõi pipeline" icon={<Gauge size={22} />} actions={<MonitorActions canCheck={user?.role === 'Engineer'} />}>
      <p>Số đo mỗi lần pipeline đã duyệt chạy theo lịch (Dagster), so với đường cơ sở lúc chạy thử. Luật suy giảm đọc
        ngưỡng từ <code>objectives.yml</code>; nhãn ở đây chỉ để xem, không tạo run sửa cho tới khi bấm <strong>Kiểm tra suy giảm</strong>.</p>
    </PageHeader>

    {'error' in found ? <div className="callout bad"><CircleAlert size={16} aria-hidden /><div><strong>Không đọc được số đo</strong><p>{found.error}</p></div></div> : <>
      <Panel title={<><BellRing size={16} aria-hidden /> Cảnh báo đang mở <span className="meta-tag">{found.alerts.length}</span></>}>
        {found.alerts.length === 0 ? <p className="muted">Không có cảnh báo nào cần xử lý.</p>
          : <ul className="plain monitor-alerts">{found.alerts.map(alert => <li key={alert.id}>
            <div><span className={`badge ${alert.kind === 'source_stalled' ? 'tone-amber' : 'tone-bad'}`}>{ALERT_LABEL[alert.kind] ?? alert.kind}</span>{' '}
              <strong className="mono">{alert.model}</strong> <span className="muted small">· {fmtAgo(alert.at)}</span>
              <p>{alert.message}</p>
              {alert.run_id && <Link href={`/runs/${shortId(alert.run_id)}`} className="small">Mở run {shortId(alert.run_id)} →</Link>}</div>
            <ResolveAlertButton alertId={alert.id} />
          </li>)}</ul>}
      </Panel>

      {found.pipelines.length === 0 ? <Empty icon={<Gauge size={20} />} title="Chưa có pipeline nào được deploy">
        Duyệt một run để ghi project ra Dagster local (<code>make dagster</code>), rồi quay lại đây sau lần chạy theo lịch đầu tiên.
      </Empty> : <Panel flush title="Pipeline đã deploy"><div className="table-scroll"><table>
        <thead><tr><th>Pipeline</th><th>Tình trạng</th><th>Lần chạy gần nhất</th><th>Thời gian</th><th>Gold</th><th>Test fail</th><th>Bản ghi mới ở nguồn</th></tr></thead>
        <tbody>{found.pipelines.map(p => {
          const last = p.last;
          const badges = findingBadges(p.findings);
          const change = versusBaseline(last?.duration_sec, p.baseline?.duration_sec);
          return <tr key={p.model}>
            <td><strong className="mono">{p.model}</strong><br /><small className="muted">
              {p.domain ?? '—'}{p.schedule && <> · <code>{p.schedule}</code></>}
              {p.deployed_run_id && <> · <Link href={`/runs/${shortId(p.deployed_run_id)}`}>run {shortId(p.deployed_run_id)}</Link></>}</small></td>
            <td>{badges.length ? badges.map(b => <span key={b.label} className={`badge tone-${b.tone}`} title={b.detail}>{b.label}</span>)
              : last ? <span className="badge tone-good">Ổn định</span> : <span className="badge tone-muted">Chưa chạy theo lịch</span>}</td>
            <td>{last ? <>{fmtAgo(last.ended_at ?? last.started_at)}<br /><small className={last.status === 'SUCCESS' ? 'muted' : 'form-error'}>{last.status} · {fmtDate(last.started_at)}</small></> : '—'}</td>
            <td className="mono">{seconds(last?.duration_sec)}{change && <><br /><small className="muted">{change} so với lúc deploy ({seconds(p.baseline?.duration_sec)})</small></>}</td>
            <td className="mono">{fmtNum(last?.gold_rows ?? null)}</td>
            <td className="mono">{last?.tests == null ? '—' : `${last.tests_failed ?? 0}/${last.tests}`}</td>
            <td className="small">{newRecordsText(last)}</td>
          </tr>;
        })}</tbody>
      </table></div></Panel>}
    </>}
  </>;
}
