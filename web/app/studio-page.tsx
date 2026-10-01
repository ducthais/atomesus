'use client';

import { useEffect, useRef, useState } from 'react';
import { ChevronRight, CircleAlert, Play, Settings2, Sparkles, X } from 'lucide-react';
import { demoRuns, makeRun, newVersion, type Plan, type Run, type Scenario } from '@/lib/studio';
import { applyRunEvent, mockPipelineApi, type RunSubscription } from '@/lib/studio-api';
import { AssistantPanel, PolicyViewer, RunsTable, Sidebar } from './components/studio-parts';
import { RequestWizard } from './components/studio-workflows';
import { IdeWorkspace } from './components/studio-ide';

type View = 'runs' | 'create' | 'detail' | 'policy';
const STORAGE = 'pipeline-studio-demo-v1';

export default function StudioPage() {
  const [runs, setRuns] = useState<Run[]>(() => demoRuns());
  const [hydrated, setHydrated] = useState(false);
  const [storageError, setStorageError] = useState(false);
  const [view, setView] = useState<View>('detail');
  const [selectedId, setSelectedId] = useState('RUN-024');
  const [role, setRole] = useState<'Engineer' | 'Reviewer'>('Engineer');
  const [collapsed, setCollapsed] = useState(false);
  const [assistant, setAssistant] = useState('plan');
  const [notice, setNotice] = useState('');
  const subscriptions = useRef(new Map<string, RunSubscription>());
  const run = runs.find(item => item.id === selectedId) ?? runs[0];
  const user = role === 'Engineer' ? 'minh' : 'an';

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE);
      if (raw) {
        const saved = JSON.parse(raw) as Run[];
        if (!Array.isArray(saved) || !saved.every(item => item.id && item.status && Array.isArray(item.checks) && Array.isArray(item.logs))) throw new Error('Invalid demo state');
        setRuns(saved);
      }
    } catch { setStorageError(true); }
    setHydrated(true);
  }, []);
  useEffect(() => { if (hydrated) localStorage.setItem(STORAGE, JSON.stringify(runs)); }, [runs, hydrated]);
  useEffect(() => {
    for (const item of runs) {
      const key = `${item.id}:v${item.version}`;
      if (item.status === 'VALIDATING' && !subscriptions.current.has(key)) {
        const sub = mockPipelineApi.subscribeToRun(item, event => {
          setRuns(prev => prev.map(current => current.id === item.id && current.version === item.version && current.status === 'VALIDATING' ? applyRunEvent(current, event) : current));
        });
        subscriptions.current.set(key, sub);
      }
    }
    for (const [key, sub] of subscriptions.current) {
      if (!runs.some(item => `${item.id}:v${item.version}` === key && item.status === 'VALIDATING')) { sub.cancel(); subscriptions.current.delete(key); }
    }
  }, [runs]);
  useEffect(() => { const live = subscriptions.current; return () => { for (const sub of live.values()) sub.cancel(); live.clear(); }; }, []);
  useEffect(() => {
    if (view === 'detail' && run?.status.startsWith('BLOCKED')) setAssistant(run.status === 'BLOCKED_BUDGET' ? 'budget' : run.status === 'BLOCKED_AST' ? 'ast' : 'semantic');
  }, [view, run?.status]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(''), 5000); return () => clearTimeout(timer); } }, [notice]);

  const patch = (id: string, update: (run: Run) => Run) => setRuns(prev => prev.map(item => item.id === id ? update(item) : item));
  const openRun = (id: string) => { setSelectedId(id); setView('detail'); setAssistant('plan'); };
  const createRun = (request: string, domain: 'Sales' | 'Marketing', scenario: Scenario, plan: Plan) => {
    if (role !== 'Engineer') return;
    const next = makeRun(request, domain, scenario, plan);
    setRuns(prev => [next, ...prev]); setSelectedId(next.id); setView('detail'); setAssistant('plan');
    setNotice('Đã tạo run trong sandbox mô phỏng.');
  };
  const retry = (scenario?: Scenario) => {
    patch(run.id, current => {
      const revised = newVersion(current, scenario ?? current.scenario);
      return current.status === 'BLOCKED_BUDGET' && scenario === 'success'
        ? { ...revised, plan: { ...revised.plan, partitionFilter: "o.updated_at >= current_date - interval '7 days'" } }
        : revised;
    });
    setNotice('Đã tạo version mới. Bằng chứng và phê duyệt cũ không còn hiệu lực.');
  };
  const review = (action: 'approve' | 'revise' | 'reject', comment: string) => {
    if (role !== 'Reviewer' || run.owner === user || run.status !== 'READY_FOR_REVIEW' || run.checks.some(c => c.state !== 'pass')) return;
    if (action === 'approve') { patch(run.id, current => ({ ...current, status: 'APPROVED', approvedHash: current.filesHash, reviewComment: comment })); setNotice('Đã duyệt đúng version và files_hash.'); }
    else if (action === 'reject' && comment.trim()) { patch(run.id, current => ({ ...current, status: 'REJECTED', reviewComment: comment })); setNotice('Đã từ chối run.'); }
    else if (action === 'revise' && comment.trim()) { patch(run.id, current => ({ ...newVersion(current), reviewComment: comment })); setNotice('Đã tạo version mới để kiểm tra lại theo nhận xét.'); }
  };
  const exportArtifact = () => {
    if (run.status !== 'APPROVED' || run.approvedHash !== run.filesHash) return;
    patch(run.id, current => ({ ...current, status: 'DEPLOYED', artifactPath: `demo-code-location/${current.id}/v${current.version}` }));
    setNotice('Đã mô phỏng ghi artifact vào code location; Dagster chưa chạy.');
  };

  return <div className={`studio-app ${view === 'detail' ? 'editor-mode' : ''}`}>
    <Sidebar view={view} role={role} onView={v => { setView(v); setAssistant(''); }} onNew={() => setView('create')} onRole={setRole} collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} runCount={runs.length} />
    <div className="studio-main"><header className="studio-topbar"><div className="studio-breadcrumbs"><button className="studio-mobile-runs" onClick={() => { setView('runs'); setAssistant(''); }}>Run</button>Pipeline Studio <ChevronRight size={13} /> {view === 'runs' ? 'Runs' : view === 'create' ? 'Tạo yêu cầu' : view === 'policy' ? 'Chính sách' : <><span>{run.title}</span><ChevronRight size={13} /><span className="studio-mono">{run.id}</span></>}</div><div className="studio-top-actions"><a className="studio-button" href="/postgres">Postgres</a>{view === 'detail' && <><button className="studio-button studio-header-tool" onClick={() => setView('policy')}><Settings2 size={14} /> Cài đặt</button><button className="studio-button studio-header-tool" disabled={role !== 'Engineer' || run.status === 'VALIDATING'} title={role !== 'Engineer' ? 'Chỉ Engineer chạy thử' : run.status === 'VALIDATING' ? 'Run đang chạy' : 'Tạo version mới và chạy thử trong sandbox'} onClick={() => retry('success')}><Play size={14} /> Chạy thử</button></>}<button className="studio-button studio-assistant-toggle" onClick={() => setAssistant(assistant ? '' : 'plan')}><Sparkles size={15} /> Trợ lý</button><label className="studio-top-role"><span>Vai trò</span><select value={role} onChange={event => setRole(event.target.value as 'Engineer' | 'Reviewer')}><option>Engineer</option><option>Reviewer</option></select></label><span className="studio-user">{user === 'minh' ? 'ML' : 'AN'}</span></div></header>
      <div className="studio-layout"><main className={`studio-content ${view === 'detail' ? 'studio-content-editor' : ''}`}>{!hydrated ? <div className="studio-loading">Đang tải workspace…</div> : <>
        {storageError && <div className="studio-alert"><CircleAlert size={16} /> Không đọc được dữ liệu demo đã lưu; đang dùng dữ liệu mẫu. <button onClick={() => { localStorage.removeItem(STORAGE); setStorageError(false); }}>Xóa dữ liệu cũ</button></div>}
        {view === 'runs' && <RunsTable runs={runs} role={role} onOpen={openRun} onNew={() => setView('create')} />}
        {view === 'create' && <RequestWizard enabled={role === 'Engineer'} onCancel={() => setView('runs')} onSubmit={createRun} />}
        {view === 'policy' && <PolicyViewer />}
        {view === 'detail' && <IdeWorkspace key={run.id} run={run} role={role} user={user} onBack={() => { setView('runs'); setAssistant(''); }} onCancel={() => { patch(run.id, current => ({ ...current, status: 'CANCELLED' })); setNotice('Run đã dừng; không còn sự kiện mới.'); }} onRetry={retry} onDomain={() => { setView('create'); setNotice('Chọn domain phù hợp trong yêu cầu mới.'); }} onTicket={setNotice} onReview={review} onExport={exportArtifact} onExplain={setAssistant} />}
      </>}</main>{assistant && <AssistantPanel context={assistant} run={view === 'detail' ? run : undefined} onClose={() => setAssistant('')} />}</div>
    </div>
    {notice && <div className="studio-toast" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Đóng thông báo"><X size={15} /></button></div>}
  </div>;
}
