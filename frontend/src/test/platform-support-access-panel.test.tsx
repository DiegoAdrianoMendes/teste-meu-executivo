/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PlatformSupportAccessPanel } from '@/components/platform/PlatformSupportAccessPanel';
import {
  clearClientSession,
  getAdministrativeSessionToken,
  getToken,
  saveAdministrativeSessionToken,
  saveToken,
} from '@/services/auth';
import type { SupportAccessGrant } from '@/services/support-access';
import { SUPPORT_ACCESS_MAX_SCOPES } from '@/support-mode/scopes';
import { SUPPORT_STEP_UP_QUERY_KEY } from '@/support-mode/SupportStepUpPanel';

const supportApi = vi.hoisted(() => ({
  activate: vi.fn(),
  events: vi.fn(),
  list: vi.fn(),
  request: vi.fn(),
}));
const securityApi = vi.hoisted(() => ({
  beginAdministrativeSession: vi.fn(),
  beginMfaEnrollment: vi.fn(),
  current: vi.fn(),
  mfaStatus: vi.fn(),
  verifyAdministrativeSession: vi.fn(),
  verifyMfaEnrollment: vi.fn(),
}));
const searchApi = vi.hoisted(() => ({ lookup: vi.fn(), resolve: vi.fn() }));

vi.mock('@/services/support-access', async () => {
  const actual = await vi.importActual<typeof import('@/services/support-access')>('@/services/support-access');
  return {
    ...actual,
    activatePlatformSupportAccess: supportApi.activate,
    pagePlatformSupportAccess: supportApi.list,
    pagePlatformSupportAccessEvents: supportApi.events,
    requestPlatformSupportAccess: supportApi.request,
  };
});
vi.mock('@/services/platform-search', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-search')>('@/services/platform-search');
  return { ...actual, lookupPlatformEntity: searchApi.lookup, resolvePlatformEntity: searchApi.resolve };
});
vi.mock('@/services/auth-security', async () => {
  const actual = await vi.importActual<typeof import('@/services/auth-security')>('@/services/auth-security');
  return {
    ...actual,
    beginAdministrativeSession: securityApi.beginAdministrativeSession,
    beginMfaEnrollment: securityApi.beginMfaEnrollment,
    fetchCurrentAdministrativeSession: securityApi.current,
    fetchMfaStatus: securityApi.mfaStatus,
    verifyAdministrativeSession: securityApi.verifyAdministrativeSession,
    verifyMfaEnrollment: securityApi.verifyMfaEnrollment,
  };
});

const approvedGrant: SupportAccessGrant = {
  id: 'grant-1',
  workspaceId: 'workspace-1',
  requesterUserId: 'platform-admin-1',
  reason: 'Diagnóstico local necessário para restaurar o acesso do cliente.',
  ticketReference: 'SUP-1234',
  requestedScopes: ['workspace:read', 'clients:read'],
  requestedDurationMinutes: 15,
  state: 'approved',
  requestedAt: '2026-07-19T15:00:00Z',
  approverUserId: 'workspace-owner-1',
  approvedScopes: ['workspace:read'],
  approvedDurationMinutes: 10,
  approvedAt: '2026-07-19T15:02:00Z',
  activatedAt: null,
  expiresAt: null,
  expiredAt: null,
  rejectedAt: null,
  rejectionReason: null,
  revokedAt: null,
  revocationReason: null,
  version: 2,
  createdAt: '2026-07-19T15:00:00Z',
  updatedAt: '2026-07-19T15:02:00Z',
};

const workspaces = [
  { id: 'workspace-1', name: 'KLM Advocacia', slug: 'klm' },
  { id: 'workspace-2', name: 'Outro Escritório', slug: 'outro' },
];

function activeAdminSession() {
  const now = Date.now();
  return {
    active: true,
    session: {
      id: 'admin-session-1',
      mfaMethod: 'challenge' as const,
      expiresAt: new Date(now + 10 * 60_000).toISOString(),
      lastVerifiedAt: new Date(now).toISOString(),
      revokedAt: null,
      revocationReason: null,
      createdAt: new Date(now).toISOString(),
    },
  };
}

function storedValues(storage: Storage): string[] {
  return Array.from({ length: storage.length }, (_, index) => storage.getItem(storage.key(index) ?? '') ?? '');
}

function renderPanel(
  props?: Partial<React.ComponentProps<typeof PlatformSupportAccessPanel>>,
  configureQueryClient?: (queryClient: QueryClient) => void,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  configureQueryClient?.(queryClient);
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <PlatformSupportAccessPanel
        workspaces={workspaces}
        currentUserId="platform-admin-1"
        {...props}
      />
    </QueryClientProvider>,
  );
  return { ...rendered, queryClient };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  supportApi.list.mockResolvedValue({ items: [approvedGrant], total: 1, limit: 25, offset: 0, hasMore: false, sort: [] });
  const lookupItems = workspaces.map((workspace) => ({
    id: workspace.id, entityType: 'accounts', label: workspace.name,
    publicIdentifier: workspace.slug, secondaryLabel: null, workspaceId: workspace.id,
    workspaceLabel: workspace.name, status: 'active', maskedEmail: null,
    badges: [], metadata: {}, allowedCommands: [], href: `/blackbox/workspaces/${workspace.id}`,
  }));
  searchApi.lookup.mockResolvedValue({ entityType: 'accounts', state: 'ready', items: lookupItems, nextCursor: null, errorCode: null });
  searchApi.resolve.mockImplementation(async ({ ids }: { ids: string[] }) => ({ entityType: 'accounts', state: 'ready', items: lookupItems.filter((item) => ids.includes(item.id)), nextCursor: null, errorCode: null }));
  supportApi.events.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, hasMore: false });
  securityApi.mfaStatus.mockResolvedValue({ enabled: true, simulated: true, recoveryCodesRemaining: 8 });
  securityApi.current.mockResolvedValue(activeAdminSession());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  clearClientSession();
  localStorage.clear();
  sessionStorage.clear();
});

describe('PlatformSupportAccessPanel', () => {
  it('requests access only for a workspace selected from the real catalog', async () => {
    supportApi.request.mockResolvedValue({ ...approvedGrant, id: 'grant-2', state: 'requested' });
    supportApi.events.mockResolvedValue({ items: [{
      id: 'event-workspace-1',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      actorUserId: 'workspace-owner-1',
      eventType: 'support_access_approved',
      fromState: 'requested',
      toState: 'approved',
      details: {},
      occurredAt: '2026-07-19T15:02:00Z',
    }], total: 1, limit: 25, offset: 0, hasMore: false });
    const { queryClient } = renderPanel();

    const workspaceSelect = await screen.findByRole('button', { name: 'Selecionar conta para acesso administrativo' });
    await waitFor(() => expect(supportApi.list).toHaveBeenCalledWith({
      workspaceId: 'workspace-1',
      state: undefined,
      limit: 25,
      offset: 0,
    }));
    fireEvent.click(await screen.findByRole('button', { name: /Ver trilha/ }));
    await screen.findByText('Acesso aprovado');
    expect(queryClient.getQueryCache().find({
      queryKey: ['support-access', 'platform', 'workspace-1', 'events', 'grant-1', 0],
      exact: true,
    })).toBeDefined();
    fireEvent.click(workspaceSelect);
    fireEvent.click(await screen.findByRole('option', { name: /Outro Escritório/ }));
    await waitFor(() => expect(supportApi.list).toHaveBeenCalledWith({
      workspaceId: 'workspace-2',
      state: undefined,
      limit: 25,
      offset: 0,
    }));
    expect(queryClient.getQueryCache().findAll({
      queryKey: ['support-access', 'platform', 'workspace-1'],
    })).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Ticket'), { target: { value: 'SUP-5678' } });
    fireEvent.change(screen.getByLabelText('Motivo operacional'), {
      target: { value: 'Validar localmente a falha reportada no atendimento.' },
    });
    fireEvent.change(screen.getByLabelText('Minutos'), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Solicitar acesso' }));

    await waitFor(() => expect(supportApi.request).toHaveBeenCalledWith({
      workspaceId: 'workspace-2',
      ticketReference: 'SUP-5678',
      reason: 'Validar localmente a falha reportada no atendimento.',
      requestedScopes: ['workspace:read'],
      durationMinutes: 20,
    }));
  });

  it('keeps activation disabled until an MFA administrative session is active', async () => {
    securityApi.current.mockResolvedValue(null);
    renderPanel();

    const activateButton = await screen.findByRole('button', { name: 'Ativar com MFA' });
    expect(activateButton).toBeDisabled();
    expect(screen.getByText('Sessão administrativa ausente. Confirme o MFA antes da ativação.')).toBeInTheDocument();
    expect(supportApi.activate).not.toHaveBeenCalled();
  });

  it('fails closed while the current platform identity is unavailable', async () => {
    renderPanel({ currentUserId: null });

    expect(await screen.findByRole('button', { name: 'Ativar com MFA' })).toBeDisabled();
    expect(screen.getByText('Identidade da sessão indisponível.')).toBeInTheDocument();
  });

  it('caps each request at the complete scope catalog accepted by the backend schema', async () => {
    renderPanel();

    const scopes = await screen.findAllByRole('checkbox');
    for (const scope of scopes.slice(1, SUPPORT_ACCESS_MAX_SCOPES)) fireEvent.click(scope);

    expect(screen.getByText(`${SUPPORT_ACCESS_MAX_SCOPES}/${SUPPORT_ACCESS_MAX_SCOPES} escopos selecionados.`)).toBeInTheDocument();
  });

  it('activates with MFA without exposing the bearer outside the memory-only session vault', async () => {
    const expiresAt = new Date(Date.now() + 10 * 60_000).toISOString();
    supportApi.activate.mockResolvedValue({
      expiresIn: 600,
      grant: {
        ...approvedGrant,
        state: 'active',
        activatedAt: new Date().toISOString(),
        expiresAt,
      },
    });
    const onEnterWorkspace = vi.fn().mockResolvedValue(undefined);
    const { queryClient } = renderPanel({ onEnterWorkspace });

    const activateButton = await screen.findByRole('button', { name: 'Ativar com MFA' });
    await waitFor(() => expect(activateButton).toBeEnabled());
    fireEvent.click(activateButton);

    await waitFor(() => expect(supportApi.activate).toHaveBeenCalledWith('grant-1'));
    expect(await screen.findByTestId('support-access-ready')).toHaveTextContent('Acesso temporário pronto');
    expect(screen.queryByText('ephemeral-support-token')).not.toBeInTheDocument();
    expect(document.querySelector('[aria-live]')?.textContent ?? '').not.toContain('ephemeral-support-token');
    expect(JSON.stringify(queryClient.getMutationCache().getAll())).not.toContain('ephemeral-support-token');
    expect(storedValues(localStorage)).not.toContain('ephemeral-support-token');
    expect(storedValues(sessionStorage)).not.toContain('ephemeral-support-token');

    fireEvent.click(screen.getByRole('button', { name: 'Entrar no workspace' }));
    await waitFor(() => expect(onEnterWorkspace).toHaveBeenCalledWith(
      expect.objectContaining({ expiresIn: 600, grant: expect.objectContaining({ expiresAt }) }),
      workspaces[0],
    ));
  });

  it('enrolls MFA natively before creating a platform step-up session', async () => {
    securityApi.mfaStatus
      .mockResolvedValueOnce({ enabled: false, simulated: true, recoveryCodesRemaining: 0 })
      .mockResolvedValue({ enabled: true, simulated: true, recoveryCodesRemaining: 8 });
    securityApi.beginMfaEnrollment.mockResolvedValue({
      challengeId: 'enrollment-1',
      purpose: 'enrollment',
      expiresIn: 300,
      attemptsRemaining: 5,
      recoveryAvailable: false,
      simulatedCode: '112233',
    });
    securityApi.verifyMfaEnrollment.mockResolvedValue({
      enabled: true,
      simulated: true,
      recoveryCodesRemaining: 8,
      recoveryCodes: ['RECOVERY-1'],
    });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Habilitar MFA' }));
    fireEvent.change(await screen.findByLabelText('Código de verificação'), { target: { value: '112233' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar habilitação' }));

    await waitFor(() => expect(securityApi.verifyMfaEnrollment).toHaveBeenCalledWith(
      'enrollment-1',
      { code: '112233' },
    ));
    expect(await screen.findByText('Códigos de recuperação gerados uma única vez')).toBeInTheDocument();
  });

  it('creates the MFA step-up natively and enables activation without workspace membership', async () => {
    securityApi.current
      .mockResolvedValueOnce(null)
      .mockResolvedValue(activeAdminSession());
    securityApi.beginAdministrativeSession.mockResolvedValue({
      challengeId: 'admin-challenge-1',
      purpose: 'admin_session',
      expiresIn: 300,
      attemptsRemaining: 5,
      recoveryAvailable: true,
      simulatedCode: '445566',
    });
    securityApi.verifyAdministrativeSession.mockResolvedValue({
      administrativeSessionToken: 'admin-step-up-token',
      tokenType: 'admin_session',
      expiresIn: 600,
      session: activeAdminSession().session,
    });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Confirmar MFA para ativar' }));
    fireEvent.change(await screen.findByLabelText('Código de verificação'), { target: { value: '445566' } });
    fireEvent.click(screen.getByRole('button', { name: 'Elevar sessão' }));

    await waitFor(() => expect(securityApi.verifyAdministrativeSession).toHaveBeenCalledWith(
      'admin-challenge-1',
      { code: '445566' },
    ));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Ativar com MFA' })).toBeEnabled());
  });

  it('expires the administrative step-up exactly at expiresAt without clearing the principal flow', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T12:00:00Z'));
    saveToken('main-access-token');
    saveAdministrativeSessionToken('admin-step-up-token');
    const administrativeSession = {
      ...activeAdminSession(),
      session: {
        ...activeAdminSession().session,
        expiresAt: new Date(Date.now() + 1_000).toISOString(),
      },
    };
    renderPanel(undefined, (queryClient) => {
      queryClient.setQueryDefaults(SUPPORT_STEP_UP_QUERY_KEY, { staleTime: Number.POSITIVE_INFINITY });
      queryClient.setQueryData(
        [...SUPPORT_STEP_UP_QUERY_KEY, 'mfa'],
        { enabled: true, simulated: true, recoveryCodesRemaining: 8 },
      );
      queryClient.setQueryData(
        [...SUPPORT_STEP_UP_QUERY_KEY, 'administrative-session'],
        administrativeSession,
      );
    });

    await act(async () => {
      for (let index = 0; index < 6; index += 1) {
        await Promise.resolve();
        await vi.advanceTimersByTimeAsync(0);
      }
    });
    expect(screen.getByRole('button', { name: 'Ativar com MFA' })).toBeEnabled();

    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(getAdministrativeSessionToken()).toBe('admin-step-up-token');
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });

    expect(getAdministrativeSessionToken()).toBeNull();
    expect(getToken()).toBe('main-access-token');
    expect(screen.getByRole('button', { name: 'Ativar com MFA' })).toBeDisabled();
  });

  it('shows the platform audit trail on demand', async () => {
    supportApi.events.mockResolvedValue({ items: [{
      id: 'event-1',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      actorUserId: 'workspace-owner-1',
      eventType: 'support_access_approved',
      fromState: 'requested',
      toState: 'approved',
      details: { scopes: ['workspace:read'], duration_minutes: 10 },
      occurredAt: '2026-07-19T15:02:00Z',
    }], total: 1, limit: 25, offset: 0, hasMore: false });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /Ver trilha/ }));

    expect(await screen.findByText('Acesso aprovado')).toBeInTheDocument();
    expect(supportApi.events).toHaveBeenCalledWith('grant-1', { limit: 25, offset: 0 });
  });
});
