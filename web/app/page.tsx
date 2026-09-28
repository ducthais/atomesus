'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bell, BookLock, CircleHelp, Filter, Grid3x3, LayoutGrid, ListChecks, PanelRightOpen, Plus, RefreshCw, Search, Settings,
  Share2, Sparkles, Users, Workflow,
} from 'lucide-react';
import { AgentMenu, type AgentFn } from '@/components/AgentMenu';
import { BottomPanel, type Tab } from '@/components/BottomPanel';
import { DetailsPane } from '@/components/DetailsPane';
import { FlowCanvas } from '@/components/FlowCanvas';
import { PolicyView } from '@/components/PolicyView';
import { DEMO, api } from '@/lib/api';
import { RECORDED_AT } from '@/lib/demo/engine';
import { usd } from '@/lib/format';
import { useRun, useRuns } from '@/lib/hooks';
import type { BudgetToday, PolicyResponse, Role, RunStatus, Viewer } from '@/lib/types';
import { canCreate } from '@/lib/workflow';

// Hai người dùng demo: engineer tạo run, reviewer duyệt. Chưa có xác thực thật (FR-01 ở bản sau).
const USERS: Record<Role, string> = { Engineer: 'minh', Reviewer: 'an' };

type Page = 'workspace' | 'review' | 'policy';
type StatusFilter = 'all' | 'review' | 'blocked' | 'running' | 'deployed';
const FILTERS: Record<StatusFilter, { label: string; match: (s: RunStatus) => boolean }> = {
  all: { label: 'Tất cả', match: () => true },
  review: { label: 'Chờ duyệt', match: (s) => s === 'READY_FOR_REVIEW' },
  blocked: { label: 'Bị chặn / cần người', match: (s) => s.startsWith('BLOCKED') || s === 'NEEDS_HUMAN' },
  running: { label: 'Đang chạy', match: (s) => ['DRAFT', 'PLAN_CONFIRMED', 'GENERATING', 'VALIDATING'].includes(s) },
  deployed: { label: 'Đã duyệt / deploy', match: (s) => s === 'APPROVED' || s === 'DEPLOYED' },
};

function domainsOf(policy: PolicyResponse | null): string[] {
  const yml = policy?.files['policy/domains.yml'] ?? '';
  return [...yml.matchAll(/^ {2}([a-z_]+):\s*$/gm)].map((m) => m[1]);
}

export default function Home() {
  const [role, setRole] = useState<Role>('Engineer');
  const viewer: Viewer = { role, user: USERS[role] };
  const [page, setPage] = useState<Page>('workspace');
  const [selected, setSelected] = useState<string | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [agent, setAgent] = useState<{ open: boolean; fn: AgentFn | null }>({ open: false, fn: null });
  const [pane, setPane] = useState(true);
  const [tab, setTab] = useState<Tab>('items');
  const [collapsed, setCollapsed] = useState(false);
  const [height, setHeight] = useState(300);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [toast, setToast] = useState<{ msg: string; bad: boolean } | null>(null);
  const [policy, setPolicy] = useState<PolicyResponse | null>(null);
  const [budget, setBudget] = useState<BudgetToday | null>(null);

  const { runs, error: runsError, refresh } = useRuns();
  const { view, events, reload } = useRun(selected);

  useEffect(() => { api.policy().then(setPolicy).catch(() => setPolicy(null)); }, []);
  useEffect(() => {
    const load = () => api.budgetToday().then(setBudget).catch(() => setBudget(null));
    void load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(null), 4500); return () => clearTimeout(t); }, [toast]);

  const notify = useCallback((msg: string, bad = false) => setToast({ msg, bad }), []);
  const openRun = useCallback((id: string) => { setSelected(id); setGroup(null); setPage('workspace'); void refresh(); }, [refresh]);
  const askAgent = (fn: AgentFn | null) => setAgent({ open: true, fn });

  // Kéo thanh chia để đổi chiều cao panel dưới.
  const drag = useRef<{ y: number; h: number } | null>(null);
  useEffect(() => {
    const move = (e: MouseEvent) => { if (drag.current) setHeight(Math.min(window.innerHeight - 260, Math.max(120, drag.current.h + drag.current.y - e.clientY))); };
    const up = () => { drag.current = null; document.body.style.userSelect = ''; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, []);

  const filtered = runs.filter((r) => FILTERS[page === 'review' ? 'review' : status].match(r.status));
  const waiting = runs.filter((r) => r.status === 'READY_FOR_REVIEW').length;
  const domains = domainsOf(policy);

  async function rerun() {
    if (!view) return;
    try { const r = await api.rerun(view.run_id, viewer.user); openRun(r.run_id); askAgent('track'); } catch (e) { notify((e as Error).message, true); }
  }
  async function cancel() {
    if (!view) return;
    try { await api.cancel(view.run_id); notify('Đã gửi yêu cầu huỷ'); void reload(); } catch (e) { notify((e as Error).message, true); }
  }

  return (
    <div className="app">
      <header className="topbar">
        <button className="waffle" aria-label="Ứng dụng"><Grid3x3 size={18} /></button>
        <span className="brand">VSF Pipeline</span>
        <div className="search">
          <Search size={16} />
          <input placeholder="Tìm run, bảng, trạng thái…" value={query} onChange={(e) => { setQuery(e.target.value); setTab('items'); }} />
        </div>
        <div className="topbar-right">
          {budget && (
            <span className="pill" title="per_day.max_usd trong budgets.yml">
              Hôm nay <b>{usd(budget.spent_usd)}</b> / {usd(budget.max_usd)}{budget.is_estimate ? ' · ước tính' : ''}
            </span>
          )}
          <button className="icon-btn" aria-label="Thông báo"><Bell size={18} /></button>
          <button className="icon-btn" aria-label="Cài đặt"><Settings size={18} /></button>
          <button className="icon-btn" aria-label="Trợ giúp"><CircleHelp size={18} /></button>
          <button className={`icon-btn ${agent.open ? 'on' : ''}`} aria-label="Agent" title="Agent" onClick={() => setAgent((a) => ({ open: !a.open, fn: null }))}>
            <Sparkles size={19} />
          </button>
          <label className="role">
            <select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="Vai trò">
              <option value="Engineer">Engineer · minh</option>
              <option value="Reviewer">Reviewer · an</option>
            </select>
            <span className={`avatar ${role === 'Reviewer' ? 'reviewer' : ''}`}>{viewer.user.slice(0, 2).toUpperCase()}</span>
          </label>
        </div>
      </header>

      <nav className="rail" aria-label="Điều hướng">
        <button className={`rail-item ${page === 'workspace' ? 'active' : ''}`} onClick={() => setPage('workspace')}><LayoutGrid size={22} />Workspace</button>
        <button className={`rail-item ${page === 'review' ? 'active' : ''}`} onClick={() => { setPage('review'); setTab('items'); setCollapsed(false); }}>
          <ListChecks size={22} />Chờ duyệt{waiting > 0 && <span className="count">{waiting}</span>}
        </button>
        <button className={`rail-item ${page === 'policy' ? 'active' : ''}`} onClick={() => setPage('policy')}><BookLock size={22} />Chính sách</button>
        <button className={`rail-item ${agent.open ? 'active' : ''}`} onClick={() => askAgent(null)}><Sparkles size={22} />Agent</button>
        <div className="rail-bottom">
          <button className="rail-item" onClick={() => void refresh()}><RefreshCw size={20} />Làm mới</button>
        </div>
      </nav>

      <main className="main">
        {DEMO ? (
          <div className="banner demo">
            <b>Chế độ demo</b>
            <span>Không cần backend: giao diện phát lại các run thật đã ghi lúc {RECORDED_AT.replace('T', ' ')}. Số đo là số thật; agent trả lời theo kịch bản đã ghi.</span>
          </div>
        ) : (
          <div className="banner">
            <b>Sandbox</b>
            <span>Mọi run chạy trên sandbox riêng, xoá sau khi xong. Deploy mức D1 chỉ ghi vào code location sandbox, không chạm prod.</span>
          </div>
        )}

        {page === 'policy' ? <PolicyView policy={policy} /> : (
          <>
            <div className="ws-head">
              <div className="ws-title">
                <span className="ws-icon"><Workflow size={18} /></span>
                <h1>{page === 'review' ? 'Hàng đợi review' : 'Pipelines'}</h1>
                <div className="ws-links">
                  <span><Users size={15} />Engineer tạo · Reviewer duyệt</span>
                  <span><Share2 size={15} />{policy ? `Trần LLM ${policy.limits.llm} lần/run` : 'Chưa tải chính sách'}</span>
                </div>
              </div>
              <div className="cmdbar">
                <button className="cmd primary" disabled={!canCreate(viewer)} title={canCreate(viewer) ? undefined : 'Chỉ Engineer tạo run'} onClick={() => askAgent('create')}>
                  <Plus size={16} />Pipeline mới
                </button>
                <button className="cmd" disabled title="UC-2, sắp có"><LayoutGrid size={16} />Onboard hàng loạt</button>
                <span className="cmd-sep" />
                <button className="cmd" onClick={() => askAgent('track')} disabled={!view}><Sparkles size={16} />Hỏi agent về run này</button>
                <div className="cmd-right">
                  <div className="filter-box">
                    <Search size={14} />
                    <input placeholder="Lọc theo từ khoá" value={query} onChange={(e) => { setQuery(e.target.value); setTab('items'); }} />
                  </div>
                  {page === 'workspace' && (
                    <label className="cmd" style={{ gap: 6 }}>
                      <Filter size={15} />
                      <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} style={{ border: 0, padding: 0, background: 'transparent' }} aria-label="Bộ lọc trạng thái">
                        {Object.entries(FILTERS).map(([k, f]) => <option key={k} value={k}>{f.label}</option>)}
                      </select>
                    </label>
                  )}
                  {!pane && <button className="icon-btn" aria-label="Mở pane chi tiết" onClick={() => setPane(true)}><PanelRightOpen size={18} /></button>}
                </div>
              </div>
            </div>

            <div className={`work ${pane ? '' : 'no-pane'}`}>
              <div className="center">
                <FlowCanvas
                  view={view}
                  events={events}
                  selectedGroup={group}
                  onSelectGroup={(g) => { setGroup(g); if (g) setPane(true); }}
                  onNew={() => askAgent('create')}
                  onRerun={() => void rerun()}
                  onCancel={() => void cancel()}
                  canCreate={canCreate(viewer)}
                />
                <div className="splitter" onMouseDown={(e) => { drag.current = { y: e.clientY, h: height }; document.body.style.userSelect = 'none'; setCollapsed(false); }} />
                <div style={{ height: collapsed ? 'auto' : height, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                  <BottomPanel
                    runs={filtered}
                    runsError={runsError}
                    filter={query}
                    selected={selected}
                    onSelect={openRun}
                    view={view}
                    events={events}
                    viewer={viewer}
                    tab={tab}
                    onTab={setTab}
                    collapsed={collapsed}
                    onToggle={() => setCollapsed((c) => !c)}
                    onReload={() => { void reload(); void refresh(); }}
                    onToast={notify}
                    onAskChanges={() => askAgent('changes')}
                    onExplainFile={() => askAgent('explain')}
                  />
                </div>
              </div>
              {pane && (
                <DetailsPane
                  view={view}
                  events={events}
                  selectedGroup={group}
                  viewer={viewer}
                  onClose={() => setPane(false)}
                  onAskAgent={askAgent}
                  onToast={notify}
                  onOpenRun={openRun}
                />
              )}
            </div>
          </>
        )}
      </main>

      {agent.open && (
        <AgentMenu
          viewer={viewer}
          view={view}
          domains={domains.length ? domains : ['sales', 'marketing']}
          initial={agent.fn}
          onClose={() => setAgent({ open: false, fn: null })}
          onRunCreated={openRun}
        />
      )}
      {toast && <div className={`toast ${toast.bad ? 'bad' : ''}`} role="status">{toast.msg}</div>}
    </div>
  );
}
