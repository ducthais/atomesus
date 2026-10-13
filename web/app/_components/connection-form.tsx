'use client';

import Link from 'next/link';
import { useRef, useState, useTransition } from 'react';
import { deleteUserConnection, submitUserConnection, type ActionState } from '../actions';
import { SSL_MODES, type ConnectionSummary, type UserConnection } from '@/lib/user-connections';
import { Facts, fmtDate, Panel } from './ui';

export function ConnectionForm({ egressIp }: { egressIp?: string }) {
  const password = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  return <Panel title="Thêm kết nối Postgres">
    <p className="muted section-note">Khuyến nghị dùng user chỉ đọc, có quyền USAGE trên schema và SELECT trên các bảng cần dùng.</p>
    {egressIp && <p className="callout info">Mở firewall của máy chủ Postgres cho IP máy chủ Atomesus: <strong>{egressIp}</strong>.</p>}
    <form method="post" onSubmit={event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const operation = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value') === 'test' ? 'test' : 'save';
      setState(null);
      start(async () => {
        try { setState(await submitUserConnection(form, operation)); }
        catch { setState({ error: 'Không gửi được yêu cầu. Vui lòng thử lại.' }); }
        finally {
          if (password.current) password.current.value = '';
          form.delete('password');
        }
      });
    }}>
      <fieldset className="connection-fields" disabled={pending}>
        <legend className="sr-only">Thông tin kết nối Postgres</legend>
        <div className="form-grid">
          <label className="field" htmlFor="connection-name">Tên kết nối<input id="connection-name" name="name" required maxLength={120} /></label>
          <label className="field" htmlFor="connection-host">Host<input id="connection-host" name="host" required maxLength={253} placeholder="db.example.vn" autoCapitalize="none" spellCheck={false} /></label>
          <label className="field" htmlFor="connection-port">Cổng<input id="connection-port" name="port" type="number" min={1} max={65535} step={1} defaultValue={5432} required /></label>
          <label className="field" htmlFor="connection-database">Database<input id="connection-database" name="database" required maxLength={128} /></label>
          <label className="field" htmlFor="connection-user">User Postgres<input id="connection-user" name="user" required maxLength={128} autoComplete="off" /></label>
          <label className="field" htmlFor="connection-password">Mật khẩu<input ref={password} id="connection-password" name="password" type="password" required maxLength={4096} autoComplete="new-password" aria-describedby="connection-password-note" /></label>
          <label className="field" htmlFor="connection-sslmode">sslmode<select id="connection-sslmode" name="sslmode" defaultValue="require">{SSL_MODES.map(mode => <option key={mode} value={mode}>{mode}</option>)}</select></label>
        </div>
        <p id="connection-password-note" className="muted small">Mật khẩu được xóa khỏi ô nhập sau mỗi lần gửi. Nhập lại mật khẩu khi cần kiểm tra hoặc lưu tiếp.</p>
        <div className="form-actions">
          <button className="button" type="submit" name="operation" value="test">Kiểm tra kết nối</button>
          <button className="button primary" type="submit" name="operation" value="save">Lưu</button>
        </div>
      </fieldset>
      {pending && <p className="muted" role="status">Đang gửi yêu cầu…</p>}
      {state?.error && <p className="form-error" role="alert">{state.error}</p>}
      {state?.message && <p className="success-text" role="status">{state.message}</p>}
    </form>
  </Panel>;
}

export function ConnectionList({ connections, engineer }: { connections: (UserConnection | ConnectionSummary)[]; engineer: boolean }) {
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  return <Panel title={engineer ? 'Kết nối của tôi' : 'Tên kết nối'}>
    {!connections.length && <p className="muted">Chưa có kết nối.</p>}
    <ul className="plain connection-list">{connections.map(connection => <li key={connection.id}>
      <div className="connection-head">
        {engineer ? <Link href={`/connections/${encodeURIComponent(connection.id)}`}><strong>{connection.name}</strong></Link> : <strong>{connection.name}</strong>}
        {engineer && <button className="button danger small" disabled={pending} aria-label={`Xóa kết nối ${connection.name}`} onClick={() => {
          if (!window.confirm(`Xóa kết nối “${connection.name}”?`)) return;
          setState(null);
          start(async () => {
            try { setState(await deleteUserConnection(connection.id)); }
            catch { setState({ error: 'Không gửi được yêu cầu xóa. Vui lòng thử lại.' }); }
          });
        }}>Xóa</button>}
      </div>
      {engineer && 'host' in connection && <Facts items={[
        ['Địa chỉ', <span key="address" className="mono">{connection.host}:{connection.port}/{connection.database}</span>],
        ['User', connection.user], ['sslmode', connection.sslmode], ['Ngày tạo', fmtDate(new Date(connection.created_at * 1000).toISOString())],
        ['Lựa chọn', connection.selection_error ? <span key="error" className="form-error" role="alert">{connection.selection_error}</span>
          : connection.selection ? `${connection.selection.schema}: ${connection.selection.tables.join(', ')}` : 'Chưa chọn bảng'],
      ]} />}
    </li>)}</ul>
    {state?.error && <p className="form-error" role="alert">{state.error}</p>}
    {state?.message && <p className="success-text" role="status">{state.message}</p>}
  </Panel>;
}
