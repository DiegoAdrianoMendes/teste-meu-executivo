/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const httpState = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', async () => {
  const actual = await vi.importActual<typeof import('@/services/http')>('@/services/http');
  return { ...actual, apiFetch: httpState.apiFetch };
});

import { SupportLegacyMigrationSection } from '@/pages/PlatformSupportTicketsPanel';

function renderSection(canManage = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<SupportLegacyMigrationSection canManage={canManage} />, { wrapper });
}

afterEach(() => {
  cleanup();
  httpState.apiFetch.mockReset();
});

describe('Migração de solicitações técnicas legadas no painel de SAC', () => {
  it('pede confirmação em diálogo antes de disparar a operação em massa', async () => {
    httpState.apiFetch.mockResolvedValue({ migrated: 3 });

    renderSection();

    fireEvent.click(screen.getByRole('button', { name: /Incorporar solicitações técnicas/ }));
    expect(
      await screen.findByRole('alertdialog', { name: /Incorporar solicitações técnicas legadas\?/ }),
    ).toBeInTheDocument();
    // Nada é disparado enquanto a confirmação não é aceita.
    expect(httpState.apiFetch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(httpState.apiFetch).not.toHaveBeenCalled();
  });

  it('mostra quantos registros foram migrados após a confirmação', async () => {
    httpState.apiFetch.mockResolvedValue({ migrated: 7 });

    renderSection();

    fireEvent.click(screen.getByRole('button', { name: /Incorporar solicitações técnicas/ }));
    fireEvent.change(await screen.findByLabelText('Motivo da operação'), {
      target: { value: 'Consolidar o histórico técnico no SAC.' },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Incorporar agora' }));

    await waitFor(() => expect(httpState.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/support/migrate-technical-requests',
      {
        method: 'POST',
        headers: {
          'X-Operation-Reason': encodeURIComponent('Consolidar o histórico técnico no SAC.'),
        },
      },
    ));
    expect(await screen.findByText('7 registro(s) migrado(s) na última execução.')).toBeInTheDocument();
  });

  it('diz "nada pendente para migrar" quando o backend retorna 0', async () => {
    httpState.apiFetch.mockResolvedValue({ migrated: 0 });

    renderSection();

    fireEvent.click(screen.getByRole('button', { name: /Incorporar solicitações técnicas/ }));
    fireEvent.change(await screen.findByLabelText('Motivo da operação'), {
      target: { value: 'Confirmar que não há legado pendente.' },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Incorporar agora' }));

    expect(await screen.findByText('Nada pendente para migrar.')).toBeInTheDocument();
  });

  it('não expõe a operação sem a permissão support.manage', () => {
    renderSection(false);
    expect(screen.queryByRole('button', { name: /Incorporar solicitações técnicas/ })).toBeNull();
  });
});
