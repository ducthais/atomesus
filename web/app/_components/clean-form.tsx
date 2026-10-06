'use client';

import { useState, useTransition } from 'react';
import { Info, LoaderCircle, Play } from 'lucide-react';
import { cleanTable, type ActionState } from '../actions';
import { COLUMN_TYPES, detectDateFormats, matchesDateFormat, validateColumns, type ColumnRule } from '@/lib/postgres-api';
import type { Dataset } from '@/lib/catalog/types';

type ColumnType = ColumnRule['type'];
const RENAME: Record<string, [string, ColumnType]> = { amount_text: ['amount', 'decimal(18,2)'], ordered_at_text: ['ordered_at', 'date'] };

const isDate = (type: ColumnType) => type === 'date' || type === 'timestamp';

/** Ép kiểu thử trên dòng mẫu, cùng luật của sandbox: cột ngày có định dạng thì thử lần lượt, không thì ép nghiêm. */
function cast(value: string | null, type: ColumnType, formats?: string[]): boolean {
  if (value === null || type === 'varchar') return true;
  if (isDate(type) && formats?.length) return formats.some(format => matchesDateFormat(value, format));
  if (type === 'bigint' || type === 'integer') return /^-?\d+$/.test(value);
  if (type === 'decimal(18,2)' || type === 'double') return value.trim() !== '' && Number.isFinite(Number(value));
  if (type === 'date') return /^\d{4}-\d{2}-\d{2}$/.test(value);
  if (type === 'timestamp') return !Number.isNaN(Date.parse(value));
  return value === 'true' || value === 'false';
}

/** Làm sạch thủ công một bảng nguồn Postgres (trước đây là trang /postgres): đổi tên cột, ép kiểu, tách dòng lỗi. */
export function CleanForm({ dataset, canRun }: { dataset: Dataset; canRun: boolean }) {
  // Cột ngày: gợi ý sẵn các định dạng thấy trong dòng mẫu (dữ liệu nguồn trộn 2026-01-05, 05/01/2026, 2026/01/07).
  const suggest = (from: string) => { const found = detectDateFormats(dataset.sample.map(row => row[from])); return found.length ? found : undefined; };
  const [columns, setColumns] = useState<ColumnRule[]>(() => dataset.columns.map(c => {
    const [as, type] = RENAME[c.name] ?? [c.name, c.type === 'integer' ? 'integer' : 'varchar'];
    return { from: c.name, as, type, date_formats: isDate(type) ? suggest(c.name) : undefined };
  }));
  const [title, setTitle] = useState(`Làm sạch ${dataset.name}`);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const error = validateColumns(columns);
  const preview = dataset.sample.map(row => {
    const bad = columns.filter(c => !cast(row[c.from] ?? null, c.type, c.date_formats));
    return { row, bad };
  });
  const valid = preview.filter(p => !p.bad.length);
  const rejected = preview.filter(p => p.bad.length);
  const update = (index: number, patch: Partial<ColumnRule>) => { setState(null); setColumns(prev => prev.map((c, i) => i === index ? { ...c, ...patch } : c)); };

  return <div className="detail-grid clean">
    <section className="panel">
      <div className="panel-head"><h2>Cấu hình làm sạch</h2><span className="muted">Mỗi dòng xác định tên và kiểu cột đầu ra</span></div>
      <label className="field">Tên run<input value={title} maxLength={160} disabled={!canRun} onChange={e => setTitle(e.target.value)} /></label>
      <div className="table-scroll"><table>
        <thead><tr><th>Cột nguồn</th><th>Tên đầu ra</th><th>Kiểu đầu ra</th><th>Định dạng ngày</th></tr></thead>
        <tbody>{columns.map((c, i) => <tr key={c.from}>
          <td><code>{c.from}</code><br /><small className="muted">{dataset.columns[i].type}</small></td>
          <td><input aria-label={`Tên đầu ra của ${c.from}`} value={c.as} disabled={!canRun} onChange={e => update(i, { as: e.target.value })} /></td>
          <td><select aria-label={`Kiểu đầu ra của ${c.from}`} value={c.type} disabled={!canRun} onChange={e => {
            const type = e.target.value as ColumnType;
            update(i, { type, date_formats: isDate(type) ? c.date_formats ?? suggest(c.from) : undefined });
          }}>{COLUMN_TYPES.map(t => <option key={t}>{t}</option>)}</select></td>
          <td>{isDate(c.type) ? <input aria-label={`Định dạng ngày của ${c.from}`} className="mono" placeholder="ép nghiêm (không định dạng)" disabled={!canRun}
            value={(c.date_formats ?? []).join(', ')} title="Định dạng strptime, cách nhau bằng dấu phẩy; thử lần lượt từ trái sang phải"
            onChange={e => { const list = e.target.value.split(',').map(f => f.trim()).filter(Boolean); update(i, { date_formats: list.length ? list : undefined }); }} />
            : <span className="muted">—</span>}</td>
        </tr>)}</tbody>
      </table></div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions">
        {!canRun && <span className="muted">Chỉ Engineer chạy pipeline. Đổi vai ở góc trên bên phải.</span>}
        <button className="button primary" disabled={!canRun || !!error || !title.trim() || pending} onClick={() => start(async () => setState(await cleanTable(dataset.name, title.trim(), columns)))}>{pending ? <LoaderCircle size={15} className="spin" aria-hidden /> : <Play size={15} aria-hidden />} {pending ? 'Đang chạy trong sandbox…' : 'Chạy làm sạch'}</button>
      </div>
      {state && <div className={`callout ${state.error ? 'bad' : 'info'}`} role="status"><Info size={16} aria-hidden /><span>{state.error ?? state.message}</span></div>}
    </section>
    {dataset.sample.length === 0 ? <section className="panel"><div className="panel-head"><h2>Kết quả</h2></div><p className="muted">Backend đọc tối đa 1.000 dòng, ép kiểu trong DuckDB sandbox và ghi RunReport với số dòng hợp lệ / bị tách, 20 dòng mẫu sạch và 20 dòng lỗi. Trang run mở ngay khi chạy xong.</p></section> : <section className="panel">
      <div className="panel-head"><h2>Xem trước trên {dataset.sample.length} dòng mẫu</h2></div>
      <div className="preview-counts"><div><strong>{dataset.sample.length}</strong>Đã đọc</div><div className="good"><strong>{valid.length}</strong>Hợp lệ</div><div className={rejected.length ? 'bad' : ''}><strong>{rejected.length}</strong>Bị tách</div></div>
      {!error && <div className="table-scroll"><table>
        <thead><tr><th />{columns.map(c => <th key={c.from} className="mono">{c.as}<br /><small className="muted">{c.type}</small></th>)}</tr></thead>
        <tbody>{preview.map(({ row, bad }, i) => <tr key={i} className={bad.length ? 'row-rejected' : ''}>
          <td>{bad.length ? <span className="badge tone-bad" title={`Ép kiểu lỗi: ${bad.map(b => b.from).join(', ')}`}>Tách</span> : <span className="badge tone-good">Giữ</span>}</td>
          {columns.map(c => <td key={c.from} className={`mono ${bad.includes(c) ? 'cell-bad' : ''}`}>{row[c.from] ?? <span className="null">NULL</span>}</td>)}
        </tr>)}</tbody>
      </table></div>}
      <p className="muted small">Ép kiểu nghiêm: <code>1.250.000</code> không phải decimal. Cột ngày chỉ đọc các định dạng đã khai báo (ví dụ <code>%d/%m/%Y</code> cho <code>28/09/2026</code>); không khai báo thì chỉ nhận <code>2026-09-28</code>. Dòng lỗi được tách sang rejected kèm lý do.</p>
    </section>}
  </div>;
}
