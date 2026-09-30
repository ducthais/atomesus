'use client';

import { Check, Clock3 } from 'lucide-react';
import type { Run } from '@/lib/demo';

export function EvidenceStrip({run,onSelect}:{run:Run;onSelect:()=>void}) {
 const e=run.evidence;
 const cells = [
  ['Đối soát dòng',e?`${e.rowSource.toLocaleString('vi-VN')} = ${e.rowBronze.toLocaleString('vi-VN')}`:'—',e?'Nguồn = bronze':'Chưa đo'],
  ['Idempotent',e?'Không đổi':'—',e?`${e.secondRunRows.toLocaleString('vi-VN')} dòng sau 2 lần`:'Chưa đo'],
  ['dbt test',e?`${e.testsPassed} / ${e.testsTotal} đạt`:'—',e?'Đúng nghiệp vụ':'Chưa đo'],
  ['Byte quét',e?`${(e.bytes/1e9).toLocaleString('vi-VN',{maximumFractionDigits:2})} GB`:'—',e?`${e.usd.toLocaleString('vi-VN',{minimumFractionDigits:4,maximumFractionDigits:4})} USD${e.estimated?' · ước tính':''}`:'Chưa đo'],
  ['Thời gian',e?`${e.baselineSec.toLocaleString('vi-VN')} → ${e.optimizedSec.toLocaleString('vi-VN')}s`:'—',e?`Cải thiện ${(e.baselineSec/e.optimizedSec).toLocaleString('vi-VN',{maximumFractionDigits:1})}×`:'Chưa đo'],
 ];
 return <div className="evidence-strip" aria-label="Bằng chứng mô phỏng">{cells.map(([name,value,detail])=><button type="button" key={name} onClick={onSelect}><span>{name}</span><strong>{value}</strong><small className={e?'green':''}>{e?<Check size={12}/>:<Clock3 size={12}/>} {detail}</small></button>)}</div>;
}
