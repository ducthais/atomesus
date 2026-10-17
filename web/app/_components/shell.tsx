'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useActionState, useRef, useState, useTransition, type ReactNode } from 'react';
import { Activity, ArrowLeft, BookOpenText, CheckCircle2, CircleAlert, Compass, Database, Eye, EyeOff, Gauge, GitMerge, Layers3, LockKeyhole, LogIn, LogOut, MailCheck, Network, Plus, Search, ShieldCheck, Sparkles, SquareTerminal, UserCheck, UserCog, UserPlus, UserRound, Waypoints } from 'lucide-react';
import { signIn, signInDemo, signOut, signUp, verifySignUp } from '../actions';
import type { Person, Role } from '@/lib/catalog/types';
import { INVITE_INVALID, REVIEW_PATH, inviteTokenFromPath, isInviteToken } from '@/lib/invite';
import { initials } from './ui';

const NAV = [
  { group: 'Danh mục dữ liệu', href: '/explore', label: 'Khám phá', icon: Compass, match: (p: string) => p.startsWith('/explore') || p.startsWith('/datasets') },
  { group: 'Danh mục dữ liệu', href: '/domains', label: 'Domain', icon: Network, match: (p: string) => p.startsWith('/domains') },
  { group: 'Danh mục dữ liệu', href: '/relationships', label: 'Quan hệ', icon: Waypoints, match: (p: string) => p.startsWith('/relationships') },
  { group: 'Danh mục dữ liệu', href: '/glossary', label: 'Thuật ngữ', icon: BookOpenText, match: (p: string) => p.startsWith('/glossary') },
  { group: 'Vận hành', href: '/runs', label: 'Hoạt động', icon: Activity, match: (p: string) => p.startsWith('/runs') && p !== '/runs/new' },
  { group: 'Vận hành', href: '/monitor', label: 'Theo dõi', icon: Gauge, match: (p: string) => p.startsWith('/monitor') },
  { group: 'Vận hành', href: '/policy', label: 'Chính sách', icon: ShieldCheck, match: (p: string) => p.startsWith('/policy') },
];

const ROLE_META: Record<Role, { label: string; short: string; description: string; icon: typeof SquareTerminal }> = {
  Engineer: { label: 'Không gian kỹ sư', short: 'Xây dựng', description: 'Tạo, kiểm thử và vận hành pipeline', icon: SquareTerminal },
  Reviewer: { label: 'Không gian kiểm duyệt', short: 'Kiểm duyệt', description: 'Đánh giá bằng chứng và phê duyệt', icon: ShieldCheck },
  Admin: { label: 'Trung tâm quản trị', short: 'Quản trị', description: 'Quản lý tài khoản và quyền truy cập', icon: UserCog },
  Steward: { label: 'Không gian quản trị dữ liệu', short: 'Quản trị dữ liệu', description: 'Theo dõi chất lượng và chính sách', icon: BookOpenText },
};

const sectionForPath = (path: string, role: Role) => {
  if (path.startsWith('/admin')) return 'Quản trị tài khoản';
  if (path.startsWith('/connections')) return 'Nguồn dữ liệu';
  if (path.startsWith('/results')) return 'Kết quả pipeline';
  if (path.startsWith('/reviewers') || path.startsWith('/runs/review')) return 'Kiểm duyệt';
  if (path.startsWith('/runs')) return 'Vòng đời pipeline';
  if (path.startsWith('/monitor')) return 'Vận hành';
  if (path.startsWith('/policy')) return role === 'Admin' ? 'Chính sách' : 'Giới hạn';
  return 'Data Catalog';
};

/**
 * Khung ứng dụng. `/` là landing page công khai (không cần đăng nhập, không có sidebar); mọi trang khác cần phiên,
 * chưa đăng nhập thì hiện màn đăng nhập ngay tại trang đó (đăng nhập xong quay lại đúng trang).
 */
export function AppFrame({ children, user, reviewCount, live, backend, demoLogin }: {
  children: ReactNode; user: Person | null; reviewCount: number; live: boolean; backend: string; demoLogin: boolean;
}) {
  const path = usePathname();
  if (path === '/') return <>{children}</>;
  if (!user) return <LoginScreen demoLogin={demoLogin} />;
  return <>
    <a href="#main" className="skip-link">Bỏ qua điều hướng</a>
    <div className={`shell shell-${user.role.toLowerCase()}`} data-role={user.role}>
      <Sidebar reviewCount={reviewCount} live={live} backend={backend} role={user.role} />
      <div className="main">
        <header className="topbar">
          <div className="topbar-context">
            <span>{ROLE_META[user.role].short}</span>
            <strong>{sectionForPath(path, user.role)}</strong>
          </div>
          <form action="/explore" className="global-search" role="search">
            <Search size={16} aria-hidden />
            <input name="q" type="search" placeholder="Tìm dataset, cột, thuật ngữ, tag…" aria-label="Tìm trong catalog" />
            <kbd>Enter</kbd>
          </form>
          <UserMenu user={user} />
        </header>
        <main id="main" className="content">{children}</main>
      </div>
    </div>
  </>;
}

export function Sidebar({ reviewCount, live, backend, role }: { reviewCount: number; live: boolean; backend: string; role: Person['role'] }) {
  const path = usePathname();
  const meta = ROLE_META[role];
  const RoleIcon = meta.icon;
  // Chỉ Admin xem file chính sách; vai khác vào cùng trang để xem giới hạn của mình.
  const base = role === 'Admin' ? NAV : NAV.map(item => item.href === '/policy' ? { ...item, label: 'Giới hạn' } : item);
  const nav = role === 'Admin' ? [...base, { group: 'Quản trị', href: '/admin/users', label: 'Tài khoản', icon: UserCog, match: (p: string) => p.startsWith('/admin') }]
    : role === 'Engineer' ? [...base, { group: 'Công cụ kỹ sư', href: '/connections', label: 'Nguồn dữ liệu', icon: Database, match: (p: string) => p.startsWith('/connections') },
      { group: 'Công cụ kỹ sư', href: '/reviewers', label: 'Reviewer', icon: UserCheck, match: (p: string) => p.startsWith('/reviewers') }] : base;
  const groups = nav.reduce<{ label: string; items: typeof nav }[]>((result, item) => {
    const current = result.at(-1);
    if (!current || current.label !== item.group) result.push({ label: item.group, items: [item] });
    else current.items.push(item);
    return result;
  }, []);
  return <aside className="sidebar">
    <Link href="/" className="brand"><span className="brand-mark"><Layers3 size={19} aria-hidden /></span><span><strong>Atomesus</strong><small>Pipeline Studio</small></span></Link>
    <div className="sidebar-role">
      <span className="sidebar-role-icon"><RoleIcon size={17} aria-hidden /></span>
      <span><strong>{meta.label}</strong><small>{meta.description}</small></span>
    </div>
    <nav aria-label="Điều hướng chính">{groups.map(group => <div className="nav-group" key={group.label}>
      <span className="nav-label">{group.label}</span>
      {group.items.map(({ href, label, icon: Icon, match }) => <Link key={href} href={href} aria-current={match(path) ? 'page' : undefined}><Icon size={17} aria-hidden />{label}{href === '/runs' && reviewCount > 0 && <span className="nav-count" title="Run chờ duyệt">{reviewCount}</span>}</Link>)}
    </div>)}</nav>
    {role === 'Engineer' && <Link href="/runs/new" className="button primary sidebar-new" aria-current={path === '/runs/new' ? 'page' : undefined}><Plus size={16} aria-hidden /> Yêu cầu pipeline</Link>}
    <div className="sidebar-foot">{live
      ? <><span className="live-tag">Backend thật</span><p>Đọc dữ liệu từ <code>{backend}</code></p></>
      : <><span className="mock-tag"><Sparkles size={11} aria-hidden /> Chế độ dữ liệu mẫu</span><p>Đang dùng catalog nội bộ để trình diễn.</p></>}</div>
  </aside>;
}

export function UserMenu({ user }: { user: Person }) {
  const [pending, start] = useTransition();
  return <div className="user-switch">
    <span className="avatar" aria-hidden>{initials(user.name)}</span>
    <span className="user-identity"><strong>{user.name}</strong><small>{ROLE_META[user.role].label}</small></span>
    <button className="icon-button" title="Đăng xuất" aria-label="Đăng xuất" disabled={pending} onClick={() => start(() => signOut())}><LogOut size={16} aria-hidden /></button>
  </div>;
}

function LoginStory() {
  return <aside className="login-story" aria-label="Giới thiệu Atomesus">
    <Link href="/" className="login-brand" aria-label="Atomesus — về trang giới thiệu">
      <Layers3 size={28} aria-hidden /><span>Atomesus</span>
    </Link>
    <div className="login-story-copy">
      <span className="login-kicker">Pipeline được kiểm chứng</span>
      <h1>Biến yêu cầu dữ liệu thành <em>pipeline được kiểm chứng.</em></h1>
      <p>Lập kế hoạch, sinh mã và kiểm thử trên dữ liệu thực. Mọi thay đổi đều có bằng chứng trước khi được đưa vào vận hành.</p>
      <ul className="login-trust-list">
        <li><CheckCircle2 size={18} aria-hidden /><span><strong>Spec định kiểu rõ ràng</strong><small>Con người xác nhận trước khi sinh mã.</small></span></li>
        <li><GitMerge size={18} aria-hidden /><span><strong>Luồng phê duyệt độc lập</strong><small>Reviewer kiểm tra bằng chứng và tác động.</small></span></li>
        <li><LockKeyhole size={18} aria-hidden /><span><strong>Vận hành có kiểm soát</strong><small>Lưu vết kiểm tra xuyên suốt vòng đời pipeline.</small></span></li>
      </ul>
    </div>
    <div className="login-preview" aria-label="Trạng thái Pipeline Studio">
      <div><span><SquareTerminal size={16} aria-hidden />PIPELINE STUDIO</span></div>
      <p>KẾ HOẠCH → SPEC → SINH MÃ → SANDBOX → DUYỆT</p>
      <small>SOURCE → BRONZE → STAGING → GOLD</small>
    </div>
  </aside>;
}

/**
 * Màn đăng nhập / đăng ký. Hiện cho mọi trang cần phiên khi chưa đăng nhập (trừ landing `/`); đăng nhập xong quay lại đúng
 * trang đó. Trên /invite/<token> thì đăng ký Reviewer theo link mời.
 *
 * `demoLogin` (backend bật PIPELINE_DEMO_LOGIN hoặc development): nút Engineer / Reviewer vào thẳng tài khoản demo, không
 * cần mật khẩu, nên production với mật khẩu ngẫu nhiên vẫn vào được. Tắt thì nút chỉ điền tên đăng nhập.
 */
export function LoginScreen({ demoLogin = false }: { demoLogin?: boolean }) {
  const path = usePathname();
  const inviteToken = inviteTokenFromPath(path);
  const [mode, setMode] = useState<'login' | 'register'>(inviteToken !== null ? 'register' : 'login');
  const [registerEpoch, setRegisterEpoch] = useState(0);
  const [username, setUsername] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [loginState, loginAction, loginPending] = useActionState(signIn, null);
  const [demoError, setDemoError] = useState<string | null>(null);
  const [demoPending, startDemo] = useTransition();
  const busy = loginPending || demoPending;
  if (inviteToken !== null && !isInviteToken(inviteToken)) return <main className="login">
    <div className="login-layout invalid-invite">
      <LoginStory />
      <section className="login-panel">
        <div className="login-card">
          <div className="login-card-heading"><span className="login-kicker">Lời mời Reviewer</span><h2>Lời mời không hợp lệ</h2>
            <p>Liên kết có thể đã hết hạn hoặc được sử dụng trước đó.</p></div>
          <p className="form-error" role="alert"><CircleAlert size={16} aria-hidden /> {INVITE_INVALID}</p>
          <Link className="landing-button primary login-submit" href="/runs">Về trang đăng nhập</Link>
        </div>
        <Link className="login-back" href="/"><ArrowLeft size={15} aria-hidden />Trang giới thiệu</Link>
      </section>
    </div>
  </main>;
  const selectRole = (account: 'engineer' | 'reviewer') => {
    if (demoLogin) {
      setDemoError(null);
      startDemo(async () => { setDemoError((await signInDemo(account, path))?.error ?? null); });
      return;
    }
    setUsername(account);
    passwordRef.current?.focus();
  };
  return <main className="login">
    <div className={`login-layout${mode === 'register' ? ' register-mode' : ''}`}>
    <LoginStory />
    <section className="login-panel">
    <div className="login-card">
      <div className="login-card-heading">
        <span className="login-kicker">{inviteToken ? 'Lời mời Reviewer' : 'Đăng nhập an toàn'}</span>
        <h2>{inviteToken ? 'Lời mời làm Reviewer' : mode === 'login' ? 'Chào mừng trở lại' : 'Tạo tài khoản mới'}</h2>
        <p>{inviteToken
          ? 'Đăng ký Reviewer mới hoặc đăng nhập bằng tài khoản Reviewer có sẵn để chấp nhận lời mời.'
          : mode === 'login' ? 'Đăng nhập để tiếp tục vào Pipeline Studio.' : 'Bắt đầu với quyền Engineer và xây dựng pipeline đầu tiên của bạn.'}</p>
      </div>
      <div className="auth-tabs" role="tablist" aria-label="Tài khoản">
        <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>Đăng nhập</button>
        <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>Đăng ký</button>
      </div>
      {mode === 'login' ? <>
        <form action={loginAction} className="login-form">
          {/* Đăng nhập xong quay lại đúng trang đang mở (landing `/` không cần đăng nhập). */}
          <input type="hidden" name="next" value={path} />
          <label className="field" htmlFor="login-username">Tên đăng nhập
            <input id="login-username" name="username" autoComplete="username" required autoFocus value={username} onChange={event => setUsername(event.target.value)} placeholder="engineer, reviewer hoặc admin" disabled={loginPending} />
          </label>
          <label className="field" htmlFor="login-password">Mật khẩu
            <span className="login-password-wrap">
              <input ref={passwordRef} id="login-password" name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" required disabled={loginPending} />
              <button type="button" className="login-password-toggle" aria-label={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'} title={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'} onClick={() => setShowPassword(value => !value)}>
                {showPassword ? <EyeOff size={17} aria-hidden /> : <Eye size={17} aria-hidden />}
              </button>
            </span>
          </label>
          <button className="landing-button primary login-submit" type="submit" disabled={loginPending}><LogIn size={16} aria-hidden />{loginPending ? 'Đang đăng nhập…' : 'Đăng nhập'}</button>
        </form>
        {loginState?.error && <p className="form-error" role="alert">{loginState.error}</p>}
        <p className="login-help">{inviteToken ? 'Đăng nhập bằng tài khoản Reviewer; bạn sẽ quay lại trang này để chấp nhận lời mời.'
          : <>Tài khoản mẫu: <code>engineer</code>, <code>reviewer</code> hoặc <code>admin</code>.</>}</p>
      </> : <>
        <RegisterForm key={registerEpoch} inviteToken={inviteToken ?? undefined} onReset={() => setRegisterEpoch(value => value + 1)} />
        {inviteToken && <button type="button" className="button" style={{ justifyContent: 'center', whiteSpace: 'normal' }} onClick={() => setMode('login')}>
          <LogIn size={15} aria-hidden />Đã có tài khoản Reviewer? Đăng nhập</button>}
      </>}
    </div>
    {mode === 'login' && !inviteToken && <aside className="login-quick" aria-labelledby="login-quick-title">
      <span className="eyebrow">Truy cập nhanh</span>
      <h2 id="login-quick-title">Chọn vai trò</h2>
      <p className="muted">{demoLogin
        ? 'Bấm một vai trò để vào ngay bằng tài khoản demo, không cần mật khẩu.'
        : 'Chọn tài khoản để điền tên đăng nhập. Mật khẩu vẫn được xác thực như bình thường.'}</p>
      <div className="login-role-list" aria-busy={demoPending}>
        <button className="login-role" type="button" disabled={busy} onClick={() => selectRole('engineer')}>
          <span className="login-role-icon engineer"><UserRound size={18} aria-hidden /></span>
          <span><strong>Engineer</strong><small>Tạo và chạy thử pipeline</small></span>
          <span className="login-role-name">engineer</span>
        </button>
        <button className="login-role" type="button" disabled={busy} onClick={() => selectRole('reviewer')}>
          <span className="login-role-icon reviewer"><ShieldCheck size={18} aria-hidden /></span>
          <span><strong>Reviewer</strong><small>Xem bằng chứng và duyệt run</small></span>
          <span className="login-role-name">reviewer</span>
        </button>
      </div>
      {demoPending && <p className="small" role="status">Đang vào tài khoản demo…</p>}
      {demoError && <p className="form-error" role="alert">{demoError}</p>}
    </aside>}
    <Link className="login-back" href="/"><ArrowLeft size={15} aria-hidden />Quay lại trang giới thiệu</Link>
    </section>
    </div>
  </main>;
}

/** Có `inviteToken`: gửi kèm token, backend tạo Reviewer liên kết với Engineer đã mời; xong thì vào trang duyệt run. */
function RegisterForm({ onReset, inviteToken }: { onReset: () => void; inviteToken?: string }) {
  const [registerState, registerAction, registerPending] = useActionState(signUp, null);
  const [verifyState, verifyAction, verifyPending] = useActionState(verifySignUp, null);
  if (registerState?.email) return <>
    <div className="verification-intro">
      <MailCheck size={22} aria-hidden />
      <div><strong>Kiểm tra email của bạn</strong><p className="muted">Đã gửi mã 6 số tới <code>{registerState.email}</code>. Mã hết hạn sau 10 phút.</p></div>
    </div>
    <form action={verifyAction} className="login-form">
      <input type="hidden" name="email" value={registerState.email} />
      {inviteToken && <input type="hidden" name="next" value={REVIEW_PATH} />}
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
          {inviteToken && <input type="hidden" name="invite_token" value={inviteToken} />}
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
          <button className="button primary" type="submit" disabled={registerPending}><UserPlus size={15} aria-hidden />{registerPending ? 'Đang đăng ký…' : inviteToken ? 'Đăng ký Reviewer' : 'Đăng ký'}</button>
        </form>
        {registerState?.error && <p className="form-error" role="alert">{registerState.error}</p>}
        <p className="login-help">{inviteToken ? 'Tài khoản tạo từ link này là Reviewer, duyệt được run của Engineer đã mời. Link chỉ dùng được một lần.'
          : 'Tài khoản mới có quyền Engineer. Quyền Reviewer phải được Admin cấp riêng.'}</p>
      </>;
}
