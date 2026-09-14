/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PlatformPeoplePanel } from '@/components/platform/PlatformPeoplePanel';

const platformApi = vi.hoisted(() => ({
  people: vi.fn(),
  packages: vi.fn(),
  lookup: vi.fn(),
  resolveLookup: vi.fn(),
  invite: vi.fn(),
  resend: vi.fn(),
  revoke: vi.fn(),
  revokeSessions: vi.fn(),
  block: vi.fn(),
  setPackage: vi.fn(),
}));

vi.mock('@/services/platform-search', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-search')>('@/services/platform-search');
  return {
    ...actual,
    lookupPlatformEntity: platformApi.lookup,
    resolvePlatformEntity: platformApi.resolveLookup,
  };
});

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>('@/services/platform');
  return {
    ...actual,
    fetchPlatformPeople: platformApi.people,
    fetchPlatformPeopleAccessPackages: platformApi.packages,
    invitePlatformPerson: platformApi.invite,
    resendPlatformPersonInvitation: platformApi.resend,
    revokePlatformPersonInvitation: platformApi.revoke,
    revokePlatformPersonSessions: platformApi.revokeSessions,
    setPlatformPersonBlocked: platformApi.block,
    setPlatformPersonAccessPackage: platformApi.setPackage,
  };
});

function renderPanel(canManage = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatformPeoplePanel enabled canManage={canManage} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  platformApi.lookup.mockResolvedValue({
    items: [{
      id: 'ws-1',
      entityType: 'accounts',
      label: 'Escritório A',
      publicIdentifier: 'escritorio-a',
      status: 'active',
      href: '/blackbox/accounts/ws-1',
      availableCommands: [],
    }],
    nextCursor: null,
    state: 'available',
  });
  platformApi.resolveLookup.mockResolvedValue({ items: [], state: 'available' });
  platformApi.packages.mockResolvedValue([
    { key: 'office_admin', label: 'Administrador do escritório', description: '', workspaceRole: 'workspace_admin', sensitiveDataPolicy: 'edit' },
    { key: 'finance', label: 'Financeiro / cobrança', description: '', workspaceRole: 'finance_manager', sensitiveDataPolicy: 'hidden' },
  ]);
});

describe('PlatformPeoplePanel', () => {
  it('renders the global user list with workspace and role', async () => {
    platformApi.people.mockResolvedValue({
      items: [
        {
          user_id: 'u1',
          name: 'Ana Advogada',
          email: 'ana@escritorio.com.br',
          workspace_id: 'ws-1',
          workspace_name: 'Escritório A',
          role: 'workspace_admin',
          membership_status: 'active',
          access_package: 'office_admin',
          last_seen_at: '2026-07-27T12:00:00Z',
          active_session_count: 2,
        },
      ],
      count: 1,
      limit: 100,
      offset: 0,
    });

    renderPanel();

    await waitFor(() => expect(screen.getByText('Ana Advogada')).toBeInTheDocument());
    expect(screen.getByText('Escritório A')).toBeInTheDocument();
    expect(screen.getByText('Bloquear')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('requires confirmation before blocking a workspace user', async () => {
    platformApi.people.mockResolvedValue({
      items: [{
        user_id: 'u1', name: 'Ana Advogada', email: 'ana@example.com', workspace_id: 'ws-1',
        workspace_name: 'Escritório A', role: 'workspace_admin', membership_status: 'active',
        access_package: 'office_admin', last_seen_at: null, active_session_count: 1,
      }],
      count: 1, limit: 100, offset: 0,
    });
    platformApi.block.mockResolvedValue({ membership_status: 'suspended' });

    renderPanel();
    fireEvent.click(await screen.findByText('Bloquear'));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('sessões ativas');
    expect(platformApi.block).not.toHaveBeenCalled();
    const reason = 'Bloqueio administrativo confirmado no ticket SUP-100.';
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: reason } });
    fireEvent.click(screen.getByText('Confirmar'));

    await waitFor(() => expect(platformApi.block).toHaveBeenCalledWith('ws-1', 'u1', true, reason));
  });

  it('requires confirmation before revoking active sessions', async () => {
    platformApi.people.mockResolvedValue({
      items: [{
        user_id: 'u1', name: 'Ana Advogada', email: 'ana@example.com', workspace_id: 'ws-1',
        workspace_name: 'Escritório A', role: 'workspace_admin', membership_status: 'active',
        access_package: 'office_admin', last_seen_at: null, active_session_count: 2,
      }],
      count: 1, limit: 100, offset: 0,
    });
    platformApi.revokeSessions.mockResolvedValue({ user_id: 'u1', workspace_id: 'ws-1', revoked_count: 2 });

    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Encerrar sessões' }));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('Todos os dispositivos');
    expect(platformApi.revokeSessions).not.toHaveBeenCalled();
    const reason = 'Encerramento solicitado pelo suporte no ticket SUP-101.';
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: reason } });
    fireEvent.click(screen.getByRole('button', { name: 'Encerrar todas' }));
    await waitFor(() => expect(platformApi.revokeSessions).toHaveBeenCalledWith('ws-1', 'u1', reason));
  });

  it('hides mutations in read-only mode', async () => {
    platformApi.people.mockResolvedValue({
      items: [{
        user_id: 'u1', name: 'Ana Advogada', email: 'ana@example.com', workspace_id: 'ws-1',
        workspace_name: 'Escritório A', role: 'workspace_admin', membership_status: 'active',
        access_package: 'office_admin', last_seen_at: null, active_session_count: 0,
      }],
      count: 1, limit: 100, offset: 0,
    });

    renderPanel(false);
    await screen.findByText('Ana Advogada');
    expect(screen.queryByText('Bloquear')).not.toBeInTheDocument();
    expect(screen.getByText('Somente leitura')).toBeInTheDocument();
  });

  it('filters by search term', async () => {
    platformApi.people.mockResolvedValue({ items: [], count: 0, limit: 100, offset: 0 });

    renderPanel();

    const input = await screen.findByPlaceholderText('Buscar por nome ou e-mail...');
    fireEvent.change(input, { target: { value: 'Ninguem' } });

    await waitFor(() => expect(platformApi.people).toHaveBeenCalledWith(
      expect.objectContaining({ search: 'Ninguem' }),
    ));
  });

  it('navigates real server-side pages', async () => {
    platformApi.people.mockImplementation(({ offset = 0 }: { offset?: number }) => Promise.resolve({
      items: [{
        user_id: `u-${offset}`, name: `Pessoa ${offset + 1}`, email: `pessoa-${offset}@example.com`, workspace_id: 'ws-1',
        workspace_name: 'Escritório A', role: 'member', membership_status: 'active',
        access_package: 'office_admin', last_seen_at: null, active_session_count: 0,
      }],
      count: 55,
      total: 55,
      limit: 50,
      offset,
      hasMore: offset === 0,
    }));

    renderPanel();
    await screen.findByText('Pessoa 1');
    expect(screen.getByText('1–1 de 55')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));

    await waitFor(() => expect(platformApi.people).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 50, offset: 50 }),
    ));
    await screen.findByText('Pessoa 51');
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Anterior' })).not.toBeDisabled();
  });

  it('invites a user into a selected workspace', async () => {
    platformApi.people.mockResolvedValue({ items: [], count: 0, limit: 100, offset: 0 });
    platformApi.invite.mockResolvedValue({
      id: 'inv-1', workspace_id: 'ws-1', user_id: 'u2', status: 'pending',
      email: 'nova@escritorio.com.br', expires_at: '2026-08-03T12:00:00Z', send_count: 1,
    });

    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Convidar usuário' }));
    fireEvent.click(screen.getByRole('button', { name: 'Conta do convite' }));
    fireEvent.click(await screen.findByRole('option', { name: /Escritório A/ }));
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Nova Pessoa' } });
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: 'nova@escritorio.com.br' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enviar convite' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: 'Enviar convite' }));

    await waitFor(() => expect(platformApi.invite).toHaveBeenCalledWith({
      workspaceId: 'ws-1',
      name: 'Nova Pessoa',
      email: 'nova@escritorio.com.br',
      accessPackage: 'office_admin',
    }, expect.anything()));
  });

  it('resends and revokes a pending invitation with confirmation', async () => {
    platformApi.people.mockResolvedValue({
      items: [{
        user_id: 'u2', name: 'Nova Pessoa', email: 'nova@escritorio.com.br', workspace_id: 'ws-1',
        workspace_name: 'Escritório A', role: 'finance_manager', membership_status: 'invited',
        access_package: 'finance', last_seen_at: null, active_session_count: 0,
        invitation_id: 'inv-1', invitation_status: 'pending',
        invitation_expires_at: '2026-08-03T12:00:00Z',
      }],
      count: 1, limit: 100, offset: 0,
    });
    platformApi.resend.mockResolvedValue({ id: 'inv-1', status: 'pending' });
    platformApi.revoke.mockResolvedValue({ id: 'inv-1', status: 'revoked' });

    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Reenviar' }));
    await waitFor(() => expect(platformApi.resend).toHaveBeenCalledWith('ws-1', 'inv-1'));

    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveTextContent('Cancelar convite');
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar convite' }));
    await waitFor(() => expect(platformApi.revoke).toHaveBeenCalledWith('ws-1', 'inv-1'));
  });
});
