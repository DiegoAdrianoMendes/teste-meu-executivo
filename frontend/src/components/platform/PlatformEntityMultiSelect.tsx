import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, Loader2, Search, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import {
  lookupPlatformEntity,
  resolvePlatformEntity,
  type PlatformSearchEntityType,
  type PlatformSearchResult,
} from '@/services/platform-search';

interface PlatformEntityMultiSelectProps {
  entityType: PlatformSearchEntityType;
  purpose: string;
  value: string[];
  onChange: (value: string[]) => void;
  label: string;
  placeholder?: string;
  workspaceId?: string | null;
  disabled?: boolean;
  excludedIds?: string[];
  className?: string;
}

export function PlatformEntityMultiSelect({
  entityType,
  purpose,
  value,
  onChange,
  label,
  placeholder = 'Pesquisar e adicionar…',
  workspaceId,
  disabled = false,
  excludedIds = [],
  className,
}: PlatformEntityMultiSelectProps) {
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
    queryKey: ['platform-lookup-resolve-many', entityType, purpose, value, workspaceId],
    queryFn: () => resolvePlatformEntity({ entityType, purpose, ids: value, workspaceId }),
    enabled: value.length > 0,
    retry: false,
  });
  const optionsQuery = useQuery({
    queryKey: ['platform-lookup-many', entityType, purpose, debouncedQuery, workspaceId],
    queryFn: () => lookupPlatformEntity({ entityType, purpose, query: debouncedQuery, workspaceId }),
    enabled: open,
    retry: false,
  });
  const labels = useMemo(
    () => new Map((resolvedQuery.data?.items ?? []).map((item) => [item.id, item.label])),
    [resolvedQuery.data],
  );
  const excluded = useMemo(() => new Set(excludedIds), [excludedIds]);
  const toggle = (item: PlatformSearchResult) => {
    if (excluded.has(item.id)) return;
    onChange(value.includes(item.id) ? value.filter((id) => id !== item.id) : [...value, item.id]);
  };

  return (
    <div className={cn('space-y-2', className)}>
      <Button type="button" variant="outline" className="w-full justify-between font-normal" disabled={disabled} onClick={() => setOpen(true)} aria-label={label}>
        <span>{value.length ? `${value.length} selecionado(s)` : placeholder}</span>
        <ChevronsUpDown className="ml-2 h-4 w-4 opacity-50" />
      </Button>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1" aria-label={`${label}: itens selecionados`}>
          {value.map((id) => (
            <Badge key={id} variant="secondary" className="max-w-full gap-1">
              <span className="truncate">{labels.get(id) ?? (resolvedQuery.isPending ? 'Carregando…' : 'Registro removido')}</span>
              <button type="button" onClick={() => onChange(value.filter((candidate) => candidate !== id))} aria-label={`Remover ${labels.get(id) ?? 'registro'}`}><X className="h-3 w-3" /></button>
            </Badge>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>{label}</DialogTitle><DialogDescription>Selecione registros autorizados. Identificadores técnicos não precisam ser digitados.</DialogDescription></DialogHeader>
          <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input autoFocus className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={placeholder} aria-label={`Pesquisar ${label}`} /></div>
          <div className="max-h-80 overflow-y-auto rounded-lg border" role="listbox" aria-multiselectable="true" aria-label={label}>
            {optionsQuery.isPending && <p role="status" className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Consultando registros…</p>}
            {optionsQuery.isError || optionsQuery.data?.state === 'unavailable' ? <p className="p-8 text-center text-sm text-destructive">Seletor indisponível. Nenhum identificador manual será aceito.</p> : null}
            {optionsQuery.data?.items.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">Nenhum registro autorizado foi encontrado.</p> : null}
            {optionsQuery.data?.items.map((item) => {
              const selected = value.includes(item.id);
              const blocked = excluded.has(item.id);
              return <button key={item.id} type="button" role="option" aria-selected={selected} disabled={blocked} className="flex w-full items-center gap-3 border-b px-3 py-3 text-left last:border-b-0 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50" onClick={() => toggle(item)}><Check className={cn('h-4 w-4', selected ? 'opacity-100' : 'opacity-0')} /><span className="min-w-0"><span className="block truncate text-sm font-medium">{item.label}</span><span className="block truncate text-xs text-muted-foreground">{[item.publicIdentifier, item.secondaryLabel, item.workspaceLabel, item.status, blocked ? 'já está na lista oposta' : null].filter(Boolean).join(' · ')}</span></span></button>;
            })}
          </div>
          <DialogFooter><Button type="button" onClick={() => setOpen(false)}>Concluir ({value.length})</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
