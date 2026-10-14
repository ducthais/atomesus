import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_PAGE_SIZE, MOCK_RESULTS, MODEL_NAME, backendQuery, exportErrorCode, exportHref,pageCount, pageOf, parseQuery, queryString, rangeText, sortQuery, toCsv } from '../lib/results.ts';

test('kết quả: tham số trang lạ về mặc định, cỡ trang chỉ nhận giá trị có sẵn', () => {
  assert.deepEqual(parseQuery({}), { page: 1, pageSize: DEFAULT_PAGE_SIZE, sort: null, order: 'asc' });
  assert.deepEqual(parseQuery({ page: '3', size: '200', sort: 'revenue', order: 'desc' }), { page: 3, pageSize: 200, sort: 'revenue', order: 'desc' });
  for (const page of ['0', '-2', '1.5', 'abc']) assert.equal(parseQuery({ page }).page, 1, page);
  for (const size of ['201', '7', '1000', 'x']) assert.equal(parseQuery({ size }).pageSize, DEFAULT_PAGE_SIZE, size);
  assert.equal(parseQuery({ order: 'sideways' }).order, 'asc');
});

test('kết quả: bấm cột lần đầu tăng dần, bấm lại giảm dần, luôn về trang 1', () => {
  const q = parseQuery({ page: '4' });
  const asc = sortQuery(q, 'revenue');
  assert.deepEqual(asc, { page: 1, pageSize: DEFAULT_PAGE_SIZE, sort: 'revenue', order: 'asc' });
  assert.equal(sortQuery(asc, 'revenue').order, 'desc');
  assert.equal(sortQuery(sortQuery(asc, 'revenue'), 'revenue').order, 'asc');
  assert.equal(sortQuery({ ...asc, order: 'desc' }, 'order_date').order, 'asc');
});

test('kết quả: URL trang, URL backend và link tải giữ sắp xếp', () => {
  assert.equal(queryString(parseQuery({})), '');
  assert.equal(queryString({ page: 2, pageSize: 100, sort: 'a b', order: 'desc' }), '?page=2&size=100&sort=a+b&order=desc');
  assert.equal(backendQuery({ page: 2, pageSize: 25, sort: null, order: 'desc' }), 'page=2&page_size=25');
  assert.equal(backendQuery({ page: 1, pageSize: 50, sort: 'revenue', order: 'desc' }), 'page=1&page_size=50&sort=revenue&order=desc');
  assert.equal(exportHref('fct_daily_revenue', 'xlsx', { page: 3, pageSize: 50, sort: 'revenue', order: 'desc' }), '/results/fct_daily_revenue/export?format=xlsx&sort=revenue&order=desc');
  assert.equal(exportHref('m', 'csv', parseQuery({})), '/results/m/export?format=csv');
});

test('kết quả: số trang và dòng đang xem', () => {
  assert.equal(pageCount(0, 50), 1);
  assert.equal(pageCount(120, 50), 3);
  assert.equal(rangeText(2, 50, 50, 120), 'Dòng 51–100 / 120');
  assert.equal(rangeText(1, 50, 0, 0), '0 dòng');
  assert.match(rangeText(9, 50, 0, 120), /Không có dòng ở trang 9/);
});

test('kết quả: mã lỗi tải chỉ nhận khóa đã biết', () => {
  assert.equal(exportErrorCode(413), '413');
  assert.equal(exportErrorCode('503'), '503');
  for (const code of [500, 401, 'toString', 'constructor', '__proto__']) assert.equal(exportErrorCode(code), 'error', String(code));
});

test('kết quả: tên model cùng luật với backend', () => {
  for (const ok of ['fct_daily_revenue', '_m', 'A1']) assert.ok(MODEL_NAME.test(ok), ok);
  for (const bad of ['1m', 'a-b', 'gold.m', 'm"; drop', '', 'x'.repeat(64)]) assert.ok(!MODEL_NAME.test(bad), bad);
});

test('kết quả mock: sắp xếp NULL cuối, phân trang, cột lạ là 422', () => {
  const table = { columns: [{ name: 'id', type: 'INTEGER' }, { name: 'amount', type: 'DOUBLE' }], rows: [[1, 5], [2, null], [3, 9], [4, 1]] };
  const desc = pageOf(table, { page: 1, pageSize: 3, sort: 'amount', order: 'desc' });
  assert.ok(!('error' in desc));
  assert.deepEqual(desc.rows.map(r => r[0]), [3, 1, 4]);
  const second = pageOf(table, { page: 2, pageSize: 3, sort: 'amount', order: 'asc' });
  assert.ok(!('error' in second));
  assert.deepEqual([second.total, second.rows.map(r => r[0])], [4, [2]]);
  assert.deepEqual(pageOf(table, { page: 1, pageSize: 3, sort: 'price', order: 'asc' }), { status: 422, error: "Không có cột 'price' để sắp xếp." });
  const daily = pageOf(MOCK_RESULTS.fct_daily_revenue, parseQuery({}));
  assert.ok(!('error' in daily) && daily.total > DEFAULT_PAGE_SIZE && daily.rows.length === DEFAULT_PAGE_SIZE);
});

test('kết quả mock: CSV có BOM, quote đúng, NULL thành ô trống', () => {
  const csv = toCsv([{ name: 'city', type: 'VARCHAR' }, { name: 'note', type: 'VARCHAR' }], [['Hà Nội', 'a,"b"'], ['Huế', null]]);
  assert.ok(csv.startsWith('﻿city,note\r\n'));
  assert.equal(csv, '﻿city,note\r\nHà Nội,"a,""b"""\r\nHuế,\r\n');
});
