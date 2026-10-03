/**
 * Công sức của một run tính từ các mốc thời gian backend ghi (RunReport.timeline) và lúc duyệt (approval.at):
 * máy chạy bao lâu, người (chỉnh mapping, duyệt) mất bao lâu. Hàm thuần, có test.
 * Thời gian của người là thời gian thực trôi qua giữa hai mốc, nên gồm cả lúc đọc, cân nhắc và chờ.
 */

export type TimelineEvent = { event: string; at: string; actor?: string | null };
export type Segment = { key: 'agent_spec' | 'mapping' | 'agent_test' | 'review'; label: string; who: 'agent' | 'human'; sec: number };
export type Effort = { segments: Segment[]; agentSec: number; humanSec: number; totalSec: number; approved: boolean };

const at = (timeline: TimelineEvent[], name: string) => {
  const found = timeline.find(e => e.event === name);
  return found ? new Date(found.at).getTime() : null;
};
const span = (from: number | null, to: number | null) => from !== null && to !== null && to >= from ? (to - from) / 1000 : null;

export function effort(timeline: TimelineEvent[], approvedAt: string | null): Effort | null {
  const submitted = at(timeline, 'submitted');
  if (submitted === null) return null;
  const specReady = at(timeline, 'spec_ready'), mapping = at(timeline, 'mapping_submitted'), finished = at(timeline, 'finished');
  const approved = approvedAt ? new Date(approvedAt).getTime() : null;
  const parts: (Segment | null)[] = [
    toSegment('agent_spec', 'Agent lập kế hoạch và viết Spec', 'agent', span(submitted, specReady ?? finished)),
    toSegment('mapping', 'Engineer xem, chỉnh mapping và xác nhận Spec', 'human', span(specReady, mapping)),
    toSegment('agent_test', 'Agent sinh code và chạy thử', 'agent', specReady !== null ? span(mapping ?? specReady, finished) : null),
    toSegment('review', 'Reviewer xem bằng chứng và duyệt', 'human', span(finished, approved)),
  ];
  const segments = parts.filter((s): s is Segment => s !== null);
  const sum = (who: Segment['who']) => segments.filter(s => s.who === who).reduce((total, s) => total + s.sec, 0);
  return { segments, agentSec: sum('agent'), humanSec: sum('human'), totalSec: sum('agent') + sum('human'), approved: approved !== null };
}

function toSegment(key: Segment['key'], label: string, who: Segment['who'], sec: number | null): Segment | null {
  return sec === null ? null : { key, label, who, sec };
}

export function duration(sec: number): string {
  const s = Math.round(sec);
  if (s < 60) return `${s} giây`;
  const m = Math.floor(s / 60), rest = s % 60;
  if (m < 60) return rest ? `${m} phút ${rest} giây` : `${m} phút`;
  const h = Math.floor(m / 60);
  return `${h} giờ ${m % 60} phút`;
}

/** Khối lượng code agent sinh ra (số file, số dòng không rỗng) theo loại, thay cho việc engineer viết tay. */
export function codeVolume(files: Record<string, string>): { kind: string; files: number; lines: number }[] {
  const kinds: [string, (path: string) => boolean][] = [
    ['Tải dữ liệu (dlt, Python)', p => p.startsWith('ingestion/')],
    ['Làm sạch staging (dbt SQL)', p => p.includes('/models/staging/')],
    ['Bảng kết quả gold (dbt SQL)', p => p.includes('/models/gold/') && p.endsWith('.sql')],
    ['Test và tài liệu (dbt YAML)', p => p.endsWith('.yml')],
    ['Lịch chạy (Dagster, Python)', p => p.startsWith('infra/dagster/')],
  ];
  return kinds.map(([kind, match]) => {
    const picked = Object.entries(files).filter(([path]) => match(path));
    return { kind, files: picked.length, lines: picked.reduce((n, [, text]) => n + text.split('\n').filter(l => l.trim()).length, 0) };
  }).filter(k => k.files);
}
