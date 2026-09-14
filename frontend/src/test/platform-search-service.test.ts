import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  executePlatformQuickCommand,
  fetchPlatformCommandCatalog,
  type PlatformSearchResult,
} from '@/services/platform-search';

const result = (entityType: PlatformSearchResult['entityType'], metadata = {}) => ({
  id: 'f41ef52a-cf5f-4dd7-a440-b864186d00d7',
  entityType,
  label: 'Registro seguro',
  secondaryLabel: null,
  publicIdentifier: null,
  workspaceId: null,
  workspaceLabel: null,
  status: 'open',
  maskedEmail: null,
  badges: [],
  metadata,
  allowedCommands: [],
  href: '/blackbox/example',
}) satisfies PlatformSearchResult;

beforeEach(() => {
  http.apiFetch.mockReset();
});

describe('platform search command service', () => {
  it('loads the session-authorized catalog from the backend', () => {
    fetchPlatformCommandCatalog();
    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/platform/commands/catalog');
  });

  it('executes ticket assignment through the typed support endpoint', () => {
    executePlatformQuickCommand({
      commandId: 'ticket.assume',
      item: result('tickets', { version: 4 }),
      actorUserId: 'actor-1',
    });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/support/tickets/f41ef52a-cf5f-4dd7-a440-b864186d00d7',
      {
        method: 'PATCH',
        body: JSON.stringify({ expectedVersion: 4, assignedToUserId: 'actor-1' }),
      },
    );
  });

  it('uses only known domain endpoints for the other quick mutations', async () => {
    await executePlatformQuickCommand({
      commandId: 'lead.assume',
      item: result('leads'),
      actorUserId: 'actor-2',
    });
    await executePlatformQuickCommand({
      commandId: 'conversation.assume',
      item: result('conversations'),
      actorUserId: 'actor-2',
    });
    await executePlatformQuickCommand({
      commandId: 'incident.assume',
      item: result('incidents'),
      actorUserId: 'actor-2',
      reason: 'Assumindo resposta do incidente crítico.',
    });

    expect(http.apiFetch.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/platform/growth/prospects/f41ef52a-cf5f-4dd7-a440-b864186d00d7',
      '/api/v1/platform/inbox/conversations/f41ef52a-cf5f-4dd7-a440-b864186d00d7',
      '/api/v1/platform/governance/security/incidents/f41ef52a-cf5f-4dd7-a440-b864186d00d7',
    ]);
  });

  it('fails closed for an unknown command', async () => {
    await expect(executePlatformQuickCommand({
      commandId: 'arbitrary.execute',
      item: result('tickets'),
      actorUserId: 'actor-1',
    })).rejects.toThrow('Comando rápido não reconhecido');
    expect(http.apiFetch).not.toHaveBeenCalled();
  });
});
