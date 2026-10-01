'use client';

import { useState } from 'react';
import { ArrowLeft, ArrowRight, Check, CircleAlert, Clock3, Database, LockKeyhole, RotateCcw, ShieldCheck, Sparkles } from 'lucide-react';
import { STAGES, STATUS_LABEL, canReview, type Run } from '@/lib/studio';

/** Yêu cầu chạy được với nguồn hiện có (Postgres sales_raw: orders, customers). */
const EXAMPLES = [
  { label: 'Doanh thu ngày', request: 'Tính doanh thu và số đơn hàng theo từng ngày đặt hàng, bỏ các đơn bị hủy' },
  { label: 'Doanh thu khách hàng', request: 'Tổng doanh thu và số đơn hàng của từng khách hàng, kèm tên khách hàng' },
  { label: 'Nguồn chưa hỗ trợ', request: 'Ghép đơn hàng Postgres với thanh toán từ REST API, tính doanh thu theo ngày' },
];

export function RequestWizard({ onCancel, onSubmit, enabled, initial = '' }: { onCancel: () => void; onSubmit: (request: string) => void; enabled: boolean; initial?: string }) {
  const [request, setRequest] = useState(initial);
  const valid = request.trim().length > 0 && request.length <= 2000;
  return <><button className="studio-back" onClick={onCancel}><ArrowLeft size={15} /> Tất cả run</button><div className="studio-page-head"><div><div className="studio-kicker">WORKSPACE / NEW REQUEST</div><h1>Tạo pipeline mới</h1><p>Mô tả nhu cầu bằng lời. Agent lập kế hoạch từ schema thật, viết Spec, sinh code và chạy thử trong sandbox.</p></div><span className="studio-pill"><Database size={13} /> Sandbox DuckDB</span></div>
    <div className="studio-wizard-grid"><section className="studio-surface studio-wizard-main"><h2>Yêu cầu nghiệp vụ</h2><p className="studio-muted">Agent chỉ dùng cột có thật ở nguồn. Một run mất khoảng 30 giây và gọi LLM tối đa theo trần trong budgets.yml.</p>
      <label className="studio-field">Mô tả bằng tiếng Việt<textarea rows={7} value={request} maxLength={2000} onChange={e => setRequest(e.target.value)} placeholder="Ví dụ: Tính doanh thu theo ngày từ bảng đơn hàng…" /></label>
      <div className="studio-example-list"><span>THỬ NHANH</span>{EXAMPLES.map(e => <button key={e.label} onClick={() => setRequest(e.request)}>{e.label}</button>)}</div>
      <div className="studio-info"><ShieldCheck size={17} /> Sprint 1 chưa có bước Engineer xác nhận kế hoạch (P2); agent đi thẳng tới chạy thử. Reviewer duyệt sau khi các check đạt.</div>
      <div className="studio-wizard-actions"><button className="studio-button" onClick={onCancel}>Hủy</button><button className="studio-button primary" disabled={!enabled || !valid} title={!enabled ? 'Chỉ Engineer tạo yêu cầu' : !valid ? 'Nhập yêu cầu' : ''} onClick={() => onSubmit(request.trim())}>Gửi cho agent<ArrowRight size={15} /></button></div></section>
    <aside className="studio-surface studio-schema"><div className="studio-section-title"><strong>Agent sẽ làm gì</strong><Sparkles size={17} /></div><ol className="studio-muted">{['Lập kế hoạch từ danh mục nguồn thật', 'Đọc schema + 20 dòng mẫu, viết Spec', 'Kiểm tra Spec; sai thì tự sửa (tối đa theo budgets.yml)', 'Sinh dlt, dbt, Dagster từ template', 'Chạy dlt → dbt trong sandbox, đối soát'].map(step => <li key={step}>{step}</li>)}</ol><small><LockKeyhole size={12} /> Chỉ đọc nguồn; sandbox xóa sau mỗi run</small></aside></div></>;
}

const RULES: Partial<Record<string, string>> = {
  BLOCKED_AST: 'Cổng ast_guard (src/codegen/guard.py): biểu thức chỉ được dùng cột và hàm cho phép.',
  BLOCKED_SEMANTIC_CONFLICT: 'policy/domains.yml: thực thể trùng tên ở hai domain, conformed_dimensions chưa công nhận.',
  BLOCKED_BUDGET: 'policy/budgets.yml: per_run.max_bytes_scanned, max_usd.',
  NEEDS_HUMAN: 'policy/budgets.yml: max_spec_attempts, max_llm_calls; hoặc kế hoạch cần làm rõ / nguồn chưa hỗ trợ.',
  INFRA_ERROR: 'Kết nối nguồn hoặc nhà cung cấp LLM. Không phải lỗi của Spec.',
  INTERNAL_ERROR: 'Lỗi của hệ thống (template/sandbox), không phải lỗi agent.',
};

/** Run dừng trước khi có code: lý do, bằng chứng và luật áp dụng đều đọc từ RunReport. */
export function BlockedRunView({ run, onRetry, onEdit, canRun }: { run: Run; onRetry: () => void; onEdit: () => void; canRun: boolean }) {
  const failed = run.checks.filter(check => check.state === 'fail');
  const lastSpecErrors = run.logs.filter(log => log.level === 'warning').at(-1)?.detail;
  return <section className="studio-blocked"><div className="studio-blocked-top"><CircleAlert size={21} /><div><span>DỪNG Ở PHASE · {STAGES[run.stage].toUpperCase()}</span><h2>{STATUS_LABEL[run.status]}</h2><p>Agent dừng trước khi sinh code. Cần quyết định của con người.</p></div></div><div className="studio-blocked-grid"><div><h3>01 · Chuyện gì xảy ra</h3><p>{run.error ?? 'RunReport không ghi lý do.'}</p></div><div><h3>02 · Bằng chứng</h3>{failed.length ? <dl className="studio-definition">{failed.map(check => <div key={check.id}><dt>{check.name}</dt><dd>{check.detail}</dd></div>)}</dl> : lastSpecErrors ? <pre>{lastSpecErrors}</pre> : <p>Không có cổng nào ghi kết quả trước khi dừng.</p>}</div><div><h3>03 · Luật áp dụng</h3><pre>{RULES[run.status] ?? '—'}</pre></div><div><h3>04 · Bước tiếp theo</h3><p>{run.question ? 'Trả lời câu hỏi bằng cách viết lại yêu cầu rõ hơn.' : 'Viết lại yêu cầu hoặc chạy lại; mỗi lần chạy tạo một run mới.'}</p><div className="studio-action-row"><button className="studio-button primary" disabled={!canRun} onClick={onEdit}>Sửa yêu cầu</button><button className="studio-button" disabled={!canRun} onClick={onRetry}><RotateCcw size={15} /> Chạy lại</button></div></div></div></section>;
}

export function RunLogs({ run, selectedStage, selectedCheck }: { run: Run; selectedStage: number | null; selectedCheck: string | null }) {
  const [level, setLevel] = useState('all');
  const logs = run.logs.filter(log => (level === 'all' || log.level === level) && (selectedStage === null || log.stage === selectedStage));
  return <div className="studio-logs"><div className="studio-log-toolbar"><div><strong>Nhật ký & RunReport</strong><small>{selectedStage !== null ? `Phase ${selectedStage + 1}: ${STAGES[selectedStage]}` : 'Tất cả phase'}</small></div><label>Mức độ<select value={level} onChange={e => setLevel(e.target.value)}><option value="all">Tất cả</option><option value="info">Thông tin</option><option value="success">Đạt</option><option value="warning">Cảnh báo</option><option value="error">Lỗi</option></select></label></div><div className="studio-check-list"><h3>Cổng kiểm tra và phép đối soát</h3>{run.checks.length ? run.checks.map(check => <div key={check.id} className={selectedCheck === check.id ? 'highlight' : ''}><span className={`studio-check-${check.state}`}>{check.state === 'pass' ? <Check size={14} /> : check.state === 'fail' ? <CircleAlert size={14} /> : <Clock3 size={14} />}</span><strong>{check.name}</strong><code>{check.id}</code><span>{check.value}</span><small>{check.detail}</small></div>) : <div className="studio-empty">Chưa có kết quả kiểm tra.</div>}</div><div className="studio-log-list">{logs.length ? logs.map(log => <details key={log.id}><summary><time>{new Date(log.at).toLocaleTimeString('vi-VN')}</time><span className={`studio-log-level ${log.level}`}>{log.level}</span><span className="studio-mono">{String(log.stage + 1).padStart(2, '0')}</span><strong>{log.message}</strong></summary>{log.detail && <pre>{log.detail}</pre>}</details>) : <div className="studio-empty">Chưa có log ở bộ lọc này.</div>}</div></div>;
}

export function ReviewBar({ run, role, user }: { run: Run; role: string; user: string }) {
  if (run.approval) return <div className="studio-review-bar"><div><strong>{run.approval.decision === 'approved' ? 'Đã duyệt' : run.approval.decision} v{run.approval.version} bởi {run.approval.reviewer}</strong><p>files_hash <code>{run.approval.files_hash.replace('sha256:', '').slice(0, 12)}</code> · {new Date(run.approval.at).toLocaleString('vi-VN')}{run.approval.comment && ` · “${run.approval.comment}”`}</p></div></div>;
  if (run.status !== 'READY_FOR_REVIEW') return null;
  const eligible = canReview(run, role, user);
  return <div className="studio-review-bar"><div><strong>Review v{run.version}</strong><p>{eligible ? 'Bạn đủ điều kiện duyệt run này. ' : 'Chỉ Reviewer khác người tạo mới được duyệt; mọi check phải đạt. '}API duyệt / từ chối / yêu cầu sửa chưa có ở backend (Sprint 1 · A), nên các nút đang khóa.</p></div><div className="studio-review-actions"><button className="studio-button" disabled>Yêu cầu sửa</button><button className="studio-button danger" disabled>Từ chối</button><button className="studio-button primary" disabled><Check size={15} /> Phê duyệt</button></div></div>;
}
