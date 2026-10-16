import type { Limits } from './studio.ts';

/** GET /api/v1/pipeline/quota: hạn mức hôm nay của chính người đang đăng nhập; `limit` null khi không có trần riêng. */
export type Quota = {
  day: string; resets_at: string; limited: boolean;
  usd: { used: number; limit: number | null; remaining: number | null };
  runs: { used: number; limit: number | null; remaining: number | null };
};
/** Một dòng của khung "Giới hạn của bạn": câu thường, không nhắc tên file cấu hình. `pct` có khi có trần để vẽ thanh đo. */
export type LimitLine = { id: 'usd' | 'runs' | 'per_run' | 'reset'; label: string; text: string; pct?: number };

/** Mock cùng hình dạng GET /pipeline/quota; Admin không chịu trần riêng như backend (src/run_report/quota.py). */
export function mockQuota(role: string, now = new Date()): Quota {
  const day = now.toISOString().slice(0, 10);
  const resets = new Date(`${day}T00:00:00Z`);
  resets.setUTCDate(resets.getUTCDate() + 1);
  const limited = role !== 'Admin';
  const [usd, runs] = role === 'Engineer' ? [0.12, 4] : [0, 0];
  return {
    day, resets_at: resets.toISOString(), limited,
    usd: { used: usd, limit: limited ? 0.3 : null, remaining: limited ? Math.max(0, 0.3 - usd) : null },
    runs: { used: runs, limit: limited ? 20 : null, remaining: limited ? Math.max(0, 20 - runs) : null },
  };
}

const money = (n: number) => `$${n > 0 && n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`;
const pct = (used: number, limit: number) => limit > 0 ? Math.min(100, used / limit * 100) : 100;
const resetTime = (iso: string) => {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null
    : at.toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
};

export function limitLines(limits: Limits, quota: Quota | null): LimitLine[] {
  const perRun: LimitLine = {
    id: 'per_run', label: 'Mỗi run',
    text: `Mỗi run được gọi AI tối đa ${limits.max_llm_calls} lần, tự sửa Spec tối đa ${limits.max_spec_attempts} lần và trả lời tối đa ${limits.max_chat_calls} câu hỏi chat.`,
  };
  if (!quota) return [
    { id: 'usd', label: 'Chi phí hôm nay', text: 'Chưa đọc được mức dùng hôm nay của bạn. Thử tải lại trang sau ít phút.' },
    perRun,
  ];
  const { usd, runs } = quota;
  const reset = resetTime(quota.resets_at);
  return [
    usd.limit !== null
      ? { id: 'usd', label: 'Chi phí hôm nay', pct: pct(usd.used, usd.limit),
        text: `Hôm nay bạn đã dùng ${money(usd.used)} tiền gọi AI và quét dữ liệu, trên trần ${money(usd.limit)}; còn ${money(usd.remaining ?? 0)}.` }
      : { id: 'usd', label: 'Chi phí hôm nay',
        text: `Tài khoản của bạn không có trần chi phí riêng; hôm nay đã dùng ${money(usd.used)}. Cả hệ thống vẫn dừng khi tổng chi phí trong ngày chạm ${money(limits.max_usd_per_day)}.` },
    runs.limit !== null
      ? { id: 'runs', label: 'Số run hôm nay', pct: pct(runs.used, runs.limit),
        text: `Hôm nay bạn đã tạo ${runs.used} run, tối đa ${runs.limit} run mỗi ngày; còn ${runs.remaining ?? 0} lượt.` }
      : { id: 'runs', label: 'Số run hôm nay', text: `Tài khoản của bạn không giới hạn số run mỗi ngày; hôm nay đã tạo ${runs.used} run.` },
    perRun,
    ...(reset ? [{ id: 'reset' as const, label: 'Đặt lại', text: `Mức dùng hôm nay được đặt lại lúc ${reset} (giờ Việt Nam).` }] : []),
  ];
}
