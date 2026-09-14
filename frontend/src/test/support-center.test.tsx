/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import {
  klmApi,
  type KlmSupportTicket,
  type KlmSupportTicketDetail,
} from '@/klm/api';
import { SupportCenter } from '@/klm/components/SupportCenter';
import {
  countQueuedSupportOperations,
  flushQueuedSupportOperations,
  submitSupportMessage,
  submitSupportTicket,
} from '@/klm/support-offline-queue';
import { getSupportEscalationStatus } from '@/services/platform-support-settings';
import * as appStore from '@/store/app-context';

vi.mock('@/klm/support-offline-queue', () => ({
  countQueuedSupportOperations: vi.fn(),
  flushQueuedSupportOperations: vi.fn(),
  submitSupportMessage: vi.fn(),
  submitSupportTicket: vi.fn(),
}));

vi.mock('@/services/platform-support-settings', () => ({
  getSupportEscalationStatus: vi.fn(),
}));

const ticket: KlmSupportTicket = {
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
};

const detail: KlmSupportTicketDetail = {
  ...ticket,
  messages: [],
  attachments: [],
  events: [],
  collaboratorIds: [],
};

function renderCenter(route = '/app/conversas', initialTicketId?: string | null) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SupportCenter
        open
        onOpenChange={() => {}}
        currentRoute={route}
        initialTicketId={initialTicketId}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(appStore, 'useAppStore').mockReturnValue({
    can: () => true,
    state: {
      currentUser: { id: 'user-1', name: 'Usuário de teste', email: 'teste@local.klm' },
      session: { activeWorkspace: { id: 'workspace-1' } },
    },
  } as unknown as ReturnType<typeof appStore.useAppStore>);
  vi.mocked(countQueuedSupportOperations).mockResolvedValue(0);
  vi.mocked(flushQueuedSupportOperations).mockResolvedValue({
    confirmedTicketIds: [],
    confirmedMessageIds: [],
    remaining: 0,
  });
  vi.mocked(submitSupportMessage).mockResolvedValue({
    state: 'confirmed',
    pendingAttachments: 0,
    failedAttachments: 0,
  });
  vi.spyOn(klmApi, 'supportTickets').mockResolvedValue({ items: [], nextCursor: null });
  vi.spyOn(klmApi, 'supportTicket').mockResolvedValue(detail);
  vi.mocked(getSupportEscalationStatus).mockResolvedValue({
    availableNow: false,
    schedulingUrl: null,
    whatsappLink: null,
    nextWindow: null,
  });
});

afterEach(() => {
  cleanup();
  delete (document as Document & { featurePolicy?: unknown }).featurePolicy;
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('SupportCenter', () => {
  it('opens with AI chat first and keeps ticket and creation views available', async () => {
    renderCenter();

    expect(screen.getByRole('heading', { name: 'Central de SAC' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Conversa agora/ })).toHaveAttribute('data-state', 'active');
    expect(screen.getByText(/Sou a IA de atendimento/)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Meus chamados/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Novo chamado/ })).toBeInTheDocument();
    await waitFor(() => expect(klmApi.supportTickets).toHaveBeenCalled());
  });

  it('moves to human support only after an explicit request', async () => {
    renderCenter();

    fireEvent.change(screen.getByLabelText('Mensagem para a IA de atendimento'), { target: { value: 'Quero falar com um humano sobre este erro.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar mensagem para a IA' }));

    expect(await screen.findByLabelText('Assunto')).toHaveValue('Atendimento humano solicitado');
    expect(screen.getByLabelText('Descrição')).toHaveValue('Quero falar com um humano sobre este erro.');
  });

  it('submits a sanitized payload and waits for the backend confirmation result', async () => {
    vi.mocked(submitSupportTicket).mockResolvedValue({
      state: 'confirmed',
      record: ticket,
      pendingAttachments: 0,
      failedAttachments: 0,
    });
    renderCenter('/app/conversas?contact=Maria&token=secret');

    await waitFor(() => expect(screen.getByRole('tab', { name: /Conversa agora/ })).toHaveAttribute('data-state', 'active'));
    fireEvent.click(screen.getByRole('button', { name: /Pedir humano/ }));
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Sugestão' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('radio', { name: 'Sugestão' }));
    fireEvent.change(screen.getByLabelText('Assunto'), {
      target: { value: 'Melhorar filtros' },
    });
    fireEvent.change(screen.getByLabelText('Descrição'), {
      target: { value: 'Adicionar um filtro persistente para a equipe.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar ao SAC' }));

    await waitFor(() => expect(submitSupportTicket).toHaveBeenCalledTimes(1));
    const [scope, payload] = vi.mocked(submitSupportTicket).mock.calls[0];
    expect(scope).toEqual({ workspaceId: 'workspace-1', userId: 'user-1' });
    expect(payload.ticketType).toBe('suggestion');
    expect(payload.technicalContext.page).toBe('/app/conversas');
    expect(JSON.stringify(payload.technicalContext)).not.toContain('Maria');
    expect(JSON.stringify(payload.technicalContext)).not.toContain('secret');
    expect(await screen.findByText(/confirmado pelo SAC/)).toBeInTheDocument();
  });

  it('reports when the deployment policy blocks automatic screen capture', async () => {
    Object.defineProperty(document, 'featurePolicy', {
      configurable: true,
      value: { allowsFeature: () => false },
    });
    renderCenter();

    await waitFor(() => expect(screen.getByRole('tab', { name: /Conversa agora/ })).toHaveAttribute('data-state', 'active'));
    fireEvent.click(screen.getByRole('button', { name: /Pedir humano/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Captura' }));

    expect(await screen.findByText(/política de segurança deste ambiente bloqueia/)).toBeInTheDocument();
  });

  it('shows "Falar com um humano" for an unresolved ticket when escalation is configured', async () => {
    vi.mocked(getSupportEscalationStatus).mockResolvedValue({
      availableNow: true,
      schedulingUrl: 'https://cal.com/mv-advocacia/consulta',
      whatsappLink: 'https://wa.me/5561999990000',
      nextWindow: null,
    });
    renderCenter('/app/conversas', ticket.id);

    expect(await screen.findByRole('heading', { name: 'Falar com um humano' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Agendar horário/ })).toHaveAttribute(
      'href',
      'https://cal.com/mv-advocacia/consulta',
    );
    expect(screen.getByRole('link', { name: /WhatsApp/ })).toHaveAttribute(
      'href',
      'https://wa.me/5561999990000',
    );
  });

  it('hides "Falar com um humano" by default and only reveals it after "Isso não resolveu" on a resolved ticket', async () => {
    vi.mocked(getSupportEscalationStatus).mockResolvedValue({
      availableNow: true,
      schedulingUrl: 'https://cal.com/mv-advocacia/consulta',
      whatsappLink: null,
      nextWindow: null,
    });
    vi.spyOn(klmApi, 'supportTicket').mockResolvedValue({ ...detail, status: 'resolved' });
    renderCenter('/app/conversas', ticket.id);

    await screen.findByText('SAC-2026-0001');
    expect(screen.queryByRole('heading', { name: 'Falar com um humano' })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: /Isso não resolveu/ }));

    expect(await screen.findByRole('heading', { name: 'Falar com um humano' })).toBeInTheDocument();
  });

  it('hides "Falar com um humano" entirely when escalation is disabled on the platform', async () => {
    renderCenter('/app/conversas', ticket.id);

    await screen.findByText('SAC-2026-0001');
    await waitFor(() => expect(getSupportEscalationStatus).toHaveBeenCalled());
    expect(screen.queryByRole('heading', { name: 'Falar com um humano' })).not.toBeInTheDocument();
  });
});
