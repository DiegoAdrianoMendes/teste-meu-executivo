import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  History,
  Loader2,
  LockKeyhole,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  approveWorkspaceSupportAccess,
  isSupportScopeSelectionValid,
  pageWorkspaceSupportAccess,
  pageWorkspaceSupportAccessEvents,
  rejectWorkspaceSupportAccess,
  revokeWorkspaceSupportAccess,
  SUPPORT_ACCESS_SCOPE_OPTIONS,
  type SupportAccessApprovalInput,
  type SupportAccessEvent,
  type SupportAccessGrant,
  type SupportAccessScope,
  type SupportAccessState,
} from '@/services/support-access';
import { useOptionalAppStore } from '@/store/app-context';
import { useSupportAccessDeadline } from '@/support-mode/use-support-deadline';

const stateLabels: Record<SupportAccessState, string> = {
  requested: 'Pendente',
  approved: 'Aprovado',
  active: 'Ativo',
  expired: 'Expirado',
  revoked: 'Revogado',
  rejected: 'Rejeitado',
};

const eventLabels: Record<string, string> = {
  support_access_requested: 'Acesso solicitado',
  support_access_approved: 'Acesso aprovado',
  support_access_rejected: 'Solicitação rejeitada',
  support_access_activated: 'Acesso ativado com MFA',
  support_access_revoked: 'Acesso revogado',
  support_access_expired: 'Acesso expirado',
  support_access_used: 'Acesso utilizado',
  support_access_denied: 'Uso bloqueado',
};

function formatDate(value?: string | null): string {
  if (!value) return 'Não informado';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'A operação de suporte não foi concluída.';
}

function statusVariant(state: SupportAccessState): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (state === 'active') return 'default';
  if (state === 'approved' || state === 'requested') return 'secondary';
  if (state === 'rejected' || state === 'revoked') return 'destructive';
  return 'outline';
}

function scopeLabel(scope: SupportAccessScope): string {
  return SUPPORT_ACCESS_SCOPE_OPTIONS.find((option) => option.value === scope)?.label ?? scope;
}

function remainingLabel(expiresAt: string | null | undefined, now: number): string | null {
  if (!expiresAt) return null;
  const remainingSeconds = Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 1000));
  if (remainingSeconds === 0) return 'expirando';
  if (remainingSeconds < 60) return `${remainingSeconds}s restantes`;
  return `${Math.ceil(remainingSeconds / 60)} min restantes`;
}

function eventDetail(event: SupportAccessEvent): string | null {
  const scopes = Array.isArray(event.details.scopes)
    ? (event.details.scopes as SupportAccessScope[]).map(scopeLabel).join(', ')
    : null;
  const duration = event.details.duration_minutes;
  const requiredScope = event.details.required_scope;
  if (scopes && typeof duration === 'number') return `${scopes} · ${duration} min`;
  if (scopes) return scopes;
  if (typeof requiredScope === 'string') return scopeLabel(requiredScope as SupportAccessScope);
  return null;
}

function GrantAuditTrail({
  grantId,
  workspaceId,
  active,
}: {
  grantId: string;
  workspaceId: string;
  active: boolean;
}) {
  const [eventPage, setEventPage] = useState(0);
  const eventPageSize = 25;
  const eventsQuery = useQuery({
    queryKey: ['support-access', 'workspace', workspaceId, 'events', grantId, eventPage],
    queryFn: () => pageWorkspaceSupportAccessEvents(grantId, {
      limit: eventPageSize,
      offset: eventPage * eventPageSize,
    }),
    retry: false,
    refetchInterval: active ? 5_000 : false,
  });

  if (eventsQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 border-t px-3 py-3 text-xs text-muted-foreground" role="status">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />Carregando trilha...
      </div>
    );
  }

  if (eventsQuery.isError) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 border-t px-3 py-3 text-xs text-destructive" role="alert">
        <span>{errorMessage(eventsQuery.error)}</span>
        <Button type="button" size="sm" variant="outline" onClick={() => eventsQuery.refetch()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente
        </Button>
      </div>
    );
  }

  const events = eventsQuery.data?.items ?? [];
  return (
    <div className="border-t bg-muted/20 px-3 py-3">
      {events.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nenhum evento registrado.</p>
      ) : (
        <ol className="space-y-3" aria-label="Trilha de auditoria do acesso">
          {events.map((event) => (
            <li key={event.id} className="grid grid-cols-[auto_1fr] gap-2 text-xs">
              <span className="mt-1 h-2 w-2 rounded-full bg-primary" aria-hidden="true" />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{eventLabels[event.eventType] ?? event.eventType.replaceAll('_', ' ')}</span>
                  <time className="text-muted-foreground" dateTime={event.occurredAt}>{formatDate(event.occurredAt)}</time>
                </div>
                {(event.fromState || event.toState) && (
                  <p className="mt-0.5 text-muted-foreground">
                    {event.fromState ? (stateLabels[event.fromState as SupportAccessState] ?? event.fromState) : 'Início'}
                    {' → '}
                    {event.toState ? (stateLabels[event.toState as SupportAccessState] ?? event.toState) : 'Sem mudança'}
                  </p>
                )}
                {eventDetail(event) && <p className="mt-0.5 break-words text-muted-foreground">{eventDetail(event)}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
      {eventsQuery.data && eventsQuery.data.total > 0 && (
        <div className="mt-3 flex items-center justify-between gap-3 border-t pt-3">
          <p className="text-xs text-muted-foreground">
            {eventsQuery.data.offset + 1}–{eventsQuery.data.offset + events.length} de {eventsQuery.data.total}
          </p>
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="outline" disabled={eventPage === 0 || eventsQuery.isFetching} onClick={() => setEventPage((current) => Math.max(0, current - 1))}>Anterior</Button>
            <Button type="button" size="sm" variant="outline" disabled={!eventsQuery.data.hasMore || eventsQuery.isFetching} onClick={() => setEventPage((current) => current + 1)}>Próxima</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function ApprovalDialog({
  grant,
  approverUserId,
  pending,
  disabled,
  onApprove,
}: {
  grant: SupportAccessGrant;
  approverUserId: string | null;
  pending: boolean;
  disabled?: boolean;
  onApprove: (payload: SupportAccessApprovalInput) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [scopes, setScopes] = useState<SupportAccessScope[]>(grant.requestedScopes);
  const [duration, setDuration] = useState(grant.requestedDurationMinutes);

  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (next) {
      setScopes(grant.requestedScopes);
      setDuration(grant.requestedDurationMinutes);
    }
  };

  const valid = isSupportScopeSelectionValid(scopes)
    && Number.isInteger(duration)
    && duration >= 1
    && duration <= grant.requestedDurationMinutes;
  const approverIsValid = Boolean(approverUserId) && approverUserId !== grant.requesterUserId;

  const submit = async () => {
    if (!valid || !approverIsValid) return;
    try {
      await onApprove({ approvedScopes: scopes, durationMinutes: duration });
      setOpen(false);
    } catch {
      // The mutation toast keeps the dialog open for correction or retry.
    }
  };

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild>
        <Button type="button" size="sm" disabled={disabled}>
          <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />Revisar e aprovar
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Aprovar acesso temporário</DialogTitle>
          <DialogDescription>
            Ticket {grant.ticketReference}. A aprovação aceita ou reduz o pedido original.
          </DialogDescription>
        </DialogHeader>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">Escopos aprovados</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {grant.requestedScopes.map((scope) => {
              const inputId = `support-approve-${grant.id}-${scope}`;
              const option = SUPPORT_ACCESS_SCOPE_OPTIONS.find((item) => item.value === scope);
              return (
                <label key={scope} htmlFor={inputId} className="flex min-w-0 items-center gap-2 rounded-md border px-3 py-2 text-sm">
                  <Checkbox
                    id={inputId}
                    checked={scopes.includes(scope)}
                    onCheckedChange={(checked) => setScopes((current) => (
                      checked ? [...new Set([...current, scope])] : current.filter((item) => item !== scope)
                    ))}
                  />
                  <span className="min-w-0 flex-1">{scopeLabel(scope)}</span>
                  {option?.mode === 'write' && <Badge variant="outline">alteração</Badge>}
                </label>
              );
            })}
          </div>
          {scopes.length === 0 && <p className="text-xs text-destructive">Selecione ao menos um escopo.</p>}
          {scopes.includes('lead_data:read') && !isSupportScopeSelectionValid(scopes) && (
            <p className="text-xs text-destructive">Dados de leads exigem ao menos um escopo primário.</p>
          )}
        </fieldset>

        <div className="space-y-1.5">
          <Label htmlFor={`support-approve-duration-${grant.id}`}>Duração aprovada em minutos</Label>
          <Input
            id={`support-approve-duration-${grant.id}`}
            type="number"
            min={1}
            max={grant.requestedDurationMinutes}
            value={duration}
            onChange={(event) => setDuration(Number(event.target.value))}
          />
          <p className="text-xs text-muted-foreground">Máximo solicitado: {grant.requestedDurationMinutes} min.</p>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancelar</Button>
          <Button type="button" onClick={submit} disabled={!valid || !approverIsValid || pending}>
            {pending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
            Aprovar acesso
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DecisionDialog({
  grant,
  kind,
  pending,
  onConfirm,
}: {
  grant: SupportAccessGrant;
  kind: 'reject' | 'revoke';
  pending: boolean;
  onConfirm: (reason: string) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const isReject = kind === 'reject';

  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (next) setReason('');
  };

  const submit = async () => {
    if (reason.trim().length < 3) return;
    try {
      await onConfirm(reason.trim());
      setOpen(false);
    } catch {
      // The mutation toast keeps the confirmation available for retry.
    }
  };

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild>
        <Button type="button" size="sm" variant="destructive">
          {isReject
            ? <ShieldX className="mr-1.5 h-3.5 w-3.5" />
            : <ShieldAlert className="mr-1.5 h-3.5 w-3.5" />}
          {isReject ? 'Rejeitar' : 'Revogar'}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Confirmar {isReject ? 'rejeição' : 'revogação'}?</DialogTitle>
          <DialogDescription>
            Ticket {grant.ticketReference}. A decisão será registrada na trilha imutável.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor={`support-${kind}-reason-${grant.id}`}>Motivo</Label>
          <Textarea
            id={`support-${kind}-reason-${grant.id}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={4}
            maxLength={500}
            autoFocus
          />
          <p className="text-xs text-muted-foreground">Mínimo de 3 caracteres.</p>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>Voltar</Button>
          <Button
            type="button"
            variant="destructive"
            onClick={submit}
            disabled={reason.trim().length < 3 || pending}
          >
            {pending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
            Confirmar {isReject ? 'rejeição' : 'revogação'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function SupportAccessPanel({
  canManage = true,
  activeWorkspaceId,
  currentUserId,
}: {
  canManage?: boolean;
  activeWorkspaceId?: string | null;
  currentUserId?: string | null;
}) {
  const queryClient = useQueryClient();
  const appStore = useOptionalAppStore();
  const { toast } = useToast();
  const [stateFilter, setStateFilter] = useState<'all' | SupportAccessState>('all');
  const [requestPage, setRequestPage] = useState(0);
  const requestPageSize = 25;
  const [expandedGrantId, setExpandedGrantId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const workspaceId = activeWorkspaceId ?? appStore?.state.activeWorkspace?.id ?? null;
  const approverUserId = currentUserId ?? appStore?.state.currentUser?.id ?? null;
  const previousWorkspaceIdRef = useRef<string | null>(workspaceId);

  const requestsQuery = useQuery({
    queryKey: ['support-access', 'workspace', workspaceId, 'requests', stateFilter, requestPage],
    queryFn: () => pageWorkspaceSupportAccess({
      state: stateFilter === 'all' ? undefined : stateFilter,
      limit: requestPageSize,
      offset: requestPage * requestPageSize,
    }),
    enabled: canManage && Boolean(workspaceId),
    retry: false,
    refetchInterval: (query) => (
      query.state.data?.items.some((grant: SupportAccessGrant) => grant.state === 'active')
        ? 10_000
        : false
    ),
  });

  useEffect(() => {
    const previousWorkspaceId = previousWorkspaceIdRef.current;
    previousWorkspaceIdRef.current = workspaceId;
    if (!previousWorkspaceId || previousWorkspaceId === workspaceId) return;
    const previousQueryKey = ['support-access', 'workspace', previousWorkspaceId] as const;
    void queryClient.cancelQueries({ queryKey: previousQueryKey });
    queryClient.removeQueries({ queryKey: previousQueryKey });
    setExpandedGrantId(null);
    setRequestPage(0);
  }, [queryClient, workspaceId]);

  useEffect(() => {
    setRequestPage(0);
    setExpandedGrantId(null);
  }, [stateFilter]);

  const grants = useMemo(
    () => requestsQuery.data?.items ?? [],
    [requestsQuery.data?.items],
  );
  const deadlineQueryKey = useMemo(
    () => ['support-access', 'workspace', workspaceId, 'requests'] as const,
    [workspaceId],
  );
  useSupportAccessDeadline(queryClient, deadlineQueryKey, grants);

  useEffect(() => {
    if (!grants.some((grant) => grant.state === 'active')) return undefined;
    const interval = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(interval);
  }, [grants]);

  const refreshRequests = async () => {
    await queryClient.invalidateQueries({ queryKey: ['support-access', 'workspace', workspaceId] });
  };

  const approveMutation = useMutation({
    mutationKey: ['support-access', 'workspace', workspaceId, 'approve'],
    mutationFn: ({
      grantId,
      requesterUserId,
      payload,
    }: {
      grantId: string;
      requesterUserId: string;
      payload: SupportAccessApprovalInput;
    }) => {
      if (!approverUserId || requesterUserId === approverUserId) {
        throw new Error('A aprovação exige uma identidade distinta do solicitante.');
      }
      return approveWorkspaceSupportAccess(grantId, payload);
    },
    onSuccess: async () => {
      await refreshRequests();
      toast({ title: 'Acesso de suporte aprovado.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Aprovação não concluída',
      description: errorMessage(error),
    }),
  });

  const rejectMutation = useMutation({
    mutationKey: ['support-access', 'workspace', workspaceId, 'reject'],
    mutationFn: ({ grantId, reason }: { grantId: string; reason: string }) =>
      rejectWorkspaceSupportAccess(grantId, { reason }),
    onSuccess: async () => {
      await refreshRequests();
      toast({ title: 'Solicitação rejeitada.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Rejeição não concluída',
      description: errorMessage(error),
    }),
  });

  const revokeMutation = useMutation({
    mutationKey: ['support-access', 'workspace', workspaceId, 'revoke'],
    mutationFn: ({ grantId, reason }: { grantId: string; reason: string }) =>
      revokeWorkspaceSupportAccess(grantId, { reason }),
    onSuccess: async () => {
      await refreshRequests();
      toast({ title: 'Acesso de suporte revogado.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Revogação não concluída',
      description: errorMessage(error),
    }),
  });

  if (!canManage) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <LockKeyhole className="h-4 w-4 text-muted-foreground" />Acesso temporário de suporte
          </CardTitle>
          <CardDescription>Área restrita a owners e admins ativos do workspace.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground" role="status">
            Sem permissão para consultar ou decidir acessos de suporte.
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldAlert className="h-4 w-4 text-primary" />Acesso temporário de suporte
            </CardTitle>
            <CardDescription>Consentimento, prazo e trilha do break-glass local.</CardDescription>
          </div>
          <Badge variant="outline">somente local</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div className="w-full space-y-1.5 sm:w-52">
            <Label htmlFor="support-workspace-state-filter">Estado</Label>
            <Select value={stateFilter} onValueChange={(value) => setStateFilter(value as 'all' | SupportAccessState)}>
              <SelectTrigger id="support-workspace-state-filter" aria-label="Filtrar acessos por estado">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos</SelectItem>
                {(Object.keys(stateLabels) as SupportAccessState[]).map((state) => (
                  <SelectItem key={state} value={state}>{stateLabels[state]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            type="button"
            size="icon"
            variant="outline"
            aria-label="Atualizar acessos de suporte"
            title="Atualizar acessos de suporte"
            onClick={() => requestsQuery.refetch()}
            disabled={requestsQuery.isFetching}
          >
            <RefreshCw className={`h-4 w-4 ${requestsQuery.isFetching ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {requestsQuery.isLoading && (
          <div className="flex items-center justify-center gap-2 rounded-md border border-dashed px-4 py-8 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando solicitações...
          </div>
        )}

        {requestsQuery.isError && (
          <div className="flex flex-col gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-4 py-4 text-sm sm:flex-row sm:items-center sm:justify-between" role="alert">
            <div>
              <p className="font-medium text-destructive">Não foi possível carregar os acessos.</p>
              <p className="mt-1 text-xs text-muted-foreground">{errorMessage(requestsQuery.error)}</p>
            </div>
            <Button type="button" size="sm" variant="outline" onClick={() => requestsQuery.refetch()}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente
            </Button>
          </div>
        )}

        {!requestsQuery.isLoading && !requestsQuery.isError && grants.length === 0 && (
          <div className="rounded-md border border-dashed px-4 py-8 text-center" role="status">
            <CheckCircle2 className="mx-auto h-5 w-5 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium">Nenhuma solicitação neste estado.</p>
            <p className="mt-1 text-xs text-muted-foreground">A fila do workspace está vazia.</p>
          </div>
        )}

        {grants.length > 0 && (
          <div className="divide-y overflow-hidden rounded-md border">
            {grants.map((grant) => {
              const isExpanded = expandedGrantId === grant.id;
              const effectiveScopes = grant.approvedScopes ?? grant.requestedScopes;
              const remaining = grant.state === 'active' ? remainingLabel(grant.expiresAt, now) : null;
              const approverIsMissing = !approverUserId;
              const isSelfApproval = grant.requesterUserId === approverUserId;
              const approvalBlocked = approverIsMissing || isSelfApproval;
              return (
                <article key={grant.id} aria-labelledby={`support-ticket-${grant.id}`}>
                  <div className="space-y-3 px-3 py-4 sm:px-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 id={`support-ticket-${grant.id}`} className="break-words text-sm font-semibold">
                            Ticket {grant.ticketReference}
                          </h3>
                          <Badge variant={statusVariant(grant.state)}>{stateLabels[grant.state]}</Badge>
                          {remaining && <Badge variant="outline" className="gap-1"><Clock3 className="h-3 w-3" />{remaining}</Badge>}
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">Solicitado em {formatDate(grant.requestedAt)}</p>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {grant.state === 'requested' && (
                          <>
                            <ApprovalDialog
                              grant={grant}
                              approverUserId={approverUserId}
                              pending={approveMutation.isPending}
                              disabled={approvalBlocked}
                              onApprove={(payload) => approveMutation.mutateAsync({
                                grantId: grant.id,
                                requesterUserId: grant.requesterUserId,
                                payload,
                              })}
                            />
                            <DecisionDialog
                              grant={grant}
                              kind="reject"
                              pending={rejectMutation.isPending}
                              onConfirm={(reason) => rejectMutation.mutateAsync({ grantId: grant.id, reason })}
                            />
                          </>
                        )}
                        {(grant.state === 'approved' || grant.state === 'active') && (
                          <DecisionDialog
                            grant={grant}
                            kind="revoke"
                            pending={revokeMutation.isPending}
                            onConfirm={(reason) => revokeMutation.mutateAsync({ grantId: grant.id, reason })}
                          />
                        )}
                      </div>
                    </div>

                    {grant.state === 'requested' && isSelfApproval && (
                      <p className="text-xs text-destructive">
                        O solicitante não pode aprovar o próprio acesso de suporte.
                      </p>
                    )}
                    {grant.state === 'requested' && approverIsMissing && (
                      <p className="text-xs text-destructive">
                        A identidade do aprovador está indisponível. A aprovação permanece bloqueada.
                      </p>
                    )}

                    <p className="break-words text-sm">{grant.reason}</p>

                    <div className="flex flex-wrap gap-1.5" aria-label={grant.approvedScopes ? 'Escopos aprovados' : 'Escopos solicitados'}>
                      {effectiveScopes.map((scope) => (
                        <Badge key={scope} variant="outline" className="whitespace-normal text-left font-normal">
                          {scopeLabel(scope)}
                        </Badge>
                      ))}
                    </div>

                    <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
                      <span>
                        {grant.approvedDurationMinutes
                          ? `${grant.approvedDurationMinutes} min aprovados de ${grant.requestedDurationMinutes} min solicitados`
                          : `${grant.requestedDurationMinutes} min solicitados`}
                        {grant.expiresAt ? ` · expira em ${formatDate(grant.expiresAt)}` : ''}
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-expanded={isExpanded}
                        aria-controls={`support-trail-${grant.id}`}
                        onClick={() => setExpandedGrantId(isExpanded ? null : grant.id)}
                      >
                        <History className="mr-1.5 h-3.5 w-3.5" />
                        {isExpanded ? 'Ocultar trilha' : 'Ver trilha'}
                        {isExpanded ? <ChevronUp className="ml-1 h-3.5 w-3.5" /> : <ChevronDown className="ml-1 h-3.5 w-3.5" />}
                      </Button>
                    </div>

                    {(grant.rejectionReason || grant.revocationReason) && (
                      <p className="rounded-md bg-muted/40 px-3 py-2 text-xs">
                        <span className="font-medium">Motivo:</span> {grant.rejectionReason ?? grant.revocationReason}
                      </p>
                    )}
                  </div>
                  {isExpanded && (
                    <div id={`support-trail-${grant.id}`}>
                      <GrantAuditTrail
                        grantId={grant.id}
                        workspaceId={grant.workspaceId}
                        active={grant.state === 'active'}
                      />
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}

        {requestsQuery.data && requestsQuery.data.total > 0 && (
          <div className="flex items-center justify-between gap-3 border-t pt-3">
            <p className="text-xs text-muted-foreground">
              {requestsQuery.data.offset + 1}–{requestsQuery.data.offset + grants.length} de {requestsQuery.data.total}
            </p>
            <div className="flex gap-2">
              <Button type="button" size="sm" variant="outline" disabled={requestPage === 0 || requestsQuery.isFetching} onClick={() => setRequestPage((current) => Math.max(0, current - 1))}>Anterior</Button>
              <Button type="button" size="sm" variant="outline" disabled={!requestsQuery.data.hasMore || requestsQuery.isFetching} onClick={() => setRequestPage((current) => current + 1)}>Próxima</Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
