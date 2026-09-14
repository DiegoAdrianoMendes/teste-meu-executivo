import { useEffect, useState } from 'react';
import {
  Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarMenu, SidebarMenuButton, SidebarMenuItem,
  SidebarHeader, SidebarFooter, useSidebar,
} from '@/components/ui/sidebar';
import { BRAND } from '@/lib/brand';
import { DesbLogo } from '@/components/ui/DesbLogo';
import {
  LayoutDashboard, Columns3, List, BarChart3,
  ShieldCheck, Settings, Scale, MessageSquare, ShieldAlert, Activity, Eye, EyeOff, Building2, Globe, Newspaper, Images, Search, MapPinned,
  PhoneCall, Megaphone, Instagram, MessageCircleMore, Link2, Bot, FileText, Webhook,
  UsersRound, LifeBuoy, BadgeCheck,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { useAppStore } from '@/store/app-context';
import { useNavigate, useLocation } from 'react-router-dom';
import { fetchWorkspaceAiCosts, fetchWorkspaceOperations, fetchWorkspacePlanSummary } from '@/services/admin';
import { fetchPlatformAiCosts } from '@/services/platform';
import type { ModuleId, PracticeArea, WorkspaceAiTokenSummary, WorkspaceAreaActivation, WorkspacePlanSummary } from '@/types';
import { canPlatform, hasPlatformAccess } from '@/lib/platform-access';

const PLAN_VISIBILITY_KEY = 'desb_plan_visibility';

type SidebarItemId =
  | ModuleId
  | 'automacoes'
  | 'platform-site'
  | 'platform-blog'
  | 'platform-media'
  | 'platform-seo'
  | 'platform-whatsapp'
  | 'platform-google'
  | 'platform-team'
  | 'platform-support';

type SidebarItem = {
  title: string;
  moduleId: SidebarItemId;
  icon: typeof LayoutDashboard;
  badge?: 'approvals' | 'messages';
  route?: string;
  platformPermission?: { module: string; action?: string };
};

const workspaceItems: SidebarItem[] = [
  { title: 'Dashboard', moduleId: 'dashboard', icon: LayoutDashboard, route: '/app' },
  { title: 'Kanban', moduleId: 'kanban', icon: Columns3, route: '/app/funil' },
  { title: 'Lista', moduleId: 'lista', icon: List, route: '/app/leads' },
  { title: 'Relatórios', moduleId: 'relatorios', icon: BarChart3, route: '/app/kpis' },
  { title: 'Aprovações', moduleId: 'aprovacoes', icon: ShieldCheck, badge: 'approvals', route: '/app/aprovacoes' },
  { title: 'Admin', moduleId: 'admin', icon: ShieldAlert, route: '/adm' },
  { title: 'Configurações', moduleId: 'configuracoes', icon: Settings, route: '/app/configuracoes' },
];

const whatsappItems: SidebarItem[] = [
  { title: 'Inbox', moduleId: 'whatsapp-inbox', icon: MessageSquare, badge: 'messages', route: '/whatsapp/inbox' },
  { title: 'Campanhas', moduleId: 'whatsapp-campaigns', icon: Megaphone, route: '/whatsapp/campaigns' },
  { title: 'Templates Meta', moduleId: 'whatsapp-meta-templates', icon: BadgeCheck, route: '/whatsapp/meta-templates' },
  { title: 'Respostas rápidas', moduleId: 'whatsapp-templates', icon: FileText, route: '/whatsapp/templates' },
  { title: 'Automações', moduleId: 'whatsapp-automations', icon: Bot, route: '/whatsapp/automations' },
  { title: 'Conexões', moduleId: 'whatsapp-connections', icon: PhoneCall, route: '/whatsapp/connections' },
];

const instagramItems: SidebarItem[] = [
  { title: 'Social Inbox', moduleId: 'instagram-inbox', icon: Instagram, route: '/instagram/inbox' },
  { title: 'Comentários', moduleId: 'instagram-comments', icon: MessageCircleMore, route: '/instagram/comments' },
  { title: 'Automação', moduleId: 'instagram-automations', icon: Bot, route: '/instagram/automation' },
  { title: 'Mensagens Validadas', moduleId: 'instagram-approved', icon: ShieldCheck, route: '/instagram/approved-messages' },
  { title: 'Conexão', moduleId: 'instagram-connections', icon: Webhook, route: '/instagram/connections' },
  { title: 'Entradas e Links', moduleId: 'instagram-entrypoints', icon: Link2, route: '/instagram/entry-points' },
];

const messengerItems: SidebarItem[] = [
  { title: 'Inbox', moduleId: 'messenger-inbox', icon: MessageCircleMore, route: '/messenger/inbox' },
  { title: 'Comentários/Page', moduleId: 'messenger-comments', icon: LayoutDashboard, route: '/messenger/comments' },
  { title: 'Automação', moduleId: 'messenger-automations', icon: Bot, route: '/messenger/automation' },
  { title: 'Mensagens Validadas', moduleId: 'messenger-approved', icon: ShieldCheck, route: '/messenger/approved-messages' },
  { title: 'Conexão', moduleId: 'messenger-connections', icon: Webhook, route: '/messenger/connections' },
  { title: 'Entradas e Menus', moduleId: 'messenger-entrypoints', icon: Link2, route: '/messenger/entry-points' },
];

const platformItems: SidebarItem[] = [
  { title: 'Plataforma', moduleId: 'platform', icon: Building2, route: '/platform', platformPermission: { module: 'dashboard' } },
  { title: 'Equipe SaaS', moduleId: 'platform-team', icon: UsersRound, route: '/platform/team', platformPermission: { module: 'staff' } },
  { title: 'Site Público', moduleId: 'platform-site', icon: Globe, route: '/platform/site', platformPermission: { module: 'content' } },
  { title: 'Blog', moduleId: 'platform-blog', icon: Newspaper, route: '/platform/blog', platformPermission: { module: 'content' } },
  { title: 'Biblioteca de Mídia', moduleId: 'platform-media', icon: Images, route: '/platform/media', platformPermission: { module: 'content' } },
  { title: 'SEO & Indexação', moduleId: 'platform-seo', icon: Search, route: '/platform/seo', platformPermission: { module: 'content' } },
  { title: 'WhatsApps Oficiais', moduleId: 'platform-whatsapp', icon: PhoneCall, route: '/platform/official-whatsapp', platformPermission: { module: 'whatsapp' } },
  { title: 'Google Business', moduleId: 'platform-google', icon: MapPinned, route: '/platform/google-business', platformPermission: { module: 'content' } },
  { title: 'Acesso de suporte', moduleId: 'platform-support', icon: LifeBuoy, route: '/platform/support-access', platformPermission: { module: 'support_access' } },
];

export function AppSidebar() {
  const { state: sidebarState } = useSidebar();
  const collapsed = sidebarState === 'collapsed';
  const { state, getPendingApprovals, setActiveModule, getTotalUnreadMessages, canAccessModule, can, dispatch } = useAppStore();
  const navigate = useNavigate();
  const location = useLocation();
  const pendingCount = getPendingApprovals().length;
  const unreadMessages = getTotalUnreadMessages();
  const activeWorkspaceId = state.activeWorkspace?.id ?? null;
  const activeWorkspacePlanSummary = state.activeWorkspace?.planSummary ?? null;
  const [practiceAreas, setPracticeAreas] = useState<WorkspaceAreaActivation[]>([]);
  const [planSummary, setPlanSummary] = useState<WorkspacePlanSummary | null>(activeWorkspacePlanSummary);
  const [aiTokenSummary, setAiTokenSummary] = useState<WorkspaceAiTokenSummary | null>(null);
  const [limitsVisible, setLimitsVisible] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    return localStorage.getItem(PLAN_VISIBILITY_KEY) !== 'hidden';
  });

  useEffect(() => {
    if (!state.isAuthenticated || !activeWorkspaceId || !can('configuracoes', 'view')) {
      return;
    }
    void fetchWorkspaceOperations()
      .then((data) => setPracticeAreas(data.areaActivations.filter((area) => area.enabled)))
      .catch(() => setPracticeAreas([]));
  }, [activeWorkspaceId, can, state.isAuthenticated]);

  useEffect(() => {
    if (!state.isAuthenticated || !activeWorkspaceId) {
      setPlanSummary(null);
      return;
    }
    void fetchWorkspacePlanSummary()
      .then((data) => setPlanSummary(data))
      .catch(() => setPlanSummary(activeWorkspacePlanSummary));
  }, [activeWorkspaceId, activeWorkspacePlanSummary, state.isAuthenticated]);

  useEffect(() => {
    const canViewTokenUsage = ['workspace_owner', 'workspace_admin'].includes(state.session?.activeRole ?? '')
      || Boolean(state.currentUser?.isPlatformAdmin);
    if (!state.isAuthenticated || !activeWorkspaceId || !canViewTokenUsage) {
      setAiTokenSummary(null);
      return;
    }
    const summaryRequest = state.currentUser?.isPlatformAdmin
      ? fetchPlatformAiCosts(30, activeWorkspaceId)
      : fetchWorkspaceAiCosts();
    void summaryRequest
      .then((data) => setAiTokenSummary(data))
      .catch(() => setAiTokenSummary(null));
  }, [activeWorkspaceId, state.currentUser, state.isAuthenticated, state.session?.activeRole]);

  const toggleLimitsVisibility = () => {
    const next = !limitsVisible;
    setLimitsVisible(next);
    localStorage.setItem(PLAN_VISIBILITY_KEY, next ? 'visible' : 'hidden');
  };

  const limitItems = [
    {
      label: 'Usuários',
      used: planSummary?.seatsUsed ?? 0,
      max: planSummary?.maxUsers ?? null,
      remaining: planSummary?.seatsRemaining ?? null,
    },
    {
      label: 'Agentes IA',
      used: planSummary?.aiAgentsUsed ?? 0,
      max: planSummary?.maxAiAgents ?? null,
      remaining: planSummary?.aiAgentsRemaining ?? null,
    },
    {
      label: 'WhatsApps',
      used: planSummary?.connectedWhatsappsUsed ?? 0,
      max: planSummary?.maxConnectedWhatsapps ?? null,
      remaining: planSummary?.connectedWhatsappsRemaining ?? null,
    },
    {
      label: 'Automações',
      used: planSummary?.automationsUsed ?? 0,
      max: planSummary?.automationLimit ?? null,
      remaining: planSummary?.automationsRemaining ?? null,
    },
    {
      label: 'Alertas extras',
      used: planSummary?.operationalRecipientsUsed ?? 0,
      max: planSummary?.maxOperationalRecipients ?? null,
      remaining: planSummary?.operationalRecipientsRemaining ?? null,
    },
  ];

  const renderMenuGroup = (label: string, items: SidebarItem[]) => (
    <SidebarGroup>
      <SidebarGroupLabel className="text-sidebar-foreground/50 text-[10px] uppercase tracking-wider">
        {label}
      </SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            if (item.platformPermission && !canPlatform(
              state.currentUser,
              item.platformPermission.module,
              item.platformPermission.action ?? 'view',
            )) {
              return null;
            }
            if (item.moduleId === 'automacoes' && !can('automations', 'manage')) {
              return null;
            }
            if (
              ![
                'automacoes',
                'platform',
                'platform-site',
                'platform-blog',
                'platform-media',
                'platform-seo',
                'platform-whatsapp',
                'platform-google',
                'platform-team',
                'platform-support',
                'whatsapp-inbox',
                'whatsapp-campaigns',
                'whatsapp-templates',
                'whatsapp-automations',
                'whatsapp-connections',
                'instagram-inbox',
                'instagram-comments',
                'instagram-automations',
                'instagram-approved',
                'instagram-connections',
                'instagram-entrypoints',
                'messenger-inbox',
                'messenger-comments',
                'messenger-automations',
                'messenger-approved',
                'messenger-connections',
                'messenger-entrypoints',
              ].includes(item.moduleId)
              && !canAccessModule(item.moduleId as ModuleId)
            ) {
              return null;
            }
            if (
              [
                'whatsapp-inbox',
                'whatsapp-campaigns',
                'whatsapp-templates',
                'whatsapp-automations',
                'whatsapp-connections',
                'instagram-inbox',
                'instagram-comments',
                'instagram-automations',
                'instagram-approved',
                'instagram-connections',
                'instagram-entrypoints',
                'messenger-inbox',
                'messenger-comments',
                'messenger-automations',
                'messenger-approved',
                'messenger-connections',
                'messenger-entrypoints',
              ].includes(item.moduleId)
              && !can('mensagens', 'view')
            ) {
              return null;
            }
            if (
              ['whatsapp-templates', 'whatsapp-automations', 'instagram-automations', 'instagram-approved', 'instagram-connections', 'messenger-automations', 'messenger-approved', 'messenger-connections'].includes(item.moduleId)
              && !can('configuracoes', 'view')
            ) {
              return null;
            }

            const routePath = item.route
              ?? (item.moduleId === 'mensagens' ? '/whatsapp/inbox' : item.moduleId === 'admin' ? '/adm' : null);
            const isRouted = !!routePath;
            const isActive = isRouted
              ? (routePath === '/app'
                ? location.pathname === '/app'
                : routePath === '/platform'
                ? (location.pathname === '/platform'
                  || location.pathname.startsWith('/platform/workspaces')
                  || location.pathname.startsWith('/platform/plans'))
                : location.pathname.startsWith(routePath!))
              : location.pathname === '/app' && state.activeModule === item.moduleId;
            const handleClick = () => {
              if (isRouted) {
                navigate(routePath!);
              } else {
                if (location.pathname !== '/app') navigate('/app');
                setActiveModule(item.moduleId as ModuleId);
              }
            };

            return (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton
                  onClick={handleClick}
                  className={`cursor-pointer ${
                    isActive
                      ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                      : 'hover:bg-sidebar-accent/50 text-sidebar-foreground/70'
                  }`}
                >
                  <item.icon className="h-4 w-4 shrink-0" />
                  {!collapsed && (
                    <span className="flex-1">{item.title}</span>
                  )}
                  {!collapsed && item.badge === 'approvals' && pendingCount > 0 && (
                    <Badge variant="destructive" className="h-5 min-w-[20px] text-[10px] px-1.5 justify-center">
                      {pendingCount}
                    </Badge>
                  )}
                  {!collapsed && item.badge === 'messages' && unreadMessages > 0 && (
                    <Badge className="h-5 min-w-[20px] text-[10px] px-1.5 justify-center bg-[hsl(var(--success))] text-[hsl(var(--success-foreground))] hover:bg-[hsl(var(--success))]">
                      {unreadMessages}
                    </Badge>
                  )}
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );

  return (
    <Sidebar collapsible="icon" className="border-r-0">
      <SidebarHeader className="p-4">
        <div className="flex items-center gap-2.5">
          <DesbLogo variant="icon" size={32} dark />
          {!collapsed && (
            <div className="flex flex-col">
              <span className="text-sm font-bold text-sidebar-primary tracking-tight">{BRAND.name}</span>
              <span className="text-[10px] text-sidebar-foreground/60 uppercase tracking-widest">{BRAND.tagline}</span>
              <span className="mt-1 inline-flex w-fit items-center gap-1 rounded-full border border-sidebar-primary/25 bg-sidebar-primary/10 px-2 py-0.5 text-[9px] font-semibold text-sidebar-primary">
                <Bot className="h-2.5 w-2.5" />
                {BRAND.workspaceTagline}
              </span>
            </div>
          )}
        </div>
      </SidebarHeader>

      <SidebarContent className="px-2">
        {/* Practice Area Selector */}
        {!collapsed && (
          <div className="px-3 mb-4">
            <div className="text-[10px] uppercase tracking-wider text-sidebar-foreground/50 mb-2 font-medium">
              Área do Direito
            </div>
            <div className="space-y-0.5">
              {practiceAreas.map(area => (
                <button
                  key={area.key}
                  onClick={() =>
                    dispatch({
                      type: 'SET_PRACTICE_AREA',
                      payload: (area.flowOptions[0]?.practiceArea ?? 'trabalhista') as PracticeArea,
                    })
                  }
                  className="w-full text-left px-2.5 py-1.5 rounded-md text-xs transition-colors flex items-center gap-2 bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                >
                  <Scale className="h-3 w-3" />
                  {area.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {!collapsed && state.activeWorkspace && planSummary && (
          <div className="px-3 mb-4">
            <div className="rounded-xl border bg-sidebar-accent/30 p-3 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-sidebar-foreground/50 font-medium">Plano do workspace</p>
                  <p className="text-sm font-semibold text-sidebar-foreground">{planSummary.name ?? 'Workspace ativo'}</p>
                </div>
                <button
                  type="button"
                  onClick={toggleLimitsVisibility}
                  className="rounded-md p-1 text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors"
                  title={limitsVisible ? 'Ocultar limites' : 'Mostrar limites'}
                >
                  {limitsVisible ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
              {limitsVisible ? (
                <div className="space-y-2">
                  {limitItems.map((item) => (
                    <div key={item.label} className="rounded-lg bg-background/60 px-2.5 py-2">
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="text-sidebar-foreground/70">{item.label}</span>
                        <span className="font-medium text-sidebar-foreground">
                          {item.max == null ? `${item.used}/ilimitado` : `${item.used}/${item.max}`}
                        </span>
                      </div>
                      <p className="mt-1 text-[10px] text-sidebar-foreground/50">
                        {item.max == null
                          ? 'Sem limite neste plano.'
                          : `${item.remaining ?? 0} restantes neste ciclo.`}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-sidebar-foreground/60">
                  Limites ocultos. Clique no olhinho para ver uso e saldo do plano.
                </p>
              )}
            </div>
          </div>
        )}

        {renderMenuGroup('Workspace', workspaceItems)}
        {renderMenuGroup('WhatsApp', whatsappItems)}
        {renderMenuGroup('Instagram', instagramItems)}
        {renderMenuGroup('Messenger', messengerItems)}
        {hasPlatformAccess(state.currentUser) && renderMenuGroup('Plataforma', platformItems)}
      </SidebarContent>

      <SidebarFooter className="p-4">
        {!collapsed && aiTokenSummary && (
          <button
            type="button"
            onClick={() => navigate('/app/configuracoes/avancadas?tab=ia')}
            className="mb-2 w-full rounded-lg border border-sidebar-primary/25 bg-sidebar-primary/10 px-3 py-2.5 text-left transition-colors hover:bg-sidebar-primary/15"
            title="Abrir uso e limite de tokens de IA"
          >
            <div className="flex items-center justify-between gap-2 text-[10px] font-semibold text-sidebar-primary">
              <span className="inline-flex items-center gap-1.5">
                <Bot className="h-3 w-3" />
                Tokens de IA
              </span>
              <span>
                {aiTokenSummary.monthlyLimit == null
                  ? `${aiTokenSummary.totalTokens.toLocaleString('pt-BR')} usados`
                  : `${Math.min(aiTokenSummary.usedPercent ?? 0, 100).toFixed(0)}%`}
              </span>
            </div>
            <Progress
              value={aiTokenSummary.monthlyLimit == null ? 0 : Math.min(aiTokenSummary.usedPercent ?? 0, 100)}
              className="mt-2 h-1.5"
            />
            <p className="mt-1.5 text-[9px] leading-snug text-sidebar-foreground/55">
              {aiTokenSummary.monthlyLimit == null
                ? 'Limite mensal ainda não definido.'
                : `${(aiTokenSummary.remainingTokens ?? 0).toLocaleString('pt-BR')} tokens disponíveis neste ciclo.`}
            </p>
          </button>
        )}
        <button
          onClick={() => window.open('/status', '_blank')}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-sidebar-foreground/50 hover:text-sidebar-foreground/80 hover:bg-sidebar-accent/30 transition-colors"
        >
          <Activity className="h-3.5 w-3.5 shrink-0" />
          {!collapsed && <span>Status do Sistema</span>}
        </button>
        {!collapsed && (
          <div className="mt-3 rounded-lg border border-sidebar-primary/20 bg-sidebar-primary/10 px-3 py-2 text-left">
            <div className="flex items-center gap-1.5 text-[10px] font-semibold text-sidebar-primary">
              <Bot className="h-3 w-3" />
              {BRAND.ai.label}
            </div>
            <p className="mt-1 text-[10px] leading-snug text-sidebar-foreground/55">
              {BRAND.ai.promise}
            </p>
          </div>
        )}
        {!collapsed && (
          <div className="space-y-1 text-center mt-2">
            <div className="text-[11px] font-medium text-sidebar-foreground/80">
              {state.activeWorkspace?.name ?? 'Sem workspace'}
            </div>
            <div className="text-[10px] text-sidebar-foreground/40">
              {hasPlatformAccess(state.currentUser) ? 'Control plane habilitado' : 'Operação do workspace'}
            </div>
          </div>
        )}
      </SidebarFooter>
    </Sidebar>
  );
}
