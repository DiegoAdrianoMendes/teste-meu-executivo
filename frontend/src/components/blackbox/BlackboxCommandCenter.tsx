import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertTriangle, Search, Zap } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useToast } from '@/hooks/use-toast';
import { getAdministrativeSessionToken } from '@/services/auth';
import {
  activatePlatformRootDirectAccess,
  isAdministrativeStepUpError,
} from '@/services/support-access';
import { useOptionalAppStore } from '@/store/app-context';
import { SupportStepUpPanel } from '@/support-mode/SupportStepUpPanel';
import {
  executePlatformQuickCommand,
  fetchPlatformCommandCatalog,
  searchPlatform,
  type PlatformCommandDefinition,
  type PlatformSearchEntityType,
  type PlatformSearchResult,
} from '@/services/platform-search';

const TYPE_LABELS: Record<PlatformSearchEntityType, string> = {
  accounts: 'Contas', users: 'Usuários', staff: 'Funcionários', tickets: 'Tickets',
  leads: 'Leads', conversations: 'Conversas', incidents: 'Incidentes', errors: 'Erros',
  support_access: 'Acessos assistidos', releases: 'Releases', subscriptions: 'Assinaturas',
  workspace_invitations: 'Convites de conta', staff_invitations: 'Convites de funcionário',
};

interface PendingCommand {
  command: PlatformCommandDefinition;
  item: PlatformSearchResult;
}

export function BlackboxCommandCenter() {
  const navigate = useNavigate();
  const { manifest } = useBlackboxManifest();
  const appStore = useOptionalAppStore();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [pending, setPending] = useState<PendingCommand | null>(null);
  const [reason, setReason] = useState('');
  const [hasActiveAdministrativeSession, setHasActiveAdministrativeSession] = useState(
    () => Boolean(getAdministrativeSessionToken()?.trim()),
  );
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timeout);
  }, [query]);
  useEffect(() => {
    if (open) window.setTimeout(() => inputRef.current?.focus(), 0);
    else {
      setQuery('');
      setDebouncedQuery('');
      setPending(null);
      setReason('');
      setHasActiveAdministrativeSession(Boolean(getAdministrativeSessionToken()?.trim()));
    }
  }, [open]);

  const catalogQuery = useQuery({
    queryKey: ['blackbox', 'command-catalog', manifest.userId],
    queryFn: fetchPlatformCommandCatalog,
    enabled: open,
    staleTime: 30_000,
    retry: false,
  });
  const searchQuery = useQuery({
    queryKey: ['blackbox', 'global-search', debouncedQuery],
    queryFn: () => searchPlatform(debouncedQuery),
    enabled: open && debouncedQuery.length >= 2,
    retry: false,
    staleTime: 0,
  });
  const commandById = useMemo(
    () => new Map((catalogQuery.data?.commands ?? []).map((command) => [command.id, command])),
    [catalogQuery.data],
  );
  const globalCommands = useMemo(
    () => (catalogQuery.data?.commands ?? []).filter((command) => command.scope === 'global'),
    [catalogQuery.data],
  );
  const resultCount = useMemo(
    () => searchQuery.data?.groups.reduce((total, group) => total + group.items.length, 0) ?? 0,
    [searchQuery.data],
  );

  const quickMutation = useMutation({
    mutationFn: async (selection: PendingCommand) => {
      if (selection.command.id === 'account.root_direct') {
        if (!appStore) throw new Error('O contexto seguro do Blackbox não está disponível.');
        const activation = await activatePlatformRootDirectAccess(selection.item.id, reason);
        await appStore.enterSupportMode(activation, {
          id: selection.item.id,
          name: selection.item.label,
          slug: selection.item.publicIdentifier,
        });
        return activation;
      }
      return executePlatformQuickCommand({
        commandId: selection.command.id,
        item: selection.item,
        actorUserId: manifest.userId,
        reason,
      });
    },
    onSuccess: (_result, selection) => {
      toast({ title: `${selection.command.label} concluído.` });
      setPending(null);
      setReason('');
      if (selection.command.id === 'account.root_direct') {
        setOpen(false);
        navigate('/blackbox/support-session', { replace: true });
        return;
      }
      void searchQuery.refetch();
    },
    onError: (error: Error) => {
      if (isAdministrativeStepUpError(error)) {
        setHasActiveAdministrativeSession(false);
      }
      toast({
        variant: 'destructive',
        title: 'Não foi possível executar o comando',
        description: error.message,
      });
    },
  });

  const go = (href: string) => {
    setOpen(false);
    navigate(href);
  };
  const chooseCommand = (command: PlatformCommandDefinition, item: PlatformSearchResult) => {
    if (command.kind === 'navigation') {
      go(item.href);
      return;
    }
    setPending({ command, item });
    setReason('');
    if (command.id === 'account.root_direct') {
      setHasActiveAdministrativeSession(Boolean(getAdministrativeSessionToken()?.trim()));
    }
  };

  return (
    <>
      <Button type="button" variant="outline" size="sm" className="hidden min-w-52 justify-between text-muted-foreground md:flex" onClick={() => setOpen(true)} aria-label="Abrir busca global">
        <span className="flex items-center gap-2"><Search className="h-4 w-4" />Buscar em toda a plataforma</span><kbd className="rounded border bg-muted px-1.5 py-0.5 text-[10px]">⌘K</kbd>
      </Button>
      <Button type="button" variant="ghost" size="icon" className="md:hidden" onClick={() => setOpen(true)} aria-label="Abrir busca global"><Search className="h-4 w-4" /></Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b p-5 pb-4"><DialogTitle>Busca global e comandos</DialogTitle><DialogDescription>Resultados e ações rápidas são filtrados pelo backend para esta sessão.</DialogDescription></DialogHeader>
          <div className="border-b p-4"><Input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Conta, usuário, ticket, lead, conversa, incidente…" aria-label="Pesquisar no Blackbox" autoComplete="off" /></div>
          <ScrollArea className="max-h-[60vh] min-h-64">
            <div className="space-y-5 p-4" aria-live="polite">
              {debouncedQuery.length < 2 ? (
                <div className="space-y-3">
                  <p className="text-sm font-medium">Ações autorizadas</p>
                  {catalogQuery.isLoading ? <p role="status" className="text-sm text-muted-foreground">Carregando catálogo de comandos…</p> : null}
                  {catalogQuery.isError ? <p className="text-sm text-destructive">O catálogo está indisponível; nenhum atalho foi presumido.</p> : null}
                  <div className="grid gap-2 sm:grid-cols-2">
                    {globalCommands.map((command) => command.href ? (
                      <Button key={command.id} variant="outline" className="h-auto justify-start py-2 text-left" onClick={() => go(command.href!)}>
                        <span><span className="block text-sm">{command.label}</span><span className="block text-xs font-normal text-muted-foreground">{command.description}</span></span>
                      </Button>
                    ) : null)}
                  </div>
                  <p className="text-xs text-muted-foreground">Digite ao menos dois caracteres. O texto pesquisado não é persistido pela telemetria.</p>
                </div>
              ) : null}
              {searchQuery.isFetching ? <p role="status" className="text-sm text-muted-foreground">Pesquisando fontes autorizadas…</p> : null}
              {searchQuery.isError ? <div className="flex gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" /><span>A busca está indisponível. Nenhum resultado vazio foi presumido.</span></div> : null}
              {searchQuery.data?.groups.map((group) => (
                <section key={group.entityType} aria-labelledby={`search-${group.entityType}`}>
                  <div className="mb-2 flex items-center justify-between"><h2 id={`search-${group.entityType}`} className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{TYPE_LABELS[group.entityType]}</h2>{group.state === 'unavailable' ? <Badge variant="destructive">Indisponível</Badge> : <Badge variant="outline">{group.items.length}</Badge>}</div>
                  {group.items.length ? <div className="space-y-1">{group.items.map((item) => (
                    <div key={`${item.entityType}:${item.id}`} className="flex items-start gap-2 rounded-md px-2 py-1 hover:bg-muted">
                      <button type="button" className="min-w-0 flex-1 px-1 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => go(item.href)}>
                        <span className="block truncate text-sm font-medium">{item.label}</span><span className="block truncate text-xs text-muted-foreground">{[item.publicIdentifier, item.secondaryLabel, item.workspaceLabel].filter(Boolean).join(' · ')}</span>
                      </button>
                      <span className="flex shrink-0 items-center gap-1 py-1">{item.status ? <Badge variant="secondary">{item.status}</Badge> : null}{item.badges.slice(0, 1).map((badge) => <Badge key={badge} variant="outline">{badge}</Badge>)}{item.allowedCommands.filter((id) => !id.endsWith('.record.open')).map((id) => { const command = commandById.get(id); return command ? <Button key={id} type="button" variant="outline" size="sm" className="h-7 gap-1" onClick={() => chooseCommand(command, item)}><Zap className="h-3 w-3" />{command.label}</Button> : null; })}</span>
                    </div>
                  ))}</div> : group.state === 'ready' ? <p className="px-3 py-2 text-xs text-muted-foreground">Nenhum resultado neste grupo.</p> : null}
                </section>
              ))}
              {searchQuery.data && resultCount === 0 && searchQuery.data.groups.every((group) => group.state === 'ready') ? <p className="py-8 text-center text-sm text-muted-foreground">A consulta foi concluída sem resultados autorizados.</p> : null}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>
      <AlertDialog open={pending !== null} onOpenChange={(value) => { if (!value && !quickMutation.isPending) setPending(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>{pending?.command.label}</AlertDialogTitle><AlertDialogDescription>{pending ? `${pending.command.description} Registro: ${pending.item.label}. A rota de domínio revalidará sua permissão antes da alteração.` : ''}</AlertDialogDescription></AlertDialogHeader>
          {pending?.command.id === 'account.root_direct' ? (
            <div className="rounded-lg border border-primary/25 bg-primary/5 p-4">
              <SupportStepUpPanel
                onAdministrativeSessionChange={setHasActiveAdministrativeSession}
              />
              <p className="mt-3 text-xs text-muted-foreground">
                O Super Admin não depende de aprovação do cliente, mas o acesso direto exige MFA recente e permanece atribuído à identidade root.
              </p>
            </div>
          ) : null}
          {pending?.command.reasonRequired ? <Input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Motivo da ação" aria-label="Motivo do comando" /> : null}
          <AlertDialogFooter><AlertDialogCancel disabled={quickMutation.isPending}>Cancelar</AlertDialogCancel><AlertDialogAction disabled={quickMutation.isPending || Boolean(pending?.command.reasonRequired && reason.trim().length < 8) || Boolean(pending?.command.id === 'account.root_direct' && !hasActiveAdministrativeSession)} onClick={(event) => { event.preventDefault(); if (pending) quickMutation.mutate(pending); }}>{quickMutation.isPending ? 'Executando…' : 'Confirmar ação'}</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
