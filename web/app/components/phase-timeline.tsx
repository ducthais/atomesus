'use client';

import { useState } from 'react';
import { Activity, Check, ChevronDown } from 'lucide-react';
import { phaseGroups, phaseIds, phases, policies, type Run } from '@/lib/demo';

export function PhaseTimeline({ run, onSelect }: { run: Run; onSelect: (phase: string) => void }) {
 const [expanded, setExpanded] = useState<string | null>(null);
 const current = Math.min(run.phase, phases.length - 1);
 const blocked = run.status.startsWith('BLOCKED') || ['NEEDS_HUMAN','INFRA_ERROR','INTERNAL_ERROR','DEPLOY_FAILED'].includes(run.status);
 return <section className="phase-panel">
  <div className="phase-top"><span><Activity size={15}/>Tiến trình pipeline</span><span className="subtle">P{current} · {phases[current]}</span></div>
  <div className="phase-groups">{phaseGroups.map(group => {
   const done = current > group.end;
   const active = current >= group.start && current <= group.end;
   return <button key={group.name} type="button" aria-expanded={expanded === group.name} className={`phase-group ${done?'done':''} ${active?'current':''} ${active&&blocked?'blocked':''}`} onClick={()=>setExpanded(expanded===group.name?null:group.name)}><span className="phase-group-index">{done?<Check size={15}/>:group.start===group.end?`P${group.start}`:`${group.start}–${group.end}`}</span><span className="phase-group-label">{group.name}<small>P{group.start}{group.start===group.end?'':`–P${group.end}`}</small></span><ChevronDown size={14}/></button>;
  })}</div>
  {expanded && <div className="phase-details">{phaseGroups.filter(g=>g.name===expanded).flatMap(g=>phases.slice(g.start,g.end+1).map((label,offset)=>{const index=g.start+offset;return <button type="button" className={`phase-detail ${index===current?'current':''}`} key={phaseIds[index]} onClick={()=>onSelect(label)}><span className="mono">P{index}</span><strong>{label}</strong><code>{phaseIds[index]}</code><span>{index<current?'Đã qua':index===current?(blocked?'Bị chặn':'Hiện tại'):'Chưa chạy'}</span></button>}))}</div>}
  <div className="retry-counts"><span>Sửa spec <code>{run.attempts?.spec ?? '—'}/2</code></span><span>Sửa SQL <code>{run.attempts?.sql ?? '—'}/3</code></span><span>Gọi LLM <code>{run.attempts?.llm ?? '—'}/{policies['budgets.yml'].match(/max_llm_calls:\s*(\d+)/)?.[1] || '—'}</code></span><span className="demo-metric">Số liệu mô phỏng · trần từ budgets.yml</span></div>
 </section>;
}
