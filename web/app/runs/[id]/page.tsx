import Link from 'next/link';
import { notFound } from 'next/navigation';
import { CircleAlert, GitBranch, RotateCcw } from 'lucide-react';
import { currentUser, getCatalog, getComparison, runFiles, runMessages } from '@/lib/catalog/source';
import { STAGES, STAGE_IDS, canReview, shortId } from '@/lib/studio';
import { CodeViewer, ReviewPanel } from '../../_components/run-client';
import { RunResultView } from '../../_components/run-result';
import { MappingEditor } from '../../_components/mapping-editor';
import { RunProgress } from '../../_components/run-progress';
import { RunChat } from '../../_components/run-chat';
import { EffortPanel } from '../../_components/effort';
import type { AgentSpec } from '@/lib/postgres-api';
import { RunCompareView, impactNotes } from '../../_components/run-compare';
import { Empty, Facts, LayerBadge, PageHeader, Panel, Person, QualityIcon, StatusBadge, Tabs, fmtDate, statusTone } from '../../_components/ui';

/** Luật áp dụng khi run dừng, để người đọc biết sửa ở đâu. */
const RULES: Partial<Record<string, string>> = {
  BLOCKED_AST: 'Cổng ast_guard (src/codegen/guard.py): biểu thức chỉ được dùng cột và hàm cho phép.',
  BLOCKED_SEMANTIC_CONFLICT: 'policy/domains.yml: thực thể trùng tên ở hai domain, conformed_dimensions chưa công nhận.',
  BLOCKED_BUDGET: 'policy/budgets.yml: per_run.max_bytes_scanned, max_usd.',
  NEEDS_HUMAN: 'policy/budgets.yml (max_spec_attempts, max_llm_calls), objectives.yml (quality.max_reject_pct), hoặc nguồn chưa hỗ trợ.',
  INFRA_ERROR: 'Kết nối nguồn hoặc nhà cung cấp LLM. Không phải lỗi của Spec.',
  INTERNAL_ERROR: 'Lỗi của hệ thống (template/sandbox), không phải lỗi agent.',
};
const RECONCILE_RULE = 'kết quả chạy thử phải khớp phép tính lại thẳng trên nguồn (metric_reconcile, src/codegen/reference.py); lệch thì không chuyển cho Reviewer.';
const LEVEL_LABEL = { info: 'Thông tin', success: 'Đạt', warning: 'Cảnh báo', error: 'Lỗi' } as const;

export default async function RunPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string; base?: string }> }) {
  const [{ id }, { tab: rawTab, base: compareBase }] = await Promise.all([params, searchParams]);
  const [cat, user] = await Promise.all([getCatalog(), currentUser()]);
  const run = cat.run(id);
  if (!run || !user) notFound();
  const policy = cat.policy;
  const datasets = cat.datasetsForRun(run);
  const files = run.fileCount;
  const passed = run.checks.filter(c => c.state === 'pass').length;
  const result = run.result ?? null;
  // Các phiên bản khác của cùng pipeline (cùng domain + model) để so sánh.
  const versions = result ? cat.runs.filter(r => r.domain === run.domain && r.result?.model === result.model && r.fileCount > 0)
    .map(r => ({ id: r.id, label: `${shortId(r.id)} · ${fmtDate(r.updatedAt)}${r.status === 'DEPLOYED' ? ' (đang chạy)' : ''}` })) : [];
  const comparable = versions.some(v => v.id !== run.id);
  // Agent chạy nền: trang tự làm mới tới khi run dừng (DRAFT là nháp chờ người sửa, không phải đang chạy).
  const inProgress = run.status === 'GENERATING' || run.status === 'VALIDATING';
  const draftSpec = run.status === 'DRAFT' && run.spec && typeof run.spec === 'object' && 'sources' in run.spec ? run.spec as AgentSpec : null;
  const tabs = [
    ...(draftSpec ? [{ id: 'mapping', label: 'Mapping' }] : []),
    ...(result && !draftSpec && !inProgress ? [{ id: 'result', label: 'Kết quả' }] : []),
    { id: 'overview', label: 'Tổng quan' },
    { id: 'chat', label: 'Hỏi đáp' },
    { id: 'evidence', label: 'Bằng chứng', count: run.checks.length },
    ...(files ? [{ id: 'code', label: 'Code', count: files }] : []),
    { id: 'logs', label: 'Log', count: run.logs.length },
    ...(comparable ? [{ id: 'compare', label: 'So sánh' }] : []),
  ];
  const tab = tabs.some(t => t.id === rawTab) ? rawTab! : tabs[0].id;
  // So sánh dùng cho tab So sánh, và cho tóm tắt ảnh hưởng trong khung duyệt của run đang chờ duyệt.
  const comparison = comparable && (tab === 'compare' || (tab === 'overview' && run.status === 'READY_FOR_REVIEW')) ? await getComparison(run.id, tab === 'compare' ? compareBase : undefined) : null;
  const notes = comparison?.available ? impactNotes(comparison) : [];
  const overviewFiles = tab === 'overview' && run.fileCount && run.timeline?.length ? await runFiles(run) : null;
  const messages = tab === 'chat' ? await runMessages(run.id) : [];
  const base = `/runs/${shortId(run.id)}`;
  const eligible = canReview(run, user.role, user.id);
  const reviewReason = eligible ? 'Duyệt sẽ ghi đúng bộ file này thành project Dagster local.' : user.role !== 'Reviewer' ? `Bạn đang là ${user.role}. Chỉ Reviewer duyệt run.` : run.owner === user.id ? 'Người tạo run không tự duyệt.' : 'Mọi kiểm tra phải đạt trước khi duyệt.';
  const blocked = statusTone(run.status) === 'bad';
  const limits = policy.limits;

  return <>
    <nav className="breadcrumbs" aria-label="Vị trí"><Link href="/runs">Hoạt động</Link><span>/</span><span className="mono">{shortId(run.id)}</span></nav>
    <PageHeader icon={<GitBranch size={22} />} kicker={<><StatusBadge status={run.status} /><span className="meta-tag">v{run.version}</span><span className="meta-tag">Phase {run.stage + 1}/{STAGES.length} · {STAGES[run.stage]}</span></>} title={run.title}
      actions={<Link href={`/runs/new?request=${encodeURIComponent(run.request)}&parent=${run.id}`} className="button"><RotateCcw size={15} aria-hidden /> Sửa & chạy lại</Link>}>
      <p className="muted"><Person name={cat.personName(run.owner)} /> tạo · cập nhật {fmtDate(run.updatedAt)} · <span className="mono">{run.id}</span></p>
    </PageHeader>

    {blocked && run.error && <div className="callout bad"><CircleAlert size={16} aria-hidden /><div><strong>Run dừng ở “{STAGES[run.stage]}”</strong><p>{run.error}</p>{(run.error.startsWith('Đối chiếu độc lập') ? RECONCILE_RULE : RULES[run.status]) && <p className="muted small">Luật áp dụng: {run.error.startsWith('Đối chiếu độc lập') ? RECONCILE_RULE : RULES[run.status]}</p>}</div></div>}

    {inProgress && <RunProgress status={run.status} stage={STAGE_IDS[run.stage] ?? 'intake'} startedAt={run.updatedAt} specRetries={run.specRetries} />}

    <Tabs base={base} active={tab} tabs={tabs} />

    {tab === 'result' && result && <RunResultView result={result} summary={run.summary} request={run.request}
      goldId={cat.datasets.find(d => d.name === result.goldTable && d.domain === run.domain)?.id ?? null} deployed={run.status === 'DEPLOYED'} />}

    {tab === 'mapping' && draftSpec && <MappingEditor runId={run.id} spec={draftSpec} canEdit={user.role === 'Engineer' && user.id === run.owner}
      sourceColumns={Object.fromEntries(draftSpec.sources.map(s => {
        const table = s.table ?? s.id;
        const dataset = cat.datasetByName(`${s.source_ref}.${table}`);
        return [s.id, { table, sourceRef: s.source_ref, columns: (dataset?.columns ?? []).map(c => ({ name: c.name, type: c.type })) }];
      }))} />}

    {tab === 'chat' && <RunChat runId={run.id} messages={messages} limit={limits.max_chat_calls}
      people={Object.fromEntries(messages.map(m => [m.author, cat.personName(m.author)]))}
      disabledReason={inProgress ? 'Agent đang chạy; hỏi sau khi run dừng để câu trả lời đọc từ RunReport đầy đủ.' : null} />}

    {tab === 'compare' && comparison && <RunCompareView comparison={comparison} runId={run.id} versions={versions.filter(v => v.id !== run.id)} />}

    {tab === 'overview' && <div className="detail-grid">
      <div className="stack">
        {run.timeline && run.timeline.length > 0 && <EffortPanel timeline={run.timeline} approvedAt={run.approval?.at ?? null} files={overviewFiles} />}
        <Panel title="Yêu cầu"><blockquote>{run.request}</blockquote>{run.summary && <><h3>Kế hoạch của agent</h3><p>{run.summary}</p></>}</Panel>
        <Panel title="Spec">
          <Facts items={[['Nguồn', run.plan.sources.join(', ') || '—'], ['Bảng', run.plan.tables.join(', ') || '—'], ['Khóa chính', run.plan.primaryKey], ['Cursor', run.plan.cursor],
            ['Chế độ ghi', run.plan.writeMode], ['Model', <code key="m">{run.plan.model}</code>], ['Chỉ số', run.plan.metric], ['Bộ lọc', run.plan.filters || 'Không có'], ['Lịch', <code key="s">{run.plan.schedule}</code>]]} />
        </Panel>
      </div>
      <div className="stack">
        {(run.status === 'READY_FOR_REVIEW' || run.approval) && <ReviewPanel runId={run.id} approval={run.approval ? { ...run.approval, reviewer: cat.personName(run.approval.reviewer) } : null} eligible={eligible} reason={reviewReason} filesHash={run.filesHash} version={run.version}
          impact={notes} compareHref={comparable ? `/runs/${shortId(run.id)}?tab=compare` : undefined} />}
        <Panel title="Dataset liên quan">{datasets.length ? <ul className="plain">{datasets.map(d => <li key={d.id}><Link href={`/datasets/${d.id}`} className="dataset-link"><LayerBadge layer={d.layer} /><span className="mono">{d.name}</span></Link></li>)}</ul> : <p className="muted">Run dừng trước khi tạo dataset.</p>}</Panel>
        <Panel title="Tài nguyên đã dùng">
          <div className="counters">
            {[['Sửa Spec', run.specRetries, limits.max_spec_attempts], ['Sửa SQL', run.sqlRetries, limits.max_sql_attempts], ['Gọi LLM', run.llmCalls, limits.max_llm_calls]].map(([label, used, max]) =>
              <div key={label}><span>{label}</span><strong className="mono">{used}/{max}</strong><div className="meter"><span style={{ width: `${Math.min(100, Number(used) / Number(max) * 100)}%` }} /></div></div>)}
            <div><span>Chi phí</span><strong className="mono">${run.usd.toFixed(4)}</strong><small className="muted">ước tính</small></div>
          </div>
          <p className="muted small">Trần đọc từ budgets.yml.</p>
        </Panel>
      </div>
    </div>}

    {tab === 'evidence' && (run.checks.length ? <>
      <p className="muted section-note">{passed}/{run.checks.length} đạt. Mọi con số truy được về một phép đo trong RunReport.</p>
      <Panel flush><div className="table-scroll"><table><thead><tr><th>Kết quả</th><th>Kiểm tra</th><th>Giá trị đo</th><th>Chi tiết</th></tr></thead>
        <tbody>{run.checks.map(c => <tr key={c.id}><td><QualityIcon state={c.state === 'running' ? 'unmeasured' : c.state} /></td><td><strong>{c.name}</strong><br /><code className="muted">{c.id}</code></td><td className="mono">{c.value}</td><td>{c.detail}</td></tr>)}</tbody></table></div></Panel>
      {run.assets.length > 0 && <Panel title="Bảng tạo ra trong sandbox" flush><div className="table-scroll"><table><thead><tr><th>Bảng</th><th>Lớp</th><th>Loại</th><th>Số dòng</th><th>Trạng thái</th></tr></thead>
        <tbody>{run.assets.map(a => <tr key={a.name}><td className="mono">{a.name}</td><td>{a.layer}</td><td>{a.kind}</td><td className="mono">{a.rows}</td><td>{a.state}</td></tr>)}</tbody></table></div></Panel>}
    </> : <Empty title="Chưa có kết quả kiểm tra">Run dừng trước khi có cổng nào ghi kết quả.</Empty>)}

    {tab === 'code' && <CodeViewer files={await runFiles(run)} />}

    {tab === 'logs' && <Panel flush><ol className="log-list">{run.logs.map(l => <li key={l.id} className={`log-${l.level}`}>
      <time dateTime={l.at}>{fmtDate(l.at)}</time><span className={`badge log-badge ${l.level}`}>{LEVEL_LABEL[l.level]}</span><span className="mono muted">{STAGES[l.stage]}</span>
      <div><strong>{l.message}</strong>{l.detail && <pre>{l.detail}</pre>}</div>
    </li>)}</ol></Panel>}
  </>;
}
