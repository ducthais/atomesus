'use client';

import { ServerCrash } from 'lucide-react';
import { Empty } from './_components/ui';

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <Empty icon={<ServerCrash size={24} />} title="Không tải được dữ liệu từ backend">
    {error.message || 'Lỗi không xác định.'} <button className="button" onClick={reset}>Thử lại</button>
  </Empty>;
}
