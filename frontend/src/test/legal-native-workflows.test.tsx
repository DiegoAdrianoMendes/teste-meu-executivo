/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { LegalArtifactsPanel } from '@/klm/components/LegalArtifactsPanel';
import { ProcessMonitoringPanel } from '@/klm/components/ProcessMonitoringPanel';
import { klmApi, type KlmLegalArtifactVersion, type KlmProcessMonitoringRecord } from '@/klm/api';

const matterId = 'matter-legal-1';
const artifactV1: KlmLegalArtifactVersion = {
  id: 'artifact-v1',
  workspaceId: 'workspace-1',
  matterId,
  artifactKey: 'artifact-key-1',
  version: 1,
  kind: 'contract',
  title: 'Contrato de honorários',
  status: 'draft',
  content: { body: 'Primeira versão.' },
  sourceSnapshot: { matter: { id: matterId } },
  createdByUserId: 'user-1',
  approvalItemId: null,
  createdAt: '2026-07-18T12:00:00Z',
  updatedAt: '2026-07-18T12:00:00Z',
};
const artifactV2: KlmLegalArtifactVersion = {
  ...artifactV1,
  id: 'artifact-v2',
  version: 2,
  title: 'Contrato revisado',
  content: { body: 'Segunda versão auditável.' },
  createdAt: '2026-07-18T13:00:00Z',
  updatedAt: '2026-07-18T13:00:00Z',
};
const processRecord: KlmProcessMonitoringRecord = {
  id: 'process-1',
  workspaceId: 'workspace-1',
  matterId,
  createdByUserId: 'user-1',
  caseNumber: '1001234-56.2026.8.26.0100',
  court: '1ª Vara Cível',
  status: 'liminar_pendente',
  nextDeadlineAt: '2027-01-20T12:00:00Z',
  lastCheckedAt: null,
  lastEventSummary: 'Distribuição concluída.',
  sourceState: 'manual_local',
  events: [],
  createdAt: '2026-07-18T12:00:00Z',
  updatedAt: '2026-07-18T12:00:00Z',
};

function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFunnel() {
  vi.spyOn(klmApi, 'funnel').mockResolvedValue({
    boardVersion: 1,
    stages: [],
    metrics: {},
    cards: [{
      id: matterId,
      clientId: 'client-1',
      name: 'Cliente de teste',
      subject: 'medicamento_alto_custo',
      object: 'Medicamento X',
      source: 'google_ads',
      owner: 'Jurídico',
      probability: 90,
      initialFee: 4000,
      projectedRevenue: 50000,
      slaDueAt: null,
      documents: 2,
      documentScore: 80,
      stage: 'documentos',
      microStatus: 'coleta',
    }],
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('native legal workflows', () => {
  it('creates an immutable next artifact version and submits that exact version', async () => {
    mockFunnel();
    vi.spyOn(klmApi, 'legalArtifacts').mockResolvedValue([artifactV1]);
    const create = vi.spyOn(klmApi, 'createLegalArtifact').mockResolvedValue(artifactV2);
    const submit = vi.spyOn(klmApi, 'submitLegalArtifact').mockResolvedValue({
      artifact: { ...artifactV2, status: 'review_pending', approvalItemId: 'approval-1' },
      approvalId: 'approval-1',
    });

    renderWithProviders(<LegalArtifactsPanel title="Minutas de fechamento" kinds={['contract']} matterId={matterId} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Nova versão' }));
    const editor = screen.getByRole('dialog');
    fireEvent.change(within(editor).getByLabelText('Título'), { target: { value: 'Contrato revisado' } });
    fireEvent.change(within(editor).getByLabelText('Conteúdo'), { target: { value: 'Segunda versão auditável.' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Salvar versão' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({
      matterId,
      kind: 'contract',
      artifactKey: artifactV1.artifactKey,
      expectedVersion: 1,
      title: 'Contrato revisado',
      content: expect.objectContaining({ body: 'Segunda versão auditável.', externalEffectsExecuted: 0 }),
    })));
    expect(await screen.findByText(/Contrato v2 registrada no banco local/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Enviar para aprovação' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith('artifact-v2'));
  });

  it('persists process creation, movement and client-update draft without external effects', async () => {
    mockFunnel();
    vi.spyOn(klmApi, 'processMonitoring').mockResolvedValue([processRecord]);
    const createProcess = vi.spyOn(klmApi, 'createProcessMonitoring').mockResolvedValue(processRecord);
    const addEvent = vi.spyOn(klmApi, 'addProcessMonitoringEvent').mockResolvedValue({
      ...processRecord,
      events: [{
        id: 'event-1',
        occurredAt: '2026-07-18T14:00:00Z',
        title: 'Liminar apreciada',
        description: 'Aguardando intimação.',
        source: 'manual_local',
        recordedByUserId: 'user-1',
      }],
    });
    const createArtifact = vi.spyOn(klmApi, 'createLegalArtifact').mockResolvedValue({
      ...artifactV1,
      id: 'client-update-1',
      artifactKey: 'client-update-key',
      kind: 'client_update',
      title: `Atualização do processo ${processRecord.caseNumber}`,
    });
    const externalIntent = vi.spyOn(klmApi, 'createExternalIntent').mockResolvedValue({
      id: 'intent-1',
      workspaceId: 'workspace-1',
      matterId,
      requestedByUserId: 'user-1',
      actionType: 'process_external_check',
      provider: 'court_connector',
      status: 'blocked_external',
      idempotencyKey: 'process-check',
      payload: { externalEffectsExecuted: 0 },
      reason: 'Execução externa bloqueada no ambiente local.',
      createdAt: '2026-07-18T12:00:00Z',
      updatedAt: '2026-07-18T12:00:00Z',
    });

    renderWithProviders(<ProcessMonitoringPanel matterId={matterId} canManageIntegrations />);

    fireEvent.click(await screen.findByRole('button', { name: 'Vincular processo' }));
    const createDialog = screen.getByRole('dialog');
    fireEvent.change(within(createDialog).getByLabelText('Número do processo'), { target: { value: '2009876-12.2026.8.26.0100' } });
    fireEvent.change(within(createDialog).getByLabelText('Tribunal'), { target: { value: '2ª Vara Cível' } });
    fireEvent.change(within(createDialog).getByLabelText('Movimentação inicial'), { target: { value: 'Cadastro manual conferido.' } });
    fireEvent.click(within(createDialog).getByRole('button', { name: 'Cadastrar' }));
    await waitFor(() => expect(createProcess).toHaveBeenCalledWith(expect.objectContaining({
      matterId,
      caseNumber: '2009876-12.2026.8.26.0100',
      court: '2ª Vara Cível',
      lastEventSummary: 'Cadastro manual conferido.',
    })));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    fireEvent.click(screen.getByRole('button', { name: 'Movimentação' }));
    const eventDialog = screen.getByRole('dialog');
    fireEvent.change(within(eventDialog).getByLabelText('Título'), { target: { value: 'Liminar apreciada' } });
    fireEvent.change(within(eventDialog).getByLabelText('Descrição'), { target: { value: 'Aguardando intimação.' } });
    fireEvent.click(within(eventDialog).getByRole('button', { name: 'Registrar' }));
    await waitFor(() => expect(addEvent).toHaveBeenCalledWith('process-1', {
      title: 'Liminar apreciada',
      description: 'Aguardando intimação.',
      source: 'manual_local',
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    fireEvent.click(screen.getByRole('button', { name: 'Atualização' }));
    await waitFor(() => expect(createArtifact).toHaveBeenCalledWith(expect.objectContaining({
      matterId,
      kind: 'client_update',
      content: expect.objectContaining({ channel: 'whatsapp', externalEffectsExecuted: 0 }),
    })));
    fireEvent.click(screen.getByRole('button', { name: 'Consulta bloqueada' }));
    await waitFor(() => expect(externalIntent).toHaveBeenCalledWith(expect.objectContaining({
      matterId,
      actionType: 'process_external_check',
      provider: 'court_connector',
      payload: { processMonitoringId: 'process-1', externalEffectsExecuted: 0 },
    })));
    expect(await screen.findByText(/Nenhum tribunal foi acessado/i)).toBeInTheDocument();
  });
});
