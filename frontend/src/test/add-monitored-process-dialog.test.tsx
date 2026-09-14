/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { klmApi, type KlmFunnel, type KlmMonitoredProcess } from '@/klm/api';
import { AddMonitoredProcessDialog } from '@/klm/components/AddMonitoredProcessDialog';

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

const VALID_CNJ = '0001234-37.2026.8.07.0001';

const criado: KlmMonitoredProcess = {
  id: 'p1', workspaceId: 'w1', matterId: null, numeroCnj: VALID_CNJ, tribunal: 'tjdft',
  status: 'ativo', oabVinculada: '68812/DF', responsibleUserId: null, createdByUserId: 'u1',
  lastMovementAt: null, lastError: null, createdAt: '2026-09-03T00:00:00Z', updatedAt: '2026-09-03T00:00:00Z',
};

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AddMonitoredProcessDialog open onOpenChange={() => undefined} canManage />
    </QueryClientProvider>,
  );
}

describe('AddMonitoredProcessDialog: a OAB entra e o tribunal sai', () => {
  beforeEach(() => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({ cards: [] } as unknown as KlmFunnel);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('manda a OAB canônica e não manda tribunal', async () => {
    const create = vi.spyOn(klmApi, 'createMonitoredProcess').mockResolvedValue(criado);
    renderDialog();

    fireEvent.change(screen.getByLabelText('Número CNJ'), { target: { value: VALID_CNJ } });
    fireEvent.change(screen.getByLabelText(/OAB vinculada/), { target: { value: 'OAB/DF 068.812' } });
    fireEvent.click(screen.getByRole('button', { name: /adicionar processo/i }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const payload = create.mock.calls[0][0];
    expect(payload.oabVinculada).toBe('68812/DF');
    expect(payload).not.toHaveProperty('tribunal'); // o backend deriva do número; o campo saiu
    expect(screen.queryByLabelText(/tribunal/i)).not.toBeInTheDocument();
  });

  it('OAB com letra vira erro sobre o que foi digitado, e nada é enviado', async () => {
    const create = vi.spyOn(klmApi, 'createMonitoredProcess').mockResolvedValue(criado);
    renderDialog();

    fireEvent.change(screen.getByLabelText('Número CNJ'), { target: { value: VALID_CNJ } });
    fireEvent.change(screen.getByLabelText(/OAB vinculada/), { target: { value: '68812A/DF' } });
    fireEvent.click(screen.getByRole('button', { name: /adicionar processo/i }));

    expect(await screen.findByText(/OAB com letra não é aceita/)).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
    expect((screen.getByLabelText(/OAB vinculada/) as HTMLInputElement).value).toBe('68812A/DF'); // não mutilado
  });

  it('sem OAB manda null e avisa que o DJEN não chega', async () => {
    const create = vi.spyOn(klmApi, 'createMonitoredProcess').mockResolvedValue({ ...criado, oabVinculada: null });
    renderDialog();

    expect(screen.getByText(/as intimações do DJEN não chegam/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Número CNJ'), { target: { value: VALID_CNJ } });
    fireEvent.click(screen.getByRole('button', { name: /adicionar processo/i }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0].oabVinculada).toBeNull();
  });
});
