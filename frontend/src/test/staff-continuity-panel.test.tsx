/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StaffContinuityPanel } from '@/components/admin/StaffContinuityPanel';

const reassignmentApi = vi.hoisted(() => ({
  list: vi.fn(),
  preview: vi.fn(),
  create: vi.fn(),
  restore: vi.fn(),
}));

vi.mock('@/services/staff-reassignment', async () => {
  const actual = await vi.importActual<typeof import('@/services/staff-reassignment')>(
    '@/services/staff-reassignment',
  );
  return {
    ...actual,
    listStaffReassignmentsPage: reassignmentApi.list,
    previewStaffReassignment: reassignmentApi.preview,
    createStaffReassignment: reassignmentApi.create,
    restoreStaffReassignment: reassignmentApi.restore,
  };
});

const users = [
  {
    id: '00000000-0000-0000-0000-000000000001',
    name: 'Ana Origem',
    email: 'ana@local.test',
    roleLabel: 'Suporte',
    isActive: true,
  },
  {
    id: '00000000-0000-0000-0000-000000000002',
    name: 'Bruno Destino',
    email: 'bruno@local.test',
    roleLabel: 'TI',
    isActive: true,
  },
];

function renderPanel(surface: 'workspace' | 'platform', canManage = true) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StaffContinuityPanel
        surface={surface}
        users={users}
        canManage={canManage}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('StaffContinuityPanel', () => {
  it('shows only platform scopes in the Blackbox surface', async () => {
    reassignmentApi.list.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0, hasMore: false });
    renderPanel('platform');

    expect(screen.getByText('Tickets de suporte e TI')).toBeInTheDocument();
    expect(screen.getByText('Oportunidades comerciais')).toBeInTheDocument();
    expect(screen.queryByText('Leads e casos')).not.toBeInTheDocument();
    await waitFor(() => expect(reassignmentApi.list).toHaveBeenCalledWith('platform', 10, 0, expect.any(AbortSignal)));
  });

  it('is directly embeddable in the workspace with its own authorized scopes', async () => {
    reassignmentApi.list.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0, hasMore: false });
    renderPanel('workspace');

    expect(screen.getByText('Leads e casos')).toBeInTheDocument();
    expect(screen.getByText('Conversas omnichannel')).toBeInTheDocument();
    expect(screen.queryByText('Tickets de suporte e TI')).not.toBeInTheDocument();
    await waitFor(() => expect(reassignmentApi.list).toHaveBeenCalledWith('workspace', 10, 0, expect.any(AbortSignal)));
  });

  it('keeps history visible but disables changes without management permission', async () => {
    reassignmentApi.list.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0, hasMore: false });
    renderPanel('platform', false);

    expect(screen.getByText(/não possui permissão para transferir responsabilidades/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revisar antes de aplicar' })).toBeDisabled();
    await waitFor(() => expect(screen.getByText('Nenhuma transferência registrada.')).toBeInTheDocument());
  });
});
