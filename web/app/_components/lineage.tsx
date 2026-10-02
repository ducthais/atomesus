import Link from 'next/link';
import { LAYERS, LAYER_LABEL } from '@/lib/catalog/query';
import type { LineageGraph } from '@/lib/catalog/types';
import { fmtNum } from './ui';

const W = 216, H = 58, COL_GAP = 56, ROW_GAP = 14, PAD = 8, HEAD = 26;

/** Lineage theo cột lớp (nguồn → bronze → staging → gold). Node là link tới trang dataset; cạnh vẽ bằng SVG. */
export function Lineage({ graph }: { graph: LineageGraph }) {
  const columns = LAYERS.map(layer => graph.nodes.filter(n => n.layer === layer).sort((a, b) => a.name.localeCompare(b.name))).filter(col => col.length);
  const pos = new Map<string, { x: number; y: number }>();
  const tallest = Math.max(...columns.map(col => col.length));
  columns.forEach((col, ci) => {
    const offset = (tallest - col.length) * (H + ROW_GAP) / 2;
    col.forEach((node, ri) => pos.set(node.id, { x: PAD + ci * (W + COL_GAP), y: HEAD + PAD + offset + ri * (H + ROW_GAP) }));
  });
  const width = PAD * 2 + columns.length * W + (columns.length - 1) * COL_GAP;
  const height = HEAD + PAD * 2 + tallest * H + (tallest - 1) * ROW_GAP;

  return <div className="lineage-scroll" role="figure" aria-label="Sơ đồ lineage">
    <div className="lineage" style={{ width, height }}>
      {columns.map((col, ci) => <div key={col[0].layer} className="lineage-col-head" style={{ left: PAD + ci * (W + COL_GAP), width: W }}>{LAYER_LABEL[col[0].layer]}</div>)}
      <svg width={width} height={height} aria-hidden>
        <defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 8 4 0 8z" className="lineage-arrow" /></marker></defs>
        {graph.edges.map(edge => {
          const a = pos.get(edge.from)!, b = pos.get(edge.to)!;
          const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 2, y2 = b.y + H / 2, mid = (x1 + x2) / 2;
          const hot = graph.focus && (edge.from === graph.focus || edge.to === graph.focus);
          return <path key={`${edge.from}>${edge.to}`} d={`M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`} className={`lineage-edge ${hot ? 'hot' : ''}`} markerEnd="url(#arrow)" />;
        })}
      </svg>
      {graph.nodes.map(node => {
        const p = pos.get(node.id)!;
        const focus = node.id === graph.focus;
        return <Link key={node.id} href={`/datasets/${node.id}`} className={`lineage-node layer-${node.layer} ${focus ? 'focus' : ''} ${node.unavailable ? 'unavailable' : ''}`}
          style={{ left: p.x, top: p.y, width: W, height: H }} aria-current={focus ? 'true' : undefined}>
          <span className="lineage-stripe" aria-hidden />
          <strong title={node.name}>{node.name}</strong>
          <small>{node.unavailable ? 'Chưa đọc được' : `${fmtNum(node.rowCount)} dòng · ${node.columns.length} cột`}</small>
        </Link>;
      })}
    </div>
  </div>;
}
