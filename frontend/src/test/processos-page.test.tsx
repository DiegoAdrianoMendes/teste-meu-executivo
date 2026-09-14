/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { klmApi, type KlmMonitoredProcess } from '@/klm/api';
import ProcessosPage from '@/klm/pages/ProcessosPage';
import { fetchWorkspacePlanSummary } from '@/services/admin';

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/store/app-context', () => ({ useAppStore: () => ({ can: () => true }) }));
vi.mock('@/services/admin', () => ({ fetchWorkspacePlanSummary: vi.fn() }));

const base: Omit<KlmMonitoredProcess, 'id' | 'numeroCnj' | 'oabVinculada' | 'lastError'> = {
  workspaceId: 'w1', matterId: null, tribunal: 'tjdft', status: 'ativo', responsibleUserId: null,
  createdByUserId: 'u1', lastMovementAt: null, createdAt: '2026-09-03T00:00:00Z', updatedAt: '2026-09-03T00:00:00Z',
};
const processos: KlmMonitoredProcess[] = [
  { ...base, id: 'p1', numeroCnj: '0001234-37.2026.8.07.0001', oabVinculada: '68812/DF', lastError: 'DataJud: HTTP 404 em tjdft' },
  { ...base, id: 'p2', numeroCnj: '0007654-13.2026.8.07.0016', oabVinculada: null, lastError: null },
  { ...base, id: 'p3', numeroCnj: '0000001-68.2026.8.26.0100', tribunal: 'tjsp', oabVinculada: '68812', lastError: null },
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProcessosPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ProcessosPage: OAB, tribunal e erro na listagem; edição', () => {
  beforeEach(() => {
    // restoreAllMocks no afterEach apaga a implementação do vi.fn do factory: reaplicar aqui
    vi.mocked(fetchWorkspacePlanSummary).mockResolvedValue({
      monitoredProcessesUsed: 3, maxMonitoredProcesses: 10, monitoredProcessesRemaining: 7,
    } as unknown as Awaited<ReturnType<typeof fetchWorkspacePlanSummary>>);
    vi.spyOn(klmApi, 'listMonitoredProcesses').mockResolvedValue(processos);
    vi.spyOn(klmApi, 'listProcessPublications').mockResolvedValue([]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('mostra os três estados de OAB, o rótulo do tribunal e o erro com o texto completo no title', async () => {
    renderPage();

    expect(await screen.findByText('68812/DF')).toBeInTheDocument();
    expect(screen.getByText('sem OAB')).toBeInTheDocument();
    expect(screen.getByText('OAB inválida, edite')).toBeInTheDocument();
    expect(screen.getAllByText('TJDFT').length).toBe(2);
    expect(screen.getByText('TJSP')).toBeInTheDocument();
    const erro = screen.getByTestId('processo-last-error');
    expect(erro).toHaveAttribute('title', 'DataJud: HTTP 404 em tjdft');
  });

  it('Editar abre o diálogo pré-preenchido e o PATCH sai só com a OAB nova', async () => {
    const update = vi.spyOn(klmApi, 'updateMonitoredProcess').mockResolvedValue({ ...processos[0], oabVinculada: '123456/SP' });
    renderPage();
    await screen.findByText('68812/DF');

    const linha = screen.getByText('0001234-37.2026.8.07.0001').closest('tr') as HTMLTableRowElement;
    fireEvent.click(within(linha).getByRole('button', { name: /editar/i }));

    const campo = await screen.findByLabelText(/OAB vinculada/) as HTMLInputElement;
    expect(campo.value).toBe('68812/DF');
    expect(screen.getByTestId('editar-processo-last-error')).toHaveTextContent('DataJud: HTTP 404 em tjdft');

    // mesma OAB: nada a salvar
    expect(screen.getByRole('button', { name: /salvar oab/i })).toBeDisabled();

    fireEvent.change(campo, { target: { value: 'oab/sp 123.456' } });
    fireEvent.click(screen.getByRole('button', { name: /salvar oab/i }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith('p1', { oabVinculada: '123456/SP' });
  });

  it('Remover OAB manda null explícito', async () => {
    const update = vi.spyOn(klmApi, 'updateMonitoredProcess').mockResolvedValue({ ...processos[0], oabVinculada: null });
    renderPage();
    await screen.findByText('68812/DF');
    const linha = screen.getByText('0001234-37.2026.8.07.0001').closest('tr') as HTMLTableRowElement;
    fireEvent.click(within(linha).getByRole('button', { name: /editar/i }));

    fireEvent.click(await screen.findByRole('button', { name: /remover oab/i }));

    await waitFor(() => expect(update).toHaveBeenCalledWith('p1', { oabVinculada: null }));
  });
});


describe('EditMonitoredProcessDialog: troca de linha', () => {
  beforeEach(() => {
    vi.mocked(fetchWorkspacePlanSummary).mockResolvedValue({
      monitoredProcessesUsed: 3, maxMonitoredProcesses: 10, monitoredProcessesRemaining: 7,
    } as unknown as Awaited<ReturnType<typeof fetchWorkspacePlanSummary>>);
    vi.spyOn(klmApi, 'listMonitoredProcesses').mockResolvedValue(processos);
    vi.spyOn(klmApi, 'listProcessPublications').mockResolvedValue([]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('abrir na linha A, cancelar e abrir na linha B mostra a OAB da B', async () => {
    renderPage();
    await screen.findByText('68812/DF');
    const linhaA = screen.getByText('0001234-37.2026.8.07.0001').closest('tr') as HTMLTableRowElement;
    fireEvent.click(within(linhaA).getByRole('button', { name: /editar/i }));
    const campoA = await screen.findByLabelText(/OAB vinculada/) as HTMLInputElement;
    expect(campoA.value).toBe('68812/DF');
    fireEvent.change(campoA, { target: { value: 'rascunho' } });
    fireEvent.click(screen.getByRole('button', { name: /cancelar/i }));

    const linhaB = screen.getByText('0000001-68.2026.8.26.0100').closest('tr') as HTMLTableRowElement;
    fireEvent.click(within(linhaB).getByRole('button', { name: /editar/i }));
    const campoB = await screen.findByLabelText(/OAB vinculada/) as HTMLInputElement;
    expect(campoB.value).toBe('68812'); // a da B (legado inválido), não o rascunho da A
  });
});
