import { useEffect, useState, useCallback, useMemo } from 'react';
import { SectionHeader } from '@/components/common/SectionHeader';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AutomationSettingsPanel } from '@/components/admin/AutomationSettingsPanel';
import { AuthSecurityPanel } from '@/components/admin/AuthSecurityPanel';
import { BrandingSettingsPanel } from '@/components/admin/BrandingSettingsPanel';
import { ConfigurationGovernancePanel } from '@/components/admin/ConfigurationGovernancePanel';
import { FinanceIntegrationsPanel } from '@/components/admin/FinanceIntegrationsPanel';
import { LocalProvisioningPanel } from '@/components/admin/LocalProvisioningPanel';
import { LocalStoragePanel } from '@/components/admin/LocalStoragePanel';
import { MeetingsAgendaPanel } from '@/components/admin/MeetingsAgendaPanel';
import { MessageTemplatesPanel } from '@/components/admin/MessageTemplatesPanel';
import { NotificationSettingsPanel } from '@/components/admin/NotificationSettingsPanel';
import { PersonalPreferencesPanel } from '@/components/admin/PersonalPreferencesPanel';
import { SaasBillingPanel } from '@/components/admin/SaasBillingPanel';
import { SecurityAuditPanel } from '@/components/admin/SecurityAuditPanel';
import { SupportAccessPanel } from '@/components/admin/SupportAccessPanel';
import { StaffContinuityPanel } from '@/components/admin/StaffContinuityPanel';
import { WhatsAppAutomationRulesPanel } from '@/components/admin/WhatsAppAutomationRulesPanel';
import { WorkspaceTeamPanel } from '@/components/admin/WorkspaceTeamPanel';
import { WorkspaceOperationsPanel } from '@/components/admin/WorkspaceOperationsPanel';
import { AiCostControlPanel, AiCostUsageSummary } from '@/components/admin/AiCostControlPanel';
import { AiByokKeysPanel, AiModelRoutingPanel } from '@/components/admin/AiModelRoutingPanel';
import { PermissionGate } from '@/klm/components/PermissionGate';
import { MetaCommercialChargePanel } from '@/components/admin/MetaCommercialChargePanel';
import { OmnichannelConnectionsPanel } from '@/klm/components/OmnichannelConnectionsPanel';
import { useAppStore } from '@/store/app-context';
import { klmApi } from '@/klm/api';
import {
  fetchAdminControlPlane,
  fetchFinanceIntegrations,
  fetchMeetings,
  fetchMessageTemplates,
  fetchPublicApiLogs,
  fetchPublicApiTokens,
  fetchWhatsAppAutomationRules,
  fetchIntegrationCatalog,
  fetchInvitationLink,
  fetchOpenApiSurface,
  fetchRoleCatalog,
  fetchTeamMembers,
  fetchWorkspaceAccessPackages,
  fetchWorkspaceAutomationSettings,
  fetchWorkspaceOnboarding,
  fetchWorkspaceOperations,
  fetchWorkspacePlanSummary,
  fetchWorkspaceAiCosts,
  fetchWorkspaceAiCredentials,
  fetchWorkspaceAiModelRouting,
  fetchWorkspaceAiTokenPackages,
  fetchWorkspaceSettings,
  deleteMeeting,
  deleteWorkspaceAiCredential,
  saveAdminControlPlane,
  saveAdminUser,
  saveFinanceIntegration,
  createPublicApiToken,
  saveMessageTemplates,
  saveMeeting,
  saveWhatsAppAutomationRules,
  saveWorkspaceAutomationSettings,
  saveWorkspaceOperations,
  saveWorkspaceAiCostPolicy,
  saveWorkspaceAiCredential,
  saveWorkspaceAiModelRouting,
  requestWorkspaceAiTokenPackage,
  revokePublicApiToken,
  resendTeamInvitation,
  revokeTeamInvitation,
} from '@/services/admin';
import { fetchMetaCommercialCharges } from '@/services/campaigns';
import { fetchPlatformAiCosts } from '@/services/platform';
import {
  Users, Database, Bell, Palette, Bot, Shield, MessageSquare,
  CheckCircle2, XCircle, Copy, RefreshCw, Loader2,
  ChevronDown, ChevronUp, AlertTriangle, Plug,
} from 'lucide-react';
import type {
  AdminControlPlane,
  AdminRole,
  AdminUser,
  BrandingSettingsProfile,
  IntegrationCatalogProvider,
  IntegrationConnection,
  Meeting,
  MessageTemplateSettings,
  NotificationSettingsProfile,
  OpenApiSurface,
  PublicApiRequestLog,
  SecurityAuditSettingsProfile,
  WorkspaceAutomationSettings,
  WorkspaceAccessPackage,
  WorkspacePublicApiToken,
  WhatsAppAutomationSettings,
  WorkspaceOperationsSettings,
  WorkspacePlanSummary,
  WorkspaceAiTokenSummary,
  WorkspaceSettingsProfile,
} from '@/types';
import { Link, useSearchParams } from 'react-router-dom';

// ── Page ──────────────────────────────────────────────────────────────────

export default function Settings({ embedded = false }: { embedded?: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { can, state } = useAppStore();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [accessPackages, setAccessPackages] = useState<WorkspaceAccessPackage[]>([]);
  const [workspaceSettings, setWorkspaceSettings] = useState<WorkspaceSettingsProfile | null>(null);
  const [automationSettings, setAutomationSettings] = useState<WorkspaceAutomationSettings | null>(null);
  const [controlPlane, setControlPlane] = useState<AdminControlPlane | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationConnection[]>([]);
  const [integrationCatalog, setIntegrationCatalog] = useState<IntegrationCatalogProvider[]>([]);
  const [openApiSurface, setOpenApiSurface] = useState<OpenApiSurface | null>(null);
  const [publicApiTokens, setPublicApiTokens] = useState<WorkspacePublicApiToken[]>([]);
  const [publicApiLogs, setPublicApiLogs] = useState<PublicApiRequestLog[]>([]);
  const [surfaceError, setSurfaceError] = useState<string | null>(null);

  const canManageUsers = can('users', 'manage');
  const canViewRoles = can('roles', 'view');
  const canManageIntegrations = can('integrations', 'manage');
  const canManageNotifications = can('notifications', 'manage');
  const canManageBranding = can('branding', 'manage');
  const canViewSecurity = can('security', 'view') || can('audit', 'view') || can('admin', 'manage');
  const canManageWorkspaceOps = can('configuracoes', 'edit');
  const canViewWorkspaceAutomation = can('configuracoes', 'view') || can('admin', 'view');
  const canManageBilling = can('workspace', 'manage') || can('admin', 'manage');
  const canViewBilling = can('finance', 'view') || canManageBilling;
  const canViewStorage = can('configuracoes', 'view') || can('admin', 'view');
  const isPlatformRoot = Boolean(state.currentUser?.isPlatformAdmin);
  const canViewWorkspaceAiCosts = ['workspace_owner', 'workspace_admin'].includes(state.session?.activeRole ?? '');
  const canManageSupportAccess = canViewWorkspaceAiCosts;
  const canViewAiCosts = canViewWorkspaceAiCosts || isPlatformRoot;
  const canManageAiCosts = canViewWorkspaceAiCosts && can('configuracoes', 'edit');
  const runtimeQuery = useQuery({
    queryKey: ['klm', 'runtime'],
    queryFn: klmApi.runtime,
  });

  const operationsQuery = useQuery<WorkspaceOperationsSettings>({
    queryKey: ['workspace-operations'],
    queryFn: fetchWorkspaceOperations,
    staleTime: 30_000,
  });
  const planSummaryQuery = useQuery<WorkspacePlanSummary | null>({
    queryKey: ['workspace-plan-summary', state.session?.activeWorkspace?.id],
    queryFn: fetchWorkspacePlanSummary,
    staleTime: 30_000,
  });
  const aiCostQuery = useQuery({
    queryKey: ['workspace-ai-costs', state.session?.activeWorkspace?.id, isPlatformRoot ? 'platform-root' : 'workspace'],
    queryFn: () => isPlatformRoot
      ? fetchPlatformAiCosts(30, state.session?.activeWorkspace?.id)
      : fetchWorkspaceAiCosts(),
    staleTime: 30_000,
    enabled: canViewAiCosts,
    retry: false,
  });
  const aiTokenPackagesQuery = useQuery({
    queryKey: ['workspace-ai-token-packages', state.session?.activeWorkspace?.id],
    queryFn: fetchWorkspaceAiTokenPackages,
    staleTime: 30_000,
    enabled: canViewWorkspaceAiCosts,
    retry: false,
  });
  const canViewIntegrations = can('integrations', 'view') || canManageIntegrations;
  const aiModelRoutingQuery = useQuery({
    queryKey: ['workspace-ai-model-routing', state.session?.activeWorkspace?.id],
    queryFn: fetchWorkspaceAiModelRouting,
    staleTime: 30_000,
    retry: false,
  });
  const aiCredentialsQuery = useQuery({
    queryKey: ['workspace-ai-credentials', state.session?.activeWorkspace?.id],
    queryFn: fetchWorkspaceAiCredentials,
    staleTime: 30_000,
    enabled: canViewIntegrations,
    retry: false,
  });
  const metaCommercialChargesQuery = useQuery({
    queryKey: ['whatsapp-commercial-charges', state.session?.activeWorkspace?.id],
    queryFn: () => fetchMetaCommercialCharges(),
    staleTime: 30_000,
    enabled: canViewWorkspaceAiCosts && can('finance', 'view'),
    retry: false,
  });
  const onboardingQuery = useQuery({
    queryKey: ['workspace-onboarding-settings', state.session?.activeWorkspace?.id],
    queryFn: fetchWorkspaceOnboarding,
    staleTime: 30_000,
  });
  const meetingsQuery = useQuery<Meeting[]>({
    queryKey: ['workspace-meetings', state.session?.activeWorkspace?.id],
    queryFn: fetchMeetings,
    staleTime: 30_000,
    enabled: canManageNotifications,
  });
  const messageTemplatesQuery = useQuery<MessageTemplateSettings>({
    queryKey: ['workspace-message-templates', state.session?.activeWorkspace?.id],
    queryFn: fetchMessageTemplates,
    staleTime: 30_000,
    enabled: canManageWorkspaceOps,
  });
  const operationsMutation = useMutation({
    mutationFn: saveWorkspaceOperations,
    onSuccess: (data) => {
      queryClient.setQueryData(['workspace-operations'], data);
      toast({ title: 'Operação do workspace atualizada.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Erro ao salvar a operação',
        description: error.message,
      });
    },
  });
  const aiCostMutation = useMutation({
    mutationFn: saveWorkspaceAiCostPolicy,
    onSuccess: (data) => {
      queryClient.setQueryData(['workspace-ai-costs', state.session?.activeWorkspace?.id], data);
      toast({ title: 'Limite de tokens de IA atualizado.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível salvar o limite de tokens de IA',
        description: error.message,
      });
    },
  });
  const aiModelRoutingMutation = useMutation({
    mutationFn: saveWorkspaceAiModelRouting,
    onSuccess: (data) => {
      queryClient.setQueryData(['workspace-ai-model-routing', state.session?.activeWorkspace?.id], data);
      toast({ title: 'Roteamento de modelos atualizado.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível salvar o roteamento de modelos',
        description: error.message,
      });
    },
  });
  const aiCredentialSaveMutation = useMutation({
    mutationFn: ({ provider, apiKey }: { provider: string; apiKey: string }) =>
      saveWorkspaceAiCredential(provider, apiKey),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-ai-credentials', state.session?.activeWorkspace?.id] });
      toast({ title: 'Chave de API armazenada com cifra.', description: 'A chave é write-only: somente os últimos 4 caracteres ficam visíveis.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível armazenar a chave de API',
        description: error.message,
      });
    },
  });
  const aiCredentialRemoveMutation = useMutation({
    mutationFn: deleteWorkspaceAiCredential,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-ai-credentials', state.session?.activeWorkspace?.id] });
      toast({ title: 'Chave própria removida.', description: 'A operação voltou para a chave da plataforma.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível remover a chave',
        description: error.message,
      });
    },
  });
  const aiCredentialBusyProvider = aiCredentialSaveMutation.isPending
    ? aiCredentialSaveMutation.variables?.provider ?? null
    : aiCredentialRemoveMutation.isPending
      ? aiCredentialRemoveMutation.variables ?? null
      : null;
  const aiTokenPackageRequestMutation = useMutation({
    mutationFn: requestWorkspaceAiTokenPackage,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-ai-token-packages', state.session?.activeWorkspace?.id] });
      toast({ title: 'Solicitação enviada ao suporte.', description: 'Os tokens só entram no saldo depois da liberação.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível solicitar o pacote',
        description: error.message,
      });
    },
  });
  const messageTemplatesMutation = useMutation({
    mutationFn: saveMessageTemplates,
    onSuccess: (data) => {
      queryClient.setQueryData(['workspace-message-templates', state.session?.activeWorkspace?.id], data);
      toast({ title: 'Templates atualizados.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Erro ao salvar templates',
        description: error.message,
      });
    },
  });

  const whatsappAutomationRulesQuery = useQuery<WhatsAppAutomationSettings>({
    queryKey: ['workspace-whatsapp-automation-rules', state.session?.activeWorkspace?.id],
    queryFn: fetchWhatsAppAutomationRules,
    enabled: canManageWorkspaceOps,
    staleTime: 30_000,
  });

  const whatsappAutomationRulesMutation = useMutation({
    mutationFn: saveWhatsAppAutomationRules,
    onSuccess: (data) => {
      queryClient.setQueryData(['workspace-whatsapp-automation-rules', state.session?.activeWorkspace?.id], data);
      toast({ title: 'Automações do inbox atualizadas.' });
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar as automações do inbox.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar automações do inbox', description: message });
    },
  });

  const settingsSurfaceQuery = useQuery({
    queryKey: ['settings-surface', state.session?.activeWorkspace?.id],
    queryFn: async () => {
      async function loadOptional<T>(enabled: boolean, request: () => Promise<T>, fallback: T): Promise<T> {
        if (!enabled) return fallback;
        return request();
      }

      const [
        usersResponse,
        rolesResponse,
        accessPackagesResponse,
        workspaceResponse,
        automationResponse,
        controlPlaneResponse,
        integrationsResponse,
        catalogResponse,
        openApiResponse,
        publicApiTokensResponse,
        publicApiLogsResponse,
      ] = await Promise.all([
        loadOptional(canManageUsers, fetchTeamMembers, [] as AdminUser[]),
        loadOptional(canViewRoles, fetchRoleCatalog, [] as AdminRole[]),
        loadOptional(canManageUsers, fetchWorkspaceAccessPackages, [] as WorkspaceAccessPackage[]),
        loadOptional(can('admin', 'view'), fetchWorkspaceSettings, null as WorkspaceSettingsProfile | null),
        loadOptional(canViewWorkspaceAutomation, fetchWorkspaceAutomationSettings, null as WorkspaceAutomationSettings | null),
        loadOptional(
          canManageNotifications || canManageBranding || canViewSecurity,
          fetchAdminControlPlane,
          null as AdminControlPlane | null,
        ),
        loadOptional(canManageIntegrations, fetchFinanceIntegrations, [] as IntegrationConnection[]),
        loadOptional(canManageIntegrations, fetchIntegrationCatalog, [] as IntegrationCatalogProvider[]),
        loadOptional(canManageIntegrations, fetchOpenApiSurface, null as OpenApiSurface | null),
        loadOptional(canManageIntegrations, fetchPublicApiTokens, [] as WorkspacePublicApiToken[]),
        loadOptional(canManageIntegrations, fetchPublicApiLogs, [] as PublicApiRequestLog[]),
      ]);

      setRoles(rolesResponse);
      setAccessPackages(accessPackagesResponse);
      setWorkspaceSettings(workspaceResponse);
      setAutomationSettings(automationResponse);
      setControlPlane(controlPlaneResponse);
      setIntegrations(integrationsResponse);
      setIntegrationCatalog(catalogResponse);
      setOpenApiSurface(openApiResponse);
      setPublicApiTokens(publicApiTokensResponse);
      setPublicApiLogs(publicApiLogsResponse);
      setUsers(usersResponse);
      setSurfaceError(null);
      return true;
    },
    staleTime: 30_000,
    retry: 1,
  });

  useEffect(() => {
    if (settingsSurfaceQuery.error instanceof Error) {
      setSurfaceError(settingsSurfaceQuery.error.message);
    }
  }, [settingsSurfaceQuery.error]);

  const refreshPublicApiState = useCallback(async () => {
    if (!canManageIntegrations) return;
    const [tokens, logs] = await Promise.all([
      fetchPublicApiTokens(),
      fetchPublicApiLogs(),
    ]);
    setPublicApiTokens(tokens);
    setPublicApiLogs(logs);
  }, [canManageIntegrations]);

  const handleCreatePublicApiToken = useCallback(async (payload: {
    label: string;
    scopes: Array<
      | 'contacts:read'
      | 'conversations:read'
      | 'leads:write'
      | 'matters:write'
      | 'notes:write'
      | 'templates:send'
      | 'campaigns:write'
    >;
    rateLimitPerMinute: number;
    expiresAt?: string | null;
  }) => {
    const created = await createPublicApiToken(payload);
    await refreshPublicApiState();
    return { plainTextToken: created.plainTextToken };
  }, [refreshPublicApiState]);

  const handleRevokePublicApiToken = useCallback(async (tokenId: string) => {
    await revokePublicApiToken(tokenId);
    await refreshPublicApiState();
  }, [refreshPublicApiState]);

  const visibleTabs = useMemo(
    () => [
      'operacao',
      canViewBilling ? 'assinatura' : null,
      canManageUsers || canViewRoles ? 'equipe' : null,
      'ia',
      canManageWorkspaceOps ? 'templates' : null,
      canManageWorkspaceOps ? 'inbox' : null,
      canManageWorkspaceOps ? 'governanca' : null,
      canViewStorage ? 'arquivos' : null,
      canManageIntegrations ? 'integracoes' : null,
      canManageNotifications ? 'notificacoes' : null,
      'personalizacao',
      canManageSupportAccess ? 'acesso-suporte' : null,
      'seguranca',
    ].filter(Boolean) as string[],
    [
      canManageIntegrations,
      canManageNotifications,
      canManageUsers,
      canManageWorkspaceOps,
      canManageSupportAccess,
      canViewBilling,
      canViewRoles,
      canViewStorage,
    ],
  );

  const defaultTab = visibleTabs[0] ?? 'operacao';
  const flowOptions = useMemo(
    () =>
      (operationsQuery.data?.areaActivations ?? [])
        .flatMap((area) => area.flowOptions)
        .filter((flow, index, current) => current.findIndex((item) => item.id === flow.id) === index),
    [operationsQuery.data?.areaActivations],
  );

  const handleSaveUser = async (user: AdminUser) => {
    try {
      await saveAdminUser(user);
      const [usersResponse, workspaceResponse] = await Promise.all([
        fetchTeamMembers(),
        fetchWorkspaceSettings(),
      ]);
      setWorkspaceSettings(workspaceResponse);
      setUsers(usersResponse);
      toast({ title: 'Equipe atualizada.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar o usuário.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar equipe', description: message });
      throw error;
    }
  };

  const refreshTeam = async () => {
    const [usersResponse, workspaceResponse] = await Promise.all([
      fetchTeamMembers(),
      fetchWorkspaceSettings(),
    ]);
    setWorkspaceSettings(workspaceResponse);
    setUsers(usersResponse);
  };

  const handleResendInvitation = async (invitationId: string) => {
    try {
      await resendTeamInvitation(invitationId);
      await refreshTeam();
      toast({ title: 'Convite local rotacionado e reenviado.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível reenviar o convite.';
      toast({ variant: 'destructive', title: 'Reenvio não concluído', description: message });
      throw error;
    }
  };

  const handleRevokeInvitation = async (invitationId: string) => {
    try {
      await revokeTeamInvitation(invitationId);
      await refreshTeam();
      toast({ title: 'Convite revogado.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível revogar o convite.';
      toast({ variant: 'destructive', title: 'Revogação não concluída', description: message });
      throw error;
    }
  };

  const handleSaveAutomation = async (value: WorkspaceAutomationSettings) => {
    try {
      const saved = await saveWorkspaceAutomationSettings(value);
      setAutomationSettings(saved);
      toast({ title: 'Fluxo operacional atualizado.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar a operação.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar operação', description: message });
    }
  };

  const handleSaveNotifications = async (value: NotificationSettingsProfile) => {
    try {
      const saved = await saveAdminControlPlane({ notifications: value });
      setControlPlane(saved);
      toast({ title: 'Notificações atualizadas.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar as notificações.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar notificações', description: message });
    }
  };

  const handleSaveMeeting = async (value: Partial<Meeting> & { id?: string }) => {
    try {
      await saveMeeting(value);
      await meetingsQuery.refetch();
      toast({ title: 'Agenda atualizada.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar a reunião.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar reunião', description: message });
    }
  };

  const handleDeleteMeeting = async (meetingId: string, expectedVersion: number) => {
    try {
      await deleteMeeting(meetingId, expectedVersion);
      await meetingsQuery.refetch();
      toast({ title: 'Reunião removida.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível remover a reunião.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao remover reunião', description: message });
    }
  };

  const handleSaveBranding = async (value: BrandingSettingsProfile) => {
    try {
      const saved = await saveAdminControlPlane({ branding: value });
      setControlPlane(saved);
      toast({ title: 'Personalização atualizada.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar a personalização.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar personalização', description: message });
    }
  };

  const handleSaveSecurity = async (value: SecurityAuditSettingsProfile) => {
    try {
      const saved = await saveAdminControlPlane({ securityAudit: value });
      setControlPlane(saved);
      toast({ title: 'Segurança atualizada.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar a segurança.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar segurança', description: message });
    }
  };

  const handleSaveIntegration = async (payload: Partial<IntegrationConnection> & { id?: string }) => {
    try {
      const saved = await saveFinanceIntegration(payload);
      setIntegrations((current) => {
        const existingIndex = current.findIndex((item) => item.id === saved.id);
        if (existingIndex < 0) return [saved, ...current];
        return current.map((item, index) => index === existingIndex ? saved : item);
      });
      toast({ title: 'Integração salva no workspace.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Não foi possível salvar a integração.';
      setSurfaceError(message);
      toast({ variant: 'destructive', title: 'Erro ao salvar integração', description: message });
      throw error;
    }
  };

  const requestedTab = searchParams.get('tab');
  const checkoutCancelled = searchParams.get('checkout') === 'cancelled';
  const activeTab = requestedTab && visibleTabs.includes(requestedTab) ? requestedTab : defaultTab;
  const changeTab = (value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', value);
    setSearchParams(next, { replace: true });
  };

  return (
    <div className={embedded ? 'w-full space-y-6' : 'max-w-4xl space-y-6'}>
      {!embedded && <SectionHeader title="Configurações" description="Central operacional do workspace para equipe, IA plug and play, integrações e governança diária" />}

      {checkoutCancelled && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Checkout cancelado</AlertTitle>
          <AlertDescription>
            Nenhuma nova contratação foi confirmada. Revise a assinatura e tente novamente quando quiser.
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">Plano do workspace</CardTitle>
          <CardDescription>
            Limites sempre visíveis para orientar equipe, IA, canais conectados e automações disponíveis no plano atual.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[
            {
              label: 'Usuários',
              used: planSummaryQuery.data?.seatsUsed ?? 0,
              max: planSummaryQuery.data?.maxUsers ?? null,
              remaining: planSummaryQuery.data?.seatsRemaining ?? null,
            },
            {
              label: 'Agentes IA',
              used: planSummaryQuery.data?.aiAgentsUsed ?? 0,
              max: planSummaryQuery.data?.maxAiAgents ?? null,
              remaining: planSummaryQuery.data?.aiAgentsRemaining ?? null,
            },
            {
              label: 'WhatsApps',
              used: planSummaryQuery.data?.connectedWhatsappsUsed ?? 0,
              max: planSummaryQuery.data?.maxConnectedWhatsapps ?? null,
              remaining: planSummaryQuery.data?.connectedWhatsappsRemaining ?? null,
            },
            {
              label: 'Conversas IA',
              used: planSummaryQuery.data?.aiConversationsUsedThisMonth ?? 0,
              max: planSummaryQuery.data?.maxAiConversationsPerMonth ?? null,
              remaining: planSummaryQuery.data?.aiConversationsRemainingThisMonth ?? null,
              unit: 'count',
            },
            {
              label: 'Áudio IA',
              used: planSummaryQuery.data?.audioMinutesUsedThisMonth ?? 0,
              max: planSummaryQuery.data?.maxAudioMinutesPerMonth ?? null,
              remaining: planSummaryQuery.data?.audioMinutesRemainingThisMonth ?? null,
              unit: 'minutes',
            },
            {
              label: 'Armazenamento',
              used: planSummaryQuery.data?.storageBytesUsed ?? 0,
              max: planSummaryQuery.data?.maxStorageBytes ?? null,
              remaining: planSummaryQuery.data?.storageBytesRemaining ?? null,
              unit: 'bytes',
            },
            {
              label: 'Arquivos',
              used: planSummaryQuery.data?.filesUsed ?? 0,
              max: planSummaryQuery.data?.maxFiles ?? null,
              remaining: planSummaryQuery.data?.filesRemaining ?? null,
              unit: 'count',
            },
            {
              label: 'Automações',
              used: planSummaryQuery.data?.automationsUsed ?? 0,
              max: planSummaryQuery.data?.automationLimit ?? null,
              remaining: planSummaryQuery.data?.automationsRemaining ?? null,
            },
            {
              label: 'Alertas extras',
              used: planSummaryQuery.data?.operationalRecipientsUsed ?? 0,
              max: planSummaryQuery.data?.maxOperationalRecipients ?? null,
              remaining: planSummaryQuery.data?.operationalRecipientsRemaining ?? null,
            },
          ].map((item) => {
            const formatMetric = (value: number) => {
              if (item.unit === 'bytes') {
                return new Intl.NumberFormat('pt-BR', {
                  style: 'unit',
                  unit: 'gigabyte',
                  maximumFractionDigits: 2,
                }).format(value / 1_073_741_824);
              }
              const formatted = new Intl.NumberFormat('pt-BR').format(value);
              return item.unit === 'minutes' ? `${formatted} min` : formatted;
            };
            return (
            <div key={item.label} className="border-l-2 border-primary/25 pl-3">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
              <p className="mt-2 text-2xl font-bold tabular-nums">
                {item.max == null ? formatMetric(item.used) : `${formatMetric(item.used)} / ${formatMetric(item.max)}`}
              </p>
              <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                {item.max == null ? 'Ilimitado no plano atual.' : `${formatMetric(item.remaining ?? 0)} restantes.`}
              </p>
            </div>
            );
          })}
          </div>
          <div className="flex flex-wrap gap-2 border-t pt-4">
            {[
              ['Assinatura digital', planSummaryQuery.data?.hasDigitalSignature],
              ['Sincronização externa de calendário', planSummaryQuery.data?.hasCalendar],
              ['E-mail', planSummaryQuery.data?.hasEmail],
              ['Webhooks', planSummaryQuery.data?.hasWebhooks],
              ['Auditoria avançada', planSummaryQuery.data?.hasAdvancedAudit],
              ['Gerente de sucesso', planSummaryQuery.data?.hasSuccessManager],
            ].map(([label, enabled]) => (
              <Badge key={String(label)} variant={enabled ? 'default' : 'outline'} className="gap-1.5">
                {enabled
                  ? <CheckCircle2 className="h-3.5 w-3.5" />
                  : <XCircle className="h-3.5 w-3.5" />}
                {label}
              </Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {onboardingQuery.data && !onboardingQuery.data.onboardingCompleted && (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader>
            <CardTitle className="text-lg font-semibold">Onboarding guiado ainda pendente</CardTitle>
            <CardDescription>
              O app está liberado parcialmente, mas a ativação principal depende do WhatsApp, da biblioteca de áreas e da revisão final.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Progress value={onboardingQuery.data.onboardingProgress} className="h-2" />
            <div className="flex flex-col gap-2 text-sm text-muted-foreground md:flex-row md:items-center md:justify-between">
              <span className="tabular-nums">
                {onboardingQuery.data.onboardingProgress}% concluído · Próxima etapa: {' '}
                <span className="font-medium text-foreground">
                  {onboardingQuery.data.steps.find((step) => step.id === onboardingQuery.data?.nextOnboardingStep)?.title ?? 'Revisão final'}
                </span>
              </span>
              <Button asChild size="sm">
                <Link to="/onboarding">Retomar onboarding</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {canViewAiCosts && (
        <AiCostUsageSummary
          value={aiCostQuery.data ?? null}
          loading={aiCostQuery.isLoading}
          error={aiCostQuery.isError}
          onOpen={() => changeTab('ia')}
          onRetry={() => { void aiCostQuery.refetch(); }}
        />
      )}

      {surfaceError && (
        <Card>
          <CardContent className="py-4 text-sm text-destructive">{surfaceError}</CardContent>
        </Card>
      )}

      <Tabs value={activeTab} onValueChange={changeTab}>
        <TabsList className="flex h-auto flex-wrap justify-start gap-2 bg-transparent p-0">
          <TabsTrigger value="operacao">Operação</TabsTrigger>
          {visibleTabs.includes('assinatura') && <TabsTrigger value="assinatura">Assinatura</TabsTrigger>}
          {visibleTabs.includes('equipe') && <TabsTrigger value="equipe">Equipe & permissões</TabsTrigger>}
          <TabsTrigger value="ia">IA e tokens</TabsTrigger>
          {visibleTabs.includes('templates') && <TabsTrigger value="templates">Templates</TabsTrigger>}
          {visibleTabs.includes('inbox') && <TabsTrigger value="inbox">Inbox comercial</TabsTrigger>}
          {visibleTabs.includes('governanca') && <TabsTrigger value="governanca">Governança</TabsTrigger>}
          {visibleTabs.includes('arquivos') && <TabsTrigger value="arquivos">Arquivos</TabsTrigger>}
          {visibleTabs.includes('integracoes') && <TabsTrigger value="integracoes">Integrações</TabsTrigger>}
          {visibleTabs.includes('notificacoes') && <TabsTrigger value="notificacoes">Notificações</TabsTrigger>}
          {visibleTabs.includes('personalizacao') && <TabsTrigger value="personalizacao">Personalização</TabsTrigger>}
          {visibleTabs.includes('acesso-suporte') && <TabsTrigger value="acesso-suporte">Acesso de suporte</TabsTrigger>}
          {visibleTabs.includes('seguranca') && <TabsTrigger value="seguranca">Segurança & auditoria</TabsTrigger>}
        </TabsList>

        <TabsContent value="operacao" className="space-y-4 pt-4">
          <LocalProvisioningPanel readOnly={!canManageBilling} />
          <WorkspaceOperationsPanel
            value={operationsQuery.data ?? null}
            loading={operationsQuery.isLoading}
            onSave={async (value) => { await operationsMutation.mutateAsync(value); }}
          />
        </TabsContent>

        {visibleTabs.includes('assinatura') && (
          <TabsContent value="assinatura" className="space-y-4 pt-4">
            <SaasBillingPanel
              readOnly={!canManageBilling}
            />
          </TabsContent>
        )}

        {visibleTabs.includes('equipe') && (
          <TabsContent value="equipe" className="space-y-4 pt-4">
            <WorkspaceTeamPanel
              workspace={workspaceSettings}
              users={users}
              roles={roles}
              accessPackages={accessPackages}
              loading={false}
              canManageUsers={canManageUsers}
              canViewRoles={canViewRoles}
              onSaveUser={handleSaveUser}
              onResendInvitation={handleResendInvitation}
              onRevokeInvitation={handleRevokeInvitation}
              onGetInvitationLink={fetchInvitationLink}
            />
            <StaffContinuityPanel
              surface="workspace"
              users={users.map((user) => ({
                id: user.id,
                name: user.name,
                email: user.email,
                roleLabel: user.role,
                isActive: user.isActive,
              }))}
              loadingUsers={settingsSurfaceQuery.isLoading}
              canManage={canManageUsers}
              title="Continuidade da equipe do escritório"
              description="Redistribua casos, tarefas, reuniões e atendimentos durante férias, afastamentos ou saídas. A autoria original é preservada e cada item recebe uma etiqueta auditável."
            />
          </TabsContent>
        )}

        <TabsContent value="ia" className="space-y-4 pt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">Agentes de IA plug and play</CardTitle>
              <CardDescription>
                O gestor define intervenção humana, follow-up e áreas ativas. Modelos, prompts e testes com Gemma no Vertex continuam governados pelo admin.
              </CardDescription>
            </CardHeader>
          </Card>
          <PermissionGate
            module="configuracoes"
            action="edit"
            variant="readonly"
            actionLabel="configurar os modelos por tarefa"
          >
            <AiModelRoutingPanel
              value={aiModelRoutingQuery.data ?? null}
              loading={aiModelRoutingQuery.isLoading}
              error={aiModelRoutingQuery.isError ? 'O backend local não retornou o catálogo de modelos. Tente novamente após confirmar o serviço local.' : null}
              readOnly={!canManageAiCosts}
              onRetry={() => { void aiModelRoutingQuery.refetch(); }}
              onSave={async (payload) => { await aiModelRoutingMutation.mutateAsync(payload); }}
            />
          </PermissionGate>
          <PermissionGate
            module="integrations"
            action="manage"
            variant="readonly"
            actionLabel="gerir as chaves de API (BYOK)"
          >
            <AiByokKeysPanel
              value={aiCredentialsQuery.data ?? null}
              loading={canViewIntegrations && aiCredentialsQuery.isLoading}
              error={aiCredentialsQuery.isError ? 'O backend local não retornou o estado das credenciais.' : null}
              readOnly={!canManageIntegrations}
              busyProvider={aiCredentialBusyProvider}
              onRetry={() => { void aiCredentialsQuery.refetch(); }}
              onSaveKey={async (provider, apiKey) => { await aiCredentialSaveMutation.mutateAsync({ provider, apiKey }); }}
              onRemoveKey={async (provider) => { await aiCredentialRemoveMutation.mutateAsync(provider); }}
            />
          </PermissionGate>
          {canViewAiCosts && (
            <>
              <AiCostControlPanel
                value={aiCostQuery.data ?? null}
                loading={aiCostQuery.isLoading}
                error={aiCostQuery.isError ? 'O backend local não retornou a medição. Tente novamente após confirmar o serviço local.' : null}
                readOnly={!canManageAiCosts}
                onRetry={() => { void aiCostQuery.refetch(); }}
                onSave={async (payload) => { await aiCostMutation.mutateAsync(payload); }}
                tokenPackages={aiTokenPackagesQuery.data ?? null}
                tokenPackagesLoading={aiTokenPackagesQuery.isLoading}
                requestingPackageId={aiTokenPackageRequestMutation.isPending ? aiTokenPackageRequestMutation.variables ?? null : null}
                onRequestPackage={async (packageId) => { await aiTokenPackageRequestMutation.mutateAsync(packageId); }}
                showTokenPackages={canViewWorkspaceAiCosts}
              />
              {canViewWorkspaceAiCosts && can('finance', 'view') && <MetaCommercialChargePanel
                  value={metaCommercialChargesQuery.data ?? null}
                  loading={metaCommercialChargesQuery.isPending && !metaCommercialChargesQuery.isError}
                  error={metaCommercialChargesQuery.isError ? 'O resumo de mensagens oficiais não pôde ser calculado pelo backend local.' : null}
                  onRetry={() => { void metaCommercialChargesQuery.refetch(); }}
                />}
            </>
          )}
          <WorkspaceOperationsPanel
            value={operationsQuery.data ?? null}
            loading={operationsQuery.isLoading}
            onSave={async (value) => { await operationsMutation.mutateAsync(value); }}
          />
          <AutomationSettingsPanel
            value={automationSettings}
            loading={!automationSettings && canViewWorkspaceAutomation}
            onSave={canManageWorkspaceOps ? handleSaveAutomation : async () => undefined}
            readOnly={!canManageWorkspaceOps}
          />
        </TabsContent>

        {visibleTabs.includes('templates') && (
          <TabsContent value="templates" className="space-y-4 pt-4">
            <MessageTemplatesPanel
              value={messageTemplatesQuery.data ?? null}
              loading={messageTemplatesQuery.isLoading}
              readOnly={!canManageWorkspaceOps}
              onSave={async (value) => { await messageTemplatesMutation.mutateAsync(value); }}
            />
          </TabsContent>
        )}

        {visibleTabs.includes('inbox') && (
          <TabsContent value="inbox" className="space-y-4 pt-4">
            <WhatsAppAutomationRulesPanel
              value={whatsappAutomationRulesQuery.data ?? null}
              loading={whatsappAutomationRulesQuery.isLoading}
              readOnly={!canManageWorkspaceOps}
              flowOptions={flowOptions}
              onSave={async (value) => { await whatsappAutomationRulesMutation.mutateAsync(value); }}
            />
          </TabsContent>
        )}

        {visibleTabs.includes('governanca') && (
          <TabsContent value="governanca" className="space-y-4 pt-4">
            <ConfigurationGovernancePanel roles={roles} users={users} />
          </TabsContent>
        )}

        {visibleTabs.includes('arquivos') && (
          <TabsContent value="arquivos" className="space-y-4 pt-4">
            <LocalStoragePanel readOnly={!canManageWorkspaceOps} />
          </TabsContent>
        )}

        {visibleTabs.includes('integracoes') && (
          <TabsContent value="integracoes" className="space-y-4 pt-4">
            <OmnichannelConnectionsPanel readOnly={!canManageIntegrations} />
            <FinanceIntegrationsPanel
              integrations={integrations}
              catalog={integrationCatalog}
              openApiSurface={openApiSurface}
              publicApiTokens={publicApiTokens}
              publicApiLogs={publicApiLogs}
              loading={false}
              readOnly={!canManageIntegrations}
              onSave={handleSaveIntegration}
              onCreatePublicApiToken={handleCreatePublicApiToken}
              onRevokePublicApiToken={handleRevokePublicApiToken}
            />
          </TabsContent>
        )}

        {visibleTabs.includes('notificacoes') && (
          <TabsContent value="notificacoes" className="space-y-4 pt-4">
            <NotificationSettingsPanel
              value={controlPlane?.notifications ?? null}
              loading={!controlPlane}
              planSummary={planSummaryQuery.data}
              onSave={handleSaveNotifications}
              readOnly={!canManageNotifications}
            />
            <MeetingsAgendaPanel
              meetings={meetingsQuery.data ?? []}
              loading={meetingsQuery.isLoading}
              defaultRecipients={controlPlane?.notifications?.recipients ?? []}
              onSave={handleSaveMeeting}
              onDelete={handleDeleteMeeting}
              readOnly={!canManageNotifications}
            />
          </TabsContent>
        )}

        {visibleTabs.includes('personalizacao') && (
          <TabsContent value="personalizacao" className="space-y-4 pt-4">
            <PersonalPreferencesPanel />
            {canManageBranding && (
              <BrandingSettingsPanel
                value={controlPlane?.branding ?? null}
                loading={!controlPlane}
                onSave={handleSaveBranding}
                readOnly={false}
              />
            )}
          </TabsContent>
        )}

        {visibleTabs.includes('acesso-suporte') && (
          <TabsContent value="acesso-suporte" className="space-y-4 pt-4">
            <SupportAccessPanel canManage={canManageSupportAccess} />
          </TabsContent>
        )}

        {visibleTabs.includes('seguranca') && (
          <TabsContent value="seguranca" className="space-y-4 pt-4">
            <AuthSecurityPanel canAdminister={can('admin', 'manage')} />
            {canViewSecurity && (
              <SecurityAuditPanel
                value={controlPlane?.securityAudit ?? null}
                loading={!controlPlane}
                onSave={handleSaveSecurity}
                readOnly={!can('admin', 'manage')}
              />
            )}
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
