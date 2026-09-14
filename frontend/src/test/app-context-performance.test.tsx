/** @vitest-environment jsdom */
import { act, cleanup, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { memo, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AuthUser, SessionContext } from '@/types';

const testState = vi.hoisted(() => ({
  session: {
    activeWorkspace: {
      id: 'workspace-1',
      name: 'KLM',
      slug: 'klm',
      status: 'active',
      isActive: true,
      onboardingCompleted: true,
    },
    availableWorkspaces: [],
    permissions: [{ module: 'dashboard', action: 'view', scope: 'workspace' }],
    isInternalStaff: true,
    activeRole: 'admin',
  },
  user: {
    id: 'user-1',
    name: 'Hermes',
    email: 'hermes@klm.test',
    role: 'admin',
    initials: 'HM',
    isInternalStaff: true,
  },
  apiFetch: vi.fn(),
  fetchCurrentSession: vi.fn(),
  fetchWebSocketTicket: vi.fn(),
  fetchMatters: vi.fn(),
  fetchMatterDetail: vi.fn(),
  fetchReportsSummary: vi.fn(),
  fetchApprovalItems: vi.fn(),
  fetchWhatsAppContacts: vi.fn(),
}));

vi.mock('@/services/auth', () => ({
  CLIENT_SESSION_EXPIRED_EVENT: 'klm:client-session-expired',
  expireClientSession: vi.fn(),
  fetchCurrentSession: testState.fetchCurrentSession,
  getAccessTokenExpiresAt: vi.fn(() => null),
  getSavedSession: vi.fn(() => null),
  getSavedUser: vi.fn(() => null),
  getToken: vi.fn(() => null),
  removeToken: vi.fn().mockResolvedValue(undefined),
  refreshAccessToken: vi.fn().mockResolvedValue('access-token'),
  requiresPasswordChange: (user?: { requiresPasswordChange?: boolean } | null, session?: { requiresPasswordChange?: boolean } | null) => (
    Boolean(user?.requiresPasswordChange || session?.requiresPasswordChange)
  ),
  saveSession: vi.fn(),
  SessionAccessError: class SessionAccessError extends Error {
    constructor(public readonly status: number, message: string) {
      super(message);
      this.name = 'SessionAccessError';
    }
  },
}));

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>();
  return {
    ...actual,
    apiFetch: testState.apiFetch,
    fetchWebSocketTicket: testState.fetchWebSocketTicket,
  };
});

vi.mock('@/services/matters', () => ({
  addMatterNote: vi.fn().mockResolvedValue({ message: 'ok', eventId: 'evt' }),
  toggleMatterTask: vi.fn(),
  updateMatterDetail: vi.fn(),
  fetchMatterDetail: testState.fetchMatterDetail,
  fetchMatters: testState.fetchMatters,
  overrideAgentRun: vi.fn(),
  retryAgentRun: vi.fn(),
}));

vi.mock('@/services/reports', () => ({
  fetchReportsSummary: testState.fetchReportsSummary,
}));

vi.mock('@/services/api', () => ({
  fetchApprovalItems: testState.fetchApprovalItems,
  resolveApprovalItem: vi.fn(),
}));

vi.mock('@/services/whatsapp', () => ({
  fetchWhatsAppConnections: vi.fn().mockResolvedValue([]),
  fetchWhatsAppContacts: testState.fetchWhatsAppContacts,
  fetchWhatsAppConversation: vi.fn(),
  handoffConversation: vi.fn(),
  markWhatsAppConversationRead: vi.fn(),
  sendWhatsAppMessage: vi.fn(),
}));

import { AppProvider, useAppStore } from '@/store/app-context';
import {
  clearSupportModeCredential,
  completeSupportModeEnding,
} from '@/support-mode/session';

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

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

  constructor(_url: string | URL, _protocols?: string | string[]) {
    MockWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }

  receive(event: string, matterId = 'matter-1'): void {
    this.onmessage?.(new MessageEvent('message', {
      data: JSON.stringify({ event, data: { matter_id: matterId } }),
    }));
  }
}

let currentStore: ReturnType<typeof useAppStore> | null = null;
let memoConsumerRenders = 0;

function StoreProbe() {
  currentStore = useAppStore();
  return null;
}

const MemoConsumer = memo(function MemoConsumer() {
  useAppStore();
  memoConsumerRenders += 1;
  return null;
});

function renderProvider(children: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>{children}</AppProvider>
    </QueryClientProvider>,
  );
}

async function flushEffects(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('AppContext performance controls', () => {
  beforeEach(() => {
    clearSupportModeCredential();
    completeSupportModeEnding();
    sessionStorage.clear();
    vi.clearAllMocks();
    testState.apiFetch.mockResolvedValue({
      workspaceId: testState.session.activeWorkspace.id,
      contractId: 'contract-1',
      contractVersion: 1,
      contractStatus: 'active',
      activeProducts: [],
      capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    testState.fetchCurrentSession.mockResolvedValue(testState.session);
    testState.fetchWebSocketTicket.mockResolvedValue({ ticket: 'ticket-1', expiresIn: 60 });
    testState.fetchMatters.mockResolvedValue([]);
    testState.fetchMatterDetail.mockResolvedValue({ id: 'matter-1' });
    testState.fetchReportsSummary.mockResolvedValue({ kpis: [] });
    testState.fetchApprovalItems.mockResolvedValue([]);
    testState.fetchWhatsAppContacts.mockResolvedValue([]);
    MockWebSocket.instances = [];
    currentStore = null;
    memoConsumerRenders = 0;
  });

  afterEach(() => {
    cleanup();
    clearSupportModeCredential();
    completeSupportModeEnding();
    sessionStorage.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('keeps the context value stable when only the provider parent rerenders', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <AppProvider><MemoConsumer /></AppProvider>
        <span>render 1</span>
      </QueryClientProvider>,
    );
    expect(memoConsumerRenders).toBe(1);

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <AppProvider><MemoConsumer /></AppProvider>
        <span>render 2</span>
      </QueryClientProvider>,
    );

    expect(memoConsumerRenders).toBe(1);
  });

  it('coalesces a websocket burst into one refresh per data source', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', MockWebSocket);
    const view = renderProvider(<StoreProbe />);

    act(() => {
      currentStore?.login(testState.user as AuthUser, testState.session as SessionContext);
    });
    await flushEffects();
    expect(MockWebSocket.instances).toHaveLength(1);
    act(() => MockWebSocket.instances[0].open());

    act(() => currentStore?.selectCard('matter-1'));
    await flushEffects();
    testState.fetchMatters.mockClear();
    testState.fetchMatterDetail.mockClear();
    testState.fetchReportsSummary.mockClear();
    testState.fetchApprovalItems.mockClear();
    testState.fetchWhatsAppContacts.mockClear();

    act(() => {
      MockWebSocket.instances[0].receive('CARD_UPDATED');
      MockWebSocket.instances[0].receive('AGENT_RUN_COMPLETED');
      MockWebSocket.instances[0].receive('APPROVAL_CREATED');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(testState.fetchMatters).not.toHaveBeenCalled();
    expect(testState.fetchMatterDetail).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(testState.fetchMatters).toHaveBeenCalledTimes(1);
    expect(testState.fetchMatterDetail).toHaveBeenCalledTimes(1);
    expect(testState.fetchReportsSummary).toHaveBeenCalledTimes(1);
    expect(testState.fetchApprovalItems).toHaveBeenCalledTimes(1);
    expect(testState.fetchWhatsAppContacts).toHaveBeenCalledTimes(1);

    view.unmount();
  });
});
