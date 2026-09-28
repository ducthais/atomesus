'use client';

import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ArrowLeft, BookOpenCheck, Bot, FileSearch, HelpCircle, Layers, MessageSquareText, Search, Send, Sparkles, TrendingDown,
  Wand2, X, Zap,
} from 'lucide-react';
import { api, subscribe } from '@/lib/api';
import { PHASES, code, isTerminal } from '@/lib/phases';
import type { Plan, RunEvent, RunView, Viewer } from '@/lib/types';
import { canCreate, canRequestChanges } from '@/lib/workflow';

export type AgentFn = 'create' | 'track' | 'why' | 'explain' | 'changes';

interface MenuItem {
  id: AgentFn | 'batch' | 'optimize' | 'regress';
  section: string;
  title: string;
  sub: string;
  color: string;
  icon: typeof Bot;
  soon?: string;
}

const ITEMS: MenuItem[] = [
  { id: 'create', section: 'Tạo và chạy', title: 'Tạo pipeline', sub: 'Mô tả yêu cầu, agent lập kế hoạch rồi chạy thử trên sandbox', color: '#6d3fd1', icon: Wand2 },
  { id: 'track', section: 'Tạo và chạy', title: 'Theo dõi run', sub: 'Tiến trình P0–P17 của run đang chọn, cập nhật trực tiếp', color: '#0f6cbd', icon: Activity },
  { id: 'batch', section: 'Tạo và chạy', title: 'Onboard hàng loạt', sub: 'UC-2: sinh N pipeline cùng quy ước, duyệt theo mẻ', color: '#117865', icon: Layers, soon: 'Sắp có' },
  { id: 'why', section: 'Giải thích', title: 'Vì sao bị chặn', sub: 'Lý do dừng, trích đúng dòng trong policy/*.yml', color: '#c50f1f', icon: HelpCircle },
  { id: 'explain', section: 'Giải thích', title: 'Giải thích code', sub: 'Chọn file và dòng, agent nói khối đó làm gì, đến từ đâu trong spec', color: '#5c2e91', icon: FileSearch },
  { id: 'changes', section: 'Quyết định', title: 'Góp ý cho agent', sub: 'Gửi nhận xét, agent sinh lại spec từ P4 (đặt lại attempts.spec)', color: '#ca5010', icon: MessageSquareText },
  { id: 'optimize', section: 'Vận hành · Day 2', title: 'Tối ưu model', sub: 'UC-3: trỏ vào model đã deploy, chỉ đề xuất khi đúng và nhanh hơn', color: '#986f0b', icon: Zap, soon: 'Sắp có' },
  { id: 'regress', section: 'Vận hành · Day 2', title: 'Theo dõi suy giảm', sub: 'UC-4: monitor phát hiện chậm/đắt dần, tự đề xuất bản sửa', color: '#0078a8', icon: TrendingDown, soon: 'Sắp có' },
];

interface Props {
  viewer: Viewer;
  view: RunView | null;
  domains: string[];
  initial: AgentFn | null;
  onClose: () => void;
  onRunCreated: (id: string) => void;
}

export function AgentMenu({ viewer, view, domains, initial, onClose, onRunCreated }: Props) {
  const [fn, setFn] = useState<AgentFn | null>(initial);
  const [query, setQuery] = useState('');
  const [seed, setSeed] = useState('');

  useEffect(() => setFn(initial), [initial]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const blocked = !!view && (view.status.startsWith('BLOCKED') || view.status === 'NEEDS_HUMAN');
  const reason = (id: MenuItem['id']): string | null => {
    if (id === 'create') return canCreate(viewer) ? null : 'Chỉ Engineer tạo run';
    if (id === 'track') return view ? null : 'Chọn một run trước';
    if (id === 'why') return blocked ? null : 'Run đang chọn không bị chặn';
    if (id === 'explain') return view && Object.keys(view.files).length ? null : 'Run chưa có file';
    if (id === 'changes') return view && canRequestChanges(view, viewer) ? null : 'Không áp dụng cho run/vai trò này';
    return null;
  };

  const shown = ITEMS.filter((i) => `${i.title} ${i.sub}`.toLowerCase().includes(query.toLowerCase()));
  const sections = [...new Set(shown.map((i) => i.section))];

  if (fn) {
    const item = ITEMS.find((i) => i.id === fn)!;
    return (
      <div className="agent-pop chat" role="dialog" aria-label={item.title}>
        <div className="agent-head">
          <button className="icon-btn" aria-label="Quay lại menu" onClick={() => setFn(null)}><ArrowLeft size={18} /></button>
          <div className="grow">
            <b>{item.title}</b>
            <small>{fn === 'create' ? 'Run mới' : view ? `Run ${view.run_id.slice(0, 8)} · v${view.version}` : ''}</small>
          </div>
          <button className="icon-btn" aria-label="Đóng" onClick={onClose}><X size={18} /></button>
        </div>
        <AgentChat key={`${fn}-${fn === 'create' ? '' : view?.run_id}`} fn={fn} viewer={viewer} view={view} domains={domains} seed={seed} onRunCreated={onRunCreated} />
      </div>
    );
  }

  return (
    <div className="agent-pop" role="dialog" aria-label="Agent">
      <div className="agent-search">
        <Search size={16} color="var(--muted)" />
        <input
          autoFocus
          placeholder="Hỏi agent hoặc tìm chức năng…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !query.trim()) return;
            if (shown.length === 0 && canCreate(viewer)) { setSeed(query); setFn('create'); }
            else if (shown[0] && !shown[0].soon && !reason(shown[0].id)) setFn(shown[0].id as AgentFn);
          }}
        />
        <button className="icon-btn" aria-label="Đóng" onClick={onClose}><X size={18} /></button>
      </div>
      <div className="agent-list">
        {sections.map((s) => (
          <div key={s}>
            <div className="agent-section">{s}</div>
            {shown.filter((i) => i.section === s).map((i) => {
              const why = i.soon ? null : reason(i.id);
              const Icon = i.icon;
              return (
                <button key={i.id} className="agent-item" disabled={!!i.soon || !!why} title={why ?? undefined}
                  onClick={() => setFn(i.id as AgentFn)}>
                  <span className="ai-icon" style={{ background: i.color }}><Icon size={20} /></span>
                  <span><b>{i.title}</b><span className="sub">{why ?? i.sub}</span></span>
                  {i.soon && <span className="tag">{i.soon}</span>}
                </button>
              );
            })}
          </div>
        ))}
        {shown.length === 0 && (
          <div className="empty" style={{ padding: 20 }}>
            Không có chức năng khớp. Nhấn Enter để gửi “{query}” thành yêu cầu tạo pipeline.
          </div>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- chat

type Msg =
  | { kind: 'user'; text: string }
  | { kind: 'agent'; text: string; bullets?: string[]; plan?: Plan; runId?: string; open?: boolean }
  | { kind: 'sys'; text: string; stage?: string; fail?: boolean };

const EXAMPLES = [
  'Đưa bảng đơn hàng từ Postgres về warehouse, cập nhật mỗi giờ, rồi tính doanh thu theo ngày.',
  'Hợp nhất danh sách khách hàng của Sales và Marketing thành một bảng.',
  'Tổng hợp lượt truy cập web theo ngày từ web_events.',
];

function planBullets(p: Plan): [string, string][] {
  return [
    ['Bảng nguồn', p.source_table ?? '—'],
    ['Độ hạt', (p.grain ?? []).join(', ') || '—'],
    ['Chỉ số', (p.metrics ?? []).map((m) => `${m.name}: ${m.formula}`).join('; ') || '—'],
    ['Khoá chống trùng', (p.dedupe_key ?? []).join(', ') || '—'],
    ['Cursor', p.cursor_field ?? '—'],
    ['Ghi', p.write_mode ?? '—'],
    ['Lịch', p.schedule ?? '—'],
  ];
}

function AgentChat({ fn, viewer, view, domains, seed, onRunCreated }: {
  fn: AgentFn; viewer: Viewer; view: RunView | null; domains: string[]; seed: string; onRunCreated: (id: string) => void;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState(seed);
  const [domain, setDomain] = useState(view?.domain || domains[0] || 'sales');
  const [runId, setRunId] = useState<string | null>(fn === 'create' ? null : view?.run_id ?? null);
  const [awaiting, setAwaiting] = useState<'confirm' | 'answer' | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState(Object.keys(view?.files ?? {})[1] ?? Object.keys(view?.files ?? {})[0] ?? '');
  const [range, setRange] = useState({ start: 1, end: 12 });
  const log = useRef<HTMLDivElement>(null);
  const explained = useRef(false); // chỉ giải thích lý do dừng một lần cho mỗi run
  const push = (m: Msg) => setMsgs((prev) => [...prev, m]);

  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: 'smooth' }); }, [msgs]);

  // Lời mở đầu theo chức năng. Chế độ tạo mới không phụ thuộc run đang chọn (run vừa tạo sẽ được chọn ngay).
  const viewId = fn === 'create' ? null : view?.run_id;
  useEffect(() => {
    let cancelled = false;
    const intro: Record<AgentFn, string> = {
      create: 'Mô tả pipeline bạn cần. Tôi sẽ lập kế hoạch để bạn xác nhận trước khi sinh bất kỳ dòng code nào.',
      track: 'Tiến trình của run, theo từng phase P0–P17.',
      why: 'Đang đọc RunReport và bộ luật…',
      explain: 'Chọn file và khoảng dòng. Tôi giải thích khối đó làm gì và nó đến từ trường nào trong spec.',
      changes: view?.status === 'READY_FOR_REVIEW'
        ? 'Góp ý của bạn là chỉ thị của con người: agent quay về P4, sinh lại spec, attempts.spec đặt về 0. Bản duyệt cũ (nếu có) mất hiệu lực.'
        : 'Agent đã dừng. Gửi góp ý cụ thể (ví dụ: “chỉ lấy 30 ngày”, “bỏ cột payload”), agent sẽ sinh lại spec từ P4.',
    };
    setMsgs([{ kind: 'agent', text: intro[fn] }]);
    if (fn === 'why' && viewId) {
      api.explain(viewId)
        .then((ex) => { if (!cancelled) setMsgs([{ kind: 'agent', text: `${ex.title}. ${ex.summary}`, bullets: ex.bullets }]); })
        .catch((e: Error) => { if (!cancelled) push({ kind: 'sys', text: e.message, fail: true }); });
    }
    return () => { cancelled = true; };
  }, [fn, viewId]); // chỉ chạy lại khi đổi chức năng hoặc đổi run, không phải mỗi lần view làm mới

  // Luồng sự kiện của run: tạo mới, theo dõi, hoặc sau khi góp ý.
  useEffect(() => {
    if (!runId || fn === 'why' || fn === 'explain') return undefined;
    let closed = false;
    let stop: (() => void) | null = null;
    const onEvent = (e: RunEvent) => {
      if (closed) return;
      const tag = `${code(e.stage)} ${PHASES[e.stage].label}`;
      // P1 hỏi lại (tối đa 1 câu): sự kiện plan/escalate kèm câu hỏi làm rõ.
      if (e.stage === 'plan' && e.status === 'escalate') {
        const plan = e.payload.plan as unknown as Plan | undefined;
        if (plan?.clarifying_question) {
          push({ kind: 'agent', text: plan.clarifying_question });
          setAwaiting('answer');
          return;
        }
      }
      if (e.stage === 'confirm' && e.status === 'running') {
        const plan = e.payload.plan as unknown as Plan | undefined;
        push({ kind: 'agent', text: 'Kế hoạch đây. Xác nhận thì tôi mới phân giải schema và sinh spec (cổng HITL 1).', plan });
        setAwaiting('confirm');
        return;
      }
      if (e.status === 'running' && e.stage !== 'review') return;
      push({ kind: 'sys', text: e.message, stage: tag, fail: e.status === 'fail' || e.status === 'blocked' || e.status === 'escalate' });
      if (e.stage === 'report' && e.status === 'pass') {
        push({ kind: 'agent', text: 'Pipeline đã chạy thử xong trên sandbox và có đủ bằng chứng. Run đang chờ Reviewer duyệt (P15).', runId: e.run_id, open: true });
      }
      if ((e.status === 'blocked' || e.status === 'escalate') && !explained.current) {
        api.run(e.run_id).then((v) => {
          if (!isTerminal(v.status) || explained.current) return;
          explained.current = true;
          return api.explain(e.run_id).then((ex) => push({ kind: 'agent', text: `${ex.title}. ${ex.summary}`, bullets: ex.bullets, runId: e.run_id, open: true }));
        }).catch(() => undefined);
      }
    };
    void (async () => {
      const past = await api.events(runId).catch(() => [] as RunEvent[]);
      if (closed) return;
      // Góp ý: chỉ hiện những gì xảy ra sau góp ý. Tạo mới / theo dõi: phát lại từ đầu.
      if (fn !== 'changes') past.forEach(onEvent);
      stop = subscribe(runId, past.at(-1)?.seq ?? 0, onEvent);
    })();
    return () => { closed = true; stop?.(); };
  }, [runId, fn]);

  async function send() {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      if (fn === 'create' && !runId) {
        push({ kind: 'user', text: t });
        const r = await api.createRun(t, domain, viewer.user);
        push({ kind: 'sys', text: `Đã tạo run ${r.run_id.slice(0, 8)} · domain ${domain}`, stage: 'P0 Tiếp nhận' });
        setRunId(r.run_id);
        onRunCreated(r.run_id);
      } else if (awaiting === 'answer' && runId) {
        push({ kind: 'user', text: t });
        await api.answer(runId, t);
        setAwaiting(null);
      } else if (fn === 'changes' && view) {
        push({ kind: 'user', text: t });
        await api.requestChanges(view.run_id, t, viewer.user);
        push({ kind: 'sys', text: 'Đã gửi góp ý, agent quay về P4', stage: 'P15 Review' });
        setRunId(view.run_id);
      }
      setText('');
    } catch (e) {
      push({ kind: 'sys', text: e instanceof Error ? e.message : String(e), fail: true });
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!runId) return;
    setAwaiting(null);
    push({ kind: 'user', text: 'Xác nhận kế hoạch' });
    try { await api.confirmPlan(runId, viewer.user); } catch (e) { push({ kind: 'sys', text: (e as Error).message, fail: true }); }
  }

  async function explainBlock() {
    if (!view || !file) return;
    push({ kind: 'user', text: `${file}:${range.start}-${range.end}` });
    try {
      const ex = await api.explainBlock(view.run_id, file, range.start, range.end);
      push({ kind: 'agent', text: ex.summary, bullets: ex.bullets });
    } catch (e) { push({ kind: 'sys', text: (e as Error).message, fail: true }); }
  }

  const inputEnabled = (fn === 'create' && (!runId || awaiting === 'answer')) || (fn === 'changes' && !runId) || (fn === 'track' && awaiting === 'answer');
  const files = useMemo(() => Object.keys(view?.files ?? {}), [view]);

  return (
    <>
      <div className="chat-log" ref={log}>
        {msgs.map((m, i) => {
          if (m.kind === 'user') return <div key={i} className="msg user">{m.text}</div>;
          if (m.kind === 'sys') {
            return (
              <div key={i} className={`msg sys ${m.fail ? 'fail' : ''}`}>
                <i className={`dot ${m.fail ? 'fail' : 'pass'}`} />
                {m.stage && <code>{m.stage}</code>}
                <span>{m.text}</span>
              </div>
            );
          }
          return (
            <div key={i} className="msg agent">
              <span className="av"><Sparkles size={14} /></span>
              <div className="bubble">
                {m.text}
                {m.bullets && m.bullets.length > 0 && <ul>{m.bullets.map((b) => <li key={b}>{b}</li>)}</ul>}
                {m.plan && (
                  <div className="plan-card">
                    <dl className="kv">{planBullets(m.plan).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
                    {awaiting === 'confirm' && i === msgs.length - 1 && (
                      <div className="row">
                        <button className="btn primary sm" onClick={confirm}>Xác nhận kế hoạch</button>
                        <span style={{ fontSize: 12, color: 'var(--muted)', alignSelf: 'center' }}>Chưa đúng? Tạo lại với mô tả rõ hơn.</span>
                      </div>
                    )}
                  </div>
                )}
                {m.open && m.runId && (
                  <div style={{ marginTop: 8 }}><button className="link" onClick={() => onRunCreated(m.runId!)}>Mở trong workspace →</button></div>
                )}
              </div>
            </div>
          );
        })}
        {fn === 'create' && !runId && msgs.length <= 1 && (
          <div className="chips">
            {EXAMPLES.map((ex) => <button key={ex} className="chip" onClick={() => setText(ex)}>{ex}</button>)}
          </div>
        )}
      </div>

      {fn === 'explain' ? (
        <div className="chat-input">
          <div className="row">
            <select value={file} onChange={(e) => setFile(e.target.value)} style={{ flex: 1, height: 32 }}>
              {files.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <input type="number" min={1} value={range.start} onChange={(e) => setRange({ ...range, start: Number(e.target.value) })} style={{ width: 64 }} aria-label="Từ dòng" />
            <input type="number" min={1} value={range.end} onChange={(e) => setRange({ ...range, end: Number(e.target.value) })} style={{ width: 64 }} aria-label="Đến dòng" />
            <button className="btn agent" onClick={explainBlock}><Send size={14} /></button>
          </div>
        </div>
      ) : (
        <div className="chat-input">
          <div className="row">
            <textarea
              rows={2}
              value={text}
              disabled={!inputEnabled || busy}
              placeholder={inputEnabled ? (awaiting === 'answer' ? 'Trả lời câu hỏi làm rõ…' : fn === 'changes' ? 'Góp ý cụ thể cho agent…' : 'Ví dụ: tính doanh thu theo ngày từ bảng đơn hàng…') : 'Agent đang làm việc — theo dõi tiến trình ở trên.'}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }}
            />
            <button className="btn agent" onClick={() => void send()} disabled={!inputEnabled || busy || !text.trim()} aria-label="Gửi"><Send size={14} /></button>
          </div>
          <div className="hint">
            {fn === 'create' && !runId && (
              <>Domain <select value={domain} onChange={(e) => setDomain(e.target.value)}>{domains.map((d) => <option key={d}>{d}</option>)}</select></>
            )}
            <BookOpenCheck size={12} /> Agent chỉ đề xuất; máy tất định quyết định đi tiếp hay dừng.
          </div>
        </div>
      )}
    </>
  );
}
