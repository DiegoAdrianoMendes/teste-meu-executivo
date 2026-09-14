/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PlaybookEditorPage from '@/klm/pages/PlaybookEditorPage';
import { playbookFixture, simulationFixture } from '@/test/playbooks-fixture';

const api = vi.hoisted(() => ({
  approve: vi.fn(),
  diff: vi.fn(),
  get: vi.fn(),
  metrics: vi.fn(),
  publish: vi.fn(),
  rollback: vi.fn(),
  save: vi.fn(),
  simulate: vi.fn(),
  simulations: vi.fn(),
  submit: vi.fn(),
}));
const storage = vi.hoisted(() => ({
  download: vi.fn(),
  list: vi.fn(),
  upload: vi.fn(),
}));

vi.mock('@/services/playbooks', async (original) => {
  const actual = await original<typeof import('@/services/playbooks')>();
  return {
    ...actual,
    approvePlaybook: api.approve,
    getPlaybook: api.get,
    getPlaybookDiff: api.diff,
    getPlaybookMetrics: api.metrics,
    listPlaybookSimulations: api.simulations,
    publishPlaybook: api.publish,
    rollbackPlaybook: api.rollback,
    savePlaybookDraft: api.save,
    simulatePlaybook: api.simulate,
    submitPlaybook: api.submit,
  };
});
vi.mock('@/store/app-context', () => ({ useAppStore: () => ({ can: () => true }) }));
vi.mock('@/services/local-storage', async (original) => {
  const actual = await original<typeof import('@/services/local-storage')>();
  return {
    ...actual,
    downloadLocalStorageObject: storage.download,
    fetchLocalStorageObjects: storage.list,
    uploadLocalStorageObject: storage.upload,
  };
});

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/app/playbooks/playbook-1']}>
        <Routes><Route path="/app/playbooks/:playbookId" element={<PlaybookEditorPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function primeApi() {
  api.get.mockResolvedValue(playbookFixture);
  api.metrics.mockResolvedValue({ simulations: 1, passRate: 100, structuralCoverage: 98, handoffRate: 20, autonomousResolutionRate: 0, objectionsCovered: 1, agentsCovered: 1, lastEvaluatedAt: '2026-07-19T12:30:00Z' });
  api.simulations.mockResolvedValue([]);
  api.diff.mockResolvedValue({ fromVersion: 1, toVersion: 2, entries: [] });
  api.save.mockImplementation(async (_id, input) => ({ ...playbookFixture, ...input, etag: 'etag-3', currentVersion: 3 }));
  api.simulate.mockResolvedValue(simulationFixture);
  storage.list.mockResolvedValue([]);
}

function activateTab(name: RegExp) {
  const tab = screen.getByRole('tab', { name });
  fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
  fireEvent.click(tab);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlaybookEditorPage', () => {
  it('edits the autonomy contract and saves with optimistic concurrency', async () => {
    primeApi();
    renderEditor();
    expect(await screen.findByTestId('playbook-editor')).toBeInTheDocument();

    activateTab(/Autonomia/);
    fireEvent.click(screen.getByRole('button', { name: /IA fecha com limites/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));

    await waitFor(() => expect(api.save).toHaveBeenCalledWith('playbook-1', expect.objectContaining({
      expectedEtag: 'etag-2',
      content: expect.objectContaining({ policy: expect.objectContaining({ autonomyMode: 'ai_close_guarded' }) }),
    })));
  });

  it('runs a local simulation with explicit version, policy trail and zero external actions', async () => {
    primeApi();
    api.simulate.mockResolvedValue({
      ...simulationFixture,
      promptTokens: 210,
      completionTokens: 86,
      estimatedCostBrl: 0.0032,
    });
    renderEditor();
    await screen.findByTestId('playbook-editor');
    activateTab(/Sandbox/);
    fireEvent.click(screen.getByRole('button', { name: 'Executar simulação' }));

    await waitFor(() => expect(api.simulate).toHaveBeenCalledWith('playbook-1', expect.objectContaining({ context: expect.objectContaining({ localOnly: true }) })));
    const result = await screen.findByTestId('playbook-simulation-result');
    expect(result).toHaveTextContent('v2');
    expect(result).toHaveTextContent('0');
    expect(result).toHaveTextContent('ações externas');
    expect(result).toHaveTextContent('legalTriage.requiredDocuments');
    expect(result).not.toHaveTextContent(/tokens/i);
    expect(result).not.toHaveTextContent('R$ 0,0032');
  });

  it('hides the internal AI budget while preserving office commercial values', async () => {
    primeApi();
    renderEditor();
    await screen.findByTestId('playbook-editor');

    fireEvent.click(screen.getByRole('button', { name: /Oferta e honorários/ }));
    expect(await screen.findByText('Honorários iniciais mínimos (R$)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('4000')).toBeInTheDocument();

    activateTab(/Autonomia/);
    expect(screen.queryByText(/Orçamento diário local/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/com orçamento/i)).not.toBeInTheDocument();
  });

  it('filters private AI cost data from metrics and historical diffs', async () => {
    primeApi();
    api.metrics.mockResolvedValue({
      simulations: 1,
      passRate: 100,
      structuralCoverage: 98,
      handoffRate: 20,
      autonomousResolutionRate: 0,
      averageTokens: 296,
      averageCostBrl: 0.0032,
      objectionsCovered: 1,
      agentsCovered: 1,
    });
    api.diff.mockResolvedValue({
      fromVersion: 1,
      toVersion: 2,
      entries: [
        { path: '$.content.policy.dailyBudgetBrl', before: 50, after: 80, change: 'changed' },
        {
          path: '$.content.policy',
          before: { minimumConfidence: 0.8, promptTokens: 210 },
          after: { minimumConfidence: 0.9, promptTokens: 240 },
          change: 'changed',
        },
        { path: '$.content.commercial.minimumInitialFeeBrl', before: 4000, after: 5000, change: 'changed' },
      ],
    });
    renderEditor();
    await screen.findByTestId('playbook-editor');

    activateTab(/Governança/);
    expect(await screen.findByText('$.content.commercial.minimumInitialFeeBrl')).toBeInTheDocument();
    expect(screen.queryByText('Custo médio')).not.toBeInTheDocument();
    expect(screen.queryByText('$.content.policy.dailyBudgetBrl')).not.toBeInTheDocument();
    expect(screen.queryByText(/promptTokens/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/minimumConfidence/)).toHaveLength(2);
  });

  it('edits the governed brand and FAQ library in the native Studio', async () => {
    primeApi();
    renderEditor();
    await screen.findByTestId('playbook-editor');

    activateTab(/Biblioteca/);
    expect(await screen.findByText('Identidade do escritório')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Nome da marca' }), { target: { value: 'KLM Saúde' } });

    activateTab(/FAQ aprovada/);
    const answer = await screen.findByDisplayValue('O prazo depende dos documentos e do caso, sem garantia individual.');
    fireEvent.change(answer, { target: { value: 'O prazo depende da documentação e da análise individual do caso.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));

    await waitFor(() => expect(api.save).toHaveBeenCalledWith('playbook-1', expect.objectContaining({
      content: expect.objectContaining({
        brandKit: expect.objectContaining({ brandName: 'KLM Saúde' }),
        faqs: expect.arrayContaining([
          expect.objectContaining({ approvedAnswer: 'O prazo depende da documentação e da análise individual do caso.' }),
        ]),
      }),
    })));
  });

  it('shows an honest error state when the backend record is unavailable', async () => {
    primeApi();
    api.get.mockRejectedValue(new Error('not found'));
    renderEditor();
    expect(await screen.findByText('Não foi possível abrir o playbook')).toBeInTheDocument();
    expect(screen.getByText('Nenhum conteúdo de demonstração foi carregado.')).toBeInTheDocument();
  });
});
