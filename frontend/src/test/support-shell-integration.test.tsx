/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

import { klmApi } from '@/klm/api';
import type { KlmSupportTicketDetail } from '@/klm/api';
import { KlmShell } from '@/klm/components/KlmShell';

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: () => true,
    logout: vi.fn(async () => undefined),
    state: {
      currentUser: {
        id: 'user-1',
        name: 'Kleist Monteiro',
        email: 'kleisle@example.test',
        initials: 'KM',
        role: 'Administrador',
      },
      activeWorkspace: { id: 'workspace-1', name: 'Meu Escritório', slug: 'meu-escritorio' },
      session: {
        activeRole: 'workspace_owner',
        activeWorkspace: { id: 'workspace-1', name: 'Meu Escritório', slug: 'meu-escritorio' },
        availableWorkspaces: [{ id: 'membership-1' }],
      },
    },
  }),
}));

vi.mock('@/klm/components/NativeContextTools', () => ({ NativeContextTools: () => null }));
vi.mock('@/services/admin', () => ({
  fetchWorkspaceAiCosts: vi.fn(async () => ({
    usedPercent: 0,
    monthlyLimit: null,
    totalTokens: 0,
  })),
  fetchWorkspacePlanSummary: vi.fn(async () => null),
}));
vi.mock('@/services/platform', () => ({
  fetchPlatformAiCosts: vi.fn(async () => ({
    usedPercent: 0,
    monthlyLimit: null,
    totalTokens: 0,
  })),
}));
vi.mock('@/services/platform-support-settings', () => ({
  getPlatformSupportAvailabilityStatus: vi.fn(async () => ({
    state: 'online',
    message: 'Equipe disponível agora.',
  })),
  getSupportEscalationStatus: vi.fn(async () => ({
    availableNow: false,
    schedulingUrl: null,
    whatsappLink: null,
    nextWindow: null,
  })),
}));
vi.mock('@/services/configurations', () => ({
  fetchWorkspaceExperience: vi.fn(async () => ({
    supportLabel: 'Atendimento Meu Executivo',
    welcomeMessage: 'Descreva o que aconteceu para acompanharmos em tempo real.',
  })),
}));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="support-location">{location.pathname}{location.search}</output>;
}

const supportTicketDetail: KlmSupportTicketDetail = {
  id: 'ticket-1',
  publicNumber: 'SAC-2026-0001',
  workspaceId: 'workspace-1',
  reporterUserId: 'user-1',
  assignedToUserId: null,
  ticketType: 'problem',
  priority: 'normal',
  severity: 'medium',
  queue: 'support',
  status: 'new',
  subject: 'Falha ao salvar',
  description: 'O formulário não conclui o salvamento.',
  route: '/app/conversas',
  technicalContext: {},
  errorFingerprint: null,
  linkedErrorLogId: null,
  linkedIncidentId: null,
  linkedReleaseId: null,
  supportAccessRequestId: null,
  duplicateOfTicketId: null,
  version: 1,
  firstResponseDueAt: '2026-07-24T16:00:00.000Z',
  resolutionDueAt: '2026-07-25T16:00:00.000Z',
  firstRespondedAt: null,
  resolvedAt: null,
  closedAt: null,
  reopenedAt: null,
  satisfactionScore: null,
  satisfactionComment: null,
  createdAt: '2026-07-24T15:00:00.000Z',
  updatedAt: '2026-07-24T15:00:00.000Z',
  reporter: null,
  assignee: null,
  messages: [],
  attachments: [],
  events: [],
  collaboratorIds: [],
};

function renderShell(initialEntry = '/app/conversas?thread=thread-1') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <KlmShell />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(klmApi, 'dashboard').mockResolvedValue({
    metrics: { leads: 0, qualified: 0, activeThreads: 0, pendingApprovals: 0, dueFollowUps: 0, externalActions: 0 },
    funnel: [],
    decisions: [],
    runtime: {
      mode: 'local',
      localOnly: true,
      localInfrastructure: true,
      externalActionsEnabled: false,
      awsEnabled: false,
      modelApisEnabled: false,
      onlineApproved: false,
      localBootstrapEnabled: true,
      providers: {},
    },
  });
  vi.spyOn(klmApi, 'quality').mockResolvedValue({
    score: 100,
    metrics: {},
    dimensions: [],
    reviewQueue: [],
    generatedAt: '2026-07-24T15:00:00.000Z',
  });
  vi.spyOn(klmApi, 'funnel').mockResolvedValue({
    boardVersion: 1,
    stages: [],
    metrics: {},
    cards: [],
  });
  vi.spyOn(klmApi, 'threads').mockResolvedValue([]);
  vi.spyOn(klmApi, 'supportTickets').mockResolvedValue({ items: [], nextCursor: null });
  vi.spyOn(klmApi, 'supportTicket').mockResolvedValue(supportTicketDetail);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SupportCenter shell integration', () => {
  it('offers the real-time support entry point over every workspace page', async () => {
    renderShell();

    const supportButton = await screen.findByTestId('floating-support-button');
    await waitFor(() => expect(supportButton).toHaveTextContent('Atendimento Meu Executivo'));
    fireEvent.click(supportButton);

    expect(await screen.findByRole('dialog', { name: 'Atendimento Meu Executivo' })).toBeVisible();
    expect(screen.getByText('Descreva o que aconteceu para acompanharmos em tempo real.')).toBeVisible();
    expect(screen.getByRole('tab', { name: /Conversa agora/ })).toHaveAttribute('data-state', 'active');
    expect(screen.getByTestId('support-location')).toHaveTextContent('/app/conversas?thread=thread-1');
  });

  it('opens from the header without changing the current route', async () => {
    renderShell();

    fireEvent.click(screen.getAllByRole('button', { name: 'Abrir Central de SAC' })[0]);

    expect(await screen.findByRole('dialog', { name: 'Atendimento Meu Executivo' })).toBeVisible();
    expect(screen.getByTestId('support-location')).toHaveTextContent('/app/conversas?thread=thread-1');
    await waitFor(() => expect(klmApi.supportTickets).toHaveBeenCalled());
  });

  it('opens from the profile item and closes the profile popover', async () => {
    renderShell();

    fireEvent.click(screen.getByRole('button', { name: 'Abrir perfil de Kleist Monteiro' }));
    expect(await screen.findByTestId('profile-menu')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Central de SAC' }));

    expect(await screen.findByRole('dialog', { name: 'Atendimento Meu Executivo' })).toBeVisible();
    expect(screen.queryByTestId('profile-menu')).not.toBeInTheDocument();
    expect(screen.getByTestId('support-location')).toHaveTextContent('/app/conversas?thread=thread-1');
  });

  it('opens a ticket from a secure deep link without losing the page context', async () => {
    renderShell('/app/conversas?thread=thread-1&support=ticket-1');

    expect(await screen.findByRole('dialog', { name: 'Atendimento Meu Executivo' })).toBeVisible();
    await waitFor(() => expect(klmApi.supportTicket).toHaveBeenCalledWith('ticket-1'));
    expect(await screen.findByText('Falha ao salvar')).toBeVisible();
    expect(screen.getByTestId('support-location')).toHaveTextContent(
      '/app/conversas?thread=thread-1&support=ticket-1',
    );
  });
});
