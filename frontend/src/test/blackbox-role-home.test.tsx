/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BlackboxRoleHome from '@/pages/BlackboxRoleHome';
import type { PlatformDashboardHome } from '@/services/platform-dashboard';

const dashboardApi = vi.hoisted(() => ({ fetchHome: vi.fn() }));
const storeApi = vi.hoisted(() => ({
  currentUser: {
    id: 'support-user',
    name: 'Suporte Um',
    platformRolePackage: 'support',
    isPlatformRoot: false,
    platformPermissions: [
      { module: 'dashboard', action: 'view' },
      { module: 'support', action: 'view' },
      { module: 'support', action: 'respond' },
    ],
  },
}));

vi.mock('@/services/platform-dashboard', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-dashboard')>(
    '@/services/platform-dashboard',
  );
  return { ...actual, fetchPlatformDashboardHome: dashboardApi.fetchHome };
});

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ state: { currentUser: storeApi.currentUser } }),
}));

const dashboard: PlatformDashboardHome = {
  role: 'support',
  roleLabel: 'Suporte',
  isPlatformRoot: false,
  scope: 'mine',
  availableScopes: ['mine'],
  teamId: null,
  teamName: null,
  periodDays: 30,
  generatedAt: '2026-07-28T12:00:00Z',
  layout: {
    templateKey: 'blackbox-home:support:v1',
    templateVersion: 1,
    mandatoryWidgets: ['alerts', 'priority_queue', 'metrics'],
    optionalWidgets: ['quick_actions'],
    hiddenWidgets: [],
    widgetOrder: ['alerts', 'priority_queue', 'metrics', 'quick_actions'],
    preferenceEtag: null,
  },
  metrics: [
    {
      key: 'tickets_open',
      label: 'Tickets abertos',
      value: 3,
      unit: 'count',
      quality: 'updated',
      source: 'support',
      updatedAt: '2026-07-28T12:00:00Z',
      href: '/blackbox/support-tickets',
      description: 'Tickets ainda não encerrados.',
    },
    {
      key: 'tickets_overdue',
      label: 'SLA vencido',
      value: null,
      unit: 'count',
      quality: 'unavailable',
      source: 'support',
      updatedAt: '2026-07-28T12:00:00Z',
      href: '/blackbox/support-tickets',
      description: 'Fonte indisponível; nenhum zero substitutivo foi usado.',
    },
  ],
  workItems: [
    {
      id: 'ticket-1',
      sourceType: 'support_ticket',
      priority: 'P1',
      safeTitle: 'Ticket SUP-101',
      safeSummary: 'Fila support · severidade high',
      href: '/blackbox/support-tickets?ticket=ticket-1',
      dueAt: '2026-07-28T15:00:00Z',
    },
  ],
  alerts: [],
  actions: [
    {
      key: 'tickets',
      label: 'Atender tickets',
      href: '/blackbox/support-tickets',
      module: 'support',
      action: 'respond',
    },
  ],
  sourceHealth: [
    {
      key: 'support',
      label: 'Support',
      status: 'partial',
      updatedAt: '2026-07-28T12:00:00Z',
      detail: 'Uma consulta ficou indisponível.',
    },
  ],
};

function renderHome() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><BlackboxRoleHome /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  dashboardApi.fetchHome.mockReset().mockResolvedValue(dashboard);
});

afterEach(cleanup);

describe('BlackboxRoleHome', () => {
  it('renders real role KPIs, queue, actions and freshness', async () => {
    renderHome();

    expect(await screen.findByRole('heading', { name: 'Suporte' })).toBeInTheDocument();
    expect(screen.getByText('Tickets abertos')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('Ticket SUP-101')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Atender tickets/i })).toBeInTheDocument();
    expect(screen.getAllByText(/Atualizado/).length).toBeGreaterThan(0);
  });

  it('shows unavailable metrics as dash and exposes partial data quality', async () => {
    renderHome();

    await waitFor(() => expect(dashboardApi.fetchHome).toHaveBeenCalledWith('mine', 30));
    expect(await screen.findByText('SLA vencido')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByText(/sem zeros fictícios/i)).toBeInTheDocument();
    expect(screen.getByText('Indisponível')).toBeInTheDocument();
  });

  it('does not replace a failed dashboard request with navigation counts', async () => {
    dashboardApi.fetchHome.mockRejectedValue(new Error('backend unavailable'));
    renderHome();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Não foi possível carregar o painel operacional');
    expect(alert).toHaveTextContent('não foi convertida em zeros');
  });
});
