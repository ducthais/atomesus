'use client';

import Link from 'next/link';
import { useState, useTransition } from 'react';
import { getUserConnectionTables, selectUserConnectionTables } from '../actions';
import type { ConnectionResult, ConnectionSchema, ConnectionSelection, ConnectionTable } from '@/lib/user-connections';
import { fmtNum, Panel } from './ui';

export function ConnectionTables({ id, schemas, initialSchema, initialTables, selection }: {
  id: string; schemas: ConnectionSchema[]; initialSchema: string; initialTables: ConnectionResult<ConnectionTable[]>; selection: ConnectionSelection | null;
}) {
  const [schema, setSchema] = useState(initialSchema);
  const [tables, setTables] = useState(initialTables);
  const [checked, setChecked] = useState(selection?.schema === initialSchema ? selection.tables : []);
  const [result, setResult] = useState<ConnectionResult<string> | null>(null);
  const [pending, start] = useTransition();
  const load = (name: string) => {
    setSchema(name);
    setTables({ data: [] });
    setChecked(selection?.schema === name ? selection.tables : []);
    setResult(null);
    start(async () => {
      try { setTables(await getUserConnectionTables(id, name)); }
      catch { setTables({ error: 'Không tải được danh sách bảng. Vui lòng thử lại.' }); }
    });
  };
  const available = 'data' in tables ? tables.data : [];
  const selected = available.filter(table => checked.includes(table.name)).map(table => table.name);
  return <Panel title="Chọn schema và bảng">
    <label className="field" htmlFor="connection-schema">Schema
      <select id="connection-schema" value={schema} disabled={pending || !schemas.length} onChange={event => load(event.target.value)}>
        {!schemas.length && <option value="">Không có schema có quyền đọc</option>}
        {schemas.map(item => <option key={item.name} value={item.name}>{item.name} · {item.table_count} bảng</option>)}
      </select>
    </label>
    {pending && <p className="muted section-note" role="status">Đang xử lý…</p>}
    {'error' in tables ? <div className="callout bad"><div><p role="alert">{tables.error}</p><button className="button small" disabled={pending} onClick={() => load(schema)}>Thử lại</button></div></div>
      : <fieldset className="connection-fields" disabled={pending}>
        <legend className="section-note">Bảng trong schema {schema || '—'} · {selected.length} bảng đã chọn</legend>
        {!available.length && !pending && <p className="muted">Không có bảng có quyền SELECT trong schema này.</p>}
        <ul className="object-list connection-objects">{available.map((table, index) => <li key={table.name} className={checked.includes(table.name) ? 'checked' : ''}>
          <label htmlFor={`connection-table-${index}`}>
            <input id={`connection-table-${index}`} type="checkbox" checked={checked.includes(table.name)} onChange={event => {
              setChecked(current => event.target.checked ? [...current, table.name] : current.filter(name => name !== table.name));
              setResult(null);
            }} />
            <span><strong className="mono">{table.name}</strong><small>{table.columns.length} cột · Khóa chính: {table.primary_key.join(', ') || 'Không có'} · {table.row_estimate === null || table.row_estimate < 0 ? 'Chưa có số dòng ước tính' : `${fmtNum(table.row_estimate)} dòng ước tính`}</small></span>
          </label>
          <details><summary>Xem cột của {table.name}</summary><ul className="object-columns">{table.columns.map(column => <li key={column.name}><span className="mono">{column.name}</span><small>{column.type}{table.primary_key.includes(column.name) && ' · Khóa chính'}</small></li>)}</ul></details>
        </li>)}</ul>
      </fieldset>}
    <div className="form-actions">
      <button className="button primary" disabled={pending || !schema || !selected.length || 'error' in tables} onClick={() => {
        setResult(null);
        start(async () => {
          try { setResult(await selectUserConnectionTables(id, { schema, tables: selected })); }
          catch { setResult({ error: 'Không lưu được lựa chọn. Vui lòng thử lại.' }); }
        });
      }}>Dùng các bảng này</button>
    </div>
    {result && ('error' in result ? <p className="form-error" role="alert">{result.error}</p> : <div className="callout good"><div><p role="status">Đã lưu lựa chọn bảng.</p><Link href={result.data}>Tạo pipeline từ các bảng này</Link></div></div>)}
  </Panel>;
}
