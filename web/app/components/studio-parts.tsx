'use client';

import { Activity, Check, ChevronRight, CircleAlert, Clock3, FileCode2, GitBranch, Layers3, LockKeyhole, LogOut, Plus, Search, ShieldCheck, Sparkles, X } from 'lucide-react';
import { RUNNING, STAGES, STATUS_LABEL, shortId, type Limits, type Run, type Status } from '@/lib/studio';
import type { User } from '@/lib/postgres-api';
import { useState } from 'react';

export function StatusBadge({ status }: { status: Status }) {
  const tone = status.startsWith('BLOCKED') || ['NEEDS_HUMAN', 'REJECTED', 'CANCELLED', 'INFRA_ERROR', 'INTERNAL_ERROR', 'DEPLOY_FAILED'].includes(status) ? 'bad' : [...RUNNING, 'READY_FOR_REVIEW'].includes(status) ? 'waiting' : 'good';
  return <span className={`studio-badge ${tone}`}><span className="studio-status-dot" />{STATUS_LABEL[status]}</span>;
}

export function Sidebar({ view, user, onView, onNew, onLogout, collapsed, onToggle, runCount }: { view: string; user: User; onView: (v: 'runs' | 'policy') => void; onNew: () => void; onLogout: () => void; collapsed: boolean; onToggle: () => void; runCount: number }) {
  return <aside className={`studio-sidebar ${collapsed ? 'collapsed' : ''}`}>
    <div className="studio-brand"><span className="studio-brand-mark"><Layers3 size={20} /></span>{!collapsed && <span><strong>Pipeline Studio</strong><small>VSF · Data Workspace</small></span>}<button className="studio-icon-button studio-collapse" onClick={onToggle} aria-label={collapsed ? 'Mở sidebar' : 'Thu gọn sidebar'}><ChevronRight size={16} /></button></div>
    <div className="studio-side-heading">{collapsed ? '•' : 'WORKSPACE'}</div>
    <nav aria-label="Điều hướng"><button className={view === 'runs' || view === 'detail' || view === 'create' ? 'selected' : ''} onClick={() => onView('runs')} title="Run"><Activity size={17} />{!collapsed && <>Run <span>{runCount}</span></>}</button><button className={view === 'policy' ? 'selected' : ''} onClick={() => onView('policy')} title="Chính sách"><ShieldCheck size={17} />{!collapsed && 'Chính sách'}</button></nav>
    <button className="studio-new-button" onClick={onNew} disabled={user.role !== 'Engineer'} title={user.role !== 'Engineer' ? 'Chỉ Engineer tạo yêu cầu' : 'Tạo yêu cầu'}><Plus size={17} />{!collapsed && 'Tạo yêu cầu'}</button>
    <div className="studio-sidebar-foot">{!collapsed && <><span className="studio-demo-tag">Sandbox DuckDB</span><p>Đăng nhập: <strong>{user.name}</strong> · {user.role}</p><button className="studio-button" onClick={onLogout}><LogOut size={14} /> Đăng xuất</button></>}</div>
  </aside>;
}

export function RunsTable({ runs, user, limits, onOpen, onNew }: { runs: Run[]; user: User; limits: Limits | null; onOpen: (id: string) => void; onNew: () => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const filtered = runs.filter(run => (filter === 'all' || filter === 'review' && run.status === 'READY_FOR_REVIEW' || filter === 'blocked' && (run.status.startsWith('BLOCKED') || ['NEEDS_HUMAN', 'INFRA_ERROR', 'INTERNAL_ERROR'].includes(run.status)) || filter === 'active' && RUNNING.includes(run.status)) && `${run.title} ${run.id} ${run.domain}`.toLowerCase().includes(query.toLowerCase()));
  filtered.sort((a, b) => user.role === 'Reviewer' ? Number(b.status === 'READY_FOR_REVIEW') - Number(a.status === 'READY_FOR_REVIEW') : 0);
  const today = new Date().toDateString();
  const spent = runs.filter(run => new Date(run.updatedAt).toDateString() === today).reduce((sum, run) => sum + run.usd, 0);
  const pct = limits ? Math.min(100, spent / limits.max_usd_per_day * 100) : 0;
  return <>
    <div className="studio-page-head"><div><div className="studio-kicker">WORKSPACE / RUNS</div><h1>Pipeline runs</h1><p>Mọi run đọc từ sổ bằng chứng của backend.</p></div>{user.role === 'Engineer' && <button className="studio-button primary" onClick={onNew}><Plus size={16} /> Tạo yêu cầu</button>}</div>
    <div className="studio-budget"><ShieldCheck size={16} /><span>Chi phí warehouse hôm nay</span><strong>${spent.toFixed(2)} / ${limits ? limits.max_usd_per_day.toFixed(2) : '—'}</strong><div className="studio-budget-track"><span style={{ width: `${pct}%` }} /></div><small>Trần từ budgets.yml · sandbox DuckDB không tính phí</small></div>
    <section className="studio-surface studio-runs"><div className="studio-table-tools"><div className="studio-filter-tabs">{[['all', 'Tất cả'], ['active', 'Đang chạy'], ['review', 'Chờ duyệt'], ['blocked', 'Dừng / bị chặn']].map(([key, label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div><label className="studio-search"><Search size={15} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm run…" aria-label="Tìm run" /></label></div>
      <div className="studio-table-scroll"><table><thead><tr><th>Yêu cầu</th><th>Domain</th><th>Version</th><th>Trạng thái</th><th>Phase dừng</th><th>Thời điểm</th><th /></tr></thead><tbody>{filtered.map(run => <tr key={run.id}><td><button className="studio-run-link" onClick={() => onOpen(run.id)}><span className="studio-run-icon"><GitBranch size={17} /></span><span><strong>{run.title}</strong><small>{shortId(run.id)} · {run.owner}</small></span></button></td><td>{run.domain}</td><td className="studio-mono">v{run.version}</td><td><StatusBadge status={run.status} /></td><td>{STAGES[run.stage]}</td><td>{new Date(run.updatedAt).toLocaleString('vi-VN')}</td><td><button className="studio-icon-button" onClick={() => onOpen(run.id)} aria-label={`Mở ${run.title}`}><ChevronRight size={16} /></button></td></tr>)}</tbody></table></div>
      {filtered.length === 0 && <div className="studio-empty"><Search size={24} /><strong>{runs.length ? 'Không có run phù hợp' : 'Chưa có run nào'}</strong><p>{runs.length ? 'Thử từ khóa khác hoặc xóa bộ lọc.' : 'Engineer tạo yêu cầu đầu tiên để agent sinh pipeline.'}</p>{runs.length > 0 && <button className="studio-button" onClick={() => { setFilter('all'); setQuery(''); }}>Xóa bộ lọc</button>}</div>}
      <div className="studio-table-foot">Hiển thị {filtered.length} / {runs.length} run · 100 run gần nhất trong sổ bằng chứng</div>
    </section>
  </>;
}

export function StageTimeline({ run, limits, onSelect }: { run: Run; limits: Limits | null; onSelect: (stage: number) => void }) {
  return <section className="studio-surface studio-stages"><div className="studio-section-title"><strong>Tiến trình agent</strong><span>Phase {run.stage + 1} / {STAGES.length} · {STAGES[run.stage]}</span></div><div className="studio-stage-scroller"><div className="studio-stage-list">{STAGES.map((name, index) => <button key={name} onClick={() => onSelect(index)} className={index < run.stage ? 'done' : index === run.stage ? 'current' : ''} aria-label={`Phase ${index + 1}: ${name}`}><span className="studio-stage-number">{index < run.stage ? <Check size={12} /> : String(index + 1).padStart(2, '0')}</span><span>{name}</span></button>)}</div></div><div className="studio-counters"><span>Sửa spec <code>{run.specRetries}/{limits?.max_spec_attempts ?? '—'}</code></span><span>Sửa SQL <code>{run.sqlRetries}/{limits?.max_sql_attempts ?? '—'}</code></span><span>Gọi LLM <code>{run.llmCalls}/{limits?.max_llm_calls ?? '—'}</code></span><small>Trần từ budgets.yml</small></div></section>;
}

export function EvidenceStrip({ run, onSelect }: { run: Run; onSelect: (id: string) => void }) {
  if (!run.checks.length) return null;
  return <section className="studio-evidence" aria-label="Bằng chứng">{run.checks.slice(0, 5).map(check => <button key={check.id} onClick={() => onSelect(check.id)}><span>{check.name}</span><strong>{check.value}</strong><small className={`studio-check-${check.state}`}>{check.state === 'pass' ? <Check size={12} /> : check.state === 'fail' ? <CircleAlert size={12} /> : <Clock3 size={12} />}{check.detail}</small></button>)}</section>;
}

export function PolicyViewer({ files }: { files: Record<string, string> | null }) {
  const names = Object.keys(files ?? {});
  const [file, setFile] = useState('domains.yml');
  const content = files?.[file] ?? '';
  return <><div className="studio-page-head"><div><div className="studio-kicker">WORKSPACE / GOVERNANCE</div><h1>Chính sách</h1><p>Nội dung thật của policy/*.yml mà backend đang áp dụng. Sửa qua pull request.</p></div><span className="studio-pill"><LockKeyhole size={13} /> Chỉ đọc</span></div><div className="studio-surface"><div className="studio-tabs">{names.map(name => <button key={name} className={file === name ? 'active' : ''} onClick={() => setFile(name)}><FileCode2 size={14} />{name}</button>)}</div><div className="studio-code-meta"><span>policy/{file}</span><span>YAML</span></div>{files ? <pre className="studio-policy-code">{content.split('\n').map((line, i) => <div key={i}><span>{String(i + 1).padStart(2, '0')}</span><code>{line}</code></div>)}</pre> : <div className="studio-loading">Đang tải chính sách…</div>}</div></>;
}

const AGENT_STEPS = ['Lập kế hoạch (LLM)', 'Viết Spec (LLM)', 'Sinh code từ template', 'Chạy thử trong sandbox'];
function stepsDone(run: Run): number {
  if (run.pending) return 0;
  if (run.checks.some(c => !c.id.startsWith('gate:') || c.id === 'gate:post_run')) return 4;
  if (run.fileCount) return 3;
  if (run.checks.some(c => c.id === 'gate:spec_schema' && c.state === 'pass')) return 2;
  return run.stage >= 1 && run.summary ? 1 : 0;
}

export function AssistantPanel({ context, run, onClose }: { context: string; run?: Run; onClose: () => void }) {
  const done = run ? stepsDone(run) : 0;
  const message = !run ? 'Chọn một run để xem kế hoạch, lý do dừng và các file agent đã sinh.'
    : run.pending ? 'Agent đang chạy. Kết quả, kể cả khi dừng giữa chừng, sẽ hiện ở đây khi backend trả về RunReport.'
    : context.includes('/') ? `File ${context} được sinh tất định từ templates/ theo Spec của run này; nội dung đầy đủ lưu trong sổ bằng chứng theo (run_id, version).`
    : run.error ?? (run.status === 'READY_FOR_REVIEW' ? 'Mọi kiểm tra trong sandbox đều đạt. Reviewer kiểm tra code, bảng dữ liệu và RunReport trước khi duyệt.' : `Trạng thái: ${STATUS_LABEL[run.status]}.`);
  return <aside className="studio-assistant"><div className="studio-assistant-head"><div><span className="studio-kicker">STUDIO AGENT</span><strong><Sparkles size={16} /> Trợ lý pipeline</strong></div><button className="studio-icon-button" onClick={onClose} aria-label="Đóng trợ lý"><X size={17} /></button></div>
    <div className="studio-assistant-scroll">{run && <><div className="ide-agent-request"><span>YÊU CẦU</span><p>{run.request}</p></div>{run.summary && <div className="ide-agent-request"><span>KẾ HOẠCH</span><p>{run.summary}</p></div>}<div className="ide-agent-plan"><div><strong>Các bước của agent</strong><span>{done}/{AGENT_STEPS.length}</span></div><ol>{AGENT_STEPS.map((step, index) => <li key={step} className={index < done ? 'done' : ''}><span>{index < done ? <Check size={11} /> : index + 1}</span>{step}</li>)}</ol></div></>}
      <div className="studio-assistant-message"><span className="studio-ai-label"><Sparkles size={14} /> Từ RunReport</span><p>{message}</p><div className="studio-assistant-ref"><FileCode2 size={13} /> {context.includes('/') ? context : run ? `RunReport · ${shortId(run.id)}` : 'Workspace'}</div></div>
      {run && run.fileCount > 0 && <div className="ide-agent-files"><div><strong>File sinh ra</strong><span>{run.fileCount} file</span></div><p>Reviewer duyệt cả bộ file theo files_hash <code>{run.filesHash.replace('sha256:', '').slice(0, 12)}</code>.</p></div>}
    </div><div className="ide-agent-compose"><label>Hỏi về pipeline này</label><small>Hỏi đáp trong chat (ý định <code>ask</code>) chưa có API, dự kiến Sprint 2.</small></div>
    <div className="studio-assistant-foot"><LockKeyhole size={14} /> Trợ lý không tự chạy hoặc phê duyệt</div></aside>;
}
