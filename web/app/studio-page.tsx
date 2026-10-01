'use client';

import { useCallback, useEffect, useState } from 'react';
import { ChevronRight, CircleAlert, LogIn, Play, Settings2, Sparkles, X } from 'lucide-react';
import { ApiError, apiRequest, pipelineRequest, type RunReport, type User } from '@/lib/postgres-api';
import { RUNNING, pendingRun, shortId, toArtifacts, toStudioRun, type FileArtifact, type Limits, type Run } from '@/lib/studio';
import { AssistantPanel, PolicyViewer, RunsTable, Sidebar } from './components/studio-parts';
import { RequestWizard, deployPath, type ReviewDecision } from './components/studio-workflows';
import { IdeWorkspace } from './components/studio-ide';

type View = 'runs' | 'create' | 'detail' | 'policy';
type Policy = { files: Record<string, string>; limits: Limits };
const message = (err: unknown) => err instanceof Error ? err.message : 'Thao tác thất bại. Vui lòng thử lại.';

export default function StudioPage() {
  const [user, setUser] = useState<User | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [runs, setRuns] = useState<Run[]>([]);
  const [pending, setPending] = useState<Run[]>([]);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [files, setFiles] = useState<Record<string, FileArtifact[]>>({});
  const [view, setView] = useState<View>('runs');
  const [selectedId, setSelectedId] = useState('');
  const [draft, setDraft] = useState('');
  const [collapsed, setCollapsed] = useState(false);
  const [assistant, setAssistant] = useState('');
  const [notice, setNotice] = useState('');
  const allRuns = [...pending, ...runs];
  const run = allRuns.find(item => item.id === selectedId);

  const fail = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) { setUser(null); setRuns([]); setView('runs'); }
    setNotice(message(err));
  }, []);
  const refreshRuns = useCallback(async () => {
    const reports = await apiRequest<RunReport[]>('/runs');
    setRuns(reports.map(toStudioRun));
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const choices = await pipelineRequest<User[]>('/users');
        if (active) setUsers(choices);
        try { const current = await pipelineRequest<User>('/session'); if (active) setUser(current); }
        catch (err) { if (!(err instanceof ApiError && err.status === 401)) throw err; }
      } catch (err) { if (active) setNotice(message(err)); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!user) return;
    refreshRuns().catch(fail);
    apiRequest<Policy>('/policy').then(setPolicy).catch(fail);
  }, [user, refreshRuns, fail]);
  useEffect(() => {
    if (view !== 'detail' || !run || run.pending || !run.fileCount || files[run.id]) return;
    apiRequest<{ files: Record<string, string> }>(`/runs/${encodeURIComponent(run.id)}/files`)
      .then(body => setFiles(prev => ({ ...prev, [run.id]: toArtifacts(body.files) })))
      .catch(fail);
  }, [view, run, files, fail]);
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(''), 6000); return () => clearTimeout(timer); } }, [notice]);

  const openRun = (id: string) => { setSelectedId(id); setView('detail'); setAssistant(''); };
  const login = async (id: string) => {
    try { setUser(await pipelineRequest<User>('/session', { method: 'POST', body: JSON.stringify({ user_id: id }) })); }
    catch (err) { fail(err); }
  };
  const logout = async () => {
    try { await pipelineRequest('/session', { method: 'DELETE' }); } catch (err) { fail(err); }
    setUser(null); setRuns([]); setPending([]); setView('runs');
  };
  const createRun = async (request: string) => {
    if (user?.role !== 'Engineer') return;
    const placeholder = pendingRun(request, user.id);
    setPending(prev => [placeholder, ...prev]); setSelectedId(placeholder.id); setView('detail'); setAssistant('');
    try {
      const report = await apiRequest<RunReport>('/agent/runs', { method: 'POST', body: JSON.stringify({ request }) });
      const created = toStudioRun(report);
      setRuns(prev => [created, ...prev.filter(item => item.id !== created.id)]);
      setSelectedId(current => current === placeholder.id ? created.id : current);
      setNotice(`Run ${shortId(created.id)}: ${created.status === 'READY_FOR_REVIEW' ? 'mọi kiểm tra đạt, chờ duyệt.' : 'đã dừng, xem lý do trong RunReport.'}`);
    } catch (err) {
      fail(err);
      setSelectedId(current => current === placeholder.id ? '' : current);
      setView(current => current === 'detail' ? 'runs' : current);
    } finally {
      setPending(prev => prev.filter(item => item.id !== placeholder.id));
    }
  };
  const editRequest = (request: string) => { setDraft(request); setView('create'); };
  const reviewRun = async (target: Run, decision: ReviewDecision, comment: string) => {
    try {
      const report = await apiRequest<RunReport>(`/runs/${encodeURIComponent(target.id)}/${decision}`, { method: 'POST', body: JSON.stringify({ files_hash: target.filesHash || null, comment }) });
      const updated = toStudioRun(report);
      setRuns(prev => prev.map(item => item.id === updated.id ? updated : item));
      setNotice(updated.status === 'DEPLOYED' ? `Đã duyệt. Project Dagster ghi tại ${deployPath(updated)}.`
        : updated.status === 'DEPLOY_FAILED' ? 'Đã duyệt nhưng ghi project Dagster thất bại, xem log.' : `Run ${shortId(updated.id)}: ${updated.status === 'REJECTED' ? 'đã từ chối' : updated.status}.`);
    } catch (err) {
      fail(err);
      refreshRuns().catch(fail); // 409 thường do run đã đổi: tải lại để thấy trạng thái mới
    }
  };

  const crumb = view === 'runs' ? 'Runs' : view === 'create' ? 'Tạo yêu cầu' : view === 'policy' ? 'Chính sách' : run ? <><span>{run.title}</span><ChevronRight size={13} /><span className="studio-mono">{shortId(run.id)}</span></> : 'Run';
  if (loading) return <div className="studio-app"><div className="studio-loading">Đang kết nối backend…</div></div>;
  if (!user) return <div className="studio-app"><main className="studio-content"><div className="studio-page-head"><div><div className="studio-kicker">PIPELINE STUDIO</div><h1>Chọn người dùng</h1><p>Đăng nhập demo do backend quản lý (bị khóa khi APP_ENV=production). Engineer tạo pipeline; Reviewer xem và duyệt.</p></div></div>
    <section className="studio-surface studio-wizard-main"><div className="studio-action-row">{users.map(item => <button key={item.id} className="studio-button primary" onClick={() => login(item.id)}><LogIn size={15} /> {item.name} · {item.role}</button>)}</div>{!users.length && <div className="studio-alert"><CircleAlert size={16} /> Không lấy được danh sách người dùng. Kiểm tra backend ở cổng 8000. <button className="studio-button" onClick={() => window.location.reload()}>Thử lại</button></div>}</section></main>
    {notice && <div className="studio-toast" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Đóng thông báo"><X size={15} /></button></div>}</div>;

  return <div className={`studio-app ${view === 'detail' ? 'editor-mode' : ''}`}>
    <Sidebar view={view} user={user} onView={v => { setView(v); setAssistant(''); }} onNew={() => editRequest('')} onLogout={logout} collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} runCount={allRuns.length} />
    <div className="studio-main"><header className="studio-topbar"><div className="studio-breadcrumbs"><button className="studio-mobile-runs" onClick={() => { setView('runs'); setAssistant(''); }}>Run</button>Pipeline Studio <ChevronRight size={13} /> {crumb}</div><div className="studio-top-actions"><a className="studio-button" href="/postgres">Postgres</a>{view === 'detail' && run && <><button className="studio-button studio-header-tool" onClick={() => setView('policy')}><Settings2 size={14} /> Chính sách</button><button className="studio-button studio-header-tool" disabled={user.role !== 'Engineer' || run.pending || RUNNING.includes(run.status)} title={user.role !== 'Engineer' ? 'Chỉ Engineer chạy agent' : 'Gửi lại cùng yêu cầu, tạo run mới'} onClick={() => createRun(run.request)}><Play size={14} /> Chạy lại</button></>}<button className="studio-button studio-assistant-toggle" onClick={() => setAssistant(assistant ? '' : 'run')}><Sparkles size={15} /> Trợ lý</button><span className="studio-user" title={`${user.name} · ${user.role}`}>{user.name.slice(0, 2).toUpperCase()}</span></div></header>
      <div className="studio-layout"><main className={`studio-content ${view === 'detail' ? 'studio-content-editor' : ''}`}>
        {view === 'runs' && <RunsTable runs={allRuns} user={user} limits={policy?.limits ?? null} onOpen={openRun} onNew={() => editRequest('')} />}
        {view === 'create' && <RequestWizard key={draft} initial={draft} enabled={user.role === 'Engineer'} onCancel={() => setView('runs')} onSubmit={createRun} />}
        {view === 'policy' && <PolicyViewer files={policy?.files ?? null} />}
        {view === 'detail' && (run ? <IdeWorkspace key={run.id} run={run} files={run.fileCount ? files[run.id] ?? null : []} role={user.role} user={user.id} onBack={() => { setView('runs'); setAssistant(''); }} onRetry={() => createRun(run.request)} onEdit={() => editRequest(run.request)} onExplain={setAssistant} onReview={(decision, comment) => reviewRun(run, decision, comment)} /> : <div className="studio-empty">Không tìm thấy run. <button className="studio-button" onClick={() => setView('runs')}>Về danh sách</button></div>)}
      </main>{assistant && <AssistantPanel context={assistant} run={view === 'detail' ? run : undefined} onClose={() => setAssistant('')} />}</div>
    </div>
    {notice && <div className="studio-toast" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Đóng thông báo"><X size={15} /></button></div>}
  </div>;
}
