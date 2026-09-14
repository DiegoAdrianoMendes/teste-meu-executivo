import { useMemo, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity,
  BarChart3,
  Bell,
  Bot,
  Boxes,
  Building2,
  CircleDollarSign,
  Compass,
  Gift,
  LifeBuoy,
  ListTodo,
  LogOut,
  Megaphone,
  RefreshCw,
  ShieldCheck,
  Settings,
  TicketPercent,
  Users,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { BlackboxCommandCenter } from '@/components/blackbox/BlackboxCommandCenter';
import { Button } from '@/components/ui/button';
import { DesbLogo } from '@/components/ui/DesbLogo';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar';
import { isLocalSimulation } from '@/klm/runtime';
import {
  bootstrapLocalPlatformRole,
  bootstrapLocalSession,
  type LocalPlatformRolePackage,
} from '@/services/auth';
import { useAppStore } from '@/store/app-context';
import {
  blackboxRoleLabel,
  visibleBlackboxSections,
} from '@/blackbox/access';
import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { isPlatformRoot } from '@/lib/platform-access';

const ICONS: Record<string, React.ElementType> = {
  dashboard: Boxes,
  bulk_operations: ListTodo,
  tasks: ListTodo,
  customers: Building2,
  support: LifeBuoy,
  staff: Users,
  announcements: Bell,
  workspace_configuration: Bot,
  benefits: Gift,
  coupons: TicketPercent,
  sales: BarChart3,
  finance: CircleDollarSign,
  billing: CircleDollarSign,
  plans: Compass,
  performance: BarChart3,
  reports: BarChart3,
  errors: Activity,
  infrastructure: Activity,
  ai_operations: Bot,
  content: Megaphone,
  marketing: Megaphone,
  whatsapp: Megaphone,
  support_access: ShieldCheck,
  staff_reassignment: Users,
  settings: Settings,
  integrations: Activity,
  logs: Activity,
  audit: ShieldCheck,
  security: ShieldCheck,
  compliance: ShieldCheck,
  data_governance: ShieldCheck,
  releases: RefreshCw,
  feature_flags: Boxes,
};

const GROUP_ORDER = ['Trabalho', 'Clientes e receita', 'Operação', 'Marca e crescimento', 'Administração'];

function sectionGroup(module: string): string {
  if (['sales', 'finance', 'billing', 'plans', 'benefits', 'coupons', 'performance', 'reports'].includes(module)) {
    return 'Clientes e receita';
  }
  if (['support', 'support_access', 'errors', 'infrastructure', 'ai_operations', 'workspace_configuration', 'integrations', 'logs', 'audit', 'security', 'compliance', 'data_governance', 'releases', 'feature_flags'].includes(module)) {
    return 'Operação';
  }
  if (['content', 'marketing', 'whatsapp', 'announcements'].includes(module)) {
    return 'Marca e crescimento';
  }
  if (['staff', 'staff_reassignment', 'settings'].includes(module)) return 'Administração';
  return 'Trabalho';
}

function BlackboxSidebar() {
  const { state: sidebarState } = useSidebar();
  const collapsed = sidebarState === 'collapsed';
  const { state, login, logout } = useAppStore();
  const { effectiveUser } = useBlackboxManifest();
  const navigate = useNavigate();
  const location = useLocation();
  const [localRole, setLocalRole] = useState(
    isPlatformRoot(effectiveUser)
      ? 'platform_owner'
      : effectiveUser.platformRolePackage ?? 'platform_owner',
  );
  const [switchingRole, setSwitchingRole] = useState(false);
  const sections = useMemo(
    () => visibleBlackboxSections(effectiveUser),
    [effectiveUser],
  );
  const groupedSections = useMemo(() => GROUP_ORDER
    .map((label) => ({
      label,
      sections: sections.filter((section) => sectionGroup(section.module) === label),
    }))
    .filter((group) => group.sections.length > 0), [sections]);
  const activeSectionPath = useMemo(() => sections
    .filter((section) => (
      location.pathname === section.path
      || location.pathname.startsWith(`${section.path}/`)
    ))
    .sort((left, right) => right.path.length - left.path.length)[0]?.path, [
    location.pathname,
    sections,
  ]);

  const switchLocalRole = async (role: string) => {
    if (!isLocalSimulation || switchingRole) return;
    setLocalRole(role);
    setSwitchingRole(true);
    try {
      const result = role === 'platform_owner'
        ? await bootstrapLocalSession('platform')
        : await bootstrapLocalPlatformRole(role as LocalPlatformRolePackage);
      login(result.user, result.session);
      navigate('/blackbox', { replace: true });
    } finally {
      setSwitchingRole(false);
    }
  };

  return (
    <Sidebar collapsible="icon" className="border-r-0 meu-executivo-sidebar">
      <SidebarHeader className="p-4">
        <div className="flex items-center gap-2.5">
          <DesbLogo variant="icon" size={32} dark />
          {!collapsed && (
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-sm font-bold tracking-tight text-sidebar-primary">
                Meu Executivo
              </span>
              <span className="text-[10px] uppercase tracking-widest text-sidebar-foreground/60">
                Operações do SaaS
              </span>
              <span className="mt-1 inline-flex w-fit items-center gap-1 rounded-full border border-sidebar-primary/25 bg-sidebar-primary/10 px-2 py-0.5 text-[9px] font-semibold text-sidebar-primary">
                <ShieldCheck className="h-2.5 w-2.5" />
                Ambiente interno
              </span>
            </div>
          )}
        </div>
      </SidebarHeader>

      <SidebarContent className="px-2">
        {groupedSections.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel className="text-[10px] uppercase tracking-wider text-sidebar-foreground/50">
              {group.label}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.sections.map((section) => {
                  const Icon = ICONS[section.module] ?? Boxes;
                  const exact = section.path === '/blackbox';
                  const isActive = activeSectionPath === section.path;
                  return (
                    <SidebarMenuItem key={section.path}>
                      <SidebarMenuButton
                        asChild
                        isActive={isActive}
                        tooltip={section.label}
                        className="text-sidebar-foreground/80 hover:text-sidebar-foreground data-[active=true]:bg-sidebar-primary data-[active=true]:text-sidebar-primary-foreground"
                      >
                        <NavLink to={section.path} end={exact}>
                          <Icon className="h-4 w-4" />
                          <span>{section.label}</span>
                        </NavLink>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter className="p-4">
        {!collapsed && isLocalSimulation && (
          <Select value={localRole} onValueChange={(value) => { void switchLocalRole(value); }}>
            <SelectTrigger
              className="mb-2 h-8 border-sidebar-primary/20 bg-sidebar-primary/10 text-[10px] text-sidebar-foreground"
              aria-label="Visualizar Blackbox como"
              disabled={switchingRole}
            >
              {switchingRole ? <RefreshCw className="mr-1.5 h-3 w-3 animate-spin" /> : null}
              <SelectValue placeholder="Visualizar como" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="platform_owner">Titular da plataforma</SelectItem>
              <SelectItem value="platform_admin">Administrador operacional</SelectItem>
              <SelectItem value="sales_sdr">Vendas / SDR</SelectItem>
              <SelectItem value="commercial_manager">Gestão comercial</SelectItem>
              <SelectItem value="onboarding">Onboarding</SelectItem>
              <SelectItem value="customer_success">Customer Success</SelectItem>
              <SelectItem value="support">Suporte</SelectItem>
              <SelectItem value="technical_support">Suporte técnico</SelectItem>
              <SelectItem value="finance_billing">Financeiro e cobrança</SelectItem>
              <SelectItem value="marketing_content">Marketing e conteúdo</SelectItem>
              <SelectItem value="community_manager">Gestão de comunidade</SelectItem>
              <SelectItem value="compliance_security">Compliance e segurança</SelectItem>
              <SelectItem value="product_operations">Produto e operações</SelectItem>
              <SelectItem value="engineering">Engenharia e TI</SelectItem>
            </SelectContent>
          </Select>
        )}
        {!collapsed && (
          <div className="px-2 py-1">
            <p className="truncate text-[11px] font-medium text-sidebar-foreground/80">
              {state.currentUser?.name}
            </p>
            <p className="mt-0.5 truncate text-[10px] text-sidebar-foreground/40">
              {blackboxRoleLabel(effectiveUser)}
            </p>
          </div>
        )}
        <Button
          type="button"
          variant="ghost"
          size={collapsed ? 'icon' : 'sm'}
          className="mt-1 w-full justify-start text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground"
          onClick={() => { void logout(); }}
          title="Encerrar sessão"
        >
          <LogOut className="h-4 w-4" />
          {!collapsed && <span className="ml-2">Encerrar sessão</span>}
        </Button>
      </SidebarFooter>
    </Sidebar>
  );
}

function BlackboxShellContent() {
  const { effectiveUser } = useBlackboxManifest();
  const location = useLocation();

  return (
    <div className="meu-executivo-shell flex h-full min-h-0 w-full overflow-hidden bg-background">
      <BlackboxSidebar />
      <SidebarInset className="min-h-0 min-w-0 overflow-hidden">
        <header
          className="flex h-14 shrink-0 items-center gap-3 border-b bg-card/95 px-3 shadow-[var(--shadow-card)] backdrop-blur sm:px-4"
        >
          <SidebarTrigger className="text-muted-foreground hover:text-foreground" />
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
              Meu Executivo · Operações
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {blackboxRoleLabel(effectiveUser)}
            </p>
          </div>
          <BlackboxCommandCenter />
          <Badge variant="outline" className="hidden gap-1.5 border-primary/20 bg-primary/5 text-primary sm:inline-flex">
            <ShieldCheck className="h-3.5 w-3.5" />
            Sessão interna
          </Badge>
        </header>
        <main
          className="app-ai-surface min-h-0 flex-1 overflow-auto overscroll-contain scrollbar-thin"
          data-testid="blackbox-route-scroll"
          data-path={location.pathname}
        >
          <Outlet />
        </main>
      </SidebarInset>
    </div>
  );
}

export function BlackboxShell() {
  return (
    <SidebarProvider className="h-dvh min-h-0 overflow-hidden">
      <BlackboxShellContent />
    </SidebarProvider>
  );
}
