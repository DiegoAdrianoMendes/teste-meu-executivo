import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { ArrowRightLeft, Bell, BriefcaseBusiness, CalendarPlus, ChevronLeft, ChevronRight, ClipboardCheck, Coins, FilePlus2, Gift, HelpCircle, LifeBuoy, ListPlus, LoaderCircle, LogOut, Menu, Moon, Plus, Scale, Search, Settings2, ShieldCheck, Sun, Trophy, UserPlus, UserRoundCog, X } from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/lib/utils';
import { defaultWorkspaceMode, getNavigationItem, klmNavigation, navigationForWorkspaceMode, navigationGroupsForMode, type KlmNavItem, type KlmWorkspaceMode } from '@/klm/navigation';
import { workspaceExperienceForMode } from '@/klm/workspace-experience';
import { KlmPill } from './Primitives';
import { klmApi } from '@/klm/api';
import { initializeKlmTheme, KLM_THEME_STORAGE_KEY } from '@/klm/theme';
import { useAppStore } from '@/store/app-context';
import { HelpDrawer } from './HelpDrawer';
import { CommandPalette } from './CommandPalette';
import { BRAND } from '@/lib/brand';
import { roleLabel } from '@/lib/role-labels';
import { useOptionalWorkspaceBrand } from '@/providers/workspace-brand-provider';
import { useOptionalUserPreferences } from '@/providers/user-preferences-provider';
import { useViewportLockedShell } from '@/hooks/use-viewport-locked-shell';
import { fetchWorkspaceAiCosts, fetchWorkspacePlanSummary } from '@/services/admin';
import { fetchPlatformAiCosts } from '@/services/platform';
import { getPlatformSupportAvailabilityStatus } from '@/services/platform-support-settings';
import { fetchWorkspaceExperience } from '@/services/configurations';
import { SupportCenter } from './SupportCenter';
import { NativeContextTools } from './NativeContextTools';

function NavigationIcon({ item, className }: { item: KlmNavItem; className: string }) {
  const SecondaryIcon = item.secondaryIcon;
  return (
    <span className="relative inline-flex shrink-0">
      <item.icon className={className} />
      {SecondaryIcon && <SecondaryIcon className="absolute -bottom-1 -right-1 h-2.5 w-2.5 rounded-sm bg-sidebar text-primary" />}
    </span>
  );
}

export function KlmShell() {
  useViewportLockedShell();
  const {
    can,
    state,
    logout,
    capabilityManifestStatus = 'ready',
    isCapabilityVisible = () => true,
  } = useAppStore();
  const workspaceBrandContext = useOptionalWorkspaceBrand();
  const userPreferencesContext = useOptionalUserPreferences();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const dashboardQuery = useQuery({ queryKey: ['klm', 'dashboard'], queryFn: klmApi.dashboard });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const threadsQuery = useQuery({ queryKey: ['klm', 'threads'], queryFn: klmApi.threads });
  const isPlatformRoot = Boolean(state?.currentUser?.isPlatformAdmin);
  const activeRole = state?.session?.activeRole ?? state?.currentUser?.role ?? '';
  const activeRoleLabel = roleLabel(activeRole);
  const isWorkspaceOwner = activeRole === 'workspace_owner';
  const canViewAiCosts = ['workspace_owner', 'workspace_admin'].includes(state?.session?.activeRole ?? '') || isPlatformRoot;
  const aiCostQuery = useQuery({
    queryKey: ['workspace-ai-costs', state?.session?.activeWorkspace?.id, isPlatformRoot ? 'platform-root' : 'workspace'],
    queryFn: () => isPlatformRoot
      ? fetchPlatformAiCosts(30, state.session?.activeWorkspace?.id)
      : fetchWorkspaceAiCosts(),
    staleTime: 30_000,
    refetchInterval: 60_000,
    enabled: canViewAiCosts,
    retry: false,
  });
  const planSummaryQuery = useQuery({
    queryKey: ['workspace-plan-summary', state?.session?.activeWorkspace?.id],
    queryFn: fetchWorkspacePlanSummary,
    staleTime: 30_000,
    refetchInterval: 60_000,
    enabled: canViewAiCosts,
    retry: false,
  });
  const supportAvailabilityQuery = useQuery({
    queryKey: ['platform-support', 'availability'],
    queryFn: getPlatformSupportAvailabilityStatus,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
  const workspaceExperienceQuery = useQuery({
    queryKey: ['workspace-experience', state?.session?.activeWorkspace?.id],
    queryFn: fetchWorkspaceExperience,
    staleTime: 60_000,
    retry: false,
    enabled: Boolean(state?.session?.activeWorkspace?.id),
  });
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [legacyDevTestDark, setLegacyDevTestDark] = useState(
    () => import.meta.env.PROD ? false : initializeKlmTheme(),
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [helpOpen, setHelpOpen] = useState(false);
  const [supportOpen, setSupportOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [sidebarProfileOpen, setSidebarProfileOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalMatterId, setApprovalMatterId] = useState('');
  const [approvalTitle, setApprovalTitle] = useState('');
  const [approvalDescription, setApprovalDescription] = useState('');
  const [legalCreateOpen, setLegalCreateOpen] = useState(false);
  const workspaceModeStorageKey = `meu-executivo:workspace-mode:${state?.session?.activeWorkspace?.id ?? 'workspace'}:${state?.currentUser?.id ?? 'user'}`;
  const [workspaceMode, setWorkspaceMode] = useState<KlmWorkspaceMode>(() => {
    const fallback = defaultWorkspaceMode(activeRole);
    const saved = window.localStorage.getItem(workspaceModeStorageKey);
    if (saved === 'commercial' || saved === 'legal' || (saved === 'general' && isWorkspaceOwner)) return saved;
    return fallback;
  });
  const gamificationQuery = useQuery({
    queryKey: ['klm', 'kpis', 30, null],
    queryFn: () => klmApi.kpis(30, null),
    staleTime: 60_000,
    retry: false,
    enabled: workspaceMode !== 'general' && can('relatorios', 'view'),
  });
  const supportTicketId = useMemo(
    () => new URLSearchParams(location.search).get('support'),
    [location.search],
  );
  const activeItem = useMemo(() => getNavigationItem(location.pathname), [location.pathname]);
  const activeLabel = workspaceMode === 'legal' && location.pathname.startsWith('/app/leads')
    ? 'Caso 360'
    : activeItem?.label;
  const legalModeAvailable = capabilityManifestStatus === 'ready'
    && isCapabilityVisible('legal_suite');
  const permittedNavigation = useMemo(
    () => klmNavigation.filter((item) => (
      can(item.permission.module, item.permission.action)
      && (!item.capability || (
        capabilityManifestStatus === 'ready'
        && isCapabilityVisible(item.capability)
      ))
    )),
    [can, capabilityManifestStatus, isCapabilityVisible],
  );
  const visibleNavigation = useMemo(
    () => navigationForWorkspaceMode(permittedNavigation, workspaceMode, isWorkspaceOwner),
    [isWorkspaceOwner, permittedNavigation, workspaceMode],
  );
  const navigationGroups = useMemo(() => navigationGroupsForMode(workspaceMode), [workspaceMode]);
  const workspaceExperience = useMemo(() => workspaceExperienceForMode(workspaceMode), [workspaceMode]);
  const canCreateLegalTask = can('tasks', 'view')
    && can('tasks', 'create')
    && capabilityManifestStatus === 'ready'
    && isCapabilityVisible('active_tasks');
  const canCreateLegalCalendarItem = can('lista', 'view') && can('kanban', 'edit');
  const canCreateLegalDemand = can('lista', 'view') && can('legal', 'manage') && legalModeAvailable;
  const canCreateLegalProcess = can('lista', 'view') && can('processos', 'manage') && legalModeAvailable;
  const visibleMobileNavigation = useMemo(
    () => {
      const order = workspaceMode === 'legal'
        ? ['/app', '/app/leads', '/app/conversas', '/app/agenda', '/app/processos']
        : workspaceMode === 'commercial'
          ? ['/app', '/app/funil', '/app/conversas', '/app/grupos', '/app/rastreamento']
          : ['/app', '/app/funil', '/app/conversas', '/app/ti', '/app/areas'];
      return order.map((path) => visibleNavigation.find((item) => item.path === path)).filter((item): item is KlmNavItem => Boolean(item));
    },
    [visibleNavigation, workspaceMode],
  );
  const primaryMobileNavigation = visibleMobileNavigation.slice(0, 4);
  const moreNavigationActive = !primaryMobileNavigation.some((item) => (
    item.path === '/app'
      ? location.pathname === '/app'
      : location.pathname.startsWith(item.path)
  ));

  useEffect(() => {
    const fallback = defaultWorkspaceMode(activeRole);
    const saved = window.localStorage.getItem(workspaceModeStorageKey);
    setWorkspaceMode(saved === 'commercial' || saved === 'legal' || (saved === 'general' && isWorkspaceOwner) ? saved : fallback);
  }, [activeRole, isWorkspaceOwner, workspaceModeStorageKey]);

  useEffect(() => {
    const capabilityResolved = capabilityManifestStatus === 'ready'
      || capabilityManifestStatus === 'not_configured';
    if (workspaceMode !== 'legal' || !capabilityResolved || legalModeAvailable) return;
    window.localStorage.setItem(workspaceModeStorageKey, 'commercial');
    setWorkspaceMode('commercial');
    if (location.pathname.startsWith('/app/leads') || activeItem?.capability === 'legal_suite') {
      navigate(`/app/funil${location.search}`, { replace: true });
    }
  }, [
    activeItem?.capability,
    capabilityManifestStatus,
    legalModeAvailable,
    location.pathname,
    location.search,
    navigate,
    workspaceMode,
    workspaceModeStorageKey,
  ]);

  useEffect(() => {
    if (supportTicketId) setSupportOpen(true);
  }, [supportTicketId]);
  useEffect(() => {
    const openSupportCenter = () => setSupportOpen(true);
    window.addEventListener('meu-executivo:support:open', openSupportCenter);
    return () => window.removeEventListener('meu-executivo:support:open', openSupportCenter);
  }, []);
  const searchResults = useMemo(() => {
    const normalized = searchQuery.trim().toLocaleLowerCase('pt-BR');
    if (!normalized) return [];
    return (funnelQuery.data?.cards ?? []).filter((card) => `${card.name} ${card.subject} ${card.object} ${card.source}`.toLocaleLowerCase('pt-BR').includes(normalized)).slice(0, 6);
  }, [funnelQuery.data?.cards, searchQuery]);
  const locationParams = new URLSearchParams(location.search);
  const requestedThreadId = locationParams.get('thread');
  const requestedThread = threadsQuery.data?.find((thread) => thread.id === requestedThreadId);
  const contextualMatterId = locationParams.get('matter') ?? requestedThread?.matterId ?? null;
  const contextualPath = (path: string) => {
    const params = new URLSearchParams();
    if (workspaceMode === 'legal' && path === '/app/leads') params.set('scope', 'clientes');
    if (contextualMatterId) params.set('matter', contextualMatterId);
    if (requestedThreadId) params.set('thread', requestedThreadId);
    const query = params.toString();
    return `${path}${query ? `?${query}` : ''}`;
  };
  const changeWorkspaceMode = (nextMode: KlmWorkspaceMode) => {
    if (nextMode === 'general' && !isWorkspaceOwner) return;
    if (nextMode === 'legal' && !legalModeAvailable) return;
    window.localStorage.setItem(workspaceModeStorageKey, nextMode);
    setWorkspaceMode(nextMode);
    const nextNavigation = navigationForWorkspaceMode(permittedNavigation, nextMode, isWorkspaceOwner);
    const currentRemainsVisible = nextNavigation.some((item) => item.path === '/app'
      ? location.pathname === '/app'
      : location.pathname.startsWith(item.path));
    if (!currentRemainsVisible) {
      navigate(contextualPath(nextMode === 'legal' ? '/app/leads' : nextMode === 'commercial' ? '/app/funil' : '/app'));
    }
  };
  const openLegalCreate = (path: string, action = 'new') => {
    setLegalCreateOpen(false);
    const params = new URLSearchParams();
    params.set('action', action);
    if (contextualMatterId) params.set('matter', contextualMatterId);
    navigate(`${path}?${params.toString()}`);
  };
  const pendingApprovals = Number(dashboardQuery.data?.metrics.pendingApprovals ?? 0);
  const dueFollowUps = Number(dashboardQuery.data?.metrics.dueFollowUps ?? 0);
  const aiCostPercent = Math.min(100, Math.max(0, aiCostQuery.data?.usedPercent ?? 0));
  const aiCostHasLimit = aiCostQuery.data?.monthlyLimit != null;
  const planUsageItems = [
    {
      label: 'Conversas com IA',
      used: planSummaryQuery.data?.aiConversationsUsedThisMonth ?? 0,
      limit: planSummaryQuery.data?.maxAiConversationsPerMonth ?? null,
    },
    {
      label: 'Destinatários de disparos',
      used: planSummaryQuery.data?.campaignRecipientsUsedThisMonth ?? 0,
      limit: planSummaryQuery.data?.maxCampaignRecipientsPerMonth ?? null,
    },
  ].map((item) => ({
    ...item,
    percent: item.limit == null || item.limit <= 0 ? 0 : Math.min(100, Math.round(item.used / item.limit * 100)),
  }));
  const gamificationRows = gamificationQuery.data?.teamPerformance?.rows ?? [];
  const gamificationIndex = gamificationRows.findIndex((row) => row.id === state?.currentUser?.id
    || row.name.toLocaleLowerCase('pt-BR') === state?.currentUser?.name?.toLocaleLowerCase('pt-BR'));
  const currentGamification = gamificationIndex >= 0 ? gamificationRows[gamificationIndex] : null;
  const decisionItems = (dashboardQuery.data?.decisions ?? []).map((decision) => decision.type === 'approval'
    ? { title: `${decision.count} ${decision.count === 1 ? 'aprovação' : 'aprovações'} aguardando decisão`, path: '/app/aprovacoes' }
    : { title: `${decision.count} follow-up${decision.count === 1 ? '' : 's'} vencido${decision.count === 1 ? '' : 's'}`, path: '/app/conversas' });
  const workspaceBrand = workspaceBrandContext?.brand;
  const userPreferences = userPreferencesContext?.preferences;
  if (import.meta.env.PROD && (!workspaceBrand || !userPreferences)) {
    throw new Error('KlmShell exige Brand e UserPreferences explícitos em produção.');
  }
  const brandName = BRAND.name;
  const brandShortName = 'ME';
  const brandTagline = BRAND.tagline;
  const dark = userPreferencesContext?.resolvedTheme === 'dark'
    || (!userPreferencesContext?.resolvedTheme && legacyDevTestDark);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setCommandOpen((current) => !current);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    document.title = `${activeLabel ?? 'Início'} | ${BRAND.name}`;
  }, [activeLabel]);

  useEffect(() => {
    if (!approvalOpen || approvalMatterId || funnelQuery.isPending) return;
    const contextualMatter = funnelQuery.data?.cards.find((card) => card.id === contextualMatterId);
    const fallbackMatter = funnelQuery.data?.cards[0];
    if (contextualMatter?.id || fallbackMatter?.id) {
      setApprovalMatterId(contextualMatter?.id ?? fallbackMatter?.id ?? '');
    }
  }, [approvalMatterId, approvalOpen, contextualMatterId, funnelQuery.data?.cards, funnelQuery.isPending]);

  const approvalMutation = useMutation({
    mutationFn: () => {
      const matter = funnelQuery.data?.cards.find((card) => card.id === approvalMatterId);
      if (!matter) throw new Error('Selecione um caso persistido para solicitar aprovação.');
      if (!approvalTitle.trim()) throw new Error('Informe o que precisa de aprovação.');
      const contextualThread = requestedThread?.matterId === matter.id ? requestedThread : undefined;
      const contextLines = [
        approvalDescription.trim(),
        `Origem: ${location.pathname}${location.search}`,
        contextualThread ? `Conversa: ${contextualThread.id}` : '',
      ].filter(Boolean);
      return klmApi.createApproval({
        matterId: matter.id,
        approvalType: 'manual_contextual',
        title: approvalTitle.trim(),
        description: contextLines.join('\n'),
        priority: 'media',
      });
    },
    onSuccess: (approval) => {
      setApprovalOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'approvals'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'dashboard'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      toast({
        title: 'Solicitação de aprovação criada.',
        description: `Registro ${approval.id} persistido na fila local.`,
      });
    },
    onError: (error: Error) => toast({
      variant: 'destructive',
      title: 'Não foi possível pedir aprovação',
      description: error.message,
    }),
  });

  const openApprovalRequest = () => {
    const contextualMatter = funnelQuery.data?.cards.find((card) => card.id === contextualMatterId);
    setApprovalMatterId(contextualMatter?.id ?? '');
    setApprovalTitle(`Aprovação em ${activeItem?.label ?? brandName}`);
    setApprovalDescription('');
    setApprovalOpen(true);
  };

  const toggleTheme = () => {
    const next = !dark;
    if (userPreferences) {
      void userPreferencesContext?.updatePreferences({ theme: next ? 'dark' : 'light' }).catch((error: Error) => {
        toast({
          variant: 'destructive',
          title: 'Não foi possível salvar o tema',
          description: error.message,
        });
      });
      return;
    }
    if (import.meta.env.PROD) {
      throw new Error('UserPreferences não está disponível para o shell em produção.');
    }
    setLegacyDevTestDark(next);
    document.documentElement.classList.toggle('dark', next);
    localStorage.setItem(KLM_THEME_STORAGE_KEY, next ? 'dark' : 'light');
  };

  const signOut = async () => {
    try {
      await logout();
      navigate('/login', { replace: true });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Não foi possível encerrar a sessão',
        description: error instanceof Error ? error.message : 'Tente novamente.',
      });
    }
  };

  return (
    <div data-testid="klm-shell" className="meu-executivo-shell h-dvh min-h-0 overflow-hidden bg-background text-foreground">
      <aside className={cn(
        'meu-executivo-sidebar fixed inset-y-0 left-0 z-50 hidden border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 xl:flex xl:flex-col',
        collapsed ? 'w-[76px]' : 'w-[224px]',
      )} data-desktop-sidebar="true">
        <div className={cn('flex h-[88px] shrink-0 items-center border-b border-white/[0.06]', collapsed ? 'justify-center px-3' : 'justify-between px-5')}>
          {!collapsed ? (
            <div className="min-w-0">
              <img src="/brand/meu-executivo/wordmark-inverse.svg" alt={brandName} className="h-auto w-[145px]" />
              <div className="mt-1.5 truncate text-[9px] font-medium text-white/48">{brandTagline}</div>
            </div>
          ) : (
            <img src="/brand/meu-executivo/mark-blue.svg" alt={brandShortName} className="h-9 w-9" />
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={cn('h-8 w-8 text-white/55 hover:bg-white/10 hover:text-white', collapsed && 'absolute -right-4 top-7 border border-sidebar-border bg-sidebar shadow-lg')}
            onClick={() => setCollapsed((current) => !current)}
            title={collapsed ? 'Expandir navegação' : 'Recolher navegação'}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-4 scrollbar-thin" aria-label="Navegação principal">
          {navigationGroups.map((group) => {
            const items = visibleNavigation.filter((item) => item.group === group.id);
            if (!items.length) return null;
            return (
              <div key={group.id} className="mb-4">
                {!collapsed && <p className="mb-1.5 px-3 text-[9px] font-semibold uppercase text-white/35">{group.label}</p>}
                <div className="space-y-1">
                  {items.map((item) => (
                    <NavLink
                      key={item.path}
                      to={contextualPath(item.path)}
                      end={item.path === '/app'}
                      aria-label={item.label}
                      title={collapsed ? item.label : undefined}
                      className={({ isActive }) => cn(
                        'group relative flex h-9 items-center gap-3 overflow-hidden rounded-lg border px-3 text-[11px] font-semibold transition-colors before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:rounded-r-full before:bg-primary before:opacity-0 before:transition-opacity',
                        isActive
                          ? 'border-primary/30 bg-primary/15 text-white shadow-[inset_0_0_0_1px_rgb(0_102_247/0.06)] before:opacity-100'
                          : 'border-transparent text-white/70 hover:bg-white/[0.06] hover:text-white',
                        collapsed && 'justify-center px-0',
                      )}
                    >
                      <NavigationIcon item={item} className="h-4 w-4 text-primary transition-transform group-hover:scale-105" />
                      {!collapsed && <span className="truncate">{item.label}</span>}
                    </NavLink>
                  ))}
                </div>
              </div>
            );
          })}
        </nav>

        <div className="shrink-0 border-t border-white/10 p-4">
          {!collapsed ? (
            <div className="space-y-2">
              {workspaceMode === 'general' && canViewAiCosts && (
                <button
                  type="button"
                  data-testid="sidebar-ai-cost-usage"
                  className={cn(
                    'block w-full rounded-lg border bg-primary/[0.07] p-2.5 text-left transition-colors hover:bg-primary/[0.12]',
                    aiCostQuery.isError ? 'border-red-400/45' : 'border-white/10',
                  )}
                  onClick={() => navigate('/app/configuracoes/avancadas?tab=ia')}
                  title="Abrir limites e consumo do plano"
                >
                  <span className="flex items-center justify-between gap-2 text-[10px]">
                    <span className="flex items-center gap-1.5 text-white/70"><Coins className="h-3.5 w-3.5 text-primary" />Uso de IA</span>
                    <strong className={aiCostQuery.isError ? 'text-red-300' : 'text-white'}>{aiCostQuery.isLoading ? '...' : aiCostQuery.isError ? 'indisponível' : aiCostHasLimit ? `${aiCostPercent.toFixed(0)}%` : 'definir limite'}</strong>
                  </span>
                  <span className="mt-2 block h-1.5 overflow-hidden rounded-full bg-white/10">
                    <span className={cn('block h-full rounded-full', aiCostQuery.isError ? 'bg-red-400' : 'bg-primary')} style={{ width: `${aiCostHasLimit ? aiCostPercent : 0}%` }} />
                  </span>
                  <span className="mt-1.5 block text-[9px] text-white/45">{aiCostQuery.data ? `${new Intl.NumberFormat('pt-BR').format(aiCostQuery.data.totalTokens ?? 0)} tokens usados` : aiCostQuery.isError ? 'Clique para revisar a conexão local' : 'Carregando consumo persistido'}</span>
                  <span className="mt-2 block space-y-1.5 border-t border-white/10 pt-2">
                    {planUsageItems.map((item) => (
                      <span key={item.label} className="block">
                        <span className="flex items-center justify-between gap-2 text-[9px] text-white/55">
                          <span>{item.label}</span>
                          <strong className="text-white/80">
                            {new Intl.NumberFormat('pt-BR').format(item.used)}/{item.limit == null ? 'sem limite' : new Intl.NumberFormat('pt-BR').format(item.limit)}
                          </strong>
                        </span>
                        <span className="mt-1 block h-1 overflow-hidden rounded-full bg-white/10">
                          <span className="block h-full rounded-full bg-primary" style={{ width: `${item.percent}%` }} />
                        </span>
                      </span>
                    ))}
                  </span>
                </button>
              )}
              {workspaceMode !== 'general' && (
                <div className="space-y-2" data-testid="sidebar-human-summary">
                  <Popover open={sidebarProfileOpen} onOpenChange={setSidebarProfileOpen}>
                    <PopoverTrigger asChild>
                      <button type="button" className="flex w-full items-center gap-2.5 rounded-lg border border-white/10 bg-white/[0.04] p-2.5 text-left transition-colors hover:bg-white/[0.08]" title="Abrir menu do perfil">
                        <Avatar className="h-9 w-9 shrink-0 border border-primary/40"><AvatarFallback className="bg-primary/20 text-[10px] font-bold text-white">{state?.currentUser?.initials ?? 'US'}</AvatarFallback></Avatar>
                        <span className="min-w-0 flex-1"><strong className="block truncate text-[10px] text-white">{state?.currentUser?.name ?? 'Usuário'}</strong><small className="mt-0.5 block truncate text-[9px] text-white/45">{activeRole || 'Membro do escritório'}</small></span>
                        <UserRoundCog className="h-4 w-4 shrink-0 text-primary" />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent side="right" align="end" sideOffset={12} className="w-56 p-1.5">
                      <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => { setSidebarProfileOpen(false); navigate('/app/preferencias'); }}><UserRoundCog className="mr-2 h-4 w-4 text-primary" />Minhas preferências</Button>
                      <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => { setSidebarProfileOpen(false); setSupportOpen(true); }}><LifeBuoy className="mr-2 h-4 w-4 text-primary" />Ajuda e suporte</Button>
                      {can('configuracoes', 'view') && <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => { setSidebarProfileOpen(false); navigate('/app/configuracoes'); }}><Settings2 className="mr-2 h-4 w-4 text-primary" />Configurações</Button>}
                      <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px] text-destructive hover:text-destructive" onClick={() => void signOut()}><LogOut className="mr-2 h-4 w-4" />Sair</Button>
                    </PopoverContent>
                  </Popover>
                  <button type="button" className="block w-full rounded-lg border border-white/10 bg-primary/[0.07] p-2.5 text-left transition-colors hover:bg-primary/[0.12]" onClick={() => navigate('/app/kpis')} title="Abrir ranking completo da equipe">
                    <span className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 text-[10px] text-white/70"><Trophy className="h-3.5 w-3.5 text-primary" />Ranking da equipe</span>
                      {currentGamification && <strong className="text-[11px] text-white">#{gamificationIndex + 1}</strong>}
                    </span>
                    {gamificationQuery.isPending ? <span className="mt-2 block text-[9px] text-white/45">Calculando com dados da equipe...</span> : currentGamification ? (
                      <span className="mt-2 flex items-end justify-between gap-2"><span className="text-[9px] text-white/45">Últimos 30 dias</span><strong className="text-[10px] text-white">{currentGamification.score} pts</strong></span>
                    ) : <span className="mt-2 block text-[9px] text-white/45">Ranking ainda sem dados</span>}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button
              type="button"
              className="mx-auto block"
              onClick={() => workspaceMode === 'general' ? canViewAiCosts && navigate('/app/configuracoes/avancadas?tab=ia') : navigate('/app/preferencias')}
              title={workspaceMode === 'general' ? canViewAiCosts ? 'Abrir uso e limite de tokens de IA' : 'Operação protegida' : 'Abrir perfil e preferências'}
            >
              {workspaceMode !== 'general' ? <Avatar className="h-8 w-8 border border-primary/40"><AvatarFallback className="bg-primary/20 text-[9px] font-bold text-white">{state?.currentUser?.initials ?? 'US'}</AvatarFallback></Avatar> : canViewAiCosts ? <Coins className="h-4 w-4 text-primary" /> : <ShieldCheck className="h-4 w-4 text-emerald-400" />}
            </button>
          )}
        </div>
      </aside>

      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 xl:hidden">
          <button className="absolute inset-0 bg-black/45" aria-label="Fechar navegação" onClick={() => setMobileMenuOpen(false)} />
          <div className="meu-executivo-sidebar absolute inset-y-0 left-0 w-[286px] overflow-y-auto border-r border-sidebar-border bg-sidebar p-4 text-white shadow-2xl">
            <div className="mb-6 flex items-center justify-between">
              <div className="min-w-0">
                <img src="/brand/meu-executivo/wordmark-inverse.svg" alt={brandName} className="h-auto w-[164px]" />
                <div className="mt-1.5 truncate text-[9px] text-white/48">{brandTagline}</div>
              </div>
              <Button variant="ghost" size="icon" className="text-white" onClick={() => setMobileMenuOpen(false)} aria-label="Fechar navegação"><X className="h-5 w-5" /></Button>
            </div>
            <div className="mb-4 grid grid-cols-3 gap-1 rounded-lg border border-white/10 bg-white/[0.04] p-1" role="group" aria-label="Painel de trabalho">
              {isWorkspaceOwner && <button type="button" aria-pressed={workspaceMode === 'general'} onClick={() => changeWorkspaceMode('general')} className={cn('min-h-11 rounded-md px-2 text-[10px] font-semibold', workspaceMode === 'general' ? 'bg-white text-slate-900' : 'text-white/65')}>Geral</button>}
              <button type="button" aria-pressed={workspaceMode === 'commercial'} onClick={() => changeWorkspaceMode('commercial')} className={cn('min-h-11 rounded-md px-2 text-[10px] font-semibold', workspaceMode === 'commercial' ? 'bg-white text-slate-900' : 'text-white/65')}>Comercial</button>
              <button type="button" disabled={!legalModeAvailable} aria-pressed={workspaceMode === 'legal'} onClick={() => changeWorkspaceMode('legal')} className={cn('min-h-11 rounded-md px-2 text-[10px] font-semibold', workspaceMode === 'legal' ? 'bg-white text-slate-900' : 'text-white/65', !legalModeAvailable && 'opacity-40')}>Jurídico</button>
            </div>
            <div className="space-y-1">
              {visibleNavigation.map((item) => (
                <NavLink
                  key={item.path}
                  to={contextualPath(item.path)}
                  end={item.path === '/app'}
                  className={({ isActive }) => cn(
                    'flex h-10 items-center gap-3 rounded-lg border px-3 text-xs font-semibold',
                    isActive ? 'border-primary/30 bg-primary/15 text-white' : 'border-transparent text-white/70',
                  )}
                >
                  <NavigationIcon item={item} className="h-4 w-4 text-primary" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className={cn('meu-executivo-main-column flex h-dvh min-h-0 flex-col overflow-hidden transition-[padding] duration-200', collapsed ? 'xl:pl-[76px]' : 'xl:pl-[224px]')}>
        <header className="relative z-40 flex min-h-[64px] shrink-0 items-center gap-2 border-b border-border bg-card/95 px-3 pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] shadow-[0_1px_0_rgb(0_102_247/0.05)] backdrop-blur sm:h-[72px] sm:px-4 lg:h-[88px] lg:px-8 xl:gap-3">
          <Button variant="ghost" size="icon" className="h-11 w-11 shrink-0 xl:hidden" onClick={() => setMobileMenuOpen(true)} title="Abrir navegação" aria-label="Abrir navegação">
            <Menu className="h-5 w-5" />
          </Button>
          <div className="min-w-0 flex-1 lg:min-w-[280px]">
            <h1 className="truncate text-[17px] font-semibold leading-6 sm:text-[20px] lg:text-[25px]" title={activeLabel ?? brandName}>{activeLabel ?? brandName}</h1>
            <p className="mt-1 hidden truncate text-[10px] text-muted-foreground sm:block">{workspaceMode === 'legal' ? 'Clientes contratados, prazos, processos e produção jurídica.' : workspaceMode === 'commercial' ? 'Aquisição, relacionamento e conversão em uma única visão.' : 'Operação jurídica, comercial e financeira em uma única visão.'}</p>
          </div>
          <div className="hidden h-9 shrink-0 items-center rounded-md border border-border bg-secondary p-1 lg:flex" role="group" aria-label="Painel de trabalho">
            {isWorkspaceOwner && (
              <button type="button" aria-pressed={workspaceMode === 'general'} onClick={() => changeWorkspaceMode('general')} className={cn('h-7 rounded px-2.5 text-[10px] font-semibold transition-colors', workspaceMode === 'general' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}>Geral</button>
            )}
            <button type="button" aria-pressed={workspaceMode === 'commercial'} onClick={() => changeWorkspaceMode('commercial')} className={cn('flex h-7 items-center gap-1.5 rounded px-2.5 text-[10px] font-semibold transition-colors', workspaceMode === 'commercial' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}><BriefcaseBusiness className="h-3.5 w-3.5" />Comercial</button>
            <button
              type="button"
              aria-pressed={workspaceMode === 'legal'}
              onClick={() => changeWorkspaceMode('legal')}
              disabled={!legalModeAvailable}
              title={legalModeAvailable ? 'Abrir operação Jurídico 360' : capabilityManifestStatus === 'loading' ? 'Validando Jurídico 360' : 'Jurídico 360 não contratado ou indisponível'}
              className={cn(
                'flex h-7 items-center gap-1.5 rounded px-2.5 text-[10px] font-semibold transition-colors',
                workspaceMode === 'legal' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                !legalModeAvailable && 'cursor-not-allowed opacity-45 hover:text-muted-foreground',
              )}
            ><Scale className="h-3.5 w-3.5" />Jurídico</button>
          </div>
          <div className="hidden min-w-[160px] max-w-[320px] flex-1 xl:block">
            {workspaceExperience.topbarVariant === 'legal_operations' ? (
              <button
                type="button"
                onClick={() => setCommandOpen(true)}
                className="relative flex h-9 w-full items-center rounded-md border border-border bg-secondary pl-9 pr-12 text-left text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
                aria-label="Abrir pesquisa jurídica global"
              >
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
                <span className="truncate">Pesquisar cliente, processo, tarefa ou documento</span>
                <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-border bg-card px-1.5 py-0.5 text-[9px] font-medium text-muted-foreground">⌘K</span>
              </button>
            ) : (
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} className="h-9 border-border bg-secondary pl-9 pr-12 text-xs" placeholder="Buscar lead ou caso" />
                <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-border bg-card px-1.5 py-0.5 text-[9px] font-medium text-muted-foreground">⌘K</span>
                {searchQuery.trim() && <div className="absolute left-0 right-0 top-11 z-50 overflow-hidden rounded-lg border border-border bg-card shadow-xl">{searchResults.length ? searchResults.map((card) => <button key={card.id} className="flex w-full items-center justify-between gap-3 border-b px-3 py-2.5 text-left last:border-0 hover:bg-secondary" onClick={() => { navigate(`/app/leads?matter=${encodeURIComponent(card.id)}`); setSearchQuery(''); }}><span className="min-w-0"><strong className="block truncate text-[10px]">{card.name}</strong><span className="mt-0.5 block truncate text-[9px] text-muted-foreground">{card.subject.replaceAll('_', ' ')} · {card.object}</span></span><KlmPill tone="gold">{card.probability}</KlmPill></button>) : <div className="p-3 text-[10px] text-muted-foreground">Nenhum caso encontrado.</div>}</div>}
              </div>
            )}
          </div>
          {workspaceMode === 'legal' && legalModeAvailable && <KlmPill tone="gold" className="hidden 2xl:inline-flex">Operação jurídica</KlmPill>}
          {workspaceExperience.topbarVariant === 'legal_operations' && (
            <Popover open={legalCreateOpen} onOpenChange={setLegalCreateOpen}>
              <PopoverTrigger asChild>
                <Button type="button" className="hidden h-9 gap-2 px-3 text-xs sm:inline-flex" aria-label="Criar item jurídico">
                  <Plus className="h-4 w-4" />Criar
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-72 p-1.5" data-testid="legal-quick-create">
                <p className="px-2.5 pb-1.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Criação jurídica</p>
                {canCreateLegalTask && <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => openLegalCreate('/app/tarefas')}><ListPlus className="mr-2 h-4 w-4 text-primary" />Tarefa</Button>}
                {canCreateLegalCalendarItem && <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => openLegalCreate('/app/agenda')}><CalendarPlus className="mr-2 h-4 w-4 text-primary" />Compromisso ou audiência</Button>}
                {canCreateLegalDemand && <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => openLegalCreate('/app/demandas')}><FilePlus2 className="mr-2 h-4 w-4 text-primary" />Caso 360</Button>}
                {canCreateLegalProcess && <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => openLegalCreate('/app/processos')}><ClipboardCheck className="mr-2 h-4 w-4 text-primary" />Processo</Button>}
                {can('aprovacoes', 'create') && <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => { setLegalCreateOpen(false); openApprovalRequest(); }}><UserPlus className="mr-2 h-4 w-4 text-primary" />Solicitar aprovação</Button>}
              </PopoverContent>
            </Popover>
          )}
          <KlmPill tone={dashboardQuery.isSuccess ? 'green' : dashboardQuery.isError ? 'red' : 'gold'} className="hidden xl:inline-flex">
            {dashboardQuery.isSuccess ? 'Conectado' : dashboardQuery.isError ? 'Indisponível' : 'Conectando'}
          </KlmPill>
          <Button variant="outline" size="icon" className="hidden h-9 w-9 sm:inline-flex" onClick={() => setCommandOpen(true)} title="Busca rápida (Cmd+K)">
            <Search className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-9 w-9"
            onClick={() => setSupportOpen(true)}
            title="Abrir Central de SAC"
            aria-label="Abrir Central de SAC"
          >
            <LifeBuoy className="h-4 w-4" />
          </Button>
          <Button variant="outline" size="icon" className="hidden h-9 w-9 sm:inline-flex" onClick={() => setHelpOpen(true)} title="Ajuda desta tela">
            <HelpCircle className="h-4 w-4" />
          </Button>
          <NativeContextTools />
          <Button variant="outline" size="icon" className="hidden h-9 w-9 sm:inline-flex" onClick={toggleTheme} title={dark ? 'Ativar modo claro' : 'Ativar modo escuro'}>
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" size="icon" className="relative h-9 w-9" title="Abrir decisões pendentes">
                <Bell className="h-4 w-4" />
                {(pendingApprovals + dueFollowUps) > 0 && <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-[#b94a48] px-1 text-[9px] font-semibold text-white">{pendingApprovals + dueFollowUps}</span>}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80 p-0">
              <div className="border-b p-3">
                <p className="text-xs font-semibold">Decisões pendentes</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">Nada será executado online nesta fase.</p>
              </div>
              {decisionItems.length ? decisionItems.map((item) => (
                <button key={item.title} className="flex w-full items-start gap-3 border-b px-3 py-3 text-left last:border-0 hover:bg-secondary" onClick={() => navigate(contextualPath(item.path))}>
                  <span className="mt-1 h-2 w-2 rounded-full bg-primary" />
                  <span className="text-[11px] leading-4">{item.title}</span>
                </button>
              )) : <div className="p-4 text-[10px] text-muted-foreground">Nenhuma decisão pendente no banco local.</div>}
            </PopoverContent>
          </Popover>
          <Button className="hidden h-9 bg-primary px-4 text-xs text-primary-foreground shadow-[0_8px_18px_rgb(0_102_247/0.18)] hover:bg-[#0057d8] sm:inline-flex" onClick={openApprovalRequest}>
            Pedir aprovação
          </Button>
          <Popover open={profileOpen} onOpenChange={setProfileOpen}>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                className="h-10 gap-2 rounded-lg px-1.5 hover:bg-primary/10 sm:px-2"
                aria-label={`Abrir perfil de ${state?.currentUser?.name ?? 'usuário'}`}
                title="Abrir perfil e configurações"
              >
                <Avatar className="h-8 w-8 border border-primary/25 shadow-sm">
                  <AvatarFallback className="bg-primary/10 text-[10px] font-bold text-primary">
                    {state?.currentUser?.initials ?? 'US'}
                  </AvatarFallback>
                </Avatar>
                <span className="hidden max-w-[120px] flex-col items-start text-left 2xl:flex">
                  <span className="w-full truncate text-[10px] font-semibold leading-4">{state?.currentUser?.name ?? 'Usuário'}</span>
                  <span className="w-full truncate text-[8px] leading-3 text-muted-foreground">{activeRoleLabel}</span>
                </span>
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" sideOffset={10} className="w-72 p-0" data-testid="profile-menu">
              <div className="border-b border-border px-4 py-3.5">
                <div className="flex items-center gap-3">
                  <Avatar className="h-10 w-10 border border-primary/20">
                    <AvatarFallback className="bg-primary text-[11px] font-bold text-primary-foreground">
                      {state?.currentUser?.initials ?? 'US'}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-semibold">{state?.currentUser?.name ?? 'Usuário'}</p>
                    <p className="mt-0.5 truncate text-[10px] text-muted-foreground">{state?.currentUser?.email ?? 'Sessão local'}</p>
                  </div>
                </div>
                <div className="mt-3 rounded-md border border-border bg-secondary/70 px-3 py-2">
                  <p className="truncate text-[10px] font-semibold">{state?.activeWorkspace?.name ?? state?.session?.activeWorkspace?.name ?? 'Ambiente atual'}</p>
                  <p className="mt-0.5 truncate text-[9px] text-muted-foreground">{activeRoleLabel}</p>
                </div>
              </div>
              <div className="p-1.5">
                <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => navigate('/app/preferencias')}>
                  <UserRoundCog className="mr-2 h-4 w-4 text-primary" />Minhas preferências
                </Button>
                {state?.session?.activeRole === 'workspace_owner' && (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-9 w-full justify-start px-2.5 text-[11px]"
                    onClick={() => {
                      setProfileOpen(false);
                      navigate('/app/beneficios');
                    }}
                  >
                    <Gift className="mr-2 h-4 w-4 text-primary" />Indique e ganhe
                  </Button>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  className="h-9 w-full justify-start px-2.5 text-[11px]"
                  onClick={() => {
                    setProfileOpen(false);
                    setSupportOpen(true);
                  }}
                >
                  <LifeBuoy className="mr-2 h-4 w-4 text-primary" />Central de SAC
                </Button>
                {can('configuracoes', 'view') && (
                  <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => navigate('/app/configuracoes/avancadas')}>
                    <Settings2 className="mr-2 h-4 w-4 text-primary" />Configurações da conta
                  </Button>
                )}
                {(state?.session?.availableWorkspaces.length ?? 0) > 1 && (
                  <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={() => navigate('/workspace/select')}>
                    <ArrowRightLeft className="mr-2 h-4 w-4 text-primary" />Trocar ambiente
                  </Button>
                )}
                <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px]" onClick={toggleTheme}>
                  {dark ? <Sun className="mr-2 h-4 w-4 text-primary" /> : <Moon className="mr-2 h-4 w-4 text-primary" />}
                  {dark ? 'Usar tema claro' : 'Usar tema escuro'}
                </Button>
              </div>
              <div className="border-t border-border p-1.5">
                <Button type="button" variant="ghost" className="h-9 w-full justify-start px-2.5 text-[11px] text-destructive hover:text-destructive" onClick={() => void signOut()}>
                  <LogOut className="mr-2 h-4 w-4" />Sair
                </Button>
              </div>
            </PopoverContent>
          </Popover>
        </header>

        <main data-testid="klm-page-frame" className="min-h-0 flex-1 overflow-hidden pb-[calc(68px+env(safe-area-inset-bottom))] xl:pb-0">
          <div
            data-testid="klm-route-scroll"
            className="meu-executivo-workspace h-full min-h-0 overflow-x-hidden overflow-y-auto overscroll-contain px-3 pb-[calc(1.5rem+env(safe-area-inset-bottom))] pt-4 scrollbar-thin sm:px-4 sm:pt-5 lg:px-8 lg:pb-10 lg:pt-7"
          >
            <Outlet context={{ workspaceExperience }} />
          </div>
        </main>
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-40 grid h-[calc(68px+env(safe-area-inset-bottom))] grid-cols-5 border-t border-sidebar-border bg-sidebar px-1 pb-[env(safe-area-inset-bottom)] text-white shadow-[0_-8px_24px_rgb(3_17_33/0.16)] xl:hidden" aria-label="Navegação móvel">
        {primaryMobileNavigation.map((item) => (
          <NavLink
            key={item.path}
            to={contextualPath(item.path)}
            end={item.path === '/app'}
            className={({ isActive }) => cn(
              'flex min-h-11 min-w-0 flex-col items-center justify-center gap-1 text-[9px] font-semibold',
              isActive ? 'text-primary' : 'text-white/55',
            )}
          >
            <NavigationIcon item={item} className="h-[18px] w-[18px]" />
            <span className="max-w-full truncate">{item.shortLabel}</span>
          </NavLink>
        ))}
        <button
          type="button"
          className={cn(
            'flex min-h-11 min-w-0 flex-col items-center justify-center gap-1 text-[9px] font-semibold',
            mobileMenuOpen || moreNavigationActive ? 'text-primary' : 'text-white/55',
          )}
          onClick={() => setMobileMenuOpen(true)}
          aria-label="Abrir mais opções de navegação"
          aria-expanded={mobileMenuOpen}
        >
          <Menu className="h-[18px] w-[18px]" />
          <span>Mais</span>
        </button>
      </nav>

      <Dialog open={approvalOpen} onOpenChange={(open) => { if (!approvalMutation.isPending) setApprovalOpen(open); }}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>Pedir aprovação</DialogTitle>
            <DialogDescription>A solicitação será persistida na fila e vinculada ao contexto selecionado.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label>Caso</Label>
              <Select value={approvalMatterId} onValueChange={setApprovalMatterId}>
                <SelectTrigger aria-label="Caso da aprovação"><SelectValue placeholder="Selecionar caso" /></SelectTrigger>
                <SelectContent>
                  {(funnelQuery.data?.cards ?? []).map((card) => <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="approval-title">Decisão solicitada</Label>
              <Input id="approval-title" value={approvalTitle} onChange={(event) => setApprovalTitle(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="approval-description">Contexto adicional</Label>
              <Textarea id="approval-description" value={approvalDescription} onChange={(event) => setApprovalDescription(event.target.value)} rows={4} placeholder="Pontos que o revisor deve considerar" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setApprovalOpen(false)} disabled={approvalMutation.isPending}>Cancelar</Button>
            <Button onClick={() => approvalMutation.mutate()} disabled={!approvalMatterId || !approvalTitle.trim() || approvalMutation.isPending}>
              {approvalMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
              Criar solicitação
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <HelpDrawer open={helpOpen} onOpenChange={setHelpOpen} />
      <Button
        type="button"
        className="fixed bottom-6 right-6 z-40 hidden h-10 gap-2 rounded-full bg-[#0057d8] px-3.5 text-[11px] font-semibold text-white shadow-[0_12px_28px_rgb(0_87_216/0.28)] hover:bg-[#004cbf] lg:inline-flex"
        onClick={() => setSupportOpen(true)}
        aria-label={supportAvailabilityQuery.data?.state === 'online' ? 'Abrir chat com o suporte' : 'Abrir Central de SAC'}
        title={supportAvailabilityQuery.data?.message ?? 'Abrir suporte'}
        data-testid="floating-support-button"
      >
        <span className="relative">
          <LifeBuoy className="h-4 w-4" />
          <span
            aria-hidden="true"
            className={cn(
              'absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-[#0057d8]',
              supportAvailabilityQuery.data?.state === 'online' ? 'bg-emerald-400' : 'bg-slate-300',
            )}
          />
        </span>
        <span>{workspaceExperienceQuery.data?.supportLabel || 'Ajuda e suporte'}</span>
      </Button>
      <SupportCenter
        open={supportOpen}
        onOpenChange={(nextOpen) => {
          setSupportOpen(nextOpen);
          if (!nextOpen && supportTicketId) {
            const search = new URLSearchParams(location.search);
            search.delete('support');
            navigate(
              {
                pathname: location.pathname,
                search: search.toString() ? `?${search.toString()}` : '',
              },
              { replace: true },
            );
          }
        }}
        currentRoute={location.pathname}
        initialTicketId={supportTicketId}
        supportLabel={workspaceExperienceQuery.data?.supportLabel || 'Central de SAC'}
        welcomeMessage={workspaceExperienceQuery.data?.welcomeMessage || ''}
      />
      <CommandPalette
        open={commandOpen}
        onOpenChange={setCommandOpen}
        navigationItems={visibleNavigation}
        funnelCards={funnelQuery.data?.cards ?? []}
        threads={threadsQuery.data ?? []}
        contextualPath={contextualPath}
        workspaceMode={workspaceMode}
        legalSearchPermissions={{
          tasks: can('tasks', 'view'),
          processes: can('processos', 'view'),
          demands: can('legal', 'view'),
          documents: can('legal', 'view'),
        }}
      />
    </div>
  );
}
