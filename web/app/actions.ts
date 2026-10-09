'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { refresh } from 'next/cache';
import type { ColumnRule, RunReport } from '@/lib/postgres-api';
import { chatPayload, type ChatResponse } from '@/lib/studio';
import { BACKEND, BackendError, MOCK, MOCK_USER_COOKIE, SESSION_COOKIE, backend, discoverObjects } from '@/lib/catalog/source';
import { CONNECTIONS_COOKIE, parseConnections } from '@/lib/catalog/connections';
import type { Discovery } from '@/lib/catalog/types';
import { toReconcile, type Reconcile } from '@/lib/catalog/explain';

export type ActionState = { error?: string; message?: string } | null;
const failure = (err: unknown): ActionState => ({ error: err instanceof Error ? err.message : 'Thao tác thất bại. Vui lòng thử lại.' });
const MAX_AGE = 28800; // = SESSION_SECONDS của backend
// Bản build (deploy sau HTTPS) chỉ gửi cookie qua HTTPS; `next dev` chạy http nên để tắt.
const SECURE = process.env.NODE_ENV === 'production';

/** Đăng nhập bằng tài khoản local. Backend đặt cookie Path=/api/v1; lưu lại token với Path=/ cho các Server Component. */
export async function signIn(_: ActionState, form: FormData): Promise<ActionState> {
  const username = String(form.get('username') ?? '').trim().toLowerCase();
  const password = String(form.get('password') ?? '');
  if (!username || !password) return { error: 'Nhập tên đăng nhập và mật khẩu.' };
  const jar = await cookies();
  if (MOCK) {
    const accounts: Record<string, { id: string; password: string }> = {
      engineer: { id: 'minh', password: process.env.PIPELINE_ENGINEER_PASSWORD || 'Engineer@123' },
      reviewer: { id: 'an', password: process.env.PIPELINE_REVIEWER_PASSWORD || 'Reviewer@123' },
    };
    const account = accounts[username];
    if (!account || account.password !== password) return { error: 'Tên đăng nhập hoặc mật khẩu không đúng.' };
    jar.set(MOCK_USER_COOKIE, account.id, { path: '/', maxAge: 31536000, sameSite: 'lax', secure: SECURE });
    redirect('/');
  }
  let response: Response;
  try {
    response = await fetch(`${BACKEND}/api/v1/pipeline/session`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }), cache: 'no-store' });
  } catch { return { error: `Không kết nối được backend ở ${BACKEND}.` }; }
  if (!response.ok) return { error: (await response.json().catch(() => null))?.detail ?? 'Đăng nhập thất bại.' };
  const token = response.headers.getSetCookie().map(c => c.match(new RegExp(`^${SESSION_COOKIE}=([^;]+)`))?.[1]).find(Boolean);
  if (!token) return { error: 'Backend không trả cookie phiên.' };
  jar.set(SESSION_COOKIE, token, { path: '/', httpOnly: true, sameSite: 'strict', secure: SECURE, maxAge: MAX_AGE });
  redirect('/');
}

/** Đăng ký tài khoản Engineer mới; backend băm mật khẩu, lưu SQLite và trả phiên đăng nhập ngay. */
export async function signUp(_: ActionState, form: FormData): Promise<ActionState> {
  const name = String(form.get('name') ?? '').trim();
  const username = String(form.get('username') ?? '').trim().toLowerCase();
  const password = String(form.get('password') ?? '');
  const confirmPassword = String(form.get('confirm_password') ?? '');
  if (name.length < 2) return { error: 'Họ tên phải có ít nhất 2 ký tự.' };
  if (!/^[a-z][a-z0-9_.-]{2,31}$/.test(username)) return { error: 'Tên đăng nhập gồm 3–32 ký tự: chữ thường, số, dấu chấm, gạch dưới hoặc gạch ngang; bắt đầu bằng chữ.' };
  if (password !== confirmPassword) return { error: 'Mật khẩu nhập lại không khớp.' };
  if (password.length < 8 || !/[a-z]/.test(password) || !/[A-Z]/.test(password) || !/\d/.test(password)) {
    return { error: 'Mật khẩu cần ít nhất 8 ký tự, gồm chữ hoa, chữ thường và chữ số.' };
  }
  if (MOCK) return { error: 'Đăng ký cần backend thật. Tắt CATALOG_MOCK rồi thử lại.' };
  let response: Response;
  try {
    response = await fetch(`${BACKEND}/api/v1/pipeline/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, username, password }), cache: 'no-store',
    });
  } catch { return { error: `Không kết nối được backend ở ${BACKEND}.` }; }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    return { error: typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('; ') : 'Đăng ký thất bại.' };
  }
  const token = response.headers.getSetCookie().map(cookie => cookie.match(new RegExp(`^${SESSION_COOKIE}=([^;]+)`))?.[1]).find(Boolean);
  if (!token) return { error: 'Backend không trả cookie phiên.' };
  (await cookies()).set(SESSION_COOKIE, token, { path: '/', httpOnly: true, sameSite: 'strict', secure: SECURE, maxAge: MAX_AGE });
  redirect('/');
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
  if (next.length) jar.set(CONNECTIONS_COOKIE, next.join(','), { path: '/', httpOnly: true, sameSite: 'lax', secure: SECURE, maxAge: MAX_AGE });
  else jar.delete(CONNECTIONS_COOKIE);
}

/** Engineer gửi yêu cầu bằng lời; backend ghi run và trả ngay, agent lập kế hoạch ở nền rồi dừng chờ xác nhận (P2). */
export async function submitRequest(_: ActionState, form: FormData): Promise<ActionState> {
  const request = String(form.get('request') ?? '').trim();
  const parent = String(form.get('parent') ?? '') || undefined;
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
  try { report = await backend<RunReport>('/agent/runs', { method: 'POST', body: JSON.stringify({ request, sources, parent_run_id: parent }) }); }
  catch (err) { return failure(err); }
  redirect(`/runs/${report.run_id.slice(0, 8)}`);
}

/** Reviewer duyệt / từ chối đúng bộ file đã xem (files_hash). Duyệt xong backend ghi project Dagster local. */
export async function reviewRun(runId: string, decision: 'approve' | 'reject', filesHash: string, comment: string): Promise<ActionState> {
  if (MOCK) return { message: `Mock: đã ghi ${decision === 'approve' ? 'duyệt' : 'từ chối'} trên trình duyệt.` };
  try {
    const report = await backend<RunReport>(`/runs/${encodeURIComponent(runId)}/${decision}`, { method: 'POST', body: JSON.stringify({ files_hash: filesHash || null, comment }) });
    refresh();
    return { message: report.status === 'DEPLOYED' ? 'Đã duyệt và ghi project Dagster local. Dagster khởi động lại để nạp pipeline mới, mất khoảng 20 giây cho mỗi pipeline đang chạy (UI Dagster tạm không vào được trong lúc này), rồi xem ở mục Jobs.' : report.status === 'DEPLOY_FAILED' ? 'Đã duyệt nhưng ghi project Dagster thất bại, xem log.' : 'Đã ghi quyết định.' };
  } catch (err) {
    if (err instanceof BackendError && err.status === 409) refresh(); // run đã đổi: tải lại để thấy trạng thái mới
    return failure(err);
  }
}

/** Reviewer trả run về bước viết Spec, giữ kế hoạch và đưa nhận xét vào feedback của version mới. */
export async function requestRunChanges(runId: string, filesHash: string, version: number, feedback: string): Promise<ActionState> {
  const comment = feedback.trim();
  if (!comment) return { error: 'Nhập nhận xét cần sửa.' };
  if (MOCK) return { message: 'Mock: đã ghi yêu cầu sửa trên trình duyệt.' };
  try {
    await backend<RunReport>(`/runs/${encodeURIComponent(runId)}/request-changes`, {
      method: 'POST', body: JSON.stringify({ feedback: comment, files_hash: filesHash, version }),
    });
    refresh();
    return { message: 'Đã gửi nhận xét. Agent đang viết lại Spec cho version mới.' };
  } catch (err) {
    if (err instanceof BackendError && err.status === 409) refresh();
    return failure(err);
  }
}

/** Engineer hủy run của mình; backend quyết định quyền và trạng thái hợp lệ, không tin điều kiện ẩn/hiện ở UI. */
export async function cancelRun(runId: string): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: đã hủy run trên trình duyệt.' };
  try {
    await backend<RunReport>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
    refresh();
    return { message: 'Đã hủy run.' };
  } catch (err) {
    if (err instanceof BackendError && err.status === 409) refresh();
    return failure(err);
  }
}

/** Yêu cầu Người 3 tối ưu pipeline đang deploy; backend tạo run mới và tự đánh giá có tốt hơn hay không. */
export async function optimizePipeline(model: string): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: đã gửi yêu cầu tối ưu pipeline.' };
  let report: RunReport;
  try {
    report = await backend<RunReport>(`/pipelines/${encodeURIComponent(model)}/optimize`, { method: 'POST' });
  } catch (err) {
    return failure(err);
  }
  redirect(`/runs/${report.run_id.slice(0, 8)}`);
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

export type ReconcileState = { available: true; checkedAt: string; runId: string; reconcile: Reconcile } | { available: false; reason: string };

/** Đối chiếu lại bảng gold Dagster đang giữ với nguồn, ngay lúc bấm (chỉ đọc). */
export async function reconcileDataset(domain: string, model: string): Promise<ReconcileState> {
  if (MOCK) return { available: false, reason: 'Mock: chưa gọi backend.' };
  try {
    const r = await backend<{ available: boolean; reason?: string; checked_at: string; run_id: string; matched?: boolean; observed?: Record<string, unknown> }>(
      `/catalog/datasets/${encodeURIComponent(domain)}/${encodeURIComponent(model)}/reconcile`, { method: 'POST' });
    if (!r.available || !r.observed) return { available: false, reason: r.reason ?? 'Không đối chiếu được.' };
    return { available: true, checkedAt: r.checked_at, runId: r.run_id, reconcile: toReconcile(r.observed, !!r.matched) };
  } catch (err) { return { available: false, reason: err instanceof Error ? err.message : 'Không đối chiếu được.' }; }
}

/** Hỏi về một run (POST /chat có run_id): backend trả lời tất định từ RunReport, không đổi trạng thái run. */
export async function askRun(runId: string, message: string): Promise<ActionState> {
  const payload = chatPayload(message, 'ask', runId);
  if (!payload) return { error: 'Nhập câu hỏi.' };
  if (MOCK) return { error: 'Mock: chưa gọi backend. Bỏ CATALOG_MOCK để hỏi về run thật.' };
  try { await backend<ChatResponse>('/chat', { method: 'POST', body: JSON.stringify(payload) }); }
  catch (err) { return failure(err); }
  refresh(); // tải lại lịch sử chat (và bộ đếm câu hỏi) từ backend
  return null;
}

/** Cổng P2: Engineer xác nhận kế hoạch; agent viết Spec rồi chạy thử ở nền (hoặc dừng sau Spec nếu muốn xem mapping). */
export async function confirmPlan(runId: string, reviewMapping: boolean, plan?: unknown): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: chưa gọi backend.' };
  try { await backend<RunReport>(`/agent/runs/${encodeURIComponent(runId)}/confirm-plan`, { method: 'POST', body: JSON.stringify({ review_mapping: reviewMapping, ...(plan ? { plan } : {}) }) }); }
  catch (err) {
    if (err instanceof BackendError && err.status === 409) refresh(); // run đã đổi (đã xác nhận ở tab khác): tải lại
    return failure(err);
  }
  refresh(); // trang chuyển sang khung tiến độ
  return null;
}

/** Chạy tiếp bản nháp với mapping đã duyệt / sửa: backend kiểm tra lại Spec, sinh code và chạy thử. */
export async function continueRun(runId: string, spec: unknown): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: chưa gọi backend.' };
  let report: RunReport;
  try { report = await backend<RunReport>(`/agent/runs/${encodeURIComponent(runId)}/continue`, { method: 'POST', body: JSON.stringify({ spec }) }); }
  catch (err) { return failure(err); }
  redirect(`/runs/${report.run_id.slice(0, 8)}`);
}

/** Theo dõi: thu số đo các lần pipeline vừa chạy theo lịch (đọc Dagster, dbt, nguồn). Chỉ đọc. */
export async function collectRuns(): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: chưa gọi backend.' };
  try {
    const result = await backend<{ collected: number }>('/monitor/collect', { method: 'POST' });
    refresh();
    return { message: result.collected ? `Đã ghi số đo ${result.collected} lần chạy mới.` : 'Không có lần chạy mới nào để ghi.' };
  } catch (err) { return failure(err); }
}

/** Theo dõi: chạy 4 luật suy giảm; luật kích hoạt thì backend tạo run sửa (chạy nền, chờ người duyệt) hoặc cảnh báo. */
export async function checkPipelines(): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: chưa gọi backend.' };
  try {
    const { actions } = await backend<{ actions: { model: string; action: string }[] }>('/monitor/check', { method: 'POST' });
    refresh();
    if (!actions.length) return { message: 'Không pipeline nào suy giảm.' };
    return { message: actions.map(a => `${a.model}: ${ACTION_LABEL[a.action] ?? a.action}`).join(' · ') };
  } catch (err) { return failure(err); }
}

const ACTION_LABEL: Record<string, string> = {
  alert: 'đã cảnh báo', already_alerted: 'đã cảnh báo trước đó', cooldown: 'đã có run sửa gần đây (cooldown)',
  fix_started: 'đã tạo run sửa, chờ người duyệt',
};

/** Theo dõi: đánh dấu một cảnh báo đã xử lý. */
export async function resolveAlert(alertId: number): Promise<ActionState> {
  if (MOCK) return { message: 'Mock: chưa gọi backend.' };
  try {
    await backend(`/monitor/alerts/${alertId}/resolve`, { method: 'POST' });
    refresh();
    return null;
  } catch (err) { return failure(err); }
}

export type BatchReviewResult = { results: { runId: string; ok: boolean; status?: string | null; message: string }[] } | { error: string };

/**
 * Duyệt / từ chối nhiều run một lượt. Mỗi run đi qua đúng API duyệt từng run (kiểm files_hash, chặn tự duyệt, chỉ
 * run READY_FOR_REVIEW), lần lượt từng cái; run nào lỗi thì báo riêng, không chặn các run khác.
 */
export async function reviewRuns(items: { runId: string; filesHash: string }[], decision: 'approve' | 'reject', comment: string): Promise<BatchReviewResult> {
  if (!items.length) return { error: 'Chọn ít nhất một run.' };
  if (items.length > 50) return { error: 'Mỗi lượt chỉ được duyệt tối đa 50 run.' };
  if (new Set(items.map(item => item.runId)).size !== items.length) return { error: 'Mỗi run chỉ được chọn một lần.' };
  if (decision === 'reject' && !comment.trim()) return { error: 'Từ chối nhiều run cần ghi lý do.' };
  if (MOCK) return { results: items.map(i => ({ runId: i.runId, ok: true, message: 'Mock: chưa gọi backend.' })) };
  try {
    const result = await backend<{ results: { run_id: string; ok: boolean; status?: string | null; message: string }[] }>('/runs/batch-review', {
      method: 'POST',
      body: JSON.stringify({
        items: items.map(item => ({ run_id: item.runId, files_hash: item.filesHash })), decision, comment: comment.trim(),
      }),
    });
    refresh();
    return { results: result.results.map(item => ({ runId: item.run_id, ok: item.ok, status: item.status, message: item.message })) };
  } catch (err) {
    return { error: err instanceof Error ? err.message : 'Duyệt hàng loạt thất bại.' };
  }
}
