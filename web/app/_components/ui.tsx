import Link from 'next/link';
import { Check, CircleAlert, Clock3, Database, FileText, Globe, Server } from 'lucide-react';
import { LAYER_LABEL, PLATFORM_LABEL } from '@/lib/catalog/query';
import type { Layer, Platform, QualityCheck, QualityState } from '@/lib/catalog/types';
import { RUNNING, STATUS_LABEL, type Status } from '@/lib/studio';

const DATE = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Asia/Ho_Chi_Minh' });
export const fmtDate = (iso: string | null) => iso ? DATE.format(new Date(iso)) : '—';
export const fmtNum = (n: number | null) => n === null ? '—' : n.toLocaleString('vi-VN');
export function fmtAgo(iso: string | null, now = Date.now()) {
  if (!iso) return 'chưa nạp';
  const min = Math.round((now - new Date(iso).getTime()) / 60000);
  if (min < 0) return fmtDate(iso);
  if (min < 1) return 'vừa xong';
  if (min < 60) return `${min} phút trước`;
  if (min < 1440) return `${Math.round(min / 60)} giờ trước`;
  return `${Math.round(min / 1440)} ngày trước`;
}

export function LayerBadge({ layer }: { layer: Layer }) {
  return <span className={`badge layer-${layer}`}><span className="dot" />{LAYER_LABEL[layer]}</span>;
}

const PLATFORM_ICON: Record<Platform, typeof Database> = { postgres: Database, mongodb: Database, duckdb: Database, rest_api: Globe, filesystem: FileText };
export function PlatformTag({ platform }: { platform: Platform }) {
  const Icon = PLATFORM_ICON[platform] ?? Server;
  return <span className="meta-tag"><Icon size={13} aria-hidden />{PLATFORM_LABEL[platform] ?? platform}</span>;
}

export const statusTone = (status: Status) => status.startsWith('BLOCKED') || ['NEEDS_HUMAN', 'REJECTED', 'CANCELLED', 'INFRA_ERROR', 'INTERNAL_ERROR', 'DEPLOY_FAILED'].includes(status)
  ? 'bad' : [...RUNNING, 'READY_FOR_REVIEW'].includes(status) ? 'wait' : 'good';
export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge tone-${statusTone(status)}`}><span className="dot" />{STATUS_LABEL[status]}</span>;
}

const QUALITY_TEXT: Record<QualityState, string> = { pass: 'Đạt', fail: 'Không đạt', unmeasured: 'Chưa đo' };
export function QualityIcon({ state, size = 14 }: { state: QualityState; size?: number }) {
  const Icon = state === 'pass' ? Check : state === 'fail' ? CircleAlert : Clock3;
  return <span className={`q-icon q-${state}`} aria-label={QUALITY_TEXT[state]}><Icon size={size} aria-hidden /></span>;
}
/** Tóm tắt chất lượng của một dataset: đếm đạt / tổng, luôn kèm chữ. */
export function QualitySummary({ checks }: { checks: QualityCheck[] }) {
  if (!checks.length) return <span className="quality-sum muted">Chưa có kiểm tra</span>;
  const failed = checks.filter(c => c.state === 'fail').length;
  const passed = checks.filter(c => c.state === 'pass').length;
  const state: QualityState = failed ? 'fail' : passed === checks.length ? 'pass' : 'unmeasured';
  return <span className={`quality-sum q-${state}`}><QualityIcon state={state} size={12} />{failed ? `${failed} lỗi` : `${passed}/${checks.length} đạt`}</span>;
}

/** Chữ cái đầu của tối đa 2 từ cuối, bỏ qua phần như "(steward)". */
export const initials = (name: string) => name.split(' ').filter(w => /^\p{L}/u.test(w)).map(w => w[0]).slice(-2).join('').toUpperCase();

export function Person({ name, withRole }: { name: string; withRole?: string }) {
  return <span className="person"><span className="avatar" aria-hidden>{initials(name)}</span>{name}{withRole && <small>{withRole}</small>}</span>;
}

export function PageHeader({ kicker, title, children, actions, icon }: { kicker?: React.ReactNode; title: React.ReactNode; children?: React.ReactNode; actions?: React.ReactNode; icon?: React.ReactNode }) {
  return <header className="page-head">
    {icon && <div className="page-icon">{icon}</div>}
    <div className="page-head-text">{kicker && <div className="kicker">{kicker}</div>}<h1>{title}</h1>{children}</div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>;
}

/** Tab dạng link (?tab=) để mỗi tab có URL riêng và dùng được nút Back. */
export function Tabs({ base, active, tabs }: { base: string; active: string; tabs: { id: string; label: string; count?: number }[] }) {
  return <nav className="tabs" aria-label="Phần nội dung">{tabs.map((tab, i) => <Link key={tab.id} href={i === 0 ? base : `${base}?tab=${tab.id}`} aria-current={active === tab.id ? 'page' : undefined} scroll={false}>{tab.label}{tab.count !== undefined && <span className="count">{tab.count}</span>}</Link>)}</nav>;
}

export function Empty({ icon, title, children }: { icon?: React.ReactNode; title: string; children?: React.ReactNode }) {
  return <div className="empty">{icon}<strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

export function Panel({ title, actions, children, flush }: { title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; flush?: boolean }) {
  return <section className={`panel ${flush ? 'flush' : ''}`}>{title && <div className="panel-head"><h2>{title}</h2>{actions}</div>}{children}</section>;
}

export function Facts({ items }: { items: [string, React.ReactNode][] }) {
  return <dl className="facts">{items.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}
