'use client';

import { Fragment, useEffect, useRef, useState, useTransition } from 'react';
import { CircleAlert, Clock3, Send, Sparkles } from 'lucide-react';
import type { ChatMessage } from '@/lib/studio';
import { askRun } from '../actions';
import { fmtDate } from './ui';

/** Câu hỏi mẫu khớp các nhóm câu trả lời của backend (src/chat/answers.py). */
const SUGGESTIONS = ['Vì sao run này dừng?', 'Nguồn nào đang bẩn, vì sao?', 'Mỗi nguồn có bao nhiêu dòng?', 'Run đã được duyệt chưa?'];

/** `tên` trong câu trả lời → <code>, phần còn lại giữ nguyên xuống dòng. */
const Rich = ({ text }: { text: string }) => <>{text.split('`').map((part, i) => i % 2 ? <code key={i}>{part}</code> : <Fragment key={i}>{part}</Fragment>)}</>;

type Props = { runId: string; messages: ChatMessage[]; people: Record<string, string>; limit: number; disabledReason: string | null };

/** Hỏi đáp về run đang mở (trả lời từ RunReport, không đổi run). Câu mệnh lệnh của Engineer ("Thêm…", "Bỏ…") tạo
 * version mới để agent sửa pipeline; duyệt và deploy vẫn chỉ qua nút bấm. */
export function RunChat({ runId, messages, people, limit, disabledReason }: Props) {
  const [draft, setDraft] = useState('');
  const [asking, setAsking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const end = useRef<HTMLDivElement>(null);
  const used = messages.filter(m => m.role === 'user' && m.intent === 'ask').length;
  const exhausted = used >= limit;
  const blocked = disabledReason ?? (exhausted ? `Run đã dùng hết ${limit} câu hỏi (budgets.yml: per_run.max_chat_calls).` : null);

  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [messages.length, asking]);

  const send = (text: string) => {
    const message = text.trim();
    if (!message || pending || blocked) return;
    setError(null);
    setAsking(message);
    setDraft('');
    start(async () => {
      const state = await askRun(runId, message);
      if (state?.error) { setError(state.error); setDraft(message); }
      setAsking(null);
    });
  };

  return <section className="panel chat">
    <div className="panel-head"><h2>Hỏi về run này</h2><span className="muted small mono" title="budgets.yml: per_run.max_chat_calls">{used}/{limit} câu hỏi</span></div>
    <p className="muted small">Câu hỏi được trả lời từ RunReport, mỗi con số kèm tên trường nguồn, không đổi run. Engineer tạo run có thể ra lệnh sửa (ví dụ <em>Thêm số đơn hàng của mỗi khách hàng</em>): agent tạo version mới và bản duyệt cũ mất hiệu lực. Duyệt và deploy chỉ qua nút bấm.</p>

    <div className="chat-log" aria-live="polite">
      {messages.length === 0 && !asking && <p className="muted chat-empty">Chưa có câu hỏi nào. Thử một câu gợi ý bên dưới.</p>}
      {messages.map(m => <div key={m.id} className={`chat-msg ${m.role}`}>
        <span className="chat-who">{m.role === 'user' ? people[m.author] ?? m.author : <><Sparkles size={13} aria-hidden /> Agent</>}<time dateTime={m.at}>{fmtDate(m.at)}</time></span>
        <p><Rich text={m.content} /></p>
      </div>)}
      {asking && <>
        <div className="chat-msg user"><span className="chat-who">Bạn</span><p>{asking}</p></div>
        <div className="chat-msg assistant" role="status"><span className="chat-who"><Clock3 size={13} aria-hidden /> Agent</span><p className="muted">Đang đọc RunReport…</p></div>
      </>}
      <div ref={end} />
    </div>

    {error && <div className="callout bad small" role="alert"><CircleAlert size={15} aria-hidden /><span>{error}</span></div>}
    {blocked ? <p className="callout warn small"><CircleAlert size={15} aria-hidden /><span>{blocked}</span></p> : <>
      <div className="chat-suggestions">{SUGGESTIONS.map(s => <button key={s} type="button" className="chip" disabled={pending} onClick={() => send(s)}>{s}</button>)}</div>
      <form className="chat-compose" onSubmit={e => { e.preventDefault(); send(draft); }}>
        <label htmlFor="chat-input" className="sr-only">Câu hỏi hoặc yêu cầu sửa run</label>
        <textarea id="chat-input" rows={2} maxLength={2000} value={draft} disabled={pending} placeholder="Ví dụ: Vì sao run này dừng? hoặc: Thêm số đơn hàng của mỗi khách hàng"
          onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(draft); } }} />
        <button type="submit" className="button primary" disabled={pending || !draft.trim()}><Send size={15} aria-hidden /> Gửi</button>
      </form>
    </>}
  </section>;
}
