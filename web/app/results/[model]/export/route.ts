import { cookies } from 'next/headers';
import { BACKEND, MOCK, SESSION_COOKIE } from '@/lib/catalog/source';
import { MOCK_RESULTS, MODEL_NAME, exportErrorCode, pageOf, toCsv } from '@/lib/results';

/**
 * Tải gold.<model> thành CSV / XLSX. Trình duyệt không giữ cookie phiên backend (actions.ts lưu token phía web), nên
 * route này gửi kèm token rồi chuyển nguyên luồng file của backend về. Lỗi thì quay lại trang kết quả với `?export=<mã>`.
 */
export async function GET(request: Request, { params }: { params: Promise<{ model: string }> }) {
  const { model } = await params;
  const asked = new URL(request.url).searchParams;
  const format = asked.get('format') === 'xlsx' ? 'xlsx' : 'csv';
  const search = new URLSearchParams({ format });
  const sort = asked.get('sort');
  if (sort) { search.set('sort', sort); search.set('order', asked.get('order') === 'desc' ? 'desc' : 'asc'); }
  const back = (status: number | string) => {
    const page = new URLSearchParams({ export: exportErrorCode(status) });
    if (sort) { page.set('sort', sort); if (search.get('order') === 'desc') page.set('order', 'desc'); }
    return new Response(null, { status: 303, headers: { Location: `/results/${encodeURIComponent(model)}?${page}` } });
  };
  if (!MODEL_NAME.test(model)) return back(404);

  if (MOCK) {
    const table = MOCK_RESULTS[model];
    if (!table) return back(404);
    if (format === 'xlsx') return back(501);
    const all = pageOf(table, { page: 1, pageSize: table.rows.length, sort, order: search.get('order') === 'desc' ? 'desc' : 'asc' });
    if ('error' in all) return back(all.status);
    return new Response(toCsv(all.columns, all.rows), {
      headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': `attachment; filename="${model}.csv"` },
    });
  }

  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  let response: Response;
  try {
    response = await fetch(`${BACKEND}/api/v1/results/${encodeURIComponent(model)}/export?${search}`, {
      cache: 'no-store', headers: token ? { Cookie: `${SESSION_COOKIE}=${token}` } : {},
    });
  } catch { return back('error'); }
  if (!response.ok || !response.body) return back(response.status);
  return new Response(response.body, {
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/octet-stream',
      'Content-Disposition': response.headers.get('Content-Disposition') ?? `attachment; filename="${model}.${format}"`,
    },
  });
}
