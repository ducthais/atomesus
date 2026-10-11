import type { Deployment, RunReport } from './postgres-api.ts';
import type { Run } from './studio.ts';

export const APPROVAL_MESSAGE = 'Đã duyệt, bấm Deploy để mở pull request';
const CI_FAILED = ['failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure'];
export type DeployStep = {
  id: 'approval' | 'pr' | 'ci' | 'merge' | 'production'; label: string;
  state: 'pass' | 'fail' | 'neutral' | 'running' | 'pending'; detail: string; at?: string | null; href?: string;
};
type DeployRun = Pick<Run, 'status' | 'approval' | 'version' | 'deployment' | 'deploymentHistory' | 'deployError'>;

/** Chỉ mở PR cho version đã duyệt và chưa có PR; backend vẫn kiểm quyền và chữ ký khi nhận POST. */
export function deployReason(run: Pick<Run, 'status' | 'deployment'>, role: string): string | null {
  if (role !== 'Reviewer') return 'Chỉ Reviewer được Deploy.';
  if (run.status !== 'APPROVED') return 'Chỉ Deploy khi run đã duyệt (APPROVED).';
  if (run.deployment) return 'Version này đã có pull request; xem tiến độ bên dưới.';
  return null;
}

/** Link từ báo cáo chỉ được mở bằng HTTP(S), tránh URL thực thi trong dữ liệu lịch sử. */
export function deploymentLink(url?: string | null): string | undefined {
  if (!url) return undefined;
  try { return ['https:', 'http:'].includes(new URL(url).protocol) ? url : undefined; }
  catch { return undefined; }
}

export async function requestDeployment(runId: string, request: <T>(path: string, init?: RequestInit) => Promise<T>): Promise<RunReport> {
  if (typeof runId !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(runId)) throw new Error('Mã run không hợp lệ.');
  return request<RunReport>(`/runs/${encodeURIComponent(runId)}/deploy`, { method: 'POST' });
}

/** Mỗi bước chỉ đánh dấu hoàn tất khi có bằng chứng; run cũ thiếu deployment vẫn đọc được. */
export function deploymentSteps(run: DeployRun): DeployStep[] {
  const d = run.deployment;
  const approved = run.approval?.decision === 'approved' || !!d || ['APPROVED', 'DEPLOYED', 'DEPLOY_FAILED'].includes(run.status);
  const ciFailed = !!d?.ci_conclusion && CI_FAILED.includes(d.ci_conclusion);
  const ciNeutral = d?.ci_conclusion === 'neutral' || d?.ci_conclusion === 'skipped';
  const ciRunning = !!d && !d.ci_conclusion && d.state !== 'closed';
  const merged = d?.state === 'merged' || !!d?.merged_at;
  const deployed = !!d?.deployed_at || run.status === 'DEPLOYED';
  // Mở PR lỗi vẫn giữ APPROVED, kể cả còn PR cũ đã đóng; đã DEPLOYED thì ẩn lỗi cũ.
  const failed = !deployed && (run.status === 'DEPLOY_FAILED' || (run.status === 'APPROVED' && (!d || d.state === 'closed') && !!run.deployError));
  return [
    { id: 'approval', label: approved ? 'Đã duyệt' : 'Chờ duyệt', state: approved ? 'pass' : 'pending',
      detail: approved ? 'Chữ ký duyệt gắn với bộ file của version này.' : 'Cần Reviewer phê duyệt version này.', at: run.approval?.at },
    { id: 'pr', label: d?.state === 'closed' ? 'PR đã đóng' : d ? 'Đã mở PR' : 'Chờ mở PR', state: d?.state === 'closed' ? 'fail' : d ? 'pass' : 'pending',
      detail: d ? `Pull request #${d.pr_number} · ${d.repo}` : approved && !deployed ? 'Bấm Deploy để mở pull request.' : 'Chưa có thông tin pull request.',
      at: d?.state === 'closed' ? d.closed_at : d?.opened_at, href: deploymentLink(d?.pr_url) },
    { id: 'ci', label: ciFailed ? 'CI đỏ' : d?.ci_conclusion === 'success' ? 'CI xanh' : d?.ci_conclusion === 'neutral' ? 'CI trung lập' : d?.ci_conclusion === 'skipped' ? 'CI đã bỏ qua' : ciRunning ? 'CI đang chạy' : 'CI chưa có kết quả',
      state: ciFailed ? 'fail' : d?.ci_conclusion === 'success' ? 'pass' : ciNeutral ? 'neutral' : ciRunning ? 'running' : 'pending',
      detail: d?.ci_conclusion ? `Kết luận: ${d.ci_conclusion}` : d ? 'Đang chờ kết quả CI từ GitHub.' : 'Chưa có thông tin CI.', at: d?.ci_at, href: deploymentLink(d?.ci_url) },
    { id: 'merge', label: merged ? 'Đã merge' : 'Chờ merge', state: merged ? 'pass' : 'pending',
      detail: merged ? 'Pull request đã được merge vào nhánh production.' : d?.state === 'closed' ? 'PR bị đóng mà không merge.' : 'Cần CI xanh và người có quyền merge.', at: d?.merged_at },
    { id: 'production', label: failed ? 'Triển khai lỗi' : deployed ? 'Đã lên production' : 'Chờ lên production', state: failed ? 'fail' : deployed ? 'pass' : merged ? 'running' : 'pending',
      detail: failed ? run.deployError || 'Deployer báo triển khai thất bại; xem log.' : deployed ? d?.deployed_at ? 'Deployer đã xác nhận cài đặt lên production.' : 'Run cũ đã lên production; chưa lưu thời điểm triển khai.' : 'Chờ deployer xác nhận cài đặt lên production.', at: d?.deployed_at },
  ];
}

/** PR cũ được backend giữ trong spec_history sau khi CI đỏ / PR đóng tạo version mới. */
export function deploymentReworks(run: DeployRun): { version?: number; deployment: Deployment; message: string }[] {
  return (run.deploymentHistory ?? []).flatMap(item => {
    const d = item.deployment;
    if (!d || (d.state !== 'closed' && !CI_FAILED.includes(d.ci_conclusion ?? ''))) return [];
    return [{ version: item.version, deployment: d, message: `${d.state === 'closed' ? 'PR bị đóng mà không merge' : `CI đỏ (${d.ci_conclusion})`}; đã tạo version mới để sửa và duyệt lại.` }];
  });
}
