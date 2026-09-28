'use client';

import { useEffect, useState } from 'react';
import { AlertOctagon, BadgeCheck, Check, Clock3, MessageSquareText, PanelRightClose, Sparkles, X } from 'lucide-react';
import { api } from '@/lib/api';
import { bytes, num, secs, shortHash, usd } from '@/lib/format';
import { GROUPS, PHASES, code, stageStates } from '@/lib/phases';
import type { Explanation, GateResult, JsonObject, RunEvent, RunView, StageId, Viewer } from '@/lib/types';
import { StateText, StatusBadge } from './ui';

/** Việc từng phase làm — để thấy phần nào là agent (LLM), phần nào là máy tất định, phần nào là người. */
const PHASE_DOC: Record<StageId, string> = {
  intake: 'Sinh run_id, tạo sandbox mới, ghi bản ghi run.',
  plan: 'LLM rẻ đọc yêu cầu + danh sách bảng, trả kế hoạch JSON. Tối đa 1 câu hỏi làm rõ.',
  confirm: 'Cổng HITL 1: người yêu cầu xác nhận kế hoạch trước khi sinh code.',
  resolve: 'Lấy schema thật từ information_schema, dò trùng thực thể theo domains.yml → cổng semantic.',
  propose: 'LLM mạnh sinh 1–2 JSON spec từ kế hoạch + schema thật + few-shot đã duyệt.',
  arbitrate: 'Chọn phương án rẻ nhất thoả mọi ràng buộc hard trong objectives.yml.',
  render: 'Jinja render 4 file từ spec. Lỗi ở đây là bug template → INTERNAL_ERROR.',
  static: 'compile + sqlglot: cổng spec_schema, ast_guard, schema_real.',
  budget: 'dry_run ước tính byte, quy USD, so với budgets.yml.',
  el_run: 'Chạy dlt đổ vào bronze trên sandbox (chạy 2 lần để thử idempotent).',
  el_validate: 'row_reconcile, hash_reconcile, idempotent.',
  t_run: 'dbt run model gold.',
  t_validate: 'dbt test + metric_reconcile (gold so với bronze).',
  optimize: 'Đo EXPLAIN ANALYZE, áp 2 luật; bản tối ưu sai hoặc chậm hơn thì bỏ. Không bao giờ làm fail run.',
  report: 'Gom RunReport, tính files_hash, lấy chi phí.',
  review: 'Cổng HITL 2: duyệt / từ chối / yêu cầu sửa. Không duyệt run của chính mình.',
  deploy: 'D1: ghi 4 file vào code location khi approval.hash == files_hash.',
  persist: 'Lưu (yêu cầu, spec) đã duyệt làm few-shot cho lần sau.',
};

interface Props {
  view: RunView | null;
  events: RunEvent[];
  selectedGroup: string | null;
  viewer: Viewer;
  onClose: () => void;
  onAskAgent: (fn: 'why' | 'changes') => void;
  onToast: (msg: string, bad?: boolean) => void;
  onOpenRun: (id: string) => void;
}

export function DetailsPane({ view, events, selectedGroup, viewer, onClose, onAskAgent, onToast, onOpenRun }: Props) {
  const group = GROUPS.find((g) => g.id === selectedGroup) ?? null;
  return (
    <aside className="pane" aria-label="Chi tiết">
      <div className="pane-head">
        <button className="icon-btn" aria-label="Đóng pane" onClick={onClose}><PanelRightClose size={18} /></button>
      </div>
      {group ? <GroupDetail groupId={group.id} view={view} events={events} />
        : view ? <RunDetail view={view} viewer={viewer} onAskAgent={onAskAgent} onToast={onToast} onOpenRun={onOpenRun} />
          : <Intro />}
    </aside>
  );
}

function Intro() {
  return (
    <div className="pane-body">
      <div>
        <h2>Luồng tạo pipeline</h2>
        <div className="kicker">18 phase P0–P17, gom thành 6 nhóm</div>
      </div>
      <p>
        Agent chỉ được quyền <b>đề xuất</b> (P1, P4, sql_fix). Máy tất định <b>quyết định</b> đi tiếp, quay lui hay dừng.
        Con người sở hữu bộ luật và hai cổng P2, P15.
      </p>
      <p>Bấm một thẻ trên canvas để xem từng phase. Mở <b>Agent</b> ở góc trên phải để bắt đầu.</p>
    </div>
  );
}

function GroupDetail({ groupId, view, events }: { groupId: string; view: RunView | null; events: RunEvent[] }) {
  const g = GROUPS.find((x) => x.id === groupId)!;
  const states = stageStates(events, view?.status);
  return (
    <div className="pane-body">
      <div>
        <h2>{g.label}</h2>
        <div className="kicker">{g.id} · {code(g.stages[0])}{g.stages.length > 1 ? `–${code(g.stages[g.stages.length - 1])}` : ''} · {g.caption}</div>
      </div>
      <div className="phase-rows">
        {g.stages.map((s) => {
          const evs = events.filter((e) => e.stage === s);
          const last = evs[evs.length - 1];
          return (
            <div className="phase-row" key={s}>
              <div className="top">
                <code>{code(s)}</code><b>{PHASES[s].label}</b><code>{s}</code>
                <span className="kind">{PHASES[s].kind}</span>
              </div>
              <p>{PHASE_DOC[s]}</p>
              {view && (
                <p style={{ marginTop: 6 }}>
                  <StateText state={states[s]} />
                  {last && <> · {last.message}</>}
                  {evs.length > 2 && <span style={{ color: 'var(--faint)' }}> · {evs.length} sự kiện</span>}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RunDetail({ view, viewer, onAskAgent, onToast, onOpenRun }: {
  view: RunView; viewer: Viewer; onAskAgent: Props['onAskAgent']; onToast: Props['onToast']; onOpenRun: Props['onOpenRun'];
}) {
  const model = (view.spec?.transform as { model_name?: string } | undefined)?.model_name;
  const check = (n: string) => view.checks.find((c) => c.name === n);
  const obs = (c: GateResult | undefined, k: string) => (c?.observed as JsonObject | undefined)?.[k];
  const row = check('row_reconcile');
  const idem = check('idempotent');
  const dbt = check('dbt_test');
  const blocked = view.status.startsWith('BLOCKED') || view.status === 'NEEDS_HUMAN';

  const cell = (label: string, value: string, c: GateResult | undefined, sub: string) => (
    <div className="ev">
      <span>{label}</span>
      <strong>{value}</strong>
      <small className={c ? (c.result === 'pass' ? 'ok' : 'bad') : 'muted'}>
        {c ? (c.result === 'pass' ? <Check size={12} /> : <X size={12} />) : <Clock3 size={12} />}{c ? sub : 'Chưa đo'}
      </small>
    </div>
  );

  return (
    <div className="pane-body">
      <div>
        <h2>{model ?? (view.user_request.length > 60 ? `${view.user_request.slice(0, 60)}…` : view.user_request)}</h2>
        <div className="kicker">Chi tiết run · v{view.version} · {view.domain}</div>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <StatusBadge status={view.status} />
        {view.provenance.template_id != null
          ? <span className="trust"><BadgeCheck size={13} />Dựa trên mẫu #{view.provenance.template_id} · {view.provenance.template_reviewer} duyệt</span>
          : view.spec && <span className="trust new">Mẫu mới</span>}
      </div>
      <p>{view.user_request}</p>

      {blocked && <BlockedCard view={view} viewer={viewer} onAskAgent={onAskAgent} onToast={onToast} onOpenRun={onOpenRun} />}

      <section>
        <h3>Bằng chứng</h3>
        <div className="evidence">
          {cell('Đối soát dòng', row ? `${num(obs(row, 'source') as number)} = ${num(obs(row, 'bronze') as number)}` : '—', row, 'Nguồn = bronze')}
          {cell('Idempotent', idem ? `${num(obs(idem, 'run2') as number)} dòng` : '—', idem, 'Chạy lần 2 không đổi')}
          {cell('dbt test', dbt ? `${num(obs(dbt, 'passed') as number)} / ${num((obs(dbt, 'passed') as number) + (obs(dbt, 'failed') as number))}` : '—', dbt, 'Đạt')}
          <div className="ev">
            <span>Byte quét</span>
            <strong>{bytes(view.cost.estimated_bytes)}</strong>
            <small className={view.cost.usd != null ? 'ok' : 'muted'}>{usd(view.cost.usd)}{view.cost.is_estimate ? ' · ước tính' : ''}</small>
          </div>
          <div className="ev wide">
            <span>Thời gian model (trước → sau tối ưu)</span>
            <strong>{secs(view.timing.baseline_sec)} → {secs(view.timing.optimized_sec)}</strong>
            <small className="muted">{view.optimizer.find((o) => o.applied)?.rule ?? 'Chưa áp luật tối ưu'}</small>
          </div>
        </div>
      </section>

      <section>
        <h3>Bộ đếm (trần đọc từ budgets.yml)</h3>
        <div className="counters">
          <span className={`counter ${view.attempts.spec >= view.caps.spec ? 'hit' : ''}`}>Sửa spec <b>{view.attempts.spec}/{view.caps.spec}</b></span>
          <span className={`counter ${view.attempts.sql >= view.caps.sql ? 'hit' : ''}`}>Sửa SQL <b>{view.attempts.sql}/{view.caps.sql}</b></span>
          <span className={`counter ${view.llm_calls >= view.caps.llm ? 'hit' : ''}`}>Gọi LLM <b>{view.llm_calls}/{view.caps.llm}</b></span>
        </div>
      </section>

      <section>
        <h3>Cổng và phép kiểm tra</h3>
        <div className="checklist">
          {[...view.gates, ...view.checks].map((g) => (
            <div className="check-row" key={g.name}>
              {g.result === 'pass' ? <Check size={14} color="var(--ok)" /> : <X size={14} color="var(--bad)" />}
              <code>{g.name}</code>
              <span className="d">{g.detail}</span>
            </div>
          ))}
          {!view.gates.length && <span style={{ color: 'var(--faint)', fontSize: 12 }}>Chưa có cổng nào chạy.</span>}
        </div>
      </section>

      {typeof view.arbitration?.reason === 'string' && (
        <section><h3>Phân xử (P5)</h3><p style={{ fontSize: 13 }}>{view.arbitration.reason}</p></section>
      )}

      <section>
        <h3>Thông tin</h3>
        <dl className="kv">
          <dt>Run</dt><dd className="mono">{view.run_id.slice(0, 8)}</dd>
          <dt>Người tạo</dt><dd>{view.created_by}</dd>
          <dt>files_hash</dt><dd className="mono">{shortHash(view.files_hash)}</dd>
          <dt>LLM</dt><dd>{view.llm_provider || '—'}</dd>
          {view.approval && <><dt>Duyệt bởi</dt><dd>{view.approval.reviewer} · hash {shortHash(view.approval.hash)}</dd></>}
          {view.deployed_paths.length > 0 && <><dt>Đã ghi</dt><dd className="mono" style={{ fontSize: 11 }}>{view.deployed_paths.join('\n')}</dd></>}
        </dl>
      </section>
    </div>
  );
}

function BlockedCard({ view, viewer, onAskAgent, onToast, onOpenRun }: {
  view: RunView; viewer: Viewer; onAskAgent: Props['onAskAgent']; onToast: Props['onToast']; onOpenRun: Props['onOpenRun'];
}) {
  const [ex, setEx] = useState<Explanation | null>(null);
  useEffect(() => { api.explain(view.run_id).then(setEx).catch(() => setEx(null)); }, [view.run_id, view.status]);
  const semantic = view.status === 'BLOCKED_SEMANTIC_CONFLICT';
  const defs = (view.gates.find((g) => g.name === 'semantic')?.observed.definitions ?? []) as { domain: string }[];

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); onToast(ok); } catch (e) { onToast(e instanceof Error ? e.message : String(e), true); }
  }

  return (
    <div className="blocked-card">
      <h3><AlertOctagon size={16} />{ex?.title ?? 'Vì sao bị chặn'}</h3>
      <p style={{ fontSize: 13 }}>{ex?.summary ?? '…'}</p>
      {ex && ex.bullets.length > 0 && <ul>{ex.bullets.map((b) => <li key={b}>{b}</li>)}</ul>}
      <div className="row">
        {semantic && (
          <>
            <button className="btn sm" disabled={!!view.ticket}
              onClick={() => act(() => api.ticket(view.run_id, 'steward_review', ex?.summary ?? '', viewer.user), 'Đã tạo phiếu chuyển steward')}>
              {view.ticket ? 'Đã có phiếu steward' : 'Chuyển steward'}
            </button>
            {defs.map((d) => (
              <button key={d.domain} className="btn sm" onClick={() => act(async () => {
                const r = await api.retryScoped(view.run_id, d.domain, viewer.user); onOpenRun(r.run_id);
              }, `Chạy lại chỉ trong domain ${d.domain}`)}>Chỉ dùng nghĩa {d.domain}</button>
            ))}
          </>
        )}
        {view.status === 'BLOCKED_BUDGET' && (
          <button className="btn sm" onClick={() => act(() => api.ticket(view.run_id, 'budget_increase', ex?.summary ?? '', viewer.user), 'Đã gửi đề nghị nâng trần')}>
            Đề nghị nâng trần
          </button>
        )}
        {(view.status === 'BLOCKED_BUDGET' || view.status === 'NEEDS_HUMAN') && (
          <button className="btn sm" onClick={() => onAskAgent('changes')}><MessageSquareText size={13} />Góp ý cho agent</button>
        )}
        <button className="btn sm agent" onClick={() => onAskAgent('why')}><Sparkles size={13} />Hỏi agent</button>
      </div>
    </div>
  );
}
