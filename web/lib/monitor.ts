/**
 * Theo dõi pipeline đã deploy (Day 2): đọc `/monitor/pipelines` và `/monitor/alerts` của backend.
 * Hàm thuần ở đây (nhãn luật, so với đường cơ sở) có test; phần gọi backend nằm ở `getMonitor`.
 */

export type Finding = {
  rule: 'runtime_regression' | 'cost_regression' | 'test_degradation' | 'freshness_breach';
  observed: number; threshold: number; baseline: number | null; detail: string; stalled_sources: string[];
};
export type MonitorRun = {
  run_id: string; status: string; started_at: string; ended_at: string | null; duration_sec: number | null;
  gold_rows: number | null; usd: number | null; tests: number | null; tests_failed: number | null;
  source_records: Record<string, number> | { error: string }; new_records: Record<string, number> | null;
};
export type MonitoredPipeline = {
  model: string; domain: string | null; owner: string | null; deployed_run_id: string | null; schedule: string | null;
  baseline: { run_id: string; at: string; duration_sec: number | null; usd: number } | null;
  runs: number; last: MonitorRun | null; findings: Finding[];
};
export type Alert = {
  id: number; model: string; kind: 'source_stalled' | 'no_improvement' | 'needs_human'; message: string;
  detail: Record<string, unknown>; run_id: string | null; owner: string | null; at: string; resolved_at: string | null;
};

export const RULE_LABEL: Record<Finding['rule'], string> = {
  runtime_regression: 'Pipeline đang chậm', cost_regression: 'Chi phí tăng', test_degradation: 'Test fail tăng', freshness_breach: 'Không đủ tươi',
};
export const ALERT_LABEL: Record<Alert['kind'], string> = {
  source_stalled: 'Nguồn ngừng cấp dữ liệu', no_improvement: 'Không có bản tốt hơn', needs_human: 'Run sửa cần người xử lý',
};

/** Nhãn hiển thị của các luật đang kích hoạt; nguồn ngừng cấp tách thành nhãn riêng kèm tên nguồn. */
export function findingBadges(findings: Finding[]): { label: string; tone: 'bad' | 'amber'; detail: string }[] {
  return findings.map(f => f.stalled_sources.length
    ? { label: `Nguồn ngừng cấp dữ liệu: ${f.stalled_sources.join(', ')}`, tone: 'amber' as const, detail: f.detail }
    : { label: RULE_LABEL[f.rule] ?? f.rule, tone: 'bad' as const, detail: f.detail });
}

/** Các con số đầu trang, tính trực tiếp từ finding Người 4 trả về; mỗi pipeline chỉ được đếm một lần cho mỗi nhóm. */
export function monitorSummary(pipelines: MonitoredPipeline[]): { total: number; slow: number; stalled: number } {
  return {
    total: pipelines.length,
    slow: pipelines.filter(p => p.findings.some(f => f.rule === 'runtime_regression')).length,
    stalled: pipelines.filter(p => p.findings.some(f => f.stalled_sources.length > 0)).length,
  };
}

/** Thời gian lần chạy gần nhất so với đường cơ sở, ví dụ "+62%". Thiếu số thì null. */
export function versusBaseline(seconds: number | null | undefined, baseline: number | null | undefined): string | null {
  if (seconds == null || baseline == null || baseline <= 0) return null;
  const pct = Math.round((seconds / baseline - 1) * 100);
  return `${pct >= 0 ? '+' : ''}${pct}%`;
}

/** Bản ghi mới ở từng nguồn kể từ lần chạy trước; null nếu chưa có lần trước để so. */
export function newRecordsText(run: MonitorRun | null): string {
  if (!run) return '—';
  if ('error' in run.source_records) return `Không đọc được nguồn: ${run.source_records.error}`;
  if (!run.new_records) return 'lần đầu, chưa có mốc so';
  return Object.entries(run.new_records).map(([source, n]) => `${source} +${n.toLocaleString('vi-VN')}`).join(' · ');
}
