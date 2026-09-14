/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlatformAiCredentialsPanel } from '@/components/platform/PlatformAiCredentialsPanel';
import type { PlatformAiCredentialsRead } from '@/services/platform';

const api = vi.hoisted(() => ({
  fetch: vi.fn(),
  save: vi.fn(),
  deactivate: vi.fn(),
}));

const toastSpy = vi.hoisted(() => vi.fn());

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>('@/services/platform');
  return {
    ...actual,
    fetchPlatformAiCredentials: api.fetch,
    savePlatformAiCredential: api.save,
    deactivatePlatformAiCredential: api.deactivate,
  };
});

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastSpy }),
}));

const PASTED_KEY = 'chave-colada-0123456789-final-zzzz';

function payload(overrides: Partial<PlatformAiCredentialsRead> = {}): PlatformAiCredentialsRead {
  return {
    gate: { modelApisEnabled: false, onlineApproved: true, cipherConfigured: true },
    providers: [
      {
        provider: 'gemini',
        configured: false,
        isActive: false,
        apiKeyMasked: null,
        keyLast4: null,
        updatedAt: null,
        updatedByUserId: null,
        keyManaged: true,
        envConfigured: false,
        adapterRegistered: true,
        effectiveSource: 'none',
        reason: 'Nenhuma chave gravada no Blackbox nem na variável de ambiente.',
      },
      {
        provider: 'anthropic',
        configured: true,
        isActive: true,
        apiKeyMasked: '****abcd',
        keyLast4: 'abcd',
        updatedAt: '2026-09-06T12:00:00Z',
        updatedByUserId: 'user-1',
        keyManaged: true,
        envConfigured: false,
        adapterRegistered: true,
        effectiveSource: 'blackbox',
        reason: 'Chave gravada pelo Blackbox; vale para todo workspace sem chave própria.',
      },
      {
        provider: 'vertex',
        configured: false,
        isActive: false,
        apiKeyMasked: null,
        keyLast4: null,
        updatedAt: null,
        updatedByUserId: null,
        keyManaged: false,
        envConfigured: false,
        adapterRegistered: false,
        effectiveSource: 'none',
        reason: 'O Vertex AI usa service account, não chave de API.',
      },
    ],
    ...overrides,
  };
}

function renderPanel(props: { canManage?: boolean } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatformAiCredentialsPanel enabled canManage {...props} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformAiCredentialsPanel', () => {
  it('renders providers, effective sources and the closed-gate notice', async () => {
    api.fetch.mockResolvedValue(payload());
    renderPanel();

    expect(await screen.findByText('Google Gemini')).toBeTruthy();
    expect(screen.getByText('****abcd')).toBeTruthy();
    expect(screen.getByText('Gate da plataforma fechado')).toBeTruthy();
    expect(screen.getByText('Blackbox')).toBeTruthy();
    expect(screen.getAllByText('Sem chave').length).toBe(2);
    expect(screen.getByText('Service account')).toBeTruthy();
    expect(screen.getByText('Ativa')).toBeTruthy();
    expect(screen.queryByText('Cifra de segredos ausente')).toBeNull();
  });

  it('saves a pasted key through the service and clears the field afterwards', async () => {
    api.fetch.mockResolvedValue(payload());
    api.save.mockResolvedValue({
      ...payload().providers[0],
      configured: true,
      isActive: true,
      apiKeyMasked: '****zzzz',
      keyLast4: 'zzzz',
      effectiveSource: 'blackbox',
    });
    renderPanel();

    const input = (await screen.findByLabelText('Nova chave de Google Gemini')) as HTMLInputElement;
    expect(input.type).toBe('password');
    expect(input.getAttribute('autocomplete')).toBe('new-password');
    const button = screen.getByRole('button', { name: 'Salvar chave' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    fireEvent.change(input, { target: { value: 'curta' } });
    expect(button.disabled).toBe(true);
    fireEvent.change(input, { target: { value: PASTED_KEY } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);

    await waitFor(() => expect(api.save).toHaveBeenCalledWith('gemini', PASTED_KEY));
    expect(api.save).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(input.value).toBe(''));
    expect(screen.queryByDisplayValue(PASTED_KEY)).toBeNull();
    expect(document.body.innerHTML).not.toContain(PASTED_KEY);
    expect(toastSpy).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Chave de Google Gemini gravada.' }),
    );
  });

  it('deactivates only after the confirmation click', async () => {
    api.fetch.mockResolvedValue(payload());
    api.deactivate.mockResolvedValue({
      ...payload().providers[1],
      isActive: false,
      effectiveSource: 'none',
    });
    renderPanel();

    const deactivate = await screen.findByRole('button', { name: 'Desativar' });
    fireEvent.click(deactivate);
    expect(api.deactivate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(screen.queryByRole('button', { name: 'Confirmar desativação' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Desativar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar desativação' }));
    await waitFor(() => expect(api.deactivate).toHaveBeenCalledWith('anthropic'));
  });

  it('surfaces the backend message when saving fails', async () => {
    api.fetch.mockResolvedValue(payload());
    api.save.mockRejectedValue(new Error('A cifra de segredos não está configurada.'));
    renderPanel();

    const input = await screen.findByLabelText('Nova chave de Google Gemini');
    fireEvent.change(input, { target: { value: PASTED_KEY } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar chave' }));

    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          variant: 'destructive',
          description: 'A cifra de segredos não está configurada.',
        }),
      ),
    );
  });

  it('blocks writes and warns when the secret cipher is missing', async () => {
    api.fetch.mockResolvedValue(
      payload({ gate: { modelApisEnabled: true, onlineApproved: true, cipherConfigured: false } }),
    );
    renderPanel();

    expect(await screen.findByText('Cifra de segredos ausente')).toBeTruthy();
    expect(screen.queryByText('Gate da plataforma fechado')).toBeNull();
    const input = screen.getByLabelText('Nova chave de Google Gemini');
    fireEvent.change(input, { target: { value: PASTED_KEY } });
    const button = screen.getByRole('button', { name: 'Salvar chave' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('is read-only without the manage permission', async () => {
    api.fetch.mockResolvedValue(payload());
    renderPanel({ canManage: false });

    expect(await screen.findByText('Google Gemini')).toBeTruthy();
    expect(screen.queryByLabelText('Nova chave de Google Gemini')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Desativar' })).toBeNull();
    expect(screen.queryByText('Ações')).toBeNull();
  });

  it('reports a load failure without inventing data', async () => {
    api.fetch.mockRejectedValue(new Error('falhou'));
    renderPanel();

    expect(await screen.findByText(/Não foi possível carregar as chaves/)).toBeTruthy();
    expect(screen.queryByText('Google Gemini')).toBeNull();
  });
});
