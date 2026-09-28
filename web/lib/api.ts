// Client mỏng cho FastAPI. Next.js chuyển tiếp `/api/*` sang CP_API_URL (next.config.ts).

import type {
  BudgetToday, CatalogTable, Explanation, FilesResponse, PolicyResponse, RunEvent, RunSummary, RunView,
} from './types';

import { demoApi, demoSubscribe } from './demo/engine';

const BASE = '/api';

/** Bật bằng NEXT_PUBLIC_DEMO=1: phát lại run đã ghi, không cần backend (lib/demo/engine.ts). */
export const DEMO = process.env.NEXT_PUBLIC_DEMO === '1';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: { method?: string; body?: object }): Promise<T> {
  const res = await fetch(BASE + path, {
    method: init?.method ?? 'GET',
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
    body: init?.body ? JSON.stringify(init.body) : undefined,
    cache: 'no-store',
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data: { detail?: unknown } = await res.json();
      if (typeof data.detail === 'string') message = data.detail;
    } catch {
      // body không phải JSON — giữ thông báo HTTP
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

const liveApi = {
  health: () => call<{ status: string; llm: string | null }>('/health'),
  runs: () => call<RunSummary[]>('/runs'),
  run: (id: string) => call<RunView>(`/runs/${id}`),
  events: (id: string, after = 0) => call<RunEvent[]>(`/runs/${id}/events.json?after=${after}`),
  files: (id: string, version?: number) => call<FilesResponse>(`/runs/${id}/files${version ? `?version=${version}` : ''}`),
  policy: () => call<PolicyResponse>('/policy'),
  catalog: (domain?: string) => call<CatalogTable[]>(`/catalog${domain ? `?domain=${domain}` : ''}`),
  budgetToday: () => call<BudgetToday>('/budget/today'),
  explain: (id: string) => call<Explanation>(`/runs/${id}/explain`),
  explainBlock: (id: string, path: string, start: number, end: number) =>
    call<Explanation>(`/runs/${id}/explain-block`, { method: 'POST', body: { path, start_line: start, end_line: end } }),

  createRun: (request: string, domain: string, createdBy: string) =>
    call<{ run_id: string }>('/runs', { method: 'POST', body: { request, domain, created_by: createdBy } }),
  confirmPlan: (id: string, actor: string) => call<{ accepted: boolean }>(`/runs/${id}/confirm-plan`, { method: 'POST', body: { actor } }),
  answer: (id: string, answer: string) => call<{ accepted: boolean }>(`/runs/${id}/answer`, { method: 'POST', body: { answer } }),
  approve: (id: string, filesHash: string, reviewer: string, comment: string) =>
    call<RunView>(`/runs/${id}/approve`, { method: 'POST', body: { files_hash: filesHash, reviewer, comment } }),
  reject: (id: string, reason: string, actor: string) => call<RunView>(`/runs/${id}/reject`, { method: 'POST', body: { reason, actor } }),
  requestChanges: (id: string, feedback: string, actor: string) =>
    call<{ accepted: boolean }>(`/runs/${id}/request-changes`, { method: 'POST', body: { feedback, actor } }),
  deploy: (id: string, actor: string) => call<RunView & { code_location: string }>(`/runs/${id}/deploy`, { method: 'POST', body: { actor } }),
  cancel: (id: string) => call<RunView>(`/runs/${id}/cancel`, { method: 'POST' }),
  rerun: (id: string, actor: string) => call<{ run_id: string }>(`/runs/${id}/rerun`, { method: 'POST', body: { actor } }),
  retryScoped: (id: string, domain: string, actor: string) =>
    call<{ run_id: string }>(`/runs/${id}/retry-scoped`, { method: 'POST', body: { domain, actor } }),
  ticket: (id: string, kind: 'steward_review' | 'budget_increase', body: string, actor: string) =>
    call<{ id: string }>(`/runs/${id}/tickets`, { method: 'POST', body: { kind, body, actor } }),
};

export const api: typeof liveApi = DEMO ? demoApi : liveApi;

/** Theo dõi sự kiện SSE của một run. Trả về hàm huỷ. */
export function subscribe(id: string, after: number, onEvent: (e: RunEvent) => void): () => void {
  if (DEMO) return demoSubscribe(id, after, onEvent);
  const source = new EventSource(`${BASE}/runs/${id}/events?after=${after}`);
  source.addEventListener('stage', (msg) => {
    onEvent(JSON.parse((msg as MessageEvent<string>).data) as RunEvent);
  });
  return () => source.close();
}
