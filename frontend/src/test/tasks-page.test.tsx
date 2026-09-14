/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import TasksPage from '@/klm/pages/TasksPage';
import { ApiError } from '@/services/http';
import type { EffectiveCapability } from '@/services/platform-commercial-catalog';
import type { TaskReminderRule } from '@/services/task-reminders';
import type { CollaborativeTask } from '@/services/tasks';
import { workspaceExperienceForMode } from '@/klm/workspace-experience';

const api = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  events: vi.fn(),
  members: vi.fn(),
  matters: vi.fn(),
  filterMembers: vi.fn(),
  filterMatters: vi.fn(),
  filterClients: vi.fn(),
  command: vi.fn(),
  comment: vi.fn(),
  update: vi.fn(),
  replaceParticipants: vi.fn(),
  participantState: vi.fn(),
  create: vi.fn(),
  legalResources: vi.fn(),
  matterDocuments: vi.fn(),
  organization: vi.fn(),
  createList: vi.fn(),
  createBoard: vi.fn(),
  createColumn: vi.fn(),
  assignList: vi.fn(),
  placeOnBoard: vi.fn(),
  archive: vi.fn(),
  labelCatalog: vi.fn(),
}));
const access = vi.hoisted(() => ({
  allowed: null as Set<string> | null,
  supportMode: null as null | { sessionType: 'root_direct' | 'support_approved' },
  reminderCapability: null as EffectiveCapability | null,
  commercialCapabilitiesAvailable: true,
}));
const reminders = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  disable: vi.fn(),
}));

vi.mock('@/services/tasks', async (original) => {
  const actual = await original<typeof import('@/services/tasks')>();
  return {
    ...actual,
    listCollaborativeTasks: api.list,
    getCollaborativeTask: api.get,
    listTaskEvents: api.events,
    listTaskMemberOptionsPage: async (...args: Parameters<typeof actual.listTaskMemberOptionsPage>) => {
      const value = await (args.length >= 4 ? api.filterMembers : api.members)(...args);
      return Array.isArray(value)
        ? { items: value, total: value.length, limit: 100, offset: 0, hasMore: false }
        : value;
    },
    listTaskMatterOptions: api.matters,
    listTaskFilterMatterOptions: api.filterMatters,
    listTaskFilterClientOptions: api.filterClients,
    executeTaskCommand: api.command,
    addTaskComment: api.comment,
    updateCollaborativeTask: api.update,
    replaceTaskParticipants: api.replaceParticipants,
    updateTaskParticipantState: api.participantState,
    createCollaborativeTask: api.create,
    getTaskLegalResources: api.legalResources,
    listTaskMatterDocuments: api.matterDocuments,
    getTaskOrganization: api.organization,
    createTaskList: api.createList,
    createTaskBoard: api.createBoard,
    createTaskBoardColumn: api.createColumn,
    assignTaskToList: api.assignList,
    placeTaskOnBoard: api.placeOnBoard,
    archiveTask: api.archive,
    listTaskLabelCatalog: api.labelCatalog,
  };
});
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/services/task-reminders', async (original) => {
  const actual = await original<typeof import('@/services/task-reminders')>();
  return {
    ...actual,
    listTaskReminderRules: reminders.list,
    createTaskReminderRule: reminders.create,
    updateTaskReminderRule: reminders.update,
    disableTaskReminderRule: reminders.disable,
  };
});
vi.mock('@/store/app-context', () => ({
  useAppStore: () => {
    const getCapability = (key: string) => {
      if (key === 'task_reminder_rules') return access.reminderCapability;
      if (!access.commercialCapabilitiesAvailable) return null;
      const limits: Record<string, number> = {
        active_tasks: 500,
        task_participants: 3,
        task_reviews: 1,
        review_rounds: 10,
      };
      if (!(key in limits)) return null;
      return {
        key,
        contracted: true,
        enabled: true,
        permitted: true,
        limit: limits[key],
        graceLimit: null,
        used: 0,
        remaining: limits[key],
        unit: 'items',
        period: 'stock',
        enforcement: 'hard',
        sources: [{ type: 'plan', id: 'offer-start', contribution: limits[key] }],
        denialReason: null,
        upgradeOffer: null,
      };
    };
    return {
      can: (module: string, action = 'view') => access.allowed === null || access.allowed.has(`${module}:${action}`),
      state: { currentUser: { id: 'user-owner', name: 'Dra. Marina' } },
      supportMode: access.supportMode,
      capabilityManifestStatus: access.commercialCapabilitiesAvailable ? 'ready' : 'not_configured',
      getCapability,
      canUseCapability: (key: string) => {
        const capability = getCapability(key);
        return Boolean(capability?.contracted && capability.enabled && capability.permitted);
      },
    };
  },
}));

const task: CollaborativeTask = {
  id: 'task-opaque-id',
  workspaceId: 'workspace-1',
  contextType: 'matter',
  origin: 'manual',
  matterId: 'matter-opaque-id',
  clientId: null,
  contractId: null,
  originResourceId: null,
  originLabel: null,
  parentTaskId: null,
  title: 'Revisar documentação do contrato',
  description: 'Conferir anexos e registrar pendências.',
  status: 'pending',
  priority: 'alta',
  assignedTo: 'user-owner',
  dueDate: null,
  dueAt: '2026-08-01T15:00:00.000Z',
  legacyDueText: null,
  reviewDueAt: '2026-08-01T18:00:00.000Z',
  reviewRequired: true,
  reviewState: 'draft',
  reviewRound: 0,
  version: 3,
  createdByUserId: 'user-owner',
  completedAt: null,
  cancelledAt: null,
  cancelledByUserId: null,
  cancellationReason: null,
  participants: [
    { id: 'participant-owner', userId: 'user-owner', labelSnapshot: 'Dra. Marina', role: 'owner', dueAt: '2026-08-01T14:00:00.000Z', state: 'assigned', requiredForCompletion: false, active: true, createdAt: '2026-07-30T10:00:00.000Z', updatedAt: '2026-07-30T10:00:00.000Z' },
    { id: 'participant-assignee', userId: 'user-assignee', labelSnapshot: 'Dra. Lia', role: 'assignee', dueAt: '2026-08-01T14:30:00.000Z', state: 'assigned', requiredForCompletion: true, active: true, createdAt: '2026-07-30T10:00:00.000Z', updatedAt: '2026-07-30T10:00:00.000Z' },
    { id: 'participant-reviewer', userId: 'user-reviewer', labelSnapshot: 'Dr. Caio', role: 'reviewer', dueAt: '2026-08-01T18:00:00.000Z', state: 'assigned', requiredForCompletion: false, active: true, createdAt: '2026-07-30T10:00:00.000Z', updatedAt: '2026-07-30T10:00:00.000Z' },
  ],
  reviewRounds: [],
  isAiGenerated: false,
  createdAt: '2026-07-30T10:00:00.000Z',
  sourceExceptionId: null,
  matterLabel: 'Caso Silva · Contrato',
  clientLabel: 'João Silva',
  myRoles: ['owner'],
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-search">{location.search}</output>;
}

function renderPage(initialEntry = '/app/tarefas') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <TasksPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderLegalPage(initialEntry = '/app/tarefas') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const context = { workspaceExperience: workspaceExperienceForMode('legal') };
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route path="/app/tarefas" element={<><TasksPage /><LocationProbe /></>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  reminders.list.mockResolvedValue([]);
  api.filterMatters.mockResolvedValue([]);
  api.filterClients.mockResolvedValue([]);
  api.filterMembers.mockResolvedValue({
    items: [],
    total: 0,
    limit: 100,
    offset: 0,
    hasMore: false,
  });
  api.legalResources.mockResolvedValue({ labels: [], subtasks: [], dependencies: [], attachments: [], mentions: [] });
  api.matterDocuments.mockResolvedValue([]);
  api.organization.mockResolvedValue({ lists: [], boards: [], listMemberships: [], placements: [] });
  api.labelCatalog.mockResolvedValue([]);
  access.reminderCapability = {
    key: 'task_reminder_rules',
    contracted: true,
    enabled: true,
    permitted: true,
    limit: 10,
    graceLimit: null,
    used: 0,
    remaining: 10,
    unit: 'rules_per_task',
    period: 'stock',
    enforcement: 'hard',
    sources: [{ type: 'plan', id: 'offer-start', contribution: 10 }],
    denialReason: null,
    upgradeOffer: null,
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  access.allowed = null;
  access.supportMode = null;
  access.reminderCapability = null;
  access.commercialCapabilitiesAvailable = true;
});

function reminderRule(
  id: string,
  state: TaskReminderRule['state'],
  overrides: Partial<TaskReminderRule> = {},
): TaskReminderRule {
  return {
    id,
    workspaceId: 'workspace-1',
    matterId: task.matterId,
    taskId: task.id,
    relativeTo: 'general_due',
    offsetMinutesBefore: 1_440,
    channel: 'in_app',
    recipient: 'owner',
    recipientUserId: null,
    state,
    active: true,
    version: 1,
    deliveryAvailable: true,
    unavailabilityReason: null,
    nextScheduledAt: null,
    deliveries: [],
    createdAt: task.createdAt,
    updatedAt: task.createdAt,
    deletedAt: null,
    ...overrides,
  };
}

describe('TasksPage', () => {
  it('announces the active display mode as a pressed button', async () => {
    renderPage();
    const list = await screen.findByRole('button', { name: 'Lista' });
    const board = screen.getByRole('button', { name: 'Quadro' });
    expect(list).toHaveAttribute('aria-pressed', 'true');
    expect(board).toHaveAttribute('aria-pressed', 'false');
  });

  it('honors the Caso 360 matter deep-link and keeps the human filter in the URL', async () => {
    const legacyMatterId = '11111111-1111-4111-8111-111111111111';
    const matterHandle = 'signed-matter-reference';
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.filterMatters.mockResolvedValue([
      {
        id: legacyMatterId,
        handle: matterHandle,
        label: task.matterLabel,
        clientLabel: task.clientLabel,
      },
    ]);

    renderPage(`/app/tarefas?matter=${legacyMatterId}`);

    expect(await screen.findByText(task.title)).toBeInTheDocument();
    await waitFor(() => expect(api.list).toHaveBeenCalledWith(
      expect.objectContaining({
        view: 'mine',
        matterRef: matterHandle,
        cursor: null,
      }),
    ));
    expect(api.filterMatters).toHaveBeenCalledWith('mine', '', [], [legacyMatterId]);
    expect(screen.getByTestId('location-search')).toHaveTextContent(
      `?matter=${matterHandle}`,
    );
    expect(screen.getByRole('combobox', { name: 'Filtrar por caso' })).toHaveTextContent(
      'João Silva',
    );
    expect(screen.queryByText(legacyMatterId)).not.toBeInTheDocument();
  });

  it('uses the real task contracts for the operational views and collaborative drawer', async () => {
    api.list.mockResolvedValue({ items: [task], total: null, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({
      total: 1,
      items: [{
        id: 'event-opaque-id', taskId: task.id, actorUserId: 'user-owner', eventType: 'created', beforeData: null,
        afterData: { status: 'pending' }, changedPaths: ['status'], comment: null, correlationId: 'correlation',
        sessionType: 'normal', administrativeSessionId: null, supportGrantId: null,
        resourceVersionBefore: null, resourceVersionAfter: 1, occurredAt: task.createdAt,
      }],
    });
    api.members.mockResolvedValue([
      { id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' },
      { id: 'user-reviewer', label: 'Dr. Caio', role: 'legal_manager' },
    ]);
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Tarefas' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Minhas tarefas/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Atribuídas por mim/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Revisões/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Minha equipe/i })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Toda a operação/i })).not.toBeInTheDocument();
    expect(await screen.findByText('Revisar documentação do contrato')).toBeInTheDocument();
    expect(screen.queryByText('task-opaque-id')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Revisar documentação do contrato/i }));
    expect((await screen.findAllByText('João Silva · Caso Silva · Contrato')).length).toBeGreaterThan(1);
    expect(screen.getByRole('tab', { name: 'Equipe' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Revisão' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Histórico' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Lembretes' })).toBeInTheDocument();
  });

  it('adds the advanced execution and checklist surfaces only to the legal experience', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([]);
    renderLegalPage();

    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    expect(await screen.findByRole('tab', { name: 'Execução' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Checklists' })).toBeInTheDocument();
    expect(document.querySelector('[data-task-experience="legal_360"]')).toBeInTheDocument();
  });

  it('filters the legal queue by a persistent label handle without exposing it to the standard view', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.labelCatalog.mockResolvedValue([
      { id: 'label-internal-id', handle: 'opaque-label-reference', name: 'Urgente', color: '#DC2626' },
    ]);

    renderLegalPage('/app/tarefas?label=opaque-label-reference');

    const labelFilterControl = await screen.findByRole('combobox', { name: 'Filtrar por etiqueta' });
    await waitFor(() => expect(labelFilterControl).toHaveTextContent('Urgente'));
    await waitFor(() => expect(api.list).toHaveBeenCalledWith(
      expect.objectContaining({ labelRef: 'opaque-label-reference' }),
    ));
    expect(screen.queryByText('label-internal-id')).not.toBeInTheDocument();

    cleanup();
    vi.clearAllMocks();
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    renderPage();
    expect(await screen.findByText(task.title)).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Filtrar por etiqueta' })).not.toBeInTheDocument();
    expect(api.labelCatalog).not.toHaveBeenCalled();
  });

  it('opens a legal subtask in the same operational drawer and returns to its parent', async () => {
    const child = {
      ...task,
      id: 'task-child-opaque-id',
      parentTaskId: task.id,
      title: 'Conferir assinatura digital',
      version: 1,
    };
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockImplementation(async (taskId: string) => taskId === child.id ? child : task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([]);
    api.legalResources.mockImplementation(async (taskId: string) => ({
      labels: [],
      subtasks: taskId === task.id ? [child] : [],
      dependencies: [],
      attachments: [],
      mentions: [],
    }));

    renderLegalPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const resourcesTab = await screen.findByRole('tab', { name: 'Recursos' });
    fireEvent.mouseDown(resourcesTab, { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole('button', { name: 'Abrir subtarefa Conferir assinatura digital' }));

    expect(await screen.findByRole('heading', { name: 'Conferir assinatura digital' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: `Voltar para ${task.title}` }));
    expect(await screen.findByRole('heading', { name: task.title })).toBeInTheDocument();
  });

  it('adds documents and human mentions to a legal comment without exposing UUID fields', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([
      { id: 'user-owner', handle: 'dra-marina', label: 'Dra. Marina', role: 'lawyer' },
      { id: 'user-reviewer', handle: 'dr-caio', label: 'Dr. Caio', role: 'legal_manager' },
    ]);
    api.matterDocuments.mockResolvedValue([
      { id: 'document-opaque-id', name: 'Contrato social.pdf', mimeType: 'application/pdf', status: 'available' },
    ]);
    api.comment.mockResolvedValue({ ...task, version: 4 });

    renderLegalPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const commentsTab = await screen.findByRole('tab', { name: 'Comentários e arquivos' });
    fireEvent.mouseDown(commentsTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(commentsTab).toHaveAttribute('data-state', 'active'));

    fireEvent.click(await screen.findByText('Dr. Caio'));
    fireEvent.click(await screen.findByText('Contrato social.pdf'));
    fireEvent.change(screen.getByLabelText('Novo comentário'), { target: { value: 'Favor revisar o documento.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar update' }));

    await waitFor(() => expect(api.comment).toHaveBeenCalledWith(
      task.id,
      task.version,
      'Favor revisar o documento.',
      { documentIds: ['document-opaque-id'], mentionedUserIds: ['user-reviewer'] },
    ));
    expect(screen.queryByText('document-opaque-id')).not.toBeInTheDocument();
    expect(screen.queryByText('user-reviewer')).not.toBeInTheDocument();
  });

  it('fails closed for legal attachments when a task has no Caso 360', async () => {
    const workspaceTask = { ...task, matterId: null, matterLabel: null, clientLabel: null, contextType: 'workspace' as const };
    api.list.mockResolvedValue({ items: [workspaceTask], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(workspaceTask);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([]);

    renderLegalPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const commentsTab = await screen.findByRole('tab', { name: 'Comentários e arquivos' });
    fireEvent.mouseDown(commentsTab, { button: 0, ctrlKey: false });

    expect(await screen.findByText('Anexos indisponíveis')).toBeInTheDocument();
    expect(screen.getByText(/Nenhum documento será inferido/i)).toBeInTheDocument();
    expect(api.matterDocuments).not.toHaveBeenCalled();
  });

  it('renders linked files and mentions from the event-backed legal history', async () => {
    const commentEvent = {
      id: 'event-comment', taskId: task.id, actorUserId: 'user-owner', eventType: 'comment_added', beforeData: null,
      afterData: { comment: 'Peça juntada.' }, changedPaths: ['comment'], comment: 'Peça juntada.', correlationId: 'correlation',
      sessionType: 'normal', administrativeSessionId: null, supportGrantId: null,
      resourceVersionBefore: 3, resourceVersionAfter: 4, occurredAt: task.createdAt,
    };
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [commentEvent], total: 1 });
    api.members.mockResolvedValue([{ id: 'user-owner', handle: 'dra-marina', label: 'Dra. Marina', role: 'lawyer' }]);
    api.legalResources.mockResolvedValue({
      labels: [], subtasks: [], dependencies: [],
      attachments: [{ id: 'attachment-1', taskEventId: 'event-comment', documentId: 'document-1', nameSnapshot: 'Petição inicial.pdf', mimeTypeSnapshot: 'application/pdf', createdAt: task.createdAt }],
      mentions: [{ id: 'mention-1', taskEventId: 'event-comment', mentionedUserId: 'user-owner', labelSnapshot: 'Dra. Marina', createdAt: task.createdAt }],
    });

    renderLegalPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const commentsTab = await screen.findByRole('tab', { name: 'Comentários e arquivos' });
    fireEvent.mouseDown(commentsTab, { button: 0, ctrlKey: false });

    expect(await screen.findByText('Petição inicial.pdf')).toBeInTheDocument();
    expect(screen.getAllByText('Dra. Marina').length).toBeGreaterThan(1);
    const historyTab = screen.getByRole('tab', { name: 'Histórico' });
    fireEvent.mouseDown(historyTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(historyTab).toHaveAttribute('data-state', 'active'));
    expect(screen.getByText('Petição inicial.pdf')).toBeInTheDocument();
  });

  it('operates reminder rules in the drawer and keeps external channels honestly unavailable', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([
      { id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' },
      { id: 'user-assignee', label: 'Dra. Lia', role: 'lawyer' },
      { id: 'user-reviewer', label: 'Dr. Caio', role: 'legal_manager' },
    ]);
    reminders.list.mockResolvedValue([
      reminderRule('rule-pending-due', 'pending_due'),
      reminderRule('rule-pending-recipient', 'pending_recipient', {
        relativeTo: 'personal_due',
        recipient: 'assignees',
      }),
      reminderRule('rule-unavailable', 'unavailable', {
        channel: 'email',
        deliveryAvailable: false,
        unavailabilityReason: 'not_configured',
      }),
    ]);
    reminders.create.mockResolvedValue(reminderRule('rule-created', 'scheduled'));

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const remindersTab = await screen.findByRole('tab', { name: 'Lembretes' });
    fireEvent.mouseDown(remindersTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(remindersTab).toHaveAttribute('data-state', 'active'));

    expect(await screen.findByText('Aguardando prazo')).toBeInTheDocument();
    expect(screen.getByText('Aguardando destinatário')).toBeInTheDocument();
    expect(screen.getByText('Canal indisponível')).toBeInTheDocument();
    expect(screen.getByText(/Nenhum envio externo é tratado como sucesso/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Novo lembrete' }));
    expect(await screen.findByRole('heading', { name: 'Novo lembrete' })).toBeInTheDocument();
    expect(screen.getByLabelText('Prazo de referência')).toBeInTheDocument();
    expect(screen.getByLabelText('Antecedência')).toBeInTheDocument();
    expect(screen.getByLabelText('Destinatário')).toBeInTheDocument();
    const channel = screen.getByLabelText('Canal');
    fireEvent.click(channel);
    const inboxOption = await screen.findByRole('option', { name: 'Inbox do Meu Executivo' });
    expect(inboxOption).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /E-mail/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /WhatsApp/ })).not.toBeInTheDocument();
    fireEvent.click(inboxOption);
    fireEvent.click(screen.getByRole('button', { name: 'Salvar lembrete' }));

    await waitFor(() => expect(reminders.create).toHaveBeenCalledWith(
      task.id,
      expect.objectContaining({
        expectedVersion: task.version,
        relativeTo: 'general_due',
        offsetMinutesBefore: 1_440,
        channel: 'in_app',
        recipient: 'owner',
        recipientUserId: null,
      }),
      expect.stringMatching(/^task-reminder:create:/),
    ));
  });

  it('fails closed when the reminder capability manifest is absent', async () => {
    access.reminderCapability = null;
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([{ id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' }]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const remindersTab = await screen.findByRole('tab', { name: 'Lembretes' });
    fireEvent.mouseDown(remindersTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(remindersTab).toHaveAttribute('data-state', 'active'));

    expect(await screen.findByText(/capacidade não foi contratada ou o manifesto está indisponível/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Novo lembrete' })).toBeDisabled();
    expect(reminders.create).not.toHaveBeenCalled();
  });

  it('surfaces reminder 409 conflicts and reloads rules before another intent', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([{ id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' }]);
    reminders.list.mockResolvedValue([]);
    reminders.create.mockRejectedValue(new ApiError(
      409,
      'A regra foi alterada por outra pessoa.',
      { code: 'TASK_REMINDER_VERSION_CONFLICT' },
    ));

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const remindersTab = await screen.findByRole('tab', { name: 'Lembretes' });
    fireEvent.mouseDown(remindersTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(remindersTab).toHaveAttribute('data-state', 'active'));
    fireEvent.click(await screen.findByRole('button', { name: 'Novo lembrete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Salvar lembrete' }));

    expect(await screen.findByText('Conflito de versão')).toBeInTheDocument();
    expect(screen.getByText(/lembretes foram recarregados/i)).toBeInTheDocument();
    await waitFor(() => expect(reminders.list.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('edits and disables an existing reminder with rule CAS and stable intent keys', async () => {
    const existingRule = reminderRule('rule-existing', 'scheduled', {
      offsetMinutesBefore: 60,
      version: 7,
      nextScheduledAt: '2026-08-01T14:00:00.000Z',
    });
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([{ id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' }]);
    reminders.list.mockResolvedValue([existingRule]);
    reminders.update.mockResolvedValue({ ...existingRule, version: 8 });
    reminders.disable.mockResolvedValue({
      id: existingRule.id,
      taskId: task.id,
      deleted: true,
      version: 8,
    });

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const remindersTab = await screen.findByRole('tab', { name: 'Lembretes' });
    fireEvent.mouseDown(remindersTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(remindersTab).toHaveAttribute('data-state', 'active'));

    fireEvent.click(await screen.findByRole('button', { name: 'Editar lembrete 1 hora antes' }));
    fireEvent.click(screen.getByRole('button', { name: 'Salvar lembrete' }));
    await waitFor(() => expect(reminders.update).toHaveBeenCalledWith(
      task.id,
      existingRule.id,
      expect.objectContaining({ expectedVersion: 7, offsetMinutesBefore: 60 }),
      expect.stringMatching(/^task-reminder:update:/),
    ));

    fireEvent.click(await screen.findByRole('button', { name: 'Desativar lembrete 1 hora antes' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Desativar' }));
    await waitFor(() => expect(reminders.disable).toHaveBeenCalledWith(
      task.id,
      existingRule.id,
      7,
      expect.stringMatching(/^task-reminder:disable:/),
    ));
  });

  it('surfaces a stale-version 409 and refreshes instead of hiding the conflict', async () => {
    api.list.mockResolvedValue({ items: [task], total: null, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([{ id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' }]);
    api.command.mockRejectedValue(new ApiError(409, 'A tarefa foi alterada por outra pessoa.', { code: 'TASK_VERSION_CONFLICT' }));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Iniciar' }));

    expect(await screen.findByText('Conflito de versão')).toBeInTheDocument();
    expect(screen.getByText(/atualizada para a versão mais recente/i)).toBeInTheDocument();
    await waitFor(() => expect(api.command).toHaveBeenCalledWith(task.id, 'start', 3, {}));
  });

  it('renders the permission state without substituting demonstration tasks', async () => {
    api.list.mockRejectedValue(new ApiError(403, 'Acesso negado.', { code: 'AUTH-013' }));
    renderPage();
    expect(await screen.findByText('Sem permissão para tarefas')).toBeInTheDocument();
    expect(screen.queryByText(task.title)).not.toBeInTheDocument();
  });

  it('fails closed when the commercial capability manifest is unavailable', async () => {
    access.allowed = new Set(['tasks:view', 'tasks:create', 'tasks:assign']);
    access.commercialCapabilitiesAvailable = false;
    api.list.mockResolvedValue({ items: [], total: 0, nextCursor: null, hasMore: false });

    renderPage();

    const createButton = await screen.findByRole('button', { name: 'Nova tarefa' });
    expect(createButton).toBeDisabled();
    expect(api.matters).not.toHaveBeenCalled();
    expect(api.members).not.toHaveBeenCalled();
  });

  it('hides every mutation whose granular tasks permission is absent', async () => {
    access.allowed = new Set(['tasks:view']);
    api.list.mockResolvedValue({ items: [task], total: null, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([
      { id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' },
      { id: 'user-reviewer', label: 'Dr. Caio', role: 'legal_manager' },
    ]);

    renderPage();

    expect(await screen.findByText(task.title)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nova tarefa' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Revisar documentação do contrato/i }));
    expect(await screen.findByRole('tab', { name: 'Histórico' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Editar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Equipe e prazos' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Iniciar' })).not.toBeInTheDocument();
    const commentsTab = screen.getByRole('tab', { name: 'Comentários' });
    fireEvent.mouseDown(commentsTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(commentsTab).toHaveAttribute('data-state', 'active'));
    expect(screen.queryByLabelText('Novo comentário')).not.toBeInTheDocument();
  });

  it('does not offer owner, executors, or reviewer when tasks:assign is absent', async () => {
    access.allowed = new Set(['tasks:view', 'tasks:create']);
    api.list.mockResolvedValue({ items: [], total: 0, nextCursor: null, hasMore: false });
    api.matters.mockResolvedValue([{ id: 'matter-1', label: 'Caso Contrato', clientLabel: 'João Silva' }]);

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Nova tarefa' }));
    expect(await screen.findByRole('heading', { name: 'Nova tarefa colaborativa' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Buscar na equipe')).not.toBeInTheDocument();
    expect(screen.queryByText('Responsável')).not.toBeInTheDocument();
    expect(screen.queryByText('Revisor opcional')).not.toBeInTheDocument();
    expect(screen.queryByText('Executores')).not.toBeInTheDocument();
    expect(api.members).not.toHaveBeenCalled();
  });

  it('allows a root-direct session to decide a pending review and requires a reason', async () => {
    access.allowed = new Set(['tasks:view', 'tasks:review']);
    access.supportMode = { sessionType: 'root_direct' };
    const pendingReview = { ...task, reviewState: 'pending' as const };
    api.list.mockResolvedValue({ items: [pendingReview], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(pendingReview);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([
      { id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' },
      { id: 'user-reviewer', label: 'Dr. Caio', role: 'legal_manager' },
    ]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Aprovar' }));

    expect(await screen.findByLabelText('Motivo')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirmar' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'Revisão excepcional pela administração raiz.' } });
    expect(screen.getByRole('button', { name: 'Confirmar' })).toBeEnabled();
  });

  it('presents a cancelled task as cancelled and never as 100 percent complete', async () => {
    const cancelled = {
      ...task,
      status: 'done' as const,
      completedAt: null,
      cancelledAt: '2026-07-30T12:00:00.000Z',
      cancellationReason: 'Escopo substituído por outra tarefa.',
    };
    api.list.mockResolvedValue({ items: [cancelled], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(cancelled);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([{ id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' }]);

    renderPage();
    expect(await screen.findByText('Cancelada')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Revisar documentação do contrato/i }));
    expect((await screen.findAllByText('Encerrada por cancelamento')).length).toBeGreaterThan(0);
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
  });

  it('updates real executor progress with version and reason instead of estimating a percentage', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(task);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([
      { id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' },
      { id: 'user-assignee', label: 'Dra. Lia', role: 'lawyer' },
      { id: 'user-reviewer', label: 'Dr. Caio', role: 'legal_manager' },
    ]);
    api.participantState.mockResolvedValue({
      ...task,
      version: 4,
      participants: task.participants.map((participant) => participant.userId === 'user-assignee'
        ? { ...participant, state: 'in_progress' as const }
        : participant),
    });

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    const teamTab = await screen.findByRole('tab', { name: 'Equipe' });
    fireEvent.mouseDown(teamTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(teamTab).toHaveAttribute('data-state', 'active'));
    expect(await screen.findByText('Dra. Lia')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Iniciar parte' }));
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'Executor iniciou a conferência dos documentos.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar progresso' }));

    await waitFor(() => expect(api.participantState).toHaveBeenCalledWith(
      task.id,
      'user-assignee',
      3,
      'in_progress',
      'Executor iniciou a conferência dos documentos.',
    ));
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it('treats an explicitly required owner as an executor without making ownership implicitly blocking', async () => {
    const requiredOwnerTask: CollaborativeTask = {
      ...task,
      status: 'in_progress',
      reviewRequired: false,
      reviewState: 'not_required',
      reviewDueAt: null,
      participants: task.participants
        .filter((participant) => participant.role === 'owner')
        .map((participant) => ({
          ...participant,
          state: 'assigned' as const,
          requiredForCompletion: true,
        })),
    };
    api.list.mockResolvedValue({ items: [requiredOwnerTask], total: 1, nextCursor: null, hasMore: false });
    api.get.mockResolvedValue(requiredOwnerTask);
    api.events.mockResolvedValue({ items: [], total: 0 });
    api.members.mockResolvedValue([
      { id: 'user-owner', label: 'Dra. Marina', role: 'lawyer' },
    ]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Revisar documentação do contrato/i }));
    expect(screen.queryByRole('button', { name: 'Concluir' })).not.toBeInTheDocument();
    expect(screen.getByText(/0 de 1 owners\/executores obrigatórios/)).toBeInTheDocument();

    const teamTab = screen.getByRole('tab', { name: 'Equipe' });
    fireEvent.mouseDown(teamTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(teamTab).toHaveAttribute('data-state', 'active'));
    expect(screen.getByText('Executor obrigatório')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Iniciar parte' })).toBeInTheDocument();
  });

  it('uses the legal organization board only in the legal experience', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });

    renderLegalPage('/app/tarefas?mode=board');

    expect(await screen.findByTestId('legal-task-organization')).toBeInTheDocument();
    expect(screen.getByText('Listas e colunas organizam o trabalho. O estado jurídico da tarefa permanece independente.')).toBeInTheDocument();
    expect(api.organization).toHaveBeenCalledTimes(1);
  });

  it('moves a legal task between organizational columns without presenting a status change', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });
    api.organization.mockResolvedValue({
      lists: [],
      boards: [{
        id: 'board-1',
        name: 'Contencioso',
        description: null,
        position: 0,
        version: 2,
        archivedAt: null,
        columns: [
          { id: 'column-doing', boardId: 'board-1', name: 'Em elaboração', position: 0, version: 1, archivedAt: null },
          { id: 'column-review', boardId: 'board-1', name: 'Em revisão', position: 1, version: 1, archivedAt: null },
        ],
      }],
      listMemberships: [],
      placements: [{
        id: 'placement-1', boardId: 'board-1', columnId: 'column-doing', taskId: task.id, position: 0, version: 4,
      }],
    });
    api.placeOnBoard.mockResolvedValue({
      id: 'placement-1', boardId: 'board-1', columnId: 'column-review', taskId: task.id, position: 0, version: 5,
    });

    renderLegalPage('/app/tarefas?mode=board');

    expect(await screen.findByText('Estado: Pendente')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: `Mover ${task.title} para coluna` }));
    fireEvent.click(await screen.findByRole('option', { name: 'Em revisão' }));

    await waitFor(() => expect(api.placeOnBoard).toHaveBeenCalledWith({
      taskId: task.id,
      boardId: 'board-1',
      columnId: 'column-review',
      expectedTaskVersion: task.version,
      expectedPlacementVersion: 4,
    }));
    expect(screen.getByText('Estado: Pendente')).toBeInTheDocument();
  });

  it('preserves the standard board outside the legal experience', async () => {
    api.list.mockResolvedValue({ items: [task], total: 1, nextCursor: null, hasMore: false });

    renderPage('/app/tarefas?mode=board');

    expect(await screen.findByText(task.title)).toBeInTheDocument();
    expect(screen.queryByTestId('legal-task-organization')).not.toBeInTheDocument();
    expect(api.organization).not.toHaveBeenCalled();
  });
});
