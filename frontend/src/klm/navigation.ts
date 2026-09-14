import {
  Activity,
  AreaChart,
  Banknote,
  BookOpen,
  Bot,
  BriefcaseBusiness,
  CalendarDays,
  ListTodo,
  Crown,
  FileCheck2,
  FilePenLine,
  Gauge,
  Instagram,
  CalendarClock,
  Landmark,
  LayoutDashboard,
  MessagesSquare,
  Megaphone,
  MessageCircleMore,
  Rocket,
  Settings,
  Scale,
  UploadCloud,
  Users,
  UsersRound,
  Workflow,
  Webhook,
  Wrench,
} from 'lucide-react';
import type { PermissionAction, PermissionModule } from '@/types';
import type { CommercialEntitlementKey } from '@/services/platform-commercial-catalog';

export type KlmNavItem = {
  label: string;
  shortLabel: string;
  path: string;
  icon: typeof LayoutDashboard;
  secondaryIcon?: typeof LayoutDashboard;
  group: 'operacao' | 'clientes' | 'juridico' | 'gestao' | 'sistema';
  permission: { module: PermissionModule; action: PermissionAction };
  capability?: CommercialEntitlementKey;
};

export type KlmWorkspaceMode = 'general' | 'commercial' | 'legal';

export const klmNavigation: KlmNavItem[] = [
  // Área de trabalho — visão do dia, agenda e decisões pendentes
  { label: 'Início', shortLabel: 'Início', path: '/app', icon: LayoutDashboard, group: 'operacao', permission: { module: 'dashboard', action: 'view' } },
  { label: 'Agenda', shortLabel: 'Agenda', path: '/app/agenda', icon: CalendarDays, group: 'operacao', permission: { module: 'lista', action: 'view' } },
  { label: 'Tarefas', shortLabel: 'Tarefas', path: '/app/tarefas', icon: ListTodo, group: 'operacao', permission: { module: 'tasks', action: 'view' }, capability: 'active_tasks' },
  // Publicações e Controladoria: fusão de Prazos (agenda de prazos do
  // módulo processual) com o painel diário de controladoria jurídica
  // (Onda 4) — mesma rota, mesmo item de menu, rótulo estendido (Sprint B UI).
  { label: 'Publicações e Controladoria', shortLabel: 'Publicações', path: '/app/prazos', icon: CalendarClock, group: 'operacao', permission: { module: 'lista', action: 'view' }, capability: 'legal_suite' },
  { label: 'Aprovações', shortLabel: 'Aprovações', path: '/app/aprovacoes', icon: FileCheck2, group: 'operacao', permission: { module: 'aprovacoes', action: 'view' } },
  // Clientes — conversas, CRM e relacionamento
  { label: 'Conversas', shortLabel: 'Chat', path: '/app/conversas', icon: MessagesSquare, group: 'clientes', permission: { module: 'mensagens', action: 'view' } },
  { label: 'Grupos', shortLabel: 'Grupos', path: '/app/grupos', icon: Users, group: 'clientes', permission: { module: 'mensagens', action: 'view' } },
  { label: 'Disparos e listas', shortLabel: 'Disparos', path: '/app/disparos', icon: Megaphone, group: 'clientes', permission: { module: 'mensagens', action: 'send_message' } },
  { label: 'Templates Meta', shortLabel: 'Templates', path: '/app/templates-meta', icon: FilePenLine, group: 'clientes', permission: { module: 'mensagens', action: 'view' } },
  { label: 'CRM', shortLabel: 'CRM', path: '/app/funil', icon: BriefcaseBusiness, group: 'clientes', permission: { module: 'kanban', action: 'view' } },
  // Conselho de executivos: mantido logo após o CRM (contrato de produto), mas visível em Sistema
  { label: 'Meus Executivos', shortLabel: 'Executivos', path: '/app/executivos', icon: Crown, group: 'sistema', permission: { module: 'executives', action: 'view' } },
  { label: 'Automações', shortLabel: 'Automações', path: '/app/comentarios', icon: MessageCircleMore, secondaryIcon: Instagram, group: 'clientes', permission: { module: 'mensagens', action: 'view' } },
  { label: 'Contratos', shortLabel: 'Contratos', path: '/app/contratos', icon: FileCheck2, group: 'clientes', permission: { module: 'aprovacoes', action: 'view' } },
  // Jurídico — casos, produção e conhecimento
  { label: 'Demandas', shortLabel: 'Demandas', path: '/app/demandas', icon: FileCheck2, group: 'juridico', permission: { module: 'lista', action: 'view' }, capability: 'legal_suite' },
  { label: 'Processos', shortLabel: 'Processos', path: '/app/processos', icon: Landmark, group: 'juridico', permission: { module: 'lista', action: 'view' }, capability: 'legal_suite' },
  { label: 'Petições', shortLabel: 'Petições', path: '/app/peticoes', icon: FilePenLine, group: 'juridico', permission: { module: 'lista', action: 'view' }, capability: 'legal_suite' },
  { label: 'Conhecimento', shortLabel: 'Ajuda', path: '/app/conhecimento', icon: BookOpen, group: 'juridico', permission: { module: 'dashboard', action: 'view' } },
  { label: 'Áreas e times', shortLabel: 'Áreas', path: '/app/areas', icon: Scale, group: 'juridico', permission: { module: 'configuracoes', action: 'view' }, capability: 'legal_suite' },
  // Gestão — financeiro, indicadores, equipe e aquisição
  { label: 'Financeiro', shortLabel: 'Financeiro', path: '/app/financeiro', icon: Banknote, group: 'gestao', permission: { module: 'finance', action: 'view' } },
  { label: 'KPIs e métricas', shortLabel: 'KPIs', path: '/app/kpis', icon: Gauge, group: 'gestao', permission: { module: 'relatorios', action: 'view' } },
  { label: 'Rastreamento', shortLabel: 'Track', path: '/app/rastreamento', icon: AreaChart, group: 'gestao', permission: { module: 'relatorios', action: 'view' } },
  { label: 'Equipe interna', shortLabel: 'Equipe', path: '/app/equipe', icon: UsersRound, group: 'gestao', permission: { module: 'mensagens', action: 'view' } },
  { label: 'Tráfego pago', shortLabel: 'Tráfego', path: '/app/aquisicao-paga', icon: Rocket, group: 'gestao', permission: { module: 'integrations', action: 'view' } },
  { label: 'Indique e ganhe', shortLabel: 'Benefícios', path: '/app/beneficios', icon: Crown, group: 'gestao', permission: { module: 'dashboard', action: 'view' } },
  // Importador Universal de Migração (Onda 3, Sprint C UI) — onboarding de
  // workspace vindo de outro sistema (Astrea/ADVBOX/DataJuri/planilhas).
  // Capability admin/view — mesmo módulo do TI Agent, ação de leitura;
  // toda ação mutante do wizard usa admin/manage no próprio endpoint.
  { label: 'Migração de dados', shortLabel: 'Migração', path: '/app/migracao', icon: UploadCloud, group: 'gestao', permission: { module: 'admin', action: 'view' } },
  // Sistema — agentes, integrações e configuração da plataforma
  { label: 'Agentes', shortLabel: 'Agentes', path: '/app/agentes', icon: Bot, group: 'sistema', permission: { module: 'agents', action: 'view' } },
  { label: 'Flows', shortLabel: 'Flows', path: '/app/flows', icon: Workflow, group: 'sistema', permission: { module: 'automations', action: 'manage' } },
  { label: 'Integrações e webhooks', shortLabel: 'Integrações', path: '/app/integracoes', icon: Webhook, group: 'sistema', permission: { module: 'integrations', action: 'view' } },
  { label: 'TI Agent', shortLabel: 'Suporte', path: '/app/ti', icon: Wrench, group: 'sistema', permission: { module: 'admin', action: 'view' } },
  { label: 'Configurações', shortLabel: 'Ajustes', path: '/app/configuracoes', icon: Settings, group: 'sistema', permission: { module: 'configuracoes', action: 'view' } },
  { label: 'Atividade', shortLabel: 'Logs', path: '/app/atividade', icon: Activity, group: 'sistema', permission: { module: 'audit', action: 'view' } },
];

const mobileOrder = ['/app', '/app/funil', '/app/conversas', '/app/ti', '/app/areas'];
export const mobileNavigation = mobileOrder.map((path) => klmNavigation.find((item) => item.path === path)!);

const commercialPaths = new Set([
  '/app', '/app/conversas', '/app/grupos', '/app/disparos', '/app/templates-meta', '/app/funil',
  '/app/tarefas',
  '/app/comentarios', '/app/financeiro', '/app/kpis', '/app/rastreamento',
  '/app/aquisicao-paga',
]);

const legalPaths = new Set([
  '/app', '/app/agenda', '/app/tarefas', '/app/prazos', '/app/aprovacoes', '/app/conversas',
  '/app/grupos', '/app/disparos', '/app/templates-meta', '/app/funil', '/app/contratos', '/app/demandas', '/app/processos',
  '/app/peticoes', '/app/conhecimento', '/app/areas', '/app/financeiro', '/app/kpis',
  '/app/equipe', '/app/executivos', '/app/agentes', '/app/flows', '/app/integracoes',
  '/app/configuracoes', '/app/atividade', '/app/ti',
]);

export const preservedNavigationGroups = [
  { id: 'operacao', label: 'Área de trabalho' },
  { id: 'clientes', label: 'Clientes' },
  { id: 'juridico', label: 'Jurídico' },
  { id: 'gestao', label: 'Gestão' },
  { id: 'sistema', label: 'Sistema' },
] as const;

export const legalNavigationGroups = [
  { id: 'operacao', label: 'Trabalho jurídico' },
  { id: 'clientes', label: 'Clientes e casos' },
  { id: 'juridico', label: 'Produção jurídica' },
  { id: 'gestao', label: 'Gestão do escritório' },
  { id: 'sistema', label: 'Sistema' },
] as const;

const legalGroupByPath: Partial<Record<string, KlmNavItem['group']>> = {
  '/app': 'operacao',
  '/app/agenda': 'operacao',
  '/app/tarefas': 'operacao',
  '/app/prazos': 'operacao',
  '/app/aprovacoes': 'operacao',
  '/app/conversas': 'clientes',
  '/app/grupos': 'clientes',
  '/app/funil': 'clientes',
  '/app/contratos': 'clientes',
  '/app/processos': 'juridico',
  '/app/demandas': 'juridico',
  '/app/peticoes': 'juridico',
  '/app/flows': 'juridico',
  '/app/conhecimento': 'juridico',
  '/app/financeiro': 'gestao',
  '/app/kpis': 'gestao',
  '/app/equipe': 'gestao',
  '/app/areas': 'gestao',
};

const legalLabelByPath: Partial<Record<string, Pick<KlmNavItem, 'label' | 'shortLabel'>>> = {
  '/app': { label: 'Início jurídico', shortLabel: 'Início' },
  '/app/conversas': { label: 'Atendimentos e conversas', shortLabel: 'Atendimentos' },
  '/app/peticoes': { label: 'Peças e petições', shortLabel: 'Petições' },
  '/app/flows': { label: 'Workflows', shortLabel: 'Workflows' },
  '/app/kpis': { label: 'Indicadores', shortLabel: 'Indicadores' },
  '/app/equipe': { label: 'Equipe', shortLabel: 'Equipe' },
  '/app/atividade': { label: 'Atividade e auditoria', shortLabel: 'Auditoria' },
};

export function navigationGroupsForMode(mode: KlmWorkspaceMode) {
  return mode === 'legal' ? legalNavigationGroups : preservedNavigationGroups;
}

export function navigationForWorkspaceMode(
  items: KlmNavItem[],
  mode: KlmWorkspaceMode,
  isWorkspaceOwner: boolean,
): KlmNavItem[] {
  if (mode === 'general') return isWorkspaceOwner ? items : [];
  const allowedPaths = mode === 'legal' ? legalPaths : commercialPaths;
  const visible = items.filter((item) => allowedPaths.has(item.path));
  if (mode === 'commercial') return visible;
  return visible.map((item) => {
    const legalItem = {
      ...item,
      ...legalLabelByPath[item.path],
      group: legalGroupByPath[item.path] ?? item.group,
    };
    return item.path === '/app/funil'
      ? { ...legalItem, label: 'Caso 360', shortLabel: 'Caso 360', path: '/app/leads', group: 'clientes' as const, capability: 'legal_suite' as const }
      : legalItem;
  });
}

export function defaultWorkspaceMode(role: string | null | undefined): KlmWorkspaceMode {
  if (role === 'workspace_owner') return 'general';
  return ['legal_manager', 'lawyer', 'paralegal', 'legal_assistant'].includes(role ?? '') ? 'legal' : 'commercial';
}

const auxiliaryNavigation: KlmNavItem[] = [
  { label: 'Minhas preferências', shortLabel: 'Preferências', path: '/app/preferencias', icon: Settings, group: 'sistema', permission: { module: 'configuracoes', action: 'view' } },
];

export function getNavigationItem(pathname: string) {
  if (pathname.startsWith('/app/leads')) {
    return klmNavigation.find((item) => item.path === '/app/funil');
  }
  if (pathname.startsWith('/app/qualidade')) {
    return klmNavigation.find((item) => item.path === '/app/agentes');
  }
  if (pathname.startsWith('/app/playbooks')) {
    return klmNavigation.find((item) => item.path === '/app/configuracoes');
  }
  if (pathname.startsWith('/app/ferramentas') || pathname.startsWith('/app/capacidades/')) {
    return klmNavigation.find((item) => item.path === '/app/configuracoes');
  }
  if (pathname.startsWith('/app/audio')) {
    return klmNavigation.find((item) => item.path === '/app/configuracoes');
  }
  if (pathname.startsWith('/app/conexoes')) {
    return klmNavigation.find((item) => item.path === '/app/integracoes');
  }
  if (pathname.startsWith('/app/omnichannel')) {
    return klmNavigation.find((item) => item.path === '/app/conversas');
  }
  return [...klmNavigation, ...auxiliaryNavigation]
    .sort((a, b) => b.path.length - a.path.length)
    .find((item) => item.path === '/app' ? pathname === '/app' : pathname.startsWith(item.path));
}
