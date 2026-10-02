/** Phiên kết nối nguồn: cookie chỉ chứa tên nguồn (không có credential). Mở form / trang Quan hệ thì tự kết nối lại. */
export const CONNECTIONS_COOKIE = 'catalog_connections';
const REF = /^[a-z][a-z0-9_]{0,62}$/;

/** Giá trị cookie → danh sách tên nguồn hợp lệ, không trùng. */
export const parseConnections = (value: string | undefined) => [...new Set((value ?? '').split(',').filter(ref => REF.test(ref)))];
