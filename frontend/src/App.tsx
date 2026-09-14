import { Suspense, lazy } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes, Navigate, useLocation, useSearchParams } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppProvider, useAppStore } from "@/store/app-context";
import type { Permission } from "@/types";
import type { CommercialEntitlementKey } from "@/services/platform-commercial-catalog";
import { FrontendContractProviders } from "@/providers/frontend-contract-providers";
import { hasPlatformAccess } from "@/lib/platform-access";
import { requiresPasswordChange } from "@/services/auth";
import { ModuleAccessDenied } from "@/klm/components/ModuleAccessDenied";
import { Button } from "@/components/ui/button";
import { AlertTriangle, LoaderCircle, ShieldCheck } from "lucide-react";

const AppShell = lazy(() => import("@/components/layout/AppShell").then((module) => ({ default: module.AppShell })));
const Login = lazy(() => import("./pages/Login"));
const NotFound = lazy(() => import("./pages/NotFound"));
const WorkspaceSelect = lazy(() => import("./pages/WorkspaceSelect"));
const Landing = lazy(() => import("./pages/Landing"));
const Blog = lazy(() => import("./pages/Blog"));
const BlogPost = lazy(() => import("./pages/BlogPost"));
const BlogCategory = lazy(() => import("./pages/BlogCategory"));
const Checkout = lazy(() => import("./pages/Checkout"));
const ThankYou = lazy(() => import("./pages/ThankYou"));
const Termos = lazy(() => import("./pages/Termos"));
const Privacidade = lazy(() => import("./pages/Privacidade"));
const FlowBuilder = lazy(() => import("./pages/FlowBuilder"));
const Onboarding = lazy(() => import("./pages/Onboarding"))
const StatusPage = lazy(() => import("./pages/Status"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const ResetPassword = lazy(() => import("./pages/ResetPassword"));
const VerifyAccount = lazy(() => import("./pages/VerifyAccount"));
const AcceptInvitation = lazy(() => import("./pages/AcceptInvitation"));
const PublicTaskShare = lazy(() => import("./pages/PublicTaskShare"));
const KlmShell = lazy(() => import("@/klm/components/KlmShell").then((module) => ({ default: module.KlmShell })));
const KlmHomePage = lazy(() => import("@/klm/pages/HomePage"));
const KlmConversationsPage = lazy(() => import("@/klm/pages/ConversationsPage"));
const KlmGroupsPage = lazy(() => import("@/klm/pages/GroupsPage"));
const KlmBroadcastsPage = lazy(() => import("./pages/Campaigns"));
const KlmWhatsAppMetaTemplatesPage = lazy(() => import("./pages/WhatsAppMetaTemplates"));
const KlmLeadPage = lazy(() => import("@/klm/pages/LeadPage"));
const KlmFlowsPage = lazy(() => import("@/klm/pages/FlowsPage"));
const KlmKpisPage = lazy(() => import("@/klm/pages/KpisPage"));
const KlmDemandasPage = lazy(() => import("@/klm/pages/DemandasPage"));
const KlmDemandaDetailPage = lazy(() => import("@/klm/pages/DemandaDetailPage"));
const KlmProcessosPage = lazy(() => import("@/klm/pages/ProcessosPage"));
const KlmPrazosPage = lazy(() => import("@/klm/pages/PrazosPage"));
const KlmToolsPage = lazy(() => import("@/klm/pages/ToolsPage"));
const KlmAreasPage = lazy(() => import("@/klm/pages/AreasPage"));
const KlmAgentsPage = lazy(() => import("@/klm/pages/AgentsPage"));
const KlmExecutivesPage = lazy(() => import("@/klm/pages/ExecutivesPage"));
const KlmTiAgentPage = lazy(() => import("@/klm/pages/TiAgentPage"));
const KlmSocialCommentsPage = lazy(() => import("@/klm/pages/SocialCommentsPage"));
const KlmAudioPage = lazy(() => import("@/klm/pages/AudioPage"));
const KlmFunnelPage = lazy(() => import("@/klm/pages/FunnelPage"));
const KlmQualityPage = lazy(() => import("@/klm/pages/QualityPage"));
const KlmTrackingPage = lazy(() => import("@/klm/pages/TrackingPage"));
const KlmIntegrationsPage = lazy(() => import("@/klm/pages/IntegrationsPage"));
const KlmConnectionsHubPage = lazy(() => import("@/klm/pages/ConnectionsHubPage"));
const KlmMetaWebhooksPage = lazy(() => import("@/klm/pages/MetaWebhooksPage"));
const KlmMetaOAuthCallbackPage = lazy(() => import("@/klm/pages/MetaOAuthCallbackPage"));
const KlmPaidAcquisitionPage = lazy(() => import("@/klm/pages/PaidAcquisitionPage"));
const KlmAgendaPage = lazy(() => import("@/klm/pages/AgendaPage"));
const KlmTasksPage = lazy(() => import("@/klm/pages/TasksPage"));
const KlmAgendaSettingsPage = lazy(() => import("@/klm/pages/AgendaSettingsPage"));
const KlmPublicBookingPage = lazy(() => import("@/klm/pages/PublicBookingPage"));
const KlmOperationalModulePage = lazy(() => import("@/klm/pages/OperationalModulePage"));
const KlmWorkspaceSettingsPage = lazy(() => import("./pages/Settings"));
const KlmProposalPreviewPage = lazy(() => import("@/klm/pages/ProposalPreviewPage"));
const KlmNativeCapabilityPage = lazy(() => import("@/klm/pages/NativeCapabilityPage"));
const KlmFunctionUnitInventoryPage = lazy(() => import("@/klm/pages/FunctionUnitInventoryPage"));
const KlmPreferencesPage = lazy(() => import("@/klm/pages/PreferencesPage"));
const KlmPlaybooksPage = lazy(() => import("@/klm/pages/PlaybooksPage"));
const KlmPlaybookEditorPage = lazy(() => import("@/klm/pages/PlaybookEditorPage"));
const KlmKnowledgePage = lazy(() => import("@/klm/pages/KnowledgePage"));
const KlmBenefitsPage = lazy(() => import("@/klm/pages/BenefitsPage"));
const KlmMigracaoPage = lazy(() => import("@/klm/pages/MigracaoPage"));
const ForcePasswordChange = lazy(() => import("./pages/ForcePasswordChange"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60 * 1000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

function RouteFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground">
      Carregando interface...
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { state } = useAppStore();
  const location = useLocation();
  if (!state.isAuthenticated) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  if (state.session?.surface !== 'workspace' || hasPlatformAccess(state.currentUser)) {
    return <SurfaceAccessDenied expected="workspace" />;
  }
  if (!state.activeWorkspace) {
    return <Navigate to="/workspace/select" state={{ from: location.pathname }} replace />;
  }
  if (requiresPasswordChange(state.currentUser, state.session)) {
    return <ForcePasswordChange />;
  }
  return <FrontendContractProviders>{children}</FrontendContractProviders>;
}

function SurfaceAccessDenied({ expected }: { expected: 'workspace' | 'platform' }) {
  const { logout } = useAppStore();
  return (
    <main className="grid min-h-screen place-items-center bg-background px-6">
      <section className="w-full max-w-md rounded-lg border bg-card p-6 text-center shadow-sm">
        <h1 className="text-lg font-semibold">Sessão incompatível com este ambiente</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {expected === 'platform'
            ? 'A Central Blackbox aceita somente identidades da equipe Meu Executivo.'
            : 'O aplicativo aceita somente usuários vinculados a uma conta contratante.'}
        </p>
        <Button className="mt-5" onClick={() => { void logout(); }}>
          Encerrar esta sessão
        </Button>
      </section>
    </main>
  );
}

function RuntimeSurfaceBoundary() {
  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 px-6 text-slate-50">
      <section className="w-full max-w-lg rounded-lg border border-slate-800 bg-slate-900 p-7 text-center shadow-xl">
        <ShieldCheck className="mx-auto h-8 w-8 text-blue-400" />
        <h1 className="mt-4 text-lg font-semibold">Ambiente separado por segurança</h1>
        <p className="mt-2 text-sm leading-6 text-slate-300">
          O painel interno da equipe Meu Executivo não é servido no endereço usado pelas
          contas contratantes.
        </p>
        <p className="mt-4 text-xs text-slate-500">
          Nenhuma sessão foi transferida ou reaproveitada.
        </p>
      </section>
    </main>
  );
}

function RuntimeSurfaceGuard({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  if (location.pathname === '/blackbox' || location.pathname.startsWith('/blackbox/')) {
    return <RuntimeSurfaceBoundary />;
  }
  return <>{children}</>;
}

function AuthRoute({ audience }: { audience: 'workspace' | 'platform' }) {
  const { state } = useAppStore();
  const location = useLocation();
  const defaultRoute = audience === 'platform' ? '/blackbox' : '/app';
  const from = (location.state as { from?: string })?.from || defaultRoute;
  if (state.isAuthenticated) {
    if (state.session?.surface !== audience) {
      return <SurfaceAccessDenied expected={audience} />;
    }
    if (audience === 'platform') {
      return <Navigate to={from.startsWith('/blackbox') ? from : '/blackbox'} replace />;
    }
    if (!state.activeWorkspace) {
      return <Navigate to="/workspace/select" state={{ from }} replace />;
    }
    return <Navigate to={from.startsWith('/app') ? from : '/app'} replace />;
  }
  return <Login audience={audience} />;
}

function WorkspaceRoute() {
  const { state } = useAppStore();
  if (!state.isAuthenticated) return <Navigate to="/login" replace />;
  if (state.session?.surface !== 'workspace') return <SurfaceAccessDenied expected="workspace" />;
  if (requiresPasswordChange(state.currentUser, state.session)) {
    return <ForcePasswordChange />;
  }
  return <WorkspaceSelect />;
}

function PermissionRoute({
  children,
  module,
  action = 'view',
  capability,
}: {
  children: React.ReactNode;
  module: Permission['module'];
  action?: Permission['action'];
  capability?: CommercialEntitlementKey;
}) {
  const {
    can,
    capabilityManifestStatus,
    getCapability,
    isCapabilityVisible,
  } = useAppStore();
  if (!can(module, action)) {
    // Rota direta (deep link, favorito, digitação manual) para um módulo fora do
    // pacote de acesso: nunca redireciona em silêncio — mostra o empty-state
    // explicando o motivo e oferecendo "Solicitar acesso" ao administrador.
    return (
      <div className="flex min-h-[70vh] items-center justify-center px-4">
        <ModuleAccessDenied module={module} />
      </div>
    );
  }
  if (capability) {
    if (capabilityManifestStatus === 'loading' || capabilityManifestStatus === 'idle') {
      return (
        <div className="flex min-h-[70vh] items-center justify-center px-4" role="status" aria-live="polite">
          <div className="rounded-lg border bg-card px-6 py-5 text-center shadow-sm">
            <LoaderCircle className="mx-auto h-6 w-6 animate-spin text-primary" />
            <p className="mt-3 text-sm font-medium">Validando recursos contratados</p>
          </div>
        </div>
      );
    }
    if (capabilityManifestStatus !== 'ready') {
      const notConfigured = capabilityManifestStatus === 'not_configured';
      return (
        <div className="flex min-h-[70vh] items-center justify-center px-4">
          <section className="w-full max-w-xl rounded-lg border bg-card p-7 text-center shadow-sm" role="alert">
            <AlertTriangle className="mx-auto h-7 w-7 text-amber-600" />
            <h2 className="mt-4 text-base font-semibold">
              {notConfigured ? 'Política comercial não configurada' : 'Não foi possível validar o plano'}
            </h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              {notConfigured
                ? 'A conta precisa de um contrato canônico antes que este módulo seja liberado.'
                : capabilityManifestStatus === 'forbidden'
                  ? 'A sessão atual não pode consultar as capacidades desta conta.'
                  : 'A consulta está indisponível. Nenhum recurso foi presumido como ativo.'}
            </p>
            <Button
              type="button"
              variant="outline"
              className="mt-5"
              onClick={() => window.dispatchEvent(new CustomEvent('meu-executivo:support:open'))}
            >
              Falar com o suporte
            </Button>
          </section>
        </div>
      );
    }
    if (!isCapabilityVisible(capability)) {
      const resolved = getCapability(capability);
      return (
        <div className="flex min-h-[70vh] items-center justify-center px-4">
          <section className="w-full max-w-xl rounded-lg border bg-card p-7 text-center shadow-sm">
            <ShieldCheck className="mx-auto h-7 w-7 text-primary" />
            <h2 className="mt-4 text-base font-semibold">Recurso não contratado</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Este módulo não faz parte da configuração atual da conta.
              {resolved?.upgradeOffer ? ` Opção indicada: ${resolved.upgradeOffer}.` : ''}
            </p>
            <Button
              type="button"
              variant="outline"
              className="mt-5"
              onClick={() => window.dispatchEvent(new CustomEvent('meu-executivo:support:open'))}
            >
              Consultar upgrade
            </Button>
          </section>
        </div>
      );
    }
  }
  return <>{children}</>;
}

type LegacyInboxChannel = 'whatsapp' | 'instagram' | 'messenger';

export function LegacyInboxRedirect({ channel }: { channel: LegacyInboxChannel }) {
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);
  searchParams.set('channel', channel);

  return (
    <Navigate
      to={{
        pathname: '/app/conversas',
        search: `?${searchParams.toString()}`,
        hash: location.hash,
      }}
      replace
    />
  );
}

export function CanonicalConversationRedirect() {
  const location = useLocation();

  return (
    <Navigate
      to={{
        pathname: '/app/conversas',
        search: location.search,
        hash: location.hash,
      }}
      replace
    />
  );
}

export function CanonicalMetaTemplatesRedirect() {
  const location = useLocation();

  return (
    <Navigate
      to={{
        pathname: '/app/templates-meta',
        search: location.search,
        hash: location.hash,
      }}
      replace
    />
  );
}

function WorkspaceExperience() {
  return <KlmShell />;
}

/**
 * Flows is a native KLM surface. The sequence picker can hand off a draft to
 * the existing FlowBuilder, but it must remain inside the workspace shell so
 * the operator keeps the workspace navigation and contextual query intact.
 */
function KlmFlowsRoute() {
  const [searchParams] = useSearchParams();
  const openBuilder = searchParams.get('sequence_draft') || searchParams.get('builder') === '1';
  return openBuilder ? <FlowBuilder /> : <KlmFlowsPage />;
}

function RootRoute() {
  const { state } = useAppStore();
  if (!state.isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <Navigate to="/app" replace />;
}

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <AppProvider>
        <BrowserRouter>
          <RuntimeSurfaceGuard>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
              <Route path="/" element={<RootRoute />} />
              <Route path="/landing" element={<Landing />} />
              <Route path="/blog" element={<Blog />} />
              <Route path="/blog/categoria/:slug" element={<BlogCategory />} />
              <Route path="/blog/:slug" element={<BlogPost />} />
              <Route path="/checkout" element={<Checkout />} />
              <Route path="/obrigado" element={<ThankYou />} />
              <Route path="/termos" element={<Termos />} />
              <Route path="/privacidade" element={<Privacidade />} />
              <Route path="/status" element={<StatusPage />} />
              <Route path="/login" element={<AuthRoute audience="workspace" />} />
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/reset-password" element={<ResetPassword />} />
              <Route path="/verify-account" element={<VerifyAccount />} />
              <Route path="/accept-invitation/:invitationId" element={<AcceptInvitation />} />
              <Route path="/task-share" element={<PublicTaskShare />} />
              <Route path="/proposta/:matterId" element={<ProtectedRoute><KlmProposalPreviewPage /></ProtectedRoute>} />
              <Route path="/agendar/:token" element={<KlmPublicBookingPage />} />
              <Route path="/workspace/select" element={<WorkspaceRoute />} />
              <Route path="/mensagens" element={<Navigate to="/app/conversas" replace />} />
              <Route path="/campanhas" element={<Navigate to="/app/disparos" replace />} />
              <Route path="/whatsapp/inbox" element={<LegacyInboxRedirect channel="whatsapp" />} />
              <Route path="/whatsapp/campaigns" element={<Navigate to="/app/disparos" replace />} />
              <Route path="/whatsapp/templates" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="whatsapp-templates" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/whatsapp/meta-templates" element={<CanonicalMetaTemplatesRedirect />} />
              <Route path="/whatsapp/automations" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="whatsapp-automations" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/whatsapp/connections" element={<ProtectedRoute><PermissionRoute module="mensagens" action="view"><AppShell module="whatsapp-connections" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/instagram/inbox" element={<LegacyInboxRedirect channel="instagram" />} />
              <Route path="/instagram/comments" element={<ProtectedRoute><PermissionRoute module="mensagens" action="view"><AppShell module="instagram-comments" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/instagram/automation" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="instagram-automations" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/instagram/approved-messages" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="instagram-approved" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/instagram/connections" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="instagram-connections" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/instagram/entry-points" element={<ProtectedRoute><PermissionRoute module="mensagens" action="view"><AppShell module="instagram-entrypoints" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/messenger/inbox" element={<LegacyInboxRedirect channel="messenger" />} />
              <Route path="/messenger/comments" element={<ProtectedRoute><PermissionRoute module="mensagens" action="view"><AppShell module="messenger-comments" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/messenger/automation" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="messenger-automations" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/messenger/approved-messages" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="messenger-approved" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/messenger/connections" element={<ProtectedRoute><PermissionRoute module="configuracoes" action="view"><AppShell module="messenger-connections" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/messenger/entry-points" element={<ProtectedRoute><PermissionRoute module="mensagens" action="view"><AppShell module="messenger-entrypoints" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/adm" element={<ProtectedRoute><PermissionRoute module="admin" action="view"><AppShell module="admin" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/automacoes" element={<ProtectedRoute><PermissionRoute module="automations" action="manage"><AppShell module="automacoes" /></PermissionRoute></ProtectedRoute>} />
              <Route path="/automacoes/editor" element={<ProtectedRoute><PermissionRoute module="automations" action="manage"><FlowBuilder /></PermissionRoute></ProtectedRoute>} />
              <Route path="/automacoes/editor/:id" element={<ProtectedRoute><PermissionRoute module="automations" action="manage"><FlowBuilder /></PermissionRoute></ProtectedRoute>} />
              <Route path="/onboarding" element={<ProtectedRoute><Onboarding /></ProtectedRoute>} />
              <Route path="/app" element={<ProtectedRoute><WorkspaceExperience /></ProtectedRoute>}>
                <Route index element={<PermissionRoute module="dashboard"><KlmHomePage /></PermissionRoute>} />
                <Route path="conversas" element={<PermissionRoute module="mensagens"><KlmConversationsPage /></PermissionRoute>} />
                <Route path="grupos" element={<PermissionRoute module="mensagens"><KlmGroupsPage /></PermissionRoute>} />
                <Route path="disparos" element={<PermissionRoute module="mensagens" action="send_message"><KlmBroadcastsPage /></PermissionRoute>} />
                <Route path="templates-meta" element={<PermissionRoute module="mensagens"><KlmWhatsAppMetaTemplatesPage /></PermissionRoute>} />
                <Route path="equipe" element={<PermissionRoute module="mensagens"><KlmOperationalModulePage /></PermissionRoute>} />
                <Route path="leads" element={<PermissionRoute module="lista"><KlmLeadPage /></PermissionRoute>} />
                <Route path="executivos" element={<PermissionRoute module="executives"><KlmExecutivesPage /></PermissionRoute>} />
                <Route path="funil" element={<PermissionRoute module="kanban"><KlmFunnelPage /></PermissionRoute>} />
                <Route path="kpis" element={<PermissionRoute module="relatorios"><KlmKpisPage /></PermissionRoute>} />
                <Route path="rastreamento" element={<PermissionRoute module="relatorios"><KlmTrackingPage /></PermissionRoute>} />
                <Route path="integracoes" element={<PermissionRoute module="integrations"><KlmIntegrationsPage /></PermissionRoute>} />
                <Route path="conexoes" element={<PermissionRoute module="integrations"><KlmConnectionsHubPage /></PermissionRoute>} />
                <Route path="conexoes/whatsapp/webhooks" element={<PermissionRoute module="integrations"><KlmMetaWebhooksPage /></PermissionRoute>} />
                <Route path="conexoes/:channel/callback" element={<PermissionRoute module="integrations" action="manage"><KlmMetaOAuthCallbackPage /></PermissionRoute>} />
                <Route path="aquisicao-paga" element={<PermissionRoute module="integrations"><KlmPaidAcquisitionPage /></PermissionRoute>} />
                <Route path="qualidade" element={<PermissionRoute module="agents"><KlmQualityPage /></PermissionRoute>} />
                <Route path="playbooks" element={<PermissionRoute module="configuracoes"><KlmPlaybooksPage /></PermissionRoute>} />
                <Route path="playbooks/:playbookId" element={<PermissionRoute module="configuracoes"><KlmPlaybookEditorPage /></PermissionRoute>} />
                <Route path="ferramentas" element={<PermissionRoute module="integrations"><KlmToolsPage /></PermissionRoute>} />
                <Route path="ferramentas/inventario" element={<PermissionRoute module="integrations"><KlmFunctionUnitInventoryPage /></PermissionRoute>} />
                <Route path="capacidades/:domain/:capability" element={<PermissionRoute module="integrations"><KlmNativeCapabilityPage /></PermissionRoute>} />
                <Route path="flows" element={<PermissionRoute module="automations" action="manage"><KlmFlowsRoute /></PermissionRoute>} />
                <Route path="omnichannel" element={<PermissionRoute module="mensagens"><CanonicalConversationRedirect /></PermissionRoute>} />
                <Route path="comentarios" element={<PermissionRoute module="mensagens"><KlmSocialCommentsPage /></PermissionRoute>} />
                <Route path="agenda" element={<PermissionRoute module="lista"><KlmAgendaPage /></PermissionRoute>} />
                <Route path="tarefas" element={<PermissionRoute module="tasks" capability="active_tasks"><KlmTasksPage /></PermissionRoute>} />
                <Route path="agenda/configuracoes" element={<PermissionRoute module="configuracoes" action="edit"><KlmAgendaSettingsPage /></PermissionRoute>} />
                <Route path="contratos" element={<PermissionRoute module="aprovacoes"><KlmOperationalModulePage /></PermissionRoute>} />
                <Route path="financeiro" element={<PermissionRoute module="finance"><KlmOperationalModulePage /></PermissionRoute>} />
                <Route path="peticoes" element={<PermissionRoute module="lista" capability="legal_suite"><KlmOperationalModulePage /></PermissionRoute>} />
                <Route path="demandas" element={<PermissionRoute module="lista" capability="legal_suite"><KlmDemandasPage /></PermissionRoute>} />
                <Route path="demandas/:demandId" element={<PermissionRoute module="lista" capability="legal_suite"><KlmDemandaDetailPage /></PermissionRoute>} />
                <Route path="processos" element={<PermissionRoute module="lista" capability="legal_suite"><KlmProcessosPage /></PermissionRoute>} />
                <Route path="prazos" element={<PermissionRoute module="lista" capability="legal_suite"><KlmPrazosPage /></PermissionRoute>} />
                <Route path="audio" element={<PermissionRoute module="mensagens"><KlmAudioPage /></PermissionRoute>} />
                <Route path="agentes" element={<PermissionRoute module="agents"><KlmAgentsPage /></PermissionRoute>} />
                <Route path="areas" element={<PermissionRoute module="configuracoes" capability="legal_suite"><KlmAreasPage /></PermissionRoute>} />
                <Route path="ti" element={<PermissionRoute module="admin"><KlmTiAgentPage /></PermissionRoute>} />
                <Route path="configuracoes" element={<PermissionRoute module="configuracoes"><KlmOperationalModulePage /></PermissionRoute>} />
                <Route path="configuracoes/avancadas" element={<PermissionRoute module="configuracoes"><KlmWorkspaceSettingsPage embedded /></PermissionRoute>} />
                <Route path="preferencias" element={<KlmPreferencesPage />} />
                <Route path="atividade" element={<PermissionRoute module="audit"><KlmOperationalModulePage /></PermissionRoute>} />
                <Route path="aprovacoes" element={<PermissionRoute module="aprovacoes"><KlmOperationalModulePage /></PermissionRoute>} />
                <Route path="conhecimento" element={<KlmKnowledgePage />} />
                <Route path="beneficios" element={<KlmBenefitsPage />} />
                <Route path="migracao" element={<PermissionRoute module="admin"><KlmMigracaoPage /></PermissionRoute>} />
              </Route>
              <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </RuntimeSurfaceGuard>
        </BrowserRouter>
      </AppProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
