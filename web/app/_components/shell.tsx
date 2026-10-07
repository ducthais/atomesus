'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useActionState, useTransition } from 'react';
import { Activity, BookOpenText, Compass, Gauge, Layers3, LogIn, LogOut, Network, Plus, ShieldCheck, Waypoints } from 'lucide-react';
import { signIn, signOut } from '../actions';
import type { Person } from '@/lib/catalog/types';
import { initials } from './ui';

const NAV = [
  { href: '/', label: 'Khám phá', icon: Compass, match: (p: string) => p === '/' || p.startsWith('/datasets') },
  { href: '/domains', label: 'Domain', icon: Network, match: (p: string) => p.startsWith('/domains') },
  { href: '/relationships', label: 'Quan hệ', icon: Waypoints, match: (p: string) => p.startsWith('/relationships') },
  { href: '/glossary', label: 'Thuật ngữ', icon: BookOpenText, match: (p: string) => p.startsWith('/glossary') },
  { href: '/runs', label: 'Hoạt động', icon: Activity, match: (p: string) => p.startsWith('/runs') && p !== '/runs/new' },
  { href: '/monitor', label: 'Theo dõi', icon: Gauge, match: (p: string) => p.startsWith('/monitor') },
  { href: '/policy', label: 'Chính sách', icon: ShieldCheck, match: (p: string) => p.startsWith('/policy') },
];

export function Sidebar({ reviewCount, live, backend }: { reviewCount: number; live: boolean; backend: string }) {
  const path = usePathname();
  return <aside className="sidebar">
    <Link href="/" className="brand"><span className="brand-mark"><Layers3 size={18} aria-hidden /></span><span><strong>Atomesus</strong><small>VSF · Pipeline Studio</small></span></Link>
    <nav aria-label="Điều hướng chính">{NAV.map(({ href, label, icon: Icon, match }) => <Link key={href} href={href} aria-current={match(path) ? 'page' : undefined}><Icon size={17} aria-hidden />{label}{href === '/runs' && reviewCount > 0 && <span className="nav-count" title="Run chờ duyệt">{reviewCount}</span>}</Link>)}</nav>
    <Link href="/runs/new" className="button primary sidebar-new" aria-current={path === '/runs/new' ? 'page' : undefined}><Plus size={16} aria-hidden /> Yêu cầu pipeline</Link>
    <div className="sidebar-foot">{live
      ? <><span className="live-tag">Backend thật</span><p>Đọc từ <code>{backend}</code></p></>
      : <><span className="mock-tag">Dữ liệu mock</span><p>CATALOG_MOCK=1: đọc <code>lib/catalog/mock.ts</code>, chưa gọi backend.</p></>}</div>
  </aside>;
}

export function UserMenu({ user }: { user: Person }) {
  const [pending, start] = useTransition();
  return <div className="user-switch">
    <span className="avatar" aria-hidden>{initials(user.name)}</span>
    <span className="user-identity"><strong>{user.name}</strong><small>{user.role}</small></span>
    <button className="icon-button" title="Đăng xuất" aria-label="Đăng xuất" disabled={pending} onClick={() => start(() => signOut())}><LogOut size={16} aria-hidden /></button>
  </div>;
}

export function LoginScreen() {
  const [state, action, pending] = useActionState(signIn, null);
  return <main className="login">
    <div className="login-card">
      <span className="brand-mark"><Layers3 size={20} aria-hidden /></span>
      <h1>Atomesus</h1>
      <p className="muted">Đăng nhập để truy cập Pipeline Studio. Engineer tạo pipeline; Reviewer kiểm tra và phê duyệt.</p>
      <form action={action} className="login-form">
        <label className="field" htmlFor="username">Tên đăng nhập
          <input id="username" name="username" autoComplete="username" required autoFocus placeholder="engineer hoặc reviewer" disabled={pending} />
        </label>
        <label className="field" htmlFor="password">Mật khẩu
          <input id="password" name="password" type="password" autoComplete="current-password" required disabled={pending} />
        </label>
        <button className="button primary" type="submit" disabled={pending}><LogIn size={15} aria-hidden />{pending ? 'Đang đăng nhập…' : 'Đăng nhập'}</button>
      </form>
      {state?.error && <p className="form-error" role="alert">{state.error}</p>}
      <p className="login-help">Tài khoản local: <code>engineer</code> hoặc <code>reviewer</code>.</p>
    </div>
  </main>;
}
