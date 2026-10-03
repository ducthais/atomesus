import { Bot, Timer, UserRound } from 'lucide-react';
import { codeVolume, duration, effort, type TimelineEvent } from '@/lib/catalog/effort';
import { Panel } from './ui';

/** Thời gian & công sức của run: máy chạy bao lâu, người mất bao lâu, và khối lượng code không phải viết tay. */
export function EffortPanel({ timeline, approvedAt, files }: { timeline: TimelineEvent[]; approvedAt: string | null; files: Record<string, string> | null }) {
  const e = effort(timeline, approvedAt);
  if (!e) return null;
  const volume = files ? codeVolume(files) : [];
  const lines = volume.reduce((n, v) => n + v.lines, 0);
  return <Panel title="Thời gian & công sức" actions={<span className="muted small">Đo từ các mốc ghi trong sổ bằng chứng</span>}>
    <div className="effort-totals">
      <div><Timer size={16} aria-hidden /><span>Tổng</span><strong>{duration(e.totalSec)}</strong><small className="muted">{e.approved ? 'từ lúc gửi yêu cầu tới lúc được duyệt' : 'tới giờ (chưa duyệt)'}</small></div>
      <div><Bot size={16} aria-hidden /><span>Máy</span><strong>{duration(e.agentSec)}</strong><small className="muted">agent tự làm</small></div>
      <div><UserRound size={16} aria-hidden /><span>Người</span><strong>{duration(e.humanSec)}</strong><small className="muted">xác nhận Spec và duyệt</small></div>
    </div>
    {e.totalSec > 0 && <div className="effort-bar" aria-hidden>{e.segments.map(s => <span key={s.key} className={s.who} style={{ flexGrow: Math.max(s.sec, e.totalSec * 0.02) }} />)}</div>}
    <ol className="effort-steps">{e.segments.map(s => <li key={s.key} className={s.who}><span>{s.label}</span><strong>{duration(s.sec)}</strong></li>)}</ol>
    {lines > 0 && <p className="muted small">Agent sinh <strong>{volume.reduce((n, v) => n + v.files, 0)} file, {lines.toLocaleString('vi-VN')} dòng code</strong> mà engineer không phải viết tay: {volume.map(v => `${v.kind} ${v.lines} dòng`).join(' · ')}.</p>}
    <p className="muted small">Thời gian của người là thời gian thực giữa hai mốc, gồm cả lúc đọc và chờ. Thời gian làm tay cùng yêu cầu: đo theo <code>docs/time-study.md</code>.</p>
  </Panel>;
}
