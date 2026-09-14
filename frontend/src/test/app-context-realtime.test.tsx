/** @vitest-environment jsdom */
import { act, cleanup, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const testState = vi.hoisted(() => {
  const workspace = {
    id: 'workspace-1',
    name: 'KLM',
    slug: 'klm',
    status: 'active',
    isActive: true,
  };
  const session = {
    activeWorkspace: workspace,
    availableWorkspaces: [],
    permissions: [{ module: 'dashboard', action: 'view', scope: 'workspace' }],
    isInternalStaff: true,
    activeRole: 'admin',
  };
  const user = {
    id: 'user-1',
    name: 'Hermes',
    email: 'hermes@klm.test',
    role: 'admin',
    initials: 'HM',
    isInternalStaff: true,
  };

  return {
    session,
    user,
    passwordChangeRequired: false,
    fetchCurrentSession: vi.fn().mockResolvedValue(session),
    apiFetch: vi.fn().mockResolvedValue({
      workspaceId: workspace.id,
      contractId: 'contract-1',
      contractVersion: 1,
      contractStatus: 'active',
      activeProducts: [],
      capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    }),
    fetchWebSocketTicket: vi.fn().mockResolvedValue({ ticket: 'ticket-1', expiresIn: 60 }),
    getSavedSession: vi.fn(() => session),
    getSavedUser: vi.fn(() => user),
    getToken: vi.fn(() => 'access-token'),
    removeToken: vi.fn(),
    saveSession: vi.fn(),
    fetchMatters: vi.fn().mockResolvedValue([]),
    fetchMatterDetail: vi.fn(),
    addMatterNote: vi.fn(),
    updateMatterDetail: vi.fn(),
    toggleMatterTask: vi.fn(),
    overrideAgentRun: vi.fn(),
    retryAgentRun: vi.fn(),
    fetchReportsSummary: vi.fn().mockResolvedValue({ kpis: [] }),
    fetchApprovalItems: vi.fn().mockResolvedValue([]),
    resolveApprovalItem: vi.fn(),
    fetchWhatsAppConnections: vi.fn().mockResolvedValue([]),
    fetchWhatsAppContacts: vi.fn().mockResolvedValue([]),
    fetchWhatsAppConversation: vi.fn(),
    handoffConversation: vi.fn(),
    markWhatsAppConversationRead: vi.fn(),
    sendWhatsAppMessage: vi.fn(),
  };
});

vi.mock('@/services/auth', () => ({
  CLIENT_SESSION_EXPIRED_EVENT: 'klm:session-expired',
  expireClientSession: vi.fn(),
  fetchCurrentSession: testState.fetchCurrentSession,
  getAccessTokenExpiresAt: vi.fn(() => null),
  getSavedSession: testState.getSavedSession,
  getSavedUser: testState.getSavedUser,
  getToken: testState.getToken,
  removeToken: testState.removeToken,
  refreshAccessToken: vi.fn().mockResolvedValue('access-token'),
  requiresPasswordChange: (user?: { requiresPasswordChange?: boolean } | null, session?: { requiresPasswordChange?: boolean } | null) => (
    Boolean(testState.passwordChangeRequired || user?.requiresPasswordChange || session?.requiresPasswordChange)
  ),
  saveSession: testState.saveSession,
  SessionAccessError: class SessionAccessError extends Error {
    constructor(public readonly status: number, message: string) {
      super(message);
      this.name = 'SessionAccessError';
    }
  },
}));

vi.mock('@/services/matters', () => ({
  addMatterNote: testState.addMatterNote,
  updateMatterDetail: testState.updateMatterDetail,
  toggleMatterTask: testState.toggleMatterTask,
  fetchMatterDetail: testState.fetchMatterDetail,
  fetchMatters: testState.fetchMatters,
  overrideAgentRun: testState.overrideAgentRun,
  retryAgentRun: testState.retryAgentRun,
}));

vi.mock('@/services/reports', () => ({
  fetchReportsSummary: testState.fetchReportsSummary,
}));

vi.mock('@/services/api', () => ({
  fetchApprovalItems: testState.fetchApprovalItems,
  resolveApprovalItem: testState.resolveApprovalItem,
}));

vi.mock('@/services/whatsapp', () => ({
  fetchWhatsAppConnections: testState.fetchWhatsAppConnections,
  fetchWhatsAppContacts: testState.fetchWhatsAppContacts,
  fetchWhatsAppConversation: testState.fetchWhatsAppConversation,
  handoffConversation: testState.handoffConversation,
  markWhatsAppConversationRead: testState.markWhatsAppConversationRead,
  sendWhatsAppMessage: testState.sendWhatsAppMessage,
}));

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>();
  return {
    ...actual,
    apiFetch: testState.apiFetch,
    fetchWebSocketTicket: testState.fetchWebSocketTicket,
  };
});

import { AppProvider, useAppStore } from '@/store/app-context';
import type { SupportAccessActivation } from '@/services/support-access';
import { supportPermissions } from '@/support-mode/scopes';
import type { MatterCard, SessionContext } from '@/types';
import {
  clearSupportModeCredential,
  completeSupportModeEnding,
  getSupportModePhase,
  initializeSupportModeFromStorage,
  stageSupportModeCredential,
  SUPPORT_MODE_TOMBSTONE_KEY,
} from '@/support-mode/session';

const REALTIME_RETRY_MAX_MS = 30_000;
const REALTIME_STABLE_CONNECTION_MS = 30_000;
const REALTIME_CONNECT_TIMEOUT_MS = 10_000;

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readonly protocols: string | string[] | undefined;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new Event('close'));
  });

  constructor(url: string | URL, protocols?: string | string[]) {
    this.url = String(url);
    this.protocols = protocols;
    MockWebSocket.instances.push(this);
  }

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }

  serverClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new Event('close'));
  }

  fail() {
    this.onerror?.(new Event('error'));
  }

  receive(payload: Record<string, unknown>) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(payload) }));
  }
}

let currentAppStore: ReturnType<typeof useAppStore> | null = null;

function ContextProbe() {
  currentAppStore = useAppStore();
  return <div>conteúdo</div>;
}

function renderProvider(
  children = <div>conteúdo</div>,
  providedQueryClient?: QueryClient,
) {
  const queryClient = providedQueryClient ?? new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>{children}</AppProvider>
    </QueryClientProvider>,
  );
  return { ...rendered, queryClient };
}

function queryKeys(queryClient: QueryClient): readonly (readonly unknown[])[] {
  return queryClient.getQueryCache().getAll().map((query) => query.queryKey);
}

function expectOnlyCapabilityManifestQuery(
  queryClient: QueryClient,
  workspaceId: string,
): void {
  expect(queryKeys(queryClient)).toEqual([
    ['workspace-capability-manifest', workspaceId],
  ]);
}

async function flushEffects() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function advanceTime(milliseconds: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

async function closeFromServer(socket: MockWebSocket) {
  await act(async () => {
    socket.serverClose();
    await Promise.resolve();
  });
}

describe('AppProvider realtime reconnect hardening', () => {
  it('does not synthesize Lista or Kanban permissions from meeting scopes', () => {
    expect(supportPermissions(['meetings:read', 'meetings:write'])).toEqual([]);
  });

  it('maps the approved finance write scope to workspace finance management', () => {
    expect(supportPermissions(['finance:read', 'finance:write'])).toEqual([
      { module: 'finance', action: 'view', scope: 'workspace' },
      { module: 'finance', action: 'manage', scope: 'workspace' },
    ]);
  });

  it('does not refresh and expire a provisional first-access session', async () => {
    testState.passwordChangeRequired = true;
    try {
      renderProvider(<ContextProbe />);
      await act(async () => { await Promise.resolve(); });

      expect(testState.fetchCurrentSession).not.toHaveBeenCalled();
      expect(currentAppStore?.state.isAuthenticated).toBe(true);
    } finally {
      testState.passwordChangeRequired = false;
    }
  });

  beforeEach(() => {
    clearSupportModeCredential();
    completeSupportModeEnding();
    sessionStorage.clear();
    vi.useFakeTimers();
    vi.clearAllMocks();
    testState.passwordChangeRequired = false;
    testState.fetchCurrentSession.mockResolvedValue(testState.session);
    testState.apiFetch.mockResolvedValue({
      workspaceId: testState.session.activeWorkspace.id,
      contractId: 'contract-1',
      contractVersion: 1,
      contractStatus: 'active',
      activeProducts: [],
      capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    testState.fetchWebSocketTicket.mockResolvedValue({ ticket: 'ticket-1', expiresIn: 60 });
    testState.fetchMatters.mockResolvedValue([]);
    testState.fetchReportsSummary.mockResolvedValue({ kpis: [] });
    testState.fetchApprovalItems.mockResolvedValue([]);
    testState.fetchWhatsAppConnections.mockResolvedValue([]);
    testState.fetchWhatsAppContacts.mockResolvedValue([]);
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    delete document.documentElement.dataset.klmRealtimeStatus;
    currentAppStore = null;
  });

  afterEach(() => {
    cleanup();
    clearSupportModeCredential();
    completeSupportModeEnding();
    sessionStorage.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('applies jitter to exponential retries and caps repeated reconnects', async () => {
    renderProvider();
    await flushEffects();

    const expectedDelays = [1_200, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000];
    for (const [index, delay] of expectedDelays.entries()) {
      const socketCount = MockWebSocket.instances.length;
      vi.mocked(Math.random).mockReturnValue(index === 0 ? 1 : 0.5);
      await closeFromServer(MockWebSocket.instances[socketCount - 1]);
      await advanceTime(delay - 1);
      expect(MockWebSocket.instances).toHaveLength(socketCount);
      await advanceTime(1);
      expect(MockWebSocket.instances).toHaveLength(socketCount + 1);
    }

    expect(testState.fetchWebSocketTicket).toHaveBeenCalledTimes(expectedDelays.length + 1);
  });

  it('keeps backoff after a short-lived connection and resets it after a stable connection', async () => {
    renderProvider();
    await flushEffects();
    expect(MockWebSocket.instances).toHaveLength(1);

    await closeFromServer(MockWebSocket.instances[0]);
    await advanceTime(999);
    expect(MockWebSocket.instances).toHaveLength(1);
    await advanceTime(1);
    expect(MockWebSocket.instances).toHaveLength(2);

    act(() => MockWebSocket.instances[1].open());
    await advanceTime(REALTIME_STABLE_CONNECTION_MS - 1);
    await closeFromServer(MockWebSocket.instances[1]);
    await advanceTime(1_999);
    expect(MockWebSocket.instances).toHaveLength(2);
    await advanceTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);

    act(() => MockWebSocket.instances[2].open());
    await advanceTime(REALTIME_STABLE_CONNECTION_MS);
    await closeFromServer(MockWebSocket.instances[2]);
    await advanceTime(999);
    expect(MockWebSocket.instances).toHaveLength(3);
    await advanceTime(1);
    expect(MockWebSocket.instances).toHaveLength(4);
  });

  it('does not duplicate sockets or timers and cancels all reconnect work on cleanup', async () => {
    const view = renderProvider();
    await flushEffects();
    const firstSocket = MockWebSocket.instances[0];

    act(() => {
      firstSocket.open();
      firstSocket.open();
    });
    await advanceTime(25_000);
    expect(firstSocket.send).toHaveBeenCalledTimes(1);

    await closeFromServer(firstSocket);
    await closeFromServer(firstSocket);
    await advanceTime(1_000);
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(testState.fetchWebSocketTicket).toHaveBeenCalledTimes(2);

    const secondSocket = MockWebSocket.instances[1];
    act(() => secondSocket.open());
    view.unmount();
    expect(secondSocket.close).toHaveBeenCalledTimes(1);

    await advanceTime(REALTIME_RETRY_MAX_MS * 2);
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(secondSocket.send).not.toHaveBeenCalled();
  });

  it('abandons a socket stuck while connecting and retries with a fresh ticket', async () => {
    renderProvider();
    await flushEffects();
    expect(MockWebSocket.instances).toHaveLength(1);

    const stuckSocket = MockWebSocket.instances[0];
    await advanceTime(REALTIME_CONNECT_TIMEOUT_MS - 1);
    expect(stuckSocket.close).not.toHaveBeenCalled();

    await advanceTime(1);
    expect(stuckSocket.close).toHaveBeenCalledTimes(1);
    expect(document.documentElement.dataset.klmRealtimeStatus).toBe('reconnecting');
    await advanceTime(1_000);

    expect(MockWebSocket.instances).toHaveLength(2);
    expect(testState.fetchWebSocketTicket).toHaveBeenCalledTimes(2);
  });

  it('retries once after a websocket transport error', async () => {
    renderProvider();
    await flushEffects();
    const failedSocket = MockWebSocket.instances[0];

    act(() => failedSocket.fail());
    expect(failedSocket.close).toHaveBeenCalledTimes(1);
    await advanceTime(1_000);

    expect(MockWebSocket.instances).toHaveLength(2);
    expect(testState.fetchWebSocketTicket).toHaveBeenCalledTimes(2);
  });

  it('refreshes conversation projections for omnichannel realtime events', async () => {
    renderProvider();
    await flushEffects();
    const socket = MockWebSocket.instances[0];
    act(() => socket.open());
    testState.fetchWhatsAppContacts.mockClear();

    act(() => socket.receive({
      event: 'OMNICHANNEL_EVENT_INGESTED',
      data: { thread_id: 'thread-1', message_id: 'message-1' },
    }));
    await advanceTime(499);
    expect(testState.fetchWhatsAppContacts).not.toHaveBeenCalled();
    await advanceTime(1);

    expect(testState.fetchWhatsAppContacts).toHaveBeenCalledTimes(1);
  });

  it('persists a quick note through the API before touching the card timeline (V9)', async () => {
    const card = {
      id: 'card-1',
      columnId: 'triagem_sdr',
      timeline: [],
      tasks: [],
      documents: [],
      conversations: [],
      agentRuns: [],
      updatedAt: '2026-09-04T12:00:00Z',
    } as unknown as MatterCard;
    testState.fetchMatters.mockResolvedValue([card]);
    renderProvider(<ContextProbe />);
    await flushEffects();
    await flushEffects();
    expect(currentAppStore?.state.cards.map((item) => item.id)).toEqual(['card-1']);

    testState.addMatterNote.mockRejectedValueOnce(new Error('backend indisponível'));
    let failure: unknown = null;
    await act(async () => {
      try {
        await currentAppStore!.addNote('card-1', 'nota que falha');
      } catch (error) {
        failure = error;
      }
    });
    expect(failure).toBeInstanceOf(Error);
    expect(currentAppStore?.state.cards[0].timeline).toHaveLength(0);
    expect(testState.fetchMatterDetail).not.toHaveBeenCalled();

    const persisted = {
      ...card,
      timeline: [{
        id: 'evt-1',
        type: 'comment',
        title: 'Observação adicionada',
        description: 'nota persistida',
        timestamp: '2026-09-04T12:01:00Z',
        agentId: 'user-1',
      }],
    } as unknown as MatterCard;
    testState.addMatterNote.mockResolvedValueOnce({ message: 'ok', event_id: 'evt-1' });
    testState.fetchMatterDetail.mockResolvedValueOnce(persisted);
    await act(async () => {
      await currentAppStore!.addNote('card-1', 'nota persistida');
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(testState.addMatterNote).toHaveBeenCalledWith('card-1', 'nota persistida');
    expect(testState.fetchMatterDetail).toHaveBeenCalledWith('card-1');
    const timeline = currentAppStore?.state.cards[0].timeline ?? [];
    expect(timeline.length).toBeGreaterThanOrEqual(1);
    expect(timeline[0]).toMatchObject({ type: 'comment', description: 'nota persistida' });
  });

  it('persists the star rating through the API and restores the server value on failure (V9-b)', async () => {
    const card = {
      id: 'card-1',
      columnId: 'triagem_sdr',
      starsRating: 2,
      timeline: [],
      tasks: [],
      documents: [],
      conversations: [],
      agentRuns: [],
      updatedAt: '2026-09-04T12:00:00Z',
    } as unknown as MatterCard;
    testState.fetchMatters.mockResolvedValue([card]);
    renderProvider(<ContextProbe />);
    await flushEffects();
    await flushEffects();
    expect(currentAppStore?.state.cards[0]?.starsRating).toBe(2);

    testState.updateMatterDetail.mockRejectedValueOnce(new Error('sem rede'));
    testState.fetchMatterDetail.mockResolvedValueOnce({ ...card, starsRating: 2 });
    let failure: unknown = null;
    await act(async () => {
      try {
        await currentAppStore!.updateCardField('card-1', 'starsRating', 5);
      } catch (error) {
        failure = error;
      }
    });
    expect(failure).toBeInstanceOf(Error);
    expect(testState.updateMatterDetail).toHaveBeenCalledWith('card-1', { starsRating: 5 });
    expect(testState.fetchMatterDetail).toHaveBeenCalledWith('card-1');
    expect(currentAppStore?.state.cards[0]?.starsRating).toBe(2);

    testState.updateMatterDetail.mockResolvedValueOnce({ ...card, starsRating: 4 });
    await act(async () => {
      await currentAppStore!.updateCardField('card-1', 'starsRating', 4);
    });
    expect(currentAppStore?.state.cards[0]?.starsRating).toBe(4);
  });

  it('completes a task with its expected version and never reopens a done task (V9-b)', async () => {
    const task = {
      id: 'task-1',
      title: 'Ligar para a cliente',
      status: 'pending',
      assignedTo: 'user-1',
      createdAt: '2026-09-04T12:00:00Z',
      isAiGenerated: false,
      version: 3,
    };
    const card = {
      id: 'card-1',
      columnId: 'triagem_sdr',
      timeline: [],
      tasks: [task],
      openTasks: 1,
      documents: [],
      conversations: [],
      agentRuns: [],
      updatedAt: '2026-09-04T12:00:00Z',
    } as unknown as MatterCard;
    testState.fetchMatters.mockResolvedValue([card]);
    renderProvider(<ContextProbe />);
    await flushEffects();
    await flushEffects();

    testState.toggleMatterTask.mockResolvedValueOnce({ id: 'task-1', status: 'done', version: 4 });
    await act(async () => {
      await currentAppStore!.toggleTask('card-1', 'task-1');
    });
    expect(testState.toggleMatterTask).toHaveBeenCalledWith('task-1', 3);
    expect(currentAppStore?.state.cards[0]?.tasks[0]).toMatchObject({ status: 'done', version: 4 });
    expect(currentAppStore?.state.cards[0]?.openTasks).toBe(0);
    expect(testState.fetchMatterDetail).not.toHaveBeenCalled();

    let failure: unknown = null;
    await act(async () => {
      try {
        await currentAppStore!.toggleTask('card-1', 'task-1');
      } catch (error) {
        failure = error;
      }
    });
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toMatch(/não reabre/);
    expect(testState.toggleMatterTask).toHaveBeenCalledTimes(1);
  });

  it('reloads the matter detail before toggling when the task has no version (V9-b)', async () => {
    const listedTask = {
      id: 'task-1',
      title: 'Ligar para a cliente',
      status: 'pending',
      assignedTo: 'user-1',
      createdAt: '2026-09-04T12:00:00Z',
      isAiGenerated: false,
    };
    const card = {
      id: 'card-1',
      columnId: 'triagem_sdr',
      timeline: [],
      tasks: [listedTask],
      openTasks: 1,
      documents: [],
      conversations: [],
      agentRuns: [],
      updatedAt: '2026-09-04T12:00:00Z',
    } as unknown as MatterCard;
    testState.fetchMatters.mockResolvedValue([card]);
    renderProvider(<ContextProbe />);
    await flushEffects();
    await flushEffects();

    testState.fetchMatterDetail.mockResolvedValueOnce({ ...card, tasks: [{ ...listedTask, version: 7 }] });
    testState.toggleMatterTask.mockResolvedValueOnce({ id: 'task-1', status: 'done', version: 8 });
    await act(async () => {
      await currentAppStore!.toggleTask('card-1', 'task-1');
    });
    expect(testState.fetchMatterDetail).toHaveBeenCalledWith('card-1');
    expect(testState.toggleMatterTask).toHaveBeenCalledWith('task-1', 7);
    expect(currentAppStore?.state.cards[0]?.tasks[0]).toMatchObject({ status: 'done', version: 8 });
  });

  it('restores the card from the server when the task toggle fails (V9-b)', async () => {
    const task = {
      id: 'task-1',
      title: 'Ligar para a cliente',
      status: 'pending',
      assignedTo: 'user-1',
      createdAt: '2026-09-04T12:00:00Z',
      isAiGenerated: false,
      version: 3,
    };
    const card = {
      id: 'card-1',
      columnId: 'triagem_sdr',
      timeline: [],
      tasks: [task],
      openTasks: 1,
      documents: [],
      conversations: [],
      agentRuns: [],
      updatedAt: '2026-09-04T12:00:00Z',
    } as unknown as MatterCard;
    testState.fetchMatters.mockResolvedValue([card]);
    renderProvider(<ContextProbe />);
    await flushEffects();
    await flushEffects();

    testState.toggleMatterTask.mockRejectedValueOnce(new Error('TASK_VERSION_CONFLICT'));
    testState.fetchMatterDetail.mockResolvedValueOnce(card);
    let failure: unknown = null;
    await act(async () => {
      try {
        await currentAppStore!.toggleTask('card-1', 'task-1');
      } catch (error) {
        failure = error;
      }
    });
    expect(failure).toBeInstanceOf(Error);
    expect(testState.fetchMatterDetail).toHaveBeenCalledWith('card-1');
    expect(currentAppStore?.state.cards[0]?.tasks[0]).toMatchObject({ status: 'pending', version: 3 });
    expect(currentAppStore?.state.cards[0]?.openTasks).toBe(1);
  });

  it('clears tenant caches when switching the active workspace', async () => {
    const { queryClient } = renderProvider(<ContextProbe />);
    await flushEffects();
    const cancelQueries = vi.spyOn(queryClient, 'cancelQueries');
    queryClient.setQueryData(['support-access', 'workspace', 'workspace-1', 'requests'], {
      sensitive: true,
    });
    const workspaceTwo: SessionContext = {
      ...(testState.session as SessionContext),
      activeWorkspace: {
        ...testState.session.activeWorkspace,
        id: 'workspace-2',
        name: 'Outro Escritório',
        slug: 'outro',
      },
    };

    await act(async () => {
      currentAppStore?.selectWorkspace(workspaceTwo);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(cancelQueries).toHaveBeenCalled();
    expectOnlyCapabilityManifestQuery(queryClient, 'workspace-2');
    expect(currentAppStore?.state.activeWorkspace?.id).toBe('workspace-2');
  });

  it('enters and exits support in memory while clearing caches and restoring the main session', async () => {
    const { queryClient } = renderProvider(<ContextProbe />);
    await flushEffects();
    const mainSession = currentAppStore?.state.session;
    const expiresAt = new Date(Date.now() + 10 * 60_000).toISOString();
    const activation: SupportAccessActivation = {
      expiresIn: 600,
      grant: {
        id: 'grant-1',
        workspaceId: 'workspace-support',
        requesterUserId: 'user-1',
        reason: 'Diagnóstico autorizado para o workspace atendido.',
        ticketReference: 'SUP-1234',
        requestedScopes: ['clients:read'],
        requestedDurationMinutes: 10,
        state: 'active',
        requestedAt: new Date().toISOString(),
        approverUserId: 'owner-2',
        approvedScopes: ['clients:read'],
        approvedDurationMinutes: 10,
        approvedAt: new Date().toISOString(),
        activatedAt: new Date().toISOString(),
        expiresAt,
        expiredAt: null,
        rejectedAt: null,
        rejectionReason: null,
        revokedAt: null,
        revocationReason: null,
        version: 3,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      },
    };
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: activation.grant.id,
      workspaceId: activation.grant.workspaceId,
      scopes: ['clients:read'],
      expiresAt,
    });
    queryClient.setQueryData(['tenant-sensitive', 'workspace-1'], { clientName: 'Sigiloso' });
    const normalSocket = MockWebSocket.instances.at(-1);

    await act(async () => {
      await currentAppStore?.enterSupportMode(activation, {
        id: 'workspace-support',
        name: 'Workspace em suporte',
        slug: 'suporte',
      });
    });

    expectOnlyCapabilityManifestQuery(queryClient, 'workspace-support');
    expect(currentAppStore?.supportMode).toMatchObject({
      grantId: 'grant-1',
      workspaceId: 'workspace-support',
      workspaceName: 'Workspace em suporte',
      scopes: ['clients:read'],
      expiresAt,
    });
    expect(currentAppStore?.state.activeWorkspace?.id).toBe('workspace-support');
    expect(currentAppStore?.can('lista', 'view')).toBe(true);
    expect(normalSocket?.close).toHaveBeenCalled();
    expect(localStorage.getItem('klm_support_token')).toBeNull();
    expect(sessionStorage.getItem('klm_support_token')).toBeNull();
    expect(JSON.stringify([...Object.values(localStorage), ...Object.values(sessionStorage)])).not.toContain('ephemeral-support-token');

    queryClient.setQueryData(['support-sensitive', 'workspace-support'], { clientName: 'Temporário' });
    queryClient.getMutationCache().build(queryClient, {
      mutationKey: ['support-mode', 'workspace-support', 'grant-1', 'write'],
      mutationFn: async () => ({ created: true }),
    });
    await act(async () => {
      window.dispatchEvent(new Event('pagehide'));
      await Promise.resolve();
      await Promise.resolve();
    });

    expectOnlyCapabilityManifestQuery(queryClient, 'workspace-1');
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
    expect(currentAppStore?.supportMode).toBeNull();
    expect(currentAppStore?.supportModeTeardownPending).toBe(true);
    expect(currentAppStore?.state.session).toEqual(mainSession);
    expect(currentAppStore?.state.activeWorkspace?.id).toBe('workspace-1');
    expect(getSupportModePhase()).toBe('bootstrap');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).toBe('1');
  });

  it('keeps tenant access ending until query cancellation, cache removal and principal restoration complete', async () => {
    const { queryClient } = renderProvider(<ContextProbe />);
    await flushEffects();
    const mainSession = currentAppStore?.state.session;
    const expiresAt = new Date(Date.now() + 10 * 60_000).toISOString();
    const activation: SupportAccessActivation = {
      expiresIn: 600,
      grant: {
        id: 'grant-ending',
        workspaceId: 'workspace-support',
        requesterUserId: 'user-1',
        reason: 'Diagnóstico autorizado para validar o teardown.',
        ticketReference: 'SUP-END-1',
        requestedScopes: ['clients:read'],
        requestedDurationMinutes: 10,
        state: 'active',
        requestedAt: new Date().toISOString(),
        approverUserId: 'owner-2',
        approvedScopes: ['clients:read'],
        approvedDurationMinutes: 10,
        approvedAt: new Date().toISOString(),
        activatedAt: new Date().toISOString(),
        expiresAt,
        expiredAt: null,
        rejectedAt: null,
        rejectionReason: null,
        revokedAt: null,
        revocationReason: null,
        version: 3,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      },
    };
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: activation.grant.id,
      workspaceId: activation.grant.workspaceId,
      scopes: ['clients:read'],
      expiresAt,
    });
    await act(async () => {
      await currentAppStore?.enterSupportMode(activation, {
        id: 'workspace-support',
        name: 'Workspace em suporte',
      });
    });

    queryClient.setQueryData(['support-mode', 'workspace-support', 'read', 'clients:read'], { sensitive: true });
    queryClient.getMutationCache().build(queryClient, {
      mutationKey: ['support-mode', 'workspace-support', 'write', 'clients:write'],
      mutationFn: async () => ({ created: true }),
    });
    let releaseCancellation: (() => void) | undefined;
    vi.spyOn(queryClient, 'cancelQueries').mockImplementationOnce(() => (
      new Promise<void>((resolve) => { releaseCancellation = resolve; })
    ));

    let exitPromise: Promise<void> | undefined;
    act(() => {
      exitPromise = currentAppStore?.exitSupportMode();
    });
    await act(async () => { await Promise.resolve(); });

    expect(getSupportModePhase()).toBe('ending');
    expect(currentAppStore?.supportMode?.workspaceId).toBe('workspace-support');
    expect(currentAppStore?.state.activeWorkspace?.id).toBe('workspace-support');
    expect(queryKeys(queryClient)).toEqual(expect.arrayContaining([
      ['support-mode', 'workspace-support', 'read', 'clients:read'],
      ['workspace-capability-manifest', 'workspace-support'],
    ]));
    expect(queryKeys(queryClient)).toHaveLength(2);
    expect(queryClient.getMutationCache().getAll()).toHaveLength(1);

    await act(async () => {
      releaseCancellation?.();
      await exitPromise;
    });

    expectOnlyCapabilityManifestQuery(queryClient, 'workspace-1');
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
    expect(currentAppStore?.supportMode).toBeNull();
    expect(currentAppStore?.state.session).toEqual(mainSession);
    expect(currentAppStore?.state.activeWorkspace?.id).toBe('workspace-1');
    expect(getSupportModePhase()).toBe('inactive');
  });

  it('bootstraps a reload tombstone by clearing queries and mutations before reopening the principal flow', async () => {
    sessionStorage.setItem(SUPPORT_MODE_TOMBSTONE_KEY, '1');
    expect(initializeSupportModeFromStorage()).toBe(true);
    expect(getSupportModePhase()).toBe('bootstrap');

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(['tenant-sensitive', 'workspace-support'], { clientName: 'Temporário' });
    queryClient.getMutationCache().build(queryClient, {
      mutationKey: ['support-mode', 'workspace-support', 'grant-antigo', 'write'],
      mutationFn: async () => ({ created: true }),
    });
    let releaseCancellation: (() => void) | undefined;
    vi.spyOn(queryClient, 'cancelQueries').mockImplementationOnce(() => (
      new Promise<void>((resolve) => { releaseCancellation = resolve; })
    ));

    renderProvider(<ContextProbe />, queryClient);
    await act(async () => { await Promise.resolve(); });

    expect(currentAppStore?.supportModeTeardownPending).toBe(true);
    const pendingQueryKeys = queryKeys(queryClient);
    expect(pendingQueryKeys).toHaveLength(2);
    expect(pendingQueryKeys).toEqual(expect.arrayContaining([
      ['tenant-sensitive', 'workspace-support'],
      ['workspace-capability-manifest', 'workspace-1'],
    ]));
    expect(queryClient.getMutationCache().getAll()).toHaveLength(1);
    expect(testState.fetchCurrentSession).not.toHaveBeenCalled();

    await act(async () => {
      releaseCancellation?.();
      await Promise.resolve();
      await Promise.resolve();
    });

    expectOnlyCapabilityManifestQuery(queryClient, 'workspace-1');
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
    expect(currentAppStore?.supportModeTeardownPending).toBe(false);
    expect(currentAppStore?.state.session).toEqual(testState.session);
    expect(getSupportModePhase()).toBe('inactive');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).toBeNull();
  });
});
