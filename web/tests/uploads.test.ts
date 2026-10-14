import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, stripTypeScriptTypes } from 'node:module';
import { createElement, type ComponentType } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as uploads from '../lib/uploads.ts';
import { proxyUpload } from '../lib/upload-api.ts';
import { sendUpload } from '../lib/upload-client.ts';
import { MOCK_UPLOADS, MOCK_UPLOAD_LIMITS, makeMockUpload, mockUploadCatalog, mockUploadDiscovery, mockUploadSources,
  uploadError, uploadTablesLink, validateUploadFiles, validateUploadSelection, type UserUpload } from '../lib/uploads.ts';

test('upload: chỉ chọn CSV/XLSX, kiểm file và tổng theo limits, kể cả dữ liệu giả mạo', () => {
  const limits = { max_file_mb: 1, max_total_mb_per_user: 2, used_bytes: 1024 ** 2 };
  assert.equal(validateUploadFiles([{ name: 'a.CSV', size: 100 }, { name: 'b.xlsx', size: 100 }], limits), null);
  for (const files of [[], [{ name: 'a.exe', size: 100 }], [{ name: 'a.csv', size: 0 }], [{ name: 'a.csv', size: NaN }],
    [{ name: 'a.xlsx', size: 1024 ** 2 + 1 }], [{ name: 'a.csv', size: 1024 ** 2 }, { name: 'b.csv', size: 1 }]]) {
    assert.equal(typeof validateUploadFiles(files, limits), 'string');
  }
  assert.equal(typeof validateUploadFiles([null] as unknown as uploads.UploadFileInfo[], limits), 'string');
});

test('upload: lựa chọn và link giữ source_ref backend đã giải quyết va chạm tiền tố', () => {
  const tables = ['orders', 'customers'];
  assert.deepEqual(validateUploadSelection(tables), { data: tables });
  assert.equal(uploadTablesLink('f_123456789abc', tables), '/runs/new?tables=f_123456789abc.orders,f_123456789abc.customers');
  for (const value of [null, [], 'orders', ['orders', 'orders'], ['../orders'], ['orders,x'], ['a.b'], [null]]) {
    assert.ok('error' in validateUploadSelection(value));
  }
  assert.equal(uploadTablesLink('f_12345678?x=#', tables), '/runs/new');
  assert.equal(uploadTablesLink('f_12345678', ['../orders']), '/runs/new');
});

test('upload: lỗi tiếng Việt của backend được giữ nguyên, có fallback cho 413 và phiên hết hạn', () => {
  for (const status of [403, 404, 409, 413, 422, 503]) assert.equal(uploadError(status, { detail: `Lỗi ${status}` }), `Lỗi ${status}`);
  assert.match(uploadError(413, null), /dung lượng/);
  assert.match(uploadError(401, null), /hết hạn/);
  assert.match(uploadError(500, '<html>'), /Không tải được/);
});

test('mock: CSV một bảng, XLSX nhiều sheet, tên an toàn và không trùng', () => {
  const item = makeMockUpload([{ name: '../Đơn hàng.csv', size: 100 }, { name: '../Đơn hàng.csv', size: 100 }, { name: '2026.xlsx', size: 200 }], 'owner', 'a'.repeat(32));
  assert.equal(item.tables.length, 4);
  assert.deepEqual(item.tables.map(table => table.name), ['don_hang', 'don_hang_2', 't_2026', 't_2026_khach_hang']);
  assert.equal(item.size_bytes, 400);
  assert.equal(item.selected_tables.length, 0);
  item.tables.forEach(table => assert.match(table.name, /^[a-z_][a-z0-9_]{0,62}$/));
});

test('mock: chỉ bảng đã chọn xuất hiện trong domain, source picker và khám phá', () => {
  const items = structuredClone(MOCK_UPLOADS);
  const extra = mockUploadCatalog(items);
  assert.equal(extra.domains[0].id, 'u_f12ab345');
  assert.deepEqual(extra.datasets.map(item => item.name), ['f_f12ab345.don_hang']);
  assert.equal(mockUploadSources(items)[0].kind, 'filesystem');
  assert.equal(mockUploadSources(items)[0].supported, true);
  const found = mockUploadDiscovery(items, 'f_f12ab345');
  assert.ok(found && 'objects' in found);
  assert.deepEqual(found.objects.map(table => table.name), ['don_hang']);
  assert.equal(mockUploadDiscovery(items, 'f_other'), undefined);
  items[0].selected_tables = [];
  assert.deepEqual(mockUploadSources(items), []);
  assert.deepEqual(mockUploadCatalog(items).datasets, []);
});

test('proxy: multipart được chuyển tiếp bằng stream với phiên, giữ 201/413/422 từ backend', async () => {
  for (const status of [201, 413, 422]) {
    const form = new FormData();
    form.append('files', new Blob(['id\n1\n']), 'orders.csv');
    const request = new Request('http://localhost/connections/upload', { method: 'POST', body: form });
    let called = false;
    const fetcher: typeof fetch = async (url, init) => {
      called = true;
      assert.equal(url, 'http://backend/api/v1/uploads');
      assert.equal(init?.body, request.body);
      assert.equal((init as RequestInit & { duplex: string }).duplex, 'half');
      assert.equal(new Headers(init?.headers).get('Cookie'), 'pipeline_session=session-token');
      assert.match(new Headers(init?.headers).get('Content-Type')!, /multipart\/form-data; boundary=/);
      const forwarded = new Request(String(url), init as RequestInit & { duplex: 'half' });
      const received = await forwarded.formData();
      assert.equal((received.get('files') as File).name, 'orders.csv');
      assert.equal(await (received.get('files') as File).text(), 'id\n1\n');
      return Response.json(status === 201 ? MOCK_UPLOADS[0] : { detail: 'Thông báo tiếng Việt' }, { status });
    };
    const response = await proxyUpload(request, 'http://backend', 'session-token', fetcher);
    assert.equal(called, true);
    assert.equal(response.status, status);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.deepEqual(await response.json(), status === 201 ? MOCK_UPLOADS[0] : { detail: 'Thông báo tiếng Việt' });
  }
});

test('proxy: JSON giả multipart bị từ chối và lỗi mạng trả 503 tiếng Việt', async () => {
  const json = new Request('http://localhost/connections/upload', { method: 'POST', body: '{}' });
  assert.equal((await proxyUpload(json, 'http://backend', 'token', async () => assert.fail('Không được gọi mạng'))).status, 422);
  const form = new FormData(); form.append('files', new Blob(['id\n']), 'a.csv');
  const response = await proxyUpload(new Request('http://localhost/connections/upload', { method: 'POST', body: form }), 'http://backend', 'token', async () => { throw new Error('network'); });
  assert.equal(response.status, 503);
  assert.match((await response.json()).detail, /Không kết nối được backend/);
});

test('client multipart: gửi đúng file, cập nhật phần trăm và giữ lỗi 413', async () => {
  const previous = globalThis.XMLHttpRequest;
  let request: FakeRequest;
  class FakeRequest {
    status = 201; response: unknown = MOCK_UPLOADS[0]; responseType = '';
    upload = { onprogress: (_: { lengthComputable: boolean; loaded: number; total: number }) => {} };
    onload = () => {}; onerror = () => {}; onabort = () => {};
    constructor() { request = this; }
    open(method: string, url: string) { assert.equal(method, 'POST'); assert.equal(url, '/connections/upload'); }
    send(form: FormData) { assert.equal((form.get('files') as File).name, 'orders.csv'); }
  }
  globalThis.XMLHttpRequest = FakeRequest as unknown as typeof XMLHttpRequest;
  try {
    const progress: number[] = [];
    const promise = sendUpload([new File(['id\n1\n'], 'orders.csv')], percent => progress.push(percent));
    request!.upload.onprogress({ lengthComputable: true, loaded: 25, total: 100 });
    request!.upload.onprogress({ lengthComputable: true, loaded: 100, total: 100 });
    assert.deepEqual(progress, [25, 100]);
    assert.equal(request!.responseType, 'json');
    request!.onload();
    assert.deepEqual(await promise, { data: MOCK_UPLOADS[0] });
    const rejected = sendUpload([new File(['id\n'], 'orders.csv')], () => {});
    request!.status = 413; request!.response = { detail: 'Vượt tổng dung lượng của bạn.' }; request!.onload();
    assert.deepEqual(await rejected, { error: 'Vượt tổng dung lượng của bạn.' });
  } finally { globalThis.XMLHttpRequest = previous; }
});

const source = stripTypeScriptTypes(readFileSync(new URL('../app/actions.ts', import.meta.url), 'utf8'));
const code = source.replace(/import \{([^}]+)\} from '([^']+)';/g, 'const {$1} = require(\'$2\');')
  .replace(/import\('([^']+)'\)/g, 'Promise.resolve(require(\'$1\'))')
  .replace(/export async function /g, 'async function ')
  + [...source.matchAll(/export async function (\w+)/g)].map(match => `\nexports.${match[1]} = ${match[1]};`).join('');

class BackendError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

function actions({ role = 'Engineer', mockMode = false, request = async () => null }: {
  role?: string; mockMode?: boolean; request?: (path: string, init?: RequestInit) => Promise<unknown>;
} = {}) {
  let items: UserUpload[] = structuredClone(MOCK_UPLOADS);
  let refreshed = 0;
  const exports: Record<string, (...args: unknown[]) => Promise<unknown>> = {};
  const modules: Record<string, unknown> = {
    'next/headers': {}, 'next/navigation': {}, 'next/cache': { refresh: () => refreshed++ }, '@/lib/studio': {}, '@/lib/deploy': {},
    '@/lib/catalog/connections': {}, '@/lib/catalog/explain': {}, '@/lib/invite': {}, '@/lib/uploads': uploads,
    '@/lib/upload-source': { mockUserUploads: async () => items, saveMockUserUploads: async (_: string, saved: UserUpload[]) => { items = saved; } },
    '@/lib/catalog/source': { MOCK: mockMode, BackendError, backend: request, currentUser: async () => ({ id: 'minh', role }) },
  };
  new Function('require', 'exports', code)((name: string) => { assert.ok(name in modules, name); return modules[name]; }, exports);
  return { actor: exports, items: () => items, refreshed: () => refreshed };
}

test('Server Actions upload: owner từ phiên, mock thêm/chọn/xóa và cập nhật source picker', async () => {
  const { actor, items, refreshed } = actions({ mockMode: true, request: async () => assert.fail('Mock không gọi backend') });
  assert.equal((await actor.listUserUploads() as { data: UserUpload[] }).data.length, 1);
  const result = await actor.submitMockUpload([{ name: 'orders.csv', size: 100 }]) as { data: UserUpload };
  assert.equal(result.data.owner_id, 'minh');
  const id = result.data.id;
  assert.ok('error' in (await actor.selectUploadTables(id, ['missing']) as object));
  assert.deepEqual(await actor.selectUploadTables(id, ['orders']), { data: `/runs/new?tables=f_${id.slice(0, 8)}.orders` });
  assert.deepEqual(items()[0].selected_tables, ['orders']);
  assert.equal(mockUploadSources(items()).length, 2);
  assert.ok('message' in (await actor.deleteUserUpload(id) as object));
  assert.equal(mockUploadSources(items()).length, 1);
  assert.equal(refreshed(), 3);
});

test('Server Actions upload: Engineer bắt buộc, giữ detail 404/409/413 và không cho mock trên production', async () => {
  const reviewer = actions({ role: 'Reviewer', request: async () => assert.fail('Reviewer không gọi backend') });
  for (const name of ['listUserUploads', 'getUploadLimits', 'submitMockUpload', 'selectUploadTables', 'deleteUserUpload']) {
    assert.match((await reviewer.actor[name]('id', ['orders']) as { error: string }).error, /Chỉ Engineer/);
  }
  for (const status of [404, 409, 413]) {
    const { actor } = actions({ request: async () => { throw new BackendError(status, `Lỗi ${status}`); } });
    assert.deepEqual(await actor.deleteUserUpload('id'), { error: `Lỗi ${status}` });
    assert.deepEqual(await actor.selectUploadTables('id', ['orders']), { error: `Lỗi ${status}` });
  }
  assert.ok('error' in (await actions().actor.submitMockUpload([{ name: 'a.csv', size: 100 }]) as object));
});

test('Server Actions upload: chọn bảng gửi PUT đúng và dùng source_ref dài backend trả về', async () => {
  const { actor } = actions({ request: async (path, init) => {
    assert.equal(path, '/uploads/upload-id/selection');
    assert.equal(init?.method, 'PUT');
    assert.deepEqual(JSON.parse(String(init?.body)), { tables: ['orders'] });
    return { source_ref: 'f_123456789abc' };
  } });
  assert.deepEqual(await actor.selectUploadTables('upload-id', ['orders']), { data: '/runs/new?tables=f_123456789abc.orders' });
});

const require = createRequire(import.meta.url);
const { transformSync } = require('next/dist/build/swc');
function component(file: string, modules: Record<string, unknown>) {
  const { code } = transformSync(readFileSync(new URL(file, import.meta.url), 'utf8'), { filename: file, module: { type: 'commonjs' },
    jsc: { parser: { syntax: 'typescript', tsx: true }, transform: { react: { runtime: 'automatic' } } } });
  const exports: Record<string, ComponentType<Record<string, unknown>>> = {};
  new Function('require', 'exports', code)((name: string) => name in modules ? modules[name] : require(name), exports);
  return exports;
}
const ui = component('../app/_components/ui.tsx', { '@/lib/catalog/query': {}, '@/lib/studio': {} });
const { UploadPanel } = component('../app/connections/upload-panel.tsx', {
  'next/navigation': { useRouter: () => ({ refresh: () => {} }) }, '../actions': {}, '@/lib/upload-client': {}, '@/lib/uploads': uploads,
  '../_components/ui': ui,
});

test('route upload: kiểm phiên, vai trò, origin và token trước khi chuyển tiếp', async () => {
  const routeCode = stripTypeScriptTypes(readFileSync(new URL('../app/connections/upload/route.ts', import.meta.url), 'utf8'))
    .replace(/import \{([^}]+)\} from '([^']+)';/g, 'const {$1} = require(\'$2\');')
    .replace('export async function POST', 'async function POST') + '\nexports.POST = POST;';
  for (const [role, token, origin, expected] of [
    [null, 'token', undefined, 401], ['Reviewer', 'token', undefined, 403], ['Engineer', undefined, undefined, 401],
    ['Engineer', 'token', 'http://other.example', 403], ['Engineer', 'token', 'null', 403], ['Engineer', 'token', 'http://localhost', 201],
  ] as const) {
    let forwarded = false;
    const modules: Record<string, unknown> = {
      'next/headers': { cookies: async () => ({ get: () => token ? { value: token } : undefined }) },
      '@/lib/catalog/source': { MOCK: false, BACKEND: 'http://backend', SESSION_COOKIE: 'pipeline_session', currentUser: async () => role ? { role } : null },
      '@/lib/upload-api': { proxyUpload: async (_: Request, backend: string, saved: string) => {
        forwarded = true; assert.equal(backend, 'http://backend'); assert.equal(saved, 'token'); return Response.json(MOCK_UPLOADS[0], { status: 201 });
      } },
    };
    const actor: { POST?: (request: Request) => Promise<Response> } = {};
    new Function('require', 'exports', routeCode)((name: string) => modules[name], actor);
    const response = await actor.POST!(new Request('http://localhost/connections/upload', { method: 'POST', headers: origin ? { origin } : {} }));
    assert.equal(response.status, expected);
    assert.equal(forwarded, expected === 201);
  }
});

test('UI upload: mock có nhiều sheet, số cột/dòng, input có nhãn và checkbox có nhãn riêng', () => {
  const html = renderToStaticMarkup(createElement(UploadPanel, { initialUploads: { data: MOCK_UPLOADS }, limits: { data: MOCK_UPLOAD_LIMITS }, mock: true }));
  assert.match(html, /Chọn hoặc kéo thả file CSV\/XLSX/);
  assert.match(html, /for="upload-files"/);
  assert.match(html, /type="file"[^>]*accept="\.csv,\.xlsx"[^>]*multiple/);
  assert.match(html, /4 cột · 1\.248 dòng/);
  assert.match(html, /3 cột · 320 dòng/);
  assert.match(html, /type="checkbox"[^>]*checked/);
  assert.match(html, /for="upload-f12ab345678901234567890123456789-table-1"/);
  assert.match(html, /aria-label="Xóa file nguồn Doanh thu tháng 10.xlsx"/);
  assert.match(html, /Dùng các bảng này/);
  assert.match(html, /Chế độ mock/);
  assert.match(html, /class="detail-grid"/);
});

test('UI upload: lỗi có role alert và chưa có file hiển thị hướng dẫn', () => {
  const html = renderToStaticMarkup(createElement(UploadPanel, { initialUploads: { error: 'Không đọc được file.' }, limits: { error: 'Không đọc được budgets.' }, mock: false }));
  assert.match(html, /role="alert">Không đọc được file\./);
  assert.match(html, /role="alert">Không đọc được budgets\./);
  assert.match(html, /Chưa có file nguồn nào/);
  assert.doesNotMatch(html, /Chế độ mock/);
});
