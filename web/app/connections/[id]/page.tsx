import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Database } from 'lucide-react';
import { currentUser } from '@/lib/catalog/source';
import { getUserConnectionSchemas, getUserConnectionTables, listUserConnections } from '../../actions';
import { ConnectionTables } from '../../_components/connection-tables';
import { PageHeader } from '../../_components/ui';

export default async function ConnectionPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await currentUser();
  if (!user) return null;
  if (user.role !== 'Engineer') notFound();
  const { id } = await params;
  const result = await listUserConnections();
  if ('error' in result) return <p className="callout bad" role="alert">{result.error}</p>;
  const connection = result.data.find(item => item.id === id);
  if (!connection || !('host' in connection)) notFound();
  const schemas = await getUserConnectionSchemas(id);
  const selection = connection.selection ?? null;
  const schema = 'data' in schemas ? (schemas.data.find(item => item.name === selection?.schema)?.name ?? schemas.data[0]?.name ?? '') : '';
  const tables = schema ? await getUserConnectionTables(id, schema) : { data: [] };
  return <div className="user-connections">
    <nav className="breadcrumbs" aria-label="Vị trí"><Link href="/connections">Nguồn dữ liệu</Link><span>/</span><span>{connection.name}</span></nav>
    <PageHeader kicker="Postgres" title={connection.name} icon={<Database size={22} aria-hidden />}>
      <p className="mono">{connection.host}:{connection.port}/{connection.database} · {connection.user} · {connection.sslmode}</p>
    </PageHeader>
    {connection.selection_error && <p className="callout bad" role="alert">{connection.selection_error}</p>}
    {'error' in schemas ? <p className="callout bad" role="alert">{schemas.error}</p>
      : <ConnectionTables id={id} schemas={schemas.data} initialSchema={schema} initialTables={tables} selection={selection} />}
  </div>;
}
