import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, Loader2, Search, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import {
  lookupPlatformEntity,
  resolvePlatformEntity,
  type PlatformSearchEntityType,
  type PlatformSearchResult,
} from '@/services/platform-search';

interface PlatformEntityComboboxProps {
  entityType: PlatformSearchEntityType;
  purpose: string;
  value: string | null;
  onChange: (value: string | null, item: PlatformSearchResult | null) => void;
  workspaceId?: string | null;
  label: string;
  placeholder?: string;
  allowClear?: boolean;
  disabled?: boolean;
  className?: string;
}

export function PlatformEntityCombobox({
  entityType,
  purpose,
  value,
  onChange,
  workspaceId,
  label,
  placeholder = 'Pesquisar…',
  allowClear = true,
  disabled = false,
  className,
}: PlatformEntityComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    if (!open) { setQuery(''); setDebouncedQuery(''); }
  }, [open]);

  const resolvedQuery = useQuery({
    queryKey: ['platform-lookup-resolve', entityType, purpose, value, workspaceId],
    queryFn: () => resolvePlatformEntity({ entityType, purpose, ids: [value!], workspaceId }),
    enabled: Boolean(value),
    retry: false,
  });
  const optionsQuery = useQuery({
    queryKey: ['platform-lookup', entityType, purpose, debouncedQuery, workspaceId],
    queryFn: () => lookupPlatformEntity({ entityType, purpose, query: debouncedQuery, workspaceId }),
    enabled: open,
    retry: false,
  });
  const selected = useMemo(
    () => resolvedQuery.data?.items.find((item) => item.id === value) ?? null,
    [resolvedQuery.data, value],
  );
  const choose = (item: PlatformSearchResult) => { onChange(item.id, item); setOpen(false); };

  return (
    <div className={cn('flex min-w-0 gap-1', className)}>
      <Button type="button" variant="outline" className="min-w-0 flex-1 justify-between font-normal" disabled={disabled} onClick={() => setOpen(true)} aria-label={label}>
        <span className="truncate">{value ? (selected?.label ?? (resolvedQuery.isPending ? 'Carregando vínculo…' : 'Registro removido ou indisponível')) : placeholder}</span>
        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
      </Button>
      {allowClear && value ? <Button type="button" variant="ghost" size="icon" disabled={disabled} onClick={() => onChange(null, null)} aria-label={`Limpar ${label}`}><X className="h-4 w-4" /></Button> : null}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>{label}</DialogTitle><DialogDescription>Os resultados já respeitam sua permissão e o contexto da conta.</DialogDescription></DialogHeader>
          <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input autoFocus className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={placeholder} aria-label={`Pesquisar ${label}`} /></div>
          <div className="max-h-80 overflow-y-auto rounded-lg border" role="listbox" aria-label={label}>
            {optionsQuery.isPending ? <p role="status" className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Consultando registros…</p> : null}
            {optionsQuery.isError || optionsQuery.data?.state === 'unavailable' ? <p className="p-8 text-center text-sm text-destructive">Seletor indisponível. O campo não aceitará um identificador manual.</p> : null}
            {optionsQuery.data?.items.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">Nenhum registro autorizado foi encontrado.</p> : null}
            {optionsQuery.data?.items.map((item) => <button key={item.id} type="button" role="option" aria-selected={item.id === value} className="flex w-full items-center gap-3 border-b px-3 py-3 text-left last:border-b-0 hover:bg-muted" onClick={() => choose(item)}><Check className={cn('h-4 w-4', item.id === value ? 'opacity-100' : 'opacity-0')} /><span className="min-w-0"><span className="block truncate text-sm font-medium">{item.label}</span><span className="block truncate text-xs text-muted-foreground">{[item.publicIdentifier, item.secondaryLabel, item.workspaceLabel, item.status].filter(Boolean).join(' · ')}</span></span></button>)}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
