'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState, useTransition } from 'react';
import { Activity, BookOpenText, Compass, Layers3, LogIn, LogOut, Network, Plus, ShieldCheck, Waypoints } from 'lucide-react';
import { signIn, signOut } from '../actions';
import type { Person } from '@/lib/catalog/types';
import { initials } from './ui';

const NAV = [
  { href: '/', label: 'Khám phá', icon: Compass, match: (p: string) => p === '/' || p.startsWith('/datasets') },
  { href: '/domains', label: 'Domain', icon: Network, match: (p: string) => p.startsWith('/domains') },
  { href: '/relationships', label: 'Quan hệ', icon: Waypoints, match: (p: string) => p.startsWith('/relationships') },
  { href: '/glossary', label: 'Thuật ngữ', icon: BookOpenText, match: (p: string) => p.startsWith('/glossary') },
  { href: '/runs', label: 'Hoạt động', icon: Activity, match: (p: string) => p.startsWith('/runs') && p !== '/runs/new' },
  { href: '/policy', label: 'Chính sách', icon: ShieldCheck, match: (p: string) => p.startsWith('/policy') },
];

export function Sidebar({ reviewCount, live, backend }: { reviewCount: number; live: boolean; backend: string }) {
  const path = usePathname();
  return <aside className="sidebar">
    <Link href="/" className="brand"><span className="brand-mark"><Layers3 size={18} aria-hidden /></span><span><strong>Data Catalog</strong><small>VSF · Pipeline Studio</small></span></Link>
    <nav aria-label="Điều hướng chính">{NAV.map(({ href, label, icon: Icon, match }) => <Link key={href} href={href} aria-current={match(path) ? 'page' : undefined}><Icon size={17} aria-hidden />{label}{href === '/runs' && reviewCount > 0 && <span className="nav-count" title="Run chờ duyệt">{reviewCount}</span>}</Link>)}</nav>
    <Link href="/runs/new" className="button primary sidebar-new" aria-current={path === '/runs/new' ? 'page' : undefined}><Plus size={16} aria-hidden /> Yêu cầu pipeline</Link>
    <div className="sidebar-foot">{live
      ? <><span className="live-tag">Backend thật</span><p>Đọc từ <code>{backend}</code></p></>
      : <><span className="mock-tag">Dữ liệu mock</span><p>CATALOG_MOCK=1: đọc <code>lib/catalog/mock.ts</code>, chưa gọi backend.</p></>}</div>
  </aside>;
}

/** Đổi người dùng: tạo phiên mới ở backend (hoặc cookie mock), rồi tải lại dữ liệu theo quyền mới. */
export function UserSwitch({ user, people }: { user: Person; people: Person[] }) {
  const [pending, start] = useTransition();
  const [error, setError] = useState('');
  return <div className="user-switch">
    <span className="avatar" aria-hidden>{initials(user.name)}</span>
    <label><span className="sr-only">Người dùng</span>
      <select value={user.id} disabled={pending} onChange={e => { const id = e.target.value; start(async () => { setError((await signIn(id))?.error ?? ''); }); }}>
        {people.map(p => <option key={p.id} value={p.id}>{p.name} · {p.role}</option>)}
      </select></label>
    <button className="icon-button" title="Đăng xuất" aria-label="Đăng xuất" disabled={pending} onClick={() => start(() => signOut())}><LogOut size={16} aria-hidden /></button>
    {error && <span className="form-error" role="alert">{error}</span>}
  </div>;
}

export function LoginScreen({ users, error }: { users: Person[]; error?: string }) {
  const [pending, start] = useTransition();
  const [message, setMessage] = useState(error ?? '');
  return <main className="login">
    <div className="login-card">
      <span className="brand-mark"><Layers3 size={20} aria-hidden /></span>
      <h1>Data Catalog</h1>
      <p className="muted">Chọn người dùng demo (bị khóa khi backend chạy APP_ENV=production). Engineer tạo pipeline; Reviewer duyệt.</p>
      <div className="login-users">{users.map(u => <button key={u.id} className="button" disabled={pending} onClick={() => start(async () => setMessage((await signIn(u.id))?.error ?? ''))}>
        <LogIn size={15} aria-hidden /><strong>{u.name}</strong><span className="muted">{u.role}</span></button>)}</div>
      {message && <p className="form-error" role="alert">{message}</p>}
    </div>
  </main>;
}
