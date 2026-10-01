'use client';

import { ArrowRight, Check, Database, Layers3, Play, Sparkles, Workflow } from 'lucide-react';
import type { Run } from '@/lib/demo';

export function PipelineCanvas({ run, onCode, onExplain }: { run: Run; onCode: () => void; onExplain: () => void }) {
 const stopped = run.status.startsWith('BLOCKED') || run.status === 'NEEDS_HUMAN';
 const stages = [
  { label: 'Nguồn', title: `${run.domain.toLowerCase()}_raw.${run.source}`, caption: 'Postgres · chỉ đọc', icon: Database, tone: 'source' },
  { label: 'Ingestion', title: `raw_${run.source}`, caption: 'dlt → DuckDB bronze', icon: ArrowRight, tone: 'ingest' },
  { label: 'Transform', title: run.model || 'fct_daily_revenue', caption: 'dbt · gold', icon: Layers3, tone: 'transform' },
  { label: 'Điều phối', title: 'Dagster asset', caption: 'Code location sandbox', icon: Workflow, tone: 'orchestrate' },
 ];
 return <section className="pipeline-canvas" aria-label="Sơ đồ pipeline minh họa">
  <div className="canvas-toolbar"><div><span className="canvas-kicker"><Sparkles size={14}/>Pipeline designer</span><strong>Luồng dữ liệu</strong><small>Đồ thị minh họa từ đặc tả · {run.id}</small></div><div className="canvas-toolbar-actions"><span className="canvas-environment"><span/>Sandbox</span><button type="button" onClick={onExplain}><Sparkles size={15}/>Giải thích</button><button type="button" onClick={onCode}>Xem mã nguồn<ArrowRight size={14}/></button></div></div>
  <div className="canvas-stage" role="list">{stages.map((stage,index)=>{const Icon=stage.icon;return <div className="canvas-segment" key={stage.label} role="listitem"><div className={`canvas-node ${stage.tone} ${stopped&&index>0?'muted':''}`}><div className="canvas-node-top"><span className="canvas-node-icon"><Icon size={19}/></span><span className="canvas-node-label">{stage.label}</span>{!stopped&&index<3&&<Check size={14} className="canvas-node-check"/>}</div><strong title={stage.title}>{stage.title}</strong><small>{stage.caption}</small></div>{index<stages.length-1&&<div className={`canvas-connector ${stopped?'muted':''}`}><span/><ArrowRight size={16}/></div>}</div>})}</div>
  <div className="canvas-footer"><span><Play size={13}/>{stopped?'Dừng trước khi chạy · kiểm tra lý do bên dưới':'dlt tải dữ liệu → dbt biến đổi → Dagster điều phối'}</span><span>{run.status==='DEPLOYED'?'Đã deploy · mô phỏng':'Bản xem trước · chưa thực thi từ UI'}</span></div>
 </section>;
}
