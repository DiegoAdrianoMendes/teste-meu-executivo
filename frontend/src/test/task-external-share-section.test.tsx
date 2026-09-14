import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TaskExternalShareSection } from '@/klm/components/TaskExternalShareSection';
import * as api from '@/services/task-external-shares';

vi.mock('@/services/task-external-shares');
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

function renderSection(canEdit = true, canPrepareWhatsApp = false) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TaskExternalShareSection taskId="opaque-task" canEdit={canEdit} canPrepareWhatsApp={canPrepareWhatsApp} /></QueryClientProvider>);
}

describe('TaskExternalShareSection', () => {
  beforeEach(() => {
    vi.mocked(api.listTaskExternalShares).mockResolvedValue([]);
    vi.mocked(api.previewTaskExternalShare).mockResolvedValue({
      title: 'Prazo de contestação', status: 'pending', dueAt: null,
      safeContext: 'Comparecer ao fórum.', expiresAt: new Date(Date.now() + 86400_000).toISOString(),
      previewFingerprint: 'a'.repeat(64),
      excludedContent: ['comentários', 'histórico', 'participantes', 'anexos'],
    });
    vi.mocked(api.createTaskExternalShare).mockResolvedValue({
      id: 'internal-share-id',
      url: 'https://app.example/task-share#token=opaque-token',
      expiresAt: new Date(Date.now() + 86400_000).toISOString(),
      revokedAt: null,
      createdAt: new Date().toISOString(),
    });
  });

  it('exibe apenas no fluxo jurídico uma prévia explícita antes de criar', async () => {
    renderSection();
    expect(screen.getByText(/somente leitura/i)).toBeInTheDocument();
    expect(screen.queryByText(/WhatsApp/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Contexto seguro opcional'), { target: { value: 'Comparecer ao fórum.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar prévia' }));
    expect(await screen.findByText('Confirme exatamente o que será compartilhado')).toBeInTheDocument();
    expect(screen.getByText('Prazo de contestação')).toBeInTheDocument();
    expect(screen.getByText(/Excluído: comentários, histórico, participantes, anexos/)).toBeInTheDocument();
    expect(api.createTaskExternalShare).not.toHaveBeenCalled();
  });

  it('não mostra identificadores e permite revogar por rótulo humano', async () => {
    vi.mocked(api.listTaskExternalShares).mockResolvedValue([{ id: 'uuid-secret', expiresAt: new Date(Date.now() + 86400_000).toISOString(), revokedAt: null, createdAt: new Date().toISOString() }]);
    vi.mocked(api.revokeTaskExternalShare).mockResolvedValue({ id: 'uuid-secret', expiresAt: new Date().toISOString(), revokedAt: new Date().toISOString(), createdAt: new Date().toISOString() });
    renderSection();
    expect(await screen.findByRole('button', { name: 'Revogar' })).toBeInTheDocument();
    expect(screen.queryByText('uuid-secret')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Revogar' }));
    await waitFor(() => expect(api.revokeTaskExternalShare).toHaveBeenCalledWith('opaque-task', 'uuid-secret'));
  });

  it('oculta o preparo WhatsApp para quem edita tarefas mas não tem capacidades de Mensagens', async () => {
    renderSection(true, false);
    fireEvent.click(screen.getByRole('button', { name: 'Revisar prévia' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirmar e criar link' }));
    expect(await screen.findByText('Link criado')).toBeInTheDocument();
    expect(screen.queryByTestId('task-whatsapp-share-preparation')).not.toBeInTheDocument();
    expect(screen.queryByText(/Preparar compartilhamento via WhatsApp/i)).not.toBeInTheDocument();
  });
});
