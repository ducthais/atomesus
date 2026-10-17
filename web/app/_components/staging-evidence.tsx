import { CircleAlert, FilterX } from 'lucide-react';
import type { StagingEvidence } from '@/lib/studio';
import { Panel, QualityIcon, fmtNum } from './ui';

const value = (raw: unknown) => raw === null || raw === undefined
  ? <span className="null">NULL</span>
  : typeof raw === 'object' ? JSON.stringify(raw) : String(raw);

const reasonLabel = (reason: string) => {
  const [kind, detail] = reason.split(':', 2);
  const labels: Record<string, string> = {
    cast: 'Không ép được kiểu dữ liệu', date_format: 'Ngày sai định dạng', unknown_currency: 'Mã tiền tệ không hỗ trợ',
    reject_if: 'Vi phạm luật loại dòng', dedupe: 'Bản ghi trùng',
  };
  return `${labels[kind] ?? kind}${detail ? ` · ${detail}` : ''}`;
};

/** Bằng chứng bronze → staging theo từng nguồn, chỉ dùng số đo và dòng mẫu đã lưu trong RunReport. */
export function StagingEvidenceView({ sources }: { sources: StagingEvidence[] }) {
  return <div className="stack">
    <div className="callout info"><CircleAlert size={16} aria-hidden /><span>Dòng bị loại không biến mất im lặng: lý do và một số giá trị thô được chép vào sổ bằng chứng trước khi sandbox bị xóa.</span></div>
    {sources.map(source => {
      const columns = [...new Set(source.samples.flatMap(sample => Object.keys(sample.values)))];
      return <Panel key={source.sourceId} title={`Staging · ${source.sourceId}`}>
        <div className="staging-flow" aria-label={`Luồng làm sạch nguồn ${source.sourceId}`}>
          <div><span>Bronze</span><strong>{fmtNum(source.bronze)}</strong><small>Dòng đọc vào</small></div>
          <b aria-hidden>−</b>
          <div><span>Trùng</span><strong>{fmtNum(source.duplicates)}</strong><small>Loại khi dedupe</small></div>
          <b aria-hidden>−</b>
          <div className={source.rejected ? 'bad' : ''}><span>Rejected</span><strong>{fmtNum(source.rejected)}</strong><small>{source.rejectPct}%{source.maxRejectPct !== null ? ` / ngưỡng ${source.maxRejectPct}%` : ''}</small></div>
          <b aria-hidden>=</b>
          <div className="good"><span>Staging</span><strong>{fmtNum(source.staging)}</strong><small><QualityIcon state={source.state} /> {source.state === 'pass' ? 'Đạt' : 'Không đạt'}</small></div>
        </div>

        <div className="staging-sections">
          <section>
            <h3>Lý do bị loại</h3>
            {source.reasons.length ? <ul className="reason-list">{source.reasons.map(item => <li key={item.reason}><span>{reasonLabel(item.reason)}</span><strong className="mono">{fmtNum(item.count)}</strong></li>)}</ul>
              : <p className="muted">Không có dòng bị loại.</p>}
          </section>
          <section>
            <h3><FilterX size={15} aria-hidden /> Mẫu dòng bị loại</h3>
            {source.samples.length ? <div className="table-scroll"><table className="rejected-samples">
              <thead><tr><th>Lý do</th>{columns.map(column => <th key={column} className="mono">{column}</th>)}</tr></thead>
              <tbody>{source.samples.map((sample, index) => <tr key={`${sample.reason}-${index}`} className="row-rejected">
                <td><span className="badge tone-bad">{reasonLabel(sample.reason)}</span></td>
                {columns.map(column => <td key={column} className="mono">{value(sample.values[column])}</td>)}
              </tr>)}</tbody>
            </table></div> : <p className="muted">Run không ghi nhận mẫu dòng bị loại.</p>}
          </section>
        </div>
      </Panel>;
    })}
  </div>;
}
