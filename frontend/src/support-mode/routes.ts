import type { SupportAccessScope } from '@/support-mode/scopes';

export type SupportRouteMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';

export interface SupportRouteDefinition {
  method: SupportRouteMethod;
  pathTemplate: string;
  scope: SupportAccessScope;
  query?: Readonly<Record<string, string>>;
  /** Chaves opcionais tipadas; valores fixos continuam declarados em query. */
  allowedQueryKeys?: readonly string[];
}

const UUID_SEGMENT = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export const SUPPORT_MODE_ROUTES = {
  workspaceCapabilityManifest: {
    method: 'GET',
    pathTemplate: '/api/v1/workspaces/capability-manifest',
    scope: 'workspace:read',
  },
  workspacePlanSummary: {
    method: 'GET',
    pathTemplate: '/api/v1/workspaces/plan-summary',
    scope: 'workspace:read',
  },
  workspaceOperationsRead: {
    method: 'GET',
    pathTemplate: '/api/v1/workspaces/settings/operations',
    scope: 'workspace:read',
  },
  workspaceOperationsWrite: {
    method: 'PATCH',
    pathTemplate: '/api/v1/workspaces/settings/operations',
    scope: 'workspace:write',
  },
  membersList: {
    method: 'GET',
    pathTemplate: '/api/v1/users',
    scope: 'members:read',
    query: { limit: '100' },
  },
  teamMembersList: {
    method: 'GET',
    pathTemplate: '/api/v1/admin/team-members',
    scope: 'members:read',
  },
  teamMemberWrite: {
    method: 'PATCH',
    pathTemplate: '/api/v1/admin/team-members/:membershipId',
    scope: 'members:write',
  },
  clientsList: {
    method: 'GET',
    pathTemplate: '/api/v1/clients',
    scope: 'clients:read',
    query: { limit: '100' },
  },
  clientsCreate: {
    method: 'POST',
    pathTemplate: '/api/v1/clients',
    scope: 'clients:write',
  },
  mattersList: {
    method: 'GET',
    pathTemplate: '/api/v1/matters',
    scope: 'matters:read',
    query: { limit: '100' },
  },
  matterNextActionWrite: {
    method: 'PATCH',
    pathTemplate: '/api/v1/matters/:matterId',
    scope: 'matters:write',
  },
  tasksForMatter: {
    method: 'GET',
    pathTemplate: '/api/v1/matters/:matterId/tasks',
    scope: 'tasks:read',
  },
  tasksWorkspaceList: {
    method: 'GET',
    pathTemplate: '/api/v1/tasks',
    scope: 'tasks:read',
    query: { scope: 'workspace' },
    allowedQueryKeys: ['limit', 'cursor', 'search', 'status', 'reviewState'],
  },
  taskRead: {
    method: 'GET',
    pathTemplate: '/api/v1/tasks/:taskId',
    scope: 'tasks:read',
  },
  taskEvents: {
    method: 'GET',
    pathTemplate: '/api/v1/tasks/:taskId/events',
    scope: 'tasks:read',
    allowedQueryKeys: ['offset', 'limit'],
  },
  taskMemberOptionsPage: {
    method: 'GET',
    pathTemplate: '/api/v1/tasks/member-options/page',
    scope: 'tasks:read',
    allowedQueryKeys: ['search', 'offset', 'limit'],
  },
  taskTitleWrite: {
    method: 'PATCH',
    pathTemplate: '/api/v1/tasks/:taskId',
    scope: 'tasks:write',
  },
  taskParticipantsWrite: {
    method: 'PUT',
    pathTemplate: '/api/v1/tasks/:taskId/participants',
    scope: 'tasks:write',
  },
  taskParticipantStateWrite: {
    method: 'PUT',
    pathTemplate: '/api/v1/tasks/:taskId/participants/:participantUserId/state',
    scope: 'tasks:write',
  },
  taskCommandStart: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/start', scope: 'tasks:write',
  },
  taskCommandBlock: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/block', scope: 'tasks:write',
  },
  taskCommandUnblock: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/unblock', scope: 'tasks:write',
  },
  taskCommandSubmitReview: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/submit-for-review', scope: 'tasks:write',
  },
  taskCommandApprove: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/approve', scope: 'tasks:write',
  },
  taskCommandRequestChanges: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/request-changes', scope: 'tasks:write',
  },
  taskCommandComplete: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/complete', scope: 'tasks:write',
  },
  taskCommandReopen: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/reopen', scope: 'tasks:write',
  },
  taskCommandCancel: {
    method: 'POST', pathTemplate: '/api/v1/tasks/:taskId/commands/cancel', scope: 'tasks:write',
  },
  taskCommentWrite: {
    method: 'POST',
    pathTemplate: '/api/v1/tasks/:taskId/comments',
    scope: 'tasks:write',
  },
  taskReminderRulesRead: {
    method: 'GET',
    pathTemplate: '/api/v1/tasks/:taskId/reminder-rules',
    scope: 'tasks:read',
  },
  taskReminderRulesCreate: {
    method: 'POST',
    pathTemplate: '/api/v1/tasks/:taskId/reminder-rules',
    scope: 'tasks:write',
  },
  taskReminderRuleWrite: {
    method: 'PATCH',
    pathTemplate: '/api/v1/tasks/:taskId/reminder-rules/:ruleId',
    scope: 'tasks:write',
  },
  taskReminderRuleDelete: {
    method: 'DELETE',
    pathTemplate: '/api/v1/tasks/:taskId/reminder-rules/:ruleId',
    scope: 'tasks:write',
    allowedQueryKeys: ['expectedVersion'],
  },
  meetingsList: {
    method: 'GET',
    pathTemplate: '/api/v1/meetings',
    scope: 'meetings:read',
  },
  meetingsCreate: {
    method: 'POST',
    pathTemplate: '/api/v1/meetings',
    scope: 'meetings:write',
  },
  meetingWrite: {
    method: 'PATCH',
    pathTemplate: '/api/v1/meetings/:meetingId',
    scope: 'meetings:write',
  },
  meetingParticipantsWrite: {
    method: 'PUT',
    pathTemplate: '/api/v1/meetings/:meetingId/participants',
    scope: 'meetings:write',
  },
  calendarSettingsRead: {
    method: 'GET',
    pathTemplate: '/api/v1/agenda/settings',
    scope: 'configurations:read',
  },
  calendarSettingsWrite: {
    method: 'PUT',
    pathTemplate: '/api/v1/agenda/settings',
    scope: 'configurations:write',
  },
  financeOverview: {
    method: 'GET',
    pathTemplate: '/api/v1/finance/overview',
    scope: 'finance:read',
  },
  reportsMetrics: {
    method: 'GET',
    pathTemplate: '/api/v1/reports/metrics',
    scope: 'reports:read',
  },
  configurationEffective: {
    method: 'GET',
    pathTemplate: '/api/v1/configurations/brand.identity/effective',
    scope: 'configurations:read',
  },
  configurationWorkspaceDocumentRead: {
    method: 'GET',
    pathTemplate: '/api/v1/configurations/brand.identity/documents/workspace',
    scope: 'configurations:read',
  },
  configurationWorkspaceDocumentWrite: {
    method: 'PUT',
    pathTemplate: '/api/v1/configurations/brand.identity/documents/workspace',
    scope: 'configurations:write',
  },
  // Agent Platform V3 is tenant-scoped when entered through an approved
  // support session. Read-only inspection and mutations deliberately use the
  // existing configuration scopes; platform RBAC is still checked by the
  // Blackbox surface before any request can be made.
  agentPlatformNodes: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/nodes',
    scope: 'configurations:read',
  },
  agentPlatformCatalog: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/catalog',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformHumanRoutingCatalog: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/human-routing/catalog',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformHumanRoutingPolicies: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/human-routing/policies',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformDecisions: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/decisions',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId', 'limit', 'offset'],
  },
  agentPlatformDecisionOverview: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/decisions/overview',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformOwnerAlertChannel: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/owner-alert-channel',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformContextOptions: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/runs/context-options',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId', 'contextType', 'query', 'limit'],
  },
  agentPlatformRuns: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/runs',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId', 'limit', 'offset'],
  },
  agentPlatformDeployments: {
    method: 'GET',
    pathTemplate: '/api/v1/agent-platform/deployments',
    scope: 'configurations:read',
    allowedQueryKeys: ['workspaceId', 'limit', 'offset'],
  },
  agentPlatformDeploymentCreate: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/deployments',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformDeploymentValidate: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/deployments/:deploymentId/validate',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformDeploymentPublish: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/deployments/:deploymentId/publish',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformDeploymentPause: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/deployments/:deploymentId/pause',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformContextRunCreate: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/runs/context',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformRunCancel: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/runs/:runId/cancel',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformRunRetry: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/runs/:runId/retry',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformRunHumanInput: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/runs/:runId/human-input',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformDecisionCreate: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/decisions',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformDecisionAcknowledge: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/decisions/:decisionId/acknowledge',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
  agentPlatformDecisionResolve: {
    method: 'POST',
    pathTemplate: '/api/v1/agent-platform/decisions/:decisionId/resolve',
    scope: 'configurations:write',
    allowedQueryKeys: ['workspaceId'],
  },
} as const satisfies Record<string, SupportRouteDefinition>;

export const SUPPORT_MODE_ROUTE_ALLOWLIST: readonly SupportRouteDefinition[] = Object.values(SUPPORT_MODE_ROUTES);

function parseRoute(path: string): URL | null {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) return null;
  try {
    const route = new URL(path, 'http://support-mode.local');
    if (route.origin !== 'http://support-mode.local' || route.hash) return null;
    return route;
  } catch {
    return null;
  }
}

function matchesTemplate(pathname: string, template: string): boolean {
  const pathSegments = pathname.split('/');
  const templateSegments = template.split('/');
  if (pathSegments.length !== templateSegments.length) return false;

  return templateSegments.every((segment, index) => {
    if (!segment.startsWith(':')) return pathSegments[index] === segment;
    return UUID_SEGMENT.test(pathSegments[index] ?? '');
  });
}

function matchesQuery(
  searchParams: URLSearchParams,
  expected: SupportRouteDefinition['query'],
  allowedQueryKeys: SupportRouteDefinition['allowedQueryKeys'],
): boolean {
  const expectedEntries = Object.entries(expected ?? {});
  const optional = new Set(allowedQueryKeys ?? []);
  if (!expectedEntries.every(([key, value]) => (
    searchParams.getAll(key).length === 1 && searchParams.get(key) === value
  ))) return false;
  const fixed = new Set(expectedEntries.map(([key]) => key));
  for (const key of new Set(searchParams.keys())) {
    if (!fixed.has(key) && !optional.has(key)) return false;
    if (searchParams.getAll(key).length !== 1) return false;
  }
  return true;
}

export function matchSupportModeRoute(
  path: string,
  method = 'GET',
): SupportRouteDefinition | null {
  const route = parseRoute(path);
  if (!route) return null;
  const normalizedMethod = method.toUpperCase();
  return SUPPORT_MODE_ROUTE_ALLOWLIST.find((candidate) => (
    candidate.method === normalizedMethod
    && matchesTemplate(route.pathname, candidate.pathTemplate)
    && matchesQuery(route.searchParams, candidate.query, candidate.allowedQueryKeys)
  )) ?? null;
}

export function supportModeRoutePath(
  route: SupportRouteDefinition,
  params: Readonly<Record<string, string>> = {},
): string {
  const pathname = route.pathTemplate.replace(/:([A-Za-z][A-Za-z0-9]*)/g, (_match, name: string) => {
    const value = params[name]?.trim();
    if (!value || !UUID_SEGMENT.test(value)) {
      throw new Error(`Identificador inválido para a rota de suporte: ${name}.`);
    }
    return encodeURIComponent(value);
  });
  const query = new URLSearchParams(route.query ?? {}).toString();
  return query ? `${pathname}?${query}` : pathname;
}
