import type { ConnectionResult } from './user-connections.ts';
import type { Dataset, DeclaredSource, Discovery, Domain } from './catalog/types.ts';

export type UploadTable = { name: string; columns: string[]; column_count: number; row_count: number; size_bytes: number };
export type UserUpload = {
  id: string; owner_id: string; name: string; files: string[]; tables: UploadTable[];
  root: string; created_at: number; size_bytes: number; selected_tables: string[];
};
export type UploadLimits = { max_file_mb: number; max_total_mb_per_user: number; used_bytes: number };
export type UploadFileInfo = { name: string; size: number };

export function validateUploadFiles(files: UploadFileInfo[], limits: UploadLimits): string | null {
  if (!Array.isArray(files) || !files.length) return 'Chọn ít nhất một file CSV hoặc XLSX.';
  if (files.some(file => !file || typeof file.name !== 'string' || !Number.isFinite(file.size) || file.size <= 0)) return 'File rỗng hoặc không hợp lệ.';
  if (files.some(file => !/\.(csv|xlsx)$/i.test(file.name))) return 'Chỉ nhận file CSV hoặc XLSX.';
  if (files.some(file => file.size > limits.max_file_mb * 1024 ** 2)) return `Mỗi file tối đa ${limits.max_file_mb} MB.`;
  if (files.reduce((size, file) => size + file.size, limits.used_bytes) > limits.max_total_mb_per_user * 1024 ** 2) {
    return `Tổng dung lượng của bạn tối đa ${limits.max_total_mb_per_user} MB. Xóa file cũ rồi thử lại.`;
  }
  return null;
}

export function validateUploadSelection(value: unknown): ConnectionResult<string[]> {
  if (!Array.isArray(value) || !value.length || new Set(value).size !== value.length
    || value.some(name => typeof name !== 'string' || !/^[a-z_][a-z0-9_]{0,62}$/.test(name))) {
    return { error: 'Chọn ít nhất một tên bảng hợp lệ, không trùng.' };
  }
  return { data: value };
}

export function uploadTablesLink(sourceRef: string, tables: string[]): string {
  if (!/^f_[a-f0-9]{8,32}$/.test(sourceRef) || 'error' in validateUploadSelection(tables)) return '/runs/new';
  return `/runs/new?tables=${tables.map(name => `${sourceRef}.${name}`).join(',')}`;
}

export function uploadError(status: number, body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') return body.detail;
  if (status === 413) return 'File vượt dung lượng cho phép. Kiểm tra giới hạn hoặc chọn file nhỏ hơn.';
  if (status === 401) return 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.';
  return 'Không tải được file nguồn. Vui lòng thử lại.';
}

export const MOCK_UPLOAD_LIMITS: UploadLimits = { max_file_mb: 20, max_total_mb_per_user: 200, used_bytes: 0 };
export const MOCK_UPLOADS: UserUpload[] = [{
  id: 'f12ab345678901234567890123456789', owner_id: 'minh', name: 'Doanh thu tháng 10.xlsx',
  files: ['Doanh thu tháng 10.xlsx'], root: 'data/uploads/minh/f12ab345678901234567890123456789',
  created_at: 1791849600, size_bytes: 18432, selected_tables: ['don_hang'],
  tables: [
    { name: 'don_hang', columns: ['order_id', 'customer_id', 'amount', 'ordered_at'], column_count: 4, row_count: 1248, size_bytes: 14336 },
    { name: 'khach_hang', columns: ['customer_id', 'name', 'city'], column_count: 3, row_count: 320, size_bytes: 4096 },
  ],
}];

export function makeMockUpload(files: UploadFileInfo[], ownerId: string, id: string): UserUpload {
  const used = new Set<string>();
  const tables = files.flatMap(file => {
    const base = file.name.replace(/\.(csv|xlsx)$/i, '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[đĐ]/g, 'd')
      .replace(/[^a-zA-Z0-9_]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase().slice(0, 48) || 'table';
    return MOCK_UPLOADS[0].tables.slice(0, /\.xlsx$/i.test(file.name) ? 2 : 1).map((table, index) => {
      const stem = `${/^\d/.test(base) ? 't_' : ''}${base}${index ? '_khach_hang' : ''}`.slice(0, 55);
      let name = stem, suffix = 2;
      while (used.has(name)) name = `${stem}_${suffix++}`;
      used.add(name);
      return { ...table, name };
    });
  });
  return { id, owner_id: ownerId, name: files.map(file => file.name).join(', ').slice(0, 120), files: files.map(file => file.name.slice(0, 120)),
    tables, root: `data/uploads/${ownerId}/${id}`, created_at: Date.now() / 1000,
    size_bytes: files.reduce((size, file) => size + file.size, 0), selected_tables: [] };
}

export function mockUploadCatalog(uploads: UserUpload[]): { domains: Domain[]; datasets: Dataset[] } {
  const domains: Domain[] = [], datasets: Dataset[] = [];
  for (const upload of uploads.filter(item => item.selected_tables.length)) {
    const domain = `u_${upload.id.slice(0, 8)}`, ref = `f_${upload.id.slice(0, 8)}`;
    const selected = upload.tables.filter(table => upload.selected_tables.includes(table.name));
    const ids = selected.map(table => `${domain}.source.${ref}.${table.name}`);
    domains.push({ id: domain, name: upload.name, description: 'File nguồn riêng đã chọn bảng.', steward: upload.owner_id,
      currency: 'VND', datasets: ids, sources: [{ ref, kind: 'filesystem', connection: `upload:${upload.id}`, location: upload.root, available: true }] });
    selected.forEach((table, index) => datasets.push({ id: ids[index], name: `${ref}.${table.name}`, layer: 'source', platform: 'filesystem',
      domain, sourceRef: ref, description: 'Bảng từ file tải lên (mock).', owner: upload.owner_id, tags: ['file nguồn'], terms: [],
      columns: table.columns.map(name => ({ name, type: 'text' })), rowCount: table.row_count, updatedAt: null,
      upstream: [], quality: [], sample: [], runs: [] }));
  }
  return { domains, datasets };
}

export function mockUploadSources(uploads: UserUpload[]): DeclaredSource[] {
  return mockUploadCatalog(uploads).domains.flatMap(domain => domain.sources.map(source => ({
    ref: source.ref, domain: domain.id, kind: source.kind, location: source.location, supported: true,
  })));
}

export function mockUploadDiscovery(uploads: UserUpload[], ref: string): Discovery | undefined {
  const upload = uploads.find(item => `f_${item.id.slice(0, 8)}` === ref && item.selected_tables.length);
  if (!upload) return undefined;
  return { discoveredAt: new Date().toISOString(), foreignKeys: [],
    objects: upload.tables.filter(table => upload.selected_tables.includes(table.name)).map(table => ({
      name: table.name, kind: 'folder', columns: table.columns.map(name => ({ name, type: 'text' })), primaryKey: [], rowEstimate: table.row_count,
    })) };
}
