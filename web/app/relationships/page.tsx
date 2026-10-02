import Link from 'next/link';
import { CircleAlert, Database, Lock, Plug, RefreshCw, Unplug, Waypoints } from 'lucide-react';
import { PLATFORM_LABEL } from '@/lib/catalog/query';
import { discoverObjects, getCatalog, listDeclaredSources, rememberedConnections } from '@/lib/catalog/source';
import { connectForRelationships, disconnectForRelationships } from '../actions';
import type { Relationship } from '@/lib/catalog/types';
import { ErDiagram, ErLegend, RelationshipTable, type ErTable } from '../_components/er-diagram';
import { Empty, PageHeader, Panel } from '../_components/ui';

const TIME = new Intl.DateTimeFormat('vi-VN', { timeStyle: 'medium', timeZone: 'Asia/Ho_Chi_Minh' });

/**
 * Quan hệ giữa các bảng, đọc khi người dùng bấm "Kết nối": `?source=<ref>` làm server kết nối trực tiếp tới nguồn
 * (GET /catalog/sources/{ref}/objects), lấy bảng, khóa chính, khóa ngoại rồi vẽ ER. Không chọn nguồn thì không kết nối.
 */
export default async function RelationshipsPage({ searchParams }: { searchParams: Promise<{ source?: string }> }) {
  const [{ source: ref }, cat, sources, connected] = await Promise.all([searchParams, getCatalog(), listDeclaredSources(), rememberedConnections()]);
  // Không chỉ định nguồn trên URL thì mở lại nguồn đã kết nối trong phiên.
  const active = sources.find(s => s.ref === (ref ?? connected.find(c => sources.some(x => x.ref === c && x.supported))) && s.supported);
  const discovery = active ? await discoverObjects(active.ref) : null;

  let tables: ErTable[] = [];
  let relationships: Relationship[] = [];
  if (active && discovery && !('error' in discovery)) {
    const schema = active.location ?? active.ref;
    const idOf = (table: string) => cat.datasetByName(`${active.ref}.${table}`)?.id ?? `${active.domain}.source.${schema}.${table}`;
    tables = discovery.objects.map(o => ({
      id: idOf(o.name), name: `${schema}.${o.name}`, layer: 'source' as const,
      columns: o.columns.map(c => ({ name: c.name, type: c.type, primaryKey: o.primaryKey.includes(c.name) })),
    }));
    const ids = new Set(tables.map(t => t.id));
    relationships = [
      ...discovery.foreignKeys.filter(fk => fk.refSchema === schema).map(fk => ({
        id: `fk:${active.ref}.${fk.name}`, kind: 'foreign_key' as const, from: idOf(fk.table), fromColumns: fk.columns,
        to: idOf(fk.refTable), toColumns: fk.refColumns, label: fk.name, usedBy: [],
      })),
      // Phép join pipeline đã dùng giữa các bảng của nguồn này (từ Spec các run), chồng lên sơ đồ.
      ...cat.relationships().filter(r => r.kind === 'join' && ids.has(r.from) && ids.has(r.to)),
    ];
  }
  const fk = relationships.filter(r => r.kind === 'foreign_key').length;

  return <>
    <PageHeader kicker="Data Catalog" title="Quan hệ giữa các bảng" icon={<Waypoints size={22} />}>
      <p>Chọn một nguồn và bấm Kết nối: hệ thống kết nối trực tiếp tới nguồn để đọc bảng, khóa chính và khóa ngoại, rồi vẽ sơ đồ quan hệ. Phép join mà pipeline đã dùng được vẽ chồng lên.</p>
    </PageHeader>

    <section className="source-picker rel-sources" aria-label="Chọn nguồn">
      {sources.map(src => {
        const isActive = src.ref === active?.ref;
        return <div key={src.ref} className={`source-option ${src.supported ? '' : 'locked'} ${isActive ? 'active' : ''}`}>
          <div className="source-head">
            <span className="source-icon" aria-hidden>{src.supported ? <Database size={16} /> : <Lock size={15} />}</span>
            <span className="source-title"><strong>{src.ref}</strong><small>{PLATFORM_LABEL[src.kind] ?? src.kind}{src.location ? ` · ${src.location}` : ''} · domain {src.domain}</small></span>
            {!src.supported ? <span className="badge tone-muted">Chưa có connector cho {PLATFORM_LABEL[src.kind] ?? src.kind}</span>
              : !isActive ? <form action={connectForRelationships.bind(null, src.ref)}><button type="submit" className="button small"><Plug size={14} aria-hidden /> Kết nối</button></form>
              : discovery && 'error' in discovery ? <span className="source-meta"><span className="badge tone-bad">Không kết nối được</span><form action={disconnectForRelationships.bind(null, src.ref)}><button type="submit" className="link-button">Đóng</button></form></span>
              : <span className="source-meta">
                  <span className="badge tone-good"><span className="dot" />Đã kết nối · {discovery && !('error' in discovery) ? TIME.format(new Date(discovery.discoveredAt)) : ''}</span>
                  <Link href={`/relationships?source=${src.ref}&at=${Date.now()}`} className="icon-button small" title="Kết nối lại để đọc quan hệ mới" aria-label={`Làm mới ${src.ref}`}><RefreshCw size={14} aria-hidden /></Link>
                  <form action={disconnectForRelationships.bind(null, src.ref)}><button type="submit" className="icon-button small" title="Ngắt: lần sau phải bấm Kết nối lại" aria-label={`Ngắt ${src.ref}`}><Unplug size={14} aria-hidden /></button></form>
                </span>}
          </div>
        </div>;
      })}
    </section>

    {!active ? <Empty icon={<Plug size={22} />} title="Chưa kết nối nguồn nào">Bấm <strong>Kết nối</strong> ở một nguồn phía trên để xem các bảng của nó quan hệ với nhau thế nào.</Empty>
      : discovery && 'error' in discovery ? <div className="callout bad" role="alert"><CircleAlert size={16} aria-hidden /><span>{discovery.error}</span><Link href={`/relationships?source=${active.ref}&at=${Date.now()}`} className="button small">Thử lại</Link></div>
      : !tables.length ? <Empty title="Nguồn không có bảng nào">Kết nối được nhưng tài khoản chỉ đọc không thấy bảng nào trong {active.location ?? active.ref}.</Empty>
      : <>
        <Panel title={`Sơ đồ ER · ${active.ref}`} actions={<span className="muted small">{tables.length} bảng · {fk} khóa ngoại · {relationships.length - fk} phép join</span>}>
          <ErLegend />
          <ErDiagram tables={tables} relationships={relationships} />
        </Panel>
        {relationships.length ? <Panel title="Danh sách quan hệ" flush><RelationshipTable cat={cat} relationships={relationships} tables={tables} /></Panel>
          : <Empty title="Chưa có quan hệ nào">Các bảng của nguồn này chưa khai báo khóa ngoại và chưa có pipeline nào join chúng.</Empty>}
      </>}
  </>;
}
