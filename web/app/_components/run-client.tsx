'use client';

import dynamic from 'next/dynamic';
import { useActionState, useEffect, useState, useTransition } from 'react';
import { Check, CircleAlert, Database, FileCode2, Info, LoaderCircle, Lock, LockKeyhole, Plug, RefreshCw, Search, Unplug } from 'lucide-react';
import { PLATFORM_LABEL } from '@/lib/catalog/query';
import type { DeclaredSource, SourceObject } from '@/lib/catalog/types';
import { discoverSource, forgetSource, reviewRun, submitRequest, type ActionState } from '../actions';
import type { Approval } from '@/lib/postgres-api';

const Editor = dynamic(() => import('@monaco-editor/react').then(m => m.Editor), { ssr: false });
const LANGUAGE: Record<string, string> = { py: 'python', sql: 'sql', yml: 'yaml', yaml: 'yaml' };

/** Hiện ngay nội dung trong lúc Monaco tải từ CDN (mạng chặn CDN thì vẫn đọc được code). */
const PlainCode = ({ content }: { content: string }) => <pre className="plain-code">{content.split('\n').map((line, i) => <div key={i}><span>{i + 1}</span><code>{line}</code></div>)}</pre>;

export function CodeViewer({ files }: { files: Record<string, string> }) {
  const paths = Object.keys(files).sort();
  const [selected, setSelected] = useState(paths.find(p => p.includes('/gold/')) ?? paths[0]);
  const [filter, setFilter] = useState('');
  const shown = paths.filter(p => p.toLowerCase().includes(filter.toLowerCase()));
  const folders = [...new Set(shown.map(p => p.split('/').slice(0, -1).join('/')))];
  return <div className="code-viewer">
    <aside className="file-tree" aria-label="File sinh ra">
      <label className="mini-search"><Search size={13} aria-hidden /><input placeholder="Tìm file…" aria-label="Tìm file" value={filter} onChange={e => setFilter(e.target.value)} /></label>
      {folders.map(folder => <div key={folder}><div className="folder">{folder}/</div>{shown.filter(p => p.startsWith(`${folder}/`) && !p.slice(folder.length + 1).includes('/')).map(p =>
        <button key={p} className="file" aria-current={p === selected ? 'true' : undefined} onClick={() => setSelected(p)}><FileCode2 size={13} aria-hidden />{p.split('/').at(-1)}</button>)}</div>)}
    </aside>
    <div className="editor">
      <div className="editor-bar"><span className="mono">{selected}</span><span className="muted"><LockKeyhole size={12} aria-hidden /> Chỉ đọc · sinh từ templates/</span></div>
      <div className="editor-body"><Editor key={selected} height="100%" language={LANGUAGE[selected.split('.').pop() ?? ''] ?? 'plaintext'} value={files[selected]} loading={<PlainCode content={files[selected]} />}
        options={{ readOnly: true, minimap: { enabled: false }, fontSize: 12, lineHeight: 19, scrollBeyondLastLine: false, automaticLayout: true, wordWrap: 'on' }} /></div>
    </div>
  </div>;
}

/** Duyệt / từ chối đúng bộ file đã xem (files_hash). Duyệt xong backend ghi project Dagster local. */
export function ReviewPanel({ runId, approval, eligible, reason, filesHash, version, impact = [], compareHref }: { runId: string; approval: Approval | null; eligible: boolean; reason: string; filesHash: string; version: number; impact?: string[]; compareHref?: string }) {
  const [comment, setComment] = useState('');
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const hash = filesHash.replace('sha256:', '').slice(0, 12);
  const decide = (decision: 'approve' | 'reject') => start(async () => setState(await reviewRun(runId, decision, filesHash, comment.trim())));
  if (approval) return <section className="panel review done"><div className="panel-head"><h2>{approval.decision === 'approved' ? 'Đã duyệt' : 'Đã từ chối'} v{approval.version}</h2></div>
    <p>bởi <strong>{approval.reviewer}</strong> · files_hash <code>{approval.files_hash.replace('sha256:', '').slice(0, 12)}</code></p>{approval.comment && <blockquote>{approval.comment}</blockquote>}
    {state?.message && <p className="muted small" role="status">{state.message}</p>}</section>;
  return <section className="panel review">
    <div className="panel-head"><h2>Review v{version}</h2><code className="muted">{hash}</code></div>
    <p className="muted">{reason}</p>
    {impact.length > 0 && <div className="callout warn small review-impact"><Info size={15} aria-hidden /><div><strong>Ảnh hưởng nếu duyệt</strong><ul>{impact.map(n => <li key={n}>{n}</li>)}</ul>{compareHref && <a href={compareHref}>Xem so sánh chi tiết →</a>}</div></div>}
    {!impact.length && compareHref && <p className="small"><a href={compareHref}>So sánh với phiên bản khác →</a></p>}
    {eligible && <textarea rows={3} maxLength={500} value={comment} onChange={e => setComment(e.target.value)} placeholder="Ghi chú (không bắt buộc)" aria-label="Ghi chú review" />}
    <div className="form-actions">
      <button className="button" disabled title="Có ở Sprint 2">Yêu cầu sửa</button>
      <button className="button danger" disabled={!eligible || pending} onClick={() => decide('reject')}>Từ chối</button>
      <button className="button primary" disabled={!eligible || pending} onClick={() => decide('approve')}><Check size={15} aria-hidden /> {pending ? 'Đang ghi…' : 'Phê duyệt'}</button>
    </div>
    {state && <div className={`callout ${state.error ? 'bad' : 'info'}`} role="status"><Info size={16} aria-hidden /><span>{state.error ?? state.message}</span></div>}
  </section>;
}

type Connection = { status: 'loading' | 'ready' | 'error'; objects: SourceObject[]; error?: string; at?: string };
const TIME = new Intl.DateTimeFormat('vi-VN', { timeStyle: 'medium' });

/**
 * Chọn nguồn dữ liệu cho agent. Danh sách bảng không có sẵn: bấm "Kết nối" thì backend mới kết nối tới nguồn
 * (connector theo loại: Postgres hôm nay, MongoDB sau này) và trả các bảng / collection đang có.
 */
function SourcePicker({ sources, selected, onChange, disabled, autoConnect }: { sources: DeclaredSource[]; selected: string[]; onChange: (update: (prev: string[]) => string[]) => void; disabled: boolean; autoConnect: string[] }) {
  const [connections, setConnections] = useState<Record<string, Connection>>({});
  const connect = async (ref: string) => {
    setConnections(prev => ({ ...prev, [ref]: { objects: prev[ref]?.objects ?? [], status: 'loading' } }));
    const result = await discoverSource(ref);
    if ('error' in result) { setConnections(prev => ({ ...prev, [ref]: { objects: [], status: 'error', error: result.error } })); return; }
    setConnections(prev => ({ ...prev, [ref]: { status: 'ready', objects: result.objects, at: result.discoveredAt } }));
    // Bảng đã chọn mà nguồn không còn nữa thì bỏ chọn.
    onChange(prev => prev.filter(key => !key.startsWith(`${ref}.`) || result.objects.some(o => `${ref}.${o.name}` === key)));
  };
  const disconnect = async (ref: string) => {
    setConnections(prev => { const next = { ...prev }; delete next[ref]; return next; });
    onChange(prev => prev.filter(key => !key.startsWith(`${ref}.`)));
    await forgetSource(ref);
  };
  // Tự kết nối lại các nguồn đã kết nối trong phiên, và nguồn của dataset mà form được mở từ đó.
  useEffect(() => { autoConnect.forEach(connect); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (keys: string[], on: boolean) => onChange(prev => on ? [...new Set([...prev, ...keys])] : prev.filter(k => !keys.includes(k)));

  return <fieldset className="source-picker" disabled={disabled}>
    <legend>Nguồn dữ liệu <span className="muted">· kết nối để xem nguồn đang có bảng nào, rồi chọn bảng cho agent</span></legend>
    {sources.map(src => {
      const conn = connections[src.ref];
      const noun = src.kind === 'mongodb' ? 'collection' : src.kind === 'rest_api' ? 'endpoint' : 'bảng';
      const keys = conn?.status === 'ready' ? conn.objects.map(o => `${src.ref}.${o.name}`) : [];
      const picked = keys.filter(k => selected.includes(k)).length;
      return <div key={src.ref} className={`source-option ${src.supported ? '' : 'locked'} ${picked ? 'active' : ''}`}>
        <div className="source-head">
          <span className="source-icon" aria-hidden>{src.supported ? <Database size={16} /> : <Lock size={15} />}</span>
          <span className="source-title"><strong>{src.ref}</strong><small>{PLATFORM_LABEL[src.kind] ?? src.kind}{src.location ? ` · ${src.location}` : ''} · domain {src.domain}</small></span>
          {!src.supported ? <span className="badge tone-muted">Chưa có connector cho {PLATFORM_LABEL[src.kind] ?? src.kind}</span>
            : !conn ? <button type="button" className="button small" onClick={() => connect(src.ref)}><Plug size={14} aria-hidden /> Kết nối</button>
            : conn.status === 'loading' ? <span className="badge tone-blue" role="status"><LoaderCircle size={12} className="spin" aria-hidden /> Đang kết nối…</span>
            : conn.status === 'error' ? <span className="badge tone-bad">Không kết nối được</span>
            : <span className="source-meta"><span className="badge tone-good"><span className="dot" />Đã kết nối · {conn.at ? TIME.format(new Date(conn.at)) : ''}</span>
                <button type="button" className="icon-button small" onClick={() => connect(src.ref)} title="Kết nối lại để lấy danh sách mới" aria-label={`Làm mới ${src.ref}`}><RefreshCw size={14} aria-hidden /></button>
                <button type="button" className="icon-button small" onClick={() => disconnect(src.ref)} title="Ngắt: lần sau phải bấm Kết nối lại" aria-label={`Ngắt ${src.ref}`}><Unplug size={14} aria-hidden /></button></span>}
        </div>
        {conn?.status === 'error' && <div className="source-body"><div className="callout bad small" role="alert"><CircleAlert size={15} aria-hidden /><span>{conn.error}</span><button type="button" className="button small" onClick={() => connect(src.ref)}>Thử lại</button></div></div>}
        {conn?.status === 'ready' && <div className="source-body">
          {conn.objects.length === 0 ? <p className="muted small">Kết nối được nhưng tài khoản chỉ đọc không thấy {noun} nào trong {src.location ?? src.ref}.</p> : <>
            <div className="source-toolbar"><span className="muted small">{conn.objects.length} {noun} · đã chọn {picked}</span>
              <button type="button" className="link-button" onClick={() => toggle(keys, picked < keys.length)}>{picked < keys.length ? 'Chọn tất cả' : 'Bỏ chọn tất cả'}</button></div>
            <ul className="object-list">{conn.objects.map(o => {
              const key = `${src.ref}.${o.name}`;
              return <li key={key} className={selected.includes(key) ? 'checked' : ''}>
                <label><input type="checkbox" name="table" value={key} checked={selected.includes(key)} onChange={e => toggle([key], e.target.checked)} />
                  <span className="mono">{o.name}</span>
                  <small>{o.columns.length} cột{o.rowEstimate !== null ? ` · ${o.rowEstimate > 100_000 ? '>100.000' : o.rowEstimate.toLocaleString('vi-VN')} dòng` : ''}</small></label>
                <details><summary>Xem cột</summary><ul className="object-columns">{o.columns.map(c => <li key={c.name}><span className="mono">{c.name}</span>{o.primaryKey.includes(c.name) && <span className="badge tone-amber">PK</span>}<small>{c.type}</small></li>)}</ul></details>
              </li>;
            })}</ul>
          </>}
        </div>}
      </div>;
    })}
  </fieldset>;
}

/** Engineer chọn nguồn và gửi yêu cầu bằng lời; backend ghi run ngay và chạy agent ở nền, trang run hiện tiến độ. */
export function RequestForm({ initial, canRun, examples, sources, initialTables, connected, parent }: { initial: string; canRun: boolean; examples: { label: string; request: string }[]; sources: DeclaredSource[]; initialTables: string[]; connected: string[]; parent?: string }) {
  const [request, setRequest] = useState(initial);
  const [tables, setTables] = useState(initialTables);
  const [state, action, pending] = useActionState(submitRequest, null);
  const valid = request.trim().length > 0 && request.length <= 2000 && tables.length > 0;
  return <form className="panel request-form" action={action}>
    {parent && <input type="hidden" name="parent" value={parent} />}
    <SourcePicker sources={sources} selected={tables} onChange={setTables} disabled={pending} autoConnect={[...new Set([...connected, ...initialTables.map(t => t.split('.')[0])])].filter(ref => sources.some(s => s.ref === ref && s.supported))} />
    <label className="field">Mô tả nhu cầu dữ liệu<textarea name="request" rows={6} maxLength={2000} value={request} readOnly={pending} onChange={e => setRequest(e.target.value)} placeholder="Ví dụ: Tính doanh thu theo ngày từ bảng đơn hàng…" /></label>
    <p className="muted small">Agent lập kế hoạch rồi dừng để bạn xác nhận; chưa xác nhận thì chưa viết Spec, chưa chạy gì trên nguồn.</p>
    <div className="examples"><span className="muted">Thử nhanh</span>{examples.map(x => <button type="button" key={x.label} className="chip" disabled={pending} onClick={() => setRequest(x.request)}>{x.label}</button>)}</div>
    <div className="form-actions">
      {!canRun ? <span className="muted">Chỉ Engineer gửi yêu cầu. Đổi người dùng ở góc trên bên phải.</span>
        : !tables.length && <span className="muted">Kết nối một nguồn và chọn ít nhất một bảng.</span>}
      <span className="muted small">{request.length}/2000</span>
      <button type="submit" className="button primary" disabled={!canRun || !valid || pending}>{pending ? <><LoaderCircle size={15} className="spin" aria-hidden /> Đang gửi…</> : 'Gửi cho agent'}</button>
    </div>
    {pending && <div className="callout info" role="status"><LoaderCircle size={16} className="spin" aria-hidden /><span>Đang gửi yêu cầu trên {tables.length} bảng đã chọn. Trang run sẽ mở ngay và hiện tiến độ từng bước của agent.</span></div>}
    {state && !pending && <div className={`callout ${state.error ? 'bad' : 'info'}`} role="alert"><Info size={16} aria-hidden /><span>{state.error ?? state.message}</span></div>}
  </form>;
}
