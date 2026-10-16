import Link from 'next/link';
import { ArrowDown, Database, Download, Filter, Sparkles, Table2, TriangleAlert } from 'lucide-react';
import { describeExpr, PERIOD_LABEL, resultWarnings, typeLabel, type RunResult } from '@/lib/catalog/explain';
import { fmtNum } from './ui';
import { ReconcileCard } from './reconcile';

/** Tab "Kết quả" của run: pipeline làm gì (bằng lời), cảnh báo dễ hiểu, và dữ liệu kết quả mẫu. */
export function RunResultView({ result, summary, request, goldId, deployed }: { result: RunResult; summary: string; request: string; goldId: string | null; deployed: boolean }) {
  const warnings = resultWarnings(result);
  const metricNames = new Set(result.metrics.map(m => m.name));
  const columns = result.sample.length ? Object.keys(result.sample[0]) : [...result.grain, ...metricNames];
  const grainLabel = (g: string) => result.timeGrain?.column === g ? `một ${PERIOD_LABEL[result.timeGrain.unit]} của ${g}` : g;
  const rowMeaning = result.grain.length ? <>mỗi dòng là <strong>{result.grain.map(grainLabel).join(' + ')}</strong></> : <>chỉ <strong>một dòng tổng</strong></>;

  return <div className="stack">
    {result.reconcile && <ReconcileCard r={result.reconcile} timezone={result.warehouseTimezone} />}
    {!result.reconcile && result.reconcileSkipped && <div className="callout warn"><TriangleAlert size={16} aria-hidden /><span>{result.reconcileSkipped}</span></div>}
    <section className="panel explain">
      <div className="panel-head"><h2><Sparkles size={16} aria-hidden /> Pipeline này làm gì</h2></div>
      <p className="explain-ask">Bạn hỏi: <q>{request}</q>{summary && <><br /><span className="muted">Agent hiểu là: {summary}</span></>}</p>
      <ol className="explain-steps">
        {result.sources.map(src => <li key={src.id}>
          <span className="step-icon" aria-hidden><Database size={15} /></span>
          <div>
            <strong>Đọc bảng <code>{src.table}</code></strong> từ nguồn <code>{src.sourceRef}</code>
            {src.rows.source !== null && <> · <strong>{fmtNum(src.rows.source)}</strong> dòng</>}
            {src.columns.length > 0 && <p className="muted">Giữ {src.columns.length} cột: {src.columns.map((c, i) => <span key={c.as}>{i > 0 && ', '}{c.from === c.as ? <code>{c.as}</code> : <><code>{c.from}</code> → <code>{c.as}</code></>} ({typeLabel(c.type)})</span>)}.</p>}
            {(src.rejectIf.length > 0 || src.rows.rejected !== null) && <p className="muted">
              Làm sạch: {src.rejectIf.length ? <>loại dòng nếu {src.rejectIf.map((r, i) => <span key={r}>{i > 0 && ' hoặc '}<code>{r}</code></span>)}</> : 'ép kiểu, khử trùng'}
              {src.rows.staging !== null && <> → còn <strong>{fmtNum(src.rows.staging)}</strong> dòng sạch, {fmtNum(src.rows.rejected ?? 0)} dòng bị loại{src.rows.duplicates ? `, ${fmtNum(src.rows.duplicates)} dòng trùng` : ''}</>}.
            </p>}
          </div>
        </li>)}
        {result.joins.map((j, i) => <li key={`j${i}`}><span className="step-icon" aria-hidden><Table2 size={15} /></span><div><strong>Ghép bảng</strong> ({j.type} join) theo {j.on.map(([a, b]) => `${a} = ${b}`).join(', ')}</div></li>)}
        {result.filters.length > 0 && <li><span className="step-icon" aria-hidden><Filter size={15} /></span><div><strong>Lọc</strong>: chỉ giữ dòng thỏa {result.filters.map(f => <code key={f}>{f}</code>)}</div></li>}
        <li className="final">
          <span className="step-icon" aria-hidden><ArrowDown size={15} /></span>
          <div>
            <strong>Tạo bảng kết quả {goldId ? <Link href={`/datasets/${goldId}`} className="mono">{result.goldTable}</Link> : <code>{result.goldTable}</code>}</strong>: {rowMeaning}
            {result.goldRows !== null && <> · <strong>{fmtNum(result.goldRows)}</strong> dòng</>}
            <ul className="explain-cols">{result.metrics.map(m => <li key={m.name}><code>{m.name}</code> = {describeExpr(m.expr) ?? <code>{m.expr}</code>}</li>)}</ul>
          </div>
        </li>
      </ol>
    </section>

    {warnings.length > 0 && <div className="callout warn" role="note"><TriangleAlert size={16} aria-hidden /><div>{warnings.map(w => <p key={w}>{w}</p>)}</div></div>}

    <section className="panel flush">
      <div className="panel-head"><h2><Table2 size={16} aria-hidden /> Dữ liệu kết quả</h2>
        <span className="muted small">{result.sample.length ? `${result.sample.length} dòng mẫu${result.goldRows !== null ? ` / ${fmtNum(result.goldRows)} dòng` : ''} · từ lần chạy thử trong sandbox` : 'Chưa có'}</span></div>
      {result.sample.length ? <div className="table-scroll"><table>
        <thead><tr>{columns.map(c => <th key={c} className={metricNames.has(c) ? 'metric' : ''}>{c}</th>)}</tr></thead>
        <tbody>{result.sample.map((row, i) => <tr key={i}>{columns.map(c => <td key={c} className={`mono ${metricNames.has(c) ? 'metric' : ''}`}>{row[c] ?? <span className="null">NULL</span>}</td>)}</tr>)}</tbody>
      </table></div> : <p className="muted pad">Run dừng trước bước chạy thử nên chưa có dữ liệu kết quả.</p>}
      <p className="muted small pad">{deployed ? 'Bảng đầy đủ nằm trong file DuckDB riêng của pipeline này trên Dagster (data/warehouse/<model>.duckdb), cập nhật theo lịch chạy.' : `Đây là dữ liệu chạy thử (sổ bằng chứng lưu tối đa 20 dòng). Bảng đầy đủ có sau khi Reviewer duyệt và pipeline chạy trên Dagster.`}</p>
      {deployed && result.model && <p className="pad"><Link className="button" href={`/results/${encodeURIComponent(result.model)}`}><Download size={15} aria-hidden /> Xem và tải bảng đầy đủ (CSV / XLSX)</Link></p>}
    </section>
  </div>;
}
