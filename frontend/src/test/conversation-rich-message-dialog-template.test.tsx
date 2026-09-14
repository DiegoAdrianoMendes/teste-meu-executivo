/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConversationRichMessageDialog } from '@/klm/components/ConversationRichMessageDialog';

const campaignMocks = vi.hoisted(() => ({ fetchTemplatePicker: vi.fn() }));

vi.mock('@/services/campaigns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/campaigns')>()),
  fetchTemplatePicker: campaignMocks.fetchTemplatePicker,
}));

const route = {
  channel: 'whatsapp',
  connectionKey: 'whatsapp:7c1b2e2a-1111-4c3d-8e9f-000000000001',
  connectionName: 'Test Number',
  transportMode: 'cloud_api',
  capabilities: { templates: true, outbound_messages: true, media: true },
} as never;

const routeCommand = {
  endpointId: 'endpoint-cloud',
  bindingId: 'binding-cloud',
  connectionId: 'connection-cloud',
  connectionKey: 'whatsapp:7c1b2e2a-1111-4c3d-8e9f-000000000001',
  transport: 'cloud_api',
} as never;

const approvedCards = [
  { id: 'tpl-1', name: 'hello_world', language: 'en_US', status: 'approved', bodyText: 'Hello', buttons: [], variables: [], intent: null },
  { id: 'tpl-2', name: 'verificacao_fluxo_09', language: 'pt_BR', status: 'APPROVED', bodyText: 'Olá', buttons: [], variables: [], intent: null },
];

function renderDialog() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConversationRichMessageDialog
        open
        onOpenChange={vi.fn()}
        threadId="thread-1"
        matterId={null}
        route={route}
        routeCommand={routeCommand}
        initialBody="Teste de envio"
        authorLabel="Kleist"
        canEdit
        externalGateOpen
        onCreated={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

async function openTemplateTab() {
  const dialog = await screen.findByRole('dialog', { name: 'Conteúdo avançado da mensagem' });
  const tab = within(dialog).getByRole('tab', { name: 'Modelo oficial' });
  fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
  await waitFor(() => expect(tab).toHaveAttribute('data-state', 'active'));
  return dialog;
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe('ConversationRichMessageDialog: modelo oficial com a lista aprovada (V27)', () => {
  it('lista os modelos aprovados e a escolha preenche nome e idioma', async () => {
    campaignMocks.fetchTemplatePicker.mockResolvedValue({ cards: approvedCards });
    renderDialog();
    const dialog = await openTemplateTab();

    await waitFor(() => expect(campaignMocks.fetchTemplatePicker).toHaveBeenCalledWith({ threadId: 'thread-1', matterId: null }));
    const trigger = within(dialog).getByRole('combobox', { name: 'Modelo aprovado' });
    await waitFor(() => expect(trigger).toBeEnabled());
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    const option = await screen.findByRole('option', { name: 'hello_world · en_US' });
    fireEvent.keyDown(option, { key: 'Enter' });

    await waitFor(() => expect(within(dialog).getByLabelText('Nome exato do modelo aprovado')).toHaveValue('hello_world'));
    expect(within(dialog).getByRole('combobox', { name: 'Idioma do modelo' })).toHaveTextContent('Inglês (EUA)');
    expect(within(dialog).getByLabelText('Nome exato do modelo aprovado')).toHaveAttribute('placeholder', 'nome_exato_do_modelo');
  });

  it('diz que não há modelo aprovado sem esconder o campo de texto', async () => {
    campaignMocks.fetchTemplatePicker.mockResolvedValue({ cards: [] });
    renderDialog();
    const dialog = await openTemplateTab();

    expect(await within(dialog).findByText(/nenhum modelo aprovado disponível; digite o nome exato/i)).toBeInTheDocument();
    expect(within(dialog).getByRole('combobox', { name: 'Modelo aprovado' })).toBeDisabled();
    expect(within(dialog).getByLabelText('Nome exato do modelo aprovado')).toBeEnabled();
  });

  it('falha da consulta vira erro com retry, nunca lista vazia', async () => {
    campaignMocks.fetchTemplatePicker.mockRejectedValueOnce(new Error('picker indisponível')).mockResolvedValue({ cards: approvedCards });
    renderDialog();
    const dialog = await openTemplateTab();

    const error = await within(dialog).findByRole('status', { name: 'Erro ao carregar modelos aprovados' });
    expect(error).toHaveTextContent('Não foi possível carregar os modelos aprovados.');
    expect(within(dialog).queryByText(/nenhum modelo aprovado/i)).not.toBeInTheDocument();

    fireEvent.click(within(error).getByRole('button', { name: 'Tentar novamente' }));
    await waitFor(() => expect(campaignMocks.fetchTemplatePicker).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(within(dialog).getByRole('combobox', { name: 'Modelo aprovado' })).toBeEnabled());
  });
});
