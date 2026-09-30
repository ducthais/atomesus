'use client';

import { Check, Clock3 } from 'lucide-react';
import type { Run } from '@/lib/demo';

export function ValidationDetails({run}:{run:Run}) {
 const e=run.evidence;
 const gates = [
  ['spec_schema','Spec khớp schema'],
  ['schema_real','Cột, PK và cursor tồn tại'],
  ['semantic','Không xung đột định nghĩa'],
  ['ast_guard','SQL không có lệnh cấm'],
  ['budget','Trong giới hạn byte và USD'],
  ['post_run','Bốn phép đối soát đạt'],
 ];
 const checks = [
  ['row_reconcile',e?`${e.rowSource.toLocaleString('vi-VN')} = ${e.rowBronze.toLocaleString('vi-VN')}`:'—'],
  ['hash_reconcile',e?(e.hashMatch?'Hash khóa chính khớp':'Không khớp'):'—'],
  ['idempotent',e?`${e.secondRunRows.toLocaleString('vi-VN')} dòng sau lần chạy thứ hai`:'—'],
  ['metric_reconcile',e?(e.metricMatch?'Gold khớp bronze':'Không khớp'):'—'],
 ];
 return <div className="validation-details"><div><h3>Sáu cổng kiểm soát</h3><p>Trạng thái dưới đây là dữ liệu mẫu, chưa có kết quả từ backend.</p>{gates.map(([id,label])=><div className="validation-row" key={id}><code>{id}</code><span>{label}</span><strong className={e?'green':''}>{e?<><Check size={12}/>Đạt</>:<><Clock3 size={12}/>Chưa đo</>}</strong></div>)}</div><div><h3>Bốn phép đối soát</h3><p>Chạy ở P10 và P12; UI vẫn ghi đủ dù dải bằng chứng chỉ có năm ô.</p>{checks.map(([id,value])=><div className="validation-row" key={id}><code>{id}</code><span>{value}</span></div>)}<div className="validation-cost"><span>Chi phí quy đổi</span><strong>{e?`${e.usd.toLocaleString('vi-VN',{minimumFractionDigits:4,maximumFractionDigits:4})} USD${e.estimated?' · ước tính':''}`:'—'}</strong><small>DuckDB ước tính theo đơn giá BigQuery trong budgets.yml.</small></div></div></div>;
}
