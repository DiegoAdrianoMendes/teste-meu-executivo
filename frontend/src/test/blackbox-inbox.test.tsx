/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BlackboxInbox from '@/pages/BlackboxInbox';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
const toast = vi.hoisted(() => vi.fn());
const access = vi.hoisted(() => ({ write: true }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast }) }));
vi.mock('@/lib/platform-access', () => ({ canPlatform: () => access.write }));
vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ state: { currentUser: { id: 'platform-root' } } }),
}));

const conversation = {
  id: '11111111-1111-4111-8111-111111111111', channel: 'whatsapp',
  contactName: 'Ana Prospect', contactAddress: '+5561999990000', subject: 'Planos',
  queue: 'sales', status: 'open', assignedToUserId: null, prospectId: null,
  supportTicketId: null, workspaceId: null, lastMessagePreview: 'Quero conhecer os planos.',
  lastMessageAt: '2026-07-27T20:00:00Z', unreadCount: 1,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><BlackboxInbox /></QueryClientProvider>);
}

describe('BlackboxInbox', () => {
  beforeEach(() => {
    access.write = true;
    http.apiFetch.mockReset().mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/v1/platform/inbox/metrics') return Promise.resolve({ total: 1, open: 1, unread: 1 });
      if (url === '/api/v1/platform/inbox/channels') return Promise.resolve([
        { channel: 'whatsapp', state: 'not_configured', inbound: false, outbound: false, detail: 'Nenhum número oficial conectado.' },
        { channel: 'webchat', state: 'available', inbound: true, outbound: true, detail: 'Sessões públicas ativas.' },
      ]);
      if (url.startsWith('/api/v1/platform/inbox/conversations?')) return Promise.resolve({ items: [conversation], count: 1, nextCursor: 'next-page', hasMore: true });
      if (url === `/api/v1/platform/inbox/conversations/${conversation.id}` && !init) return Promise.resolve({
        ...conversation, unreadCount: 0,
        messages: [{ id: 'm1', direction: 'inbound', body: 'Quero conhecer os planos.', deliveryStatus: 'received', actorLabel: 'Ana Prospect', occurredAt: '2026-07-27T20:00:00Z' }],
      });
      if (url.endsWith('/messages')) return Promise.resolve({ id: 'm2', direction: 'outbound', body: 'Vamos conversar.', deliveryStatus: 'not_configured', actorLabel: 'SDR', occurredAt: '2026-07-27T20:10:00Z' });
      return Promise.resolve(conversation);
    });
  });
  afterEach(() => cleanup());

  it('opera a fila e deixa explícita a ausência de transporte externo', async () => {
    renderPage();
    expect(await screen.findByText('Ana Prospect')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Ana Prospect'));
    const composer = await screen.findByLabelText('Mensagem da conversa');
    expect(screen.getAllByText('Quero conhecer os planos.').length).toBeGreaterThan(0);
    fireEvent.change(composer, { target: { value: 'Vamos conversar.' } });
    fireEvent.click(screen.getByLabelText('Enviar mensagem'));
    await waitFor(() => expect(http.apiFetch).toHaveBeenCalledWith(
      `/api/v1/platform/inbox/conversations/${conversation.id}/messages`,
      expect.objectContaining({ method: 'POST' }),
    ));
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({
      description: expect.stringContaining('não está configurado'),
    }));
  });

  it('registra uma conversa recebida sem fixture de produção', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Registrar conversa' }));
    fireEvent.change(screen.getByLabelText('Contato'), { target: { value: 'Novo contato' } });
    fireEvent.change(screen.getByLabelText('Mensagem recebida'), { target: { value: 'Preciso de ajuda com a conta.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar' }));
    await waitFor(() => expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/inbox/conversations',
      expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('navega pela paginação cursor sem chamar coleção ilimitada', async () => {
    renderPage();
    await screen.findByText('Ana Prospect');
    expect(http.apiFetch).toHaveBeenCalledWith(expect.stringContaining('limit=50'));
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));
    await waitFor(() => expect(http.apiFetch).toHaveBeenCalledWith(expect.stringContaining('cursor=next-page')));
    fireEvent.click(screen.getByRole('button', { name: 'Anterior' }));
    await waitFor(() => expect(http.apiFetch).toHaveBeenCalledWith(expect.not.stringContaining('cursor=')));
  });

  it('não apresenta mutações a um perfil com acesso somente de leitura', async () => {
    access.write = false;
    renderPage();
    expect(await screen.findByText('Ana Prospect')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Registrar conversa' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Ana Prospect'));
    await screen.findByRole('heading', { name: 'Ana Prospect' });
    expect(screen.queryByRole('button', { name: 'Responsável da conversa' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Mensagem da conversa')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Criar lead no CRM' })).not.toBeInTheDocument();
  });
});
