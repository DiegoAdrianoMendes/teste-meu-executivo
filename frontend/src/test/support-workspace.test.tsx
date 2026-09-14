/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

const supportMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  events: vi.fn(),
}));

vi.mock('@/services/http', async () => {
  const actual = await vi.importActual<typeof import('@/services/http')>('@/services/http');
  return { ...actual, apiFetch: supportMocks.apiFetch };
});

vi.mock('@/services/support-access', async () => {
  const actual = await vi.importActual<typeof import('@/services/support-access')>('@/services/support-access');
  return { ...actual, listPlatformSupportAccessEvents: supportMocks.events };
});

vi.mock('@/klm/components/AgentPlatformOperationsPanel', () => ({
  AgentPlatformOperationsPanel: ({ canManage, currentUserId, isPlatformRoot, workspaceId }: {
    canManage: boolean;
    currentUserId: string | null;
    isPlatformRoot: boolean;
    workspaceId: string;
  }) => <output data-testid="support-agent-platform">{JSON.stringify({ canManage, currentUserId, isPlatformRoot, workspaceId })}</output>,
}));

import { SUPPORT_ACCESS_SCOPE_OPTIONS, type SupportAccessScope } from '@/services/support-access';
import {
  activateStagedSupportMode,
  clearSupportModeCredential,
  completeSupportModeEnding,
  resolveSupportRequest,
  stageSupportModeCredential,
  type SupportModeSession,
} from '@/support-mode/session';
import { SupportWorkspace } from '@/support-mode/SupportWorkspace';
import { matchSupportModeRoute } from '@/support-mode/routes';
import { SUPPORT_SCOPE_SURFACES } from '@/support-mode/surfaces';

const writeMatrix = [
  ['workspace:write', 'update-workspace-area', 'PATCH', '/api/v1/workspaces/settings/operations'],
  ['members:write', 'update-team-member', 'PATCH', '/api/v1/admin/team-members/:membershipId'],
  ['clients:write', 'create-client', 'POST', '/api/v1/clients'],
  ['matters:write', 'update-matter-next-action', 'PATCH', '/api/v1/matters/:matterId'],
  ['tasks:write', 'update-task-title', 'PATCH', '/api/v1/tasks/:taskId'],
  ['configurations:write', 'update-brand-tagline', 'PUT', '/api/v1/configurations/brand.identity/documents/workspace'],
] as const;

function session(scopes: SupportAccessScope[]): SupportModeSession {
  return {
    grantId: 'grant-support',
    workspaceId: 'workspace-support',
    workspaceName: 'Workspace em suporte',
    scopes,
    expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    sessionType: 'support_approved',
  };
}

function mockApi(path: string, init?: RequestInit): Promise<unknown> {
  if (path === '/api/v1/workspaces/settings/operations' && !init?.method) {
    return Promise.resolve({
      areaActivations: [{
        key: 'trabalhista',
        label: 'Trabalhista',
        enabled: true,
        selectedFlowTemplateId: null,
        followUpProfile: 'balanced',
        humanInterventionMode: 'human_in_the_loop',
      }],
    });
  }
  if (path === '/api/v1/configurations/brand.identity/documents/workspace' && !init?.method) {
    return Promise.resolve({ etag: 'etag-1', schemaVersion: '1.0', payload: { tagline: 'Atual' } });
  }
  if (path === '/api/v1/admin/team-members' && !init?.method) {
    return Promise.resolve([{
      membershipId: '33333333-3333-4333-8333-333333333333',
      name: 'Ana Advogada',
      email: 'ana@example.com',
      workspaceRole: 'operator',
      accessPackage: 'lawyer',
      status: 'active',
    }]);
  }
  if (path === '/api/v1/matters?limit=100' && !init?.method) {
    return Promise.resolve([{
      id: '11111111-1111-4111-8111-111111111111',
      caseNumber: '0001234-56.2026.8.07.0001',
      demandType: 'Ação de cobrança',
      client: { name: 'Cliente autorizado' },
    }]);
  }
  if (path === '/api/v1/matters/11111111-1111-4111-8111-111111111111/tasks' && !init?.method) {
    return Promise.resolve([{
      id: '22222222-2222-4222-8222-222222222222',
      title: 'Revisar documentos',
      status: 'pending',
    }]);
  }
  if (path === '/api/v1/tasks?scope=workspace&limit=50' && !init?.method) {
    return Promise.resolve({
      items: [{
        id: '22222222-2222-4222-8222-222222222222',
        matterId: '11111111-1111-4111-8111-111111111111',
        title: 'Revisar documentos autorizados',
        description: 'Conteúdo disponível somente na sessão temporária.',
        status: 'pending',
        priority: 'media',
        assignedTo: null,
        dueDate: null,
        dueAt: null,
        legacyDueText: null,
        reviewDueAt: null,
        reviewRequired: false,
        reviewState: 'not_required',
        reviewRound: 0,
        version: 1,
        createdByUserId: null,
        completedAt: null,
        cancelledAt: null,
        cancelledByUserId: null,
        cancellationReason: null,
        participants: [],
        reviewRounds: [],
        isAiGenerated: false,
        createdAt: '2026-07-30T12:00:00Z',
        sourceExceptionId: null,
        matterLabel: 'Ação de cobrança',
        clientLabel: 'Cliente autorizado',
        myRoles: [],
      }],
      total: null,
      nextCursor: null,
      hasMore: false,
    });
  }
  return Promise.resolve([]);
}

function renderWorkspace(scopes: SupportAccessScope[], activateVault = false) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const supportSession = session(scopes);
  if (activateVault) {
    stageSupportModeCredential({
      token: 'support-token',
      grantId: supportSession.grantId,
      workspaceId: supportSession.workspaceId,
      scopes,
      expiresAt: supportSession.expiresAt,
    });
    activateStagedSupportMode({
      grantId: supportSession.grantId,
      workspaceId: supportSession.workspaceId,
      workspaceName: supportSession.workspaceName,
      sessionType: supportSession.sessionType,
    });
  }
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <SupportWorkspace session={supportSession} onExit={vi.fn().mockResolvedValue(undefined)} />
    </QueryClientProvider>,
  );
  return { ...rendered, queryClient };
}

afterEach(() => {
  cleanup();
  clearSupportModeCredential();
  completeSupportModeEnding();
  vi.clearAllMocks();
});

describe('SupportWorkspace native scope surfaces', () => {
  it('routes Agent Platform V3 through finite configuration scopes', () => {
    expect(matchSupportModeRoute(
      '/api/v1/agent-platform/runs?workspaceId=11111111-1111-4111-8111-111111111111&limit=25&offset=0',
      'GET',
    )?.scope).toBe('configurations:read');
    expect(matchSupportModeRoute(
      '/api/v1/agent-platform/runs/11111111-1111-4111-8111-111111111111/retry?workspaceId=11111111-1111-4111-8111-111111111111',
      'POST',
    )?.scope).toBe('configurations:write');
    expect(matchSupportModeRoute(
      '/api/v1/agent-platform/human-routing/policies?workspaceId=11111111-1111-4111-8111-111111111111',
      'GET',
    )?.scope).toBe('configurations:read');
  });

  it('exposes Agent Platform only inside an authorized root-direct support session', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const rootSession: SupportModeSession = {
      ...session(['configurations:read', 'configurations:write']),
      sessionType: 'root_direct',
    };
    render(
      <QueryClientProvider client={queryClient}>
        <SupportWorkspace
          actor={null}
          session={rootSession}
          onExit={vi.fn().mockResolvedValue(undefined)}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Agentes e chamadas humanas' }));
    expect(await screen.findByTestId('support-agent-platform')).toHaveTextContent('"canManage":true');
    expect(screen.getByTestId('support-agent-platform')).toHaveTextContent('"isPlatformRoot":true');
    expect(screen.getByTestId('support-agent-platform')).toHaveTextContent('"workspaceId":"workspace-support"');
  });

  it('maps every offered scope to exactly one visible surface', () => {
    const offeredScopes = SUPPORT_ACCESS_SCOPE_OPTIONS.map((option) => option.value).sort();
    const surfaceScopes = SUPPORT_SCOPE_SURFACES.map((surface) => surface.scope).sort();

    expect(surfaceScopes).toEqual(offeredScopes);
    expect(new Set(surfaceScopes).size).toBe(surfaceScopes.length);
    expect(SUPPORT_SCOPE_SURFACES.find((surface) => surface.scope === 'tasks:read')).toMatchObject({ mode: 'tasks' });
    expect(SUPPORT_SCOPE_SURFACES.find((surface) => surface.scope === 'meetings:read')).toMatchObject({ mode: 'meetings' });
    expect(SUPPORT_SCOPE_SURFACES.find((surface) => surface.scope === 'meetings:write')).toMatchObject({ mode: 'meetings' });
    expect(SUPPORT_SCOPE_SURFACES.find((surface) => surface.scope === 'documents:read')).toMatchObject({ mode: 'blocked' });
    expect(SUPPORT_SCOPE_SURFACES.find((surface) => surface.scope === 'finance:write')).toMatchObject({ mode: 'augment' });
    expect(SUPPORT_SCOPE_SURFACES.find((surface) => surface.scope === 'lead_data:read')).toMatchObject({ mode: 'augment' });
  });

  it.each(writeMatrix)('maps %s to its exact native action', (scope, action, method, pathTemplate) => {
    expect(SUPPORT_SCOPE_SURFACES.find((surface) => surface.scope === scope)).toEqual(expect.objectContaining({
      mode: 'write',
      action,
      method,
      pathTemplate,
    }));

    const path = pathTemplate
      .replace(':membershipId', '33333333-3333-4333-8333-333333333333')
      .replace(':matterId', '11111111-1111-4111-8111-111111111111')
      .replace(':taskId', '22222222-2222-4222-8222-222222222222');
    stageSupportModeCredential({
      token: 'support-token',
      grantId: `grant-${scope}`,
      workspaceId: 'workspace-support',
      scopes: [scope],
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: `grant-${scope}`,
      workspaceId: 'workspace-support',
      workspaceName: 'Workspace em suporte',
    });
    expect(resolveSupportRequest(path, method)).toMatchObject({ kind: 'support', requiredScope: scope });
    clearSupportModeCredential();
    completeSupportModeEnding();
  });

  it('routes each executable read through its exact approved scope', () => {
    for (const surface of SUPPORT_SCOPE_SURFACES) {
      if (surface.mode !== 'read') continue;
      stageSupportModeCredential({
        token: 'support-token',
        grantId: `grant-${surface.scope}`,
        workspaceId: 'workspace-support',
        scopes: [surface.scope],
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      });
      activateStagedSupportMode({
        grantId: `grant-${surface.scope}`,
        workspaceId: 'workspace-support',
        workspaceName: 'Workspace em suporte',
      });
      expect(resolveSupportRequest(surface.path, 'GET')).toMatchObject({
        kind: 'support',
        requiredScope: surface.scope,
      });
      clearSupportModeCredential();
      completeSupportModeEnding();
    }
  });

  it('keeps task lookup inside the approved task-read route and workspace session', () => {
    stageSupportModeCredential({
      token: 'support-token',
      grantId: 'grant-task-lookup',
      workspaceId: 'workspace-support',
      scopes: ['tasks:read'],
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-task-lookup',
      workspaceId: 'workspace-support',
      workspaceName: 'Workspace em suporte',
    });

    expect(resolveSupportRequest(
      '/api/v1/matters/11111111-1111-4111-8111-111111111111/tasks',
      'GET',
    )).toMatchObject({ kind: 'support', requiredScope: 'tasks:read' });
  });

  it('keeps meetings, calendar settings, and public booking on distinct boundaries', () => {
    const scopes: SupportAccessScope[] = [
      'meetings:read',
      'meetings:write',
      'configurations:read',
      'configurations:write',
    ];
    stageSupportModeCredential({
      token: 'support-token',
      grantId: 'grant-meeting-boundaries',
      workspaceId: 'workspace-support',
      scopes,
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-meeting-boundaries',
      workspaceId: 'workspace-support',
      workspaceName: 'Workspace em suporte',
    });

    expect(resolveSupportRequest('/api/v1/meetings', 'GET')).toMatchObject({
      kind: 'support',
      requiredScope: 'meetings:read',
    });
    expect(resolveSupportRequest('/api/v1/meetings', 'POST')).toMatchObject({
      kind: 'support',
      requiredScope: 'meetings:write',
    });
    expect(resolveSupportRequest(
      '/api/v1/meetings/77777777-7777-4777-8777-777777777777',
      'PATCH',
    )).toMatchObject({ kind: 'support', requiredScope: 'meetings:write' });
    expect(resolveSupportRequest(
      '/api/v1/meetings/77777777-7777-4777-8777-777777777777/participants',
      'PUT',
    )).toMatchObject({ kind: 'support', requiredScope: 'meetings:write' });
    expect(resolveSupportRequest('/api/v1/agenda/settings', 'GET')).toMatchObject({
      kind: 'support',
      requiredScope: 'configurations:read',
    });
    expect(resolveSupportRequest('/api/v1/agenda/settings', 'PUT')).toMatchObject({
      kind: 'support',
      requiredScope: 'configurations:write',
    });
    expect(resolveSupportRequest('/api/v1/agenda/public/token-publico', 'POST')).toEqual({
      kind: 'blocked',
      requiredScope: null,
    });
    expect(resolveSupportRequest('/api/v1/meetings?includePrivate=true', 'GET')).toEqual({
      kind: 'blocked',
      requiredScope: null,
    });
  });

  it('never promotes a meetings read grant into write or configuration access', () => {
    stageSupportModeCredential({
      token: 'support-token',
      grantId: 'grant-meeting-read-only',
      workspaceId: 'workspace-support',
      scopes: ['meetings:read'],
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-meeting-read-only',
      workspaceId: 'workspace-support',
      workspaceName: 'Workspace em suporte',
    });

    expect(resolveSupportRequest('/api/v1/meetings', 'GET')).toMatchObject({
      kind: 'support',
      requiredScope: 'meetings:read',
    });
    expect(resolveSupportRequest('/api/v1/meetings', 'POST')).toEqual({
      kind: 'blocked',
      requiredScope: 'meetings:write',
    });
    expect(resolveSupportRequest('/api/v1/agenda/settings', 'GET')).toEqual({
      kind: 'blocked',
      requiredScope: 'configurations:read',
    });
  });

  it('allows only the workspace-wide tenant task query and blocks it immediately after revocation', () => {
    stageSupportModeCredential({
      token: 'support-token',
      grantId: 'grant-workspace-task-list',
      workspaceId: 'workspace-support',
      scopes: ['tasks:read'],
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-workspace-task-list',
      workspaceId: 'workspace-support',
      workspaceName: 'Workspace em suporte',
    });

    const authorized = '/api/v1/tasks?scope=workspace&limit=50&search=revisao';
    expect(resolveSupportRequest(authorized, 'GET')).toMatchObject({
      kind: 'support',
      requiredScope: 'tasks:read',
    });
    expect(resolveSupportRequest('/api/v1/tasks?scope=mine&limit=50', 'GET')).toEqual({
      kind: 'blocked',
      requiredScope: null,
    });
    expect(resolveSupportRequest('/api/v1/tasks?scope=workspace&private=true', 'GET')).toEqual({
      kind: 'blocked',
      requiredScope: null,
    });
    expect(resolveSupportRequest('/api/v1/tasks?scope=workspace&limit=50&limit=100', 'GET')).toEqual({
      kind: 'blocked',
      requiredScope: null,
    });

    clearSupportModeCredential('manual');
    expect(resolveSupportRequest(authorized, 'GET')).toEqual({
      kind: 'blocked',
      requiredScope: null,
    });
  });

  it.each([
    'https://attacker.example/api/v1/clients?limit=100',
    '//attacker.example/api/v1/clients?limit=100',
    '/api/v1/clients?limit=100#support-token',
    '/api/v1\\clients?limit=100',
  ])('rejects non-relative or ambiguous support destinations: %s', (path) => {
    expect(matchSupportModeRoute(path, 'GET')).toBeNull();
  });

  it('renders every granted scope and refuses tenant tasks without an active administrative vault', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);
    const allScopes = SUPPORT_ACCESS_SCOPE_OPTIONS.map((option) => option.value);
    const { container } = renderWorkspace(allScopes);

    for (const scope of allScopes) {
      expect(container.querySelector(`[data-scope="${scope}"]`)).not.toBeNull();
    }

    fireEvent.click(container.querySelector('[data-scope="tasks:read"]') as HTMLElement);
    expect(await screen.findByText('Sessão administrativa ativa obrigatória')).toBeInTheDocument();
    expect(supportMocks.apiFetch).not.toHaveBeenCalledWith(expect.stringContaining('/tasks'), expect.anything());

    fireEvent.click(container.querySelector('[data-scope="documents:read"]') as HTMLElement);
    expect(await screen.findByText(/Downloads e uploads também são vedados/)).toBeInTheDocument();

    for (const [, action] of writeMatrix) {
      const scope = writeMatrix.find((entry) => entry[1] === action)?.[0];
      fireEvent.click(container.querySelector(`[data-scope="${scope}"]`) as HTMLElement);
      expect(await screen.findByTestId(`support-write-${action}`)).toBeInTheDocument();
    }
  });

  it('lists tenant tasks only inside the active workspace session and never copies them into a generic read surface', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);
    const { container } = renderWorkspace(['tasks:read'], true);

    fireEvent.click(container.querySelector('[data-scope="tasks:read"]') as HTMLElement);

    expect(await screen.findByText('Conteúdo tenant autorizado nesta sessão')).toBeInTheDocument();
    expect(await screen.findByText('Revisar documentos autorizados')).toBeInTheDocument();
    expect(screen.getByText(/exclusivamente a Workspace em suporte/)).toBeInTheDocument();
    expect(supportMocks.apiFetch).toHaveBeenCalledWith('/api/v1/tasks?scope=workspace&limit=50');
  });

  it('does not mix the platform identity into the temporary support workspace', () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);

    renderWorkspace(['clients:read']);

    expect(screen.getByText('Registro ativo')).toBeInTheDocument();
    expect(screen.getByText(/Nenhuma credencial administrativa é usada/)).toBeInTheDocument();
    expect(supportMocks.events).not.toHaveBeenCalled();
  });

  it('executes a write only after explicit confirmation, then reports and invalidates safely', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);
    const { queryClient } = renderWorkspace(['clients:write'], true);

    fireEvent.change(screen.getByLabelText('Nome do cliente'), { target: { value: 'Cliente autorizado' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar cliente' }));
    expect(supportMocks.apiFetch).not.toHaveBeenCalledWith('/api/v1/clients', expect.objectContaining({ method: 'POST' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar alteração' }));

    await waitFor(() => expect(supportMocks.apiFetch).toHaveBeenCalledWith('/api/v1/clients', {
      method: 'POST',
      body: JSON.stringify({ name: 'Cliente autorizado' }),
    }));
    expect(await screen.findByText('Cliente criado e operação enviada à trilha.')).toBeInTheDocument();
    expect(JSON.stringify(queryClient.getMutationCache().getAll())).not.toContain('support-token');
  });

  it('updates a workspace member only with paired read and write scopes', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);
    renderWorkspace(['members:read', 'members:write'], true);

    fireEvent.click(screen.getByRole('button', { name: /Alterar acesso da equipe/ }));
    expect(await screen.findByText('Ana Advogada · ana@example.com')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: 'Status do vínculo' }));
    fireEvent.click(screen.getByRole('option', { name: 'Suspenso' }));
    fireEvent.click(screen.getByRole('button', { name: 'Atualizar acesso' }));
    expect(supportMocks.apiFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('33333333-3333-4333-8333-333333333333'),
      expect.objectContaining({ method: 'PATCH' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar alteração' }));

    await waitFor(() => expect(supportMocks.apiFetch).toHaveBeenCalledWith(
      '/api/v1/admin/team-members/33333333-3333-4333-8333-333333333333',
      { method: 'PATCH', body: JSON.stringify({ status: 'suspended' }) },
    ));
  });

  it('selects a matter by human label before updating it and never asks for a UUID', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);
    renderWorkspace(['matters:read', 'matters:write'], true);

    fireEvent.click(screen.getByRole('button', { name: /Atualizar caso/ }));
    expect(screen.queryByText(/ID do caso/i)).not.toBeInTheDocument();
    const matterSelect = await screen.findByRole('combobox', { name: 'Caso do workspace' });
    await waitFor(() => expect(matterSelect).toBeEnabled());
    fireEvent.click(matterSelect);
    fireEvent.click(await screen.findByRole('option', { name: /Cliente autorizado/ }));
    fireEvent.change(screen.getByLabelText('Próxima ação'), { target: { value: 'Enviar proposta revisada' } });
    fireEvent.click(screen.getByRole('button', { name: 'Atualizar caso' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar alteração' }));

    await waitFor(() => expect(supportMocks.apiFetch).toHaveBeenCalledWith(
      '/api/v1/matters/11111111-1111-4111-8111-111111111111',
      { method: 'PATCH', body: JSON.stringify({ nextAction: 'Enviar proposta revisada' }) },
    ));
  });

  it('selects task through its authorized matter and task collections', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);
    renderWorkspace(['matters:read', 'tasks:read', 'tasks:write'], true);

    fireEvent.click(screen.getByRole('button', { name: /Atualizar tarefa/ }));
    expect(screen.queryByText(/ID da tarefa/i)).not.toBeInTheDocument();
    const taskMatterSelect = await screen.findByRole('combobox', { name: 'Caso do workspace' });
    await waitFor(() => expect(taskMatterSelect).toBeEnabled());
    fireEvent.click(taskMatterSelect);
    fireEvent.click(screen.getByRole('option', { name: /Cliente autorizado/ }));
    await waitFor(() => expect(supportMocks.apiFetch).toHaveBeenCalledWith(
      '/api/v1/matters/11111111-1111-4111-8111-111111111111/tasks',
    ));
    const taskSelect = screen.getByRole('combobox', { name: 'Tarefa do caso' });
    await waitFor(() => expect(taskSelect).toBeEnabled());
    fireEvent.click(taskSelect);
    fireEvent.click(screen.getByRole('option', { name: /Revisar documentos/ }));
    fireEvent.change(screen.getByLabelText('Novo título'), { target: { value: 'Protocolar documentos revisados' } });
    fireEvent.click(screen.getByRole('button', { name: 'Atualizar tarefa' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar alteração' }));

    await waitFor(() => expect(supportMocks.apiFetch).toHaveBeenCalledWith(
      '/api/v1/tasks/22222222-2222-4222-8222-222222222222',
      { method: 'PATCH', body: JSON.stringify({ title: 'Protocolar documentos revisados' }) },
    ));
  });

  it('blocks entity writes when the grant omits the corresponding read scopes', async () => {
    supportMocks.apiFetch.mockImplementation(mockApi);
    supportMocks.events.mockResolvedValue([]);
    renderWorkspace(['matters:write'], true);

    fireEvent.click(screen.getByRole('button', { name: /Atualizar caso/ }));
    expect(await screen.findByText(/matters:read também é necessário/)).toBeInTheDocument();
    expect(supportMocks.apiFetch).not.toHaveBeenCalledWith('/api/v1/matters?limit=100');
  });

  it('suppresses stale write callbacks after the grant phase changes', async () => {
    let finishWrite: ((value: unknown) => void) | undefined;
    supportMocks.apiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/v1/clients' && init?.method === 'POST') {
        return new Promise((resolve) => { finishWrite = resolve; });
      }
      return mockApi(path, init);
    });
    supportMocks.events.mockResolvedValue([]);
    const { queryClient } = renderWorkspace(['clients:write'], true);
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries');

    fireEvent.change(screen.getByLabelText('Nome do cliente'), { target: { value: 'Cliente antigo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar cliente' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar alteração' }));
    await waitFor(() => expect(supportMocks.apiFetch).toHaveBeenCalledTimes(1));

    clearSupportModeCredential('manual');
    finishWrite?.({ id: 'cliente-antigo' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Criar cliente' })).toBeEnabled());

    expect(screen.queryByText('Cliente criado e operação enviada à trilha.')).not.toBeInTheDocument();
    expect(screen.queryByText(/requisição temporária foi cancelada/i)).not.toBeInTheDocument();
    expect(invalidateQueries).not.toHaveBeenCalled();
    expect(JSON.stringify(queryClient.getMutationCache().getAll())).not.toContain('support-token');
  });
});
