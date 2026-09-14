import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  cancelPlatformBulkJob,
  createPlatformBulkJob,
  createPlatformBulkPreview,
  createPlatformWorkspace,
  createPlatformWorkspaceSelection,
  fetchPlatformAiCosts,
  fetchPlatformBulkJob,
  fetchPlatformBulkPreview,
  fetchPlatformOverview,
  fetchPlatformWorkspacePage,
  savePlatformWorkspaceAiCostPolicy,
  updatePlatformWorkspace,
} from '@/services/platform';
import type { PlatformBulkJob, PlatformBulkPreview, PlatformWorkspaceBulkAction } from '@/services/platform';
import { activatePlatformRootDirectAccess } from '@/services/support-access';
import type { PlatformAiCostSummary, PlatformWorkspaceSummary } from '@/types';
import { Building2, Cpu, DollarSign, ExternalLink, Info, ShieldCheck, Sparkles } from 'lucide-react';
import { WorkspaceAccount360 } from '@/components/platform/WorkspaceAccount360';
import { PlatformSitePanel } from '@/components/platform/PlatformSitePanel';
import { PlatformBlogPanel } from '@/components/platform/PlatformBlogPanel';
import { PlatformMediaLibraryPanel } from '@/components/platform/PlatformMediaLibraryPanel';
import { PlatformSeoIndexingPanel } from '@/components/platform/PlatformSeoIndexingPanel';
import { PlatformGoogleBusinessPanel } from '@/components/platform/PlatformGoogleBusinessPanel';
import { PlatformOfficialWhatsAppPanel } from '@/components/platform/PlatformOfficialWhatsAppPanel';
import { PlatformSupportAccessPanel } from '@/components/platform/PlatformSupportAccessPanel';
import { PlatformStaffPanel } from '@/components/platform/PlatformStaffPanel';
import { PlatformAiModelsPanel } from '@/components/platform/PlatformAiModelsPanel';
import { PlatformAgentBillingReconciliationPanel } from '@/components/platform/PlatformAgentBillingReconciliationPanel';
import { PlatformAiOperationsPanel } from '@/components/platform/PlatformAiOperationsPanel';
import { PlatformAiCredentialsPanel } from '@/components/platform/PlatformAiCredentialsPanel';
import { PlatformAiTokenPackagesPanel } from '@/components/platform/PlatformAiTokenPackagesPanel';
import { PlatformMetaCommercialRatesPanel } from '@/components/platform/PlatformMetaCommercialRatesPanel';
import { AiCostControlPanel } from '@/components/admin/AiCostControlPanel';
import { useAppStore } from '@/store/app-context';
import { canPlatform, isPlatformRoot as hasPlatformRoot } from '@/lib/platform-access';
import { PlatformPlanPoliciesPanel } from '@/components/platform/PlatformPlanPoliciesPanel';
import { ErrorLogsPanel } from '@/components/admin/ErrorLogsPanel';
import { PlatformMetaHealthPanel } from '@/components/platform/PlatformMetaHealthPanel';
import { PlatformBillingPanel } from '@/components/platform/PlatformBillingPanel';
import { PlatformKpiPanel } from '@/components/platform/PlatformKpiPanel';
import { PlatformPeoplePanel } from '@/components/platform/PlatformPeoplePanel';
import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import type { PlatformSearchResult } from '@/services/platform-search';

const PlatformAnnouncementsPanel = lazy(() =>
  import('@/components/platform/PlatformAnnouncementsPanel').then((module) => ({
    default: module.PlatformAnnouncementsPanel,
  })),
);
const PlatformGrowthCrmPanel = lazy(() =>
  import('@/components/platform/PlatformGrowthCrmPanel').then((module) => ({
    default: module.PlatformGrowthCrmPanel,
  })),
);
const PlatformSupportTicketsPanel = lazy(() =>
  import('@/pages/PlatformSupportTicketsPanel').then((module) => ({
    default: module.PlatformSupportTicketsPanel,
  })),
);

function DeferredPanel({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<Skeleton className="h-40 w-full rounded-xl" />}>
      {children}
    </Suspense>
  );
}

// Sprint B1 — Centro de Comando: os 17 painéis existentes são redistribuídos
// em 4 domínios via navegação secundária, sem remover nenhum. "billing" e
// "people" são os dois painéis novos desta onda.
const TAB_DOMAIN: Record<string, 'negocio' | 'financeiro' | 'operacao' | 'pessoas'> = {
  overview: 'negocio',
  workspaces: 'negocio',
  plans: 'negocio',
  announcements: 'negocio',
  site: 'negocio',
  blog: 'negocio',
  media: 'negocio',
  seo: 'negocio',
  google: 'negocio',
  'growth-crm': 'negocio',
  billing: 'financeiro',
  'ai-costs': 'financeiro',
  'meta-rates': 'financeiro',
  'token-packages': 'financeiro',
  'support-tickets': 'operacao',
  'ai-operations': 'operacao',
  errors: 'operacao',
  'meta-health': 'operacao',
  'official-whatsapp': 'operacao',
  people: 'pessoas',
  team: 'pessoas',
  'support-access': 'pessoas',
};

const DOMAIN_LABEL: Record<string, string> = {
  negocio: 'Negócio',
  financeiro: 'Financeiro',
  operacao: 'Operação / Risco',
  pessoas: 'Pessoas',
};

const DOMAIN_ORDER = ['negocio', 'financeiro', 'operacao', 'pessoas'] as const;

const PLATFORM_SOURCE_CONTEXT_ROUTES: Record<string, { path: string; tab: string }> = {
  support_ticket: { path: '/blackbox/support-tickets', tab: 'tickets' },
  support_access: { path: '/blackbox/support-access', tab: 'requests' },
  workspace_onboarding: { path: '/blackbox/workspaces', tab: 'onboarding' },
  billing: { path: '/blackbox/billing', tab: 'subscriptions' },
  workspace_invitation: { path: '/blackbox/workspaces', tab: 'invitations' },
  staff_invitation: { path: '/blackbox/team', tab: 'invitations' },
  publication: { path: '/blackbox/announcements', tab: 'publications' },
  commercial_request: { path: '/blackbox/workspaces', tab: 'commercial' },
  commercial_checkout_reconcile: { path: '/blackbox/workspaces', tab: 'commercial' },
  commercial_subscription_reconcile: { path: '/blackbox/workspaces', tab: 'commercial' },
};

const PLATFORM_SOURCE_CONTEXT_LABELS: Record<string, string> = {
  support_ticket: 'ticket de suporte',
  support_access: 'acesso assistido',
  workspace_onboarding: 'onboarding da conta',
  billing: 'assinatura e cobrança',
  workspace_invitation: 'convite da conta',
  staff_invitation: 'convite de funcionário',
  publication: 'publicação',
  commercial_request: 'solicitação comercial',
  commercial_checkout_reconcile: 'reconciliação de checkout',
  commercial_subscription_reconcile: 'reconciliação de assinatura',
};

export interface PlatformSourceContext {
  sourceType: string;
  sourceId: string;
  workspaceId: string | null;
  tab: string;
  focus: 'context' | 'workspace' | 'record';
}

// eslint-disable-next-line react-refresh/only-export-components
export function resolvePlatformSourceContext(
  pathname: string,
  searchParams: URLSearchParams,
): PlatformSourceContext | null {
  const sourceType = searchParams.get('sourceType');
  const sourceId = searchParams.get('sourceId');
  const tab = searchParams.get('tab');
  if (!sourceType || !sourceId || !tab) return null;
  const definition = PLATFORM_SOURCE_CONTEXT_ROUTES[sourceType];
  if (!definition || definition.path !== pathname || definition.tab !== tab) return null;
  const rawFocus = searchParams.get('focus');
  const focus = rawFocus === 'workspace' || rawFocus === 'record' ? rawFocus : 'context';
  return {
    sourceType,
    sourceId,
    workspaceId: searchParams.get('workspaceId'),
    tab,
    focus,
  };
}

// eslint-disable-next-line react-refresh/only-export-components
export function shouldOpenWorkspaceSourceContext(
  context: PlatformSourceContext | null,
): boolean {
  return Boolean(
    context
    && ['workspace_onboarding', 'commercial_request', 'commercial_checkout_reconcile', 'commercial_subscription_reconcile'].includes(context.sourceType)
    && context.focus === 'workspace'
    && context.workspaceId,
  );
}

export function PlatformSourceContextNotice({
  context,
  canOpenWorkspace,
  onOpenWorkspace,
}: {
  context: PlatformSourceContext;
  canOpenWorkspace: boolean;
  onOpenWorkspace: () => void;
}) {
  const sourceLabel = PLATFORM_SOURCE_CONTEXT_LABELS[context.sourceType] ?? 'registro operacional';
  const workspaceFocused = shouldOpenWorkspaceSourceContext(context);
  return (
    <Alert data-testid="platform-source-context">
      <Info className="h-4 w-4" />
      <AlertTitle>Contexto da Central de Tarefas</AlertTitle>
      <AlertDescription className="space-y-2">
        <p>
          {workspaceFocused
            ? `A Conta 360 relacionada ao ${sourceLabel} foi aberta automaticamente.`
            : `A área de ${sourceLabel} foi aberta com o contexto preservado. Esta superfície ainda não seleciona esse registro automaticamente; localize-o pelos filtros da própria área.`}
        </p>
        {!workspaceFocused && context.workspaceId && canOpenWorkspace ? (
          <Button type="button" size="sm" variant="outline" onClick={onOpenWorkspace}>
            Abrir Conta 360 relacionada
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

function fmtCurrency(value?: number | null, currency = 'BRL') {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'Dados indisponíveis';
  return new Intl.NumberFormat(currency === 'BRL' ? 'pt-BR' : 'en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

function observedCount(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('pt-BR')
    : 'Dados indisponíveis';
}

function platformTabForPath(pathname: string): string {
  if (pathname.startsWith('/blackbox/support-tickets')) return 'support-tickets';
  if (pathname.startsWith('/blackbox/growth-crm')) return 'growth-crm';
  if (pathname.startsWith('/blackbox/ai-operations')) return 'ai-operations';
  if (pathname.startsWith('/blackbox/meta-rates')) return 'meta-rates';
  if (pathname.startsWith('/blackbox/token-packages')) return 'token-packages';
  if (pathname.startsWith('/blackbox/ai-costs')) return 'ai-costs';
  if (pathname.startsWith('/blackbox/site')) return 'site';
  if (pathname.startsWith('/blackbox/blog')) return 'blog';
  if (pathname.startsWith('/blackbox/media')) return 'media';
  if (pathname.startsWith('/blackbox/seo')) return 'seo';
  if (pathname.startsWith('/blackbox/official-whatsapp')) return 'official-whatsapp';
  if (pathname.startsWith('/blackbox/google-business')) return 'google';
  if (pathname.startsWith('/blackbox/support-access')) return 'support-access';
  if (pathname.startsWith('/blackbox/team')) return 'team';
  if (pathname.startsWith('/blackbox/workspaces')) return 'workspaces';
  if (pathname.startsWith('/blackbox/plans')) return 'plans';
  if (pathname.startsWith('/blackbox/announcements')) return 'announcements';
  if (pathname.startsWith('/blackbox/errors')) return 'errors';
  if (pathname.startsWith('/blackbox/meta-health')) return 'meta-health';
  if (pathname.startsWith('/blackbox/billing')) return 'billing';
  if (pathname.startsWith('/blackbox/people')) return 'people';
  return 'overview';
}

function KpiCard({
  title,
  value,
  sub,
  icon: Icon,
  loading,
}: {
  title: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
  loading?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-32" />
        ) : (
          <>
            <p className="text-2xl font-bold tabular-nums">{value}</p>
            {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function PlatformDashboard() {
  const location = useLocation();
  const navigate = useNavigate();
  const [workspaceUrlParams, setWorkspaceUrlParams] = useSearchParams();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const { state, enterSupportMode } = useAppStore();
  const { effectiveUser } = useBlackboxManifest();
  const canViewFinance = canPlatform(effectiveUser, 'finance', 'view');
  const canViewReports = canPlatform(effectiveUser, 'reports', 'view');
  const canViewCustomers = canPlatform(effectiveUser, 'customers', 'view');
  const canManageCustomers = canPlatform(effectiveUser, 'customers', 'manage');
  const canViewAiOperations = canPlatform(effectiveUser, 'ai_operations', 'view');
  const canManageAiOperations = canPlatform(effectiveUser, 'ai_operations', 'manage');
  const canViewInfrastructure = canPlatform(effectiveUser, 'infrastructure', 'view');
  const canManagePlans = canPlatform(effectiveUser, 'plans', 'manage');
  const canViewCommercialContracts = canPlatform(effectiveUser, 'commercial_contracts', 'view');
  const canManageCommercialContracts = canPlatform(effectiveUser, 'commercial_contracts', 'manage');
  const canViewCommercialAdjustments = canPlatform(effectiveUser, 'commercial_adjustments', 'view');
  const canManageCommercialAdjustments = canPlatform(effectiveUser, 'commercial_adjustments', 'manage');
  const canViewEntitlementBonuses = canPlatform(effectiveUser, 'entitlement_bonuses', 'view');
  const canManageEntitlementBonuses = canPlatform(effectiveUser, 'entitlement_bonuses', 'manage');
  const canManageAnnouncements = canPlatform(effectiveUser, 'announcements', 'manage');
  const canViewErrors = canPlatform(effectiveUser, 'errors', 'view');
  const canManageContent = canPlatform(effectiveUser, 'content', 'manage');
  const canManageWhatsApp = canPlatform(effectiveUser, 'whatsapp', 'manage');
  const canRequestSupportAccess = canPlatform(effectiveUser, 'support_access', 'request');
  const canViewSupport = canPlatform(effectiveUser, 'support', 'view');
  const canManageSupport = canPlatform(effectiveUser, 'support', 'manage');
  const canViewSales = canPlatform(effectiveUser, 'sales', 'view');
  const isPlatformRoot = hasPlatformRoot(effectiveUser);
  const requestedTab = useMemo(() => platformTabForPath(location.pathname), [location.pathname]);
  const sourceContext = useMemo(
    () => resolvePlatformSourceContext(location.pathname, workspaceUrlParams),
    [location.pathname, workspaceUrlParams],
  );
  const visibleTabs = useMemo(() => [
    canPlatform(effectiveUser, 'dashboard', 'view') && 'overview',
    canPlatform(effectiveUser, 'customers', 'view') && 'workspaces',
    canPlatform(effectiveUser, 'plans', 'view') && 'plans',
    canPlatform(effectiveUser, 'announcements', 'view') && 'announcements',
    canViewSupport && 'support-tickets',
    canViewAiOperations && 'ai-operations',
    canViewErrors && 'errors',
    isPlatformRoot && 'meta-health',
    isPlatformRoot && 'ai-costs',
    isPlatformRoot && 'meta-rates',
    canViewSupport && 'token-packages',
    canPlatform(effectiveUser, 'staff', 'view') && 'team',
    canPlatform(effectiveUser, 'content', 'view') && 'site',
    canPlatform(effectiveUser, 'content', 'view') && 'blog',
    canPlatform(effectiveUser, 'content', 'view') && 'media',
    canPlatform(effectiveUser, 'content', 'view') && 'seo',
    canPlatform(effectiveUser, 'whatsapp', 'view') && 'official-whatsapp',
    canPlatform(effectiveUser, 'content', 'view') && 'google',
    canPlatform(effectiveUser, 'support_access', 'view') && 'support-access',
    canViewFinance && 'billing',
    canPlatform(effectiveUser, 'customers', 'view') && 'people',
    canViewSales && 'growth-crm',
  ].filter((value): value is string => Boolean(value)), [
    canViewAiOperations,
    canViewErrors,
    canViewFinance,
    canViewSales,
    canViewSupport,
    isPlatformRoot,
    effectiveUser,
  ]);
  const activeTab = visibleTabs.includes(requestedTab) ? requestedTab : (visibleTabs[0] ?? requestedTab);
  const [activeDomain, setActiveDomain] = useState<string>(TAB_DOMAIN[activeTab] ?? 'negocio');
  useEffect(() => {
    setActiveDomain(TAB_DOMAIN[activeTab] ?? 'negocio');
  }, [activeTab]);
  const domainTabs = useMemo(
    () => visibleTabs.filter((tab) => (TAB_DOMAIN[tab] ?? 'negocio') === activeDomain),
    [activeDomain, visibleTabs],
  );
  const [workspaceDraft, setWorkspaceDraft] = useState<Record<string, string>>({
    name: '',
    slug: '',
    legalName: '',
    ownerName: '',
    ownerEmail: '',
    ownerPassword: '',
    description: '',
    launchNotes: '',
    planKey: 'pro',
    setupMode: 'assisted',
  });
  const [selectedWorkspaceIds, setSelectedWorkspaceIds] = useState<string[]>([]);
  const [selectAllMatching, setSelectAllMatching] = useState(false);
  const [excludedWorkspaceIds, setExcludedWorkspaceIds] = useState<string[]>([]);
  const workspaceSearch = workspaceUrlParams.get('q') ?? '';
  const rawWorkspaceStatus = workspaceUrlParams.get('status');
  const workspaceStatus = rawWorkspaceStatus
    && ['provisioning', 'active', 'suspended'].includes(rawWorkspaceStatus)
    ? rawWorkspaceStatus
    : 'all';
  const rawWorkspacePage = Number(workspaceUrlParams.get('page') ?? '1');
  const workspacePage = Number.isInteger(rawWorkspacePage) && rawWorkspacePage > 0
    ? rawWorkspacePage - 1
    : 0;
  const rawWorkspacePageSize = Number(workspaceUrlParams.get('pageSize') ?? '50');
  const workspacePageSize = [25, 50, 100].includes(rawWorkspacePageSize)
    ? rawWorkspacePageSize
    : 50;
  const updateWorkspaceUrl = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(workspaceUrlParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    });
    setWorkspaceUrlParams(next, { replace: true });
  };
  const setWorkspaceSearch = (value: string) => updateWorkspaceUrl({ q: value, page: null });
  const setWorkspaceStatus = (value: string) => updateWorkspaceUrl({
    status: value === 'all' ? null : value,
    page: null,
  });
  const setWorkspacePage = (update: number | ((current: number) => number)) => {
    const value = typeof update === 'function' ? update(workspacePage) : update;
    updateWorkspaceUrl({ page: value > 0 ? String(value + 1) : null });
  };
  const setWorkspacePageSize = (value: number) => updateWorkspaceUrl({
    pageSize: value === 50 ? null : String(value),
    page: null,
  });
  const [bulkAction, setBulkAction] = useState<PlatformWorkspaceBulkAction>('suspend');
  const [bulkReason, setBulkReason] = useState('');
  const [bulkPlanKey, setBulkPlanKey] = useState('pro');
  const [bulkPreview, setBulkPreview] = useState<PlatformBulkPreview | null>(null);
  const [bulkConfirmation, setBulkConfirmation] = useState('');
  const [bulkJobId, setBulkJobId] = useState<string | null>(null);
  const [bulkIdempotencyKey, setBulkIdempotencyKey] = useState(() => crypto.randomUUID());
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [workspaceChangeReason, setWorkspaceChangeReason] = useState('');
  const [aiCostsSection, setAiCostsSection] = useState<string>('custos');
  const [aiWorkspaceId, setAiWorkspaceId] = useState<string | null>(null);
  const [aiWorkspace, setAiWorkspace] = useState<PlatformSearchResult | null>(null);
  const [account360WorkspaceId, setAccount360WorkspaceId] = useState<string | null>(null);
  const [account360Open, setAccount360Open] = useState(false);
  const openAccount360 = (workspaceId: string) => {
    setAccount360WorkspaceId(workspaceId);
    setAccount360Open(true);
  };
  useEffect(() => {
    if (!shouldOpenWorkspaceSourceContext(sourceContext) || !sourceContext?.workspaceId) return;
    setAccount360WorkspaceId(sourceContext.workspaceId);
    setAccount360Open(true);
  }, [sourceContext]);

  useEffect(() => {
    if (activeTab === requestedTab) return;
    const fallbackRoutes: Record<string, string> = {
      overview: '/blackbox', workspaces: '/blackbox/workspaces', plans: '/blackbox/plans', announcements: '/blackbox/announcements', errors: '/blackbox/errors', 'meta-health': '/blackbox/meta-health', 'ai-costs': '/blackbox/ai-costs', 'meta-rates': '/blackbox/meta-rates', 'token-packages': '/blackbox/token-packages', team: '/blackbox/team',
      site: '/blackbox/site', blog: '/blackbox/blog', media: '/blackbox/media', seo: '/blackbox/seo',
      'official-whatsapp': '/blackbox/official-whatsapp', google: '/blackbox/google-business',
      'support-access': '/blackbox/support-access',
      'support-tickets': '/blackbox/support-tickets',
      'ai-operations': '/blackbox/ai-operations',
      'growth-crm': '/blackbox/growth-crm',
      billing: '/blackbox/billing',
      people: '/blackbox/people',
    };
    navigate(fallbackRoutes[activeTab] ?? '/blackbox', { replace: true });
  }, [activeTab, navigate, requestedTab]);

  const overviewQuery = useQuery({
    queryKey: ['platform', 'overview'],
    queryFn: fetchPlatformOverview,
    staleTime: 30_000,
    enabled: activeTab === 'overview',
  });
  const overviewWorkspacesQuery = useQuery({
    queryKey: ['platform', 'workspaces', 'overview-health'],
    queryFn: () => fetchPlatformWorkspacePage({ limit: 8, offset: 0 }),
    staleTime: 30_000,
    enabled: activeTab === 'overview' && canViewCustomers,
  });
  const workspacePageQuery = useQuery({
    queryKey: ['platform', 'workspaces', 'page', workspacePage, workspacePageSize, workspaceSearch, workspaceStatus],
    queryFn: () => fetchPlatformWorkspacePage({
      limit: workspacePageSize,
      offset: workspacePage * workspacePageSize,
      search: workspaceSearch,
      status: workspaceStatus,
    }),
    staleTime: 15_000,
    enabled: activeTab === 'workspaces' && canViewCustomers,
  });

  useEffect(() => {
    setSelectedWorkspaceIds([]);
    setSelectAllMatching(false);
    setExcludedWorkspaceIds([]);
    setBulkPreview(null);
    setBulkJobId(null);
  }, [workspaceSearch, workspaceStatus]);

  const createWorkspaceMutation = useMutation({
    mutationFn: createPlatformWorkspace,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspaces'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'overview'] });
      setWorkspaceDraft({
        name: '',
        slug: '',
        legalName: '',
        ownerName: '',
        ownerEmail: '',
        ownerPassword: '',
        description: '',
        launchNotes: '',
        planKey: 'pro',
        setupMode: 'assisted',
      });
      toast({ title: 'Workspace criado na central da plataforma.' });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Erro ao criar workspace', description: error.message });
    },
  });

  const updateWorkspaceMutation = useMutation({
    mutationFn: ({ workspaceId, payload }: { workspaceId: string; payload: Record<string, unknown> }) =>
      updatePlatformWorkspace(workspaceId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspaces'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'overview'] });
      setWorkspaceChangeReason('');
      toast({ title: 'Workspace atualizado.' });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Erro ao atualizar workspace', description: error.message });
    },
  });

  const bulkPreviewMutation = useMutation({
    mutationFn: async () => {
      const selection = await createPlatformWorkspaceSelection(selectAllMatching
        ? {
            mode: 'filter',
            filters: {
              ...(workspaceSearch.trim() ? { search: workspaceSearch.trim() } : {}),
              ...(workspaceStatus !== 'all' ? { status: workspaceStatus } : {}),
            },
            excludedIds: excludedWorkspaceIds,
          }
        : { mode: 'explicit', ids: selectedWorkspaceIds });
      return createPlatformBulkPreview({
        selectionSetId: selection.id,
        action: bulkAction,
        parameters: bulkAction === 'set_plan' ? { planKey: bulkPlanKey } : {},
      });
    },
    onSuccess: (preview) => {
      setBulkPreview(preview);
      setBulkConfirmation('');
      toast(preview.status === 'building'
        ? { title: 'Prévia em processamento', description: 'O impacto está sendo calculado em segundo plano.' }
        : { title: 'Prévia calculada', description: `${preview.eligibleCount} elegível(is), ${preview.blockedCount} bloqueado(s) e ${preview.noOpCount} sem alteração.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível calcular a prévia', description: error.message }),
  });

  const bulkPreviewQuery = useQuery<PlatformBulkPreview>({
    queryKey: ['platform', 'bulk-preview', bulkPreview?.id],
    queryFn: () => fetchPlatformBulkPreview(bulkPreview!.id),
    enabled: bulkPreview?.status === 'building',
    // The first poll may race the worker and still return `building`.  Drive
    // the interval from the persisted local preview until a terminal payload
    // replaces it; relying on query.state.data can stop after that first race.
    refetchInterval: bulkPreview?.status === 'building' ? 2_000 : false,
    retry: false,
  });
  useEffect(() => {
    if (bulkPreviewQuery.data) {
      setBulkPreview(bulkPreviewQuery.data);
    }
  }, [bulkPreviewQuery.data]);

  const bulkJobMutation = useMutation({
    mutationFn: () => createPlatformBulkJob({
      previewId: bulkPreview!.id,
      reason: bulkReason.trim(),
      confirmationPhrase: bulkPreview!.confirmationPhrase ? bulkConfirmation : null,
      idempotencyKey: bulkIdempotencyKey,
    }),
    onSuccess: (job) => {
      setBulkJobId(job.id);
      setBulkIdempotencyKey(crypto.randomUUID());
      toast({ title: 'Lote enfileirado', description: `Operação ${job.correlationId} iniciada com rastreabilidade por item.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Lote não iniciado', description: error.message }),
  });

  const bulkJobQuery = useQuery<PlatformBulkJob>({
    queryKey: ['platform', 'bulk-job', bulkJobId],
    queryFn: () => fetchPlatformBulkJob(bulkJobId!),
    enabled: Boolean(bulkJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ['succeeded', 'partial', 'failed', 'cancelled'].includes(status) ? false : 2_000;
    },
  });

  const cancelBulkJobMutation = useMutation({
    mutationFn: () => cancelPlatformBulkJob(bulkJobId!),
    onSuccess: (job) => {
      queryClient.setQueryData(['platform', 'bulk-job', bulkJobId], job);
      toast({ title: 'Cancelamento solicitado', description: 'O item em execução poderá concluir; nenhum novo item será iniciado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível cancelar', description: error.message }),
  });


  const pagedWorkspaces = useMemo(
    () => workspacePageQuery.data?.items ?? [],
    [workspacePageQuery.data?.items],
  );
  const selectedWorkspace = useMemo(() => {
    return pagedWorkspaces.find((workspace) => workspace.id === selectedWorkspaceId) ?? pagedWorkspaces[0] ?? null;
  }, [pagedWorkspaces, selectedWorkspaceId]);
  useEffect(() => {
    setWorkspaceChangeReason('');
  }, [selectedWorkspace?.id]);
  const selectedWorkspaceCount = selectAllMatching
    ? Math.max(0, (workspacePageQuery.data?.total ?? 0) - excludedWorkspaceIds.length)
    : selectedWorkspaceIds.length;
  const confirmWorkspaceFilterChange = () => {
    if (selectedWorkspaceCount === 0) return true;
    return window.confirm('Alterar os filtros descartará a seleção atual. Deseja continuar?');
  };
  const clearWorkspaceSelection = () => {
    setSelectedWorkspaceIds([]);
    setSelectAllMatching(false);
    setExcludedWorkspaceIds([]);
    setBulkPreview(null);
    setBulkJobId(null);
  };
  const globalAiCostsQuery = useQuery<PlatformAiCostSummary>({
    queryKey: ['platform', 'ai-costs', 'global'],
    queryFn: () => fetchPlatformAiCosts(30),
    staleTime: 30_000,
    enabled: activeTab === 'ai-costs' && isPlatformRoot,
    retry: false,
  });
  const workspaceAiCostsQuery = useQuery<PlatformAiCostSummary>({
    queryKey: ['platform', 'ai-costs', aiWorkspaceId],
    queryFn: () => fetchPlatformAiCosts(30, aiWorkspaceId!),
    staleTime: 30_000,
    enabled: activeTab === 'ai-costs' && isPlatformRoot && Boolean(aiWorkspaceId),
    retry: false,
  });
  const aiCostPolicyMutation = useMutation({
    mutationFn: ({ workspaceId, payload }: { workspaceId: string; payload: Parameters<typeof savePlatformWorkspaceAiCostPolicy>[1] }) =>
      savePlatformWorkspaceAiCostPolicy(workspaceId, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['platform', 'ai-costs', data.workspaceId], data);
      void queryClient.invalidateQueries({ queryKey: ['platform', 'ai-costs', 'global'] });
      toast({ title: 'Orçamento de IA da conta atualizado.' });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Não foi possível atualizar o orçamento de IA', description: error.message });
    },
  });

  const overview = overviewQuery.data ?? {};
  const mrr = overview.mrr ?? {};
  const ws = overview.workspaces ?? {};
  const ai = overview.ai_costs_30d ?? {};
  const aws = overview.aws_costs ?? {};
  const handleTabChange = (value: string) => {
    const routeMap: Record<string, string> = {
      overview: '/blackbox',
      workspaces: '/blackbox/workspaces',
      plans: '/blackbox/plans',
      announcements: '/blackbox/announcements',
      errors: '/blackbox/errors',
      'meta-health': '/blackbox/meta-health',
      'ai-costs': '/blackbox/ai-costs',
      'meta-rates': '/blackbox/meta-rates',
      'token-packages': '/blackbox/token-packages',
      team: '/blackbox/team',
      site: '/blackbox/site',
      blog: '/blackbox/blog',
      media: '/blackbox/media',
      seo: '/blackbox/seo',
      'official-whatsapp': '/blackbox/official-whatsapp',
      google: '/blackbox/google-business',
      'support-access': '/blackbox/support-access',
      'support-tickets': '/blackbox/support-tickets',
      'ai-operations': '/blackbox/ai-operations',
      'growth-crm': '/blackbox/growth-crm',
      billing: '/blackbox/billing',
      people: '/blackbox/people',
    };
    navigate(routeMap[value] ?? '/blackbox');
  };

  return (
    <div className="p-6 space-y-6 max-w-[1480px] mx-auto">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Central da plataforma</h1>
          <p className="text-sm text-muted-foreground">
            Governança global dos workspaces, catálogo de planos, onboarding e KPIs do sistema.
          </p>
        </div>
        <Badge className="w-fit gap-1 bg-primary/10 text-primary hover:bg-primary/10">
          <Sparkles className="h-3 w-3" />
          {isPlatformRoot ? 'Titular da plataforma' : state.currentUser?.platformRolePackage ?? 'Equipe SaaS'}
        </Badge>
      </div>

      <div className="flex flex-wrap gap-2">
        {DOMAIN_ORDER.filter((domain) => visibleTabs.some((tab) => (TAB_DOMAIN[tab] ?? 'negocio') === domain)).map((domain) => (
          <button
            key={domain}
            type="button"
            onClick={() => {
              setActiveDomain(domain);
              const firstTab = visibleTabs.find((tab) => (TAB_DOMAIN[tab] ?? 'negocio') === domain);
              if (firstTab && firstTab !== activeTab) handleTabChange(firstTab);
            }}
            className={`rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
              activeDomain === domain ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-transparent text-muted-foreground hover:bg-muted/50'
            }`}
          >
            {DOMAIN_LABEL[domain]}
          </button>
        ))}
      </div>

      {sourceContext ? (
        <PlatformSourceContextNotice
          context={sourceContext}
          canOpenWorkspace={canViewCustomers}
          onOpenWorkspace={() => {
            if (sourceContext.workspaceId) openAccount360(sourceContext.workspaceId);
          }}
        />
      ) : null}

      <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-4">
        <TabsList className="flex h-auto flex-wrap justify-start gap-2 bg-transparent p-0">
          {domainTabs.includes('overview') && <TabsTrigger value="overview">Visão geral</TabsTrigger>}
          {domainTabs.includes('workspaces') && <TabsTrigger value="workspaces">Workspaces</TabsTrigger>}
          {domainTabs.includes('plans') && <TabsTrigger value="plans">Planos</TabsTrigger>}
          {domainTabs.includes('announcements') && <TabsTrigger value="announcements">Avisos</TabsTrigger>}
          {domainTabs.includes('support-tickets') && <TabsTrigger value="support-tickets">SAC e tickets</TabsTrigger>}
          {domainTabs.includes('ai-operations') && <TabsTrigger value="ai-operations">Operações de IA</TabsTrigger>}
          {domainTabs.includes('errors') && <TabsTrigger value="errors">Erros e saúde</TabsTrigger>}
          {domainTabs.includes('meta-health') && <TabsTrigger value="meta-health">Saúde Meta &amp; Funil</TabsTrigger>}
          {domainTabs.includes('billing') && <TabsTrigger value="billing">Financeiro</TabsTrigger>}
          {domainTabs.includes('ai-costs') && <TabsTrigger value="ai-costs">Custos internos de IA</TabsTrigger>}
          {domainTabs.includes('meta-rates') && <TabsTrigger value="meta-rates">Tarifas Meta</TabsTrigger>}
          {domainTabs.includes('token-packages') && <TabsTrigger value="token-packages">Pacotes de tokens</TabsTrigger>}
          {domainTabs.includes('people') && <TabsTrigger value="people">Pessoas</TabsTrigger>}
          {domainTabs.includes('team') && <TabsTrigger value="team">Equipe SaaS</TabsTrigger>}
          {domainTabs.includes('site') && <TabsTrigger value="site">Site Público</TabsTrigger>}
          {domainTabs.includes('blog') && <TabsTrigger value="blog">Blog</TabsTrigger>}
          {domainTabs.includes('media') && <TabsTrigger value="media">Biblioteca de mídia</TabsTrigger>}
          {domainTabs.includes('seo') && <TabsTrigger value="seo">SEO & indexação</TabsTrigger>}
          {domainTabs.includes('official-whatsapp') && <TabsTrigger value="official-whatsapp">WhatsApps Oficiais</TabsTrigger>}
          {domainTabs.includes('google') && <TabsTrigger value="google">Google Business</TabsTrigger>}
          {domainTabs.includes('support-access') && <TabsTrigger value="support-access">Acesso de suporte</TabsTrigger>}
          {domainTabs.includes('growth-crm') && <TabsTrigger value="growth-crm">Growth CRM</TabsTrigger>}
        </TabsList>

        <TabsContent value="billing" className="space-y-4">
          <PlatformBillingPanel enabled={activeTab === 'billing' && canViewFinance} />
          <PlatformKpiPanel enabled={activeTab === 'billing' && canViewReports} defaultPeriodDays={30} />
        </TabsContent>

        <TabsContent value="people" className="space-y-4">
          <PlatformPeoplePanel
            enabled={activeTab === 'people' && canViewCustomers}
            canManage={isPlatformRoot}
          />
        </TabsContent>

        <TabsContent value="support-tickets" className="space-y-4">
          <DeferredPanel>
            <PlatformSupportTicketsPanel enabled={activeTab === 'support-tickets' && canViewSupport} />
          </DeferredPanel>
        </TabsContent>

        <TabsContent value="ai-operations" className="space-y-4">
          <PlatformAiCredentialsPanel
            enabled={activeTab === 'ai-operations' && canViewAiOperations}
            canManage={canManageAiOperations}
          />
          <PlatformAiOperationsPanel enabled={activeTab === 'ai-operations' && canViewAiOperations} />
        </TabsContent>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {canViewFinance && <KpiCard
              title="MRR da plataforma"
              value={overviewQuery.isLoading ? '—' : fmtCurrency(typeof mrr.total_mrr_cents === 'number' ? mrr.total_mrr_cents / 100 : null)}
              sub={`ARR: ${fmtCurrency(typeof mrr.total_arr_cents === 'number' ? mrr.total_arr_cents / 100 : null)}`}
              icon={DollarSign}
              loading={overviewQuery.isLoading}
            />}
            {canViewCustomers && <KpiCard
              title="Workspaces ativos"
              value={overviewQuery.isLoading ? '—' : observedCount(ws.active_workspaces)}
              sub={`${observedCount(ws.new_last_30d)} novos nos últimos 30 dias`}
              icon={Building2}
              loading={overviewQuery.isLoading}
            />}
            {isPlatformRoot && <KpiCard
              title="Custo IA (30d)"
              value={overviewQuery.isLoading ? '—' : fmtCurrency(ai.total_cost_usd, 'USD')}
              sub={`${observedCount(ai.total_runs)} execuções registradas`}
              icon={Cpu}
              loading={overviewQuery.isLoading}
            />}
            {isPlatformRoot && <KpiCard
              title="AWS mirror"
              value={overviewQuery.isLoading ? '—' : aws.source === 'estimate' ? 'Estimado' : 'Sincronizado'}
              sub={aws.total_usd != null ? fmtCurrency(aws.total_usd, 'USD') : 'Aguardando custo real'}
              icon={ShieldCheck}
              loading={overviewQuery.isLoading}
            />}
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            {canViewCustomers && <Card>
              <CardHeader>
                <CardTitle className="text-base">Saúde dos workspaces</CardTitle>
                <CardDescription>Visão consolidada da ativação, uso recente e risco operacional.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {overviewWorkspacesQuery.isLoading && <Skeleton className="h-40 w-full" />}
                {overviewWorkspacesQuery.isError && <p className="rounded-lg border border-destructive/30 p-4 text-sm text-destructive">A saúde das contas está indisponível. Nenhum estado foi presumido.</p>}
                {!overviewWorkspacesQuery.isLoading && !overviewWorkspacesQuery.isError && overviewWorkspacesQuery.data?.items.length === 0 && <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Nenhuma conta encontrada.</p>}
                {(overviewWorkspacesQuery.data?.items ?? []).map((workspace) => (
                  <div
                    key={workspace.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => openAccount360(workspace.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        openAccount360(workspace.id);
                      }
                    }}
                    className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/40"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{workspace.name}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {workspace.owner.email ?? 'Sem owner definido'} · {workspace.planSummary?.name ?? 'Sem plano'}
                      </p>
                      <div className="mt-2 flex items-center gap-2">
                        <Progress value={workspace.onboardingProgress ?? 0} className="h-2 w-28" />
                        <span className="truncate text-2xs text-muted-foreground">
                          onboarding {workspace.onboardingProgress ?? 0}% · {workspace.nextOnboardingStep ?? 'concluído'}
                        </span>
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <Badge variant={workspace.healthStatus === 'healthy' ? 'secondary' : workspace.healthStatus === 'at_risk' ? 'outline' : 'destructive'}>
                        {workspace.healthStatus}
                      </Badge>
                      <p className="mt-1 text-xs text-muted-foreground">score {workspace.healthScore}</p>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>}

            {(canViewFinance || canViewCustomers || canViewAiOperations || canViewInfrastructure) && <Card>
              <CardHeader>
                <CardTitle className="text-base">Indicadores da operação</CardTitle>
                <CardDescription>Métricas liberadas para o seu pacote funcional.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {canViewFinance && <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Financeiro da plataforma</p>
                  <p className="mt-1 text-lg font-semibold tabular-nums">{overviewQuery.isLoading ? '—' : fmtCurrency(typeof mrr.total_mrr_cents === 'number' ? mrr.total_mrr_cents / 100 : null)}</p>
                  <p className="mt-0.5 text-2xs text-muted-foreground">
                    ARR {overviewQuery.isLoading ? '—' : fmtCurrency(typeof mrr.total_arr_cents === 'number' ? mrr.total_arr_cents / 100 : null)}
                  </p>
                </div>}
                {canViewCustomers && <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Churn 30 dias</p>
                  <p className="mt-1 text-lg font-semibold tabular-nums">{typeof ws.churn_rate_30d_pct === 'number' ? `${ws.churn_rate_30d_pct}%` : 'Dados indisponíveis'}</p>
                </div>}
                {canViewCustomers && <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Trials ativos</p>
                  <p className="mt-1 text-lg font-semibold tabular-nums">{observedCount(ws.trial_workspaces)}</p>
                </div>}
                {canViewAiOperations && <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Modelos usados</p>
                  <p className="mt-1 text-lg font-semibold tabular-nums">{Array.isArray(ai.by_model) ? ai.by_model.length : 'Dados indisponíveis'}</p>
                </div>}
                {canViewInfrastructure && <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Mirror técnico</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    GitHub continua como fonte única. VPS segue pública e a AWS espelha saúde, build e deploy.
                  </p>
                </div>}
              </CardContent>
            </Card>}
          </div>

          {(canViewFinance || canViewCustomers) && (
          <PlatformKpiPanel enabled={activeTab === 'overview' && canViewReports} defaultPeriodDays={30} />
          )}
        </TabsContent>

        <TabsContent value="meta-health" className="space-y-4">
          <PlatformMetaHealthPanel enabled={activeTab === 'meta-health' && isPlatformRoot} />
        </TabsContent>

        <TabsContent value="ai-costs" className="space-y-4">
          <Tabs value={aiCostsSection} onValueChange={setAiCostsSection} className="space-y-4">
            <TabsList className="flex h-auto flex-wrap justify-start gap-2 bg-transparent p-0">
              <TabsTrigger value="custos">Custos e políticas</TabsTrigger>
              <TabsTrigger value="conciliacao">Conciliação de cobrança</TabsTrigger>
            </TabsList>

            <TabsContent value="conciliacao" className="space-y-4">
              <PlatformAgentBillingReconciliationPanel
                defaultWorkspaceId={aiWorkspaceId}
                enabled={activeTab === 'ai-costs' && aiCostsSection === 'conciliacao' && isPlatformRoot}
                canDecide={isPlatformRoot}
              />
            </TabsContent>

            <TabsContent value="custos" className="space-y-4">
          {isPlatformRoot && <PlatformAiModelsPanel enabled={activeTab === 'ai-costs'} />}
          <AiCostControlPanel
            value={globalAiCostsQuery.data ?? null}
            loading={globalAiCostsQuery.isLoading}
            readOnly
            showMonetaryCost={isPlatformRoot}
            title={isPlatformRoot ? 'Custo global de IA' : 'Uso global de tokens de IA'}
            description={isPlatformRoot
              ? 'Execuções, tokens e custo medido em todos os workspaces nos últimos 30 dias. Nenhum gate é aberto por esta tela.'
              : 'Execuções e tokens agregados nos últimos 30 dias. Valores monetários ficam restritos ao superadministrador.'}
          />
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Política por conta</CardTitle>
              <CardDescription>Selecione um workspace para revisar o teto efetivo e alterar sua política quando sua permissão permitir.</CardDescription>
            </CardHeader>
            <CardContent className="max-w-xl">
              <Label>Conta</Label>
              <PlatformEntityCombobox className="mt-1.5" entityType="accounts" purpose="ai_cost_policy" value={aiWorkspaceId} onChange={(value, item) => { setAiWorkspaceId(value); setAiWorkspace(item); }} label="Selecionar conta para política de IA" placeholder="Pesquisar conta" allowClear />
            </CardContent>
          </Card>
          {aiWorkspaceId && (
            <AiCostControlPanel
              value={workspaceAiCostsQuery.data ?? null}
              loading={workspaceAiCostsQuery.isLoading}
              readOnly={!canManageAiOperations}
              showMonetaryCost={isPlatformRoot}
              title={`Orçamento de ${aiWorkspace?.label ?? 'conta selecionada'}`}
              description="O superadministrador controla a política desta conta sem habilitar provedores, modelos ou infraestrutura externa."
              onSave={canManageAiOperations ? async (payload) => {
                await aiCostPolicyMutation.mutateAsync({ workspaceId: aiWorkspaceId, payload });
              } : undefined}
            />
          )}
            </TabsContent>
          </Tabs>
        </TabsContent>

        <TabsContent value="token-packages" className="space-y-4">
          <PlatformAiTokenPackagesPanel
            isPlatformRoot={isPlatformRoot}
            canManageSupport={canManageSupport}
          />
        </TabsContent>

        <TabsContent value="meta-rates" className="space-y-4">
          <PlatformMetaCommercialRatesPanel />
        </TabsContent>

        <TabsContent value="workspaces" className="space-y-4">
          <div className={`grid gap-4 ${canManageCustomers ? 'xl:grid-cols-[0.95fr,1.05fr]' : ''}`}>
            {canManageCustomers && <Card>
              <CardHeader>
                <CardTitle className="text-base">Novo workspace</CardTitle>
                <CardDescription>
                  Crie, aprove e oriente workspaces pela central da plataforma. O owner pode ser definido já no onboarding inicial.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1.5 md:col-span-2">
                  <Label>Nome do workspace</Label>
                  <Input value={workspaceDraft.name} onChange={(event) => setWorkspaceDraft((current) => ({ ...current, name: event.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label>Slug</Label>
                  <Input value={workspaceDraft.slug} onChange={(event) => setWorkspaceDraft((current) => ({ ...current, slug: event.target.value }))} placeholder="Opcional" />
                </div>
                <div className="space-y-1.5">
                  <Label>Plano inicial</Label>
                  <Select value={workspaceDraft.planKey} onValueChange={(value) => setWorkspaceDraft((current) => ({ ...current, planKey: value }))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="basic">Basic</SelectItem>
                      <SelectItem value="pro">Pro</SelectItem>
                      <SelectItem value="scale">Scale</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>Owner</Label>
                  <Input value={workspaceDraft.ownerName} onChange={(event) => setWorkspaceDraft((current) => ({ ...current, ownerName: event.target.value }))} placeholder="Nome do gestor" />
                </div>
                <div className="space-y-1.5">
                  <Label>E-mail do owner</Label>
                  <Input value={workspaceDraft.ownerEmail} onChange={(event) => setWorkspaceDraft((current) => ({ ...current, ownerEmail: event.target.value }))} placeholder="gestor@cliente.com" />
                </div>
                <div className="space-y-1.5">
                  <Label>Senha provisória</Label>
                  <Input type="password" value={workspaceDraft.ownerPassword} onChange={(event) => setWorkspaceDraft((current) => ({ ...current, ownerPassword: event.target.value }))} placeholder="Opcional" />
                </div>
                <div className="space-y-1.5">
                  <Label>Modo de setup</Label>
                  <Select value={workspaceDraft.setupMode} onValueChange={(value) => setWorkspaceDraft((current) => ({ ...current, setupMode: value }))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="self_service">Self-service</SelectItem>
                      <SelectItem value="assisted">Assistido</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5 md:col-span-2">
                  <Label>Descrição</Label>
                  <Textarea value={workspaceDraft.description} onChange={(event) => setWorkspaceDraft((current) => ({ ...current, description: event.target.value }))} rows={3} />
                </div>
                <div className="space-y-1.5 md:col-span-2">
                  <Label>Notas de lançamento</Label>
                  <Textarea value={workspaceDraft.launchNotes} onChange={(event) => setWorkspaceDraft((current) => ({ ...current, launchNotes: event.target.value }))} rows={3} />
                </div>
                <div className="md:col-span-2 flex justify-end">
                  <Button
                    onClick={() => createWorkspaceMutation.mutate({
                      name: workspaceDraft.name,
                      slug: workspaceDraft.slug || null,
                      legalName: workspaceDraft.legalName || null,
                      ownerName: workspaceDraft.ownerName || null,
                      ownerEmail: workspaceDraft.ownerEmail || null,
                      ownerPassword: workspaceDraft.ownerPassword || null,
                      description: workspaceDraft.description || null,
                      launchNotes: workspaceDraft.launchNotes || null,
                      planKey: workspaceDraft.planKey,
                      setupMode: workspaceDraft.setupMode,
                    })}
                    disabled={createWorkspaceMutation.isPending || !workspaceDraft.name.trim()}
                  >
                    {createWorkspaceMutation.isPending ? 'Criando...' : 'Criar workspace'}
                  </Button>
                </div>
              </CardContent>
            </Card>}

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Operação global de workspaces</CardTitle>
                <CardDescription>
                  Aprovação, orientação inicial, personalização e acompanhamento de saúde da base.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {isPlatformRoot && (
                  <div className="space-y-3 rounded-xl border border-primary/20 bg-primary/5 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="text-sm font-semibold">Operação em massa com prévia</p>
                        <p className="text-xs text-muted-foreground">
                          {selectedWorkspaceCount} conta(s) em uma seleção {selectAllMatching ? 'global congelada por filtro' : 'explícita'}. Nenhuma alteração ocorre antes da confirmação.
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setSelectedWorkspaceIds(pagedWorkspaces.map((workspace) => workspace.id));
                            setSelectAllMatching(false);
                            setExcludedWorkspaceIds([]);
                            setBulkPreview(null);
                          }}
                          disabled={pagedWorkspaces.length === 0}
                        >
                          Selecionar página
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setSelectAllMatching(true);
                            setSelectedWorkspaceIds([]);
                            setExcludedWorkspaceIds([]);
                            setBulkPreview(null);
                          }}
                          disabled={(workspacePageQuery.data?.total ?? 0) === 0}
                        >
                          Selecionar todos os {workspacePageQuery.data?.total ?? 0} resultados
                        </Button>
                        <Button type="button" size="sm" variant="ghost" onClick={() => {
                          setSelectedWorkspaceIds([]);
                          setSelectAllMatching(false);
                          setExcludedWorkspaceIds([]);
                          setBulkPreview(null);
                        }} disabled={selectedWorkspaceCount === 0}>
                          Limpar
                        </Button>
                      </div>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="space-y-1.5">
                        <Label>Ação</Label>
                        <Select value={bulkAction} onValueChange={(value) => {
                          setBulkAction(value as PlatformWorkspaceBulkAction);
                          setBulkPreview(null);
                        }}>
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="suspend">Suspender contas</SelectItem>
                            <SelectItem value="reactivate">Reativar contas</SelectItem>
                            <SelectItem value="complete_onboarding">Concluir onboarding</SelectItem>
                            <SelectItem value="reopen_onboarding">Reabrir onboarding</SelectItem>
                            <SelectItem value="set_plan">Alterar plano</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      {bulkAction === 'set_plan' && (
                        <div className="space-y-1.5">
                          <Label>Plano de destino</Label>
                          <Select value={bulkPlanKey} onValueChange={(value) => {
                            setBulkPlanKey(value);
                            setBulkPreview(null);
                          }}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="basic">Basic</SelectItem>
                              <SelectItem value="pro">Pro</SelectItem>
                              <SelectItem value="scale">Scale</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      )}
                      <div className="space-y-1.5 md:col-span-2">
                        <Label>Justificativa obrigatória</Label>
                        <Textarea value={bulkReason} onChange={(event) => setBulkReason(event.target.value)} rows={2} placeholder="Explique o motivo operacional (mínimo de 8 caracteres)." />
                      </div>
                    </div>
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        disabled={bulkPreviewMutation.isPending || selectedWorkspaceCount === 0}
                        onClick={() => bulkPreviewMutation.mutate()}
                      >
                        {bulkPreviewMutation.isPending ? 'Calculando impacto...' : 'Gerar prévia e validar impacto'}
                      </Button>
                    </div>
                    {bulkPreview && (
                      <div className="space-y-3 rounded-lg border bg-background p-3" data-testid="bulk-preview">
                        {bulkPreview.status === 'building' && <p role="status" className="text-sm text-muted-foreground">Calculando o impacto do lote em segundo plano...</p>}
                        <div className="grid gap-2 sm:grid-cols-4">
                          <div><p className="text-2xs text-muted-foreground">Alvos</p><p className="font-semibold">{bulkPreview.targetCount}</p></div>
                          <div><p className="text-2xs text-muted-foreground">Elegíveis</p><p className="font-semibold text-emerald-600">{bulkPreview.eligibleCount}</p></div>
                          <div><p className="text-2xs text-muted-foreground">Sem alteração</p><p className="font-semibold">{bulkPreview.noOpCount}</p></div>
                          <div><p className="text-2xs text-muted-foreground">Bloqueados</p><p className="font-semibold text-destructive">{bulkPreview.blockedCount}</p></div>
                        </div>
                        {bulkPreview.warnings.map((warning, index) => (
                          <p key={`${warning.code ?? 'warning'}-${index}`} className="text-xs text-amber-700">{warning.message ?? warning.code}</p>
                        ))}
                        {bulkPreview.confirmationPhrase && (
                          <div className="space-y-1.5">
                            <Label>Digite para confirmar: <span className="font-mono">{bulkPreview.confirmationPhrase}</span></Label>
                            <Input value={bulkConfirmation} onChange={(event) => setBulkConfirmation(event.target.value)} autoComplete="off" />
                          </div>
                        )}
                        <div className="flex justify-end">
                          <Button
                            type="button"
                            variant={bulkPreview.risk === 'critical' ? 'destructive' : 'default'}
                            disabled={bulkJobMutation.isPending
                              || bulkPreview.status !== 'ready'
                              || bulkPreview.eligibleCount === 0
                              || bulkReason.trim().length < 8
                              || Boolean(bulkPreview.confirmationPhrase && bulkConfirmation !== bulkPreview.confirmationPhrase)}
                            onClick={() => bulkJobMutation.mutate()}
                          >
                            {bulkJobMutation.isPending ? 'Enfileirando...' : `Confirmar ${bulkPreview.eligibleCount} alteração(ões)`}
                          </Button>
                        </div>
                      </div>
                    )}
                    {bulkJobQuery.data && (
                      <div className="space-y-2 rounded-lg border bg-background p-3" aria-live="polite" data-testid="bulk-job-progress">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <p className="text-sm font-semibold">Lote {bulkJobQuery.data.status}</p>
                            <p className="text-xs text-muted-foreground">{bulkJobQuery.data.processedCount}/{bulkJobQuery.data.eligibleCount} processados · correlação {bulkJobQuery.data.correlationId}</p>
                          </div>
                          {['queued', 'running'].includes(bulkJobQuery.data.status) && (
                            <Button type="button" size="sm" variant="outline" disabled={cancelBulkJobMutation.isPending} onClick={() => cancelBulkJobMutation.mutate()}>
                              {cancelBulkJobMutation.isPending ? 'Cancelando...' : 'Cancelar com segurança'}
                            </Button>
                          )}
                        </div>
                        <Progress value={bulkJobQuery.data.progressPercent} aria-label={`Progresso do lote: ${bulkJobQuery.data.progressPercent}%`} />
                        <p className="text-xs text-muted-foreground">
                          {bulkJobQuery.data.succeededCount} sucesso(s), {bulkJobQuery.data.failedCount} falha(s), {bulkJobQuery.data.conflictedCount} conflito(s), {bulkJobQuery.data.cancelledCount} cancelado(s).
                        </p>
                      </div>
                    )}
                  </div>
                )}
                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px_120px]">
                  <Input
                    value={workspaceSearch}
                    onChange={(event) => {
                      if (!confirmWorkspaceFilterChange()) return;
                      clearWorkspaceSelection();
                      setWorkspaceSearch(event.target.value);
                    }}
                    placeholder="Buscar conta por nome ou slug"
                    aria-label="Buscar contas"
                  />
                  <Select value={workspaceStatus} onValueChange={(value) => {
                    if (!confirmWorkspaceFilterChange()) return;
                    clearWorkspaceSelection();
                    setWorkspaceStatus(value);
                  }}>
                    <SelectTrigger aria-label="Filtrar contas por status"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todos os status</SelectItem>
                      <SelectItem value="provisioning">Provisionando</SelectItem>
                      <SelectItem value="active">Ativa</SelectItem>
                      <SelectItem value="suspended">Suspensa</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select value={String(workspacePageSize)} onValueChange={(value) => {
                    setWorkspacePageSize(Number(value));
                  }}>
                    <SelectTrigger aria-label="Contas por página"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="25">25 / página</SelectItem>
                      <SelectItem value="50">50 / página</SelectItem>
                      <SelectItem value="100">100 / página</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {workspacePageQuery.isLoading && <Skeleton className="h-40 w-full" />}
                {workspacePageQuery.isError && <p className="rounded-lg border border-destructive/30 p-4 text-sm text-destructive">Não foi possível carregar as contas. Tente novamente.</p>}
                {!workspacePageQuery.isLoading && !workspacePageQuery.isError && pagedWorkspaces.length === 0 && (
                  <p className="rounded-lg border p-4 text-sm text-muted-foreground">Nenhuma conta corresponde aos filtros atuais.</p>
                )}
                <div className="grid gap-3 max-h-[520px] overflow-y-auto pr-1">
                  {pagedWorkspaces.map((workspace) => (
                    <div
                      key={workspace.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelectedWorkspaceId(workspace.id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          setSelectedWorkspaceId(workspace.id);
                        }
                      }}
                      className={`rounded-xl border p-4 text-left transition-colors ${selectedWorkspace?.id === workspace.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex min-w-0 items-start gap-3">
                          {isPlatformRoot && (
                            <Checkbox
                              aria-label={`Selecionar ${workspace.name}`}
                              checked={selectAllMatching ? !excludedWorkspaceIds.includes(workspace.id) : selectedWorkspaceIds.includes(workspace.id)}
                              onClick={(event) => event.stopPropagation()}
                              onCheckedChange={(checked) => {
                                setBulkPreview(null);
                                if (selectAllMatching) {
                                  setExcludedWorkspaceIds((current) => checked
                                    ? current.filter((id) => id !== workspace.id)
                                    : [...new Set([...current, workspace.id])]);
                                } else {
                                  setSelectedWorkspaceIds((current) => checked
                                    ? [...new Set([...current, workspace.id])]
                                    : current.filter((id) => id !== workspace.id));
                                }
                              }}
                            />
                          )}
                          <div className="min-w-0">
                          <p className="truncate text-sm font-semibold">{workspace.name}</p>
                          <p className="truncate text-xs text-muted-foreground">{workspace.owner.email ?? 'Sem owner'} · {workspace.planSummary?.name ?? 'Sem plano'}</p>
                          <p className="mt-1 truncate text-2xs text-muted-foreground">
                            Onboarding {workspace.onboardingProgress ?? 0}% · próxima etapa: {workspace.nextOnboardingStep ?? 'concluído'}
                          </p>
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1.5">
                          <Badge variant={workspace.healthStatus === 'healthy' ? 'secondary' : workspace.healthStatus === 'at_risk' ? 'outline' : 'destructive'}>
                            {workspace.healthStatus}
                          </Badge>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-6 gap-1 px-1.5 text-2xs text-muted-foreground"
                            onClick={(event) => {
                              event.stopPropagation();
                              openAccount360(workspace.id);
                            }}
                          >
                            <ExternalLink className="h-3 w-3" /> Conta 360
                          </Button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                {(workspacePageQuery.data?.total ?? 0) > 0 && (
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>
                      Exibindo {workspacePage * workspacePageSize + 1}–{Math.min((workspacePage + 1) * workspacePageSize, workspacePageQuery.data?.total ?? 0)} de {workspacePageQuery.data?.total ?? 0}
                    </span>
                    <div className="flex gap-2">
                      <Button type="button" size="sm" variant="outline" disabled={workspacePage === 0} onClick={() => setWorkspacePage((current) => Math.max(0, current - 1))}>Anterior</Button>
                      <Button type="button" size="sm" variant="outline" disabled={!workspacePageQuery.data?.hasMore} onClick={() => setWorkspacePage((current) => current + 1)}>Próxima</Button>
                    </div>
                  </div>
                )}

                {selectedWorkspace && (
                  <div key={selectedWorkspace.id} className="rounded-xl border p-4 space-y-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold">{selectedWorkspace.name}</p>
                        <p className="truncate text-xs text-muted-foreground">{selectedWorkspace.slug}</p>
                      </div>
                      <Badge className="shrink-0">{selectedWorkspace.planSummary?.name ?? 'Sem plano'}</Badge>
                    </div>

                    <div className="space-y-1.5 rounded-lg border bg-muted/20 p-3">
                      <Label htmlFor="workspace-change-reason">Motivo da alteração</Label>
                      <Textarea
                        id="workspace-change-reason"
                        value={workspaceChangeReason}
                        maxLength={500}
                        rows={2}
                        onChange={(event) => setWorkspaceChangeReason(event.target.value)}
                        placeholder="Informe o motivo operacional (mínimo de 8 caracteres)."
                      />
                      <p className="text-xs text-muted-foreground">Status, plano, onboarding e notas só podem ser alterados com justificativa auditável.</p>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="space-y-1.5">
                        <Label>Status</Label>
                        <Select
                          disabled={!canManageCustomers || workspaceChangeReason.trim().length < 8}
                          value={selectedWorkspace.status}
                          onValueChange={(value) => {
                            void updateWorkspaceMutation.mutateAsync({
                              workspaceId: selectedWorkspace.id,
                              payload: { status: value, reason: workspaceChangeReason.trim() },
                            });
                          }}
                        >
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="provisioning">Provisionando</SelectItem>
                            <SelectItem value="active">Ativo</SelectItem>
                            <SelectItem value="suspended">Suspenso</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-1.5">
                        <Label>Plano</Label>
                        <Select
                          disabled={!canManageCustomers || workspaceChangeReason.trim().length < 8}
                          value={selectedWorkspace.planSummary?.tier ?? 'pro'}
                          onValueChange={(value) => {
                            void updateWorkspaceMutation.mutateAsync({
                              workspaceId: selectedWorkspace.id,
                              payload: { planKey: value, reason: workspaceChangeReason.trim() },
                            });
                          }}
                        >
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="basic">Basic</SelectItem>
                            <SelectItem value="pro">Pro</SelectItem>
                            <SelectItem value="scale">Scale</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="rounded-lg bg-muted/40 p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground">Equipe</p>
                        <p className="mt-1 text-lg font-semibold tabular-nums">{selectedWorkspace.memberCount}</p>
                      </div>
                      <div className="rounded-lg bg-muted/40 p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground">KPIs recentes</p>
                        <p className="mt-1 text-sm tabular-nums text-foreground">{selectedWorkspace.matterCount30d} cards · {selectedWorkspace.aiRuns30d} runs IA</p>
                      </div>
                    </div>

                    <div className="rounded-lg border p-3 space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium">Progresso do onboarding</p>
                        <Badge variant={selectedWorkspace.onboardingCompleted ? 'secondary' : 'outline'}>
                          {selectedWorkspace.onboardingCompleted ? 'Concluído' : 'Pendente'}
                        </Badge>
                      </div>
                      <Progress value={selectedWorkspace.onboardingProgress ?? 0} className="h-2" />
                      <p className="text-xs text-muted-foreground">
                        {selectedWorkspace.onboardingProgress ?? 0}% concluído · próxima etapa: {selectedWorkspace.nextOnboardingStep ?? 'revisão final'}
                      </p>
                    </div>

                    <div className="flex items-center justify-between rounded-lg border p-3">
                      <div>
                        <p className="text-sm font-medium">Onboarding concluído</p>
                        <p className="text-xs text-muted-foreground">Controla se o login vai para `/app` sem cair em onboarding pendente.</p>
                      </div>
                      <Switch
                        aria-label="Marcar onboarding da conta como concluído"
                        disabled={!canManageCustomers || workspaceChangeReason.trim().length < 8}
                        checked={selectedWorkspace.onboardingCompleted}
                        onCheckedChange={(checked) => {
                          void updateWorkspaceMutation.mutateAsync({
                            workspaceId: selectedWorkspace.id,
                            payload: { onboardingCompleted: checked, reason: workspaceChangeReason.trim() },
                          });
                        }}
                      />
                    </div>

                    <div className="space-y-1.5">
                      <Label>Notas de lançamento / orientação</Label>
                      <Textarea
                        disabled={!canManageCustomers || workspaceChangeReason.trim().length < 8}
                        defaultValue={selectedWorkspace.launchNotes ?? ''}
                        rows={3}
                        onBlur={(event) => {
                          if (event.target.value === (selectedWorkspace.launchNotes ?? '')) return;
                          void updateWorkspaceMutation.mutateAsync({
                            workspaceId: selectedWorkspace.id,
                            payload: { launchNotes: event.target.value, reason: workspaceChangeReason.trim() },
                          });
                        }}
                      />
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="site" className="space-y-4">
          <fieldset disabled={!canManageContent} className="min-w-0 border-0 p-0">
            <PlatformSitePanel />
          </fieldset>
        </TabsContent>

        <TabsContent value="team" className="space-y-4">
          <PlatformStaffPanel currentUser={effectiveUser} />
        </TabsContent>

        <TabsContent value="blog" className="space-y-4">
          <fieldset disabled={!canManageContent} className="min-w-0 border-0 p-0">
            <PlatformBlogPanel />
          </fieldset>
        </TabsContent>

        <TabsContent value="media" className="space-y-4">
          <fieldset disabled={!canManageContent} className="min-w-0 border-0 p-0">
            <PlatformMediaLibraryPanel />
          </fieldset>
        </TabsContent>

        <TabsContent value="seo" className="space-y-4">
          <fieldset disabled={!canManageContent} className="min-w-0 border-0 p-0">
            <PlatformSeoIndexingPanel />
          </fieldset>
        </TabsContent>

        <TabsContent value="official-whatsapp" className="space-y-4">
          <fieldset disabled={!canManageWhatsApp} className="min-w-0 border-0 p-0">
            <PlatformOfficialWhatsAppPanel />
          </fieldset>
        </TabsContent>

        <TabsContent value="google" className="space-y-4">
          <fieldset disabled={!canManageContent} className="min-w-0 border-0 p-0">
            <PlatformGoogleBusinessPanel />
          </fieldset>
        </TabsContent>

        <TabsContent value="support-access" className="space-y-4">
          <fieldset disabled={!canRequestSupportAccess} className="min-w-0 border-0 p-0">
            <PlatformSupportAccessPanel
              currentUserId={state.currentUser?.id}
              isPlatformRoot={isPlatformRoot}
              onEnterWorkspace={async (activation, workspace) => {
                await enterSupportMode(activation, workspace);
                navigate('/blackbox/support-session', { replace: true });
              }}
            />
          </fieldset>
        </TabsContent>

        <TabsContent value="plans" className="space-y-4">
          <PlatformPlanPoliciesPanel canManage={canManagePlans} />
        </TabsContent>

        <TabsContent value="announcements" className="space-y-4">
          <DeferredPanel>
            <PlatformAnnouncementsPanel canManage={canManageAnnouncements} />
          </DeferredPanel>
        </TabsContent>

        <TabsContent value="errors" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Erros, incidentes e saúde operacional</CardTitle>
              <CardDescription>
                Acompanhe falhas capturadas pelo sistema, recorrência, severidade e resolução. O histórico permanece local e auditável.
              </CardDescription>
            </CardHeader>
          </Card>
          <ErrorLogsPanel />
        </TabsContent>

        <TabsContent value="growth-crm" className="space-y-4">
          <DeferredPanel>
            <PlatformGrowthCrmPanel enabled={activeTab === 'growth-crm' && canViewSales} />
          </DeferredPanel>
        </TabsContent>
      </Tabs>

      <WorkspaceAccount360
        workspaceId={account360WorkspaceId}
        open={account360Open}
        onOpenChange={setAccount360Open}
        isPlatformRoot={isPlatformRoot}
        canManageAssignments={canManageCustomers}
        canManageMilestones={canPlatform(effectiveUser, 'onboarding', 'manage')}
        canViewCommercial={canViewCommercialContracts}
        canManageCommercialContracts={canManageCommercialContracts}
        canViewCommercialAdjustments={canViewCommercialAdjustments}
        canManageCommercialAdjustments={canManageCommercialAdjustments}
        canViewEntitlementBonuses={canViewEntitlementBonuses}
        canManageEntitlementBonuses={canManageEntitlementBonuses}
        onEnterDirect={isPlatformRoot ? async (workspace, reason) => {
          const activation = await activatePlatformRootDirectAccess(workspace.id, reason);
          await enterSupportMode(activation, workspace);
          setAccount360Open(false);
          navigate('/blackbox/support-session', { replace: true });
        } : undefined}
      />
    </div>
  );
}
