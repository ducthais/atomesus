'use client';

import { useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import { ChevronDown, ChevronUp, FileCode2, Sparkles } from 'lucide-react';
import { api } from '@/lib/api';
import { ago, usd } from '@/lib/format';
import { PHASES, code } from '@/lib/phases';
import type { FilesResponse, JsonValue, RunEvent, RunSummary, RunView, Viewer } from '@/lib/types';
import { ReviewBar } from './ReviewBar';
import { StatusBadge } from './ui';

const DiffEditor = dynamic(() => import('@monaco-editor/react').then((m) => m.DiffEditor), {
  ssr: false,
  loading: () => <div className="empty">Đang tải Monaco…</div>,
});

export type Tab = 'items' | 'files' | 'checks' | 'log' | 'spec' | 'gold';

interface Props {
  runs: RunSummary[];
  runsError: string | null;
  filter: string;
  selected: string | null;
  onSelect: (id: string) => void;
  view: RunView | null;
  events: RunEvent[];
  viewer: Viewer;
  tab: Tab;
  onTab: (t: Tab) => void;
  collapsed: boolean;
  onToggle: () => void;
  onReload: () => void;
  onToast: (msg: string, bad?: boolean) => void;
  onAskChanges: () => void;
  onExplainFile: () => void;
}

export function BottomPanel(p: Props) {
  const tabs: [Tab, string, number | null][] = [
    ['items', 'Mục', p.runs.length],
    ['files', 'File', p.view ? Object.keys(p.view.files).length : null],
    ['checks', 'Kiểm tra', p.view ? p.view.gates.length + p.view.checks.length : null],
    ['log', 'Log', p.view ? p.events.length : null],
    ['spec', 'Spec', p.view?.spec_history.length ?? null],
    ['gold', 'Kết quả gold', p.view?.gold_preview.length ?? null],
  ];
  return (
    <section className="bottom">
      {p.view && <ReviewBar view={p.view} viewer={p.viewer} onDone={p.onReload} onToast={p.onToast} onAskChanges={p.onAskChanges} />}
      <div className="tabs" role="tablist">
        {tabs.map(([id, label, n]) => (
          <button key={id} role="tab" aria-selected={p.tab === id} className={`tab ${p.tab === id ? 'active' : ''}`}
            disabled={id !== 'items' && !p.view} onClick={() => { p.onTab(id); if (p.collapsed) p.onToggle(); }}>
            {label}{n != null && <span className="n">{n}</span>}
          </button>
        ))}
        <div className="tabs-right">
          <button className="icon-btn" aria-label={p.collapsed ? 'Mở rộng' : 'Thu gọn'} onClick={p.onToggle}>
            {p.collapsed ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
        </div>
      </div>
      {!p.collapsed && (
        <div className="tab-body">
          {p.tab === 'items' && <Items {...p} />}
          {p.tab === 'files' && p.view && <Files view={p.view} onExplain={p.onExplainFile} />}
          {p.tab === 'checks' && p.view && <Checks view={p.view} />}
          {p.tab === 'log' && <Log events={p.events} />}
          {p.tab === 'spec' && p.view && <pre className="json">{JSON.stringify(p.view.spec, null, 2)}</pre>}
          {p.tab === 'gold' && p.view && <Gold rows={p.view.gold_preview} />}
        </div>
      )}
    </section>
  );
}

function Items({ runs, runsError, filter, selected, onSelect }: Props) {
  if (runsError) {
    return (
      <div className="offline">
        Không kết nối được control plane API ({runsError}). Chạy <code>make seed</code> rồi <code>make api</code> ở thư mục gốc,
        sau đó tải lại trang.
      </div>
    );
  }
  const q = filter.toLowerCase();
  const shown = runs.filter((r) => `${r.title} ${r.request} ${r.run_id} ${r.status} ${r.created_by}`.toLowerCase().includes(q));
  if (!shown.length) return <div className="empty">{runs.length ? 'Không có run khớp bộ lọc.' : 'Chưa có run nào. Mở menu Agent → Tạo pipeline.'}</div>;
  return (
    <table className="grid">
      <thead><tr><th>Tên</th><th>Trạng thái</th><th>Phase</th><th>Domain</th><th>Người tạo</th><th>Người duyệt</th><th>Chi phí</th><th>Cập nhật</th></tr></thead>
      <tbody>
        {shown.map((r) => (
          <tr key={r.run_id} className={`clickable ${selected === r.run_id ? 'sel' : ''}`} onClick={() => onSelect(r.run_id)}>
            <td><div className="item-name"><FileCode2 size={16} color="var(--brand)" /><div>{r.title}<small>{r.request}</small></div></div></td>
            <td><StatusBadge status={r.status} /></td>
            <td className="mono">{code(r.stage_reached)} {PHASES[r.stage_reached].label}</td>
            <td>{r.domain}</td>
            <td>{r.created_by}</td>
            <td>{r.reviewer ?? '—'}</td>
            <td>{usd(r.usd)}</td>
            <td>{ago(r.updated_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function language(path: string) {
  if (path.endsWith('.py')) return 'python';
  if (path.endsWith('.sql')) return 'sql';
  if (path.endsWith('.yml')) return 'yaml';
  return 'plaintext';
}

function Files({ view, onExplain }: { view: RunView; onExplain: () => void }) {
  const [data, setData] = useState<FilesResponse | null>(null);
  const [path, setPath] = useState<string | null>(null);
  useEffect(() => {
    api.files(view.run_id).then((d) => { setData(d); setPath((p) => p && d.files[p] != null ? p : Object.keys(d.files)[0] ?? null); }).catch(() => setData(null));
  }, [view.run_id, view.version, view.files_hash]);
  if (!data || !path) return <div className="empty">Chưa có file nào được render.</div>;
  return (
    <div className="files">
      <div className="file-list">
        {Object.keys(data.files).map((f) => (
          <button key={f} className={f === path ? 'active' : ''} onClick={() => setPath(f)}>
            <span className="fname">{f.split('/').pop()}</span>
            <small>{f}</small>
            <small>{(view.files[f] ?? '').replace('sha256:', '').slice(0, 12)}</small>
          </button>
        ))}
      </div>
      <div className="editor-wrap">
        <div className="editor-bar">
          <span>{data.previous_version ? `So với v${data.previous_version}` : 'v1 · chưa có bản trước'} → v{data.version}</span>
          <button className="cmd" onClick={onExplain}><Sparkles size={14} />Giải thích khối này</button>
        </div>
        <div style={{ flex: 1, minHeight: 200 }}>
          <DiffEditor
            height="100%"
            loading={<PlainCode text={data.files[path] ?? ''} />}
            language={language(path)}
            original={data.previous[path] ?? ''}
            modified={data.files[path] ?? ''}
            options={{ readOnly: true, renderSideBySide: !!data.previous_version, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false }}
          />
        </div>
      </div>
    </div>
  );
}

/** Hiện ngay khi Monaco chưa tải xong (hoặc CDN bị chặn): code thuần, có số dòng. */
function PlainCode({ text }: { text: string }) {
  return (
    <div className="policy" style={{ height: '100%' }}>
      <pre>{text.split('\n').map((l, i) => <span key={i}>{l || ' '}</span>)}</pre>
    </div>
  );
}

function Checks({ view }: { view: RunView }) {
  const rows = [
    ...view.gates.map((g) => ({ ...g, group: 'Cổng' })),
    ...view.checks.map((g) => ({ ...g, group: 'Kiểm tra sau chạy' })),
  ];
  return (
    <table className="grid">
      <thead><tr><th>Loại</th><th>Tên</th><th>Kết quả</th><th>Chi tiết</th><th>Số đo</th></tr></thead>
      <tbody>
        {rows.map((g) => (
          <tr key={`${g.group}-${g.name}`}>
            <td>{g.group}</td>
            <td className="mono">{g.name}</td>
            <td><span className={`badge ${g.result === 'pass' ? 'ok' : 'bad'}`}>{g.result === 'pass' ? 'Đạt' : g.result === 'fail' ? 'Không đạt' : 'Chuyển người'}</span></td>
            <td>{g.detail}</td>
            <td className="mono" style={{ fontSize: 11, maxWidth: 420, wordBreak: 'break-all' }}>{compact(g.observed)}</td>
          </tr>
        ))}
        {view.optimizer.map((o) => (
          <tr key={o.rule}>
            <td>Tối ưu (P13)</td><td className="mono">{o.rule}</td>
            <td><span className={`badge ${o.applied ? 'ok' : 'muted'}`}>{o.applied ? 'Áp dụng' : 'Giữ bản gốc'}</span></td>
            <td>{o.reason}</td><td className="mono">{o.before_sec ?? '—'}s → {o.after_sec ?? '—'}s</td>
          </tr>
        ))}
        {view.error_trace.map((e) => (
          <tr key={e.at + e.message}>
            <td>Vết lỗi</td><td className="mono">{code(e.stage)} {e.kind}</td>
            <td><span className="badge bad">Lỗi</span></td><td>{e.message}</td><td>{e.route}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function compact(o: { [k: string]: JsonValue }): string {
  const s = JSON.stringify(o);
  return s.length > 220 ? `${s.slice(0, 220)}…` : s;
}

function Log({ events }: { events: RunEvent[] }) {
  if (!events.length) return <div className="empty">Chưa có sự kiện.</div>;
  return (
    <div className="log">
      {events.map((e) => (
        <div key={e.seq} className={`log-row ${e.status === 'fail' || e.status === 'blocked' ? 'fail' : ''}`}>
          <span style={{ color: 'var(--faint)' }}>#{e.seq}</span>
          <span>{code(e.stage)}</span>
          <span>{e.stage}</span>
          <span className={`state ${e.status === 'escalate' ? 'blocked' : e.status}`}>{e.status}</span>
          <span className="msg">{e.message}</span>
        </div>
      ))}
    </div>
  );
}

function Gold({ rows }: { rows: { [k: string]: JsonValue }[] }) {
  if (!rows.length) return <div className="empty">Chưa có dữ liệu gold.</div>;
  const cols = Object.keys(rows[0]);
  return (
    <table className="grid">
      <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className="mono">{String(r[c] ?? '')}</td>)}</tr>)}</tbody>
    </table>
  );
}
