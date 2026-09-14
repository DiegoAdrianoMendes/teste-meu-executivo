import { apiFetch } from '@/services/http';

export type PlatformSearchEntityType =
  | 'accounts'
  | 'users'
  | 'staff'
  | 'tickets'
  | 'leads'
  | 'conversations'
  | 'incidents'
  | 'errors'
  | 'support_access'
  | 'releases'
  | 'subscriptions'
  | 'workspace_invitations'
  | 'staff_invitations';

export interface PlatformSearchResult {
  id: string;
  entityType: PlatformSearchEntityType;
  label: string;
  secondaryLabel: string | null;
  publicIdentifier: string | null;
  workspaceId: string | null;
  workspaceLabel: string | null;
  status: string | null;
  maskedEmail: string | null;
  badges: string[];
  metadata: Record<string, unknown>;
  allowedCommands: string[];
  href: string;
}

export type PlatformCommandKind = 'navigation' | 'create' | 'mutation';
export type PlatformCommandRisk = 'none' | 'low' | 'high' | 'critical';

export interface PlatformCommandDefinition {
  id: string;
  label: string;
  description: string;
  kind: PlatformCommandKind;
  scope: 'global' | 'entity';
  module: string;
  action: string;
  href: string | null;
  entityTypes: PlatformSearchEntityType[];
  risk: PlatformCommandRisk;
  reasonRequired: boolean;
  confirmationRequired: boolean;
}

export interface PlatformCommandCatalogResponse {
  commands: PlatformCommandDefinition[];
}

export interface PlatformSearchGroup {
  entityType: PlatformSearchEntityType;
  state: 'ready' | 'unavailable';
  items: PlatformSearchResult[];
  errorCode: string | null;
}

export interface PlatformSearchResponse { groups: PlatformSearchGroup[] }

export interface PlatformLookupResponse {
  entityType: PlatformSearchEntityType;
  state: 'ready' | 'unavailable';
  items: PlatformSearchResult[];
  nextCursor: string | null;
  errorCode: string | null;
}

export function searchPlatform(query: string): Promise<PlatformSearchResponse> {
  return apiFetch('/api/v1/platform/search/query', {
    method: 'POST',
    body: JSON.stringify({ query, limitPerType: 8, includeInactive: false }),
  });
}

export function fetchPlatformCommandCatalog(): Promise<PlatformCommandCatalogResponse> {
  return apiFetch('/api/v1/platform/commands/catalog');
}

export async function executePlatformQuickCommand(input: {
  commandId: string;
  item: PlatformSearchResult;
  actorUserId: string;
  reason?: string;
}): Promise<unknown> {
  const encodedId = encodeURIComponent(input.item.id);
  switch (input.commandId) {
    case 'ticket.assume': {
      const expectedVersion = input.item.metadata.version;
      if (typeof expectedVersion !== 'number') {
        throw new Error('A versão atual do ticket não está disponível. Refaça a busca.');
      }
      return apiFetch(`/api/v1/platform/support/tickets/${encodedId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          expectedVersion,
          assignedToUserId: input.actorUserId,
        }),
      });
    }
    case 'lead.assume':
      return apiFetch(`/api/v1/platform/growth/prospects/${encodedId}`, {
        method: 'PATCH',
        body: JSON.stringify({ ownerUserId: input.actorUserId }),
      });
    case 'conversation.assume':
      return apiFetch(`/api/v1/platform/inbox/conversations/${encodedId}`, {
        method: 'PATCH',
        body: JSON.stringify({ assignedToUserId: input.actorUserId }),
      });
    case 'incident.assume':
      return apiFetch(`/api/v1/platform/governance/security/incidents/${encodedId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ownerUserId: input.actorUserId,
          note: input.reason?.trim() || 'Incidente assumido pela Central de Comandos.',
        }),
      });
    default:
      throw new Error('Comando rápido não reconhecido pelo cliente.');
  }
}

export function lookupPlatformEntity(input: {
  entityType: PlatformSearchEntityType;
  purpose: string;
  query?: string;
  workspaceId?: string | null;
  cursor?: string | null;
  limit?: number;
}): Promise<PlatformLookupResponse> {
  return apiFetch('/api/v1/platform/lookups/query', {
    method: 'POST',
    body: JSON.stringify({
      entityType: input.entityType,
      purpose: input.purpose,
      query: input.query ?? '',
      workspaceId: input.workspaceId ?? null,
      cursor: input.cursor ?? null,
      limit: input.limit ?? 20,
    }),
  });
}

export async function resolvePlatformEntity(input: {
  entityType: PlatformSearchEntityType;
  purpose: string;
  ids: string[];
  workspaceId?: string | null;
}): Promise<PlatformLookupResponse> {
  const chunks: string[][] = [];
  for (let index = 0; index < input.ids.length; index += 100) {
    chunks.push(input.ids.slice(index, index + 100));
  }
  const responses: PlatformLookupResponse[] = [];
  for (const ids of chunks) {
    responses.push(await apiFetch('/api/v1/platform/lookups/resolve', {
      method: 'POST',
      body: JSON.stringify({
        entityType: input.entityType,
        purpose: input.purpose,
        ids,
        workspaceId: input.workspaceId ?? null,
      }),
    }));
  }
  return {
    entityType: input.entityType,
    state: responses.some((response) => response.state === 'unavailable') ? 'unavailable' : 'ready',
    items: responses.flatMap((response) => response.items),
    nextCursor: null,
    errorCode: responses.find((response) => response.errorCode)?.errorCode ?? null,
  };
}
