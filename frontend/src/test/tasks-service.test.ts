import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({
  ApiError: class ApiError extends Error {},
  apiFetch: http.apiFetch,
}));

import {
  addTaskComment,
  createClientContract,
  createContextualTask,
  listClientContracts,
  listCollaborativeTasks,
  listTaskFilterClientOptions,
  listTaskFilterMatterOptions,
  listTaskEvents,
  listTaskMatterOptions,
  listTaskMemberOptions,
  listTaskMemberOptionsPage,
  listTaskMatterDocuments,
  listTaskLabelCatalog,
  listTaskDependencyCandidates,
  addTaskDependency,
  updateTaskParticipantState,
} from '@/services/tasks';

afterEach(() => vi.clearAllMocks());

describe('collaborative task transport', () => {
  it('exposes canonical client contracts and creates a contract-scoped task', async () => {
    http.apiFetch.mockResolvedValue({ id: 'contract-1' });

    await listClientContracts('client-1', 'mensal');
    await createClientContract({ clientId: 'client-1', title: 'Assessoria mensal' });
    await createContextualTask({
      title: 'Revisar contrato',
      context: { type: 'contract', clientId: 'client-1', contractId: 'contract-1' },
    });

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/legal/client-contracts?clientId=client-1&search=mensal',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/legal/client-contracts',
      { method: 'POST', body: JSON.stringify({ clientId: 'client-1', title: 'Assessoria mensal', status: 'active' }) },
    );
    expect(http.apiFetch.mock.calls[2]?.[0]).toBe('/api/v1/tasks');
    expect(JSON.parse(http.apiFetch.mock.calls[2]?.[1]?.body as string)).toMatchObject({
      title: 'Revisar contrato',
      context: { type: 'contract', clientId: 'client-1', contractId: 'contract-1' },
    });
  });

  it('serializes every operational filter without changing cursor pagination', async () => {
    http.apiFetch.mockResolvedValue({ items: [], nextCursor: null, hasMore: false });

    await listCollaborativeTasks({
      view: 'team',
      limit: 25,
      cursor: 'opaque-cursor',
      matterRef: 'matter-reference',
      clientRef: 'client-reference',
      ownerRef: 'owner-reference',
      assigneeRef: 'assignee-reference',
      reviewerRef: 'reviewer-reference',
      labelRef: 'label-reference',
      priority: 'alta',
      status: 'blocked',
      dueFrom: '2026-08-01T03:00:00.000Z',
      dueTo: '2026-09-01T03:00:00.000Z',
    });

    const [path] = http.apiFetch.mock.calls[0] as [string];
    const request = new URL(path, 'https://app.meuexecutivo.com');
    expect(request.pathname).toBe('/api/v1/tasks');
    expect(Object.fromEntries(request.searchParams)).toEqual({
      scope: 'team',
      limit: '25',
      matterRef: 'matter-reference',
      clientRef: 'client-reference',
      ownerRef: 'owner-reference',
      assigneeRef: 'assignee-reference',
      reviewerRef: 'reviewer-reference',
      labelRef: 'label-reference',
      priority: 'alta',
      status: 'blocked',
      dueFrom: '2026-08-01T03:00:00.000Z',
      dueTo: '2026-09-01T03:00:00.000Z',
      cursor: 'opaque-cursor',
    });
  });

  it('loads the persistent label catalog without putting internal ids in the URL', async () => {
    http.apiFetch.mockResolvedValue([]);
    await listTaskLabelCatalog();
    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/tasks/label-catalog');
  });

  it('hydrates task filter selectors through scope-aware lookup endpoints', async () => {
    http.apiFetch.mockResolvedValue([]);

    await listTaskFilterMatterOptions('mine', 'Silva', ['matter-reference']);
    await listTaskFilterClientOptions('team', 'Acme', ['client-reference']);

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/tasks/filter-options/matters?scope=mine&limit=50&search=Silva&selectedRef=matter-reference',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/tasks/filter-options/clients?scope=team&limit=50&search=Acme&selectedRef=client-reference',
    );
  });

  it('uses the task-owned safe lookup endpoints instead of Kanban or UUID input', async () => {
    http.apiFetch.mockResolvedValue([]);

    await listTaskMatterOptions('Silva');
    await listTaskMemberOptions('Marina');

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/tasks/matter-options?limit=50&search=Silva',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/tasks/member-options?limit=50&search=Marina',
    );
  });

  it('uses an actor-scoped dependency lookup and submits only its opaque handle', async () => {
    http.apiFetch.mockResolvedValue([]);

    await listTaskDependencyCandidates('task-current');
    await addTaskDependency('task-current', 4, 'opaque-task-handle', 'Vínculo revisado pelo responsável.');

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/tasks/task-current/dependency-candidates?limit=50',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/tasks/task-current/dependencies',
      {
        method: 'POST',
        body: JSON.stringify({
          expectedVersion: 4,
          predecessorRef: 'opaque-task-handle',
          reason: 'Vínculo revisado pelo responsável.',
        }),
      },
    );
    expect(JSON.stringify(http.apiFetch.mock.calls)).not.toContain('predecessorTaskId');
  });

  it('does not send one-character searches that the backend contract rejects', async () => {
    http.apiFetch.mockResolvedValue({ items: [], nextCursor: null, hasMore: false });

    await listCollaborativeTasks({ view: 'mine', search: 'a', limit: 50 });
    await listTaskMatterOptions('a');
    await listTaskMemberOptions('a');

    expect(http.apiFetch).toHaveBeenNthCalledWith(1, '/api/v1/tasks?scope=mine&limit=50');
    expect(http.apiFetch).toHaveBeenNthCalledWith(2, '/api/v1/tasks/matter-options?limit=50');
    expect(http.apiFetch).toHaveBeenNthCalledWith(3, '/api/v1/tasks/member-options?limit=50');
  });

  it('pages the append-only history instead of truncating it at a fixed first window', async () => {
    http.apiFetch.mockResolvedValue({ items: [], total: 240 });

    await listTaskEvents('task-1', 200, 50);

    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/tasks/task-1/events?offset=200&limit=50');
  });

  it('uses the paginated member lookup contract for teams larger than one page', async () => {
    http.apiFetch.mockResolvedValue({ items: [], total: 245, limit: 100, offset: 100, hasMore: true });

    await listTaskMemberOptionsPage('Marina', 100, 100);

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/tasks/member-options/page?limit=100&offset=100&scope=workspace&search=Marina',
    );
  });

  it('separates assignment lookup from a team-scoped operational filter', async () => {
    http.apiFetch.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0, hasMore: false });

    await listTaskMemberOptionsPage('', 0, 100, ['member-reference'], 'team');

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/tasks/member-options/page?limit=100&offset=0&scope=team&selectedRef=member-reference',
    );
  });

  it('updates one executor through the versioned participant-state command', async () => {
    http.apiFetch.mockResolvedValue({ id: 'task-1', version: 9 });

    await updateTaskParticipantState('task-1', 'user-2', 8, 'completed', 'Parte obrigatória conferida.');

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/tasks/task-1/participants/user-2/state',
      {
        method: 'PUT',
        body: JSON.stringify({
          expectedVersion: 8,
          state: 'completed',
          reason: 'Parte obrigatória conferida.',
        }),
      },
    );
  });

  it('sends comment attachments and mentions through the typed collaboration contract', async () => {
    http.apiFetch.mockResolvedValue({ id: 'task-1', version: 4 });

    await addTaskComment('task-1', 3, 'Documento conferido.', {
      documentIds: ['document-1'],
      mentionedUserIds: ['member-1'],
    });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/tasks/task-1/comments',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          expectedVersion: 3,
          comment: 'Documento conferido.',
          documentIds: ['document-1'],
          mentionedUserIds: ['member-1'],
        }),
      }),
    );
  });

  it('loads attachment choices exclusively from the selected Caso 360', async () => {
    http.apiFetch.mockResolvedValue([]);

    await listTaskMatterDocuments('matter/opaque');

    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/matters/matter%2Fopaque/documents');
  });
});
