import { ListChecks } from 'lucide-react';
import { currentUser, getCatalog } from '@/lib/catalog/source';
import { batchReviewItems, shortId } from '@/lib/studio';
import { BatchReview, type BatchRow } from '../../_components/batch-review';
import { Empty, PageHeader, fmtDate } from '../../_components/ui';

const usd = (n: number) => `$${n.toFixed(4)}`;

/** Duyệt nhiều run chờ duyệt cùng lúc (SPEC §3.2: trình kết quả theo mẻ). Cùng luật với nút Duyệt trên trang run. */
export default async function BatchReviewPage() {
  const [cat, user] = await Promise.all([getCatalog(), currentUser()]);
  const items = batchReviewItems(cat.runs, user?.role ?? '', user?.id ?? '');
  const rows: BatchRow[] = items.map(({ run, eligible, reason }) => ({
    id: run.id, shortId: shortId(run.id), title: run.title, domain: run.domain, owner: cat.personName(run.owner),
    version: run.version, filesHash: run.filesHash,
    checks: `${run.checks.filter(c => c.state === 'pass').length}/${run.checks.length} đạt`,
    usd: usd(run.usd), llmUsd: usd(run.llmUsd), updatedAt: fmtDate(run.updatedAt), eligible, reason,
  }));
  return <>
    <PageHeader kicker="Review" title="Duyệt nhiều run" icon={<ListChecks size={22} />}>
      <p>Mọi run đang chờ duyệt. Chỉ chọn được run của người khác và đã đạt mọi kiểm tra; mỗi run vẫn được backend kiểm
        lại đúng bộ file (<code>files_hash</code>) như khi duyệt từng run. Xem kỹ bằng chứng của từng run trước khi duyệt hàng loạt.</p>
    </PageHeader>
    {rows.length === 0 ? <Empty icon={<ListChecks size={20} />} title="Không có run nào chờ duyệt">Run đạt mọi kiểm tra sẽ hiện ở đây.</Empty>
      : <BatchReview rows={rows} />}
  </>;
}
