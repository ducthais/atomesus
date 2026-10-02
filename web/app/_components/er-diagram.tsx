import Link from 'next/link';
import { ArrowRight, KeyRound, Link2 } from 'lucide-react';
import type { Catalog } from '@/lib/catalog/query';
import type { Dataset, Relationship } from '@/lib/catalog/types';
import { LayerBadge } from './ui';

/** Bảng vẽ trên sơ đồ: dataset trong catalog, hoặc bảng vừa đọc được khi kết nối nguồn. */
export type ErTable = Pick<Dataset, 'id' | 'name' | 'layer' | 'columns'>;

const W = 260, HEADER = 50, ROW = 28, FOOT = 8, COL_GAP = 150, ROW_GAP = 28, PAD = 12;

/**
 * Sơ đồ ER: mỗi bảng là một thẻ liệt kê cột (khóa chính, khóa ngoại); đường liền = khóa ngoại, đường đứt = join của pipeline.
 * Bảng chứa khóa ngoại đặt bên trái bảng nó tham chiếu, nên mũi tên luôn đi từ "nhiều" (N) sang "một" (1).
 */
export function ErDiagram({ tables, relationships, focus }: { tables: ErTable[]; relationships: Relationship[]; focus?: string }) {
  const fks = relationships.filter(r => r.kind === 'foreign_key');
  // Độ sâu theo khóa ngoại: bảng không tham chiếu ai = 0; cột vẽ = maxDepth - depth.
  const depthMemo = new Map<string, number>();
  const depth = (id: string, seen: Set<string> = new Set()): number => {
    if (depthMemo.has(id)) return depthMemo.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const targets = fks.filter(r => r.from === id && r.to !== id).map(r => r.to);
    const value = targets.length ? 1 + Math.max(...targets.map(t => depth(t, seen))) : 0;
    depthMemo.set(id, value);
    return value;
  };
  const maxDepth = Math.max(0, ...tables.map(t => depth(t.id)));
  const columns: ErTable[][] = Array.from({ length: maxDepth + 1 }, () => []);
  for (const t of [...tables].sort((a, b) => a.name.localeCompare(b.name))) columns[maxDepth - depth(t.id)].push(t);
  const used = columns.filter(c => c.length);

  const height = (t: ErTable) => HEADER + Math.max(1, t.columns.length) * ROW + FOOT;
  const pos = new Map<string, { x: number; y: number }>();
  used.forEach((col, ci) => { let y = PAD; for (const t of col) { pos.set(t.id, { x: PAD + ci * (W + COL_GAP), y }); y += height(t) + ROW_GAP; } });
  const width = PAD * 2 + used.length * W + (used.length - 1) * COL_GAP;
  const total = PAD + Math.max(...used.map(col => col.reduce((h, t) => h + height(t) + ROW_GAP, 0)));
  const anchorY = (t: ErTable, column: string) => {
    const index = t.columns.findIndex(c => c.name === column);
    return pos.get(t.id)!.y + (index < 0 ? HEADER / 2 : HEADER + index * ROW + ROW / 2);
  };
  const byId = new Map(tables.map(t => [t.id, t]));
  const fkColumns = new Set(fks.flatMap(r => r.fromColumns.map(c => `${r.from}:${c}`)));

  return <div className="er-scroll" role="figure" aria-label="Sơ đồ quan hệ giữa các bảng">
    <div className="er" style={{ width, height: total }}>
      <svg width={width} height={total} aria-hidden>
        <defs>
          <marker id="er-one" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto"><path d="M9 0v10M6 0v10" className="er-mark" /></marker>
          <marker id="er-many" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="10" markerHeight="10" orient="auto"><path d="M10 5L0 0M10 5L0 10M10 5H0" className="er-mark" /></marker>
        </defs>
        {relationships.map((rel, i) => {
          const a = byId.get(rel.from), b = byId.get(rel.to);
          if (!a || !b) return null;
          const pa = pos.get(a.id)!, pb = pos.get(b.id)!;
          const ya = anchorY(a, rel.fromColumns[0]), yb = anchorY(b, rel.toColumns[0]);
          // Hai quan hệ trùng hai đầu (khóa ngoại + join) lệch nhau một chút để cùng nhìn thấy.
          const bend = rel.kind === 'join' ? 26 : 0;
          let d: string;
          if (pa.x === pb.x) {
            const x = pa.x + W, out = x + 50 + bend;
            d = `M${x} ${ya} C${out} ${ya} ${out} ${yb} ${x} ${yb}`;
          } else {
            const [x1, x2] = pa.x < pb.x ? [pa.x + W, pb.x] : [pa.x, pb.x + W];
            const mid = (x1 + x2) / 2;
            d = `M${x1} ${ya} C${mid} ${ya + bend} ${mid} ${yb + bend} ${x2} ${yb}`;
          }
          const hot = focus && (rel.from === focus || rel.to === focus || rel.usedBy.includes(focus));
          return <path key={`${rel.id}-${i}`} d={d} className={`er-edge ${rel.kind} ${hot ? 'hot' : ''}`}
            markerStart={rel.kind === 'foreign_key' ? 'url(#er-many)' : undefined} markerEnd={rel.kind === 'foreign_key' ? 'url(#er-one)' : undefined}>
            <title>{`${a.name}.${rel.fromColumns.join(', ')} → ${b.name}.${rel.toColumns.join(', ')} (${rel.kind === 'foreign_key' ? 'khóa ngoại' : rel.label})`}</title>
          </path>;
        })}
      </svg>
      {tables.map(t => {
        const p = pos.get(t.id)!;
        return <div key={t.id} className={`er-table layer-${t.layer} ${t.id === focus ? 'focus' : ''}`} style={{ left: p.x, top: p.y, width: W, height: height(t) }}>
          <Link href={`/datasets/${t.id}`} className="er-head"><LayerBadge layer={t.layer} /><strong title={t.name}>{t.name}</strong></Link>
          {t.columns.length ? <ul>{t.columns.map(c => {
            const fk = fkColumns.has(`${t.id}:${c.name}`) || !!c.references;
            return <li key={c.name} className={c.primaryKey ? 'pk' : fk ? 'fk' : ''}>
              <span className="er-key" aria-hidden>{c.primaryKey ? <KeyRound size={12} /> : fk ? <Link2 size={12} /> : null}</span>
              <span className="er-col" title={c.references ? `→ ${c.references}` : undefined}>{c.name}</span>
              <span className="er-type">{c.primaryKey ? 'PK' : fk ? 'FK' : c.type}</span>
            </li>;
          })}</ul> : <p className="er-empty">Chưa đọc được schema</p>}
        </div>;
      })}
    </div>
  </div>;
}

export function ErLegend() {
  return <div className="er-legend" aria-label="Chú giải">
    <span><svg width="36" height="10" aria-hidden><path d="M2 5H34" className="er-edge foreign_key" /></svg>Khóa ngoại (khai báo trong nguồn), N → 1</span>
    <span><svg width="36" height="10" aria-hidden><path d="M2 5H34" className="er-edge join" /></svg>Phép join pipeline dùng</span>
    <span><KeyRound size={12} aria-hidden /> Khóa chính</span>
    <span><Link2 size={12} aria-hidden /> Khóa ngoại</span>
  </div>;
}

/** Bảng liệt kê quan hệ: dùng chung cho trang Quan hệ và tab Quan hệ của dataset. */
export function RelationshipTable({ cat, relationships, tables = [] }: { cat: Catalog; relationships: Relationship[]; tables?: ErTable[] }) {
  const find = (id: string) => tables.find(t => t.id === id) ?? cat.dataset(id);
  return <div className="table-scroll"><table className="rel-table">
    <thead><tr><th>Bảng</th><th>Cột</th><th /><th>Bảng tham chiếu</th><th>Cột</th><th>Loại</th><th>Nguồn gốc</th></tr></thead>
    <tbody>{relationships.map(r => {
      const [a, b] = [find(r.from), find(r.to)];
      return <tr key={r.id}>
        <td>{a && <Link href={`/datasets/${a.id}`} className="dataset-link"><LayerBadge layer={a.layer} /><span className="mono">{a.name}</span></Link>}</td>
        <td className="mono">{r.fromColumns.join(', ')}</td>
        <td className="muted"><ArrowRight size={14} aria-label="tham chiếu" /></td>
        <td>{b && <Link href={`/datasets/${b.id}`} className="dataset-link"><LayerBadge layer={b.layer} /><span className="mono">{b.name}</span></Link>}</td>
        <td className="mono">{r.toColumns.join(', ')}</td>
        <td>{r.kind === 'foreign_key' ? <span className="badge tone-blue">Khóa ngoại · N–1</span> : <span className="badge tone-violet">{r.label}</span>}</td>
        <td>{r.kind === 'foreign_key' ? <><span className="muted small">constraint</span> <code>{r.label}</code></>
          : r.usedBy.length ? <span className="stack-xs">{r.usedBy.map(id => { const g = cat.dataset(id); return g && <Link key={id} href={`/datasets/${id}`} className="dataset-link"><LayerBadge layer={g.layer} /><span className="mono">{g.name}</span></Link>; })}</span>
          : <span className="muted small">Run chưa chạy thử xong</span>}</td>
      </tr>;
    })}</tbody>
  </table></div>;
}
