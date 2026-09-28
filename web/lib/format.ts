const nf = new Intl.NumberFormat('vi-VN');

export const num = (n: number | null | undefined) => (n == null ? '—' : nf.format(n));

export function bytes(n: number | null | undefined): string {
  if (n == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i += 1; }
  return `${v.toLocaleString('vi-VN', { maximumFractionDigits: 1 })} ${units[i]}`;
}

export function usd(n: number | null | undefined): string {
  if (n == null) return '—';
  const digits = n !== 0 && n < 0.01 ? 6 : 4;
  return `${n.toLocaleString('vi-VN', { minimumFractionDigits: 2, maximumFractionDigits: digits })} USD`;
}

export function secs(n: number | null | undefined): string {
  if (n == null) return '—';
  return `${n.toLocaleString('vi-VN', { maximumFractionDigits: n < 1 ? 4 : 1 })}s`;
}

export function ago(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'vừa xong';
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`;
  return new Date(iso).toLocaleDateString('vi-VN');
}

export const shortHash = (h: string | null | undefined) => (h ? h.replace('sha256:', '').slice(0, 8) : '—');
