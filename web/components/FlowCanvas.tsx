'use client';

import { useEffect, useRef, useState } from 'react';
import {
  CheckCircle2, ClipboardCheck, Cpu, Download, Inbox, Maximize, Play, RotateCcw, SearchCode, Sparkles, Square, ZoomIn, ZoomOut,
} from 'lucide-react';
import { GROUPS, PHASES, code, groupState, stageStates, type Group } from '@/lib/phases';
import type { RunEvent, RunView } from '@/lib/types';
import { StateText } from './ui';

const ICONS = { G1: Inbox, G2: SearchCode, G3: Sparkles, G4: ClipboardCheck, G5: Cpu, G6: CheckCircle2 } as const;

const NODE_W = 184;
const GAP = 44;
const TOP = 196;
const nodeX = (i: number) => 24 + i * (NODE_W + GAP);
const nodeH = (g: Group) => 84 + g.stages.length * 18;
const STAGE_W = nodeX(GROUPS.length - 1) + NODE_W + 40;
const STAGE_H = TOP + Math.max(...GROUPS.map(nodeH)) + 110;

interface Props {
  view: RunView | null;
  events: RunEvent[];
  selectedGroup: string | null;
  onSelectGroup: (id: string | null) => void;
  onNew: () => void;
  onRerun: () => void;
  onCancel: () => void;
  canCreate: boolean;
}

export function FlowCanvas({ view, events, selectedGroup, onSelectGroup, onNew, onRerun, onCancel, canCreate }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [fit, setFit] = useState(true);

  useEffect(() => {
    if (!fit || !wrap.current) return undefined;
    const el = wrap.current;
    const ro = new ResizeObserver(() => setScale(Math.min(1, Math.max(0.45, (el.clientWidth - 72) / STAGE_W))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [fit]);

  const states = stageStates(events, view?.status);
  const gStates = GROUPS.map((g) => groupState(states, g));
  const attempts = view?.attempts ?? { spec: 0, sql: 0, el: 0 };
  const caps = view?.caps;
  const humanLoops = view?.decisions.filter((d) => d.action === 'request_changes').length ?? 0;

  const zoom = (d: number) => { setFit(false); setScale((s) => Math.min(1.6, Math.max(0.4, s + d))); };

  function download() {
    if (!view) return;
    const blob = new Blob([JSON.stringify(view, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `run-${view.run_id.slice(0, 8)}-v${view.version}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Cạnh chính: nối mép phải nhóm i với mép trái nhóm i+1, ngang tầm đầu thẻ.
  const mainEdges = GROUPS.slice(0, -1).map((_, i) => {
    const y = TOP + 34;
    const done = gStates[i] === 'pass';
    return <path key={`e${i}`} className={`edge ${done ? 'done' : ''}`} d={`M${nodeX(i) + NODE_W} ${y} L${nodeX(i + 1)} ${y}`} markerEnd="url(#arrow)" />;
  });

  // Ba vòng lặp (SPEC_1.md mục 11.3) vẽ bằng nét đứt.
  const cx = (i: number) => nodeX(i) + NODE_W / 2;
  const above = (from: number, to: number, lift: number) =>
    `M${cx(from)} ${TOP} C${cx(from)} ${TOP - lift}, ${cx(to)} ${TOP - lift}, ${cx(to) + 8} ${TOP}`;
  const bottomY = (i: number) => TOP + nodeH(GROUPS[i]);
  const below = (from: number, to: number, drop: number) => {
    const y = Math.max(bottomY(from), bottomY(to));
    return `M${cx(from)} ${bottomY(from)} C${cx(from)} ${y + drop}, ${cx(to)} ${y + drop}, ${cx(to) - 8} ${bottomY(to)}`;
  };
  const loops = [
    {
      d: above(4, 2, 70), hot: attempts.spec > 0, labelX: (cx(2) + cx(4)) / 2, labelY: TOP - 58,
      label: `Vòng spec · P7–P10 → P4 · ${attempts.spec}/${caps?.spec ?? '—'}`,
    },
    {
      d: above(5, 2, 118), hot: humanLoops > 0, labelX: (cx(2) + cx(5)) / 2, labelY: TOP - 94,
      label: `Vòng người · P15 → P4 · ${humanLoops} lần`,
    },
    {
      d: below(4, 2, 70), hot: attempts.sql > 0, labelX: (cx(2) + cx(4)) / 2, labelY: Math.max(bottomY(4), bottomY(2)) + 64,
      label: `Vòng SQL · P7/P11/P12 → sql_fix → P6 · ${attempts.sql}/${caps?.sql ?? '—'}`,
    },
  ];

  const running = !!view && !view.terminal && !['READY_FOR_REVIEW', 'APPROVED'].includes(view.status);

  return (
    <div className="canvas-wrap" ref={wrap}>
      <div className="canvas-toolbar">
        <span className="title" title={view?.user_request}>
          <Play size={14} />
          {view ? ((view.spec?.transform as { model_name?: string } | undefined)?.model_name ?? view.user_request) : 'Luồng P0–P17'}
        </span>
        <span className="cmd-sep" />
        <button className="cmd" onClick={onNew} disabled={!canCreate}><Sparkles size={16} />Yêu cầu mới</button>
        <button className="cmd" onClick={onRerun} disabled={!view || !canCreate}><RotateCcw size={16} />Chạy lại</button>
        <button className="cmd" onClick={onCancel} disabled={!running}><Square size={15} />Huỷ run</button>
        <button className="cmd" onClick={download} disabled={!view}><Download size={16} />Tải RunReport</button>
      </div>
      <div className="zoom">
        <button aria-label="Phóng to" onClick={() => zoom(0.1)}><ZoomIn size={20} /></button>
        <button aria-label="Thu nhỏ" onClick={() => zoom(-0.1)}><ZoomOut size={20} /></button>
        <button aria-label="Vừa khung" onClick={() => setFit(true)}><Maximize size={18} /></button>
      </div>
      <div className="canvas-scroll" onClick={() => onSelectGroup(null)}>
        <div className="canvas-stage" style={{ width: STAGE_W * scale, height: STAGE_H * scale }}>
          <div style={{ width: STAGE_W, height: STAGE_H, transform: `scale(${scale})`, transformOrigin: '0 0', position: 'relative' }}>
            <svg width={STAGE_W} height={STAGE_H}>
              <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0 0 L10 5 L0 10 z" fill="#8a8a8a" />
                </marker>
              </defs>
              {mainEdges}
              {loops.map((l) => (
                <g key={l.label}>
                  <path className={`edge loop ${l.hot ? 'hot' : ''}`} d={l.d} markerEnd="url(#arrow)" />
                  <text className={`edge-label ${l.hot ? 'hot' : ''}`} x={l.labelX} y={l.labelY} textAnchor="middle">{l.label}</text>
                </g>
              ))}
            </svg>
            {GROUPS.map((g, i) => {
              const Icon = ICONS[g.id as keyof typeof ICONS];
              const st = gStates[i];
              return (
                <button
                  key={g.id}
                  type="button"
                  className={`node ${st} ${selectedGroup === g.id ? 'selected' : ''}`}
                  style={{ left: nodeX(i), top: TOP, ['--tone' as string]: `var(--${g.tone})` }}
                  onClick={(e) => { e.stopPropagation(); onSelectGroup(g.id); }}
                  aria-label={`${g.label}: ${st}`}
                >
                  <div className="node-head">
                    <span className="gicon"><Icon size={12} /></span>
                    <b>{g.label}</b>
                    <span className="codes">{code(g.stages[0])}{g.stages.length > 1 ? `–${code(g.stages[g.stages.length - 1])}` : ''}</span>
                  </div>
                  <div className="node-sub">{g.caption}</div>
                  <div className="node-phases">
                    {g.stages.map((s) => (
                      <div className="node-phase" key={s}>
                        <i className={`dot ${states[s]}`} />
                        <span className="p">{code(s)}</span>
                        {PHASES[s].label}
                        {PHASES[s].kind !== 'Tất định' && <span className="k">{PHASES[s].kind}</span>}
                      </div>
                    ))}
                  </div>
                  <div className="node-foot"><StateText state={st} /><span>{g.id}</span></div>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      {!view && (
        <div className="canvas-empty">
          <Sparkles size={16} color="var(--agent)" />
          Chọn một run ở danh sách bên dưới, hoặc mở menu Agent để tạo pipeline mới.
        </div>
      )}
    </div>
  );
}
