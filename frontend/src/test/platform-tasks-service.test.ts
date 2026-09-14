import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({
  ApiError: class ApiError extends Error {
    readonly status: number;
    readonly detail: unknown;

    constructor(status: number, message: string, detail?: unknown) {
      super(message);
      this.status = status;
      this.detail = detail;
    }
  },
  apiFetch: http.apiFetch,
}));

import { ApiError } from '@/services/http';
import {
  addPlatformTaskComment,
  classifyPlatformTaskError,
  createPlatformTask,
  executePlatformTaskCommand,
  getPlatformTask,
  listPlatformTaskEvents,
  listPlatformTasks,
  replacePlatformTaskAssignments,
  updatePlatformTask,
  updatePlatformTaskParticipantState,
  type PlatformTaskCreateInput,
} from '@/services/platform-tasks';

afterEach(() => vi.clearAllMocks());

describe('platform task transport', () => {
  it('serializes the complete server-side filter and pagination contract', () => {
    listPlatformTasks({
      scope: 'platform',
      statuses: ['pending', 'blocked'],
      priorities: ['P0', 'P1'],
      workspaceId: 'workspace/1',
      search: '  falha critica  ',
      overdue: true,
      unassigned: false,
      limit: 25,
      offset: 50,
    });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/tasks?scope=platform&limit=25&offset=50'
      + '&statuses=pending&statuses=blocked&priorities=P0&priorities=P1'
      + '&workspaceId=workspace%2F1&search=falha+critica&overdue=true&unassigned=false',
    );
  });

  it('uses typed CRUD and CAS assignment endpoints without exposing generic mutation routes', () => {
    const createInput: PlatformTaskCreateInput = {
      title: 'Investigar incidente',
      description: 'Verificar a fila sem copiar conteudo tenant.',
      priority: 'P0',
      ownerUserId: 'owner-1',
      reviewerUserId: 'reviewer-1',
      reviewRequired: true,
      participants: [{ userId: 'executor-1', role: 'assignee', requiredForCompletion: true }],
      workspaceId: 'workspace-1',
      linkedIncidentId: 'incident-1',
      reason: 'Incidente critico exige coordenacao interna.',
    };

    getPlatformTask('task/1');
    createPlatformTask(createInput);
    updatePlatformTask('task/1', {
      expectedVersion: 3,
      title: 'Investigar incidente P0',
      reason: 'Escopo atualizado depois da triagem.',
    });
    replacePlatformTaskAssignments('task/1', {
      expectedVersion: 4,
      ownerUserId: 'owner-2',
      participants: [{ userId: 'executor-2', role: 'assignee' }],
      reason: 'Plantao transferido para a equipe disponivel.',
    });

    expect(http.apiFetch.mock.calls[0]).toEqual(['/api/v1/platform/tasks/task%2F1']);
    expect(http.apiFetch.mock.calls[1][0]).toBe('/api/v1/platform/tasks');
    expect(http.apiFetch.mock.calls[1][1]).toMatchObject({
      method: 'POST',
      body: JSON.stringify(createInput),
    });
    expect(http.apiFetch.mock.calls[1][1].headers['Idempotency-Key']).toMatch(
      /^platform-task:create:/,
    );
    expect(http.apiFetch.mock.calls[2]).toEqual([
      '/api/v1/platform/tasks/task%2F1',
      {
        method: 'PATCH',
        body: JSON.stringify({
          expectedVersion: 3,
          title: 'Investigar incidente P0',
          reason: 'Escopo atualizado depois da triagem.',
        }),
      },
    ]);
    expect(http.apiFetch.mock.calls[3]).toEqual([
      '/api/v1/platform/tasks/task%2F1/assignments',
      {
        method: 'PUT',
        body: JSON.stringify({
          expectedVersion: 4,
          ownerUserId: 'owner-2',
          participants: [{ userId: 'executor-2', role: 'assignee' }],
          reason: 'Plantao transferido para a equipe disponivel.',
        }),
      },
    ]);
  });

  it('preserves expectedVersion across progress, commands, comments and history', () => {
    updatePlatformTaskParticipantState(
      'task-1',
      'executor/1',
      5,
      'completed',
      'Executor concluiu sua entrega.',
    );
    executePlatformTaskCommand(
      'task-1',
      'submit-for-review',
      6,
      { reason: 'Entregas obrigatorias concluidas.', comment: 'Pronto para revisar.' },
    );
    addPlatformTaskComment('task-1', 7, 'Atualizacao factual registrada.');
    listPlatformTaskEvents('task/1', 25, 50);

    expect(http.apiFetch.mock.calls[0]).toEqual([
      '/api/v1/platform/tasks/task-1/participants/executor%2F1/state',
      {
        method: 'PUT',
        body: JSON.stringify({
          expectedVersion: 5,
          state: 'completed',
          reason: 'Executor concluiu sua entrega.',
        }),
      },
    ]);
    expect(http.apiFetch.mock.calls[1]).toEqual([
      '/api/v1/platform/tasks/task-1/commands/submit-for-review',
      {
        method: 'POST',
        headers: { 'Idempotency-Key': 'platform-task:task-1:6:submit-for-review' },
        body: JSON.stringify({
          expectedVersion: 6,
          reason: 'Entregas obrigatorias concluidas.',
          comment: 'Pronto para revisar.',
        }),
      },
    ]);
    expect(http.apiFetch.mock.calls[2][0]).toBe('/api/v1/platform/tasks/task-1/comments');
    expect(http.apiFetch.mock.calls[2][1]).toMatchObject({
      method: 'POST',
      body: JSON.stringify({ expectedVersion: 7, comment: 'Atualizacao factual registrada.' }),
    });
    expect(http.apiFetch.mock.calls[2][1].headers['Idempotency-Key']).toMatch(
      /^platform-task:comment:/,
    );
    expect(http.apiFetch.mock.calls[3]).toEqual([
      '/api/v1/platform/tasks/task%2F1/events?offset=25&limit=50',
    ]);
  });

  it('maps authorization, stale-version and availability failures to explicit UI states', () => {
    expect(classifyPlatformTaskError(new ApiError(403, 'negado'))).toBe('forbidden');
    expect(classifyPlatformTaskError(new ApiError(404, 'ausente'))).toBe('not_found');
    expect(classifyPlatformTaskError(new ApiError(409, 'versao obsoleta'))).toBe('conflict');
    expect(classifyPlatformTaskError(new ApiError(429, 'limite'))).toBe('unavailable');
    expect(classifyPlatformTaskError(new ApiError(503, 'indisponivel'))).toBe('unavailable');
    expect(classifyPlatformTaskError(new Error('falha local'))).toBe('error');
  });
});
