import Link from 'next/link';
import { SearchX } from 'lucide-react';
import { Empty } from './_components/ui';

export default function NotFound() {
  return <Empty icon={<SearchX size={24} />} title="Không tìm thấy trong catalog">Dataset, domain hoặc run này không tồn tại. <Link href="/">Về trang khám phá</Link>.</Empty>;
}
