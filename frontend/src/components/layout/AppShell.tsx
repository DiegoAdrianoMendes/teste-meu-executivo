import { Suspense, lazy } from 'react';
import { SidebarProvider } from '@/components/ui/sidebar';
import { AppSidebar } from './AppSidebar';
import { Topbar } from './Topbar';
import { DetailDrawer } from './DetailDrawer';
import { useAppStore } from '@/store/app-context';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { useQuery } from '@tanstack/react-query';
import { fetchWorkspaceOnboarding } from '@/services/admin';
import { Link } from 'react-router-dom';
import { useViewportLockedShell } from '@/hooks/use-viewport-locked-shell';

const Dashboard = lazy(() => import('@/pages/Dashboard'));
const Kanban = lazy(() => import('@/pages/Kanban'));
const Lista = lazy(() => import('@/pages/Lista'));
const Reports = lazy(() => import('@/pages/Reports'));
const ApprovalQueue = lazy(() => import('@/pages/ApprovalQueue'));
const Settings = lazy(() => import('@/pages/Settings'));
const Mensagens = lazy(() => import('@/pages/Mensagens'));
const Campaigns = lazy(() => import('@/pages/Campaigns'));
const Admin = lazy(() => import('@/pages/Admin'));
const Automations = lazy(() => import('@/pages/Automations'));
const WhatsAppTemplates = lazy(() => import('@/pages/WhatsAppTemplates'));
const WhatsAppMetaTemplates = lazy(() => import('@/pages/WhatsAppMetaTemplates'));
const WhatsAppConnections = lazy(() => import('@/pages/WhatsAppConnections'));
const WhatsAppAutomationWorkspace = lazy(() => import('@/pages/WhatsAppAutomationWorkspace'));
const InstagramInbox = lazy(() => import('@/pages/InstagramInbox'));
const InstagramComments = lazy(() => import('@/pages/InstagramComments'));
const InstagramAutomation = lazy(() => import('@/pages/InstagramAutomation'));
const InstagramApproved = lazy(() => import('@/pages/InstagramApproved'));
const InstagramConnections = lazy(() => import('@/pages/InstagramConnections'));
const InstagramEntryPoints = lazy(() => import('@/pages/InstagramEntryPoints'));
const MessengerInbox = lazy(() => import('@/pages/MessengerInbox'));
const MessengerComments = lazy(() => import('@/pages/MessengerComments'));
const MessengerAutomation = lazy(() => import('@/pages/MessengerAutomation'));
const MessengerApproved = lazy(() => import('@/pages/MessengerApproved'));
const MessengerConnections = lazy(() => import('@/pages/MessengerConnections'));
const MessengerEntryPoints = lazy(() => import('@/pages/MessengerEntryPoints'));

const moduleComponents = {
  dashboard: Dashboard,
  kanban: Kanban,
  lista: Lista,
  relatorios: Reports,
  aprovacoes: ApprovalQueue,
  configuracoes: Settings,
  mensagens: Mensagens,
  campanhas: Campaigns,
  admin: Admin,
  automacoes: Automations,
  'whatsapp-inbox': Mensagens,
  'whatsapp-campaigns': Campaigns,
  'whatsapp-templates': WhatsAppTemplates,
  'whatsapp-meta-templates': WhatsAppMetaTemplates,
  'whatsapp-automations': WhatsAppAutomationWorkspace,
  'whatsapp-connections': WhatsAppConnections,
  'instagram-inbox': InstagramInbox,
  'instagram-comments': InstagramComments,
  'instagram-automations': InstagramAutomation,
  'instagram-approved': InstagramApproved,
  'instagram-connections': InstagramConnections,
  'instagram-entrypoints': InstagramEntryPoints,
  'messenger-inbox': MessengerInbox,
  'messenger-comments': MessengerComments,
  'messenger-automations': MessengerAutomation,
  'messenger-approved': MessengerApproved,
  'messenger-connections': MessengerConnections,
  'messenger-entrypoints': MessengerEntryPoints,
} as const;

interface AppShellProps {
  module?:
    | 'mensagens'
    | 'campanhas'
    | 'admin'
    | 'automacoes'
    | 'whatsapp-inbox'
    | 'whatsapp-campaigns'
    | 'whatsapp-templates'
    | 'whatsapp-meta-templates'
    | 'whatsapp-automations'
    | 'whatsapp-connections'
    | 'instagram-inbox'
    | 'instagram-comments'
    | 'instagram-automations'
    | 'instagram-approved'
    | 'instagram-connections'
    | 'instagram-entrypoints'
    | 'messenger-inbox'
    | 'messenger-comments'
    | 'messenger-automations'
    | 'messenger-approved'
    | 'messenger-connections'
    | 'messenger-entrypoints';
}

export function AppShell({ module }: AppShellProps) {
  useViewportLockedShell();
  const { state } = useAppStore();
  const activeModule = module ?? state.activeModule;
  const ActiveModule = moduleComponents[activeModule];
  const onboardingQuery = useQuery({
    queryKey: ['workspace-onboarding-banner', state.activeWorkspace?.id],
    queryFn: fetchWorkspaceOnboarding,
    enabled: Boolean(state.activeWorkspace?.id),
    staleTime: 30_000,
  });
  const onboarding = onboardingQuery.data;

  return (
    <SidebarProvider className="h-dvh min-h-0 overflow-hidden">
      <div data-testid="legacy-app-shell" className="flex h-full min-h-0 w-full overflow-hidden bg-background">
        <AppSidebar />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <Topbar />
          <main data-testid="legacy-route-scroll" className="app-ai-surface min-h-0 flex-1 overflow-auto overscroll-contain p-4 scrollbar-thin lg:p-6">
            {onboarding && !onboarding.onboardingCompleted && (
              <Card className="mb-6 border-primary/20 bg-primary/5">
                <CardContent className="flex flex-col gap-4 py-4 lg:flex-row lg:items-center lg:justify-between">
                  <div className="space-y-2">
                    <p className="text-sm font-semibold">Onboarding do workspace ainda está em andamento</p>
                    <p className="text-sm text-muted-foreground">
                      Próxima etapa recomendada: <span className="font-medium text-foreground">{onboarding.steps.find((step) => step.id === onboarding.nextOnboardingStep)?.title ?? 'Revisar setup'}</span>.
                      {' '}O app continua liberado, mas o WhatsApp e a ativação final ainda dependem da conclusão do setup.
                    </p>
                    <Progress value={onboarding.onboardingProgress} className="h-2 max-w-xl" />
                    <p className="text-xs tabular-nums text-muted-foreground">
                      {onboarding.onboardingProgress}% concluído · Pendências obrigatórias: {onboarding.pendingRequiredSteps.length}
                    </p>
                  </div>
                  <Button asChild>
                    <Link to="/onboarding">Retomar onboarding</Link>
                  </Button>
                </CardContent>
              </Card>
            )}
            <Suspense fallback={<div className="py-10 text-sm text-muted-foreground">Carregando módulo...</div>}>
              <ActiveModule />
            </Suspense>
          </main>
        </div>
        <DetailDrawer />
      </div>
    </SidebarProvider>
  );
}
