import { useMemo, useState } from 'react';
import { Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { formatBRL } from '@/lib/formatters';
import type { KlmFinanceAnalyticsGroup } from '@/klm/api';

export type FinanceDrilldownRow = { label: string; value: number; detail?: string };

type HierarchicalProps = {
  groups: KlmFinanceAnalyticsGroup[];
  dimension: KlmFinanceAnalyticsGroup['dimension'];
  total: number;
  totalCount: number;
  offset: number;
  limit: number;
  hasMore: boolean;
  breadcrumb: Array<{ dimension:string; label:string; onSelect:()=>void }>;
  onGroupSelect: (group:KlmFinanceAnalyticsGroup)=>void;
  onPageChange: (offset:number)=>void;
  loading?: boolean;
};

export function FinanceDrilldownExplorer({ rows, title = 'Explorar composição', onSelect }: { rows: FinanceDrilldownRow[]; title?: string; onSelect?: (row: FinanceDrilldownRow) => void }) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<FinanceDrilldownRow | null>(null);
  const filtered = useMemo(() => rows.filter((row) => row.label.toLowerCase().includes(query.toLowerCase())), [rows, query]);
  return <div className="rounded-xl border border-border bg-card p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">{title}</h3><div className="relative w-full sm:w-56"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filtrar categorias" className="h-8 pl-8 text-xs" /></div></div>
    <div className="mt-3 divide-y divide-border">{filtered.length ? filtered.map((row) => <button key={row.label} onClick={() => { setSelected(row); onSelect?.(row); }} className="flex w-full items-center justify-between gap-3 py-2 text-left text-xs hover:bg-muted/40"><span className="truncate">{row.label}</span><span className="font-medium tabular-nums">{formatBRL(row.value)}</span></button>) : <p className="py-4 text-xs text-muted-foreground">Nenhuma categoria encontrada.</p>}</div>
    {selected && <div className="mt-3 rounded-lg bg-muted/50 p-3 text-xs"><div className="flex justify-between"><strong>{selected.label}</strong><Button variant="ghost" size="icon" className="h-5 w-5" onClick={() => setSelected(null)}><X className="h-3 w-3" /></Button></div><p className="mt-1 text-muted-foreground">{selected.detail ?? 'Detalhamento disponível conforme os lançamentos persistidos.'}</p><p className="mt-2 font-semibold tabular-nums">{formatBRL(selected.value)}</p></div>}
  </div>;
}

const dimensionLabel: Record<KlmFinanceAnalyticsGroup['dimension'], string> = {
  cost_center: 'centro de custo', client: 'cliente', contract: 'contrato', matter: 'caso', entry: 'lançamento',
};

export function FinanceHierarchicalDrilldown({ groups, dimension, total, totalCount, offset, limit, hasMore, breadcrumb, onGroupSelect, onPageChange, loading = false }: HierarchicalProps) {
  const [query, setQuery] = useState('');
  const filtered = useMemo(() => groups.filter((group) => group.label.toLowerCase().includes(query.toLowerCase())), [groups, query]);
  const currentPage = Math.floor(offset / limit) + 1;
  const pageCount = Math.max(1, Math.ceil(totalCount / limit));
  return <section className="rounded-xl border border-border bg-card p-3 sm:p-4" aria-label="Explorador financeiro hierárquico">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div><h3 className="text-sm font-semibold">Composição por {dimensionLabel[dimension]}</h3><p className="mt-1 text-xs text-muted-foreground">Total do recorte: <strong className="text-foreground">{formatBRL(total)}</strong></p></div>
      <div className="relative w-full sm:w-64"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input aria-label={`Filtrar ${dimensionLabel[dimension]}`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Filtrar ${dimensionLabel[dimension]}`} className="min-h-11 pl-9 text-base sm:min-h-8 sm:text-xs" /></div>
    </div>
    <nav className="mt-3 flex flex-wrap items-center gap-1 text-xs" aria-label="Breadcrumb financeiro">
      {breadcrumb.map((crumb, index) => <span key={`${crumb.dimension}-${index}`} className="flex items-center gap-1"><button type="button" className="min-h-11 rounded px-2 text-primary hover:bg-muted sm:min-h-8" onClick={crumb.onSelect}>{crumb.label}</button>{index < breadcrumb.length - 1 && <span aria-hidden>›</span>}</span>)}
    </nav>
    {loading ? <p className="py-8 text-center text-xs text-muted-foreground" role="status">Consultando composição…</p> : filtered.length ? <div className="mt-2 divide-y divide-border">{filtered.map((group) => <button type="button" key={`${group.dimension}:${group.id ?? 'unallocated'}`} disabled={!group.id} onClick={() => onGroupSelect(group)} className="flex min-h-11 w-full items-center justify-between gap-3 rounded px-2 py-2 text-left text-xs hover:bg-muted/50 disabled:cursor-not-allowed disabled:text-muted-foreground"><span className="min-w-0 truncate">{group.label}</span><span className="shrink-0 font-medium tabular-nums">{formatBRL(Number(group.amount))}</span></button>)}</div> : <p className="py-8 text-center text-xs text-muted-foreground">Nenhum valor neste recorte.</p>}
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs"><span>Página {currentPage} de {pageCount} · {totalCount} lançamentos</span><div className="flex gap-2"><Button size="sm" variant="outline" className="min-h-11 sm:min-h-8" disabled={offset === 0 || loading} onClick={() => onPageChange(Math.max(0, offset - limit))}>Anterior</Button><Button size="sm" variant="outline" className="min-h-11 sm:min-h-8" disabled={!hasMore || loading} onClick={() => onPageChange(offset + limit)}>Próxima</Button></div></div>
  </section>;
}
