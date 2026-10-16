import { Check, CircleAlert, Clock3, LoaderCircle, Minus } from 'lucide-react';
import type { Run } from '@/lib/studio';
import { DEFAULT_DEPLOY_MODE, deploymentLink, deploymentReworks, deploymentSteps, deployPending, deployReason, type DeployMode } from '@/lib/deploy';
import { DeployRunButton, DeployWatch } from './run-controls';
import { Panel, fmtDate } from './ui';

export function DeployPanel({ run, role, mode = DEFAULT_DEPLOY_MODE }: { run: Run; role: string; mode?: DeployMode }) {
  if (!run.deployment && !['APPROVED', 'DEPLOYED', 'DEPLOY_FAILED'].includes(run.status)) return null;
  // Run đã có pull request (deploy ở chế độ pr trước đây) vẫn hiện tiến độ PR.
  const direct = mode.mode === 'direct' && !run.deployment;
  const steps = deploymentSteps(run, direct ? 'direct' : 'pr');
  const reworks = deploymentReworks(run);
  const pending = direct && deployPending(run);
  const flow = direct ? 'Duyệt → Gửi deployer → Chạy thử trên Dagster → Production' : 'Duyệt → Mở PR → CI → Merge → Production';
  return <div className="deployment-panel"><Panel title="Triển khai">
    <p className="small">Version {run.version} · {flow}</p>
    {direct && mode.gitops && <p className="small muted">Một pull request lưu vết (GitOps) được mở chạy nền; không chặn triển khai.</p>}
    {!direct && run.deployment ? <p className="small">Version này đã có pull request; xem liên kết và tiến độ bên dưới.</p>
      : run.status === 'APPROVED' && <DeployRunButton runId={run.id} reason={deployReason(run, role, direct ? 'direct' : 'pr')} direct={direct} />}
    {pending && <DeployWatch />}
    <ol className="deployment-steps" aria-label={`Tiến độ triển khai version ${run.version}`}>
      {steps.map(step => {
        const Icon = step.state === 'pass' ? Check : step.state === 'fail' ? CircleAlert : step.state === 'neutral' ? Minus : step.state === 'running' ? LoaderCircle : Clock3;
        return <li key={step.id}>
          <span className="q-icon" aria-hidden><Icon size={16} className={step.state === 'running' ? 'spin' : undefined} /></span>
          <div><strong>{step.label}</strong><p className="small">{step.detail}</p>
            {step.href && <a className="deployment-link" href={step.href} target="_blank" rel="noopener noreferrer">{step.id === 'pr' ? `Xem pull request #${run.deployment?.pr_number}` : 'Xem kết quả CI'}<span className="sr-only"> (mở tab mới)</span></a>}
            {step.at && <p className="small"><time dateTime={step.at}>{fmtDate(step.at)}</time></p>}
          </div>
        </li>;
      })}
    </ol>
    {reworks.map((item, index) => <div className="callout warn" key={`${item.version}-${index}`}><CircleAlert size={16} aria-hidden /><div>
      <strong>Triển khai {item.version ? `version ${item.version}` : 'version trước'}</strong><p>{item.message}</p>
      {deploymentLink(item.deployment.pr_url) && <a className="deployment-link" href={deploymentLink(item.deployment.pr_url)} target="_blank" rel="noopener noreferrer">Xem pull request #{item.deployment.pr_number}<span className="sr-only"> (mở tab mới)</span></a>}
      {deploymentLink(item.deployment.ci_url) && <a className="deployment-link" href={deploymentLink(item.deployment.ci_url)} target="_blank" rel="noopener noreferrer">Xem kết quả CI của PR #{item.deployment.pr_number}<span className="sr-only"> (mở tab mới)</span></a>}
    </div></div>)}
  </Panel></div>;
}
