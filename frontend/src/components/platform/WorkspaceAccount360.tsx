import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { WorkspaceOperationsPanel } from '@/components/platform/WorkspaceOperationsPanel';
import { WorkspaceCommercialSection } from '@/components/platform/WorkspaceCommercialSection';
import { WorkspaceAccountTasksSection } from '@/components/platform/WorkspaceAccountTasksSection';
import { UtilityTemplateLibraryPanel } from '@/components/whatsapp/UtilityTemplateLibraryPanel';
import { getAdministrativeSessionToken } from '@/services/auth';
import { isAdministrativeStepUpError } from '@/services/support-access';
import { SupportStepUpPanel } from '@/support-mode/SupportStepUpPanel';
import {
  fetchWorkspaceOverview,
  fetchPlatformPeopleAccessPackages,
  grantWorkspaceFinanceHarmonizationBonus,
  putWorkspaceEntitlementOverrides,
  revokePlatformPersonSessions,
  setPlatformPersonAccessPackage,
  setPlatformPersonBlocked,
  transferPlatformWorkspaceOwnership,
} from '@/services/platform';
import type {
  PlatformFinanceHarmonizationQuota,
  WorkspaceOverviewEntitlement,
  WorkspaceOverviewResponse,
} from '@/types';

const ENTITLEMENT_LABEL: Record<string, string> = {
  tokens: 'Tokens de IA',
  ai_executions: 'Execuções de IA',
  ai_conversations: 'Conversas de IA',
  audio_minutes: 'Minutos de áudio',
  storage_bytes: 'Armazenamento',
  files: 'Arquivos',
  users: 'Usuários',
  whatsapp_numbers: 'Números de WhatsApp',
  agents: 'Agentes de IA',
  legal_areas: 'Áreas jurídicas',
  automations: 'Automações',
  digital_signature: 'Assinatura digital',
  calendar: 'Agenda',
  email: 'E-mail',
  webhooks: 'Webhooks',
  advanced_audit: 'Auditoria avançada',
  success_manager: 'Gestor de sucesso',
};

interface EditDraft {
  entitlementKey: string;
  mode: 'grant' | 'revoke' | 'limit';
  quantitative: boolean;
  metered: boolean;
  quantity: string;
  reason: string;
}

function fmtDateTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleString('pt-BR');
}

function fmtBRL(value?: string | number | null): string {
  const numeric = typeof value === 'string' ? Number(value) : value;
  if (numeric == null || Number.isNaN(numeric)) return '—';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(numeric);
}

function isMeteredEntitlement(entitlement: WorkspaceOverviewEntitlement): boolean {
  return entitlement.metered;
}

function hasMeteredUsage(
  entitlement: WorkspaceOverviewEntitlement,
): entitlement is WorkspaceOverviewEntitlement & {
  usage_this_month: NonNullable<WorkspaceOverviewEntitlement['usage_this_month']>;
} {
  return entitlement.metered && entitlement.usage_this_month != null;
}

function isValidFiniteQuantity(value: string): boolean {
  return /^\d+$/.test(value.trim()) && Number.isSafeInteger(Number(value.trim()));
}

function currentCompetence(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

interface WorkspaceAccount360Props {
  workspaceId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** O root opera; funcionários autorizados recebem a leitura técnica mascarada. */
  isPlatformRoot: boolean;
  canManageAssignments?: boolean;
  canManageMilestones?: boolean;
  canViewCommercial?: boolean;
  canManageCommercialContracts?: boolean;
  canViewCommercialAdjustments?: boolean;
  canManageCommercialAdjustments?: boolean;
  canViewEntitlementBonuses?: boolean;
  canManageEntitlementBonuses?: boolean;
  onEnterDirect?: (
    workspace: { id: string; name: string; slug?: string | null },
    reason: string,
  ) => Promise<void>;
}

export function WorkspaceAccount360({
  workspaceId,
  open,
  onOpenChange,
  isPlatformRoot,
  canManageAssignments = isPlatformRoot,
  canManageMilestones = isPlatformRoot,
  canViewCommercial = isPlatformRoot,
  canManageCommercialContracts = isPlatformRoot,
  canViewCommercialAdjustments = isPlatformRoot,
  canManageCommercialAdjustments = isPlatformRoot,
  canViewEntitlementBonuses = isPlatformRoot,
  canManageEntitlementBonuses = isPlatformRoot,
  onEnterDirect,
}: WorkspaceAccount360Props) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [bonusCompetence, setBonusCompetence] = useState(() => currentCompetence());
  const [bonusExtra, setBonusExtra] = useState('');
  const [bonusReason, setBonusReason] = useState('');
  const [lastBonusQuota, setLastBonusQuota] = useState<PlatformFinanceHarmonizationQuota | null>(null);
  const [memberAction, setMemberAction] = useState<{
    member: WorkspaceOverviewResponse['members'][number];
    kind: 'block' | 'sessions' | 'ownership' | 'package';
    accessPackage?: string;
  } | null>(null);
  const [memberActionReason, setMemberActionReason] = useState('');
  const [directStepUpVisible, setDirectStepUpVisible] = useState(false);
  const [directAccessReason, setDirectAccessReason] = useState('');
  const [hasActiveAdministrativeSession, setHasActiveAdministrativeSession] = useState(
    () => Boolean(getAdministrativeSessionToken()?.trim()),
  );

  useEffect(() => {
    if (!open) {
      setDraft(null);
      setBonusExtra('');
      setBonusReason('');
      setLastBonusQuota(null);
      setMemberAction(null);
      setMemberActionReason('');
      setDirectStepUpVisible(false);
      setDirectAccessReason('');
      setHasActiveAdministrativeSession(Boolean(getAdministrativeSessionToken()?.trim()));
    }
  }, [open]);

  const overviewQuery = useQuery<WorkspaceOverviewResponse>({
    queryKey: ['platform', 'workspace-overview', workspaceId],
    queryFn: () => fetchWorkspaceOverview(workspaceId as string),
    enabled: open && Boolean(workspaceId),
    staleTime: 15_000,
  });
  const data = overviewQuery.data;
  const directAccessMutation = useMutation({
    mutationFn: () => {
      if (!data?.workspace) throw new Error('A conta ainda não foi carregada.');
      return onEnterDirect?.({
        id: data.workspace.id,
        name: data.workspace.name,
        slug: data.workspace.slug,
      }, directAccessReason.trim());
    },
    onError: (error: Error) => {
      if (isAdministrativeStepUpError(error)) {
        setHasActiveAdministrativeSession(false);
        setDirectStepUpVisible(true);
      }
      toast({
        variant: 'destructive',
        title: 'Não foi possível entrar diretamente na conta',
        description: error.message,
      });
    },
  });
  const accessPackagesQuery = useQuery({
    queryKey: ['platform', 'people', 'access-packages'],
    queryFn: fetchPlatformPeopleAccessPackages,
    enabled: open && isPlatformRoot,
    staleTime: 60_000,
  });

  const overrideMutation = useMutation({
    mutationFn: (payload: { entitlementKey: string; mode: 'grant' | 'revoke' | 'limit'; quantity: number | null; reason: string }) =>
      putWorkspaceEntitlementOverrides(workspaceId as string, [
        {
          entitlementKey: payload.entitlementKey,
          action: 'upsert',
          mode: payload.mode,
          quantity: payload.quantity,
          reason: payload.reason,
        },
      ]),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspace-overview', workspaceId] });
      toast({ title: 'Função da conta atualizada.' });
      setDraft(null);
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Não foi possível atualizar a função', description: error.message });
    },
  });

  const harmonizationBonusMutation = useMutation({
    mutationFn: (payload: { competence: string; extra: number; reason: string }) =>
      grantWorkspaceFinanceHarmonizationBonus(workspaceId as string, payload),
    onSuccess: (quota) => {
      setLastBonusQuota(quota);
      setBonusExtra('');
      setBonusReason('');
      toast({
        title: 'Bônus de harmonização concedido.',
        description: `Competência ${quota.competence}: ${quota.used}/${quota.limit} usadas · ${quota.remaining} restantes.`,
      });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Não foi possível conceder o bônus', description: error.message });
    },
  });
  const memberPackageMutation = useMutation({
    mutationFn: (payload: { userId: string; accessPackage: string; reason: string }) =>
      setPlatformPersonAccessPackage(workspaceId as string, payload.userId, payload.accessPackage, payload.reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspace-overview', workspaceId] });
      toast({ title: 'Pacote do usuário atualizado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível atualizar o pacote', description: error.message }),
  });
  const memberBlockMutation = useMutation({
    mutationFn: (payload: { member: WorkspaceOverviewResponse['members'][number]; reason: string }) =>
      setPlatformPersonBlocked(workspaceId as string, payload.member.user_id, payload.member.status !== 'suspended', payload.reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspace-overview', workspaceId] });
      toast({ title: 'Acesso do usuário atualizado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível atualizar o acesso', description: error.message }),
  });
  const memberSessionsMutation = useMutation({
    mutationFn: (payload: { member: WorkspaceOverviewResponse['members'][number]; reason: string }) =>
      revokePlatformPersonSessions(workspaceId as string, payload.member.user_id, payload.reason),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspace-overview', workspaceId] });
      toast({ title: `${result.revoked_count} sessão(ões) encerrada(s).` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível encerrar as sessões', description: error.message }),
  });
  const ownershipMutation = useMutation({
    mutationFn: (payload: { member: WorkspaceOverviewResponse['members'][number]; reason: string }) =>
      transferPlatformWorkspaceOwnership(workspaceId as string, payload.member.user_id, payload.reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspace-overview', workspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspaces'] });
      toast({ title: 'Titularidade transferida. Sessões com autoridade anterior foram encerradas.' });
      setMemberActionReason('');
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível transferir a titularidade', description: error.message }),
  });


  function beginEdit(entitlement: WorkspaceOverviewEntitlement, nextEnabled: boolean) {
    setDraft({
      entitlementKey: entitlement.entitlement,
      mode: nextEnabled ? 'grant' : 'revoke',
      quantitative: entitlement.quantitative,
      metered: entitlement.metered,
      quantity: entitlement.limit_quantity != null ? String(entitlement.limit_quantity) : '',
      reason: '',
    });
  }

  function beginLimitEdit(entitlement: WorkspaceOverviewEntitlement) {
    setDraft({
      entitlementKey: entitlement.entitlement,
      mode: 'limit',
      quantitative: true,
      metered: entitlement.metered,
      quantity: entitlement.limit_quantity != null ? String(entitlement.limit_quantity) : '',
      reason: '',
    });
  }

  function submitEdit() {
    if (!draft || !draft.reason.trim()) return;
    const trimmedQuantity = draft.quantity.trim();
    if ((draft.mode === 'grant' || draft.mode === 'limit') && draft.quantitative) {
      if (!isValidFiniteQuantity(trimmedQuantity)) return;
      const parsed = Number(trimmedQuantity);
      overrideMutation.mutate({
        entitlementKey: draft.entitlementKey,
        mode: 'limit',
        quantity: parsed,
        reason: draft.reason.trim(),
      });
      return;
    }
    overrideMutation.mutate({
      entitlementKey: draft.entitlementKey,
      mode: draft.mode,
      quantity: null,
      reason: draft.reason.trim(),
    });
  }

  function submitHarmonizationBonus() {
    const competence = bonusCompetence.trim();
    const extra = Number(bonusExtra.trim());
    const reason = bonusReason.trim();
    if (!competence || !Number.isFinite(extra) || extra <= 0 || reason.length < 8) return;
    harmonizationBonusMutation.mutate({ competence, extra: Math.trunc(extra), reason });
  }

  function beginDirectAccess() {
    if (!hasActiveAdministrativeSession) {
      setDirectStepUpVisible(true);
      return;
    }
    if (directAccessReason.trim().length < 8) {
      toast({
        variant: 'destructive',
        title: 'Informe o motivo do acesso direto',
        description: 'A justificativa auditável deve possuir ao menos 8 caracteres.',
      });
      return;
    }
    directAccessMutation.mutate();
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
        <SheetHeader className="border-b p-6 pb-4 text-left">
          <SheetTitle>{data?.workspace.name ?? (overviewQuery.isLoading ? 'Carregando conta...' : 'Conta 360')}</SheetTitle>
          <SheetDescription>Agregado 360 da conta na central da plataforma.</SheetDescription>
          <Badge variant={isPlatformRoot ? 'default' : 'outline'} className="w-fit">
            {isPlatformRoot ? 'Super Admin · visão administrativa' : 'Leitura operacional · dados privados ocultos'}
          </Badge>
          {data && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Badge variant="secondary">{data.subscription?.plan?.name ?? 'Sem plano'}</Badge>
              <Badge variant={data.workspace.status === 'active' ? 'secondary' : 'outline'}>
                {data.workspace.status}
              </Badge>
              <span className="text-xs text-muted-foreground">criado em {fmtDateTime(data.workspace.created_at)}</span>
              {isPlatformRoot && onEnterDirect && (
                <Button
                  type="button"
                  size="sm"
                  className="ml-auto"
                  disabled={
                    directAccessMutation.isPending
                    || (hasActiveAdministrativeSession && directAccessReason.trim().length < 8)
                  }
                  onClick={beginDirectAccess}
                >
                  {directAccessMutation.isPending
                    ? 'Entrando...'
                    : hasActiveAdministrativeSession
                      ? 'Entrar diretamente na conta'
                      : 'Confirmar MFA para entrar'}
                </Button>
              )}
            </div>
          )}
          {data && isPlatformRoot && onEnterDirect ? (
            <div className="space-y-1.5 pt-2">
              <Label htmlFor="workspace-root-direct-reason">Motivo do acesso direto</Label>
              <Input
                id="workspace-root-direct-reason"
                value={directAccessReason}
                onChange={(event) => setDirectAccessReason(event.target.value)}
                placeholder="Ex.: diagnóstico do ticket SUP-1234"
              />
              <p className="text-xs text-muted-foreground">
                Obrigatório, com ao menos 8 caracteres. O motivo integra a trilha auditável.
              </p>
            </div>
          ) : null}
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-6" data-testid="workspace-account-360-scroll">
          {directStepUpVisible && isPlatformRoot && onEnterDirect ? (
            <div className="rounded-lg border border-primary/25 bg-primary/5 p-4">
              <SupportStepUpPanel
                onAdministrativeSessionChange={setHasActiveAdministrativeSession}
              />
              <p className="mt-3 text-xs text-muted-foreground">
                {hasActiveAdministrativeSession
                  ? 'MFA confirmado. Use novamente “Entrar diretamente na conta”.'
                  : 'O acesso direto dispensa aprovação do cliente, mas exige confirmação MFA recente do Super Admin.'}
              </p>
            </div>
          ) : null}
          {overviewQuery.isLoading && (
            <div className="space-y-3">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-40 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          )}

          {overviewQuery.isError && (
            <p className="text-sm text-destructive">Não foi possível carregar a Conta 360 desta conta.</p>
          )}

          {data && (
            <>
              <WorkspaceOperationsPanel workspaceId={data.workspace.id} canManageAssignments={canManageAssignments} canManageMilestones={canManageMilestones} />
              <WorkspaceCommercialSection
                workspaceId={data.workspace.id}
                canView={canViewCommercial}
                canManageContracts={canManageCommercialContracts}
                canViewAdjustments={canViewCommercialAdjustments}
                canManageAdjustments={canManageCommercialAdjustments}
                canViewBonuses={canViewEntitlementBonuses}
                canManageBonuses={canManageEntitlementBonuses}
                canRestoreContract={isPlatformRoot}
                isPlatformRoot={isPlatformRoot}
              />
              <WorkspaceAccountTasksSection
                summary={data.tasks}
                isPlatformRoot={isPlatformRoot}
                canEnterDirect={Boolean(onEnterDirect)}
                directAccessReady={
                  !hasActiveAdministrativeSession || directAccessReason.trim().length >= 8
                }
                entering={directAccessMutation.isPending}
                onEnterDirect={beginDirectAccess}
              />
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Funções</h3>
                <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">
                  Override vence o pacote. Revogar uma função esconde as telas dela para o cliente; os dados são preservados.
                </p>
                <div className="space-y-2">
                  {data.entitlements.map((entitlement) => {
                    const label = ENTITLEMENT_LABEL[entitlement.entitlement] ?? entitlement.entitlement;
                    const metered = isMeteredEntitlement(entitlement);
                    const rowDraft = draft?.entitlementKey === entitlement.entitlement ? draft : null;
                    return (
                      <div key={entitlement.entitlement} className="rounded-lg border p-3 space-y-2">
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{label}</p>
                            <div className="mt-1 flex flex-wrap items-center gap-1.5">
                              <Badge variant={entitlement.origin === 'override' ? 'outline' : 'secondary'} className="text-2xs">
                                {entitlement.origin === 'override' ? 'override' : entitlement.origin === 'plan' ? 'plano' : 'não configurado'}
                              </Badge>
                              {entitlement.limit_quantity != null && (
                                <span className="text-2xs tabular-nums text-muted-foreground">
                                  limite {entitlement.limit_quantity}
                                </span>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            {isPlatformRoot && entitlement.enabled && entitlement.quantitative && (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                aria-label={`Alterar limite de ${label}`}
                                onClick={() => beginLimitEdit(entitlement)}
                              >
                                Alterar limite
                              </Button>
                            )}
                            <Switch
                              aria-label={`Alternar ${label}`}
                              checked={Boolean(entitlement.enabled)}
                              disabled={!isPlatformRoot}
                              onCheckedChange={(checked) => beginEdit(entitlement, checked)}
                            />
                          </div>
                        </div>

                        {metered && entitlement.usage_this_month && (
                          <div className="space-y-1">
                            <Progress
                              value={
                                entitlement.limit_quantity
                                  ? Math.min(100, (entitlement.usage_this_month.consumed_quantity / entitlement.limit_quantity) * 100)
                                  : 0
                              }
                              className="h-1.5"
                            />
                            <p className="text-2xs tabular-nums text-muted-foreground">
                              {entitlement.usage_this_month.consumed_quantity} consumidos · {entitlement.usage_this_month.reserved_quantity} reservados
                              {entitlement.usage_this_month.remaining_quantity != null && ` · ${entitlement.usage_this_month.remaining_quantity} restantes`}
                            </p>
                          </div>
                        )}

                        {rowDraft && (
                          <div className="space-y-2 rounded-md bg-muted/40 p-2">
                            {(rowDraft.mode === 'grant' || rowDraft.mode === 'limit')
                              && rowDraft.quantitative && (
                              <div className="space-y-1">
                                <Label htmlFor={`qty-${entitlement.entitlement}`}>
                                  {rowDraft.metered
                                    ? 'Quantidade (limite mensal, obrigatório)'
                                    : 'Quantidade (limite da capacidade, obrigatório)'}
                                </Label>
                                <Input
                                  id={`qty-${entitlement.entitlement}`}
                                  type="number"
                                  min={0}
                                  value={rowDraft.quantity}
                                  onChange={(event) => setDraft({ ...rowDraft, quantity: event.target.value })}
                                  className="tabular-nums"
                                />
                              </div>
                            )}
                            <div className="space-y-1">
                              <Label htmlFor={`reason-${entitlement.entitlement}`}>Motivo (obrigatório)</Label>
                              <Textarea
                                id={`reason-${entitlement.entitlement}`}
                                rows={2}
                                value={rowDraft.reason}
                                onChange={(event) => setDraft({ ...rowDraft, reason: event.target.value })}
                                placeholder="Justifique a alteração desta função para a conta"
                              />
                            </div>
                            <div className="flex justify-end gap-2">
                              <Button type="button" variant="ghost" size="sm" onClick={() => setDraft(null)}>
                                Cancelar
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                disabled={
                                  !rowDraft.reason.trim()
                                  || (
                                    (rowDraft.mode === 'grant' || rowDraft.mode === 'limit')
                                    && rowDraft.quantitative
                                    && !isValidFiniteQuantity(rowDraft.quantity)
                                  )
                                  || overrideMutation.isPending
                                }
                                onClick={submitEdit}
                              >
                                {overrideMutation.isPending ? 'Salvando...' : 'Salvar'}
                              </Button>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Usuários da conta</h3>
                {data.members.length === 0 && <p className="text-xs text-muted-foreground">Nenhum usuário humano vinculado.</p>}
                <div className="space-y-2">
                  {data.members.map((member) => (
                    <div key={member.user_id} className="space-y-2 rounded-lg border p-3 text-xs">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate font-medium">{member.name}</p>
                          <p className="truncate text-muted-foreground">{member.email ?? 'Sem e-mail'}</p>
                        </div>
                        <Badge variant={member.status === 'active' ? 'secondary' : 'outline'}>{member.status}</Badge>
                      </div>
                      <p className="mt-1 text-2xs text-muted-foreground">
                        {member.access_package ?? member.role} · {member.active_session_count} sessões ativas
                      </p>
                      {isPlatformRoot && <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                        <Select
                          value={member.access_package ?? undefined}
                          disabled={accessPackagesQuery.isLoading || memberPackageMutation.isPending}
                          onValueChange={(accessPackage) => {
                            setMemberActionReason('');
                            setMemberAction({ member, kind: 'package', accessPackage });
                          }}
                        >
                          <SelectTrigger aria-label={`Pacote de ${member.name}`} className="h-8"><SelectValue placeholder="Pacote personalizado" /></SelectTrigger>
                          <SelectContent>{(accessPackagesQuery.data ?? []).map((item) => <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>)}</SelectContent>
                        </Select>
                        <div className="flex gap-1">
                          {member.active_session_count > 0 && <Button type="button" size="sm" variant="outline" className="h-8" onClick={() => { setMemberActionReason(''); setMemberAction({ member, kind: 'sessions' }); }}>Encerrar sessões</Button>}
                          {member.role !== 'workspace_owner' && <Button type="button" size="sm" variant="outline" className="h-8" onClick={() => { setMemberActionReason(''); setMemberAction({ member, kind: 'ownership' }); }}>Tornar titular</Button>}
                          <Button type="button" size="sm" variant={member.status === 'suspended' ? 'outline' : 'destructive'} className="h-8" onClick={() => { setMemberActionReason(''); setMemberAction({ member, kind: 'block' }); }}>
                            {member.status === 'suspended' ? 'Reativar' : 'Suspender'}
                          </Button>
                        </div>
                      </div>}
                    </div>
                  ))}
                </div>
                {memberAction && <div role="alertdialog" aria-modal="true" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3">
                  <p className="text-sm font-medium">
                    {memberAction.kind === 'ownership'
                      ? `Transferir a titularidade para ${memberAction.member.name}?`
                      : memberAction.kind === 'package'
                      ? `Alterar o pacote de ${memberAction.member.name}?`
                      : memberAction.kind === 'sessions'
                      ? `Encerrar todas as sessões de ${memberAction.member.name}?`
                      : `${memberAction.member.status === 'suspended' ? 'Reativar' : 'Suspender'} ${memberAction.member.name}?`}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {memberAction.kind === 'ownership'
                      ? 'Titulares anteriores serão rebaixados para administradores e todas as sessões afetadas serão encerradas.'
                      : memberAction.kind === 'package'
                      ? `O novo pacote será ${memberAction.accessPackage ?? 'o selecionado'} e a mudança ficará registrada no histórico.`
                      : memberAction.kind === 'sessions'
                      ? 'Todos os dispositivos serão desconectados sem remover o vínculo.'
                      : memberAction.member.status === 'suspended'
                        ? 'O usuário voltará a acessar esta conta.'
                        : 'O vínculo será suspenso e as sessões ativas serão encerradas.'}
                  </p>
                  <div className="mt-3 space-y-1">
                    <Label htmlFor="workspace-member-action-reason">
                      {memberAction.kind === 'ownership' ? 'Motivo da transferência' : 'Motivo da alteração'}
                    </Label>
                    <Textarea id="workspace-member-action-reason" rows={2} value={memberActionReason} onChange={(event) => setMemberActionReason(event.target.value)} />
                  </div>
                  <div className="mt-3 flex justify-end gap-2">
                    <Button type="button" size="sm" variant="outline" onClick={() => { setMemberAction(null); setMemberActionReason(''); }}>Cancelar</Button>
                    <Button type="button" size="sm" variant="destructive" disabled={memberActionReason.trim().length < 8} onClick={() => {
                      const reason = memberActionReason.trim();
                      if (memberAction.kind === 'ownership') ownershipMutation.mutate({ member: memberAction.member, reason });
                      else if (memberAction.kind === 'sessions') memberSessionsMutation.mutate({ member: memberAction.member, reason });
                      else if (memberAction.kind === 'package' && memberAction.accessPackage) memberPackageMutation.mutate({ userId: memberAction.member.user_id, accessPackage: memberAction.accessPackage, reason });
                      else memberBlockMutation.mutate({ member: memberAction.member, reason });
                      setMemberAction(null);
                      setMemberActionReason('');
                    }}>Confirmar</Button>
                  </div>
                </div>}
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Uso do mês</h3>
                <div className="space-y-2">
                  {data.entitlements
                    .filter(hasMeteredUsage)
                    .map((entitlement) => (
                      <div key={`usage-${entitlement.entitlement}`} className="rounded-lg border p-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs font-medium">{ENTITLEMENT_LABEL[entitlement.entitlement] ?? entitlement.entitlement}</p>
                          <span className="text-2xs tabular-nums text-muted-foreground">
                            {entitlement.usage_this_month.consumed_quantity}
                            {entitlement.limit_quantity != null ? ` / ${entitlement.limit_quantity}` : ''}
                          </span>
                        </div>
                        <Progress
                          value={
                            entitlement.limit_quantity
                              ? Math.min(100, (entitlement.usage_this_month.consumed_quantity / entitlement.limit_quantity) * 100)
                              : 0
                          }
                          className="mt-1.5 h-1.5"
                        />
                      </div>
                    ))}
                </div>
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Conexões</h3>
                {data.integrations.length === 0 && <p className="text-xs text-muted-foreground">Nenhuma integração conectada.</p>}
                <div className="grid gap-2">
                  {data.integrations.map((integration, index) => (
                    <div key={`${integration.provider}-${index}`} className="flex items-center justify-between rounded-lg border p-2.5 text-xs">
                      <span className="font-medium">{integration.label ?? integration.provider}</span>
                      <Badge variant={integration.status === 'connected' ? 'secondary' : 'outline'}>{integration.status}</Badge>
                    </div>
                  ))}
                </div>
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">WhatsApp</h3>
                {data.whatsapp_numbers.length === 0 && <p className="text-xs text-muted-foreground">Nenhum número conectado.</p>}
                <div className="grid gap-2">
                  {data.whatsapp_numbers.map((number, index) => (
                    <div key={`${number.display_phone_number ?? index}`} className="flex items-center justify-between rounded-lg border p-2.5 text-xs">
                      <span className="font-medium">{number.display_name ?? number.display_phone_number ?? 'Número'}</span>
                      <div className="flex items-center gap-1.5">
                        {number.is_default && <Badge variant="outline">padrão</Badge>}
                        <Badge variant="secondary">{number.status}</Badge>
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              <UtilityTemplateLibraryPanel
                surface="blackbox"
                workspaceId={workspaceId}
                canRetry={isPlatformRoot}
              />

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Agentes</h3>
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-lg bg-muted/40 p-3">
                    <p className="text-2xs uppercase tracking-wide text-muted-foreground">Agentes ativos</p>
                    <p className="mt-1 text-lg font-semibold tabular-nums">{data.agents.active_ai_agents}</p>
                  </div>
                  <div className="rounded-lg bg-muted/40 p-3">
                    <p className="text-2xs uppercase tracking-wide text-muted-foreground">Playbooks publicados</p>
                    <p className="mt-1 text-lg font-semibold tabular-nums">{data.agents.published_playbooks}</p>
                  </div>
                </div>
                {data.agents.autonomy_by_playbook.length > 0 && (
                  <div className="space-y-1">
                    {data.agents.autonomy_by_playbook.map((playbook, index) => (
                      <div key={`${playbook.name}-${index}`} className="flex items-center justify-between rounded-lg border p-2 text-xs">
                        <span>{playbook.name}</span>
                        <Badge variant="outline">{playbook.autonomy_mode}</Badge>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Suporte</h3>
                <div className="rounded-lg bg-muted/40 p-3">
                  <p className="text-2xs uppercase tracking-wide text-muted-foreground">Tickets abertos</p>
                  <p className="mt-1 text-lg font-semibold tabular-nums">{data.support.open_tickets}</p>
                </div>
              </section>

              {data.private_data_included && <section className="space-y-2">
                <h3 className="text-sm font-semibold">Financeiro resumido</h3>
                {data.finance_kpis ? (
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-lg bg-muted/40 p-3">
                      <p className="text-2xs uppercase tracking-wide text-muted-foreground">Recebido (30d)</p>
                      <p className="mt-1 text-sm font-semibold tabular-nums">{fmtBRL(data.finance_kpis.recebido)}</p>
                    </div>
                    <div className="rounded-lg bg-muted/40 p-3">
                      <p className="text-2xs uppercase tracking-wide text-muted-foreground">A receber</p>
                      <p className="mt-1 text-sm font-semibold tabular-nums">{fmtBRL(data.finance_kpis.a_receber)}</p>
                    </div>
                    <div className="rounded-lg bg-muted/40 p-3">
                      <p className="text-2xs uppercase tracking-wide text-muted-foreground">Despesas</p>
                      <p className="mt-1 text-sm font-semibold tabular-nums">{fmtBRL(data.finance_kpis.despesas)}</p>
                    </div>
                    <div className="rounded-lg bg-muted/40 p-3">
                      <p className="text-2xs uppercase tracking-wide text-muted-foreground">Resultado</p>
                      <p className="mt-1 text-sm font-semibold tabular-nums">{fmtBRL(data.finance_kpis.resultado)}</p>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">Sem dados financeiros disponíveis para o período.</p>
                )}
              </section>}

              {isPlatformRoot && <section className="space-y-2">
                <h3 className="text-sm font-semibold">Bônus de harmonização financeira (F14)</h3>
                <p className="text-2xs text-muted-foreground">
                  Concede verificações completas extras na cota mensal desta conta, além do limite do plano.
                </p>
                <div className="rounded-lg border p-3 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <Label htmlFor="harmonization-bonus-competence">Competência (AAAA-MM)</Label>
                      <Input
                        id="harmonization-bonus-competence"
                        value={bonusCompetence}
                        onChange={(event) => setBonusCompetence(event.target.value)}
                        placeholder={currentCompetence()}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="harmonization-bonus-extra">Verificações extras</Label>
                      <Input
                        id="harmonization-bonus-extra"
                        type="number"
                        min={1}
                        value={bonusExtra}
                        onChange={(event) => setBonusExtra(event.target.value)}
                        className="tabular-nums"
                      />
                    </div>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="harmonization-bonus-reason">Motivo da concessão</Label>
                    <Input
                      id="harmonization-bonus-reason"
                      value={bonusReason}
                      onChange={(event) => setBonusReason(event.target.value)}
                      placeholder="Ex.: compensação aprovada para a competência"
                      maxLength={500}
                    />
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    {lastBonusQuota ? (
                      <p className="text-2xs tabular-nums text-muted-foreground">
                        Última concessão: {lastBonusQuota.used}/{lastBonusQuota.limit} usadas · {lastBonusQuota.remaining} restantes · renova em{' '}
                        {fmtDateTime(lastBonusQuota.renewsAt)}
                      </p>
                    ) : (
                      <span />
                    )}
                    <Button
                      type="button"
                      size="sm"
                      disabled={
                        !bonusCompetence.trim() ||
                        !bonusExtra.trim() ||
                        Number(bonusExtra) <= 0 ||
                        bonusReason.trim().length < 8 ||
                        harmonizationBonusMutation.isPending
                      }
                      onClick={submitHarmonizationBonus}
                    >
                      {harmonizationBonusMutation.isPending ? 'Concedendo...' : 'Conceder bônus'}
                    </Button>
                  </div>
                </div>
              </section>}

              {data.private_data_included && <section className="space-y-2">
                <h3 className="text-sm font-semibold">Auditoria recente</h3>
                {data.recent_audit_events.length === 0 && <p className="text-xs text-muted-foreground">Sem eventos recentes.</p>}
                <div className="space-y-1.5">
                  {data.recent_audit_events.map((event, index) => (
                    <div key={`${event.event_type}-${index}`} className="rounded-lg border p-2.5 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">{event.summary ?? event.event_type}</span>
                        <span className="shrink-0 text-2xs text-muted-foreground">{fmtDateTime(event.occurred_at)}</span>
                      </div>
                      <p className="mt-0.5 text-2xs text-muted-foreground">{event.actor_label ?? 'sistema'}</p>
                    </div>
                  ))}
                </div>
              </section>}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
