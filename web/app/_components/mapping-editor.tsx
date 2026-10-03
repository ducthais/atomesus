'use client';

import { useMemo, useState, useTransition } from 'react';
import { CircleAlert, LoaderCircle, Play, Plus } from 'lucide-react';
import { COLUMN_TYPES, type AgentSpec, type TimeUnit } from '@/lib/postgres-api';
import { PERIOD_LABEL } from '@/lib/catalog/explain';
import { applyMapping, mappingErrors, suggestColumn, type SourceMapping } from '@/lib/catalog/mapping';
import { continueRun, type ActionState } from '../actions';

type SourceColumns = Record<string, { table: string; sourceRef: string; columns: { name: string; type: string }[] }>;

/**
 * Bản nháp: Engineer xem và sửa mapping cột (tên, kiểu, định dạng ngày, thêm / bỏ cột) và luật loại dòng
 * trước khi agent sinh code và chạy thử. Gửi đi thì backend kiểm tra lại toàn bộ Spec bằng các cổng của agent.
 */
export function MappingEditor({ runId, spec, sourceColumns, canEdit }: { runId: string; spec: AgentSpec; sourceColumns: SourceColumns; canEdit: boolean }) {
  const [sources, setSources] = useState<SourceMapping[]>(() => spec.sources.map(s => ({
    id: s.id, rejectIf: s.staging?.reject_if ?? [],
    columns: (s.staging?.columns ?? []).map(c => ({ ...(c as object), from: c.from, as: c.as, type: c.type, keep: true })),
  })));
  // Kỳ gộp cho cột thời gian của grain (tên theo Spec gốc, applyMapping đổi theo khi đổi tên cột).
  const [period, setPeriod] = useState<{ column: string; unit: TimeUnit } | null>(spec.transform.time_grain ?? null);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const errors = mappingErrors(sources);
  const preview = useMemo(() => applyMapping({ ...spec, transform: { ...spec.transform, time_grain: period } }, sources), [spec, sources, period]);
  // Cột grain (tên theo Spec gốc) mà kiểu hiện tại là date / timestamp thì cho chọn gộp theo kỳ.
  const timeColumns = spec.transform.grain.filter(g => {
    const [alias, name] = g.includes('.') ? g.split('.') : [spec.sources[0].id, g];
    const from = spec.sources.find(s => s.id === alias)?.staging?.columns.find(c => c.as === name)?.from;
    const type = sources.find(s => s.id === alias)?.columns.find(c => c.from === from)?.type;
    return type === 'date' || type === 'timestamp';
  });

  const update = (sid: string, change: (s: SourceMapping) => SourceMapping) => { setState(null); setSources(prev => prev.map(s => s.id === sid ? change(s) : s)); };
  const setColumn = (sid: string, from: string, patch: Partial<SourceMapping['columns'][number]>) =>
    update(sid, s => ({ ...s, columns: s.columns.map(c => c.from === from ? { ...c, ...patch } : c) }));

  return <div className="stack">
    <div className="callout info"><CircleAlert size={16} aria-hidden /><span>Đây là bản nháp: agent đã đọc schema thật và đề xuất mapping dưới đây, <strong>chưa sinh code và chưa chạy gì</strong>. Sửa nếu cần rồi bấm <strong>Chạy thử</strong>; hệ thống kiểm tra lại cột có thật ở nguồn và biểu thức an toàn trước khi chạy.</span></div>
    <div className="callout warn"><CircleAlert size={16} aria-hidden /><span><strong>Bạn xác nhận Spec đúng nhu cầu.</strong> Sau khi chạy thử, hệ thống đối chiếu kết quả với nguồn để kiểm tra pipeline <strong>làm đúng Spec này</strong>; nó không biết Spec có đúng ý nghiệp vụ hay không. Hãy kiểm tra cột nào được lấy, gộp theo gì, chỉ số tính thế nào và bộ lọc ở khung “Bảng kết quả sẽ tính”.</span></div>
    {sources.map(src => {
      const info = sourceColumns[src.id];
      const specSource = spec.sources.find(s => s.id === src.id)!;
      const used = new Set(src.columns.map(c => c.from));
      const available = (info?.columns ?? []).filter(c => !used.has(c.name));
      const sourceType = (name: string) => info?.columns.find(c => c.name === name)?.type ?? '';
      return <section key={src.id} className="panel flush">
        <div className="panel-head"><h2>Mapping cột · <code>{specSource.source_ref}.{info?.table ?? specSource.table ?? src.id}</code></h2>
          <span className="muted small">{src.columns.filter(c => c.keep).length} cột đưa vào staging</span></div>
        <div className="table-scroll"><table className="mapping-table">
          <thead><tr><th>Giữ</th><th>Cột nguồn</th><th>Tên đầu ra</th><th>Kiểu</th><th>Định dạng ngày</th></tr></thead>
          <tbody>{src.columns.map(c => <tr key={c.from} className={c.keep ? '' : 'dropped'}>
            <td><input type="checkbox" checked={c.keep} disabled={!canEdit} onChange={e => setColumn(src.id, c.from, { keep: e.target.checked })} aria-label={`Giữ cột ${c.from}`} /></td>
            <td><code>{c.from}</code><br /><small className="muted">{sourceType(c.from) || '—'}</small></td>
            <td><input value={c.as} disabled={!canEdit || !c.keep} onChange={e => setColumn(src.id, c.from, { as: e.target.value })} aria-label={`Tên đầu ra của ${c.from}`} /></td>
            <td><select value={c.type} disabled={!canEdit || !c.keep} onChange={e => setColumn(src.id, c.from, { type: e.target.value })} aria-label={`Kiểu của ${c.from}`}>
              {[...new Set([c.type, ...COLUMN_TYPES])].map(t => <option key={t}>{t}</option>)}</select></td>
            {/* Định dạng ngày chỉ cần khi nguồn là chuỗi; cột nguồn đã là date / timestamp thì ép kiểu trực tiếp. */}
            <td>{['date', 'timestamp'].includes(c.type) && (c.date_formats?.length || !/^(date|timestamp)/.test(sourceType(c.from)))
              ? <input value={(c.date_formats ?? []).join(', ')} placeholder="%Y-%m-%d, %d/%m/%Y" disabled={!canEdit || !c.keep}
                  onChange={e => setColumn(src.id, c.from, { date_formats: e.target.value.split(',').map(v => v.trim()).filter(Boolean) })} aria-label={`Định dạng ngày của ${c.from}`} />
              : <span className="muted">—</span>}</td>
          </tr>)}</tbody>
        </table></div>
        {canEdit && available.length > 0 && <div className="pad add-column"><Plus size={14} aria-hidden /><label>Thêm cột từ nguồn:
          <select value="" onChange={e => { const col = available.find(a => a.name === e.target.value); if (col) update(src.id, s => ({ ...s, columns: [...s.columns, suggestColumn(col.name, col.type)] })); }}>
            <option value="">— chọn cột —</option>{available.map(a => <option key={a.name} value={a.name}>{a.name} ({a.type})</option>)}
          </select></label></div>}
        <div className="pad"><label className="field">Luật loại dòng <span className="muted small">(mỗi dòng một điều kiện SQL; dòng thỏa điều kiện bị tách sang bảng rejected)</span>
          <textarea rows={3} value={src.rejectIf.join('\n')} disabled={!canEdit} onChange={e => update(src.id, s => ({ ...s, rejectIf: e.target.value.split('\n') }))} /></label></div>
      </section>;
    })}

    <section className="panel">
      <div className="panel-head"><h2>Bảng kết quả sẽ tính</h2><span className="muted small">Tự cập nhật khi đổi tên cột</span></div>
      <dl className="facts">
        <div><dt>Bảng</dt><dd><code>gold.{preview.transform.model_name}</code></dd></div>
        <div><dt>Gộp theo</dt><dd>{preview.transform.grain.length ? preview.transform.grain.map(g => <code key={g} className="block-code">{g}</code>) : 'một dòng tổng'}</dd></div>
        {timeColumns.map(g => <div key={g}><dt>Kỳ thời gian</dt><dd>
          <select value={period?.column === g ? period.unit : ''} disabled={!canEdit} aria-label={`Gộp ${g} theo kỳ`}
            onChange={e => { setState(null); setPeriod(e.target.value ? { column: g, unit: e.target.value as TimeUnit } : null); }}>
            <option value="">từng thời điểm (không gộp)</option>
            {(Object.keys(PERIOD_LABEL) as TimeUnit[]).map(u => <option key={u} value={u}>theo {PERIOD_LABEL[u]}</option>)}
          </select> <span className="muted small">mỗi dòng kết quả là một {period?.column === g ? PERIOD_LABEL[period.unit] : 'thời điểm'} của <code>{g.split('.').at(-1)}</code></span>
        </dd></div>)}
        <div><dt>Chỉ số</dt><dd>{preview.transform.metrics.map(m => <code key={m.name} className="block-code">{m.name} = {m.expr}</code>)}</dd></div>
        <div><dt>Bộ lọc</dt><dd>{preview.transform.filters.length ? preview.transform.filters.map(f => <code key={f} className="block-code">{f}</code>) : 'không có'}</dd></div>
      </dl>
    </section>

    {errors.length > 0 && <div className="callout warn" role="alert"><CircleAlert size={16} aria-hidden /><div>{errors.map(e => <p key={e}>{e}</p>)}</div></div>}
    {state?.error && <div className="callout bad" role="alert"><CircleAlert size={16} aria-hidden /><div><strong>Chưa chạy được</strong>{state.error.split('; ').map(e => <p key={e}>{e}</p>)}</div></div>}
    <div className="form-actions">
      {!canEdit ? <span className="muted">Chỉ người tạo bản nháp (Engineer) được sửa và chạy tiếp.</span>
        : <span className="muted small">Bấm chạy thử là xác nhận Spec này đúng nhu cầu; mốc xác nhận được ghi vào sổ bằng chứng.</span>}
      <button className="button primary" disabled={!canEdit || errors.length > 0 || pending} onClick={() => start(async () => setState(await continueRun(runId, preview)))}>
        {pending ? <><LoaderCircle size={15} className="spin" aria-hidden /> Đang sinh code và chạy thử…</> : <><Play size={15} aria-hidden /> Chạy thử với mapping này</>}
      </button>
    </div>
  </div>;
}
