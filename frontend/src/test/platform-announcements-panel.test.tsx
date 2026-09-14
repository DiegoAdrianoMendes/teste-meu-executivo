/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PlatformAnnouncementsPanel } from '@/components/platform/PlatformAnnouncementsPanel';
import type {
  PlatformNotice,
  PlatformNoticeAuditEvent,
  PlatformNoticeReceipt,
  PlatformNoticeRecipientPreview,
} from '@/services/platform-notices';

const noticesApi = vi.hoisted(() => ({
  audit: vi.fn(),
  cancel: vi.fn(),
  create: vi.fn(),
  dispatchDue: vi.fn(),
  list: vi.fn(),
  preview: vi.fn(),
  publish: vi.fn(),
  receipts: vi.fn(),
  schedule: vi.fn(),
  update: vi.fn(),
}));

const toastApi = vi.hoisted(() => ({ toast: vi.fn() }));

vi.mock('@/services/platform-notices', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-notices')>('@/services/platform-notices');
  return {
    ...actual,
    cancelPlatformNotice: noticesApi.cancel,
    createPlatformNotice: noticesApi.create,
    dispatchDuePlatformNotices: noticesApi.dispatchDue,
    listPlatformNoticeAudit: noticesApi.audit,
    listPlatformNoticeReceipts: noticesApi.receipts,
    listPlatformNotices: noticesApi.list,
    previewPlatformNoticeRecipients: noticesApi.preview,
    publishPlatformNotice: noticesApi.publish,
    schedulePlatformNotice: noticesApi.schedule,
    updatePlatformNotice: noticesApi.update,
  };
});

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastApi.toast }),
}));

const noticeId = '11111111-1111-4111-8111-111111111111';
const recipientId = '22222222-2222-4222-8222-222222222222';

const draftNotice: PlatformNotice = {
  id: noticeId,
  title: 'Manutenção programada',
  body: 'A plataforma ficará indisponível por alguns minutos.',
  status: 'draft',
  audience: { kind: 'global', workspaceIds: [], userIds: [], rule: null },
  channels: ['in_app', 'email'],
  requiresAcknowledgement: false,
  emailDeliveryMode: null,
  scheduledAt: null,
  publishedAt: null,
  cancelledAt: null,
  createdByUserId: '33333333-3333-4333-8333-333333333333',
  updatedByUserId: '33333333-3333-4333-8333-333333333333',
  publishedByUserId: null,
  cancelledByUserId: null,
  version: 1,
  createdAt: '2026-07-22T12:00:00Z',
  updatedAt: '2026-07-22T12:00:00Z',
};

const realPreview: PlatformNoticeRecipientPreview = {
  noticeId,
  totalRecipients: 1,
  emailEligible: 1,
  emailBlockedMissingAddress: 0,
  sample: [{
    userId: recipientId,
    name: 'Ana Souza',
    emailHint: 'a***@exemplo.com',
    matchedBy: 'global',
    workspaceIds: [],
  }],
  evaluatedAt: '2026-07-22T12:05:00Z',
};

const localEmailReceipt: PlatformNoticeReceipt = {
  id: '44444444-4444-4444-8444-444444444444',
  noticeId,
  recipientUserId: recipientId,
  recipientName: 'Ana Souza',
  channel: 'email',
  state: 'email_queued_local',
  deliveryMode: 'local_queue_only',
  externalDeliveryAttempted: false,
  recipientAddressHint: 'a***@exemplo.com',
  matchedBy: 'global',
  scopeAllWorkspaces: true,
  workspaceIds: [],
  availableAt: null,
  queuedAt: '2026-07-22T12:10:00Z',
  readAt: null,
  acknowledgedAt: null,
  createdAt: '2026-07-22T12:10:00Z',
  updatedAt: '2026-07-22T12:10:00Z',
};

const createdAuditEvent: PlatformNoticeAuditEvent = {
  id: '55555555-5555-4555-8555-555555555555',
  noticeId,
  actorUserId: '33333333-3333-4333-8333-333333333333',
  actorUserName: 'Administrador da plataforma',
  action: 'created',
  details: { status: 'draft' },
  occurredAt: '2026-07-22T12:00:00Z',
};

function renderPanel(props?: React.ComponentProps<typeof PlatformAnnouncementsPanel>) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatformAnnouncementsPanel {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  noticesApi.list.mockResolvedValue({ items: [draftNotice], total: 1 });
  noticesApi.receipts.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, hasMore: false });
  noticesApi.audit.mockResolvedValue({ items: [createdAuditEvent], total: 1, limit: 25, offset: 0, hasMore: false });
  noticesApi.preview.mockResolvedValue(realPreview);
  noticesApi.create.mockResolvedValue(draftNotice);
  noticesApi.update.mockResolvedValue({ ...draftNotice, version: 2 });
  noticesApi.publish.mockResolvedValue({
    notice: {
      ...draftNotice,
      status: 'published',
      version: 2,
      emailDeliveryMode: 'local_queue_only',
      publishedAt: '2026-07-22T12:10:00Z',
    },
    recipientCount: 1,
    receiptCount: 2,
    receiptsByState: { in_app_available: 1, email_queued_local: 1 },
    emailDeliveryMode: 'local_queue_only',
  });
  noticesApi.schedule.mockResolvedValue({
    ...draftNotice,
    status: 'scheduled',
    version: 3,
    scheduledAt: '2027-08-01T15:00:00.000Z',
  });
  noticesApi.cancel.mockResolvedValue({
    ...draftNotice,
    status: 'cancelled',
    version: 4,
    cancelledAt: '2026-07-22T12:15:00Z',
  });
  noticesApi.dispatchDue.mockResolvedValue({ processedCount: 0, publications: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformAnnouncementsPanel', () => {
  it('explains the closed email APIs and exposes accessible field help for every technical audience mode', async () => {
    renderPanel();

    await screen.findByText('Editar rascunho');
    expect(screen.getByText('E-mail em fila local — APIs externas fechadas')).toBeInTheDocument();
    expect(screen.getByLabelText('Título')).toHaveValue('Manutenção programada');
    expect(screen.getByRole('checkbox', { name: 'E-mail — somente fila local' })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Ajuda sobre Público' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ajuda sobre Canais de entrega' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ajuda sobre Confirmação de leitura' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ajuda sobre Data e hora do envio' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ajuda sobre Prévia real' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Ajuda sobre Canais de entrega' }));
    const helpDialog = await screen.findByRole('dialog');
    expect(within(helpDialog).getByText(/O que é:/)).toBeInTheDocument();
    expect(within(helpDialog).getByText(/Onde encontrar:/)).toBeInTheDocument();
    expect(within(helpDialog).getByText(/nenhuma integração externa de e-mail/i)).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });

    const audienceSelect = screen.getByRole('combobox', { name: 'Tipo de público' });
    fireEvent.click(audienceSelect);
    fireEvent.click(await screen.findByRole('option', { name: 'Contas específicas' }));
    expect(screen.getByRole('button', { name: 'Selecionar contas destinatárias' })).toBeInTheDocument();
    expect(screen.queryByText(/IDs das contas/i)).not.toBeInTheDocument();

    fireEvent.click(audienceSelect);
    fireEvent.click(await screen.findByRole('option', { name: 'Usuários específicos' }));
    expect(screen.getByRole('button', { name: 'Selecionar usuários destinatários' })).toBeInTheDocument();
    expect(screen.queryByText(/IDs dos usuários/i)).not.toBeInTheDocument();

    fireEvent.click(audienceSelect);
    fireEvent.click(await screen.findByRole('option', { name: 'Regra por papel' }));
    expect(screen.getByRole('combobox', { name: 'Campo da regra de público' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ajuda sobre Campo da regra' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ajuda sobre Papéis incluídos' })).toBeInTheDocument();
  });

  it('uses a saved real preview as a gate before confirmed publication', async () => {
    renderPanel();

    await screen.findByText('Editar rascunho');
    expect(screen.getByRole('button', { name: 'Publicar agora' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia real' }));
    await waitFor(() => expect(noticesApi.preview).toHaveBeenCalledWith(noticeId, 50));
    expect(await screen.findByText('Ana Souza')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Amostra real de destinatários' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publicar agora' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Publicar agora' }));
    expect(await screen.findByText('Publicar para 1 destinatário(s)?')).toBeInTheDocument();
    expect(screen.getByText(/sem envio externo/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar publicação' }));

    await waitFor(() => expect(noticesApi.publish).toHaveBeenCalledWith(noticeId));
    expect(await screen.findByText(/Publicado para 1 destinatário\(s\), com 2 recibo\(s\) local\(is\)/)).toBeInTheDocument();
  });

  it('creates a draft, and updates an existing draft with the backend version', async () => {
    noticesApi.list.mockResolvedValueOnce({ items: [], total: 0 });
    const createdNotice = {
      ...draftNotice,
      title: 'Novo comunicado geral',
      body: 'Mensagem simples para todas as pessoas.',
    };
    noticesApi.create.mockResolvedValue(createdNotice);
    const { unmount } = renderPanel();

    await screen.findByText('Novo rascunho');
    fireEvent.change(screen.getByLabelText('Título'), { target: { value: createdNotice.title } });
    fireEvent.change(screen.getByLabelText('Mensagem'), { target: { value: createdNotice.body } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar rascunho' }));

    await waitFor(() => expect(noticesApi.create).toHaveBeenCalledWith({
      title: createdNotice.title,
      body: createdNotice.body,
      audience: { kind: 'global', workspaceIds: [], userIds: [], rule: null },
      channels: ['in_app'],
      requiresAcknowledgement: false,
    }));

    unmount();
    noticesApi.list.mockResolvedValue({ items: [draftNotice], total: 1 });
    noticesApi.update.mockResolvedValue({ ...draftNotice, title: 'Título revisado', version: 2 });
    renderPanel();
    await screen.findByText('Editar rascunho');
    fireEvent.change(screen.getByLabelText('Título'), { target: { value: 'Título revisado' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar alterações' }));

    await waitFor(() => expect(noticesApi.update).toHaveBeenCalledWith(noticeId, {
      expectedVersion: 1,
      title: 'Título revisado',
    }));
  });

  it('schedules from the real preview and cancels with confirmation', async () => {
    renderPanel();

    await screen.findByText('Editar rascunho');
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia real' }));
    await screen.findByText('Ana Souza');

    const future = new Date(Date.now() + 7 * 24 * 60 * 60 * 1_000);
    const localFuture = new Date(future.getTime() - future.getTimezoneOffset() * 60_000)
      .toISOString()
      .slice(0, 16);
    fireEvent.change(screen.getByLabelText('Data e hora do envio'), { target: { value: localFuture } });
    fireEvent.click(screen.getByRole('button', { name: 'Agendar' }));

    await waitFor(() => expect(noticesApi.schedule).toHaveBeenCalledWith(
      noticeId,
      new Date(localFuture).toISOString(),
    ));
    await screen.findByText(/Comunicado agendado para/);

    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(await screen.findByText('Cancelar este comunicado?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar cancelamento' }));
    await waitFor(() => expect(noticesApi.cancel).toHaveBeenCalledWith(noticeId));
    await screen.findByText('Comunicado cancelado e recibos locais marcados como cancelados.');
  });

  it('shows local email receipts and the preserved audit trail', async () => {
    noticesApi.list.mockResolvedValue({
      items: [{
        ...draftNotice,
        status: 'published',
        version: 2,
        emailDeliveryMode: 'local_queue_only',
        publishedAt: '2026-07-22T12:10:00Z',
      }],
      total: 1,
    });
    noticesApi.receipts.mockResolvedValue({ items: [localEmailReceipt], total: 1, limit: 25, offset: 0, hasMore: false });
    renderPanel();

    await screen.findByText('Consultar comunicado');
    await waitFor(() => expect(noticesApi.receipts).toHaveBeenCalledWith(noticeId, 0, 25));

    const receiptsTab = screen.getByRole('tab', { name: 'Recibos' });
    fireEvent.mouseDown(receiptsTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(receiptsTab).toHaveAttribute('data-state', 'active'));
    await screen.findByText('1 de 1 recibo(s) exibido(s).');
    const receiptsTable = await screen.findByRole('table', { name: 'Recibos do comunicado' });
    expect(within(receiptsTable).getByText('Na fila local de e-mail')).toBeInTheDocument();
    expect(within(receiptsTable).getByText('E-mail (fila local)')).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Auditoria' }), { button: 0, ctrlKey: false });
    expect(await screen.findByText('Rascunho criado')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Eventos de auditoria do comunicado' })).toBeInTheDocument();
  });

  it('fails closed when the current operator lacks notice-management permission', () => {
    renderPanel({ canManage: false });

    expect(screen.getByText('Acesso restrito')).toBeInTheDocument();
    expect(screen.getByText(/permissão individual de gerenciamento/i)).toBeInTheDocument();
    expect(noticesApi.list).not.toHaveBeenCalled();
  });
});
