import type { Permission } from '@/types';

export const SUPPORT_ACCESS_SCOPE_OPTIONS = [
  { value: 'workspace:read', label: 'Workspace: leitura', mode: 'read' },
  { value: 'workspace:write', label: 'Workspace: alteração', mode: 'write' },
  { value: 'members:read', label: 'Equipe: leitura', mode: 'read' },
  { value: 'members:write', label: 'Equipe: alteração', mode: 'write' },
  { value: 'clients:read', label: 'Clientes: leitura', mode: 'read' },
  { value: 'clients:write', label: 'Clientes: alteração', mode: 'write' },
  { value: 'matters:read', label: 'Casos: leitura', mode: 'read' },
  { value: 'matters:write', label: 'Casos: alteração', mode: 'write' },
  { value: 'tasks:read', label: 'Tarefas: leitura', mode: 'read' },
  { value: 'tasks:write', label: 'Tarefas: alteração', mode: 'write' },
  { value: 'meetings:read', label: 'Compromissos: leitura', mode: 'read' },
  { value: 'meetings:write', label: 'Compromissos: alteração', mode: 'write' },
  { value: 'documents:read', label: 'Documentos: leitura', mode: 'read' },
  { value: 'finance:read', label: 'Financeiro: leitura', mode: 'read' },
  { value: 'finance:write', label: 'Financeiro: alteração', mode: 'write' },
  { value: 'reports:read', label: 'Relatórios: leitura', mode: 'read' },
  { value: 'configurations:read', label: 'Configurações: leitura', mode: 'read' },
  { value: 'configurations:write', label: 'Configurações: alteração', mode: 'write' },
  { value: 'lead_data:read', label: 'Dados de leads: leitura', mode: 'read' },
] as const;

export const SUPPORT_ACCESS_MAX_SCOPES = SUPPORT_ACCESS_SCOPE_OPTIONS.length;

export type SupportAccessScope = (typeof SUPPORT_ACCESS_SCOPE_OPTIONS)[number]['value'];

const SUPPORT_SCOPE_PERMISSIONS: Partial<Record<SupportAccessScope, Permission[]>> = {
  'workspace:read': [{ module: 'workspace', action: 'view', scope: 'workspace' }],
  'workspace:write': [{ module: 'workspace', action: 'edit', scope: 'workspace' }],
  'members:read': [
    { module: 'users', action: 'view', scope: 'workspace' },
    { module: 'memberships', action: 'view', scope: 'workspace' },
    { module: 'roles', action: 'view', scope: 'workspace' },
  ],
  'clients:read': [
    { module: 'lista', action: 'view', scope: 'workspace' },
    { module: 'kanban', action: 'view', scope: 'workspace' },
  ],
  'clients:write': [
    { module: 'lista', action: 'edit', scope: 'workspace' },
    { module: 'kanban', action: 'edit', scope: 'workspace' },
  ],
  'matters:read': [
    { module: 'lista', action: 'view', scope: 'workspace' },
    { module: 'kanban', action: 'view', scope: 'workspace' },
  ],
  'matters:write': [
    { module: 'lista', action: 'edit', scope: 'workspace' },
    { module: 'kanban', action: 'edit', scope: 'workspace' },
  ],
  'tasks:read': [{ module: 'tasks', action: 'view', scope: 'workspace' }],
  'tasks:write': [
    { module: 'tasks', action: 'create', scope: 'workspace' },
    { module: 'tasks', action: 'edit', scope: 'workspace' },
    { module: 'tasks', action: 'assign', scope: 'workspace' },
    { module: 'tasks', action: 'review', scope: 'workspace' },
    { module: 'tasks', action: 'complete', scope: 'workspace' },
    { module: 'tasks', action: 'comment', scope: 'workspace' },
  ],
  // A superfície de compromissos autoriza diretamente pelos escopos
  // `meetings:*`; não sintetize autoridade sobre Lista ou Kanban.
  'meetings:read': [],
  'meetings:write': [],
  'documents:read': [{ module: 'lista', action: 'view', scope: 'workspace' }],
  'finance:read': [{ module: 'finance', action: 'view', scope: 'workspace' }],
  'finance:write': [{ module: 'finance', action: 'manage', scope: 'workspace' }],
  'reports:read': [{ module: 'relatorios', action: 'view', scope: 'workspace' }],
  'configurations:read': [{ module: 'configuracoes', action: 'view', scope: 'workspace' }],
  'configurations:write': [{ module: 'configuracoes', action: 'edit', scope: 'workspace' }],
  'lead_data:read': [{ module: 'lead_data', action: 'view', scope: 'workspace' }],
};

export function supportPermissions(scopes: SupportAccessScope[]): Permission[] {
  const permissions = scopes.flatMap((scope) => SUPPORT_SCOPE_PERMISSIONS[scope] ?? []);
  return permissions.filter((permission, index) => (
    permissions.findIndex((candidate) => (
      candidate.module === permission.module
      && candidate.action === permission.action
      && candidate.scope === permission.scope
    )) === index
  ));
}

const KNOWN_SUPPORT_SCOPES = new Set<string>(
  SUPPORT_ACCESS_SCOPE_OPTIONS.map((option) => option.value),
);

export function isKnownSupportAccessScope(value: unknown): value is SupportAccessScope {
  return typeof value === 'string' && KNOWN_SUPPORT_SCOPES.has(value);
}

export function hasPrimarySupportScope(scopes: readonly SupportAccessScope[]): boolean {
  return scopes.some((scope) => scope !== 'lead_data:read');
}

export function isSupportScopeSelectionValid(scopes: readonly SupportAccessScope[]): boolean {
  return scopes.length > 0
    && scopes.length <= SUPPORT_ACCESS_MAX_SCOPES
    && new Set(scopes).size === scopes.length
    && scopes.every(isKnownSupportAccessScope)
    && (!scopes.includes('lead_data:read') || hasPrimarySupportScope(scopes));
}

export function parseExactSupportAccessScopes(value: unknown): SupportAccessScope[] | null {
  if (!Array.isArray(value) || !value.every(isKnownSupportAccessScope)) return null;
  const scopes = [...value];
  return isSupportScopeSelectionValid(scopes) ? scopes : null;
}
