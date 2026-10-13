import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, stripTypeScriptTypes } from 'node:module';
import { createElement, isValidElement, type ComponentType, type ReactElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as connections from '../lib/user-connections.ts';
import { connectionTablesLink, connectionTablesToSelectManually, validateConnectionForm, validateConnectionSelection, type UserConnection } from '../lib/user-connections.ts';
import * as mock from '../lib/catalog/mock.ts';

function form(changes: Record<string, string | null | undefined> = {}) {
  const data = new FormData();
  const fields = { name: 'Postgres bán hàng', host: 'sales.example.vn', port: '5432', database: 'sales',
    user: 'reader', password: ' test-secret ', sslmode: 'require', ...changes };
  for (const [key, value] of Object.entries(fields)) if (value != null) data.set(key, value);
  return data;
}

test('kết nối: link dùng nguồn c_<8 ký tự id>, giữ thứ tự và bỏ bảng trùng', () => {
  const tables = ['orders', 'customers', 'orders'];
  assert.equal(connectionTablesLink(mock.USER_CONNECTIONS[0].id, tables), '/runs/new?tables=c_a12bc345.orders,c_a12bc345.customers');
  assert.deepEqual(tables, ['orders', 'customers', 'orders']);
});

test('kết nối: bỏ tên không được trang tạo run hỗ trợ khỏi link và liệt kê để chọn tay', () => {
  const manual = ['Đơn hàng & khách?x=#', 'order.items', 'orders,customers', 'space name', 'bảng', 'orders%20', 'orders\n', 'orders\r', 'orders\t', ''];
  const tables = [...manual, 'orders', 'order_items-2026', ...manual, 'orders'];
  const link = connectionTablesLink('a12bc3456789', tables);
  assert.equal(link, '/runs/new?tables=c_a12bc345.orders,c_a12bc345.order_items-2026');
  assert.deepEqual(connectionTablesToSelectManually(tables), manual);
  const url = new URL(link, 'https://example.vn');
  assert.equal(url.searchParams.size, 1);
  assert.equal(url.searchParams.get('tables'), 'c_a12bc345.orders,c_a12bc345.order_items-2026');
  assert.equal(connectionTablesLink('a12bc3456789', manual), '/runs/new?tables=');
  assert.deepEqual(connectionTablesToSelectManually(['orders', 'order_items-2026']), []);
});

test('kết nối: kiểm trường bắt buộc, chuẩn hóa metadata nhưng giữ nguyên mật khẩu', () => {
  const result = validateConnectionForm(form({ name: ' Sales ', host: ' sales.example.vn ', database: ' sales ', user: ' reader ' }));
  assert.ok('data' in result);
  assert.deepEqual(result.data, { name: 'Sales', host: 'sales.example.vn', port: 5432, database: 'sales', user: 'reader', password: ' test-secret ', sslmode: 'require' });
  for (const key of ['name', 'host', 'database', 'user', 'password', 'port']) {
    for (const value of ['', null]) assert.ok('error' in validateConnectionForm(form({ [key]: value })), key);
  }
  for (const key of ['name', 'host', 'database', 'user']) assert.ok('error' in validateConnectionForm(form({ [key]: '   ' })), key);
  const invalid = form();
  invalid.set('host', new Blob(['host']));
  assert.ok('error' in validateConnectionForm(invalid));
});

test('kết nối: cổng chỉ nhận số nguyên 1–65535', () => {
  for (const port of ['1', '5432', '65535']) {
    const result = validateConnectionForm(form({ port }));
    assert.ok('data' in result);
    assert.equal(result.data.port, Number(port));
  }
  for (const port of ['0', '-1', '65536', '1.5', '5432x', 'NaN', 'Infinity', '1e3', '']) {
    const result = validateConnectionForm(form({ port }));
    assert.ok('error' in result, port);
    assert.match(result.error, /1 đến 65535/);
  }
});

test('kết nối: sslmode, độ dài và ký tự NUL bị kiểm tra, lỗi không chứa mật khẩu', () => {
  for (const sslmode of connections.SSL_MODES) assert.ok('data' in validateConnectionForm(form({ sslmode })));
  for (const changes of [{ sslmode: 'invalid' }, { name: 'n'.repeat(121) }, { host: 'h'.repeat(254) },
    { database: 'd'.repeat(129) }, { user: 'u'.repeat(129) }, { password: 'p'.repeat(4097) }, { host: 'bad\0host' }, { password: 'test-secret\0' }]) {
    const result = validateConnectionForm(form(changes));
    assert.ok('error' in result);
    assert.ok(!result.error.includes('test-secret'));
  }
});

test('kết nối: lựa chọn cần schema và bảng hợp lệ, không trùng', () => {
  const selection = { schema: 'public', tables: ['orders', 'customers'] };
  assert.deepEqual(validateConnectionSelection(selection), { data: selection });
  for (const value of [null, {}, { schema: 'public', tables: [] }, { schema: '', tables: ['orders'] },
    { schema: 'public', tables: ['orders', 'orders'] }, { schema: 'public', tables: [null] },
    { schema: 'public', tables: 'orders' }, { schema: 'pg\0', tables: ['orders'] }, { schema: 'public', tables: [' '] }]) {
    assert.ok('error' in validateConnectionSelection(value));
  }
});

// Giả lập phiên và backend như kiểm thử Deploy; chạy đúng Server Actions mà không cần Next server.
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
  let items: UserConnection[] = structuredClone(mock.USER_CONNECTIONS);
  let refreshed = 0;
  const exports: Record<string, (...args: unknown[]) => Promise<unknown>> = {};
  const modules: Record<string, unknown> = {
    'next/headers': {}, 'next/navigation': {}, 'next/cache': { refresh: () => refreshed++ }, '@/lib/studio': {}, '@/lib/deploy': {},
    '@/lib/catalog/connections': {}, '@/lib/catalog/explain': {}, '@/lib/user-connections': connections, '@/lib/catalog/mock': mock,
    '@/lib/catalog/source': { MOCK: mockMode, BackendError, backend: request, currentUser: async () => ({ id: 'minh', role }),
      mockUserConnections: async () => items, saveMockUserConnections: async (_: string, saved: UserConnection[]) => { items = saved; } },
  };
  new Function('require', 'exports', code)((name: string) => {
    assert.ok(name in modules, `Module chưa giả lập: ${name}`); return modules[name];
  }, exports);
  return { actor: exports, items: () => items, refreshed: () => refreshed };
}

test('Server Actions: giữ nguyên detail 422/403/404/409/503 và xóa mật khẩu sau cả lỗi', async () => {
  for (const status of [422, 403, 404, 409, 503]) {
    const detail = `Lỗi ${status}: lý do tiếng Việt từ backend.`;
    const { actor } = actions({ request: async () => { throw new BackendError(status, detail); } });
    const data = form();
    assert.deepEqual(await actor.submitUserConnection(data, 'save'), { error: detail });
    assert.equal(data.has('password'), false);
    assert.deepEqual(await actor.deleteUserConnection('id'), { error: detail });
    assert.deepEqual(await actor.getUserConnectionSchemas('id'), { error: detail });
    assert.deepEqual(await actor.getUserConnectionTables('id', 'public'), { error: detail });
    assert.deepEqual(await actor.selectUserConnectionTables('id', { schema: 'public', tables: ['orders'] }), { error: detail });
  }
});

test('Server Actions: Reviewer chỉ nhận id/tên và không thể thao tác kết nối', async () => {
  let calls = 0;
  const { actor } = actions({ role: 'Reviewer', request: async () => { calls++; return mock.USER_CONNECTIONS; } });
  assert.deepEqual(await actor.listUserConnections(), { data: mock.USER_CONNECTIONS.map(({ id, name }) => ({ id, name })) });
  assert.equal(calls, 1);
  for (const [name, args] of [ ['submitUserConnection', [form(), 'test']], ['deleteUserConnection', ['id']],
    ['getUserConnectionSchemas', ['id']], ['getUserConnectionTables', ['id', 'public']],
    ['selectUserConnectionTables', ['id', { schema: 'public', tables: ['orders'] }]] ] as const) {
    const result = await actor[name](...args) as { error: string };
    assert.match(result.error, /Chỉ Engineer/);
  }
  assert.equal(calls, 1);
});

test('Server Actions: POST test/lưu và PUT lựa chọn dùng đúng hợp đồng backend', async () => {
  const calls: { path: string; method?: string; body: unknown }[] = [];
  const { actor, refreshed } = actions({ request: async (path, init) => {
    calls.push({ path, method: init?.method, body: JSON.parse(String(init?.body)) });
    return path.endsWith('/selection') ? { schema: 'public', tables: ['orders'] } : { message: 'Kết nối thành công.' };
  } });
  for (const operation of ['test', 'save']) {
    const data = form();
    assert.ok('message' in (await actor.submitUserConnection(data, operation) as object));
    assert.equal(data.has('password'), false);
  }
  const id = mock.USER_CONNECTIONS[0].id;
  assert.deepEqual(await actor.selectUserConnectionTables(id, { schema: 'public', tables: ['orders'] }), { data: '/runs/new?tables=c_a12bc345.orders' });
  assert.deepEqual(calls.map(c => [c.path, c.method]), [['/connections/test', 'POST'], ['/connections', 'POST'], [`/connections/${id}/selection`, 'PUT']]);
  assert.equal((calls[0].body as { password: string }).password, ' test-secret ');
  assert.deepEqual(calls[2].body, { schema: 'public', tables: ['orders'] });
  assert.equal(refreshed(), 2);
});

test('mock: thêm, kiểm tra, chọn schema/bảng, lưu và xóa không gọi mạng hay lưu mật khẩu', async () => {
  const { actor, items } = actions({ mockMode: true, request: async () => { throw new Error('Mock không được gọi mạng.'); } });
  assert.equal((await actor.listUserConnections() as { data: unknown[] }).data.length, 2);
  await actor.submitUserConnection(form(), 'test');
  assert.equal(items().length, 2);
  await actor.submitUserConnection(form(), 'save');
  assert.equal(items().length, 3);
  assert.ok(!JSON.stringify(items()).includes('password'));
  assert.ok(!JSON.stringify(items()).includes('test-secret'));
  const id = items()[0].id;
  assert.deepEqual(await actor.getUserConnectionSchemas(id), { data: mock.USER_CONNECTION_SCHEMAS });
  assert.deepEqual(await actor.getUserConnectionTables(id, 'archive'), { data: mock.USER_CONNECTION_TABLES.archive });
  assert.deepEqual(await actor.selectUserConnectionTables(id, { schema: 'archive', tables: ['orders_2025'] }), { data: connectionTablesLink(id, ['orders_2025']) });
  assert.deepEqual(items()[0].selection, { schema: 'archive', tables: ['orders_2025'] });
  assert.ok('error' in (await actor.selectUserConnectionTables(id, { schema: 'public', tables: ['missing'] }) as object));
  await actor.deleteUserConnection(id);
  assert.equal(items().length, 2);
  assert.ok('error' in (await actor.getUserConnectionSchemas(id) as object));
});

// Kiểm HTML của component thật bằng SWC như các kiểm thử giao diện hiện có.
const require = createRequire(import.meta.url);
const { transformSync } = require('next/dist/build/swc');
function component(file: string, modules: Record<string, unknown>) {
  const { code } = transformSync(readFileSync(new URL(file, import.meta.url), 'utf8'), {
    filename: file, module: { type: 'commonjs' },
    jsc: { parser: { syntax: 'typescript', tsx: true }, transform: { react: { runtime: 'automatic' } } },
  });
  const exports: Record<string, ComponentType<Record<string, unknown>>> = {};
  new Function('require', 'exports', code)((name: string) => name in modules ? modules[name] : require(name), exports);
  return exports;
}
const ui = component('../app/_components/ui.tsx', { '@/lib/catalog/query': {}, '@/lib/studio': {} });
const modules = { '../actions': {}, '@/lib/user-connections': connections, './ui': ui };
const { ConnectionForm, ConnectionList } = component('../app/_components/connection-form.tsx', modules);
const { ConnectionTables } = component('../app/_components/connection-tables.tsx', modules);
const { Sidebar } = component('../app/_components/shell.tsx', { ...modules, 'next/navigation': { usePathname: () => '/connections' } });

// Giữ state qua các lần render và chạy handler thật; chỉ giả lập hooks và Server Actions.
function interactiveTables(props: Record<string, unknown>, actor: Record<string, unknown>) {
  const state: unknown[] = [], transitions: Promise<void>[] = [];
  let cursor = 0, tree: ReactNode;
  const { ConnectionTables } = component('../app/_components/connection-tables.tsx', {
    ...modules, '../actions': actor,
    react: { ...require('react'), useState: (initial: unknown) => {
      const index = cursor++;
      if (index >= state.length) state.push(initial);
      return [state[index], (value: unknown) => {
        state[index] = typeof value === 'function' ? value(state[index]) : value;
      }];
    }, useTransition: () => [false, (action: () => Promise<void>) => { transitions.push(action()); }] },
  });
  const render = () => { cursor = 0; tree = (ConnectionTables as (props: Record<string, unknown>) => ReactNode)(props); };
  const find = (predicate: (element: ReactElement<Record<string, unknown>>) => boolean) => {
    const visit = (node: ReactNode): ReactElement<Record<string, unknown>> | undefined => {
      if (Array.isArray(node)) {
        for (const child of node) { const found = visit(child); if (found) return found; }
      } else if (isValidElement<Record<string, unknown>>(node)) {
        if (predicate(node)) return node;
        return visit(node.props.children as ReactNode);
      }
    };
    const found = visit(tree);
    assert.ok(found, 'Không tìm thấy control trong component.');
    return found;
  };
  render();
  return {
    find, html: () => renderToStaticMarkup(tree),
    flush: async () => { await Promise.all(transitions.splice(0)); render(); },
    check: (index: number, checked: boolean) => {
      const input = find(node => node.props.id === `connection-table-${index}`);
      (input.props.onChange as (event: { target: { checked: boolean } }) => void)({ target: { checked } });
      render();
    },
    schema: (value: string) => {
      const select = find(node => node.type === 'select');
      (select.props.onChange as (event: { target: { value: string } }) => void)({ target: { value } });
      render();
    },
    save: () => {
      const button = find(node => node.type === 'button' && node.props.children === 'Dùng các bảng này');
      assert.equal(button.props.disabled, false);
      (button.props.onClick as () => void)();
      render();
    },
  };
}

test('giao diện: lựa chọn vừa lưu thay thế prop ban đầu khi đổi schema rồi quay lại; lỗi giữ lựa chọn cũ', async () => {
  const id = mock.USER_CONNECTIONS[0].id;
  let fail = false;
  const saved: connections.ConnectionSelection[] = [];
  const view = interactiveTables({ id, schemas: mock.USER_CONNECTION_SCHEMAS, initialSchema: 'public',
    initialTables: { data: mock.USER_CONNECTION_TABLES.public }, selection: { schema: 'public', tables: ['orders'] } }, {
    getUserConnectionTables: async (_: string, schema: string) => ({ data: mock.USER_CONNECTION_TABLES[schema] }),
    selectUserConnectionTables: async (_: string, selection: connections.ConnectionSelection) => {
      saved.push(selection);
      return fail ? { error: 'Không lưu được lựa chọn.' } : { data: connectionTablesLink(id, selection.tables) };
    },
  });
  view.check(0, false);
  view.check(1, true);
  view.save();
  await view.flush();
  assert.deepEqual(saved, [{ schema: 'public', tables: ['customers'] }]);
  assert.match(view.html(), /href="\/runs\/new\?tables=c_a12bc345.customers"/);
  view.schema('archive');
  await view.flush();
  view.schema('public');
  await view.flush();
  assert.equal(view.find(node => node.props.id === 'connection-table-0').props.checked, false);
  assert.equal(view.find(node => node.props.id === 'connection-table-1').props.checked, true);
  fail = true;
  view.check(0, true);
  view.check(1, false);
  view.save();
  await view.flush();
  assert.match(view.html(), /role="alert">Không lưu được lựa chọn\./);
  view.schema('archive');
  await view.flush();
  view.schema('public');
  await view.flush();
  assert.equal(view.find(node => node.props.id === 'connection-table-0').props.checked, false);
  assert.equal(view.find(node => node.props.id === 'connection-table-1').props.checked, true);
});

test('giao diện: sau khi lưu, ghi chú cạnh link liệt kê đúng các bảng cần chọn tay', async () => {
  const id = mock.USER_CONNECTIONS[0].id;
  const manual = ['Đơn hàng & khách?x=#', 'order.items'];
  for (const names of [['orders', ...manual], manual, ['orders']]) {
    const view = interactiveTables({ id, schemas: mock.USER_CONNECTION_SCHEMAS, initialSchema: 'public',
      initialTables: { data: names.map(name => ({ name, columns: [], primary_key: [], row_estimate: null })) },
      selection: null }, {
      selectUserConnectionTables: async (_: string, selection: connections.ConnectionSelection) => ({ data: connectionTablesLink(id, selection.tables) }),
    });
    names.forEach((_, index) => view.check(index, true));
    view.save();
    await view.flush();
    const html = view.html();
    assert.match(html, /Đã lưu lựa chọn bảng\./);
    assert.match(html, new RegExp(`href="${connectionTablesLink(id, names).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`));
    if (names.includes(manual[0])) assert.match(html, /Tạo pipeline từ các bảng này<\/a><p class="section-note">Chọn tay các bảng sau ở trang tạo run: Đơn hàng &amp; khách\?x=#, order\.items\./);
    else assert.doesNotMatch(html, /Chọn tay các bảng sau/);
  }
});

test('giao diện: Reviewer chỉ thấy tên, không thấy metadata, liên kết hay nút xóa', () => {
  const html = renderToStaticMarkup(createElement(ConnectionList, { connections: mock.USER_CONNECTIONS, engineer: false }));
  assert.match(html, /Postgres bán hàng/);
  assert.doesNotMatch(html, /sales\.example\.vn|reader|sslmode|<button|href=/);
  for (const role of ['Engineer', 'Reviewer', 'Admin']) {
    const nav = renderToStaticMarkup(createElement(Sidebar, { role, reviewCount: 0, live: false, backend: '' }));
    assert.equal(nav.includes('Nguồn dữ liệu'), role === 'Engineer');
  }
});

test('giao diện: mật khẩu không có giá trị HTML, input có nhãn, IP firewall chỉ hiện khi có cấu hình', () => {
  const html = renderToStaticMarkup(createElement(ConnectionForm));
  assert.match(html, /<input[^>]*type="password"[^>]*name="password"/);
  const input = html.match(/<input[^>]*name="password"[^>]*>/)![0];
  assert.doesNotMatch(input, /value=/);
  assert.doesNotMatch(html, /Mở firewall/);
  assert.match(html, /user chỉ đọc/);
  for (const id of ['name', 'host', 'port', 'database', 'user', 'password', 'sslmode']) assert.ok(html.includes(`for="connection-${id}"`));
  assert.match(html, /value="5432"/);
  assert.match(html, /value="require" selected/);
  assert.match(renderToStaticMarkup(createElement(ConnectionForm, { egressIp: '203.0.113.10' })), /Mở firewall.*203\.0\.113\.10/);
});

test('trang kết nối: đọc IP lúc request, ưu tiên runtime, fallback biến public và ẩn khi thiếu', async () => {
  const previous = { runtime: process.env.PIPELINE_EGRESS_IP, public: process.env.NEXT_PUBLIC_EGRESS_IP };
  let requested = false, role = 'Engineer';
  const page = component('../app/connections/page.tsx', {
    'next/server': { connection: async () => { await Promise.resolve(); requested = true; } },
    '@/lib/catalog/source': { currentUser: async () => {
      assert.equal(requested, true, 'Trang phải chờ request trước khi render.');
      return { role };
    } },
    '../actions': { listUserConnections: async () => ({ data: [] }) },
    '../_components/connection-form': { ConnectionForm, ConnectionList }, '../_components/ui': ui,
  }).default as unknown as () => Promise<ReactNode>;
  try {
    for (const [runtime, publicIp, expected] of [
      [' 4.145.113.187 ', '203.0.113.10', '4.145.113.187'],
      ['198.51.100.20', undefined, '198.51.100.20'],
      [undefined, ' 203.0.113.10 ', '203.0.113.10'],
      ['', '203.0.113.10', '203.0.113.10'],
      ['   ', '203.0.113.10', '203.0.113.10'],
      [undefined, undefined, undefined],
      ['', '', undefined],
      ['   ', '   ', undefined],
    ]) {
      if (runtime === undefined) delete process.env.PIPELINE_EGRESS_IP;
      else process.env.PIPELINE_EGRESS_IP = runtime;
      if (publicIp === undefined) delete process.env.NEXT_PUBLIC_EGRESS_IP;
      else process.env.NEXT_PUBLIC_EGRESS_IP = publicIp;
      requested = false;
      const html = renderToStaticMarkup(await page());
      if (expected) assert.ok(html.includes(`Mở firewall của máy chủ Postgres cho IP máy chủ Atomesus: <strong>${expected}</strong>.`));
      else assert.doesNotMatch(html, /Mở firewall/);
      if (runtime?.trim()) assert.doesNotMatch(html, /203\.0\.113\.10/);
    }
    process.env.PIPELINE_EGRESS_IP = '4.145.113.187';
    role = 'Reviewer';
    requested = false;
    assert.doesNotMatch(renderToStaticMarkup(await page()), /Mở firewall|4\.145\.113\.187|<form/);
  } finally {
    if (previous.runtime === undefined) delete process.env.PIPELINE_EGRESS_IP;
    else process.env.PIPELINE_EGRESS_IP = previous.runtime;
    if (previous.public === undefined) delete process.env.NEXT_PUBLIC_EGRESS_IP;
    else process.env.NEXT_PUBLIC_EGRESS_IP = previous.public;
  }
});

test('giao diện: chọn schema có số bảng, ô tick có nhãn, xem cột và vô hiệu hóa lựa chọn rỗng', () => {
  const props = { id: mock.USER_CONNECTIONS[0].id, schemas: mock.USER_CONNECTION_SCHEMAS, initialSchema: 'public',
    initialTables: { data: mock.USER_CONNECTION_TABLES.public }, selection: mock.USER_CONNECTIONS[0].selection };
  const html = renderToStaticMarkup(createElement(ConnectionTables, props));
  assert.match(html, /public · 2 bảng/);
  assert.match(html, /type="checkbox"[^>]*checked/);
  assert.match(html, /for="connection-table-0"/);
  assert.match(html, /4 cột · Khóa chính: order_id · 12\.480 dòng ước tính/);
  assert.match(html, /<summary>Xem cột của orders<\/summary>/);
  assert.match(html, /Dùng các bảng này/);
  const empty = renderToStaticMarkup(createElement(ConnectionTables, { ...props, selection: null }));
  assert.match(empty, /<button[^>]*disabled[^>]*>Dùng các bảng này<\/button>/);
  const failure = renderToStaticMarkup(createElement(ConnectionTables, { ...props, initialTables: { error: 'User chưa có quyền SELECT.' } }));
  assert.match(failure, /role="alert">User chưa có quyền SELECT\./);
});
