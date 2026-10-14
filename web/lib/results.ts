/**
 * Xem và tải kết quả pipeline (ADR-0001 D9): bảng gold đọc qua `/results/{model}` của backend.
 * Hàm thuần ở đây (đọc tham số trang, link sắp xếp, phân trang, dữ liệu mock) có test; phần gọi backend ở `getResults`.
 */

export const PAGE_SIZES = [25, 50, 100, 200] as const;
export const DEFAULT_PAGE_SIZE = 50;
export const MAX_PAGE_SIZE = 200;
/** Tên model dbt hợp lệ, cùng luật với backend; tên khác không gọi backend. */
export const MODEL_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

export type Order = 'asc' | 'desc';
export type ResultColumn = { name: string; type: string };
export type Cell = string | number | boolean | null;
export type ResultPage = { columns: ResultColumn[]; rows: Cell[][]; total: number; page: number; page_size: number };
export type ResultQuery = { page: number; pageSize: number; sort: string | null; order: Order };
export type ExportFormat = 'csv' | 'xlsx';

/** Lý do tải không được, theo mã lỗi backend (route tải chuyển về trang với `?export=<mã>`); không nhận chữ tùy ý từ URL. */
export const EXPORT_ERROR: Record<string, string> = {
  '413': 'Bảng có nhiều dòng hơn trần được tải (results.max_export_rows trong policy/budgets.yml). Vẫn xem được từng trang ở đây.',
  '503': 'Pipeline đang chạy, thử lại sau ít phút.',
  '404': 'Không có kết quả này hoặc bạn không được xem.',
  '501': 'Chế độ mock chỉ tạo được file CSV.',
  error: 'Không tải được file, thử lại sau.',
};

/** Mã lỗi tải đã biết, còn lại là 'error'; chỉ nhận khóa của chính EXPORT_ERROR (không lấy 'toString'...). */
export const exportErrorCode = (status: number | string) => Object.hasOwn(EXPORT_ERROR, String(status)) ? String(status) : 'error';

const positive =(raw: string | undefined, fallback: number) => {
  const n = Number(raw);
  return Number.isInteger(n) && n >= 1 ? n : fallback;
};

/** Tham số trang từ URL: số trang ≥ 1, cỡ trang trong PAGE_SIZES, chiều sắp xếp asc/desc. */
export function parseQuery(params: { page?: string; size?: string; sort?: string; order?: string }): ResultQuery {
  const size = positive(params.size, DEFAULT_PAGE_SIZE);
  return {
    page: positive(params.page, 1),
    pageSize: (PAGE_SIZES as readonly number[]).includes(size) ? size : DEFAULT_PAGE_SIZE,
    sort: params.sort || null,
    order: params.order === 'desc' ? 'desc' : 'asc',
  };
}

/** Query string của trang kết quả; bỏ giá trị mặc định cho URL gọn. */
export function queryString(q: ResultQuery, extra: Record<string, string> = {}): string {
  const search = new URLSearchParams();
  if (q.page !== 1) search.set('page', String(q.page));
  if (q.pageSize !== DEFAULT_PAGE_SIZE) search.set('size', String(q.pageSize));
  if (q.sort) search.set('sort', q.sort);
  if (q.sort && q.order === 'desc') search.set('order', 'desc');
  for (const [key, value] of Object.entries(extra)) search.set(key, value);
  const text = search.toString();
  return text ? `?${text}` : '';
}

/** Bấm tiêu đề cột: cột mới thì tăng dần, bấm lại thì giảm dần; luôn về trang 1. */
export function sortQuery(q: ResultQuery, column: string): ResultQuery {
  const order: Order = q.sort === column && q.order === 'asc' ? 'desc' : 'asc';
  return { ...q, page: 1, sort: column, order };
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

/** "Dòng 51–100 / 365"; trang rỗng thì "0 dòng". */
export function rangeText(page: number, pageSize: number, shown: number, total: number): string {
  if (!shown) return total ? `Không có dòng ở trang ${page} / ${total.toLocaleString('vi-VN')} dòng` : '0 dòng';
  const first = (page - 1) * pageSize + 1;
  return `Dòng ${first.toLocaleString('vi-VN')}–${(first + shown - 1).toLocaleString('vi-VN')} / ${total.toLocaleString('vi-VN')}`;
}

/** Link tải qua route của web (gửi kèm phiên đăng nhập), giữ thứ tự sắp xếp đang xem. */
export function exportHref(model: string, format: ExportFormat, q: ResultQuery): string {
  const search = new URLSearchParams({ format });
  if (q.sort) { search.set('sort', q.sort); search.set('order', q.order); }
  return `/results/${encodeURIComponent(model)}/export?${search}`;
}

/** Tham số gọi backend: page, page_size, sort, order. */
export function backendQuery(q: ResultQuery): string {
  const search = new URLSearchParams({ page: String(q.page), page_size: String(q.pageSize) });
  if (q.sort) { search.set('sort', q.sort); search.set('order', q.order); }
  return search.toString();
}

const compare = (a: Cell, b: Cell) => a === b ? 0 : a === null ? 1 : b === null ? -1 : a < b ? -1 : 1;

/** Sắp xếp và cắt trang trong bộ nhớ (chế độ mock), cùng quy ước với backend: NULL luôn cuối. */
export function pageOf(table: { columns: ResultColumn[]; rows: Cell[][] }, q: ResultQuery): ResultPage | { error: string; status: number } {
  const index = q.sort ? table.columns.findIndex(c => c.name === q.sort) : -1;
  if (q.sort && index < 0) return { status: 422, error: `Không có cột '${q.sort}' để sắp xếp.` };
  const rows = index < 0 ? table.rows : [...table.rows].sort((a, b) => {
    if (a[index] === null || b[index] === null) return compare(a[index], b[index]);
    return q.order === 'desc' ? compare(b[index], a[index]) : compare(a[index], b[index]);
  });
  const start = (q.page - 1) * q.pageSize;
  return { columns: table.columns, rows: rows.slice(start, start + q.pageSize), total: rows.length, page: q.page, page_size: q.pageSize };
}

/** CSV UTF-8 có BOM như backend, để chế độ mock cũng tải được file mở đúng tiếng Việt trong Excel. */
export function toCsv(columns: ResultColumn[], rows: Cell[][]): string {
  const field = (v: Cell) => {
    const text = v === null ? '' : String(v);
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return '﻿' + [columns.map(c => field(c.name)), ...rows.map(r => r.map(field))].map(r => r.join(',')).join('\r\n') + '\r\n';
}

const CITIES = ['Hà Nội', 'TP. Hồ Chí Minh', 'Đà Nẵng', 'Cần Thơ', 'Hải Phòng'];

/** Bảng gold mẫu cho chế độ mock (CATALOG_MOCK=1), khớp hai dataset gold trong lib/catalog/mock.ts. */
export const MOCK_RESULTS: Record<string, { columns: ResultColumn[]; rows: Cell[][] }> = {
  fct_daily_revenue: {
    columns: [{ name: 'order_date', type: 'DATE' }, { name: 'revenue', type: 'DECIMAL(18,2)' }, { name: 'order_count', type: 'BIGINT' }],
    rows: Array.from({ length: 120 }, (_, i) => {
      const day = new Date(Date.UTC(2026, 5, 4 + i)).toISOString().slice(0, 10);
      const orders = 30 + (i * 7) % 19;
      return [day, Math.round(orders * (1_050_000 + (i * 37_000) % 400_000) / 1000) * 1000, orders];
    }),
  },
  fct_customer_revenue: {
    columns: [{ name: 'customer_id', type: 'BIGINT' }, { name: 'full_name', type: 'VARCHAR' }, { name: 'city', type: 'VARCHAR' }, { name: 'revenue', type: 'DECIMAL(18,2)' }, { name: 'order_count', type: 'BIGINT' }],
    rows: Array.from({ length: 64 }, (_, i) => [501 + i, i % 9 === 4 ? null : `Khách hàng ${i + 1} ***`, CITIES[i % CITIES.length], (i * 1_370_000) % 19_000_000 + 450_000, 1 + (i * 5) % 13]),
  },
};
