'use client';

import dynamic from 'next/dynamic';
import { useState } from 'react';
import { Activity, ArrowLeft, Check, ChevronDown, ChevronRight, CircleAlert, Clock3, Code2, Database, FileCode2, FileText, Folder, FolderOpen, GitBranch, Layers3, LockKeyhole, Play, RotateCcw, Search, ShieldCheck, Sparkles, Square, Table2 } from 'lucide-react';
import { canReview, filesForRun, STAGES, type Run, type Scenario } from '@/lib/studio';
import { StatusBadge } from './studio-parts';
import { BlockedRunView, ReviewBar, RunLogs } from './studio-workflows';

const DiffEditor = dynamic(() => import('@monaco-editor/react').then(m => m.DiffEditor), { ssr: false, loading: () => <div className="ide-editor-loading">Đang tải Monaco DiffEditor…</div> });
type BottomTab = 'assets' | 'checks' | 'logs' | 'plan';

export function IdeWorkspace({ run, role, user, onBack, onCancel, onRetry, onDomain, onTicket, onReview, onExport, onExplain }: {
  run: Run; role: string; user: string; onBack: () => void; onCancel: () => void;
  onRetry: (scenario?: Scenario) => void; onDomain: () => void; onTicket: (message: string) => void;
  onReview: (action: 'approve' | 'revise' | 'reject', comment: string) => void;
  onExport: () => void; onExplain: (context: string) => void;
}) {
  const files = filesForRun(run);
  const [selectedFile, setSelectedFile] = useState(1);
  const [bottomTab, setBottomTab] = useState<BottomTab>('assets');
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const [selectedCheck, setSelectedCheck] = useState<string | null>(null);
  const [fileSearch, setFileSearch] = useState('');
  const [expandedFolders, setExpandedFolders] = useState<string[]>(['ingestion', 'models', 'dagster']);
  const selected = files[selectedFile];
  const blocked = run.status === 'BLOCKED_SEMANTIC_CONFLICT' || run.status === 'BLOCKED_BUDGET';
  const folders = ['ingestion', 'models', 'dagster'];
  const toggleFolder = (folder: string) => setExpandedFolders(previous => previous.includes(folder) ? previous.filter(item => item !== folder) : [...previous, folder]);
  const showLog = (stage: number | null, check: string | null) => { setSelectedStage(stage); setSelectedCheck(check); setBottomTab('logs'); };
  const statusCheck = (id: string) => run.checks.find(check => check.id === id);
  const sourceNames = run.domain === 'Marketing' ? ['bronze.campaigns'] : run.status === 'BLOCKED_SEMANTIC_CONFLICT' ? ['bronze.customers', 'bronze.leads'] : ['bronze.orders', 'bronze.payments'];
  const assets = [
    ...sourceNames.map(name => ({ name, layer: 'Bronze', rows: statusCheck('row_reconcile')?.state === 'pass' ? '12.480' : '—', kind: 'Bảng nguồn', state: run.stage >= 10 ? 'Đã thử' : 'Chưa chạy' })),
    { name: `gold.${run.plan.model}`, layer: 'Gold', rows: statusCheck('row_reconcile')?.state === 'pass' ? '365' : '—', kind: 'Model dbt', state: run.checks.every(c => c.state === 'pass') ? 'Đạt' : run.status === 'NEEDS_HUMAN' ? 'Cần kiểm tra' : 'Chưa xác nhận' },
  ];

  return <div className="ide-shell">
    <aside className="ide-explorer" aria-label="Cây file pipeline">
      <div className="ide-explorer-head"><button onClick={onBack} title="Danh sách run" aria-label="Danh sách run"><ArrowLeft size={15} /></button><strong>Pipeline</strong><span className="ide-explorer-count">4 file</span></div>
      <button className="ide-config-row" onClick={() => setBottomTab('plan')}><Layers3 size={15} /> Cấu hình pipeline <ChevronRight size={13} /></button>
      <div className="ide-last-run"><span>LẦN CHẠY GẦN NHẤT</span><div><span className={`ide-run-dot ${run.status === 'READY_FOR_REVIEW' || run.status === 'APPROVED' || run.status === 'DEPLOYED' ? 'good' : run.status.startsWith('BLOCKED') ? 'bad' : 'waiting'}`} />{run.status === 'VALIDATING' ? 'Đang chạy sandbox' : 'Bản mô phỏng'}<small>{run.status === 'VALIDATING' ? STAGES[run.stage] : `v${run.version}`}</small></div></div>
      <div className="ide-tree-head"><span><Folder size={14} /> {run.title}</span><small>{run.id}</small></div>
      <label className="ide-file-search"><Search size={13} /><input aria-label="Tìm file pipeline" placeholder="Tìm file…" value={fileSearch} onChange={event => setFileSearch(event.target.value)} /></label>
      <div className="ide-file-list">{folders.map(folder => {
        const matching = files.map((file, index) => ({ file, index })).filter(({ file }) => file.path.startsWith(folder + '/') && file.path.toLowerCase().includes(fileSearch.toLowerCase()));
        if (matching.length === 0) return null;
        const open = expandedFolders.includes(folder) || !!fileSearch;
        return <div key={folder}><button className="ide-folder" aria-expanded={open} onClick={() => toggleFolder(folder)}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{open ? <FolderOpen size={14} /> : <Folder size={14} />}{folder}</button>{open && matching.map(({ file, index }) => <button key={file.path} className={`ide-file ${selectedFile === index ? 'active' : ''}`} onClick={() => setSelectedFile(index)}><FileCode2 size={13} />{file.path.split('/')[1]}<span className="ide-file-change">+{index === 1 ? '35' : index === 0 ? '21' : '8'}</span></button>)}</div>;
      })}</div>
      <div className="ide-explorer-foot"><button onClick={() => showLog(null, null)}><Activity size={14} /> RunReport <ChevronRight size={13} /></button><div><LockKeyhole size={12} /> Sandbox · chỉ đọc</div></div>
    </aside>

    <section className="ide-workbench" aria-label="Trình biên tập pipeline">
      <div className="ide-workbench-head"><div className="ide-workbench-title"><GitBranch size={16} /><strong>{run.title}</strong><span className="ide-version">v{run.version}</span><StatusBadge status={run.status} /></div><div className="ide-workbench-actions"><span className="ide-sandbox"><span /> Sandbox</span>{run.status === 'VALIDATING' && <button className="ide-compact-button" disabled={role !== 'Engineer'} title={role !== 'Engineer' ? 'Chỉ Engineer hủy run' : ''} onClick={onCancel}><Square size={12} /> Hủy</button>}{run.status === 'CANCELLED' && <button className="ide-compact-button" disabled={role !== 'Engineer'} onClick={() => onRetry()}><Play size={13} /> Chạy lại</button>}{run.status === 'READY_FOR_REVIEW' && <button className="ide-compact-button" disabled={role !== 'Engineer'} onClick={() => onRetry('success')} title={role !== 'Engineer' ? 'Chỉ Engineer chạy thử' : 'Tạo version mới và chạy kiểm tra lại'}><RotateCcw size={13} /> Chạy thử lại</button>}<button className="ide-compact-button primary" onClick={() => setBottomTab('checks')}><ShieldCheck size={13} /> Bằng chứng</button></div></div>

      {blocked ? <div className="ide-blocked-body"><BlockedRunView run={run} onDomain={onDomain} onRetry={() => onRetry('success')} onTicket={onTicket} /></div> : <>
        <div className="ide-file-tabs">{files.map((file, index) => <button key={file.path} className={selectedFile === index ? 'active' : ''} onClick={() => setSelectedFile(index)}><FileCode2 size={13} />{file.path.split('/')[1]}{index === 1 && <span className="ide-file-unsaved" />}</button>)}</div>
        <div className="ide-editor-toolbar"><span><Code2 size={13} />{selected.language.toUpperCase()}<ChevronDown size={12} /></span><span>Diff · v{Math.max(1, run.version - 1)} → v{run.version}</span><span className="ide-readonly"><LockKeyhole size={12} /> Chỉ đọc</span><button onClick={() => onExplain(selected.path)}><Sparkles size={13} /> Giải thích khối này</button></div>
        <div className="ide-editor-area"><DiffEditor key={selected.path} height="100%" language={selected.language} original={selected.before} modified={selected.after} theme="light" options={{ readOnly: true, originalEditable: false, renderSideBySide: false, minimap: { enabled: false }, fontSize: 12, lineHeight: 19, scrollBeyondLastLine: false, automaticLayout: true, wordWrap: 'on', diffWordWrap: 'on', overviewRulerBorder: false }} /></div>
        <div className="ide-change-bar"><span><Check size={13} /> Bản đề xuất · 4 file · hash <code>{run.filesHash.slice(0, 8)}</code></span><button onClick={() => setBottomTab('checks')}>Xem bằng chứng <ChevronRight size={13} /></button></div>
      </>}

      <section className="ide-bottom" aria-label="Kết quả pipeline"><div className="ide-bottom-tabs">{[['assets', 'Bảng dữ liệu'], ['checks', 'RunReport'], ['logs', 'Log'], ['plan', 'Kế hoạch']].map(([key, label]) => <button key={key} className={bottomTab === key ? 'active' : ''} onClick={() => setBottomTab(key as BottomTab)}>{label}{key === 'checks' && <span>{run.checks.filter(check => check.state === 'pass').length}/{run.checks.length}</span>}</button>)}<span className="ide-bottom-note">Dữ liệu minh họa</span></div>
        <div className="ide-bottom-content">{bottomTab === 'assets' && <div className="ide-assets"><div className="ide-assets-toolbar"><span><Table2 size={14} /> {assets.length} assets</span><span>Warehouse · DuckDB / BigQuery sandbox</span></div><div className="ide-assets-scroll"><table><thead><tr><th>Tên</th><th>Lớp</th><th>Loại</th><th>Dòng</th><th>Trạng thái</th></tr></thead><tbody>{assets.map(asset => <tr key={asset.name}><td><Database size={13} /><strong>{asset.name}</strong></td><td>{asset.layer}</td><td>{asset.kind}</td><td className="studio-mono">{asset.rows}</td><td><span className={asset.state === 'Đạt' ? 'ide-good' : ''}>{asset.state}</span></td></tr>)}</tbody></table></div></div>}
          {bottomTab === 'checks' && <div className="ide-checks"><div className="ide-checks-intro"><strong>RunReport · v{run.version}</strong><span>Chạy được: {run.stage >= 12 ? 'đã thử' : 'chưa xác nhận'} · Đúng nghiệp vụ: {statusCheck('metric_reconcile')?.state === 'pass' ? 'đạt' : 'chưa đạt'} · Đã duyệt: {['APPROVED', 'DEPLOYED'].includes(run.status) ? 'có' : 'chưa'}</span></div><div className="ide-check-grid">{run.checks.map(check => <button key={check.id} onClick={() => showLog(null, check.id)}><span className={`ide-check-icon ${check.state}`}>{check.state === 'pass' ? <Check size={13} /> : check.state === 'fail' ? <CircleAlert size={13} /> : <Clock3 size={13} />}</span><strong>{check.name}</strong><code>{check.value}</code><small>{check.detail}</small></button>)}</div></div>}
          {bottomTab === 'logs' && <RunLogs run={run} selectedStage={selectedStage} selectedCheck={selectedCheck} />}
          {bottomTab === 'plan' && <div className="ide-plan"><div className="ide-plan-head"><strong>Đặc tả đã xác nhận</strong><span>{run.domain} · {run.plan.schedule} · {run.plan.writeMode}</span></div><div className="ide-plan-grid">{[['Nguồn', run.plan.sources.join(', ')], ['Bảng', run.plan.tables.join(', ')], ['Khóa', run.plan.primaryKey], ['Cursor', run.plan.cursor], ['Model', run.plan.model], ['Bộ lọc phân vùng', run.plan.partitionFilter || 'Chưa có']].map(([key, value]) => <div key={key}><span>{key}</span><code>{value}</code></div>)}</div><div className="ide-plan-stages"><strong>Tiến trình · {run.stage + 1}/{STAGES.length}</strong><div>{STAGES.map((stage, index) => <button key={stage} className={index === run.stage ? 'current' : index < run.stage ? 'done' : ''} onClick={() => showLog(index, null)} title={stage}>{index < run.stage ? <Check size={10} /> : index + 1}</button>)}</div></div></div>}
        </div>
      </section>
      {run.status === 'BLOCKED_AST' && <div className="ide-inline-alert"><CircleAlert size={15} /> SQL bị chặn bởi chính sách AST. <button onClick={() => showLog(7, null)}>Xem trace</button><button onClick={() => onRetry('success')}>Yêu cầu sửa</button></div>}
      {run.status === 'NEEDS_HUMAN' && <div className="ide-inline-alert"><CircleAlert size={15} /> Đối soát doanh thu không đạt, chưa thể duyệt. <button onClick={() => showLog(null, 'metric_reconcile')}>Xem check</button><button onClick={() => onRetry('success')}>Yêu cầu sửa</button></div>}
      {run.status === 'REJECTED' && <div className="ide-inline-alert"><CircleAlert size={15} /> Reviewer đã từ chối phiên bản này. <button onClick={() => onRetry('success')}>Tạo phiên bản mới</button></div>}
      <ReviewBar run={run} role={role} user={user} onReview={onReview} onExport={onExport} />
      <div className="ide-statusbar"><span><span className="ide-run-dot good" /> Sandbox demo</span><span>{run.id} · v{run.version} · {run.stage + 1}/{STAGES.length} stages</span><span>{canReview(run, role, user) ? 'Sẵn sàng phê duyệt' : 'Mọi thao tác production cần review'}</span></div>
    </section>
  </div>;
}
