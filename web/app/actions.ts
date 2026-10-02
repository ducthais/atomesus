'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { refresh } from 'next/cache';
import type { ColumnRule, RunReport } from '@/lib/postgres-api';
import { BACKEND, BackendError, MOCK, MOCK_USER_COOKIE, SESSION_COOKIE, backend, discoverObjects } from '@/lib/catalog/source';
import { CONNECTIONS_COOKIE, parseConnections } from '@/lib/catalog/connections';
import type { Discovery } from '@/lib/catalog/types';

export type ActionState = { error?: string; message?: string } | null;
const failure = (err: unknown): ActionState => ({ error: err instanceof Error ? err.message : 'Thao tác thất bại. Vui lòng thử lại.' });
const MAX_AGE = 28800; // = SESSION_SECONDS của backend

/** Đăng nhập (hoặc đổi người dùng). Backend đặt cookie Path=/api/v1; lưu lại token với Path=/ để mọi trang server gửi kèm. */
export async function signIn(userId: string): Promise<ActionState> {
  const jar = await cookies();
  if (MOCK) { jar.set(MOCK_USER_COOKIE, userId, { path: '/', maxAge: 31536000, sameSite: 'lax' }); refresh(); return null; }
  let response: Response;
  try {
    response = await fetch(`${BACKEND}/api/v1/pipeline/session`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId }), cache: 'no-store' });
  } catch { return { error: `Không kết nối được backend ở ${BACKEND}.` }; }
  if (!response.ok) return { error: (await response.json().catch(() => null))?.detail ?? 'Đăng nhập thất bại.' };
  const token = response.headers.getSetCookie().map(c => c.match(new RegExp(`^${SESSION_COOKIE}=([^;]+)`))?.[1]).find(Boolean);
  if (!token) return { error: 'Backend không trả cookie phiên.' };
  jar.set(SESSION_COOKIE, token, { path: '/', httpOnly: true, sameSite: 'strict', maxAge: MAX_AGE });
  refresh();
  return null;
}

export async function signOut(): Promise<void> {
  const jar = await cookies();
  if (!MOCK) await backend('/pipeline/session', { method: 'DELETE' }).catch(() => null);
  jar.delete(SESSION_COOKIE);
  jar.delete(CONNECTIONS_COOKIE);
  redirect('/');
}

/** Thêm / bỏ một nguồn khỏi phiên kết nối. Chỉ ghi cookie khi danh sách thật sự đổi. */
async function remember(ref: string, on: boolean) {
  const jar = await cookies();
  const now = parseConnections(jar.get(CONNECTIONS_COOKIE)?.value);
  const next = on ? [...new Set([...now, ref])] : now.filter(r => r !== ref);
  if (next.length === now.length) return;
  if (next.length) jar.set(CONNECTIONS_COOKIE, next.join(','), { path: '/', httpOnly: true, sameSite: 'lax', maxAge: MAX_AGE });
  else jar.delete(CONNECTIONS_COOKIE);
}

/** Engineer gửi yêu cầu bằng lời; agent chạy đồng bộ (~30 giây) rồi mở trang run vừa tạo. */
export async function submitRequest(_: ActionState, form: FormData): Promise<ActionState> {
  const request = String(form.get('request') ?? '').trim();
  if (!request) return { error: 'Nhập yêu cầu.' };
  // Mỗi ô chọn bảng gửi "<source_ref>.<bảng>"; gom theo nguồn thành [{source_ref, tables}].
  const bySource = new Map<string, string[]>();
  for (const value of form.getAll('table').map(String)) {
    const [ref, table] = value.split('.');
    if (ref && table) bySource.set(ref, [...bySource.get(ref) ?? [], table]);
  }
  if (!bySource.size) return { error: 'Chọn ít nhất một bảng nguồn.' };
  const sources = [...bySource].map(([source_ref, tables]) => ({ source_ref, tables }));
  if (MOCK) return { message: `Mock: chưa gọi backend. Bản thật gửi POST /agent/runs với nguồn ${sources.map(s => `${s.source_ref} (${s.tables.join(', ')})`).join('; ')}.` };
  let report: RunReport;
  try { report = await backend<RunReport>('/agent/runs', { method: 'POST', body: JSON.stringify({ request, sources }) }); }
  catch (err) { return failure(err); }
  redirect(`/runs/${report.run_id.slice(0, 8)}`);
}

/** Reviewer duyệt / từ chối đúng bộ file đã xem (files_hash). Duyệt xong backend ghi project Dagster local. */
export async function reviewRun(runId: string, decision: 'approve' | 'reject', filesHash: string, comment: string): Promise<ActionState> {
  if (MOCK) return { message: `Mock: đã ghi ${decision === 'approve' ? 'duyệt' : 'từ chối'} trên trình duyệt.` };
  try {
    const report = await backend<RunReport>(`/runs/${encodeURIComponent(runId)}/${decision}`, { method: 'POST', body: JSON.stringify({ files_hash: filesHash || null, comment }) });
    refresh();
    return { message: report.status === 'DEPLOYED' ? 'Đã duyệt và ghi project Dagster local.' : report.status === 'DEPLOY_FAILED' ? 'Đã duyệt nhưng ghi project Dagster thất bại, xem log.' : 'Đã ghi quyết định.' };
  } catch (err) {
    if (err instanceof BackendError && err.status === 409) refresh(); // run đã đổi: tải lại để thấy trạng thái mới
    return failure(err);
  }
}

/** Làm sạch thủ công một bảng nguồn (POST /pipeline/runs): đổi tên, ép kiểu, tách dòng lỗi trong sandbox. */
export async function cleanTable(table: string, title: string, columns: ColumnRule[]): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: chưa gọi backend. Bỏ CATALOG_MOCK để gửi POST /pipeline/runs thật.' };
  let report: RunReport;
  try { report = await backend<RunReport>('/pipeline/runs', { method: 'POST', body: JSON.stringify({ title, table, columns }) }); }
  catch (err) { return failure(err); }
  redirect(`/runs/${report.run_id.slice(0, 8)}`);
}

/** Kết nối tới một nguồn (theo khai báo trong domains.yml) để xem nó đang có bảng nào. Không đọc dữ liệu. */
export async function discoverSource(ref: string): Promise<Discovery> {
  const result = await discoverObjects(ref);
  if (!('error' in result)) await remember(ref, true); // kết nối được thì nhớ cho các lần mở form sau
  return result;
}

/** Ngắt: quên nguồn khỏi phiên, lần sau phải bấm Kết nối lại. */
export async function forgetSource(ref: string): Promise<void> {
  await remember(ref, false);
}

/** Trang Quan hệ: kết nối một nguồn và nhớ nó trong phiên. */
export async function connectForRelationships(ref: string): Promise<void> {
  await remember(ref, true);
  redirect(`/relationships?source=${encodeURIComponent(ref)}`);
}

export async function disconnectForRelationships(ref: string): Promise<void> {
  await remember(ref, false);
  redirect('/relationships');
}
