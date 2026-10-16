import type { Metadata } from 'next';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/playfair-display/400.css';
import '@fontsource/playfair-display/400-italic.css';
import '@fontsource/playfair-display/700.css';
import '@fontsource/playfair-display/700-italic.css';
import './globals.css';
import { BACKEND, MOCK, countAwaitingReview, currentUser, getDemoLogin } from '@/lib/catalog/source';
import { AppFrame } from './_components/shell';

export const metadata: Metadata = {
  title: 'Atomesus · Pipeline Studio',
  description: 'Biến yêu cầu dữ liệu thành pipeline được kiểm chứng, có bằng chứng và sẵn sàng vận hành.',
};

/**
 * `/` là landing page công khai; mọi trang khác cần đăng nhập (AppFrame hiện màn đăng nhập khi chưa có phiên).
 * Layout chạy ở mọi trang: chỉ đọc phiên và số run chờ duyệt, không dựng catalog (trang nào cần thì tự gọi getCatalog).
 */
export default async function Layout({ children }: { children: React.ReactNode }) {
  const user = await currentUser().catch(() => null);
  const [reviewCount, demoLogin] = await Promise.all([user ? countAwaitingReview() : 0, user ? false : getDemoLogin()]);
  return <html lang="vi"><body>
    <AppFrame user={user} reviewCount={reviewCount} live={!MOCK} backend={BACKEND} demoLogin={demoLogin}>
      {children}
    </AppFrame>
  </body></html>;
}
