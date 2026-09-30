'use client';

import dynamic from 'next/dynamic';
import { Activity, ArrowRight, Check, ChevronRight, CircleAlert, Clock3, Database, FileCode2, GitBranch, Layers3, LockKeyhole, Plus, Search, Send, ShieldCheck, Sparkles, X } from 'lucide-react';
import { CHECK_NAMES, LIMITS, POLICIES, STAGES, STATUS_LABEL, filesForRun, type CheckState, type Run, type Status } from '@/lib/studio';
import { useState } from 'react';

const DiffEditor = dynamic(() => import('@monaco-editor/react').then(m => m.DiffEditor), { ssr: false, loading: () => <div className="studio-editor-loading">Đang tải trình xem diff…</div> });

export function StatusBadge({ status }: { status: Status }) {
  const tone = status.startsWith('BLOCKED') || ['NEEDS_HUMAN', 'REJECTED', 'CANCELLED'].includes(status) ? 'bad' : ['VALIDATING', 'READY_FOR_REVIEW'].includes(status) ? 'waiting' : 'good';
  return <span className={`studio-badge ${tone}`}><span className="studio-status-dot" />{STATUS_LABEL[status]}</span>;
}

export function Sidebar({ view, role, onView, onNew, onRole, collapsed, onToggle, runCount }: { view: string; role: string; onView: (v: 'runs' | 'policy') => void; onNew: () => void; onRole: (role: 'Engineer' | 'Reviewer') => void; collapsed: boolean; onToggle: () => void; runCount: number }) {
  return <aside className={`studio-sidebar ${collapsed ? 'collapsed' : ''}`}>
    <div className="studio-brand"><span className="studio-brand-mark"><Layers3 size={20} /></span>{!collapsed && <span><strong>Pipeline Studio</strong><small>VSF · Data Workspace</small></span>}<button className="studio-icon-button studio-collapse" onClick={onToggle} aria-label={collapsed ? 'Mở sidebar' : 'Thu gọn sidebar'}><ChevronRight size={16} /></button></div>
    <div className="studio-side-heading">{collapsed ? '•' : 'WORKSPACE'}</div>
    <nav aria-label="Điều hướng"><button className={view === 'runs' || view === 'detail' || view === 'create' ? 'selected' : ''} onClick={() => onView('runs')} title="Run"><Activity size={17} />{!collapsed && <>Run <span>{runCount}</span></>}</button><button className={view === 'policy' ? 'selected' : ''} onClick={() => onView('policy')} title="Chính sách"><ShieldCheck size={17} />{!collapsed && 'Chính sách'}</button></nav>
    <button className="studio-new-button" onClick={onNew} disabled={role !== 'Engineer'} title={role !== 'Engineer' ? 'Chỉ Engineer tạo yêu cầu' : 'Tạo yêu cầu'}><Plus size={17} />{!collapsed && 'Tạo yêu cầu'}</button>
    <div className="studio-sidebar-foot">{!collapsed && <><span className="studio-demo-tag">Chế độ demo</span><p>Không kết nối warehouse hay Dagster thật.</p><label>Vai trò mô phỏng<select value={role} onChange={e => onRole(e.target.value as 'Engineer' | 'Reviewer')}><option>Engineer</option><option>Reviewer</option></select></label></>}</div>
  </aside>;
}

export function RunsTable({ runs, role, onOpen, onNew }: { runs: Run[]; role: string; onOpen: (id: string) => void; onNew: () => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const filtered = runs.filter(run => (filter === 'all' || filter === 'review' && run.status === 'READY_FOR_REVIEW' || filter === 'blocked' && run.status.startsWith('BLOCKED') || filter === 'active' && run.status === 'VALIDATING') && `${run.title} ${run.id} ${run.domain}`.toLowerCase().includes(query.toLowerCase()));
  filtered.sort((a, b) => role === 'Reviewer' ? Number(b.status === 'READY_FOR_REVIEW') - Number(a.status === 'READY_FOR_REVIEW') : 0);
  return <>
    <div className="studio-page-head"><div><div className="studio-kicker">WORKSPACE / RUNS</div><h1>Pipeline runs</h1><p>Theo dõi đặc tả, mã nguồn và bằng chứng cho từng yêu cầu.</p></div>{role === 'Engineer' && <button className="studio-button primary" onClick={onNew}><Plus size={16} /> Tạo yêu cầu</button>}</div>
    <div className="studio-budget"><ShieldCheck size={16} /><span>Ngân sách sandbox hôm nay</span><strong>$0,18 / $2,00</strong><div className="studio-budget-track"><span /></div><small>Số liệu mô phỏng · 9% đã dùng</small></div>
    <section className="studio-surface studio-runs"><div className="studio-table-tools"><div className="studio-filter-tabs">{[['all', 'Tất cả'], ['active', 'Đang chạy'], ['review', 'Chờ duyệt'], ['blocked', 'Bị chặn']].map(([key, label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div><label className="studio-search"><Search size={15} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm run…" aria-label="Tìm run" /></label></div>
      <div className="studio-table-scroll"><table><thead><tr><th>Tên yêu cầu</th><th>Domain</th><th>Version</th><th>Trạng thái</th><th>Stage hiện tại</th><th>Cập nhật</th><th /></tr></thead><tbody>{filtered.map(run => <tr key={run.id}><td><button className="studio-run-link" onClick={() => onOpen(run.id)}><span className="studio-run-icon"><GitBranch size={17} /></span><span><strong>{run.title}</strong><small>{run.id}</small></span></button></td><td>{run.domain}</td><td className="studio-mono">v{run.version}</td><td><StatusBadge status={run.status} /></td><td>{STAGES[run.stage]}</td><td>{new Date(run.updatedAt).toLocaleDateString('vi-VN')}</td><td><button className="studio-icon-button" onClick={() => onOpen(run.id)} aria-label={`Mở ${run.title}`}><ChevronRight size={16} /></button></td></tr>)}</tbody></table></div>
      {filtered.length === 0 && <div className="studio-empty"><Search size={24} /><strong>Không có run phù hợp</strong><p>Thử từ khóa khác hoặc xóa bộ lọc.</p><button className="studio-button" onClick={() => { setFilter('all'); setQuery(''); }}>Xóa bộ lọc</button></div>}
      <div className="studio-table-foot">Hiển thị {filtered.length} / {runs.length} run · dữ liệu minh họa</div>
    </section>
  </>;
}

export function StageTimeline({ run, onSelect }: { run: Run; onSelect: (stage: number) => void }) {
  return <section className="studio-surface studio-stages"><div className="studio-section-title"><strong>Tiến trình agent</strong><span>Stage {run.stage + 1} / {STAGES.length} · {STAGES[run.stage]}</span></div><div className="studio-stage-scroller"><div className="studio-stage-list">{STAGES.map((name, index) => <button key={name} onClick={() => onSelect(index)} className={index < run.stage ? 'done' : index === run.stage ? 'current' : ''} aria-label={`Stage ${index + 1}: ${name}`}><span className="studio-stage-number">{index < run.stage ? <Check size={12} /> : String(index + 1).padStart(2, '0')}</span><span>{name}</span></button>)}</div></div><div className="studio-counters"><span>Sửa spec <code>{run.specRetries}/{LIMITS.specRetries}</code></span><span>Sửa SQL <code>{run.sqlRetries}/{LIMITS.sqlRetries}</code></span><span>Gọi LLM <code>{run.llmCalls}/{LIMITS.llmCalls}</code></span><small>Giới hạn minh họa từ budgets.yml</small></div></section>;
}

export function EvidenceStrip({ run, onSelect }: { run: Run; onSelect: (id: string) => void }) {
  return <section className="studio-evidence" aria-label="Bằng chứng mô phỏng">{CHECK_NAMES.slice(0, 5).map(([id, name]) => {
    const check = run.checks.find(c => c.id === id)!;
    return <button key={id} onClick={() => onSelect(id)}><span>{name}</span><strong>{check.value}</strong><small className={`studio-check-${check.state}`}>{check.state === 'pass' ? <Check size={12} /> : check.state === 'fail' ? <CircleAlert size={12} /> : <Clock3 size={12} />}{check.detail}</small></button>;
  })}</section>;
}

export function PolicyViewer() {
  const [file, setFile] = useState('domains.yml');
  return <><div className="studio-page-head"><div><div className="studio-kicker">WORKSPACE / GOVERNANCE</div><h1>Chính sách</h1><p>Các quy tắc minh họa được áp dụng cho sandbox. Chỉ đọc.</p></div><span className="studio-pill"><LockKeyhole size={13} /> Chỉ đọc</span></div><div className="studio-surface"><div className="studio-tabs">{Object.keys(POLICIES).map(name => <button key={name} className={file === name ? 'active' : ''} onClick={() => setFile(name)}><FileCode2 size={14} />{name}</button>)}</div><div className="studio-code-meta"><span>policy/{file}</span><span>YAML · minh họa</span></div><pre className="studio-policy-code">{POLICIES[file].split('\n').map((line, i) => <div key={i}><span>{String(i + 1).padStart(2, '0')}</span><code>{line}</code></div>)}</pre></div></>;
}

export function CodeDiffViewer({ run, onExplain }: { run: Run; onExplain: (path: string) => void }) {
  const files = filesForRun(run);
  const [selected, setSelected] = useState(0);
  const file = files[selected];
  return <div className="studio-code-layout"><div className="studio-file-tree"><div className="studio-file-tree-title">ARTIFACTS · 4 FILE</div>{files.map((item, i) => <button key={item.path} className={selected === i ? 'active' : ''} onClick={() => setSelected(i)}><FileCode2 size={14} />{item.path}</button>)}</div><div className="studio-code-main"><div className="studio-code-meta"><span>{file.path} · v{Math.max(1, run.version - 1)} → v{run.version}</span><button onClick={() => onExplain(file.path)}><Sparkles size={14} /> Giải thích khối này</button></div><DiffEditor key={file.path} height="510px" language={file.language} original={file.before} modified={file.after} theme="light" options={{ readOnly: true, originalEditable: false, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false, automaticLayout: true }} /></div></div>;
}

export function AssistantPanel({ context, run, onClose }: { context: string; run?: Run; onClose: () => void }) {
  const [draft, setDraft] = useState('');
  const [question, setQuestion] = useState('');
  const answer = context.includes('semantic') || context.includes('customer')
    ? 'Hai domain dùng customer với nghĩa khác nhau. Cần chọn một domain hoặc chuyển steward xem xét policy/domains.yml.'
    : context.includes('budget') ? 'Ước tính 8,2 GB vượt trần 5 GB. Bản rút gọn thêm bộ lọc phân vùng rồi chạy lại trong sandbox.'
    : context.includes('ast') ? 'Cổng AST chặn SELECT * trong gold theo budgets.yml. Xem trace ở tab Log, sau đó yêu cầu tạo version mới với danh sách cột tường minh.'
    : context.includes('.py') || context.includes('.sql') || context.includes('.yml') ? 'File này là artifact mẫu chỉ đọc. Diff thể hiện mã đề xuất; hãy kiểm tra RunReport trước khi Reviewer phê duyệt đúng hash.'
    : question ? 'Đây là câu trả lời mô phỏng. Hãy chọn file, stage hoặc check cụ thể để xem bằng chứng liên quan; agent demo không gọi LLM thật.'
    : 'Kế hoạch tạo ingestion, model dbt, tests và asset Dagster đã được chuẩn bị. Mọi kết quả chạy và số đo trên màn hình là dữ liệu minh họa.';
  const steps = ['Đọc schema và chính sách', 'Lập kế hoạch ELT', 'Sinh 4 artifact', 'Kiểm tra SQL & ngân sách', 'Chạy sandbox và đối soát', 'Bàn giao reviewer'];
  const finished = run ? run.status === 'VALIDATING' ? Math.min(6, Math.max(1, Math.ceil((run.stage + 1) / 2.5))) : run.status.startsWith('BLOCKED') ? Math.min(4, Math.ceil((run.stage + 1) / 2.5)) : 6 : 0;
  return <aside className="studio-assistant"><div className="studio-assistant-head"><div><span className="studio-kicker">STUDIO AGENT · MÔ PHỎNG</span><strong><Sparkles size={16} /> Trợ lý pipeline</strong></div><button className="studio-icon-button" onClick={onClose} aria-label="Đóng trợ lý"><X size={17} /></button></div>
    <div className="studio-assistant-scroll">{run && <><div className="ide-agent-request"><span>YÊU CẦU</span><p>{run.request}</p></div><div className="ide-agent-plan"><div><strong>Kế hoạch agent</strong><span>{finished}/{steps.length}</span></div><ol>{steps.map((step, index) => <li key={step} className={index < finished ? 'done' : ''}><span>{index < finished ? <Check size={11} /> : index + 1}</span>{step}</li>)}</ol></div></>}
      <div className="studio-assistant-message"><span className="studio-ai-label"><Sparkles size={14} /> Giải thích theo ngữ cảnh · mô phỏng</span>{question && <div className="ide-agent-question">{question}</div>}<p>{answer}</p><div className="studio-assistant-ref"><FileCode2 size={13} /> {context.includes('budget') || context.includes('ast') ? 'policy/budgets.yml' : context.includes('semantic') ? 'policy/domains.yml' : context.includes('.') ? context : run ? `RunReport · ${run.id}` : 'Workspace demo'}</div></div>
      {run && <div className="ide-agent-files"><div><strong>Thay đổi đề xuất</strong><span>4 file</span></div>{filesForRun(run).map((file, index) => <div key={file.path}><FileCode2 size={13} /><span>{file.path}</span><small>+{index === 1 ? 35 : index === 0 ? 21 : 8}</small></div>)}<p>Reviewer duyệt toàn bộ version và files_hash sau khi các check đạt.</p></div>}
    </div><form className="ide-agent-compose" onSubmit={event => { event.preventDefault(); if (draft.trim()) { setQuestion(draft.trim()); setDraft(''); } }}><label htmlFor="ide-agent-input">Hỏi về pipeline này</label><div><textarea id="ide-agent-input" rows={2} value={draft} onChange={event => setDraft(event.target.value)} placeholder="Ví dụ: Giải thích model doanh thu…" /><button disabled={!draft.trim()} aria-label="Gửi câu hỏi"><Send size={15} /></button></div><small>Câu trả lời minh họa · không gọi LLM thật</small></form>
    <div className="studio-assistant-foot"><LockKeyhole size={14} /> Trợ lý không tự chạy hoặc phê duyệt</div></aside>;
}
