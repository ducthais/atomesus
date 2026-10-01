'use client';

import { useState } from 'react';
import { ArrowRight, Database, Sparkles, WandSparkles } from 'lucide-react';

export function CreatePrompt({ onStart, enabled }: { onStart: (request: string) => void; enabled: boolean }) {
 const [value,setValue]=useState('');
 const examples = ['Đưa bảng đơn hàng từ Postgres về warehouse, cập nhật mỗi giờ, rồi tính doanh thu theo ngày.', 'Đồng bộ các bảng của hệ thống nguồn mới'];
 return <section className="create-prompt"><div className="create-prompt-body"><span className="create-prompt-eyebrow"><WandSparkles size={15}/>AI pipeline workspace</span><h2>Biến yêu cầu dữ liệu thành<br/><em>pipeline có thể kiểm chứng.</em></h2><p>Mô tả nhu cầu, xem kế hoạch và bằng chứng sandbox trước khi Reviewer quyết định.</p><div className="create-prompt-form"><Sparkles size={19}/><input aria-label="Mô tả pipeline muốn tạo" placeholder="Ví dụ: Tính doanh thu đơn hàng theo ngày…" value={value} onChange={e=>setValue(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&enabled)onStart(value);}} disabled={!enabled}/><button type="button" onClick={()=>onStart(value)} disabled={!enabled}>{value.trim()?'Lập kế hoạch':'Bắt đầu'}<ArrowRight size={16}/></button></div><div className="create-prompt-examples"><span>Thử nhanh</span>{examples.map(example=><button type="button" key={example} onClick={()=>{setValue(example);if(enabled)onStart(example);}} disabled={!enabled}>{example}<ArrowRight size={12}/></button>)}</div></div><div className="create-prompt-art" aria-hidden="true"><div className="art-grid"/><div className="art-card art-source"><Database size={18}/><span>Postgres</span><b>orders</b></div><div className="art-link one"/><div className="art-card art-transform"><WandSparkles size={18}/><span>dbt model</span><b>daily_revenue</b></div><div className="art-link two"/><div className="art-card art-dagster"><Sparkles size={18}/><span>Review</span><b>Dagster</b></div></div></section>;
}
