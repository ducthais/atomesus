import type { Metadata } from 'next';
import { Search } from 'lucide-react';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/jetbrains-mono/400.css';
import './globals.css';
import { BACKEND, currentUser, getCatalog, listUsers } from '@/lib/catalog/source';
import { LoginScreen, Sidebar, UserSwitch } from './_components/shell';

export const metadata: Metadata = { title: 'Atomesus · VSF', description: 'Khám phá dataset, lineage, chất lượng và pipeline sinh ra chúng.' };

export default async function Layout({ children }: { children: React.ReactNode }) {
  const user = await currentUser();
  if (!user) {
    const users = await listUsers().catch(() => []);
    return <html lang="vi"><body><LoginScreen users={users} error={users.length ? undefined : `Không lấy được danh sách người dùng từ ${BACKEND}. Kiểm tra backend.`} /></body></html>;
  }
  const [people, catalog] = await Promise.all([listUsers(), getCatalog()]);
  return <html lang="vi"><body>
    <a href="#main" className="skip-link">Bỏ qua điều hướng</a>
    <div className="shell">
      <Sidebar reviewCount={catalog.stats().awaitingReview} live={catalog.live} backend={BACKEND} />
      <div className="main">
        <header className="topbar">
          <form action="/" className="global-search" role="search">
            <Search size={16} aria-hidden />
            <input name="q" type="search" placeholder="Tìm dataset, cột, thuật ngữ, tag…" aria-label="Tìm trong catalog" />
            <kbd>Enter</kbd>
          </form>
          <UserSwitch user={user} people={people} />
        </header>
        <main id="main" className="content">{children}</main>
      </div>
    </div>
  </body></html>;
}
