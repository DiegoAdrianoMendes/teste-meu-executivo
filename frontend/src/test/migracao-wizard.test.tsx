/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import MigracaoPage from '@/klm/pages/MigracaoPage';
import { klmApi, type KlmMigrationDivergence, type KlmMigrationJob } from '@/klm/api';

const toastMock = vi.hoisted(() => vi.fn());
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }));

function baseJob(overrides: Partial<KlmMigrationJob> = {}): KlmMigrationJob {
  return {
    id: 'job-1',
    workspaceId: 'workspace-1',
    sourceSystem: 'universal_csv',
    status: 'recebido',
    sourceManifest: {},
    fieldMapping: {},
    previewSummary: {},
    reconciliationSummary: {},
    semaphore: null,
    semaphoreComputedAt: null,
    cutoverDecision: null,
    errorMessage: null,
    startedAt: '2026-07-26T10:00:00Z',
    completedAt: null,
    rolledBackAt: null,
    createdBy: 'user-1',
    createdAt: '2026-07-26T10:00:00Z',
    updatedAt: '2026-07-26T10:00:00Z',
    ...overrides,
  };
}

const validatedJob = baseJob({ status: 'validado' });
const previaJob = baseJob({
  status: 'previa',
  previewSummary: { clientes: { staged: 10, validos: 8, invalidos: 1, duplicados: 1, ambiguos: 0 } },
});
const reconciliandoJob = baseJob({ status: 'reconciliando', previewSummary: previaJob.previewSummary });
const concluidoVermelhoJob = baseJob({
  status: 'concluido',
  previewSummary: previaJob.previewSummary,
  semaphore: 'vermelho',
  semaphoreComputedAt: '2026-07-26T11:00:00Z',
  reconciliationSummary: {
    clientes: { origem: 10, importado: 8, mesclado: 1, rejeitado: 0, invalidos: 1, aguardandoImportador: 0 },
  },
});
const decidedJob = baseJob({
  ...concluidoVermelhoJob,
  cutoverDecision: {
    decision: 'prosseguir',
    decidedBy: 'user-1',
    decidedAt: '2026-07-26T12:00:00Z',
    notes: null,
  },
});

const openDivergence: KlmMigrationDivergence = {
  id: 'div-1',
  jobId: 'job-1',
  domain: 'clientes',
  tipo: 'campo_obrigatorio_ausente',
  severity: 'critica',
  detalhe: '1 registro de clientes rejeitado por CPF inválido.',
  context: null,
  resolvido: false,
  resolvedBy: null,
  resolvedAt: null,
  resolutionNote: null,
  createdAt: '2026-07-26T11:00:00Z',
};
const resolvedDivergence: KlmMigrationDivergence = {
  ...openDivergence,
  resolvido: true,
  resolvedBy: 'user-1',
  resolvedAt: '2026-07-26T13:00:00Z',
  resolutionNote: 'CPF corrigido manualmente.',
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MigracaoPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  toastMock.mockReset();
});

describe('MigracaoPage — wizard de migração', () => {
  it('walks the full wizard: prévia mostra contagens, divergência resolve, semáforo vermelho bloqueia o corte até decisão explícita, rollback fica visível', async () => {
    let currentJob = validatedJob;
    vi.spyOn(klmApi, 'getMigrationJob').mockImplementation(() => Promise.resolve(currentJob));
    const createJob = vi.spyOn(klmApi, 'createMigrationJob').mockImplementation(async () => {
      currentJob = validatedJob;
      return validatedJob;
    });
    const buildPreview = vi.spyOn(klmApi, 'buildMigrationPreview').mockImplementation(async () => {
      currentJob = previaJob;
      return previaJob;
    });
    const importBatch = vi.spyOn(klmApi, 'importMigrationBatch').mockImplementation(async () => {
      currentJob = reconciliandoJob;
      return { processed: 10, remaining: 0, status: 'reconciliando' };
    });
    const reconcile = vi.spyOn(klmApi, 'reconcileMigrationJob').mockImplementation(async () => {
      currentJob = concluidoVermelhoJob;
      return concluidoVermelhoJob;
    });
    let divergenceState = openDivergence;
    vi.spyOn(klmApi, 'listMigrationDivergences').mockImplementation(() => Promise.resolve([divergenceState]));
    const resolveDivergence = vi.spyOn(klmApi, 'resolveMigrationDivergence').mockImplementation(async () => {
      divergenceState = resolvedDivergence;
      return resolvedDivergence;
    });
    const decideCutover = vi.spyOn(klmApi, 'decideMigrationCutover').mockImplementation(async () => {
      currentJob = decidedJob;
      return decidedJob;
    });
    const rollback = vi.spyOn(klmApi, 'rollbackMigrationJob').mockResolvedValue(baseJob({ status: 'revertido', rolledBackAt: '2026-07-26T14:00:00Z' }));

    renderPage();

    // Passo 1 — origem: universal_csv já vem selecionado e habilitado.
    fireEvent.click(screen.getByRole('button', { name: /Continuar/ }));

    // Passo 2 — upload do arquivo e cálculo da prévia.
    const fileInput = screen.getByLabelText('Selecionar arquivo de migração') as HTMLInputElement;
    const file = new File(['nome,email\nAna,ana@x.com'], 'clientes.csv', { type: 'text/csv' });
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: /Enviar e calcular prévia/ }));

    await waitFor(() => expect(createJob).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(buildPreview).toHaveBeenCalledWith('job-1'));

    // Passo 3 — prévia mostra contagens por domínio, lidas do backend.
    expect(await screen.findByText('Clientes')).toBeInTheDocument();
    const previewRow = screen.getByText('Clientes').closest('tr')!;
    expect(within(previewRow).getByText('10')).toBeInTheDocument();
    expect(within(previewRow).getByText('8')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Iniciar importação/ }));

    // Passo 4 — importa o lote, que encadeia a reconciliação automaticamente.
    fireEvent.click(await screen.findByRole('button', { name: /Importar próximo lote/ }));
    await waitFor(() => expect(importBatch).toHaveBeenCalledWith('job-1', 50));
    await waitFor(() => expect(reconcile).toHaveBeenCalledWith('job-1'));

    // Passo 5 — divergência aparece e pode ser resolvida.
    expect(await screen.findByText(/CPF inválido/)).toBeInTheDocument();
    expect(screen.getByText('1 em aberto')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Resolver' }));
    await waitFor(() => expect(resolveDivergence).toHaveBeenCalledWith('job-1', 'div-1', null));

    fireEvent.click(screen.getByRole('button', { name: /Ir para o semáforo de corte/ }));

    // Passo 6 — semáforo vermelho bloqueia "prosseguir" até decisão explícita de risco.
    expect(await screen.findByText(/Vermelho — pendência crítica/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Prosseguir com o corte'));
    const registrarButton = screen.getByRole('button', { name: /Registrar decisão/ });
    expect(registrarButton).toBeDisabled();
    expect(screen.getByText(/Confirme a ciência do risco/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() => expect(registrarButton).not.toBeDisabled());
    fireEvent.click(registrarButton);
    await waitFor(() => expect(decideCutover).toHaveBeenCalledWith('job-1', 'prosseguir', null));

    // Passo 7 — rollback fica visível e disponível mesmo após a decisão.
    expect(await screen.findByRole('button', { name: /Reverter este job/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Reverter este job/ }));
    await waitFor(() => expect(rollback).toHaveBeenCalledWith('job-1'));
  });
});
