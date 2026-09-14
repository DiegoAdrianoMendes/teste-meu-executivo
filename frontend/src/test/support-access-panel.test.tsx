/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SupportAccessPanel } from '@/components/admin/SupportAccessPanel';
import type { SupportAccessGrant, SupportAccessScope } from '@/services/support-access';
import { useSupportAccessDeadline } from '@/support-mode/use-support-deadline';

const supportApi = vi.hoisted(() => ({
  approve: vi.fn(),
  events: vi.fn(),
  list: vi.fn(),
  reject: vi.fn(),
  revoke: vi.fn(),
}));

vi.mock('@/services/support-access', async () => {
  const actual = await vi.importActual<typeof import('@/services/support-access')>('@/services/support-access');
  return {
    ...actual,
    approveWorkspaceSupportAccess: supportApi.approve,
    pageWorkspaceSupportAccess: supportApi.list,
    pageWorkspaceSupportAccessEvents: supportApi.events,
    rejectWorkspaceSupportAccess: supportApi.reject,
    revokeWorkspaceSupportAccess: supportApi.revoke,
  };
});

const baseGrant: SupportAccessGrant = {
  id: 'grant-1',
  workspaceId: 'workspace-1',
  requesterUserId: 'platform-admin-1',
  reason: 'Diagnóstico local necessário para restaurar o acesso do cliente.',
  ticketReference: 'SUP-1234',
  requestedScopes: ['workspace:read', 'workspace:write'],
  requestedDurationMinutes: 20,
  state: 'requested',
  requestedAt: '2026-07-19T15:00:00Z',
  approverUserId: null,
  approvedScopes: null,
  approvedDurationMinutes: null,
  approvedAt: null,
  activatedAt: null,
  expiresAt: null,
  expiredAt: null,
  rejectedAt: null,
  rejectionReason: null,
  revokedAt: null,
  revocationReason: null,
  version: 1,
  createdAt: '2026-07-19T15:00:00Z',
  updatedAt: '2026-07-19T15:00:00Z',
};

const grantPage = (items: SupportAccessGrant[]) => ({
  items,
  total: items.length,
  limit: 25,
  offset: 0,
  hasMore: false,
  sort: [],
});

function renderPanel(props?: Partial<React.ComponentProps<typeof SupportAccessPanel>>) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const panel = (overrides?: Partial<React.ComponentProps<typeof SupportAccessPanel>>) => (
    <QueryClientProvider client={queryClient}>
      <SupportAccessPanel
        activeWorkspaceId="workspace-1"
        currentUserId="workspace-owner-1"
        {...props}
        {...overrides}
      />
    </QueryClientProvider>
  );
  const rendered = render(panel());
  return {
    ...rendered,
    queryClient,
    rerenderPanel: (overrides: Partial<React.ComponentProps<typeof SupportAccessPanel>>) => rendered.rerender(panel(overrides)),
  };
}

function DeadlineProbe({
  queryClient,
  grants,
}: {
  queryClient: QueryClient;
  grants: SupportAccessGrant[];
}) {
  useSupportAccessDeadline(
    queryClient,
    ['support-access', 'workspace', 'workspace-1', 'requests'],
    grants,
  );
  return null;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('SupportAccessPanel', () => {
  it('blocks users outside the owner/admin roles without calling the API', () => {
    renderPanel({ canManage: false });

    expect(screen.getByText('Sem permissão para consultar ou decidir acessos de suporte.')).toBeInTheDocument();
    expect(supportApi.list).not.toHaveBeenCalled();
  });

  it('approves only the selected subset and a reduced duration', async () => {
    supportApi.list.mockResolvedValue(grantPage([baseGrant]));
    supportApi.approve.mockResolvedValue({
      ...baseGrant,
      state: 'approved',
      approvedScopes: ['workspace:read'],
      approvedDurationMinutes: 10,
    });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Revisar e aprovar' }));
    const writeScope = await screen.findByRole('checkbox', { name: /Workspace: alteração/ });
    expect(writeScope).toBeChecked();
    fireEvent.click(writeScope);
    fireEvent.change(screen.getByLabelText('Duração aprovada em minutos'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Aprovar acesso' }));

    await waitFor(() => expect(supportApi.approve).toHaveBeenCalledWith('grant-1', {
      approvedScopes: ['workspace:read'],
      durationMinutes: 10,
    }));
  });

  it('requires and sends the reason for destructive decisions', async () => {
    supportApi.list.mockResolvedValue(grantPage([baseGrant]));
    supportApi.reject.mockResolvedValue({ ...baseGrant, state: 'rejected' });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Rejeitar' }));
    const confirm = screen.getByRole('button', { name: 'Confirmar rejeição' });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'Pedido sem necessidade operacional atual.' } });
    fireEvent.click(confirm);

    await waitFor(() => expect(supportApi.reject).toHaveBeenCalledWith('grant-1', {
      reason: 'Pedido sem necessidade operacional atual.',
    }));
  });

  it('revokes approved access with an explicit reason', async () => {
    const approved = {
      ...baseGrant,
      state: 'approved' as const,
      approvedScopes: ['workspace:read'] as SupportAccessScope[],
      approvedDurationMinutes: 10,
    };
    supportApi.list.mockResolvedValue(grantPage([approved]));
    supportApi.revoke.mockResolvedValue({ ...approved, state: 'revoked' });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Revogar' }));
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'Diagnóstico local concluído.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar revogação' }));

    await waitFor(() => expect(supportApi.revoke).toHaveBeenCalledWith('grant-1', {
      reason: 'Diagnóstico local concluído.',
    }));
  });

  it('loads the immutable trail only when requested', async () => {
    supportApi.list.mockResolvedValue(grantPage([baseGrant]));
    supportApi.events.mockResolvedValue({ items: [{
      id: 'event-1',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      actorUserId: 'platform-admin-1',
      eventType: 'support_access_requested',
      fromState: null,
      toState: 'requested',
      details: { scopes: ['workspace:read'], duration_minutes: 20 },
      occurredAt: '2026-07-19T15:00:00Z',
    }], total: 1, limit: 25, offset: 0, hasMore: false });
    renderPanel();

    expect(supportApi.events).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: /Ver trilha/ }));

    expect(await screen.findByText('Acesso solicitado')).toBeInTheDocument();
    expect(supportApi.events).toHaveBeenCalledWith('grant-1', { limit: 25, offset: 0 });
  });

  it('keys the sensitive request cache by active workspace', async () => {
    supportApi.list.mockResolvedValue(grantPage([baseGrant]));
    const { queryClient } = renderPanel();

    await screen.findByRole('button', { name: 'Revisar e aprovar' });
    expect(queryClient.getQueryCache().find({
      queryKey: ['support-access', 'workspace', 'workspace-1', 'requests', 'all', 0],
      exact: true,
    })).toBeDefined();
  });

  it('purges requests and events from the previous workspace under one prefix', async () => {
    supportApi.list.mockResolvedValue(grantPage([baseGrant]));
    supportApi.events.mockResolvedValue({ items: [{
      id: 'event-1',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      actorUserId: 'platform-admin-1',
      eventType: 'support_access_requested',
      fromState: null,
      toState: 'requested',
      details: {},
      occurredAt: '2026-07-19T15:00:00Z',
    }], total: 1, limit: 25, offset: 0, hasMore: false });
    const { queryClient, rerenderPanel } = renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /Ver trilha/ }));
    await screen.findByText('Acesso solicitado');
    expect(queryClient.getQueryCache().findAll({
      queryKey: ['support-access', 'workspace', 'workspace-1'],
    }).length).toBeGreaterThanOrEqual(2);

    rerenderPanel({ activeWorkspaceId: 'workspace-2' });
    await waitFor(() => expect(queryClient.getQueryCache().findAll({
      queryKey: ['support-access', 'workspace', 'workspace-1'],
    })).toHaveLength(0));
  });

  it('prevents the requester from approving their own access', async () => {
    supportApi.list.mockResolvedValue(grantPage([baseGrant]));
    renderPanel({ currentUserId: 'platform-admin-1' });

    expect(await screen.findByRole('button', { name: 'Revisar e aprovar' })).toBeDisabled();
    expect(screen.getByText('O solicitante não pode aprovar o próprio acesso de suporte.')).toBeInTheDocument();
    expect(supportApi.approve).not.toHaveBeenCalled();
  });

  it('fails closed when the approver identity is unavailable', async () => {
    supportApi.list.mockResolvedValue(grantPage([baseGrant]));
    renderPanel({ currentUserId: null });

    expect(await screen.findByRole('button', { name: 'Revisar e aprovar' })).toBeDisabled();
    expect(screen.getByText('A identidade do aprovador está indisponível. A aprovação permanece bloqueada.')).toBeInTheDocument();
    expect(supportApi.approve).not.toHaveBeenCalled();
  });

  it('does not approve lead data without a primary scope', async () => {
    supportApi.list.mockResolvedValue(grantPage([{
      ...baseGrant,
      requestedScopes: ['lead_data:read'],
    }]));
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Revisar e aprovar' }));
    expect(await screen.findByText('Dados de leads exigem ao menos um escopo primário.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Aprovar acesso' })).toBeDisabled();
    expect(supportApi.approve).not.toHaveBeenCalled();
  });

  it('invalidates active grants at their authoritative expiry deadline', async () => {
    vi.useFakeTimers();
    const queryClient = new QueryClient();
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries');
    const activeGrant: SupportAccessGrant = {
      ...baseGrant,
      state: 'active',
      expiresAt: new Date(Date.now() + 1_000).toISOString(),
    };
    render(<DeadlineProbe queryClient={queryClient} grants={[activeGrant]} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(invalidateQueries).not.toHaveBeenCalled();

    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['support-access', 'workspace', 'workspace-1', 'requests'],
    });
  });
});
