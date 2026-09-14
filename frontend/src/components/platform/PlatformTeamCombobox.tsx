import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, Loader2, Search, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { fetchPlatformTeamsPage, type PlatformTeam } from '@/services/platform-organization';

interface PlatformTeamComboboxProps {
  value: string | null;
  onChange: (value: string | null, team: PlatformTeam | null) => void;
  label: string;
  placeholder?: string;
  allowClear?: boolean;
  disabled?: boolean;
  className?: string;
}

export function PlatformTeamCombobox({
  value,
  onChange,
  label,
  placeholder = 'Pesquisar equipe…',
  allowClear = true,
  disabled = false,
  className,
}: PlatformTeamComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    if (!open) {
      setQuery('');
      setDebouncedQuery('');
    }
  }, [open]);

  const resolvedQuery = useQuery({
    queryKey: ['platform', 'organization', 'team-resolve', value],
    queryFn: () => fetchPlatformTeamsPage({ search: value!, limit: 1, offset: 0 }),
    enabled: Boolean(value),
    retry: false,
  });
  const optionsQuery = useQuery({
    queryKey: ['platform', 'organization', 'team-lookup', debouncedQuery],
    queryFn: () => fetchPlatformTeamsPage({ search: debouncedQuery, active: true, limit: 20, offset: 0 }),
    enabled: open,
    retry: false,
  });
  const selected = useMemo(
    () => resolvedQuery.data?.items.find((team) => team.id === value) ?? null,
    [resolvedQuery.data, value],
  );
  const choose = (team: PlatformTeam) => {
    onChange(team.id, team);
    setOpen(false);
  };

  return (
    <div className={cn('flex min-w-0 gap-1', className)}>
      <Button type="button" variant="outline" className="min-w-0 flex-1 justify-between font-normal" disabled={disabled} onClick={() => setOpen(true)} aria-label={label}>
        <span className="truncate">{value ? (selected?.name ?? (resolvedQuery.isPending ? 'Carregando equipe…' : 'Equipe removida ou indisponível')) : placeholder}</span>
        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
      </Button>
      {allowClear && value ? <Button type="button" variant="ghost" size="icon" disabled={disabled} onClick={() => onChange(null, null)} aria-label={`Limpar ${label}`}><X className="h-4 w-4" /></Button> : null}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>{label}</DialogTitle><DialogDescription>Busca paginada nas equipes ativas autorizadas para sua função.</DialogDescription></DialogHeader>
          <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input autoFocus className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={placeholder} aria-label={`Pesquisar ${label}`} /></div>
          <div className="max-h-80 overflow-y-auto rounded-lg border" role="listbox" aria-label={label}>
            {optionsQuery.isPending ? <p role="status" className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Consultando equipes…</p> : null}
            {optionsQuery.isError ? <p className="p-8 text-center text-sm text-destructive">Seletor indisponível. O campo não aceitará um identificador manual.</p> : null}
            {optionsQuery.data?.items.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">Nenhuma equipe ativa foi encontrada.</p> : null}
            {optionsQuery.data?.items.map((team) => <button key={team.id} type="button" role="option" aria-selected={team.id === value} className="flex w-full items-center gap-3 border-b px-3 py-3 text-left last:border-b-0 hover:bg-muted" onClick={() => choose(team)}><Check className={cn('h-4 w-4', team.id === value ? 'opacity-100' : 'opacity-0')} /><span className="min-w-0"><span className="block truncate text-sm font-medium">{team.name}</span><span className="block truncate text-xs text-muted-foreground">{team.slug}{team.managerUserId ? ' · gestor definido' : ' · sem gestor'}</span></span></button>)}
          </div>
          {optionsQuery.data?.hasMore ? <p className="text-xs text-muted-foreground">Há mais resultados. Refine a busca pelo nome da equipe.</p> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
