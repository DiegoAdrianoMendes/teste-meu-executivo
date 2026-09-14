import type { PermissionAction, PermissionModule } from '@/types';
import { klmNavigation } from '@/klm/navigation';

/**
 * Rótulos legíveis (PT-BR) para módulos e ações técnicas de permissão,
 * usados pelo `PermissionGate` e pelo `ModuleAccessDenied` para nunca
 * mostrar chaves técnicas ("kanban", "manage") para usuários leigos.
 *
 * A navegação (`klmNavigation`) já mapeia cada item do menu ao seu módulo
 * técnico com um label amigável — reaproveitamos isso como fonte primária
 * e completamos com módulos que não têm item de navegação próprio.
 */
const NAV_MODULE_LABELS: Partial<Record<PermissionModule, string>> = {};
for (const item of klmNavigation) {
  if (!NAV_MODULE_LABELS[item.permission.module]) {
    NAV_MODULE_LABELS[item.permission.module] = item.label;
  }
}

const EXTRA_MODULE_LABELS: Partial<Record<PermissionModule, string>> = {
  dashboard: 'Início',
  kanban: 'Funil',
  lista: 'Leads',
  mensagens: 'Conversas',
  relatorios: 'Relatórios e KPIs',
  aprovacoes: 'Aprovações',
  configuracoes: 'Configurações',
  admin: 'Administração',
  workspace: 'Workspace',
  users: 'Usuários',
  memberships: 'Membros da equipe',
  roles: 'Papéis',
  permissions: 'Permissões',
  finance: 'Financeiro',
  integrations: 'Integrações e conexões',
  automations: 'Automações',
  agents: 'Agentes de IA',
  notifications: 'Notificações',
  branding: 'Identidade visual',
  security: 'Segurança',
  audit: 'Auditoria',
  lead_data: 'Dados sensíveis de clientes',
  legal: 'Jurídico',
};

export function getModuleLabel(module: PermissionModule): string {
  return NAV_MODULE_LABELS[module] ?? EXTRA_MODULE_LABELS[module] ?? module;
}

const ACTION_LABELS: Partial<Record<PermissionAction, string>> = {
  view: 'visualizar',
  create: 'criar',
  edit: 'editar',
  delete: 'excluir',
  approve: 'aprovar',
  export: 'exportar',
  manage_ai: 'gerenciar a IA',
  send_message: 'enviar mensagens',
  manage: 'gerenciar',
};

export function getActionLabel(action: PermissionAction): string {
  return ACTION_LABELS[action] ?? action;
}
