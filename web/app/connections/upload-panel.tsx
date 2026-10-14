'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState, useTransition } from 'react';
import { deleteUserUpload, selectUploadTables, submitMockUpload, type ActionState } from '../actions';
import { sendUpload } from '@/lib/upload-client';
import { validateUploadFiles, type UploadLimits, type UserUpload } from '@/lib/uploads';
import type { ConnectionResult } from '@/lib/user-connections';
import { fmtNum, Panel } from '../_components/ui';

function UploadTables({ upload, onDelete }: { upload: UserUpload; onDelete: () => void }) {
  const [checked, setChecked] = useState(upload.selected_tables);
  const [result, setResult] = useState<ConnectionResult<string> | null>(null);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  return <li>
    <div className="connection-head"><strong>{upload.name}</strong>
      <button className="button danger small" disabled={pending} aria-label={`Xóa file nguồn ${upload.name}`} onClick={() => {
        if (!window.confirm(`Xóa file nguồn “${upload.name}”?`)) return;
        setState(null);
        start(async () => {
          try {
            const next = await deleteUserUpload(upload.id);
            setState(next);
            if (!next?.error) onDelete();
          } catch { setState({ error: 'Không gửi được yêu cầu xóa. Vui lòng thử lại.' }); }
        });
      }}>Xóa</button>
    </div>
    <p className="muted small section-note">{upload.files.join(', ')} · {(upload.size_bytes / 1024).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} KB</p>
    <fieldset className="connection-fields" disabled={pending}>
      <legend className="section-note">{upload.tables.length} bảng phát hiện · {checked.length} bảng đã chọn</legend>
      <ul className="object-list connection-objects">{upload.tables.map((table, index) => <li key={table.name} className={checked.includes(table.name) ? 'checked' : ''}>
        <label htmlFor={`upload-${upload.id}-table-${index}`}>
          <input id={`upload-${upload.id}-table-${index}`} type="checkbox" checked={checked.includes(table.name)} onChange={event => {
            setChecked(current => event.target.checked ? [...current, table.name] : current.filter(name => name !== table.name));
            setResult(null);
          }} />
          <span><strong className="mono">{table.name}</strong><small style={{ color: 'var(--ink-2)' }}>{table.column_count} cột · {fmtNum(table.row_count)} dòng</small></span>
        </label>
        <details><summary style={{ color: 'var(--ink-2)' }}>Xem cột của {table.name}</summary><ul className="object-columns">{table.columns.map(column => <li key={column}><span className="mono">{column}</span><small style={{ color: 'var(--ink-2)' }}>text</small></li>)}</ul></details>
      </li>)}</ul>
      <div className="form-actions"><button className="button primary" disabled={pending || !checked.length} onClick={() => {
        setResult(null);
        start(async () => {
          try { setResult(await selectUploadTables(upload.id, checked)); }
          catch { setResult({ error: 'Không lưu được lựa chọn bảng. Vui lòng thử lại.' }); }
        });
      }}>Dùng các bảng này</button></div>
    </fieldset>
    {pending && <p className="muted" role="status">Đang xử lý…</p>}
    {state?.error && <p className="form-error" role="alert">{state.error}</p>}
    {result && ('error' in result ? <p className="form-error" role="alert">{result.error}</p>
      : <div className="callout good"><div><p role="status">Đã lưu lựa chọn. Nguồn này đã sẵn sàng trong trang tạo run và chat.</p><Link href={result.data}>Tạo pipeline từ các bảng này</Link></div></div>)}
  </li>;
}

export function UploadPanel({ initialUploads, limits, mock }: {
  initialUploads: ConnectionResult<UserUpload[]>; limits: ConnectionResult<UploadLimits>; mock: boolean;
}) {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [latest, setLatest] = useState<UserUpload | null>(null);
  const [pending, setPending] = useState(false);
  const [percent, setPercent] = useState(0);
  const [state, setState] = useState<ActionState>(null);
  const items = 'data' in initialUploads ? initialUploads.data : [];
  const uploads = latest && !items.some(item => item.id === latest.id) ? [latest, ...items] : items;
  const choose = (next: File[]) => { if (!pending) { setFiles(next); setState(null); } };
  const submit = async () => {
    if ('error' in limits) { setState({ error: limits.error }); return; }
    const error = validateUploadFiles(files, limits.data);
    if (error) { setState({ error }); return; }
    setPending(true); setPercent(0); setState(null);
    try {
      const result = mock ? await submitMockUpload(files.map(file => ({ name: file.name, size: file.size })))
        : await sendUpload(files, setPercent);
      if ('error' in result) setState(result);
      else {
        setLatest(result.data); setFiles([]);
        if (input.current) input.current.value = '';
        setState({ message: mock ? 'Mock: đã tạo bảng minh họa; chưa đọc nội dung file thật.' : 'Đã tải file và phát hiện bảng. Chọn các bảng cần dùng bên dưới.' });
        router.refresh();
      }
    } catch { setState({ error: 'Không tải được file nguồn. Vui lòng thử lại.' }); }
    finally { setPending(false); }
  };
  return <section aria-label="Tải file nguồn lên">
    <div className="detail-grid">
      <Panel title="Tải file lên">
        <p className="muted section-note">CSV dùng UTF-8 hoặc UTF-8-BOM, có dòng tiêu đề. Mỗi sheet XLSX trở thành một bảng riêng.</p>
        {mock && <p className="callout info">Chế độ mock: bảng và số dòng bên dưới là dữ liệu minh họa.</p>}
        {'data' in limits ? <p className="section-note">Mỗi file tối đa {limits.data.max_file_mb} MB · Tổng tối đa {limits.data.max_total_mb_per_user} MB · Đã dùng {(limits.data.used_bytes / 1024 ** 2).toLocaleString('vi-VN', { maximumFractionDigits: 2 })} MB</p>
          : <p className="form-error" role="alert">{limits.error}</p>}
        <form method="post" encType="multipart/form-data" onSubmit={event => { event.preventDefault(); void submit(); }}>
          <fieldset className="connection-fields" disabled={pending}>
            <legend className="sr-only">Chọn file nguồn CSV hoặc XLSX</legend>
            <div className="callout info" onDragOver={event => event.preventDefault()} onDrop={event => {
              event.preventDefault(); choose(Array.from(event.dataTransfer.files));
            }}>
              <label className="field" htmlFor="upload-files">Chọn hoặc kéo thả file CSV/XLSX
                <input ref={input} id="upload-files" name="files" type="file" accept=".csv,.xlsx" multiple
                  aria-describedby="upload-files-note" onChange={event => choose(Array.from(event.target.files ?? []))} style={{ maxWidth: '100%' }} />
              </label>
            </div>
            <p id="upload-files-note" className="muted small">Có thể chọn nhiều file cùng lúc. File được kiểm tra nội dung khi tải lên.</p>
            {!!files.length && <ul className="plain section-note">{files.map((file, index) => <li key={index}>{file.name}</li>)}</ul>}
            <div className="form-actions"><button className="button primary" type="submit" disabled={!files.length || pending || 'error' in limits}>Tải file lên</button></div>
          </fieldset>
          {pending && <div role="status"><p>{mock ? 'Đang tạo bảng minh họa…' : percent >= 100 ? 'Đã gửi file. Đang kiểm tra và đọc các bảng…' : `Đang tải file… ${percent}%`}</p>
            {!mock && <progress max={100} value={percent} aria-label="Tiến trình tải file" style={{ maxWidth: '100%' }} />}</div>}
          {state?.error && <p className="form-error" role="alert">{state.error}</p>}
          {state?.message && <p className="success-text" role="status">{state.message}</p>}
        </form>
      </Panel>
      <Panel title="File đã tải của tôi">
        {'error' in initialUploads && <p className="form-error" role="alert">{initialUploads.error}</p>}
        {!uploads.length && <p className="muted">Chưa có file nguồn nào.</p>}
        <ul className="plain connection-list">{uploads.map(upload => <UploadTables key={upload.id} upload={upload} onDelete={() => {
          if (latest?.id === upload.id) setLatest(null);
          router.refresh();
        }} />)}</ul>
      </Panel>
    </div>
  </section>;
}
