import { useEffect, useMemo, useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { roleLabel } from '@/lib/role-labels';
import { UserManagement } from '@/components/admin/UserManagement';
import { AutomationSettingsPanel } from '@/components/admin/AutomationSettingsPanel';
import { AiGovernancePanel } from '@/components/admin/AiGovernancePanel';
import { BrandingSettingsPanel } from '@/components/admin/BrandingSettingsPanel';
import { WorkspaceSetupPanel } from '@/components/admin/WorkspaceSetupPanel';
import { FinanceControlPanel } from '@/components/admin/FinanceControlPanel';
import { FinanceIntegrationsPanel } from '@/components/admin/FinanceIntegrationsPanel';
import { NotificationSettingsPanel } from '@/components/admin/NotificationSettingsPanel';
import { PracticeAreaLibraryPanel } from '@/components/admin/PracticeAreaLibraryPanel';
import { RolesPermissions } from '@/components/admin/RolesPermissions';
import { SecurityAuditPanel } from '@/components/admin/SecurityAuditPanel';
import { ErrorLogsPanel } from '@/components/admin/ErrorLogsPanel';
import { Users, Bot, Shield, Building2, Workflow, Landmark, Cable, AlertTriangle, BellRing, Palette, BrainCircuit, BookTemplate } from 'lucide-react';
import type {
  AdminControlPlane,
  AdminUser,
  AdminRole,
  AiGovernanceSettings,
  BrandingSettingsProfile,
  FinanceEntry,
  FinanceOverview,
  FinanceReminder,
  FinanceSettingsProfile,
  IntegrationCatalogProvider,
  IntegrationConnection,
  NotificationSettingsProfile,
  OpenApiSurface,
  PublicApiRequestLog,
  PracticeAreaTemplate,
  SecurityAuditSettingsProfile,
  WorkspaceAutomationSettings,
  WorkspaceAccessPackage,
  WorkspaceMembershipSummary,
  WorkspacePublicApiToken,
  WorkspaceSettingsProfile,
} from '@/types';
import { useAppStore } from '@/store/app-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  fetchAdminUsers,
  fetchAdminControlPlane,
  fetchFinanceEntries,
  fetchFinanceIntegrations,
  fetchFinanceOverview,
  fetchFinanceReminders,
  fetchFinanceSettings,
  fetchIntegrationCatalog,
  fetchOpenApiSurface,
  fetchPublicApiLogs,
  fetchPublicApiTokens,
  fetchRoleCatalog,
  fetchWorkspaceAccessPackages,
  fetchWorkspaceAutomationSettings,
  fetchWorkspaceMemberships,
  fetchWorkspaceSettings,
  mapAdminUsers,
  createPublicApiToken,
  saveFinanceEntry,
  saveFinanceIntegration,
  saveFinanceReminder,
  saveFinanceSettings,
  saveAdminControlPlane,
  saveWorkspaceSettings,
  saveWorkspaceAutomationSettings,
  saveAdminUser,
  revokePublicApiToken,
} from '@/services/admin';

export default function Admin() {
  const { state, can } = useAppStore();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [accessPackages, setAccessPackages] = useState<WorkspaceAccessPackage[]>([]);
  const [workspaceSettings, setWorkspaceSettings] = useState<WorkspaceSettingsProfile | null>(null);
  const [automationSettings, setAutomationSettings] = useState<WorkspaceAutomationSettings | null>(null);
  const [controlPlane, setControlPlane] = useState<AdminControlPlane | null>(null);
  const [financeSettings, setFinanceSettings] = useState<FinanceSettingsProfile | null>(null);
  const [financeOverview, setFinanceOverview] = useState<FinanceOverview | null>(null);
  const [financeEntries, setFinanceEntries] = useState<FinanceEntry[]>([]);
  const [financeReminders, setFinanceReminders] = useState<FinanceReminder[]>([]);
  const [financeIntegrations, setFinanceIntegrations] = useState<IntegrationConnection[]>([]);
  const [integrationCatalog, setIntegrationCatalog] = useState<IntegrationCatalogProvider[]>([]);
  const [openApiSurface, setOpenApiSurface] = useState<OpenApiSurface | null>(null);
  const [publicApiTokens, setPublicApiTokens] = useState<WorkspacePublicApiToken[]>([]);
  const [publicApiLogs, setPublicApiLogs] = useState<PublicApiRequestLog[]>([]);
  const [memberships, setMemberships] = useState<WorkspaceMembershipSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const canViewAdmin = can('admin', 'view');
  const canManageAdmin = can('admin', 'manage');
  const canManageUsers = can('users', 'manage');
  const canManageMemberships = can('memberships', 'manage');
  const canViewRoles = can('roles', 'view');
  const canViewWorkspace = can('admin', 'view');
  const canEditWorkspace = can('configuracoes', 'edit') || canManageAdmin;
  const canViewAutomation = can('admin', 'view');
  const canEditAutomation = can('configuracoes', 'edit') || canManageAdmin;
  const canViewFinance = can('finance', 'view');
  const canViewIntegrations = can('integrations', 'view');
  const canManageIntegrations = can('integrations', 'manage') || canManageAdmin;
  const canViewErrors = canManageAdmin;
  const canManageAutomations = can('automations', 'manage') || canManageAdmin;
  const canManageAgents = can('agents', 'manage') || canManageAdmin;
  const canManageNotifications = can('notifications', 'manage') || canManageAdmin;
  const canManageBranding = can('branding', 'manage') || canManageAdmin;
  const canViewSecurity = can('security', 'view') || canManageAdmin;
  const canViewAudit = can('audit', 'view') || canManageAdmin;

  const enabledTabs = useMemo(() => {
    return [
      canViewWorkspace ? 'workspace' : null,
      canViewAutomation ? 'automation' : null,
      canManageAutomations ? 'library' : null,
      canManageAgents ? 'ai' : null,
      canManageNotifications ? 'notifications' : null,
      canManageBranding ? 'branding' : null,
      canViewSecurity || canViewAudit ? 'security' : null,
      canViewFinance ? 'finance' : null,
      canViewIntegrations ? 'integrations' : null,
      canManageUsers ? 'users' : null,
      canViewRoles ? 'roles' : null,
      canViewErrors ? 'errors' : null,
    ].filter(Boolean) as Array<'workspace' | 'automation' | 'library' | 'ai' | 'notifications' | 'branding' | 'security' | 'finance' | 'integrations' | 'users' | 'roles' | 'errors'>;
  }, [
    canManageUsers,
    canManageAutomations,
    canManageAgents,
    canManageBranding,
    canManageNotifications,
    canViewAutomation,
    canViewAudit,
    canViewErrors,
    canViewFinance,
    canViewIntegrations,
    canViewRoles,
    canViewSecurity,
    canViewWorkspace,
  ]);

  const defaultTab = enabledTabs[0] ?? 'workspace';

  useEffect(() => {
    let mounted = true;

    async function loadOptional<T>(
      enabled: boolean,
      request: () => Promise<T>,
      fallback: T,
      label: string,
    ): Promise<{ value: T; error: string | null }> {
      if (!enabled) {
        return { value: fallback, error: null };
      }
      try {
        return { value: await request(), error: null };
      } catch (requestError) {
        const message = requestError instanceof Error ? requestError.message : `Falha ao carregar ${label}.`;
        return { value: fallback, error: message };
      }
    }

    async function loadAdminSurface() {
      if (!enabledTabs.length) {
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);
      const shouldLoadFinance = canViewFinance || canViewIntegrations;
      const [
        usersResult,
        membershipsResult,
        rolesResult,
        accessPackagesResult,
        workspaceResult,
        automationResult,
        controlPlaneResult,
        financeOverviewResult,
        financeSettingsResult,
        financeEntriesResult,
        financeRemindersResult,
        financeIntegrationsResult,
        integrationCatalogResult,
        openApiSurfaceResult,
        publicApiTokensResult,
        publicApiLogsResult,
      ] = await Promise.all([
        loadOptional(canManageUsers, fetchAdminUsers, [] as AdminUser[], 'usuários'),
        loadOptional(canManageMemberships, fetchWorkspaceMemberships, [] as WorkspaceMembershipSummary[], 'memberships'),
        loadOptional(canViewRoles, fetchRoleCatalog, [] as AdminRole[], 'papéis'),
        loadOptional(canManageUsers, fetchWorkspaceAccessPackages, [] as WorkspaceAccessPackage[], 'pacotes de acesso'),
        loadOptional(canViewWorkspace, fetchWorkspaceSettings, null as WorkspaceSettingsProfile | null, 'configurações do workspace'),
        loadOptional(canViewAutomation, fetchWorkspaceAutomationSettings, null as WorkspaceAutomationSettings | null, 'automações do workspace'),
        loadOptional(canManageAutomations || canManageAgents || canManageNotifications || canManageBranding || canViewSecurity || canViewAudit, fetchAdminControlPlane, null as AdminControlPlane | null, 'control plane administrativo'),
        loadOptional(shouldLoadFinance, fetchFinanceOverview, null as FinanceOverview | null, 'visão financeira'),
        loadOptional(shouldLoadFinance, fetchFinanceSettings, null as FinanceSettingsProfile | null, 'política financeira'),
        loadOptional(shouldLoadFinance, fetchFinanceEntries, [] as FinanceEntry[], 'lançamentos financeiros'),
        loadOptional(shouldLoadFinance, fetchFinanceReminders, [] as FinanceReminder[], 'lembretes financeiros'),
        loadOptional(shouldLoadFinance, fetchFinanceIntegrations, [] as IntegrationConnection[], 'integrações financeiras'),
        loadOptional(shouldLoadFinance, fetchIntegrationCatalog, [] as IntegrationCatalogProvider[], 'catálogo de integrações'),
        loadOptional(canViewIntegrations || can('integrations', 'manage'), fetchOpenApiSurface, null as OpenApiSurface | null, 'API aberta'),
        loadOptional(canViewIntegrations || can('integrations', 'manage'), fetchPublicApiTokens, [] as WorkspacePublicApiToken[], 'tokens da API externa'),
        loadOptional(canViewIntegrations || can('integrations', 'manage'), fetchPublicApiLogs, [] as PublicApiRequestLog[], 'auditoria da API externa'),
      ]);

      if (!mounted) {
        return;
      }

      setMemberships(membershipsResult.value);
      setRoles(rolesResult.value);
      setAccessPackages(accessPackagesResult.value);
      setWorkspaceSettings(workspaceResult.value);
      setAutomationSettings(automationResult.value);
      setControlPlane(controlPlaneResult.value);
      setFinanceOverview(financeOverviewResult.value);
      setFinanceSettings(financeSettingsResult.value);
      setFinanceEntries(financeEntriesResult.value);
      setFinanceReminders(financeRemindersResult.value);
      setFinanceIntegrations(financeIntegrationsResult.value);
      setIntegrationCatalog(integrationCatalogResult.value);
      setOpenApiSurface(openApiSurfaceResult.value);
      setPublicApiTokens(publicApiTokensResult.value);
      setPublicApiLogs(publicApiLogsResult.value);
      setUsers(mapAdminUsers(usersResult.value, membershipsResult.value));

      const loadErrors = [
        usersResult.error,
        membershipsResult.error,
        rolesResult.error,
        accessPackagesResult.error,
        workspaceResult.error,
        automationResult.error,
        controlPlaneResult.error,
        financeOverviewResult.error,
        financeSettingsResult.error,
        financeEntriesResult.error,
        financeRemindersResult.error,
        financeIntegrationsResult.error,
        integrationCatalogResult.error,
        openApiSurfaceResult.error,
        publicApiTokensResult.error,
        publicApiLogsResult.error,
      ].filter(Boolean);

      setError(loadErrors.length ? loadErrors[0] ?? null : null);
      if (mounted) {
        setLoading(false);
      }
    }

    void loadAdminSurface();
    return () => {
      mounted = false;
    };
  }, [
    can,
    canManageMemberships,
    canManageAutomations,
    canManageAgents,
    canManageBranding,
    canManageNotifications,
    canManageUsers,
    canViewAdmin,
    canViewAudit,
    canViewAutomation,
    canViewFinance,
    canViewIntegrations,
    canViewRoles,
    canViewSecurity,
    canViewWorkspace,
    enabledTabs.length,
  ]);

  const handleSaveUser = async (user: AdminUser) => {
    try {
      setError(null);
      await saveAdminUser(user, memberships);
      const [usersResponse, membershipsResponse] = await Promise.all([
        fetchAdminUsers(),
        fetchWorkspaceMemberships(),
      ]);
      setMemberships(membershipsResponse);
      setUsers(mapAdminUsers(usersResponse, membershipsResponse));
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar o usuário.');
    }
  };

  const handleSaveAutomation = async (settings: WorkspaceAutomationSettings) => {
    try {
      setError(null);
      const saved = await saveWorkspaceAutomationSettings(settings);
      setAutomationSettings(saved);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar as automações.');
    }
  };

  const handleSaveWorkspaceSetup = async (payload: { workspace: Partial<WorkspaceSettingsProfile>; automation: WorkspaceAutomationSettings }) => {
    try {
      setError(null);
      const [savedWorkspace, savedAutomation] = await Promise.all([
        saveWorkspaceSettings(payload.workspace),
        saveWorkspaceAutomationSettings(payload.automation),
      ]);
      setWorkspaceSettings(savedWorkspace);
      setAutomationSettings(savedAutomation);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar o setup do workspace.');
    }
  };

  const handleSavePracticeAreas = async (practiceAreas: PracticeAreaTemplate[]) => {
    try {
      setError(null);
      const saved = await saveAdminControlPlane({ practiceAreas });
      setControlPlane(saved);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar a biblioteca de áreas.');
    }
  };

  const handleSaveAiGovernance = async (aiGovernance: AiGovernanceSettings) => {
    try {
      setError(null);
      const saved = await saveAdminControlPlane({ aiGovernance });
      setControlPlane(saved);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar a governança de IA.');
    }
  };

  const handleSaveNotifications = async (notifications: NotificationSettingsProfile) => {
    try {
      setError(null);
      const saved = await saveAdminControlPlane({ notifications });
      setControlPlane(saved);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar as notificações.');
    }
  };

  const handleSaveBranding = async (branding: BrandingSettingsProfile) => {
    try {
      setError(null);
      const saved = await saveAdminControlPlane({ branding });
      setControlPlane(saved);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar a personalização.');
    }
  };

  const handleSaveSecurity = async (securityAudit: SecurityAuditSettingsProfile) => {
    try {
      setError(null);
      const saved = await saveAdminControlPlane({ securityAudit });
      setControlPlane(saved);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar a política de segurança.');
    }
  };

  const refreshFinance = async () => {
    const [overview, settingsProfile, entries, reminders, integrations, catalogResponse, openApiResponse] = await Promise.all([
      fetchFinanceOverview(),
      fetchFinanceSettings(),
      fetchFinanceEntries(),
      fetchFinanceReminders(),
      fetchFinanceIntegrations(),
      fetchIntegrationCatalog(),
      fetchOpenApiSurface(),
    ]);
    setFinanceOverview(overview);
    setFinanceSettings(settingsProfile);
    setFinanceEntries(entries);
    setFinanceReminders(reminders);
    setFinanceIntegrations(integrations);
    setIntegrationCatalog(catalogResponse);
    setOpenApiSurface(openApiResponse);
  };

  const handleSaveFinanceSettings = async (payload: Partial<FinanceSettingsProfile>) => {
    try {
      setError(null);
      await saveFinanceSettings(payload);
      await refreshFinance();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar a política financeira.');
    }
  };

  const handleSaveFinanceEntry = async (payload: Partial<FinanceEntry> & { id?: string }) => {
    try {
      setError(null);
      await saveFinanceEntry(payload);
      await refreshFinance();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar o lançamento financeiro.');
    }
  };

  const handleUpdateReminder = async (reminderId: string, payload: Partial<FinanceReminder>) => {
    try {
      setError(null);
      await saveFinanceReminder(reminderId, payload);
      await refreshFinance();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível atualizar o lembrete.');
    }
  };

  const handleSaveIntegration = async (payload: Partial<IntegrationConnection> & { id?: string }) => {
    try {
      setError(null);
      await saveFinanceIntegration(payload);
      await refreshFinance();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar a integração financeira.');
    }
  };

  const refreshPublicApiState = async () => {
    const [tokens, logs] = await Promise.all([
      fetchPublicApiTokens(),
      fetchPublicApiLogs(),
    ]);
    setPublicApiTokens(tokens);
    setPublicApiLogs(logs);
  };

  const handleCreatePublicApiToken = async (payload: {
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
  };

  const handleRevokePublicApiToken = async (tokenId: string) => {
    await revokePublicApiToken(tokenId);
    await refreshPublicApiState();
  };

  const humanCount = users.filter(u => u.type === 'human').length;
  const effectiveWorkspace = useMemo(
    () => workspaceSettings ?? state.activeWorkspace,
    [workspaceSettings, state.activeWorkspace],
  );
  const automationBots = useMemo<AdminUser[]>(() => {
    const stagePolicies = automationSettings?.stagePolicies ?? [];
    const uniquePolicies = Array.from(
      new Map(
        stagePolicies
          .filter((policy) => policy.mode !== 'human_only')
          .map((policy) => [policy.agentKey, policy]),
      ).values(),
    );
    return uniquePolicies.map((policy, index) => ({
      id: `agent:${policy.agentKey}`,
      name: policy.label.replace(/^Etapa:\s*/i, ''),
      initials: 'AI',
      email: '',
      type: 'ai',
      role: policy.agentKey.replace(/_/g, ' '),
      color: ['hsl(199 89% 48%)', 'hsl(38 92% 50%)', 'hsl(262 50% 45%)', 'hsl(155 55% 32%)'][index % 4],
      isActive: true,
      lastLogin: '-',
      roles: [],
      directPermissions: [],
      aiModel: policy.model ?? 'Padrão do workspace',
      maxActionsPerDay: policy.costProfile === 'economic' ? 500 : policy.costProfile === 'balanced' ? 200 : 80,
      requiresHumanApproval: policy.humanReviewRequired || policy.mode === 'ai_with_human_approval',
      description: policy.description,
    }));
  }, [automationSettings]);
  const displayUsers = useMemo(() => [...users, ...automationBots], [users, automationBots]);
  const botCount = automationBots.length;
  const activeCount = displayUsers.filter(u => u.isActive).length;

  if (!canViewAdmin) {
    return (
      <div className="space-y-3">
        <h1 className="text-2xl font-semibold">Administração</h1>
        <p className="text-sm text-muted-foreground">
          Seu papel atual não tem acesso à superfície administrativa deste workspace.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Administração</h1>
        <p className="text-sm text-muted-foreground">Central end-to-end para biblioteca, prompts, IA, pessoas, integrações, branding e governança do sistema</p>
      </div>

      {loading && (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            Carregando superfície administrativa do workspace...
          </CardContent>
        </Card>
      )}

      {error && (
        <Card>
          <CardContent className="py-6 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Usuários', value: humanCount, icon: Users },
          { label: 'Agentes IA', value: botCount, icon: Bot },
          { label: 'Ativos', value: activeCount, icon: Users },
          { label: 'Papéis', value: roles.length, icon: Shield },
        ].map(s => (
          <div key={s.label} className="rounded-lg border bg-card p-3 flex items-center gap-3">
            <div className="h-9 w-9 rounded-md bg-primary/10 flex items-center justify-center">
              <s.icon className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-2xl font-bold tabular-nums">{s.value}</p>
              <p className="text-xs text-muted-foreground uppercase tracking-wide">{s.label}</p>
            </div>
          </div>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg font-semibold">
            <Building2 className="h-4 w-4" />
            Workspace Ativo
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm text-muted-foreground md:grid-cols-3">
          <div>
            <p className="text-xs uppercase tracking-wide">Nome</p>
            <p className="font-medium text-foreground">{effectiveWorkspace?.name ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide">Slug</p>
            <p className="font-medium text-foreground">{effectiveWorkspace?.slug ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide">Papel ativo</p>
            <p className="font-medium text-foreground">{roleLabel(state.session?.activeRole)}</p>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue={defaultTab}>
        <TabsList>
          {canViewWorkspace && <TabsTrigger value="workspace" className="gap-1.5"><Building2 className="h-3.5 w-3.5" /> Workspace</TabsTrigger>}
          {canViewAutomation && <TabsTrigger value="automation" className="gap-1.5"><Workflow className="h-3.5 w-3.5" /> Operação guiada</TabsTrigger>}
          {canManageAutomations && <TabsTrigger value="library" className="gap-1.5"><BookTemplate className="h-3.5 w-3.5" /> Biblioteca</TabsTrigger>}
          {canManageAgents && <TabsTrigger value="ai" className="gap-1.5"><BrainCircuit className="h-3.5 w-3.5" /> IA governada</TabsTrigger>}
          {canManageNotifications && <TabsTrigger value="notifications" className="gap-1.5"><BellRing className="h-3.5 w-3.5" /> Notificações</TabsTrigger>}
          {canManageBranding && <TabsTrigger value="branding" className="gap-1.5"><Palette className="h-3.5 w-3.5" /> Personalização</TabsTrigger>}
          {(canViewSecurity || canViewAudit) && <TabsTrigger value="security" className="gap-1.5"><Shield className="h-3.5 w-3.5" /> Segurança</TabsTrigger>}
          {canViewFinance && <TabsTrigger value="finance" className="gap-1.5"><Landmark className="h-3.5 w-3.5" /> Financeiro</TabsTrigger>}
          {canViewIntegrations && <TabsTrigger value="integrations" className="gap-1.5"><Cable className="h-3.5 w-3.5" /> Integrações</TabsTrigger>}
          {canManageUsers && <TabsTrigger value="users" className="gap-1.5"><Users className="h-3.5 w-3.5" /> Usuários</TabsTrigger>}
          {canViewRoles && <TabsTrigger value="roles" className="gap-1.5"><Shield className="h-3.5 w-3.5" /> Papéis</TabsTrigger>}
          {canViewErrors && <TabsTrigger value="errors" className="gap-1.5"><AlertTriangle className="h-3.5 w-3.5" /> Erros</TabsTrigger>}
        </TabsList>
        {canViewWorkspace && (
          <TabsContent value="workspace" className="mt-4">
            <WorkspaceSetupPanel
              workspace={workspaceSettings}
              automation={automationSettings}
              loading={loading}
              onSave={canEditWorkspace ? handleSaveWorkspaceSetup : async () => undefined}
              readOnly={!canEditWorkspace}
            />
          </TabsContent>
        )}
        {canViewAutomation && (
          <TabsContent value="automation" className="mt-4">
            <AutomationSettingsPanel
              value={automationSettings}
              loading={loading}
              onSave={canEditAutomation ? handleSaveAutomation : async () => undefined}
              readOnly={!canEditAutomation}
            />
          </TabsContent>
        )}
        {canManageAutomations && (
          <TabsContent value="library" className="mt-4">
            <PracticeAreaLibraryPanel
              value={controlPlane?.practiceAreas ?? []}
              loading={loading}
              onSave={handleSavePracticeAreas}
              readOnly={!canManageAutomations}
            />
          </TabsContent>
        )}
        {canManageAgents && (
          <TabsContent value="ai" className="mt-4">
            <AiGovernancePanel
              value={controlPlane?.aiGovernance ?? null}
              loading={loading}
              onSave={handleSaveAiGovernance}
              readOnly={!canManageAgents}
            />
          </TabsContent>
        )}
        {canManageNotifications && (
          <TabsContent value="notifications" className="mt-4">
            <NotificationSettingsPanel
              value={controlPlane?.notifications ?? null}
              loading={loading}
              onSave={handleSaveNotifications}
              readOnly={!canManageNotifications}
            />
          </TabsContent>
        )}
        {canManageBranding && (
          <TabsContent value="branding" className="mt-4">
            <BrandingSettingsPanel
              value={controlPlane?.branding ?? null}
              loading={loading}
              onSave={handleSaveBranding}
              readOnly={!canManageBranding}
            />
          </TabsContent>
        )}
        {(canViewSecurity || canViewAudit) && (
          <TabsContent value="security" className="mt-4 space-y-4">
            <SecurityAuditPanel
              value={controlPlane?.securityAudit ?? null}
              loading={loading}
              onSave={handleSaveSecurity}
              readOnly={!canManageAdmin}
            />
            {canViewErrors && <ErrorLogsPanel />}
          </TabsContent>
        )}
        {canViewFinance && (
          <TabsContent value="finance" className="mt-4">
            <FinanceControlPanel
              overview={financeOverview}
              settings={financeSettings}
              entries={financeEntries}
              reminders={financeReminders}
              loading={loading}
              onSaveSettings={handleSaveFinanceSettings}
              onSaveEntry={handleSaveFinanceEntry}
              onUpdateReminder={handleUpdateReminder}
            />
          </TabsContent>
        )}
        {canViewIntegrations && (
          <TabsContent value="integrations" className="mt-4">
            <FinanceIntegrationsPanel
              integrations={financeIntegrations}
              catalog={integrationCatalog}
              openApiSurface={openApiSurface}
              publicApiTokens={publicApiTokens}
              publicApiLogs={publicApiLogs}
              loading={loading}
              readOnly={!canManageIntegrations}
              onSave={handleSaveIntegration}
              onCreatePublicApiToken={handleCreatePublicApiToken}
              onRevokePublicApiToken={handleRevokePublicApiToken}
            />
          </TabsContent>
        )}
        {canManageUsers && (
          <TabsContent value="users" className="mt-4">
            <UserManagement users={users} roles={roles} accessPackages={accessPackages} onSave={handleSaveUser} />
          </TabsContent>
        )}
        {canViewRoles && (
          <TabsContent value="roles" className="mt-4">
            <RolesPermissions roles={roles} users={displayUsers} />
          </TabsContent>
        )}
        {canViewErrors && !(canViewSecurity || canViewAudit) && (
          <TabsContent value="errors" className="mt-4">
            <ErrorLogsPanel />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
