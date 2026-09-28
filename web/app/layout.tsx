import type { Metadata } from 'next';
import '@fontsource/public-sans/400.css';
import '@fontsource/public-sans/600.css';
import '@fontsource/jetbrains-mono/400.css';
import './styles.css';

export const metadata: Metadata = {
  title: 'VSF · Pipeline Control',
  description: 'Không gian tạo, thẩm định và phê duyệt pipeline dữ liệu.',
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  );
}
