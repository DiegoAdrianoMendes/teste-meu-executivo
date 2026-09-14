/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BlackboxTasks from '@/pages/BlackboxTasks';
import type { PlatformTask } from '@/services/platform-tasks';
import type { PlatformWorkItem } from '@/services/platform-work-items';

const manifest = vi.hoisted(() => ({
  effectiveUser: {
    id: 'root-1',
    name: 'Root',
    isPlatformRoot: true,
    platformAccessActive: true,
    platformRolePackage: 'platform_owner',
    platformPermissions: [] as Array<{ module: string; action: string }>,
  },
}));
const taskApi = vi.hoisted(() => ({ list: vi.fn() }));
const workItemApi = vi.hoisted(() => ({
  list: vi.fn(),
  counts: vi.fn(),
  command: vi.fn(),
  reconcile: vi.fn(),
}));

vi.mock('@/blackbox/session-manifest', () => ({
  useBlackboxManifest: () => ({ effectiveUser: manifest.effectiveUser }),
}));
vi.mock('@/services/platform-tasks', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-tasks')>(
    '@/services/platform-tasks',
  );
  return { ...actual, listPlatformTasks: taskApi.list };
});
vi.mock('@/services/platform-work-items', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-work-items')>(
    '@/services/platform-work-items',
  );
  return {
    ...actual,
    fetchPlatformWorkItems: workItemApi.list,
    fetchPlatformWorkItemCounts: workItemApi.counts,
    dispatchPlatformWorkItemCommand: workItemApi.command,
    reconcilePlatformWorkItems: workItemApi.reconcile,
  };
});
vi.mock('@/components/platform/PlatformTaskEditor', () => ({
  PlatformTaskCreateDialog: ({ open }: { open: boolean }) => (
    open ? <div role="dialog">Criar tarefa interna</div> : null
  ),
}));
vi.mock('@/components/platform/PlatformTaskDrawer', () => ({
  PlatformTaskDrawer: ({ open, summary }: { open: boolean; summary: PlatformTask | null }) => (
    open ? <div role="dialog">Detalhe: {summary?.title}</div> : null
  ),
}));
vi.mock('@/components/platform/PlatformWorkflowAdministration', () => ({
  PlatformWorkflowAdministration: () => <div>Administração de work items</div>,
}));

const task = {
  id: 'task-1',
  title: 'Preparar resposta ao incidente',
  description: 'Tarefa interna sem conteúdo privado do cliente.',
  status: 'pending',
  priority: 'P0',
  dueAt: '2026-07-30T18:00:00Z',
  reviewRequired: true,
  reviewState: 'draft',
  version: 2,
  participants: [{ role: 'assignee' }],
} as unknown as PlatformTask;
const unassignedWorkItem: PlatformWorkItem = {
  id: 'work-item-unassigned-1',
  sourceType: 'support_ticket',
  sourceId: 'ticket-1',
  sourceVersion: '3',
  kind: 'task',
  state: 'open',
  priority: 'P1',
  workspaceId: 'workspace-1',
  assignedUserId: null,
  assignedTeamId: null,
  safeTitle: 'Ticket crítico sem responsável',
  safeSummary: 'Resumo operacional seguro.',
  href: '/blackbox/support-tickets?sourceType=support_ticket&sourceId=ticket-1&tab=tickets&focus=context&workspaceId=workspace-1',
  allowedCommands: ['assign_to_me'],
  openedAt: '2026-07-30T12:00:00Z',
  dueAt: '2026-07-30T13:00:00Z',
  snoozedUntil: null,
  sourceUpdatedAt: '2026-07-30T12:30:00Z',
  projectedAt: '2026-07-30T12:30:00Z',
  projectionVersion: 4,
  payloadSafe: {},
};
const commandRestrictedWorkItem: PlatformWorkItem = {
  ...unassignedWorkItem,
  id: 'work-item-unassigned-2',
  sourceId: 'ticket-2',
  safeTitle: 'Ticket sem permissão de comando',
  allowedCommands: [],
};

function renderPage(initialEntry = '/blackbox/tasks') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}><BlackboxTasks /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  manifest.effectiveUser = {
    id: 'root-1',
    name: 'Root',
    isPlatformRoot: true,
    platformAccessActive: true,
    platformRolePackage: 'platform_owner',
    platformPermissions: [],
  };
  taskApi.list.mockReset().mockResolvedValue({
    items: [task], total: 1, limit: 50, offset: 0, hasMore: false,
  });
  workItemApi.list.mockReset().mockResolvedValue({ items: [], nextCursor: null, hasMore: false });
  workItemApi.counts.mockReset().mockResolvedValue({ total: 0, open: 0, overdue: 0, unassigned: 0 });
  workItemApi.command.mockReset().mockResolvedValue({});
  workItemApi.reconcile.mockReset().mockResolvedValue({
    adapters: [],
    projectedAt: '2026-07-30T12:30:00Z',
  });
});

afterEach(cleanup);

describe('Blackbox platform tasks', () => {
  it('renders all operational views and uses platform-wide filters only for root', async () => {
    renderPage();

    expect(await screen.findByText('Preparar resposta ao incidente')).toBeInTheDocument();
    for (const label of [
      'Minhas tarefas', 'Minha equipe', 'Revisões', 'Work items',
      'Não atribuídas', 'P0/P1', 'Vencidas', 'Concluídas',
    ]) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    expect(taskApi.list).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'mine',
      statuses: ['pending', 'in_progress', 'blocked'],
      limit: 50,
      offset: 0,
    }));

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'P0/P1' }), {
      button: 0,
      ctrlKey: false,
    });
    await waitFor(() => expect(taskApi.list).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'platform',
      priorities: ['P0', 'P1'],
      statuses: ['pending', 'in_progress', 'blocked'],
    })));

    fireEvent.click(
      await screen.findByRole('button', { name: /Preparar resposta ao incidente/i }),
    );
    expect(screen.getByRole('dialog')).toHaveTextContent(
      'Detalhe: Preparar resposta ao incidente',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Nova tarefa' }));
    expect(screen.getByText('Criar tarefa interna')).toBeInTheDocument();
  });

  it('keeps broad tabs scoped to the current employee and hides create without permission', async () => {
    manifest.effectiveUser = {
      id: 'support-1',
      name: 'Suporte',
      isPlatformRoot: false,
      platformAccessActive: true,
      platformRolePackage: 'support',
      platformPermissions: [{ module: 'platform_tasks', action: 'view' }],
    };
    renderPage();
    expect(await screen.findByText('Preparar resposta ao incidente')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nova tarefa' })).not.toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Não atribuídas' }), {
      button: 0,
      ctrlKey: false,
    });
    await waitFor(() => expect(taskApi.list).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'mine',
      unassigned: true,
    })));
  });

  it('shows authorized unassigned work items and executes only backend-provided commands', async () => {
    manifest.effectiveUser = {
      id: 'support-1',
      name: 'Suporte',
      isPlatformRoot: false,
      platformAccessActive: true,
      platformRolePackage: 'support',
      platformPermissions: [
        { module: 'platform_tasks', action: 'view' },
        { module: 'support', action: 'view' },
      ],
    };
    workItemApi.counts.mockResolvedValue({
      total: 2,
      open: 2,
      overdue: 1,
      unassigned: 2,
      byPriority: { P1: 2 },
      bySource: { support_ticket: 2 },
      generatedAt: '2026-07-30T12:30:00Z',
    });
    workItemApi.list.mockImplementation(async (query: { scope: string; unassigned?: boolean }) => (
      query.scope === 'platform' && query.unassigned
        ? {
            items: [unassignedWorkItem, commandRestrictedWorkItem],
            nextCursor: null,
            hasMore: false,
            generatedAt: '2026-07-30T12:30:00Z',
          }
        : {
            items: [],
            nextCursor: null,
            hasMore: false,
            generatedAt: '2026-07-30T12:30:00Z',
          }
    ));
    renderPage();

    fireEvent.change(await screen.findByLabelText('Buscar tarefas internas'), {
      target: { value: 'incidente' },
    });
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Work items' }), {
      button: 0,
      ctrlKey: false,
    });
    expect(await screen.findByLabelText('Buscar work items')).toHaveValue('');
    await waitFor(() => expect(workItemApi.list).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'mine',
      unassigned: undefined,
      search: '',
    })));

    fireEvent.click(await screen.findByRole('button', { name: 'Sem responsável' }));
    expect(await screen.findByText('Ticket crítico sem responsável')).toBeInTheDocument();
    expect(workItemApi.list).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'platform',
      unassigned: true,
    }));

    const restrictedCard = screen.getByText('Ticket sem permissão de comando').closest('article');
    expect(restrictedCard).not.toBeNull();
    expect(within(restrictedCard as HTMLElement).queryByRole('button', { name: 'Assumir' }))
      .not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Assumir' }));
    fireEvent.change(screen.getByLabelText('Motivo'), {
      target: { value: 'Motivo que deve ser descartado.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Assumir' }));
    expect(screen.getByRole('dialog', { name: 'Confirmar: Assumir' })).toBeInTheDocument();
    expect(screen.getByLabelText('Motivo')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Confirmar' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo'), {
      target: { value: 'Assumir chamado crítico da fila.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(workItemApi.command).toHaveBeenCalledWith(
      unassignedWorkItem,
      'assign_to_me',
      'Assumir chamado crítico da fila.',
    ));
  });

  it('reports a partial source reconciliation instead of presenting it as success', async () => {
    workItemApi.reconcile.mockResolvedValue({
      adapters: [{
        sourceType: 'billing',
        status: 'partial',
        projected: 2,
        unchanged: 1,
        reasonCode: 'provider_not_configured',
      }],
      projectedAt: '2026-07-30T12:30:00Z',
    });
    renderPage();

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Work items' }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Reconciliar fontes' }));

    expect(await screen.findByText('Reconciliação parcial')).toBeInTheDocument();
    expect(screen.getByText(/Cobrança ainda possuem cobertura parcial/i)).toBeInTheDocument();
  });

  it('opens every work-item source through an existing canonical Blackbox route', async () => {
    const definitions = [
      ['support_ticket', '/blackbox/support-tickets', 'tickets', 'context'],
      ['incident', '/blackbox/governance/security', 'incidents', 'context'],
      ['support_access', '/blackbox/support-access', 'requests', 'context'],
      ['workspace_onboarding', '/blackbox/workspaces', 'onboarding', 'workspace'],
      ['billing', '/blackbox/billing', 'subscriptions', 'context'],
      ['workspace_invitation', '/blackbox/workspaces', 'invitations', 'context'],
      ['staff_invitation', '/blackbox/team', 'invitations', 'context'],
      ['publication', '/blackbox/announcements', 'publications', 'context'],
      ['configuration', '/blackbox/settings', 'configuration', 'context'],
      ['commercial_request', '/blackbox/workspaces', 'commercial', 'workspace'],
      ['commercial_checkout_reconcile', '/blackbox/workspaces', 'commercial', 'workspace'],
      ['commercial_subscription_reconcile', '/blackbox/workspaces', 'commercial', 'workspace'],
    ] as const;
    const sourceItems = definitions.map(([sourceType, path, tab, focus], index) => {
      const sourceId = `${sourceType}-${index + 1}`;
      const params = new URLSearchParams({ sourceType, sourceId, tab, focus });
      if (!['incident', 'staff_invitation', 'publication', 'configuration'].includes(sourceType)) {
        params.set('workspaceId', 'workspace-1');
      }
      if (sourceType === 'incident') params.set('incident', sourceId);
      if (sourceType === 'configuration') params.set('namespace', 'brand.identity');
      return {
        ...unassignedWorkItem,
        id: `work-item-${index + 1}`,
        sourceType,
        sourceId,
        safeTitle: `Fonte ${sourceType}`,
        href: `${path}?${params.toString()}`,
        allowedCommands: [],
        workspaceId: ['incident', 'staff_invitation', 'publication', 'configuration'].includes(
          sourceType,
        )
          ? null
          : 'workspace-1',
        payloadSafe: sourceType === 'configuration'
          ? { namespace: 'brand.identity' }
          : {},
      } satisfies PlatformWorkItem;
    });
    workItemApi.list.mockResolvedValue({
      items: sourceItems,
      nextCursor: null,
      hasMore: false,
      generatedAt: '2026-07-30T12:30:00Z',
    });

    renderPage('/blackbox/tasks?tab=work-items');

    const links = await screen.findAllByRole('link', { name: 'Abrir fonte' });
    expect(links).toHaveLength(definitions.length);
    links.forEach((link, index) => {
      const parsed = new URL(link.getAttribute('href') ?? '', 'https://blackbox.internal');
      const [sourceType, path, tab, focus] = definitions[index];
      expect(parsed.pathname).toBe(path);
      expect(parsed.searchParams.get('sourceType')).toBe(sourceType);
      expect(parsed.searchParams.get('sourceId')).toBe(`${sourceType}-${index + 1}`);
      expect(parsed.searchParams.get('tab')).toBe(tab);
      expect(parsed.searchParams.get('focus')).toBe(focus);
    });
  });

  it.each([
    ['https://evil.example/steal'],
    ['/blackbox/route-that-does-not-exist'],
    ['/blackbox/billing?sourceType=support_ticket&sourceId=ticket-1&tab=tickets&focus=context&workspaceId=workspace-1'],
    ['/blackbox/support-tickets?sourceType=support_ticket&sourceId=another-ticket&tab=tickets&focus=context&workspaceId=workspace-1'],
  ])('fails closed to the Task Center for unsafe persisted href %s', async (href) => {
    workItemApi.list.mockResolvedValue({
      items: [{
        ...unassignedWorkItem,
        href,
      }],
      nextCursor: null,
      hasMore: false,
      generatedAt: '2026-07-30T12:30:00Z',
    });

    renderPage('/blackbox/tasks?tab=work-items');

    expect(await screen.findByRole('link', { name: 'Abrir fonte' })).toHaveAttribute(
      'href',
      '/blackbox/tasks?tab=work-items&sourceType=support_ticket&sourceId=ticket-1&focus=context&workspaceId=workspace-1',
    );
  });
});
