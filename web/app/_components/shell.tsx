'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useActionState, useState, useTransition } from 'react';
import { Activity, BookOpenText, Compass, Gauge, Layers3, LogIn, LogOut, MailCheck, Network, Plus, ShieldCheck, UserCog, UserPlus, Waypoints } from 'lucide-react';
import { signIn, signOut, signUp, verifySignUp } from '../actions';
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

export function Sidebar({ reviewCount, live, backend, role }: { reviewCount: number; live: boolean; backend: string; role: Person['role'] }) {
  const path = usePathname();
  const nav = role === 'Admin' ? [...NAV, { href: '/admin/users', label: 'Tài khoản', icon: UserCog, match: (p: string) => p.startsWith('/admin') }] : NAV;
  return <aside className="sidebar">
    <Link href="/" className="brand"><span className="brand-mark"><Layers3 size={18} aria-hidden /></span><span><strong>Atomesus</strong><small>VSF · Pipeline Studio</small></span></Link>
    <nav aria-label="Điều hướng chính">{nav.map(({ href, label, icon: Icon, match }) => <Link key={href} href={href} aria-current={match(path) ? 'page' : undefined}><Icon size={17} aria-hidden />{label}{href === '/runs' && reviewCount > 0 && <span className="nav-count" title="Run chờ duyệt">{reviewCount}</span>}</Link>)}</nav>
    {role === 'Engineer' && <Link href="/runs/new" className="button primary sidebar-new" aria-current={path === '/runs/new' ? 'page' : undefined}><Plus size={16} aria-hidden /> Yêu cầu pipeline</Link>}
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
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [registerEpoch, setRegisterEpoch] = useState(0);
  const [loginState, loginAction, loginPending] = useActionState(signIn, null);
  return <main className="login">
    <div className="login-card">
      <span className="brand-mark"><Layers3 size={20} aria-hidden /></span>
      <h1>Atomesus</h1>
      <p className="muted">Đăng nhập để truy cập Pipeline Studio. Engineer tạo pipeline; Reviewer phê duyệt; Admin quản lý tài khoản.</p>
      <div className="auth-tabs" role="tablist" aria-label="Tài khoản">
        <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>Đăng nhập</button>
        <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>Đăng ký</button>
      </div>
      {mode === 'login' ? <>
        <form action={loginAction} className="login-form">
          <label className="field" htmlFor="login-username">Tên đăng nhập
            <input id="login-username" name="username" autoComplete="username" required autoFocus placeholder="engineer, reviewer hoặc admin" disabled={loginPending} />
          </label>
          <label className="field" htmlFor="login-password">Mật khẩu
            <input id="login-password" name="password" type="password" autoComplete="current-password" required disabled={loginPending} />
          </label>
          <button className="button primary" type="submit" disabled={loginPending}><LogIn size={15} aria-hidden />{loginPending ? 'Đang đăng nhập…' : 'Đăng nhập'}</button>
        </form>
        {loginState?.error && <p className="form-error" role="alert">{loginState.error}</p>}
        <p className="login-help">Tài khoản mẫu: <code>engineer</code>, <code>reviewer</code> hoặc <code>admin</code>.</p>
      </> : <RegisterForm key={registerEpoch} onReset={() => setRegisterEpoch(value => value + 1)} />}
    </div>
  </main>;
}

function RegisterForm({ onReset }: { onReset: () => void }) {
  const [registerState, registerAction, registerPending] = useActionState(signUp, null);
  const [verifyState, verifyAction, verifyPending] = useActionState(verifySignUp, null);
  if (registerState?.email) return <>
    <div className="verification-intro">
      <MailCheck size={22} aria-hidden />
      <div><strong>Kiểm tra email của bạn</strong><p className="muted">Đã gửi mã 6 số tới <code>{registerState.email}</code>. Mã hết hạn sau 10 phút.</p></div>
    </div>
    <form action={verifyAction} className="login-form">
      <input type="hidden" name="email" value={registerState.email} />
      <label className="field" htmlFor="register-code">Mã xác thực
        <input id="register-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" minLength={6} maxLength={6} required autoFocus disabled={verifyPending} placeholder="000000" />
      </label>
      {registerState.developmentCode && <p className="login-help">Development: mã xác thực là <code>{registerState.developmentCode}</code>.</p>}
      <button className="button primary" type="submit" disabled={verifyPending}><MailCheck size={15} aria-hidden />{verifyPending ? 'Đang xác thực…' : 'Xác thực và tạo tài khoản'}</button>
      <button className="button secondary" type="button" disabled={verifyPending} onClick={onReset}>Đổi email hoặc gửi lại mã</button>
    </form>
    {verifyState?.error && <p className="form-error" role="alert">{verifyState.error}</p>}
  </>;
  return <>
        <form action={registerAction} className="login-form">
          <label className="field" htmlFor="register-name">Họ và tên
            <input id="register-name" name="name" autoComplete="name" minLength={2} maxLength={80} required autoFocus disabled={registerPending} />
          </label>
          <label className="field" htmlFor="register-email">Email
            <input id="register-email" name="email" type="email" autoComplete="email" maxLength={254} required disabled={registerPending} />
          </label>
          <label className="field" htmlFor="register-username">Tên đăng nhập
            <input id="register-username" name="username" autoComplete="username" minLength={3} maxLength={32} pattern="[a-z][a-z0-9_.-]+" title="Bắt đầu bằng chữ thường; chỉ dùng chữ thường, số, dấu chấm, gạch dưới hoặc gạch ngang" required disabled={registerPending} />
          </label>
          <label className="field" htmlFor="register-password">Mật khẩu
            <input id="register-password" name="password" type="password" autoComplete="new-password" minLength={8} required disabled={registerPending} />
            <small className="muted">Ít nhất 8 ký tự, gồm chữ hoa, chữ thường và chữ số.</small>
          </label>
          <label className="field" htmlFor="register-confirm">Nhập lại mật khẩu
            <input id="register-confirm" name="confirm_password" type="password" autoComplete="new-password" minLength={8} required disabled={registerPending} />
          </label>
          <button className="button primary" type="submit" disabled={registerPending}><UserPlus size={15} aria-hidden />{registerPending ? 'Đang đăng ký…' : 'Đăng ký'}</button>
        </form>
        {registerState?.error && <p className="form-error" role="alert">{registerState.error}</p>}
        <p className="login-help">Tài khoản mới có quyền Engineer. Quyền Reviewer phải được Admin cấp riêng.</p>
      </>;
}
