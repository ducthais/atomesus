'use client';

import { useState, useTransition } from 'react';
import { CircleAlert, LoaderCircle, ShieldCheck } from 'lucide-react';
import type { User } from '@/lib/postgres-api';
import { updateUserRole, type ActionState } from '../actions';

export function AdminUsers({ users }: { users: User[] }) {
  const [state, setState] = useState<Record<string, ActionState>>({});
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [pending, start] = useTransition();

  const changeRole = (user: User, role: 'Engineer' | 'Reviewer') => {
    if (role === user.role) return;
    if (!window.confirm(`Đổi vai trò của ${user.name} từ ${user.role} thành ${role}?`)) return;
    setPendingId(user.id);
    start(async () => {
      const result = await updateUserRole(user.id, role);
      setState(current => ({ ...current, [user.id]: result }));
      setPendingId(null);
    });
  };

  return <section className="panel flush">
    <div className="table-scroll"><table>
      <thead><tr><th>Tài khoản</th><th>Họ và tên</th><th>Email</th><th>Vai trò</th><th>Quản lý</th></tr></thead>
      <tbody>{users.map(user => <tr key={user.id}>
        <td><strong className="mono">{user.username ?? user.id}</strong><br /><small className="muted mono">{user.id}</small></td>
        <td>{user.name}</td>
        <td>{user.email ? <span className="mono small">{user.email}</span> : <span className="muted">—</span>}</td>
        <td><span className={`badge ${user.role === 'Admin' ? 'tone-bad' : user.role === 'Reviewer' ? 'tone-wait' : 'tone-good'}`}>{user.role}</span></td>
        <td>{user.managed ? <div className="admin-role-control">
          <select aria-label={`Vai trò của ${user.name}`} value={user.role} disabled={pending}
            onChange={event => changeRole(user, event.target.value as 'Engineer' | 'Reviewer')}>
            <option value="Engineer">Engineer</option>
            <option value="Reviewer">Reviewer</option>
          </select>
          {pending && pendingId === user.id && <LoaderCircle size={15} className="spin" aria-label="Đang cập nhật" />}
          {state[user.id]?.error && <small className="form-error" role="alert"><CircleAlert size={12} />{state[user.id]?.error}</small>}
          {state[user.id]?.message && <small className="success-text" role="status">{state[user.id]?.message}</small>}
        </div> : <span className="muted small"><ShieldCheck size={14} aria-hidden /> Tài khoản hệ thống, không thể sửa</span>}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}
