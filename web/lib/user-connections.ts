export const SSL_MODES = ['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'] as const;
export type ConnectionRequest = {
  name: string; host: string; port: number; database: string; user: string; password: string; sslmode: typeof SSL_MODES[number];
};
export type ConnectionSelection = { schema: string; tables: string[] };
export type ConnectionSummary = { id: string; name: string };
export type UserConnection = ConnectionSummary & Omit<ConnectionRequest, 'password'> & {
  owner_id: string; created_at: number; selection?: ConnectionSelection | null; selection_error?: string;
};
export type ConnectionSchema = { name: string; table_count: number };
export type ConnectionTable = {
  name: string; columns: { name: string; type: string }[]; primary_key: string[]; row_estimate: number | null;
};
export type ConnectionResult<T> = { data: T } | { error: string };

/** Kiểm tra dữ liệu nhập; không đưa giá trị hoặc mật khẩu vào thông báo lỗi. */
export function validateConnectionForm(form: FormData): ConnectionResult<ConnectionRequest> {
  const text = (key: string) => typeof form.get(key) === 'string' ? String(form.get(key)) : '';
  const name = text('name').trim(), host = text('host').trim(), database = text('database').trim(), user = text('user').trim();
  const password = text('password'), rawPort = text('port').trim(), sslmode = text('sslmode');
  if (!name || !host || !database || !user || !password) return { error: 'Nhập đủ tên kết nối, host, database, user và mật khẩu.' };
  if (!/^\d+$/.test(rawPort) || Number(rawPort) < 1 || Number(rawPort) > 65535) return { error: 'Cổng phải là số nguyên từ 1 đến 65535.' };
  if (!SSL_MODES.includes(sslmode as ConnectionRequest['sslmode'])) return { error: 'Chọn sslmode hợp lệ.' };
  if (name.length > 120 || host.length > 253 || database.length > 128 || user.length > 128 || password.length > 4096
    || [name, host, database, user, password].some(value => value.includes('\0'))) {
    return { error: 'Thông tin kết nối quá dài hoặc chứa ký tự không hợp lệ.' };
  }
  return { data: { name, host, port: Number(rawPort), database, user, password, sslmode: sslmode as ConnectionRequest['sslmode'] } };
}

/** Kiểm tra cả cấu trúc vì Server Action có thể được gọi ngoài giao diện. */
export function validateConnectionSelection(value: unknown): ConnectionResult<ConnectionSelection> {
  const error = 'Chọn schema và ít nhất một bảng; danh sách bảng không được trùng.';
  if (!value || typeof value !== 'object') return { error };
  const { schema, tables } = value as Partial<ConnectionSelection>;
  if (typeof schema !== 'string' || !Array.isArray(tables) || !tables.length
    || new Set(tables).size !== tables.length
    || [schema, ...tables].some(name => typeof name !== 'string' || !name.trim() || name.length > 128 || name.includes('\0'))) {
    return { error };
  }
  return { data: { schema, tables } };
}

/** Mỗi bảng thuộc nguồn riêng c_<8 ký tự đầu id>; mã hóa tên để không tạo thêm tham số URL. */
export function connectionTablesLink(id: string, tables: string[]): string {
  const source = `c_${id.slice(0, 8)}`;
  return `/runs/new?tables=${[...new Set(tables)].map(table => encodeURIComponent(`${source}.${table}`)).join(',')}`;
}
