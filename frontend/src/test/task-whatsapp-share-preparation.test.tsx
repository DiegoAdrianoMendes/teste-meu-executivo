import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TaskWhatsAppSharePreparation } from '@/klm/components/TaskWhatsAppSharePreparation';
import * as api from '@/services/task-whatsapp-share';

vi.mock('@/services/task-whatsapp-share');

describe('TaskWhatsAppSharePreparation', () => {
  it('mostra gates e persiste somente após prévia e confirmação humana', async () => {
    const contact = {
      contactRef: 'opaque-contact-reference', label: 'Ana Cliente', maskedAddress: 'WhatsApp terminado em 1234',
      consentState: 'unknown', serviceWindowState: 'closed' as const, serviceWindowExpiresAt: null,
      sendEligible: false, gateReasons: ['consentimento_nao_comprovado', 'fora_da_janela_de_24_horas'],
    };
    vi.mocked(api.listTaskWhatsAppContacts).mockResolvedValue([contact]);
    vi.mocked(api.previewTaskWhatsAppDraft).mockResolvedValue({
      contact, messageBody: 'Mensagem editada https://app.example/task-share#token=secret',
      previewFingerprint: 'a'.repeat(64), deliveryMode: 'conversations_local_draft',
      externalActionsExecuted: 0, humanConfirmationRequired: true,
    });
    vi.mocked(api.prepareTaskWhatsAppDraft).mockResolvedValue({
      contact, messageBody: 'Mensagem editada https://app.example/task-share#token=secret',
      deliveryStatus: 'local_draft', deliveryMode: 'conversations_local_draft', externalActionsExecuted: 0,
      conversationPath: '/app/conversas', replayed: false,
    });
    render(<QueryClientProvider client={new QueryClient()}><TaskWhatsAppSharePreparation taskId="opaque-task" shareId="internal-share" shareUrl="https://app.example/task-share#token=secret" /></QueryClientProvider>);

    expect(await screen.findByText(/Ana Cliente/)).toBeInTheDocument();
    expect(screen.queryByText('opaque-contact-reference')).not.toBeInTheDocument();
    expect(api.prepareTaskWhatsAppDraft).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Texto editável'), { target: { value: 'Mensagem editada https://app.example/task-share#token=secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validar e revisar rascunho' }));
    expect(await screen.findByText(/gate atual exige revisão/i)).toBeInTheDocument();
    expect(screen.getByText(/Janela de 24h: fechada/i)).toBeInTheDocument();
    expect(api.prepareTaskWhatsAppDraft).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar e preparar em Conversas' }));
    await waitFor(() => expect(api.prepareTaskWhatsAppDraft).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Rascunho preparado, sem envio/i)).toBeInTheDocument();
    expect(screen.getByText(/Ações externas executadas: 0/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Abrir Conversas/ })).toHaveAttribute('href', '/app/conversas');
  });
});
