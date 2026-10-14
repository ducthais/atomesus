import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowDown, ArrowUp, ArrowUpDown, CircleAlert, Download, Table2 } from 'lucide-react';
import { currentUser, getResults } from '@/lib/catalog/source';
import { EXPORT_ERROR, PAGE_SIZES, exportErrorCode, exportHref, pageCount, parseQuery, queryString, rangeText, sortQuery, type Cell, type ResultPage, type ResultQuery } from '@/lib/results';
import { Empty, PageHeader, Panel } from '../../_components/ui';

type Search = { page?: string; size?: string; sort?: string; order?: string; export?: string };

function Value({ value }: { value: Cell }) {
  return value === null ? <span className="null">NULL</span> : <>{String(value)}</>;
}

function ResultTable({ model, base, found, query }: { model: string; base: string; found: ResultPage; query: ResultQuery }) {
  const pages = pageCount(found.total, found.page_size);
  const at = (page: number) => `${base}${queryString({ ...query, page })}`;
  return <Panel flush title={<>{found.total.toLocaleString('vi-VN')} dòng · {found.columns.length} cột</>}>
    <div className="table-scroll"><table>
      <caption className="sr-only">gold.{model}, trang {found.page} / {pages}. Bấm tiêu đề cột để sắp xếp.</caption>
      <thead><tr>{found.columns.map(c => {
        const active = query.sort === c.name;
        const next = sortQuery(query, c.name);
        const Icon = !active ? ArrowUpDown : query.order === 'asc' ? ArrowUp : ArrowDown;
        return <th key={c.name} scope="col" aria-sort={active ? (query.order === 'asc' ? 'ascending' : 'descending') : undefined}>
          <Link href={`${base}${queryString(next)}`} className="col-name mono" scroll={false}>{c.name}<Icon size={13} aria-hidden />
            <span className="sr-only">, sắp xếp {next.order === 'asc' ? 'tăng dần' : 'giảm dần'}</span></Link>
          <br /><small className="muted">{c.type}</small></th>;
      })}</tr></thead>
      <tbody>{found.rows.length ? found.rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j} className="mono"><Value value={value} /></td>)}</tr>)
        : <tr><td colSpan={found.columns.length} className="muted">Trang này không có dòng nào.</td></tr>}</tbody>
    </table></div>
    <nav className="form-actions" aria-label="Phân trang" style={{ margin: 0, padding: '12px 20px', borderTop: '1px solid var(--rule-soft)' }}>
      <span className="muted">{rangeText(found.page, found.page_size, found.rows.length, found.total)}</span>
      <form action={base} className="form-actions" style={{ margin: 0 }}>
        {query.sort && <input type="hidden" name="sort" value={query.sort} />}
        {query.sort && query.order === 'desc' && <input type="hidden" name="order" value="desc" />}
        <label className="small">Số dòng mỗi trang{' '}
          <select name="size" defaultValue={String(query.pageSize)}>{PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}</select></label>
        <button type="submit" className="button small">Áp dụng</button>
      </form>
      {found.page > 1 && <Link className="button small" href={at(Math.min(found.page - 1, pages))} scroll={false}>← Trang trước</Link>}
      <span className="small">Trang {found.page} / {pages}</span>
      {found.page < pages && <Link className="button small" href={at(found.page + 1)} scroll={false}>Trang sau →</Link>}
    </nav>
  </Panel>;
}

/** ADR-0001 D9: bảng gold đầy đủ của pipeline đã deploy, phân trang và sắp xếp ở backend, tải CSV / XLSX. */
export default async function ResultsPage({ params, searchParams }: { params: Promise<{ model: string }>; searchParams: Promise<Search> }) {
  const [{ model: raw }, search, user] = await Promise.all([params, searchParams, currentUser()]);
  if (!user) notFound();
  let model = raw;
  try { model = decodeURIComponent(raw); } catch { /* tên lạ: getResults trả 404 */ }
  const query = parseQuery(search);
  const found = await getResults(model, query);
  const table = 'error' in found ? null : found;
  const failure = 'error' in found ? found : null;
  const base = `/results/${encodeURIComponent(model)}`;
  const exportError = search.export ? EXPORT_ERROR[exportErrorCode(search.export)] : null;

  return <>
    <nav className="breadcrumbs" aria-label="Vị trí"><Link href="/monitor">Theo dõi pipeline</Link><span>/</span><span className="mono" style={{ overflowWrap: 'anywhere' }}>gold.{model}</span></nav>
    <PageHeader kicker="Kết quả pipeline" title={<span className="mono">gold.{model}</span>} icon={<Table2 size={22} />}
      actions={table && <>
        <a className="button" href={exportHref(model, 'csv', query)}><Download size={15} aria-hidden /> Tải CSV</a>
        <a className="button" href={exportHref(model, 'xlsx', query)}><Download size={15} aria-hidden /> Tải XLSX</a>
      </>}>
      <p>Bảng gold đầy đủ trong kho dữ liệu production, đọc lúc mở trang. File tải giữ thứ tự sắp xếp đang xem; bảng quá
        trần <code>results.max_export_rows</code> trong <code>budgets.yml</code> thì chỉ xem từng trang.</p>
    </PageHeader>

    {exportError && <div className="callout warn" role="alert"><CircleAlert size={16} aria-hidden /><div><strong>Chưa tải được file</strong><p>{exportError}</p></div></div>}

    {failure && (failure.status === 404
      ? <Empty icon={<Table2 size={20} />} title="Không có kết quả để xem">{failure.error} Pipeline cần chạy ít nhất một lần trên Dagster sau khi deploy.</Empty>
      : <div className={`callout ${failure.status === 503 ? 'warn' : 'bad'}`} role="alert"><CircleAlert size={16} aria-hidden />
        <div><strong>{failure.status === 503 ? 'Chưa đọc được bảng' : 'Không đọc được kết quả'}</strong><p>{failure.error}</p>
          {failure.status === 422 && <Link href={base} className="small">Bỏ sắp xếp →</Link>}</div></div>)}

    {table && <ResultTable model={model} base={base} found={table} query={query} />}
  </>;
}
