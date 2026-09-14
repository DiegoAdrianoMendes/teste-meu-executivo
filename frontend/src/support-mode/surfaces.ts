import type { SupportAccessScope } from '@/support-mode/scopes';
import {
  SUPPORT_MODE_ROUTES,
  supportModeRoutePath,
  type SupportRouteDefinition,
} from '@/support-mode/routes';

export type SupportWriteActionKind =
  | 'update-workspace-area'
  | 'update-team-member'
  | 'create-client'
  | 'update-matter-next-action'
  | 'update-task-title'
  | 'update-brand-tagline';

export type SupportScopeSurface =
  | {
      scope: SupportAccessScope;
      label: string;
      mode: 'read';
      route: SupportRouteDefinition;
      path: string;
    }
  | {
      scope: SupportAccessScope;
      label: string;
      mode: 'write';
      action: SupportWriteActionKind;
      route: SupportRouteDefinition;
      method: 'POST' | 'PATCH' | 'PUT';
      pathTemplate: string;
    }
  | {
      scope: SupportAccessScope;
      label: string;
      mode: 'tasks';
    }
  | {
      scope: SupportAccessScope;
      label: string;
      mode: 'meetings';
    }
  | {
      scope: SupportAccessScope;
      label: string;
      mode: 'blocked';
      reason: string;
    }
  | {
      scope: SupportAccessScope;
      label: string;
      mode: 'augment';
      reason: string;
    };

export const SUPPORT_SCOPE_SURFACES: readonly SupportScopeSurface[] = [
  {
    scope: 'workspace:read',
    label: 'Workspace',
    mode: 'read',
    route: SUPPORT_MODE_ROUTES.workspacePlanSummary,
    path: supportModeRoutePath(SUPPORT_MODE_ROUTES.workspacePlanSummary),
  },
  {
    scope: 'workspace:write',
    label: 'Operação do workspace',
    mode: 'write',
    action: 'update-workspace-area',
    route: SUPPORT_MODE_ROUTES.workspaceOperationsWrite,
    method: SUPPORT_MODE_ROUTES.workspaceOperationsWrite.method,
    pathTemplate: SUPPORT_MODE_ROUTES.workspaceOperationsWrite.pathTemplate,
  },
  {
    scope: 'members:read',
    label: 'Equipe',
    mode: 'read',
    route: SUPPORT_MODE_ROUTES.membersList,
    path: supportModeRoutePath(SUPPORT_MODE_ROUTES.membersList),
  },
  {
    scope: 'members:write',
    label: 'Alterar acesso da equipe',
    mode: 'write',
    action: 'update-team-member',
    route: SUPPORT_MODE_ROUTES.teamMemberWrite,
    method: SUPPORT_MODE_ROUTES.teamMemberWrite.method,
    pathTemplate: SUPPORT_MODE_ROUTES.teamMemberWrite.pathTemplate,
  },
  {
    scope: 'clients:read',
    label: 'Clientes',
    mode: 'read',
    route: SUPPORT_MODE_ROUTES.clientsList,
    path: supportModeRoutePath(SUPPORT_MODE_ROUTES.clientsList),
  },
  {
    scope: 'clients:write',
    label: 'Criar cliente',
    mode: 'write',
    action: 'create-client',
    route: SUPPORT_MODE_ROUTES.clientsCreate,
    method: SUPPORT_MODE_ROUTES.clientsCreate.method,
    pathTemplate: SUPPORT_MODE_ROUTES.clientsCreate.pathTemplate,
  },
  {
    scope: 'matters:read',
    label: 'Casos',
    mode: 'read',
    route: SUPPORT_MODE_ROUTES.mattersList,
    path: supportModeRoutePath(SUPPORT_MODE_ROUTES.mattersList),
  },
  {
    scope: 'matters:write',
    label: 'Atualizar caso',
    mode: 'write',
    action: 'update-matter-next-action',
    route: SUPPORT_MODE_ROUTES.matterNextActionWrite,
    method: SUPPORT_MODE_ROUTES.matterNextActionWrite.method,
    pathTemplate: SUPPORT_MODE_ROUTES.matterNextActionWrite.pathTemplate,
  },
  {
    scope: 'tasks:read',
    label: 'Tarefas',
    mode: 'tasks',
  },
  {
    scope: 'tasks:write',
    label: 'Atualizar tarefa',
    mode: 'write',
    action: 'update-task-title',
    route: SUPPORT_MODE_ROUTES.taskTitleWrite,
    method: SUPPORT_MODE_ROUTES.taskTitleWrite.method,
    pathTemplate: SUPPORT_MODE_ROUTES.taskTitleWrite.pathTemplate,
  },
  {
    scope: 'meetings:read',
    label: 'Compromissos',
    mode: 'meetings',
  },
  {
    scope: 'meetings:write',
    label: 'Criar e editar compromissos',
    mode: 'meetings',
  },
  {
    scope: 'documents:read',
    label: 'Documentos',
    mode: 'blocked',
    reason: 'O backend expõe documentos somente em rotas aninhadas de casos, autorizadas como matters:read. Downloads e uploads também são vedados no support mode.',
  },
  {
    scope: 'finance:read',
    label: 'Financeiro',
    mode: 'read',
    route: SUPPORT_MODE_ROUTES.financeOverview,
    path: supportModeRoutePath(SUPPORT_MODE_ROUTES.financeOverview),
  },
  {
    scope: 'finance:write',
    label: 'Operações financeiras',
    mode: 'augment',
    reason: 'Este escopo habilita as mutações financeiras tipadas na superfície Financeiro do Blackbox; cada operação continua revalidando a sessão administrativa, o workspace e a permissão finance:manage.',
  },
  {
    scope: 'reports:read',
    label: 'Relatórios',
    mode: 'read',
    route: SUPPORT_MODE_ROUTES.reportsMetrics,
    path: supportModeRoutePath(SUPPORT_MODE_ROUTES.reportsMetrics),
  },
  {
    scope: 'configurations:read',
    label: 'Configurações',
    mode: 'read',
    route: SUPPORT_MODE_ROUTES.configurationEffective,
    path: supportModeRoutePath(SUPPORT_MODE_ROUTES.configurationEffective),
  },
  {
    scope: 'configurations:write',
    label: 'Atualizar configuração',
    mode: 'write',
    action: 'update-brand-tagline',
    route: SUPPORT_MODE_ROUTES.configurationWorkspaceDocumentWrite,
    method: SUPPORT_MODE_ROUTES.configurationWorkspaceDocumentWrite.method,
    pathTemplate: SUPPORT_MODE_ROUTES.configurationWorkspaceDocumentWrite.pathTemplate,
  },
  {
    scope: 'lead_data:read',
    label: 'Dados de leads',
    mode: 'augment',
    reason: 'Este escopo remove mascaramento nas superfícies primárias aprovadas. Ele não autoriza uma rota tenant independente.',
  },
];

export function supportSurfaceFor(scope: SupportAccessScope): SupportScopeSurface {
  const surface = SUPPORT_SCOPE_SURFACES.find((candidate) => candidate.scope === scope);
  if (!surface) throw new Error(`Escopo de suporte sem superfície: ${scope}`);
  return surface;
}
