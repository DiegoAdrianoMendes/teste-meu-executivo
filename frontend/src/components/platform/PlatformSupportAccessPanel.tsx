import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  History,
  KeyRound,
  LogIn,
  Loader2,
  RefreshCw,
  Send,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { useToast } from '@/hooks/use-toast';
import {
  activatePlatformSupportAccess,
  activatePlatformRootDirectAccess,
  isAdministrativeStepUpError,
  isSupportScopeSelectionValid,
  pagePlatformSupportAccess,
  pagePlatformSupportAccessEvents,
  requestPlatformSupportAccess,
  SUPPORT_ACCESS_MAX_SCOPES,
  SUPPORT_ACCESS_REQUEST_MAX_DURATION_MINUTES,
  SUPPORT_ACCESS_REQUEST_MIN_DURATION_MINUTES,
  SUPPORT_ACCESS_SCOPE_OPTIONS,
  type SupportAccessActivation,
  type SupportAccessEvent,
  type SupportAccessGrant,
  type SupportAccessScope,
  type SupportAccessState,
} from '@/services/support-access';
import { discardStagedSupportMode } from '@/support-mode/session';
import { SupportStepUpPanel, SUPPORT_STEP_UP_QUERY_KEY } from '@/support-mode/SupportStepUpPanel';
import { useSupportAccessDeadline } from '@/support-mode/use-support-deadline';
import type { PlatformSearchResult } from '@/services/platform-search';

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
  support_access_root_direct: 'Acesso direto do Super Admin',
};

export interface SupportAccessWorkspaceOption {
  id: string;
  name: string;
  slug?: string | null;
}

export interface PlatformSupportAccessPanelProps {
  workspaces?: SupportAccessWorkspaceOption[];
  workspacesLoading?: boolean;
  workspacesError?: unknown;
  currentUserId?: string | null;
  isPlatformRoot?: boolean;
  canManage?: boolean;
  onRetryWorkspaces?: () => void;
  onEnterWorkspace?: (
    activation: SupportAccessActivation,
    workspace: SupportAccessWorkspaceOption,
  ) => Promise<void>;
}

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
  if (state === 'requested' || state === 'approved') return 'secondary';
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

function PlatformGrantAuditTrail({
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
    queryKey: ['support-access', 'platform', workspaceId, 'events', grantId, eventPage],
    queryFn: () => pagePlatformSupportAccessEvents(grantId, {
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

export function PlatformSupportAccessPanel({
  workspaces = [],
  workspacesLoading = false,
  workspacesError,
  currentUserId,
  isPlatformRoot = false,
  canManage = true,
  onRetryWorkspaces,
  onEnterWorkspace,
}: PlatformSupportAccessPanelProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [workspaceId, setWorkspaceId] = useState('');
  const [workspaceLookup, setWorkspaceLookup] = useState<PlatformSearchResult | null>(null);
  const [ticketReference, setTicketReference] = useState('');
  const [reason, setReason] = useState('');
  const [requestedScopes, setRequestedScopes] = useState<SupportAccessScope[]>(['workspace:read']);
  const [durationMinutes, setDurationMinutes] = useState(15);
  const [stateFilter, setStateFilter] = useState<'all' | SupportAccessState>('all');
  const [requestPage, setRequestPage] = useState(0);
  const requestPageSize = 25;
  const [expandedGrantId, setExpandedGrantId] = useState<string | null>(null);
  const [preparedActivation, setPreparedActivation] = useState<SupportAccessActivation | null>(null);
  const [hasActiveAdminSession, setHasActiveAdminSession] = useState(false);
  const [activatingGrantId, setActivatingGrantId] = useState<string | null>(null);
  const [enteringWorkspace, setEnteringWorkspace] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const previousWorkspaceIdRef = useRef(workspaceId);

  useEffect(() => {
    if (!workspaceId && workspaces.length > 0) setWorkspaceId(workspaces[0].id);
  }, [workspaceId, workspaces]);

  useEffect(() => () => discardStagedSupportMode(), []);

  useEffect(() => {
    const previousWorkspaceId = previousWorkspaceIdRef.current;
    previousWorkspaceIdRef.current = workspaceId;
    if (!previousWorkspaceId || previousWorkspaceId === workspaceId) return;
    const previousQueryKey = ['support-access', 'platform', previousWorkspaceId] as const;
    void queryClient.cancelQueries({ queryKey: previousQueryKey });
    queryClient.removeQueries({ queryKey: previousQueryKey });
    const previousMutationKey = ['support-access', 'platform', previousWorkspaceId] as const;
    queryClient.getMutationCache()
      .findAll({ mutationKey: previousMutationKey })
      .forEach((mutation) => queryClient.getMutationCache().remove(mutation));
  }, [queryClient, workspaceId]);

  useEffect(() => {
    setRequestPage(0);
  }, [stateFilter, workspaceId]);

  useEffect(() => {
    const expiresAt = preparedActivation?.grant.expiresAt;
    if (!expiresAt) return undefined;
    const remaining = new Date(expiresAt).getTime() - Date.now();
    if (remaining <= 0) {
      discardStagedSupportMode();
      setPreparedActivation(null);
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      discardStagedSupportMode();
      setPreparedActivation(null);
    }, remaining);
    return () => window.clearTimeout(timeout);
  }, [preparedActivation]);

  const requestsQuery = useQuery({
    queryKey: ['support-access', 'platform', workspaceId, 'requests', stateFilter, requestPage],
    queryFn: () => pagePlatformSupportAccess({
      workspaceId,
      state: stateFilter === 'all' ? undefined : stateFilter,
      limit: requestPageSize,
      offset: requestPage * requestPageSize,
    }),
    enabled: canManage && Boolean(workspaceId) && !workspacesLoading && !workspacesError,
    retry: false,
    refetchInterval: (query) => (
      Array.isArray(query.state.data?.items)
      && query.state.data.items.some((grant: SupportAccessGrant) => grant.state === 'active')
        ? 10_000
        : false
    ),
  });

  useEffect(() => {
    if (!(requestsQuery.data?.items ?? []).some((grant) => grant.state === 'active')) return undefined;
    const interval = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(interval);
  }, [requestsQuery.data]);

  const selectedWorkspace = useMemo(
    () => workspaceLookup?.id === workspaceId
      ? { id: workspaceLookup.id, name: workspaceLookup.label, slug: workspaceLookup.publicIdentifier }
      : workspaces.find((workspace) => workspace.id === workspaceId) ?? null,
    [workspaceId, workspaceLookup, workspaces],
  );

  const grants = requestsQuery.data?.items ?? [];
  const deadlineQueryKey = useMemo(
    () => ['support-access', 'platform', workspaceId, 'requests'] as const,
    [workspaceId],
  );
  useSupportAccessDeadline(queryClient, deadlineQueryKey, grants);

  const refreshRequests = async () => {
    await queryClient.invalidateQueries({ queryKey: ['support-access', 'platform', workspaceId] });
  };

  const requestMutation = useMutation({
    mutationKey: ['support-access', 'platform', workspaceId, 'request'],
    mutationFn: (input: Parameters<typeof requestPlatformSupportAccess>[0]) => requestPlatformSupportAccess(input),
    onSuccess: async () => {
      setTicketReference('');
      setReason('');
      setRequestedScopes(['workspace:read']);
      setDurationMinutes(15);
      await refreshRequests();
      toast({ title: 'Solicitação enviada ao workspace.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Solicitação não enviada',
      description: errorMessage(error),
    }),
  });

  const rootDirectMutation = useMutation({
    mutationKey: ['support-access', 'platform', workspaceId, 'root-direct'],
    mutationFn: () => activatePlatformRootDirectAccess(workspaceId),
    onSuccess: async (activation) => {
      setPreparedActivation(activation);
      await refreshRequests();
      toast({
        title: 'Acesso direto do Super Admin preparado.',
        description: 'A sessão está identificada e será registrada na trilha da conta.',
      });
    },
    onError: async (error) => {
      if (isAdministrativeStepUpError(error)) {
        setHasActiveAdminSession(false);
        await queryClient.invalidateQueries({ queryKey: SUPPORT_STEP_UP_QUERY_KEY });
      }
      toast({
        variant: 'destructive',
        title: 'Acesso direto não iniciado',
        description: errorMessage(error),
      });
    },
  });

  const activateGrant = async (grantId: string) => {
    setActivatingGrantId(grantId);
    try {
      const result = await activatePlatformSupportAccess(grantId);
      setPreparedActivation(result);
      await refreshRequests();
      toast({
        title: 'Acesso ativado com MFA.',
        description: 'A sessão temporária está pronta para entrar no workspace.',
      });
    } catch (error) {
      if (isAdministrativeStepUpError(error)) {
        setHasActiveAdminSession(false);
        await queryClient.invalidateQueries({ queryKey: SUPPORT_STEP_UP_QUERY_KEY });
      }
      toast({
        variant: 'destructive',
        title: 'Ativação não concluída',
        description: errorMessage(error),
      });
    } finally {
      setActivatingGrantId(null);
    }
  };

  const enterWorkspace = async () => {
    if (!preparedActivation || !selectedWorkspace || !onEnterWorkspace) return;
    setEnteringWorkspace(true);
    try {
      await onEnterWorkspace(preparedActivation, selectedWorkspace);
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Não foi possível entrar no workspace',
        description: errorMessage(error),
      });
      setEnteringWorkspace(false);
    }
  };

  if (!canManage) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4 text-muted-foreground" />Break-glass local
          </CardTitle>
          <CardDescription>Área restrita a platform admins ativos.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground" role="status">
            Sem permissão para solicitar ou ativar acessos de suporte.
          </div>
        </CardContent>
      </Card>
    );
  }

  const formValid = Boolean(workspaceId)
    && ticketReference.trim().length >= 3
    && reason.trim().length >= 15
    && isSupportScopeSelectionValid(requestedScopes)
    && Number.isInteger(durationMinutes)
    && durationMinutes >= SUPPORT_ACCESS_REQUEST_MIN_DURATION_MINUTES
    && durationMinutes <= SUPPORT_ACCESS_REQUEST_MAX_DURATION_MINUTES;

  return (
    <Card>
      <CardHeader className="pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldAlert className="h-4 w-4 text-primary" />Break-glass local
            </CardTitle>
            <CardDescription>Solicitação consentida, MFA, prazo curto e trilha imutável.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">sessão administrativa</Badge>
            <Badge variant={hasActiveAdminSession ? 'secondary' : 'destructive'}>
              {hasActiveAdminSession ? 'MFA elevado' : 'MFA necessário'}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <SupportStepUpPanel onAdministrativeSessionChange={setHasActiveAdminSession} />

        {workspacesLoading && (
          <div className="flex items-center justify-center gap-2 rounded-md border border-dashed px-4 py-8 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando workspaces...
          </div>
        )}

        {!workspacesLoading && Boolean(workspacesError) && (
          <div className="flex flex-col gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-4 py-4 text-sm sm:flex-row sm:items-center sm:justify-between" role="alert">
            <div>
              <p className="font-medium text-destructive">Não foi possível carregar os workspaces.</p>
              <p className="mt-1 text-xs text-muted-foreground">{errorMessage(workspacesError)}</p>
            </div>
            {onRetryWorkspaces && (
              <Button type="button" size="sm" variant="outline" onClick={onRetryWorkspaces}>
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente
              </Button>
            )}
          </div>
        )}

        {!workspacesLoading && !workspacesError && (
          <>
            <div className="space-y-1.5">
              <Label>Conta do acesso administrativo</Label>
              <PlatformEntityCombobox
                entityType="accounts"
                purpose="support_access_workspace"
                value={workspaceId || null}
                onChange={(value, item) => {
                  setWorkspaceId(value ?? '');
                  setWorkspaceLookup(item);
                  setExpandedGrantId(null);
                  discardStagedSupportMode();
                  setPreparedActivation(null);
                }}
                label="Selecionar conta para acesso administrativo"
                placeholder="Pesquisar conta"
                allowClear
              />
              {!workspaceId && <p className="text-xs text-muted-foreground">Pesquise uma conta para consultar ou iniciar o acesso. Nenhum UUID precisa ser informado.</p>}
            </div>
            {isPlatformRoot && (
              <section className="mb-5 flex flex-col gap-3 rounded-lg border border-primary/25 bg-primary/5 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-semibold">Super Admin — acesso direto</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Não exige permissão nem aprovação do cliente. Exige MFA recente e a sessão permanece identificada e auditada.
                  </p>
                </div>
                <Button
                  type="button"
                  onClick={() => rootDirectMutation.mutate()}
                  disabled={!hasActiveAdminSession || !workspaceId || rootDirectMutation.isPending}
                >
                  {rootDirectMutation.isPending
                    ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    : <ShieldCheck className="mr-1.5 h-4 w-4" />}
                  {hasActiveAdminSession ? 'Entrar diretamente' : 'Confirme o MFA para entrar'}
                </Button>
              </section>
            )}
            {!isPlatformRoot && <form
              className="space-y-4 border-b pb-5"
              onSubmit={(event) => {
                event.preventDefault();
                if (!formValid) return;
                requestMutation.mutate({
                  workspaceId,
                  ticketReference: ticketReference.trim(),
                  reason: reason.trim(),
                  requestedScopes,
                  durationMinutes,
                });
              }}
            >
              <div className="grid gap-3 md:grid-cols-[1fr_0.5fr]">
                <div className="space-y-1.5">
                  <Label htmlFor="platform-support-ticket">Ticket</Label>
                  <Input
                    id="platform-support-ticket"
                    value={ticketReference}
                    onChange={(event) => setTicketReference(event.target.value)}
                    minLength={3}
                    maxLength={120}
                    placeholder="SUP-1234"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="platform-support-duration">Minutos</Label>
                  <Input
                    id="platform-support-duration"
                    type="number"
                    min={SUPPORT_ACCESS_REQUEST_MIN_DURATION_MINUTES}
                    max={SUPPORT_ACCESS_REQUEST_MAX_DURATION_MINUTES}
                    value={durationMinutes}
                    onChange={(event) => setDurationMinutes(Number(event.target.value))}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="platform-support-reason">Motivo operacional</Label>
                <Textarea
                  id="platform-support-reason"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  minLength={15}
                  maxLength={1000}
                  rows={3}
                />
                <p className="text-xs text-muted-foreground">Mínimo de 15 caracteres.</p>
              </div>

              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">Escopos solicitados</legend>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {SUPPORT_ACCESS_SCOPE_OPTIONS.map((option) => {
                    const inputId = `platform-support-scope-${option.value}`;
                    return (
                      <label key={option.value} htmlFor={inputId} className="flex min-w-0 items-center gap-2 rounded-md border px-3 py-2 text-sm">
                        <Checkbox
                          id={inputId}
                          checked={requestedScopes.includes(option.value)}
                          disabled={!requestedScopes.includes(option.value) && requestedScopes.length >= SUPPORT_ACCESS_MAX_SCOPES}
                          onCheckedChange={(checked) => setRequestedScopes((current) => (
                            checked
                              ? [...new Set([...current, option.value])]
                              : current.filter((scope) => scope !== option.value)
                          ))}
                        />
                        <span className="min-w-0 flex-1">{option.label}</span>
                        {option.mode === 'write' && <Badge variant="outline">alteração</Badge>}
                      </label>
                    );
                  })}
                </div>
                {requestedScopes.length === 0 && <p className="text-xs text-destructive">Selecione ao menos um escopo.</p>}
                {requestedScopes.includes('lead_data:read') && !isSupportScopeSelectionValid(requestedScopes) && (
                  <p className="text-xs text-destructive">Dados de leads exigem ao menos um escopo primário.</p>
                )}
                <p className="text-xs text-muted-foreground">
                  {requestedScopes.length}/{SUPPORT_ACCESS_MAX_SCOPES} escopos selecionados.
                </p>
              </fieldset>

              <div className="flex justify-end">
                <Button type="submit" disabled={!formValid || requestMutation.isPending}>
                  {requestMutation.isPending
                    ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    : <Send className="mr-1.5 h-4 w-4" />}
                  Solicitar acesso
                </Button>
              </div>
            </form>}

            {preparedActivation && (
              <section
                className="rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
                aria-labelledby="support-ready-heading"
                data-testid="support-access-ready"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 id="support-ready-heading" className="text-sm font-semibold">Acesso temporário pronto</h3>
                    <p className="mt-1 text-xs opacity-80">
                      Entre em {selectedWorkspace?.name} antes de {formatDate(preparedActivation.grant.expiresAt)}.
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    onClick={enterWorkspace}
                    disabled={!onEnterWorkspace || enteringWorkspace}
                  >
                    {enteringWorkspace
                      ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                      : <LogIn className="mr-1.5 h-3.5 w-3.5" />}
                    Entrar no workspace
                  </Button>
                </div>
              </section>
            )}

            <section className="space-y-3" aria-labelledby="platform-support-history-heading">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h3 id="platform-support-history-heading" className="text-sm font-semibold">Solicitações de {selectedWorkspace?.name}</h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {hasActiveAdminSession
                      ? 'Sessão administrativa ativa para uma ativação MFA.'
                      : 'Sessão administrativa ausente. Confirme o MFA antes da ativação.'}
                  </p>
                </div>
                <div className="flex flex-wrap items-end gap-2">
                  <div className="w-44 space-y-1.5">
                    <Label htmlFor="platform-support-state-filter">Estado</Label>
                    <Select value={stateFilter} onValueChange={(value) => setStateFilter(value as 'all' | SupportAccessState)}>
                      <SelectTrigger id="platform-support-state-filter" aria-label="Filtrar acessos por estado">
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
                    aria-label="Atualizar solicitações de suporte"
                    title="Atualizar solicitações de suporte"
                    onClick={() => { void requestsQuery.refetch(); }}
                    disabled={requestsQuery.isFetching}
                  >
                    <RefreshCw className={`h-4 w-4 ${requestsQuery.isFetching ? 'animate-spin' : ''}`} />
                  </Button>
                </div>
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
                </div>
              )}

              {grants.length > 0 && (
                <div className="divide-y overflow-hidden rounded-md border">
                  {grants.map((grant) => {
                    const isExpanded = expandedGrantId === grant.id;
                    const effectiveScopes = grant.approvedScopes ?? grant.requestedScopes;
                    const remaining = grant.state === 'active' ? remainingLabel(grant.expiresAt, now) : null;
                    const belongsToCurrentUser = Boolean(currentUserId) && grant.requesterUserId === currentUserId;
                    return (
                      <article key={grant.id} aria-labelledby={`platform-support-ticket-${grant.id}`}>
                        <div className="space-y-3 px-3 py-4 sm:px-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <h4 id={`platform-support-ticket-${grant.id}`} className="break-words text-sm font-semibold">
                                  Ticket {grant.ticketReference}
                                </h4>
                                <Badge variant={statusVariant(grant.state)}>{stateLabels[grant.state]}</Badge>
                                {remaining && <Badge variant="outline" className="gap-1"><Clock3 className="h-3 w-3" />{remaining}</Badge>}
                              </div>
                              <p className="mt-1 text-xs text-muted-foreground">Solicitado em {formatDate(grant.requestedAt)}</p>
                            </div>
                            {grant.state === 'approved' && (
                              <div className="text-right">
                                <Button
                                  type="button"
                                  size="sm"
                                  onClick={() => { void activateGrant(grant.id); }}
                                  disabled={!hasActiveAdminSession || !belongsToCurrentUser || activatingGrantId !== null}
                                >
                                  {activatingGrantId === grant.id
                                    ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                    : <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />}
                                  Ativar com MFA
                                </Button>
                                {!belongsToCurrentUser && (
                                  <p className="mt-1 text-2xs text-muted-foreground">
                                    {currentUserId ? 'Somente o solicitante pode ativar.' : 'Identidade da sessão indisponível.'}
                                  </p>
                                )}
                              </div>
                            )}
                          </div>

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
                              aria-controls={`platform-support-trail-${grant.id}`}
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
                          <div id={`platform-support-trail-${grant.id}`}>
                            <PlatformGrantAuditTrail
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
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground">{requestsQuery.data.offset + 1}–{Math.min(requestsQuery.data.offset + grants.length, requestsQuery.data.total)} de {requestsQuery.data.total}</span>
                  <span className="flex gap-2"><Button type="button" size="sm" variant="outline" disabled={requestPage === 0 || requestsQuery.isFetching} onClick={() => setRequestPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button type="button" size="sm" variant="outline" disabled={!requestsQuery.data.hasMore || requestsQuery.isFetching} onClick={() => setRequestPage((value) => value + 1)}>Próxima</Button></span>
                </div>
              )}
            </section>
          </>
        )}
      </CardContent>
    </Card>
  );
}
