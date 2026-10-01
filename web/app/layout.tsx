import type { Metadata } from 'next';
import '@fontsource/public-sans/400.css';
import '@fontsource/public-sans/500.css';
import '@fontsource/public-sans/600.css';
import '@fontsource/public-sans/700.css';
import '@fontsource/jetbrains-mono/400.css';
import './globals.css';
import './studio.css';
import './ide.css';
export const metadata: Metadata = { title: 'Pipeline Studio · VSF', description: 'Workspace mô phỏng tạo, thẩm định và phê duyệt pipeline ELT.' };
export default function Layout({ children }: { children: React.ReactNode }) { return <html lang="vi"><body>{children}</body></html>; }
