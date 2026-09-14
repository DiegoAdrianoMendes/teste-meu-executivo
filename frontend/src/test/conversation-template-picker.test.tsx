/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConversationTemplatePicker } from '@/klm/components/ConversationTemplatePicker';

const mocks = vi.hoisted(() => ({
  fetchTemplatePicker: vi.fn(),
  fetchTemplateVariables: vi.fn(),
  quickSendWhatsAppTemplate: vi.fn(),
  startWhatsAppSequence: vi.fn(),
}));
vi.mock('@/services/campaigns', () => mocks);

const card = {
  id: 'tpl-1', name: 'aviso_audiencia', language: 'pt_BR', intent: 'audiencia', sector: 'juridico', metaCategory: 'UTILITY', status: 'APPROVED', bodyText: 'Olá {{1}}, sua audiência é {{2}}.', footerText: null,
  header: { type: 'NONE' }, buttons: [{ type: 'QUICK_REPLY', label: 'Quero falar' }], variables: [{ position: 1, scope: 'body', example: 'Maria' }, { position: 2, scope: 'body', example: '03/09 às 14h' }], usageCount30d: 4,
};

function renderPicker() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={vi.fn()} threadId="thread-1" contactId="contact-1" onSent={vi.fn()} /></QueryClientProvider>);
}

describe('ConversationTemplatePicker', () => {
  it('mostra apenas aprovados, busca pelo corpo e oculta faixa sem sugestões', async () => {
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [card, { ...card, id: 'pending', name: 'pendente', status: 'PENDING' }], suggestions: [], refreshedAt: '2026-08-19T12:00:00Z' });
    renderPicker();
    expect(await screen.findByText('aviso_audiencia')).toBeInTheDocument();
    expect(screen.queryByText('pendente')).not.toBeInTheDocument();
    expect(screen.queryByText('Sugerido para este caso')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Buscar modelo aprovado' }), { target: { value: 'audiência' } });
    expect(screen.getByText('aviso_audiencia')).toBeInTheDocument();
  });

  it('abre a prévia com bindings editáveis e registra estado pós-envio', async () => {
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [card], suggestions: [{ templateId: 'tpl-1', reasonCode: 'hearing_upcoming', reasonText: 'audiência nos próximos 7 dias', rank: 1 }] });
    mocks.fetchTemplateVariables.mockResolvedValue({ groups: [] });
    mocks.quickSendWhatsAppTemplate.mockResolvedValue({ messageId: 'msg-1', status: 'queued' });
    renderPicker();
    fireEvent.click((await screen.findAllByText('aviso_audiencia'))[0]);
    expect(await screen.findByText('Prévia do modelo aprovado')).toBeInTheDocument();
    const fields = screen.getAllByRole('textbox', { name: 'Variável 1' });
    fireEvent.change(fields[0], { target: { value: 'Kleist' } });
    fireEvent.click(screen.getByRole('button', { name: /enviar modelo/i }));
    await waitFor(() => expect(mocks.quickSendWhatsAppTemplate).toHaveBeenCalledWith(expect.objectContaining({
      templateId: 'tpl-1',
      threadId: 'thread-1',
      idempotencyKey: expect.any(String),
      bodyParameters: ['Kleist', '03/09 às 14h'],
      buttonParameters: [],
    })));
  });

  it('abre sequência em subtela, mantém contexto e oferece iniciar pelo contrato do host', async () => {
    const sequenceCard = {
      ...card,
      id: 'tpl-sequence',
      name: 'cadencia_audiencia',
      isSequence: true,
      sequenceStepCount: 2,
      variables: [{ position: 1, scope: 'body' as const, example: 'Maria', binding: { kind: 'field' as const, fieldKey: 'contact.first_name' } }],
    };
    const onStartSequence = vi.fn();
    const onCreateTemplate = vi.fn();
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [sequenceCard], suggestions: [] });
    mocks.fetchTemplateVariables.mockResolvedValue({ groups: [{ key: 'contact', label: 'Contato', values: [{ fieldKey: 'contact.first_name', label: 'Nome', resolvedValue: 'Ana' }] }] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={vi.fn()} threadId="thread-1" matterId="matter-1" contactId="contact-1" onStartSequence={onStartSequence} onCreateTemplate={onCreateTemplate} /></QueryClientProvider>);

    fireEvent.click(await screen.findByText('cadencia_audiencia'));
    expect(await screen.findByTestId('sequence-builder')).toBeInTheDocument();
    expect(screen.getByText('Configurar sequência')).toBeInTheDocument();
    expect(screen.getByLabelText('Prazo da mensagem 2')).toHaveValue(24);
    await waitFor(() => expect(screen.getAllByLabelText('Valor resolvido para contact.first_name').every((field) => (field as HTMLInputElement).value === 'Ana')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Criar novo template' }));
    expect(onCreateTemplate).toHaveBeenCalledWith();
    const editorHref = screen.getByRole('link', { name: /Editar sequência/ }).getAttribute('href') ?? '';
    expect(editorHref).toContain('thread=thread-1');
    expect(editorHref).toContain('sequence_draft=');
    expect(editorHref).not.toContain('Ana');
    expect(editorHref).not.toContain('contact.first_name');
    fireEvent.click(screen.getByRole('switch', { name: 'Interromper sequência ao responder' }));
    await waitFor(() => {
      const stored = Object.entries(window.sessionStorage).find(([key]) => key.startsWith('klm-sequence-draft:'))?.[1];
      expect(stored ? JSON.parse(stored).interruptOnReply : undefined).toBe(false);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    expect(onStartSequence).toHaveBeenCalledWith(expect.objectContaining({
      card: expect.objectContaining({ id: 'tpl-sequence' }),
      threadId: 'thread-1',
      matterId: 'matter-1',
      contactId: 'contact-1',
      interruptOnReply: false,
      messages: expect.arrayContaining([expect.objectContaining({ bindings: [{ fieldKey: 'contact.first_name', value: 'Ana' }] })]),
    }));
  });

  it('permite criar sequência a partir de template aprovado sem isSequence no cartão', async () => {
    const followUp = { ...card, id: 'tpl-follow-up', name: 'follow_up_sem_metadado_de_sequencia' };
    const second = { ...card, id: 'tpl-second', name: 'segundo_passo' };
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [followUp, second], suggestions: [] });
    mocks.fetchTemplateVariables.mockResolvedValue({ groups: [] });
    mocks.startWhatsAppSequence.mockResolvedValue({ campaignId: 'camp-1', status: 'queued' });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={vi.fn()} threadId="thread-1" contactId="contact-1" /></QueryClientProvider>);

    fireEvent.click((await screen.findAllByRole('button', { name: 'Criar sequência' }))[0]);
    expect(await screen.findByTestId('sequence-builder')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /segundo_passo/ })).toBeInTheDocument();
    expect(screen.getByText('Sequência de mensagens')).toBeInTheDocument();
  });

  it('passa o id somente ao editar um modelo selecionado', async () => {
    const onCreateTemplate = vi.fn();
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [card], suggestions: [] });
    mocks.fetchTemplateVariables.mockResolvedValue({ groups: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={vi.fn()} threadId="thread-1" onCreateTemplate={onCreateTemplate} /></QueryClientProvider>);

    fireEvent.click((await screen.findAllByText('aviso_audiencia'))[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Editar modelo' }));
    expect(onCreateTemplate).toHaveBeenCalledWith('tpl-1');
  });

  it('inicia pelo endpoint real e publica o primeiro corpo com bindings resolvidos', async () => {
    const sequenceCard = {
      ...card,
      id: 'tpl-real-sequence',
      name: 'cadencia_real',
      bodyText: 'Olá {{1}}, sua audiência é {{2}}.',
      variables: [
        { position: 1, scope: 'body' as const, example: 'Maria', binding: { kind: 'field' as const, fieldKey: 'contact.first_name' } },
        { position: 2, scope: 'body' as const, example: '03/09 às 14h', binding: { kind: 'field' as const, fieldKey: 'matter.hearing_at' } },
      ],
    };
    const onSent = vi.fn();
    const onOpenChange = vi.fn();
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [sequenceCard], suggestions: [] });
    mocks.fetchTemplateVariables.mockResolvedValue({ groups: [
      { key: 'contact', label: 'Contato', values: [{ fieldKey: 'contact.first_name', label: 'Nome', resolvedValue: 'Ana' }] },
      { key: 'matter', label: 'Caso', values: [{ fieldKey: 'matter.hearing_at', label: 'Audiência', resolvedValue: '03/09 às 14h' }] },
    ] });
    mocks.startWhatsAppSequence.mockResolvedValue({ campaignId: 'camp-sequence', firstRecipientId: 'recipient-1', dispatchState: 'accepted', status: 'queued' });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={onOpenChange} threadId="thread-1" matterId="matter-1" contactId="contact-1" onSent={onSent} /></QueryClientProvider>);

    fireEvent.click((await screen.findAllByText('cadencia_real'))[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Criar sequência' }));
    await waitFor(() => expect(screen.getAllByLabelText('Valor resolvido para contact.first_name').every((field) => (field as HTMLInputElement).value === 'Ana')).toBe(true));
    await waitFor(() => expect(screen.getAllByLabelText('Valor resolvido para matter.hearing_at').every((field) => (field as HTMLInputElement).value === '03/09 às 14h')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    await waitFor(() => expect(mocks.startWhatsAppSequence).toHaveBeenCalledWith(expect.objectContaining({
      threadId: 'thread-1',
      matterId: 'matter-1',
      stages: expect.arrayContaining([expect.objectContaining({
        bindings: [
          { fieldKey: 'contact.first_name', value: 'Ana' },
          { fieldKey: 'matter.hearing_at', value: '03/09 às 14h' },
        ],
      })]),
    })));
    await waitFor(() => expect(onSent).toHaveBeenCalledWith(expect.objectContaining({ body: 'Olá Ana, sua audiência é 03/09 às 14h.' })));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('mantém o editor aberto quando o start não retorna o primeiro destinatário', async () => {
    const onSent = vi.fn();
    const onOpenChange = vi.fn();
    const sequenceCard = { ...card, id: 'tpl-missing-recipient', name: 'cadencia_sem_destinatario', variables: [] };
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [sequenceCard], suggestions: [] });
    mocks.fetchTemplateVariables.mockResolvedValue({ groups: [] });
    mocks.startWhatsAppSequence.mockResolvedValue({ campaignId: 'camp-without-recipient', firstRecipientId: '', dispatchState: 'accepted', status: 'queued' });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={onOpenChange} threadId="thread-1" onSent={onSent} /></QueryClientProvider>);

    fireEvent.click((await screen.findAllByText('cadencia_sem_destinatario'))[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Criar sequência' }));
    expect(await screen.findByTestId('sequence-builder')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('WA-SEQUENCE-RESPONSE-MISSING-FIRST-RECIPIENT'));
    expect(onSent).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByTestId('sequence-builder')).toBeInTheDocument();
  });

  it.each([
    ['failed', 'WA-SEQUENCE-DISPATCH-FAILED'],
    ['uncertain', 'WA-SEQUENCE-DISPATCH-UNCERTAIN'],
  ] as const)('mantém o diálogo para dispatchState %s com código explícito', async (dispatchState, expectedCode) => {
    const onSent = vi.fn();
    const onOpenChange = vi.fn();
    const sequenceCard = { ...card, id: `tpl-${dispatchState}`, name: `cadencia_${dispatchState}`, variables: [] };
    mocks.fetchTemplatePicker.mockResolvedValue({ cards: [sequenceCard], suggestions: [] });
    mocks.fetchTemplateVariables.mockResolvedValue({ groups: [] });
    mocks.startWhatsAppSequence.mockResolvedValue({ campaignId: `camp-${dispatchState}`, firstRecipientId: 'recipient-1', dispatchState, code: expectedCode, status: 'queued' });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={onOpenChange} threadId="thread-1" onSent={onSent} /></QueryClientProvider>);

    fireEvent.click((await screen.findAllByText(`cadencia_${dispatchState}`))[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Criar sequência' }));
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(expectedCode));
    expect(onSent).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByTestId('sequence-builder')).toBeInTheDocument();
  });

  it('ordena por setor e deixa os demais atrás de Ver todos, sem agrupar no modo condensado', async () => {
    const juridico = { ...card, id: 'juridico', name: 'aviso_juridico', sector: 'juridico' as const };
    const comercial = { ...card, id: 'comercial', name: 'aviso_comercial', sector: 'comercial' as const };
    mocks.fetchTemplatePicker.mockResolvedValueOnce({ cards: [juridico, comercial], suggestions: [], sectorMode: 'by_sector', userSector: 'juridico' });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ConversationTemplatePicker open onOpenChange={vi.fn()} threadId="thread-1" /></QueryClientProvider>);
    expect(await screen.findByText('aviso_juridico')).toBeInTheDocument();
    expect(screen.queryByText('aviso_comercial')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ver todos' }));
    expect(screen.getByText('aviso_comercial')).toBeInTheDocument();

    cleanup();
    mocks.fetchTemplatePicker.mockResolvedValueOnce({ cards: [juridico, comercial], suggestions: [], sectorMode: 'condensed', userSector: 'juridico' });
    const condensedClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={condensedClient}><ConversationTemplatePicker open onOpenChange={vi.fn()} threadId="thread-1" /></QueryClientProvider>);
    expect(await screen.findByText('aviso_comercial')).toBeInTheDocument();
  });
});
