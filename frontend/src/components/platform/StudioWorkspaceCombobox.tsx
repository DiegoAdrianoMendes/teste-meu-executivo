import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, Loader2, Search } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import {
  fetchPlatformConfigurationWorkspacePage,
  type StudioWorkspaceOption,
} from '@/services/platform-configuration-studio';

export function StudioWorkspaceCombobox({
  value,
  selected,
  onChange,
}: {
  value: string;
  selected: StudioWorkspaceOption | null;
  onChange: (workspace: StudioWorkspaceOption) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [page, setPage] = useState(0);
  const pageSize = 20;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(0), [debouncedQuery]);
  useEffect(() => {
    if (!open) {
      setQuery('');
      setDebouncedQuery('');
      setPage(0);
    }
  }, [open]);

  const workspaces = useQuery({
    queryKey: ['blackbox', 'configuration-studio', 'workspace-page', debouncedQuery, page],
    queryFn: () => fetchPlatformConfigurationWorkspacePage({
      search: debouncedQuery,
      limit: pageSize,
      offset: page * pageSize,
    }),
    enabled: open,
    retry: false,
  });

  return (
    <>
      <Button
        id="studio-workspace"
        type="button"
        variant="outline"
        className="w-full justify-between font-normal"
        onClick={() => setOpen(true)}
        aria-label="Conta que receberá a configuração"
      >
        <span className="truncate">{selected?.name ?? (value ? 'Conta indisponível' : 'Selecione uma conta')}</span>
        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Selecionar conta do Estúdio</DialogTitle>
            <DialogDescription>Somente contas autorizadas aparecem, incluindo a validade do acesso assistido.</DialogDescription>
          </DialogHeader>
          <div className="relative">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input autoFocus className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Pesquisar por nome ou slug" />
          </div>
          <div className="max-h-80 overflow-y-auto rounded-lg border" role="listbox" aria-label="Contas autorizadas no Estúdio">
            {workspaces.isPending ? <p role="status" className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Consultando contas…</p> : null}
            {workspaces.isError ? <p className="p-8 text-center text-sm text-destructive">Não foi possível consultar as contas autorizadas.</p> : null}
            {workspaces.data?.items.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">Nenhuma conta autorizada foi encontrada.</p> : null}
            {workspaces.data?.items.map((workspace) => (
              <button
                key={workspace.id}
                type="button"
                role="option"
                aria-selected={workspace.id === value}
                className="flex w-full items-center gap-3 border-b px-3 py-3 text-left last:border-b-0 hover:bg-muted"
                onClick={() => { onChange(workspace); setOpen(false); }}
              >
                <Check className={cn('h-4 w-4', workspace.id === value ? 'opacity-100' : 'opacity-0')} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{workspace.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">{workspace.slug} · {workspace.canWrite ? 'leitura e escrita' : 'somente leitura'}{workspace.expiresAt ? ` · expira ${new Date(workspace.expiresAt).toLocaleString('pt-BR')}` : ''}</span>
                </span>
              </button>
            ))}
          </div>
          {workspaces.data ? (
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">{workspaces.data.total ? `${page * pageSize + 1}–${Math.min((page + 1) * pageSize, workspaces.data.total)} de ${workspaces.data.total}` : 'Sem resultados'}</p>
              <div className="flex gap-2">
                <Button type="button" size="sm" variant="outline" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={page === 0 || workspaces.isFetching}>Anterior</Button>
                <Button type="button" size="sm" variant="outline" onClick={() => setPage((current) => current + 1)} disabled={!workspaces.data.hasMore || workspaces.isFetching}>Próxima</Button>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
