'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { ApiError, COLUMN_TYPES, apiRequest, defaultColumns, pipelineRequest, runError, stagingOf, validateColumns,
  type ColumnRule, type RunReport, type RunRequest, type Source, type User } from '@/lib/postgres-api';
import styles from './postgres.module.css';

const STATUS_LABEL: Record<string, string> = {
  READY_FOR_REVIEW: 'Chờ duyệt', NEEDS_HUMAN: 'Cần kiểm tra', INFRA_ERROR: 'Lỗi kết nối nguồn', INTERNAL_ERROR: 'Lỗi hệ thống',
  APPROVED: 'Đã duyệt', DEPLOYED: 'Đã triển khai', REJECTED: 'Bị từ chối',
};
const statusLabel = (status: string) => STATUS_LABEL[status] ?? status;

export default function PostgresPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [columns, setColumns] = useState<ColumnRule[]>([]);
  const [runs, setRuns] = useState<RunReport[]>([]);
  const [report, setReport] = useState<RunReport | null>(null);
  const [title, setTitle] = useState('Làm sạch đơn hàng Postgres');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const validation = validateColumns(columns);
  const staged = report ? stagingOf(report) : null;
  const handleError = useCallback((err: unknown) => {
    setError(err instanceof Error ? err.message : 'Thao tác thất bại. Vui lòng thử lại.');
    if (err instanceof ApiError && err.status === 401) { setUser(null); setReport(null); setRuns([]); }
  }, []);
  const refreshSource = useCallback(async () => {
    setSourceError('');
    try {
      const next = await pipelineRequest<Source>('/source');
      setSource(next); setColumns(defaultColumns(next));
    } catch (err) {
      setSource(null);
      if (err instanceof ApiError && err.status === 401) handleError(err);
      else setSourceError(err instanceof Error ? err.message : 'Không đọc được schema.');
    }
  }, [handleError]);
  const refreshRuns = useCallback(async () => setRuns(await apiRequest<RunReport[]>('/runs')), []);
  useEffect(() => {
    let active = true;
    async function init() {
      try {
        const choices = await pipelineRequest<User[]>('/users');
        if (active) setUsers(choices);
        try {
          const current = await pipelineRequest<User>('/session');
          if (active) setUser(current);
        } catch (err) { if (!(err instanceof ApiError && err.status === 401)) throw err; }
      } catch (err) { if (active) handleError(err); }
      finally { if (active) setLoading(false); }
    }
    void init();
    return () => { active = false; };
  }, [handleError]);
  useEffect(() => {
    if (user) { void refreshSource(); void refreshRuns().catch(handleError); }
  }, [user, refreshSource, refreshRuns, handleError]);
  async function login(id: string) {
    setBusy(true); setError('');
    try { setUser(await pipelineRequest<User>('/session', { method: 'POST', body: JSON.stringify({ user_id: id }) })); }
    catch (err) { handleError(err); }
    finally { setBusy(false); }
  }
  async function logout() {
    setBusy(true);
    try {
      await pipelineRequest('/session', { method: 'DELETE' });
      setUser(null); setRuns([]); setReport(null); setSource(null); setError('');
    } catch (err) { handleError(err); }
    finally { setBusy(false); }
  }
  async function execute(event: React.FormEvent) {
    event.preventDefault();
    if (!source || validation || user?.role !== 'Engineer' || busy) return;
    setBusy(true); setError(''); setReport(null);
    const request: RunRequest = { title, table: source.table, columns };
    try {
      const next = await pipelineRequest<RunReport>('/runs', { method: 'POST', body: JSON.stringify(request) });
      setReport(next);
      await refreshRuns();
    } catch (err) { handleError(err); }
    finally { setBusy(false); }
  }
  async function openRun(id: string) {
    setBusy(true); setError('');
    try { setReport(await apiRequest<RunReport>(`/runs/${encodeURIComponent(id)}`)); }
    catch (err) { handleError(err); }
    finally { setBusy(false); }
  }
  return <main className={styles.page}>
    <header className={styles.header}><div><Link href="/">← Pipeline Studio</Link><h1>Pipeline Postgres</h1>
      <p>Đọc dữ liệu nguồn, đổi tên cột và ép kiểu trong sandbox riêng cho mỗi run.</p></div>
      {user && <div><strong>{user.name} · {user.role}</strong><button className="studio-button" disabled={busy} onClick={logout}>Đăng xuất</button></div>}
    </header>
    {error && <div className={styles.error} role="alert">{error}</div>}
    {loading ? <p role="status">Đang kết nối backend…</p> : !user ? <section className={styles.card}>
      <h2>Chọn người dùng</h2><p>Phiên đăng nhập demo được lưu ở backend. Engineer chạy pipeline; Reviewer xem kết quả.</p>
      <div className={styles.actions}>{users.map(item => <button className="studio-button primary" key={item.id} disabled={busy} onClick={() => login(item.id)}>{item.name} · {item.role}</button>)}</div>
      {!users.length && <button className="studio-button" onClick={() => window.location.reload()}>Kết nối lại</button>}
    </section> : <>
      <div className={styles.grid}><section className={styles.card}>
        <div className={styles.sectionHead}><h2>1. Nguồn dữ liệu</h2><button className="studio-button" disabled={busy} onClick={refreshSource}>Đọc lại schema</button></div>
        {sourceError && <p className={styles.error} role="alert">{sourceError}</p>}
        {source ? <><p><strong>{source.table}</strong> · giới hạn {source.max_rows} dòng/run</p>
          <form onSubmit={execute}>
            <label className="studio-field">Tên run<input required maxLength={160} value={title} disabled={busy || user.role !== 'Engineer'} onChange={event => setTitle(event.target.value)} /></label>
            <h2>2. Làm sạch dữ liệu</h2><p>Mỗi dòng cấu hình xác định tên và kiểu cột đầu ra. Dòng ép kiểu lỗi được tách riêng.</p>
            <div className={styles.scroll}><table><thead><tr><th>Cột nguồn</th><th>Tên đầu ra</th><th>Kiểu đầu ra</th></tr></thead><tbody>
              {columns.map((column, index) => <tr key={column.from}><td><code>{column.from}</code><small>{source.columns[index].type}</small></td>
                <td><input aria-label={`Tên đầu ra ${column.from}`} value={column.as} disabled={busy || user.role !== 'Engineer'} onChange={event => setColumns(prev => prev.map((item, i) => i === index ? { ...item, as: event.target.value } : item))} /></td>
                <td><select aria-label={`Kiểu đầu ra ${column.from}`} value={column.type} disabled={busy || user.role !== 'Engineer'} onChange={event => setColumns(prev => prev.map((item, i) => i === index ? { ...item, type: event.target.value as ColumnRule['type'] } : item))}>{COLUMN_TYPES.map(type => <option key={type}>{type}</option>)}</select></td></tr>)}
            </tbody></table></div>
            {validation && <p role="alert" className={styles.error}>{validation}</p>}
            <button className="studio-button primary" type="submit" disabled={busy || !!validation || !title.trim() || user.role !== 'Engineer'}>{busy ? 'Đang xử lý…' : 'Chạy pipeline'}</button>
            {user.role === 'Reviewer' && <p>Đăng nhập bằng Engineer để tạo run.</p>}
          </form></> : !sourceError && <p role="status">Đang đọc schema…</p>}
      </section><section className={styles.card}>
        <div className={styles.sectionHead}><h2>Lịch sử run</h2><button className="studio-button" disabled={busy} onClick={() => refreshRuns().catch(handleError)}>Tải lại</button></div>
        <p>100 run gần nhất trong sổ bằng chứng.</p>
        {runs.length === 0 ? <p>Chưa có run. Chọn cấu hình và chạy pipeline để bắt đầu.</p> : <ul className={styles.runs}>{runs.map(item => { const counts = stagingOf(item); return <li key={item.run_id}><button disabled={busy} aria-pressed={report?.run_id === item.run_id} onClick={() => openRun(item.run_id)}><strong>{item.user_request}</strong><span>{statusLabel(item.status)}{counts && ` · ${counts.staging}/${counts.bronze} dòng`}</span><small>{item.created_by} · v{item.version} · {new Date(item.created_at).toLocaleString('vi-VN')}</small></button></li>; })}</ul>}
      </section></div>
      {busy && <p role="status">Đang gửi yêu cầu tới backend. Kết quả sẽ hiện bên dưới.</p>}
      {report && <section className={styles.card} aria-label="Kết quả pipeline">
        <h2>3. Kết quả: {statusLabel(report.status)}</h2><p>{report.user_request} · người tạo: {report.created_by} · version {report.version}</p>
        <p><code>{report.run_id}</code>{report.timing.baseline_sec !== null && ` · ${report.timing.baseline_sec} s`}</p>
        {runError(report) && <p className={styles.error} role="alert">{runError(report)}</p>}
        {staged && <div className={styles.metrics}><div><strong>{staged.bronze}</strong>Đã đọc</div><div><strong>{staged.staging}</strong>Hợp lệ</div><div><strong>{staged.rejected}</strong>Bị tách</div></div>}
        {report.status === 'NEEDS_HUMAN' && <p className={styles.error}>Cần kiểm tra dữ liệu bị tách hoặc nguồn không có dòng nào.</p>}
        <p>Phê duyệt: chưa có. Kết quả này ghi nhận bước đọc và làm sạch dữ liệu.</p>
        {staged && <><h3>Dữ liệu sau làm sạch</h3><p>Sổ bằng chứng lưu tối đa 20 dòng mẫu cho mỗi run.</p>
        <div className={styles.scroll}><table><thead><tr>{staged.output_schema.map(column => <th key={column.name}>{column.name}<small>{column.type}</small></th>)}</tr></thead>
          <tbody>{staged.output_samples.map((row, index) => <tr key={index}>{staged.output_schema.map(column => <td key={column.name}>{row[column.name] ?? 'NULL'}</td>)}</tr>)}</tbody></table></div></>}
        {staged && staged.reject_samples.length > 0 && <><h3>Dòng ép kiểu lỗi (tối đa 20 dòng)</h3><div className={styles.scroll}><table><thead><tr><th>Dòng</th><th>Cột lỗi</th><th>Giá trị gốc</th></tr></thead><tbody>{staged.reject_samples.map(row => <tr key={row.row}><td>{row.row}</td><td>{row.columns.join(', ')}</td><td><code>{JSON.stringify(row.values)}</code></td></tr>)}</tbody></table></div></>}
        <details><summary>Xem RunReport (JSON)</summary><pre className={styles.json}>{JSON.stringify(report, null, 2)}</pre></details>
      </section>}
    </>}
  </main>;
}
