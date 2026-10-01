'use client';

import dynamic from 'next/dynamic';
import { useState } from 'react';
import { Activity, ArrowLeft, Check, ChevronDown, ChevronRight, CircleAlert, Clock3, Code2, Database, FileCode2, Folder, FolderOpen, GitBranch, Layers3, LoaderCircle, LockKeyhole, RotateCcw, Search, ShieldCheck, Sparkles, Table2 } from 'lucide-react';
import { RUNNING, STAGES, canReview, shortId, type FileArtifact, type Run } from '@/lib/studio';
import { StatusBadge } from './studio-parts';
import { BlockedRunView, ReviewBar, RunLogs, type ReviewDecision } from './studio-workflows';

const Editor = dynamic(() => import('@monaco-editor/react').then(m => m.Editor), { ssr: false });
/** Hiện ngay nội dung file trong lúc Monaco tải (Monaco lấy mã từ CDN; mạng chặn CDN thì vẫn đọc được code). */
const PlainCode = ({ content }: { content: string }) => <pre className="studio-policy-code ide-plain-code">{content.split('\n').map((line, i) => <div key={i}><span>{String(i + 1).padStart(3, ' ')}</span><code>{line}</code></div>)}</pre>;
type BottomTab = 'assets' | 'checks' | 'logs' | 'plan';
const dirname = (path: string) => path.split('/').slice(0, -1).join('/');
const basename = (path: string) => path.split('/').at(-1) ?? path;

export function IdeWorkspace({ run, files, role, user, onBack, onRetry, onEdit, onExplain, onReview }: {
  run: Run; files: FileArtifact[] | null; role: string; user: string;
  onBack: () => void; onRetry: () => void; onEdit: () => void; onExplain: (context: string) => void;
  onReview: (decision: ReviewDecision, comment: string) => Promise<void>;
}) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [bottomTab, setBottomTab] = useState<BottomTab>('checks');
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const [selectedCheck, setSelectedCheck] = useState<string | null>(null);
  const [fileSearch, setFileSearch] = useState('');
  const [collapsedFolders, setCollapsedFolders] = useState<string[]>([]);
  const list = files ?? [];
  const selected = list.find(file => file.path === selectedPath) ?? list.find(file => file.path.includes('/gold/') && file.path.endsWith('.sql')) ?? list[0];
  const folders = [...new Set(list.map(file => dirname(file.path)))];
  const running = run.pending || RUNNING.includes(run.status);
  const isEngineer = role === 'Engineer';
  const toggleFolder = (folder: string) => setCollapsedFolders(previous => previous.includes(folder) ? previous.filter(item => item !== folder) : [...previous, folder]);
  const showLog = (stage: number | null, check: string | null) => { setSelectedStage(stage); setSelectedCheck(check); setBottomTab('logs'); };
  const passed = run.checks.filter(check => check.state === 'pass').length;

  return <div className="ide-shell">
    <aside className="ide-explorer" aria-label="Cây file pipeline">
      <div className="ide-explorer-head"><button onClick={onBack} title="Danh sách run" aria-label="Danh sách run"><ArrowLeft size={15} /></button><strong>Pipeline</strong><span className="ide-explorer-count">{run.fileCount} file</span></div>
      <button className="ide-config-row" onClick={() => setBottomTab('plan')}><Layers3 size={15} /> Kế hoạch & Spec <ChevronRight size={13} /></button>
      <div className="ide-last-run"><span>LẦN CHẠY</span><div><span className={`ide-run-dot ${['READY_FOR_REVIEW', 'APPROVED', 'DEPLOYED'].includes(run.status) ? 'good' : running ? 'waiting' : 'bad'}`} />{running ? 'Agent đang chạy' : STAGES[run.stage]}<small>v{run.version}</small></div></div>
      <div className="ide-tree-head"><span><Folder size={14} /> {run.plan.model}</span><small>{shortId(run.id)}</small></div>
      <label className="ide-file-search"><Search size={13} /><input aria-label="Tìm file pipeline" placeholder="Tìm file…" value={fileSearch} onChange={event => setFileSearch(event.target.value)} /></label>
      <div className="ide-file-list">{files === null && run.fileCount > 0 ? <div className="ide-editor-loading">Đang tải file…</div> : !list.length ? <div className="ide-editor-loading">{running ? 'Chưa có file.' : 'Run dừng trước khi sinh code.'}</div> : folders.map(folder => {
        const matching = list.filter(file => dirname(file.path) === folder && file.path.toLowerCase().includes(fileSearch.toLowerCase()));
        if (matching.length === 0) return null;
        const open = !collapsedFolders.includes(folder) || !!fileSearch;
        return <div key={folder}><button className="ide-folder" aria-expanded={open} onClick={() => toggleFolder(folder)} title={folder}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{open ? <FolderOpen size={14} /> : <Folder size={14} />}{folder}</button>{open && matching.map(file => <button key={file.path} className={`ide-file ${selected?.path === file.path ? 'active' : ''}`} onClick={() => setSelectedPath(file.path)} title={file.path}><FileCode2 size={13} />{basename(file.path)}<span className="ide-file-change">{file.content.split('\n').length}</span></button>)}</div>;
      })}</div>
      <div className="ide-explorer-foot"><button onClick={() => showLog(null, null)}><Activity size={14} /> RunReport <ChevronRight size={13} /></button><div><LockKeyhole size={12} /> Sandbox · chỉ đọc</div></div>
    </aside>

    <section className="ide-workbench" aria-label="Trình xem pipeline">
      <div className="ide-workbench-head"><div className="ide-workbench-title"><GitBranch size={16} /><strong>{run.title}</strong><span className="ide-version">v{run.version}</span><StatusBadge status={run.status} /></div><div className="ide-workbench-actions"><span className="ide-sandbox"><span /> Sandbox</span>{!running && <button className="ide-compact-button" disabled={!isEngineer} title={isEngineer ? 'Gửi lại cùng yêu cầu, tạo run mới' : 'Chỉ Engineer chạy agent'} onClick={onRetry}><RotateCcw size={13} /> Chạy lại</button>}<button className="ide-compact-button primary" onClick={() => setBottomTab('checks')}><ShieldCheck size={13} /> Bằng chứng</button></div></div>

      {running ? <div className="ide-blocked-body"><section className="studio-blocked"><div className="studio-blocked-top"><LoaderCircle size={21} /><div><span>AGENT ĐANG CHẠY</span><h2>Lập kế hoạch → viết Spec → sinh code → chạy thử</h2><p>Backend chạy đồng bộ, thường khoảng 30 giây. Run sẽ hiện đầy đủ khi RunReport được ghi.</p></div></div></section></div>
        : !list.length && files !== null ? <div className="ide-blocked-body"><BlockedRunView run={run} onRetry={onRetry} onEdit={onEdit} canRun={isEngineer} /></div>
        : selected ? <>
          <div className="ide-file-tabs">{list.map(file => <button key={file.path} className={selected.path === file.path ? 'active' : ''} onClick={() => setSelectedPath(file.path)} title={file.path}><FileCode2 size={13} /><span>{basename(file.path)}</span></button>)}</div>
          <div className="ide-editor-toolbar"><span><Code2 size={13} />{selected.language.toUpperCase()}</span><span className="studio-mono">{selected.path}</span><span className="ide-readonly"><LockKeyhole size={12} /> Chỉ đọc</span><button onClick={() => onExplain(selected.path)}><Sparkles size={13} /> Về file này</button></div>
          <div className="ide-editor-area"><Editor key={selected.path} height="100%" language={selected.language} value={selected.content} theme="light" loading={<PlainCode content={selected.content} />} options={{ readOnly: true, minimap: { enabled: false }, fontSize: 12, lineHeight: 19, scrollBeyondLastLine: false, automaticLayout: true, wordWrap: 'on' }} /></div>
          <div className="ide-change-bar"><span><Check size={13} /> {list.length} file sinh từ template · files_hash <code>{run.filesHash.replace('sha256:', '').slice(0, 12)}</code></span><button onClick={() => setBottomTab('checks')}>Xem bằng chứng <ChevronRight size={13} /></button></div>
        </> : <div className="ide-editor-loading">Đang tải file…</div>}

      <section className="ide-bottom" aria-label="Kết quả pipeline"><div className="ide-bottom-tabs">{[['assets', 'Bảng dữ liệu'], ['checks', 'RunReport'], ['logs', 'Log'], ['plan', 'Kế hoạch']].map(([key, label]) => <button key={key} className={bottomTab === key ? 'active' : ''} onClick={() => setBottomTab(key as BottomTab)}>{label}{key === 'checks' && run.checks.length > 0 && <span>{passed}/{run.checks.length}</span>}</button>)}<span className="ide-bottom-note">Từ sổ bằng chứng</span></div>
        <div className="ide-bottom-content">{bottomTab === 'assets' && <div className="ide-assets"><div className="ide-assets-toolbar"><span><Table2 size={14} /> {run.assets.length} bảng</span><span>Sandbox DuckDB · số dòng đo khi chạy thử</span></div>{run.assets.length ? <div className="ide-assets-scroll"><table><thead><tr><th>Tên</th><th>Lớp</th><th>Loại</th><th>Dòng</th><th>Trạng thái</th></tr></thead><tbody>{run.assets.map(asset => <tr key={asset.name}><td><Database size={13} /><strong>{asset.name}</strong></td><td>{asset.layer}</td><td>{asset.kind}</td><td className="studio-mono">{asset.rows}</td><td><span className={asset.state === 'Đạt' || asset.state === 'Khớp nguồn' ? 'ide-good' : ''}>{asset.state}</span></td></tr>)}</tbody></table></div> : <div className="studio-empty">Run chưa có Spec nên chưa có bảng.</div>}</div>}
          {bottomTab === 'checks' && <div className="ide-checks"><div className="ide-checks-intro"><strong>RunReport · v{run.version}</strong><span>Chạy thử: {run.assets.some(a => a.layer === 'Gold' && a.state !== 'Chưa chạy') ? 'đã chạy' : 'chưa'} · Gọi LLM: {run.llmCalls} · Đã duyệt: {run.approval ? 'có' : 'chưa'}</span></div>{run.checks.length ? <div className="ide-check-grid">{run.checks.map(check => <button key={check.id} onClick={() => showLog(null, check.id)}><span className={`ide-check-icon ${check.state}`}>{check.state === 'pass' ? <Check size={13} /> : check.state === 'fail' ? <CircleAlert size={13} /> : <Clock3 size={13} />}</span><strong>{check.name}</strong><code>{check.value}</code><small>{check.detail}</small></button>)}</div> : <div className="studio-empty">Chưa có kết quả kiểm tra.</div>}</div>}
          {bottomTab === 'logs' && <RunLogs run={run} selectedStage={selectedStage} selectedCheck={selectedCheck} />}
          {bottomTab === 'plan' && <div className="ide-plan"><div className="ide-plan-head"><strong>Kế hoạch & Spec</strong><span>{run.domain} · {run.plan.schedule}</span></div>{run.summary && <p className="studio-muted">{run.summary}</p>}<div className="ide-plan-grid">{[['Nguồn', run.plan.sources.join(', ') || '—'], ['Bảng', run.plan.tables.join(', ') || '—'], ['Khóa chính', run.plan.primaryKey], ['Cursor', run.plan.cursor], ['Chế độ ghi', run.plan.writeMode], ['Model', run.plan.model], ['Chỉ số', run.plan.metric], ['Bộ lọc', run.plan.filters || 'Không có']].map(([key, value]) => <div key={key}><span>{key}</span><code>{value}</code></div>)}</div><div className="ide-plan-stages"><strong>Phase · {run.stage + 1}/{STAGES.length}</strong><div>{STAGES.map((stage, index) => <button key={stage} className={index === run.stage ? 'current' : index < run.stage ? 'done' : ''} onClick={() => showLog(index, null)} title={stage}>{index < run.stage ? <Check size={10} /> : index + 1}</button>)}</div></div></div>}
        </div>
      </section>
      {!running && list.length > 0 && run.status !== 'READY_FOR_REVIEW' && run.error && <div className="ide-inline-alert"><CircleAlert size={15} /> {run.error.split('\n')[0].slice(0, 220)} <button onClick={() => showLog(null, null)}>Xem log</button>{isEngineer && <button onClick={onEdit}>Sửa yêu cầu</button>}</div>}
      <ReviewBar run={run} role={role} user={user} onReview={onReview} />
      <div className="ide-statusbar"><span><span className="ide-run-dot good" /> Backend thật</span><span>{shortId(run.id)} · v{run.version} · phase {run.stage + 1}/{STAGES.length}</span><span>{canReview(run, role, user) ? 'Đủ điều kiện duyệt' : 'Mọi thay đổi production cần review'}</span></div>
    </section>
  </div>;
}
