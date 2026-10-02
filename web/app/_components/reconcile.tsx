import { CircleAlert, ShieldCheck } from 'lucide-react';
import type { Reconcile } from '@/lib/catalog/explain';

const NUMBER = /^-?\d+(\.\d+)?$/;
const fmt = (v: string | null) => v === null ? 'NULL' : NUMBER.test(v) ? Number(v).toLocaleString('vi-VN', { maximumFractionDigits: 4 }) : v;
const fmtKey = (key: Record<string, string | null>) => Object.values(key).map(fmt).join(' · ');
const count = (n: number) => n.toLocaleString('vi-VN');

/**
 * Kết quả đối chiếu độc lập: bảng gold của pipeline so với kết quả tính lại thẳng trên Postgres nguồn
 * (SQL sinh từ Spec bằng code riêng, không qua dlt / DuckDB / dbt / template), so từng dòng.
 */
export function ReconcileCard({ r, subject = 'Kết quả chạy thử' }: { r: Reconcile; subject?: string }) {
  const totalsAgree = Object.values(r.totals).every(t => Number(t.gold) === Number(t.source));
  return <section className={`panel reconcile ${r.matched ? 'ok' : 'bad'}`}>
    <div className="reconcile-head">
      <span className="reconcile-icon" aria-hidden>{r.matched ? <ShieldCheck size={22} /> : <CircleAlert size={22} />}</span>
      <div>
        <h2>{r.matched
          ? <>Đã đối chiếu độc lập với nguồn: {count(r.matchedRows)}/{count(r.rows.source)} dòng khớp tới từng xu</>
          : <>Lệch so với nguồn: {[r.mismatchCount && `${count(r.mismatchCount)} giá trị khác`, r.missingCount && `${count(r.missingCount)} dòng thiếu`, r.extraCount && `${count(r.extraCount)} dòng thừa`].filter(Boolean).join(' · ')}</>}</h2>
        <p className="muted">{subject} được tính lại bằng một đường hoàn toàn khác: một câu SQL sinh từ Spec, chạy thẳng trên Postgres nguồn
          (tài khoản chỉ đọc), không đi qua dlt, DuckDB, dbt hay template. Rồi so từng dòng theo khóa gộp.</p>
      </div>
    </div>
    <dl className="reconcile-totals">
      {Object.entries(r.totals).map(([metric, t]) => <div key={metric}><dt><code>{metric}</code></dt>
        <dd><span>Pipeline <strong>{fmt(t.gold)}</strong></span><span>Nguồn <strong>{fmt(t.source)}</strong></span></dd></div>)}
      <div><dt>Số dòng</dt><dd><span>Pipeline <strong>{count(r.rows.gold)}</strong></span><span>Nguồn <strong>{count(r.rows.source)}</strong></span></dd></div>
    </dl>
    {r.shiftHours !== null && <p className="reconcile-diagnosis"><strong>Chẩn đoán: mọi thời điểm của pipeline lệch đúng {r.shiftHours > 0 ? '+' : ''}{r.shiftHours.toLocaleString('vi-VN')} giờ so với nguồn.</strong> Đây là dấu hiệu ép kiểu thời gian theo múi giờ của máy chạy thay vì UTC.</p>}
    {!r.matched && totalsAgree && <p className="reconcile-note"><strong>Tổng vẫn khớp</strong> nên chỉ kiểm tra tổng sẽ không thấy lỗi này. Phải so từng dòng mới phát hiện các dòng bị đặt sai chỗ.</p>}
    {!r.matched && <div className="reconcile-diff">
      {(r.missingInGold.length > 0 || r.extraInGold.length > 0) && <div className="reconcile-keys">
        {r.missingInGold.length > 0 && <div><h3>Nguồn có, pipeline không có ({count(r.missingCount)})</h3><ul>{r.missingInGold.map(k => <li key={fmtKey(k)} className="mono">{fmtKey(k)}</li>)}</ul></div>}
        {r.extraInGold.length > 0 && <div><h3>Pipeline có, nguồn không có ({count(r.extraCount)})</h3><ul>{r.extraInGold.map(k => <li key={fmtKey(k)} className="mono">{fmtKey(k)}</li>)}</ul></div>}
      </div>}
      {r.mismatched.length > 0 && <table className="data-table"><thead><tr><th>Khóa</th><th>Chỉ số</th><th>Pipeline</th><th>Nguồn</th></tr></thead>
        <tbody>{r.mismatched.map((m, i) => <tr key={i}><td className="mono">{fmtKey(m.key)}</td><td><code>{m.metric}</code></td><td className="num bad-text">{fmt(m.gold)}</td><td className="num">{fmt(m.source)}</td></tr>)}</tbody></table>}
    </div>}
    <details className="reconcile-sql"><summary>Truy vấn đối chiếu trên Postgres (có thể tự chạy lại)</summary><pre>{r.sql}</pre></details>
  </section>;
}
