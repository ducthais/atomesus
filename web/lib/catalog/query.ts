/**
 * Truy vấn trên một Snapshot của catalog: tìm kiếm + facet, lineage, tra run ↔ dataset, xung đột thuật ngữ.
 * Thuần (không gọi mạng) nên test được với snapshot mock; trang lấy instance qua `getCatalog()` (source.ts).
 */
import type { Activity, CatalogRun, Dataset, Facet, Layer, LineageGraph, Platform, Relationship, SearchQuery, SearchResult, Snapshot } from './types.ts';

export const LAYERS: Layer[] = ['source', 'bronze', 'staging', 'gold'];
export const LAYER_LABEL: Record<Layer, string> = { source: 'Nguồn', bronze: 'Bronze', staging: 'Staging', gold: 'Gold' };
export const PLATFORM_LABEL: Record<Platform, string> = { postgres: 'PostgreSQL', mongodb: 'MongoDB', rest_api: 'REST API', filesystem: 'Filesystem', duckdb: 'DuckDB' };

const byNewest = (a: CatalogRun, b: CatalogRun) => b.updatedAt.localeCompare(a.updatedAt);
/** Dataset lưu id run đầy đủ (backend) hoặc 8 ký tự đầu (mock). */
const refersTo = (ids: string[], run: CatalogRun) => ids.some(id => run.id === id || run.id.startsWith(id));

function facet(items: Dataset[], key: (d: Dataset) => string[], label: (v: string) => string = v => v, order?: string[]): Facet[] {
  const counts = new Map<string, number>();
  for (const item of items) for (const value of key(item)) counts.set(value, (counts.get(value) ?? 0) + 1);
  const facets = [...counts].map(([value, count]) => ({ value, label: label(value), count }));
  return order ? facets.sort((a, b) => order.indexOf(a.value) - order.indexOf(b.value)) : facets.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

const matches = (d: Dataset, text: string) => {
  const hay = [d.name, d.description, d.domain, ...d.tags, ...d.terms, ...d.columns.map(c => c.name)].join(' ').toLowerCase();
  return text.toLowerCase().split(/\s+/).filter(Boolean).every(word => hay.includes(word));
};

export class Catalog {
  readonly s: Snapshot;
  readonly runs: CatalogRun[];
  constructor(snapshot: Snapshot) { this.s = snapshot; this.runs = [...snapshot.runs].sort(byNewest); }

  get datasets() { return this.s.datasets; }
  get domains() { return this.s.domains; }
  get glossary() { return this.s.glossary; }
  get people() { return this.s.people; }
  get policy() { return this.s.policy; }
  get live() { return this.s.live; }

  person(id: string) { return this.s.people.find(p => p.id === id || p.email === id); }
  personName = (id: string) => this.person(id)?.name ?? id;
  domainName = (id: string) => this.s.domains.find(d => d.id === id)?.name ?? id;

  /** Tìm dataset theo từ khóa (tên, mô tả, cột, tag, thuật ngữ) và facet. Facet đếm trên kết quả của từ khóa. */
  search(query: SearchQuery): SearchResult {
    const all = this.s.datasets;
    const byText = query.q ? all.filter(d => matches(d, query.q!)) : all;
    const items = byText.filter(d => (!query.domain || d.domain === query.domain) && (!query.layer || d.layer === query.layer)
      && (!query.platform || d.platform === query.platform) && (!query.tag || d.tags.includes(query.tag)) && (!query.owner || d.owner === query.owner));
    items.sort((a, b) => LAYERS.indexOf(b.layer) - LAYERS.indexOf(a.layer) || a.name.localeCompare(b.name));
    return {
      items, total: all.length,
      facets: {
        layer: facet(byText, d => [d.layer], v => LAYER_LABEL[v as Layer], LAYERS),
        domain: facet(byText, d => [d.domain], this.domainName),
        platform: facet(byText, d => [d.platform], v => PLATFORM_LABEL[v as Platform] ?? v),
        tag: facet(byText, d => d.tags),
        owner: facet(byText, d => [d.owner], this.personName),
      },
    };
  }

  dataset(id: string) { return this.s.datasets.find(d => d.id === id) ?? null; }
  /** `<source_ref>.<bảng>` (cách domains.yml viết) hoặc tên vật lý → dataset. */
  datasetByName(name: string) {
    const [ref, table] = name.split('.');
    return this.s.datasets.find(d => d.name === name || (d.sourceRef === ref && d.name.split('.').at(-1) === table));
  }
  downstreamOf(id: string) { return this.s.datasets.filter(d => d.upstream.includes(id)); }

  /** Lineage quanh một dataset (mọi tổ tiên và hậu duệ), hoặc toàn bộ một domain khi không truyền id. */
  lineage(id?: string, domain?: string): LineageGraph {
    let nodes: Dataset[];
    if (id) {
      const seen = new Set([id]);
      const walk = (next: (d: Dataset) => string[]) => {
        const stack = [id];
        while (stack.length) {
          const node = this.dataset(stack.pop()!);
          for (const n of node ? next(node) : []) if (!seen.has(n)) { seen.add(n); stack.push(n); }
        }
      };
      walk(d => d.upstream);
      walk(d => this.downstreamOf(d.id).map(x => x.id));
      nodes = this.s.datasets.filter(d => seen.has(d.id));
    } else nodes = this.s.datasets.filter(d => !domain || d.domain === domain);
    const ids = new Set(nodes.map(n => n.id));
    const edges = nodes.flatMap(n => n.upstream.filter(u => ids.has(u)).map(u => ({ from: u, to: n.id })));
    return { nodes, edges, focus: id };
  }

  /** Bảng nguồn ("<source_ref>.<bảng>") nằm phía thượng nguồn của một dataset, để chọn sẵn khi tạo yêu cầu từ dataset đó. */
  sourceTablesOf(id: string): string[] {
    return this.lineage(id).nodes.filter(d => d.layer === 'source' && d.sourceRef && !d.unavailable && (d.id === id || this.isUpstream(d.id, id)))
      .map(d => `${d.sourceRef}.${d.name.split('.').at(-1)}`);
  }
  private isUpstream(ancestor: string, id: string): boolean {
    const node = this.dataset(id);
    return !!node && node.upstream.some(u => u === ancestor || this.isUpstream(ancestor, u));
  }

  /** Quan hệ giữa các bảng (khóa ngoại + join của pipeline), lọc theo domain nếu có. */
  relationships(domain?: string): Relationship[] {
    return this.s.relationships.filter(r => !domain || this.dataset(r.from)?.domain === domain);
  }
  /** Quan hệ chạm tới một dataset; với bảng gold là các phép join đã dựng nên nó. */
  relationsOf(id: string): Relationship[] {
    return this.s.relationships.filter(r => r.from === id || r.to === id || r.usedBy.includes(id));
  }
  /** Các bảng xuất hiện trong một tập quan hệ. */
  involvedTables(relationships: Relationship[]): Dataset[] {
    const ids = new Set(relationships.flatMap(r => [r.from, r.to]));
    return this.s.datasets.filter(d => ids.has(d.id));
  }
  domain(id: string) { return this.s.domains.find(d => d.id === id) ?? null; }
  domainDatasets(id: string) { return this.s.datasets.filter(d => d.domain === id); }

  term(id: string) { return this.s.glossary.find(t => t.id === id); }
  datasetsForTerm(id: string) { return this.s.datasets.filter(d => d.terms.includes(id) || d.columns.some(c => c.terms?.includes(id))); }
  /** Thực thể cùng tên ở domain khác mà chưa nằm trong conformed_dimensions: nguyên nhân BLOCKED_SEMANTIC_CONFLICT. */
  conflictsOf(id: string) {
    const term = this.term(id);
    const key = term?.id.split('.')[1];
    if (!term || !key || this.s.conformed.includes(key)) return [];
    return this.s.glossary.filter(t => t.id !== id && t.id.split('.')[1] === key && t.domain !== term.domain);
  }

  /** Tra run theo id đầy đủ hoặc 8 ký tự đầu (URL dùng id ngắn). */
  run(id: string) { return this.runs.find(r => r.id === id || r.id.startsWith(id)) ?? null; }
  runsForDataset(d: Dataset) { return this.runs.filter(r => refersTo(d.runs, r)); }
  datasetsForRun(run: CatalogRun) { return this.s.datasets.filter(d => refersTo(d.runs, run)); }

  /** Dòng hoạt động gần đây: tạo run, duyệt / từ chối. */
  activity(limit = 8): Activity[] {
    const items: Activity[] = [];
    for (const run of this.runs) {
      const href = `/runs/${run.id.slice(0, 8)}`;
      items.push({ id: `${run.id}-c`, at: run.logs[0]?.at ?? run.updatedAt, actor: run.owner, action: 'tạo run', target: run.title, href });
      if (run.approval) items.push({ id: `${run.id}-a`, at: run.approval.at, actor: run.approval.reviewer, action: run.approval.decision === 'approved' ? 'duyệt' : 'từ chối', target: run.title, href });
    }
    return items.sort((a, b) => b.at.localeCompare(a.at)).slice(0, limit);
  }

  stats() {
    const d = this.s.datasets;
    return {
      datasets: d.length, domains: this.s.domains.length, terms: this.s.glossary.length,
      gold: d.filter(x => x.layer === 'gold').length,
      unavailable: d.filter(x => x.unavailable).length,
      failing: d.flatMap(x => x.quality).filter(c => c.state === 'fail').length,
      awaitingReview: this.s.runs.filter(r => r.status === 'READY_FOR_REVIEW').length,
    };
  }
}
