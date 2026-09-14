/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AcquisitionIntegrationsPanel } from '@/klm/components/AcquisitionIntegrationsPanel';
import {
  klmApi,
  type KlmAdsManagerSummary,
  type KlmChannelConnection,
  type KlmLeadHydrationJob,
  type KlmThread,
} from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const connection: KlmChannelConnection = {
  id: 'connection-meta',
  workspaceId: 'workspace',
  channel: 'facebook',
  provider: 'meta_ads',
  connectionKey: 'meta_ads:local',
  displayName: 'Meta Ads local',
  externalAccountId: null,
  transportMode: 'local_fixture',
  status: 'configured_local',
  isDefault: true,
  capabilities: { lead_forms: { supported: true, enabled: true, state: 'available_local' } },
  limits: {},
  routingPolicy: {},
  health: { state: 'local_configuration_complete' },
  configuration: { mappingVersion: 'v7' },
  credentialReference: null,
  webhookState: 'not_configured',
  version: 1,
  verifiedAt: null,
  connectedAt: null,
  lastSyncedAt: null,
  createdAt: '2026-07-20T12:00:00Z',
  updatedAt: '2026-07-20T12:00:00Z',
};

const thread: KlmThread = {
  id: 'thread-canonical',
  workspaceId: 'workspace',
  matterId: 'matter-health',
  clientId: 'client-lead',
  assignedHumanId: null,
  primaryChannel: 'facebook',
  status: 'open',
  subject: 'Lead de validação local',
  objectName: null,
  ownerAgentKey: null,
  score: 0,
  unreadCount: 0,
  lastMessagePreview: null,
  lastMessageAt: null,
  slaDueAt: null,
  threadMetadata: {},
};

const adsSummary: KlmAdsManagerSummary = {
  dateFrom: '2026-07-01',
  dateTo: '2026-07-20',
  provider: null,
  currency: 'BRL',
  spend: '1280.000000',
  impressions: 18400,
  reach: 13920,
  clicks: 426,
  conversations: 61,
  comments: 28,
  providerLeads: 34,
  capturedLeads: 31,
  hydrationPending: 1,
  hydrationFailed: 0,
  qualifiedLeads: 19,
  opportunities: 11,
  contracts: 5,
  revenue: '26000.000000',
  ctr: '2.3152',
  cpl: '41.2903',
  cac: '256.0000',
  roas: '20.3125',
  campaigns: [{
    entityId: 'campaign-entity',
    externalId: 'campaign-001',
    displayName: 'Negativas de plano de saúde',
    provider: 'meta_ads',
    effectiveStatus: 'active',
    objective: 'lead_generation',
    currency: 'BRL',
    spend: '1280.000000',
    impressions: 18400,
    reach: 13920,
    clicks: 426,
    conversations: 61,
    comments: 28,
    providerLeads: 34,
    capturedLeads: 31,
    qualifiedLeads: 19,
    opportunities: 11,
    contracts: 5,
    revenue: '26000.000000',
    ctr: '2.3152',
    cpl: '41.2903',
    cac: '256.0000',
    roas: '20.3125',
  }],
  generatedAt: '2026-07-20T12:00:00Z',
};

const hydrationJob: KlmLeadHydrationJob = {
  id: 'hydration-1',
  workspaceId: 'workspace',
  connectionId: connection.id,
  publicIngressReceiptId: 'ingress-1',
  leadCaptureReceiptId: null,
  provider: 'meta_ads',
  connectionKey: connection.connectionKey,
  externalEventId: 'event-1',
  externalLeadId: 'lead-pending-1',
  externalFormId: 'form-001',
  locatorPayload: { campaignId: 'campaign-001' },
  payloadSha256: 'hash',
  state: 'blocked_external',
  attemptCount: 1,
  maxAttempts: 7,
  nextAttemptAt: null,
  leaseExpiresAt: null,
  fetchAttemptedAt: null,
  hydratedAt: null,
  completedAt: null,
  lastErrorCode: 'external_gate_closed',
  lastErrorMessage: 'A conexão oficial ainda não foi aprovada.',
  providerReceipt: { externalRequests: 0 },
  createdAt: '2026-07-20T12:00:00Z',
  updatedAt: '2026-07-20T12:00:00Z',
};

function mockManagerReads({ hydrations = [] as KlmLeadHydrationJob[] } = {}) {
  vi.spyOn(klmApi, 'leadHydrations').mockResolvedValue(hydrations);
  vi.spyOn(klmApi, 'adsManagerSummary').mockResolvedValue(adsSummary);
  vi.spyOn(klmApi, 'adsManagerEntities').mockResolvedValue([]);
  vi.spyOn(klmApi, 'adsManagerImports').mockResolvedValue([]);
  vi.spyOn(klmApi, 'leadHydrationEvents').mockResolvedValue([]);
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><AcquisitionIntegrationsPanel canManageIntegrations /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AcquisitionIntegrationsPanel', () => {
  it('prepares a missing source through the native connection contract', async () => {
    mockManagerReads();
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([]);
    vi.spyOn(klmApi, 'leadCaptures').mockResolvedValue({
      items: [], total: 0, hasMore: false, countsByProvider: {}, countsByState: {}, generatedAt: '2026-07-20T12:00:00Z',
    });
    const create = vi.spyOn(klmApi, 'createOmnichannelConnection').mockResolvedValue(connection);
    renderPanel();

    const prepareButtons = await screen.findAllByRole('button', { name: 'Configurar' });
    fireEvent.click(prepareButtons[0]);

    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({
      provider: 'meta_ads',
      channel: 'facebook',
      transportMode: 'local_fixture',
    })));
    expect(await screen.findByText(/Meta Ads configurada/)).toBeInTheDocument();
  });

  it('sends a provider-shaped raw payload into the canonical local capture route', async () => {
    mockManagerReads();
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'leadCaptures').mockResolvedValue({
      items: [], total: 0, hasMore: false, countsByProvider: {}, countsByState: {}, generatedAt: '2026-07-20T12:00:00Z',
    });
    const ingest = vi.spyOn(klmApi, 'ingestRawLeadCapture').mockResolvedValue({
      receipt: {
        id: 'receipt', workspaceId: 'workspace', provider: 'meta_ads', connectionKey: connection.connectionKey,
        externalLeadId: 'lead', externalFormId: 'form', payloadSha256: 'hash', state: 'completed', mappingVersion: 'v7',
        identityId: 'identity', threadId: thread.id, matterId: 'matter-health',
        result: { proactiveRoute: { state: 'scheduled', connectionKey: 'whatsapp:principal' } },
        errorCode: null, completedAt: '2026-07-20T12:00:00Z', createdAt: '2026-07-20T12:00:00Z', updatedAt: '2026-07-20T12:00:00Z',
      },
      identity: { id: 'identity', clientId: 'client-lead', displayName: 'Lead de validação local', canonicalPhone: '+5511900000001', canonicalEmail: 'lead.local@exemplo.test', identityState: 'verified', source: 'meta_ads', mergeVersion: 1, identityMetadata: {} },
      endpoint: { id: 'endpoint' },
      binding: { id: 'binding' },
      thread,
      message: null,
      clientId: 'client-lead',
      matterId: 'matter-health',
      deduplicated: false,
    });
    renderPanel();
    expect(await screen.findByText('Conta principal de anúncios da Meta.')).toBeInTheDocument();
    expect(screen.queryByText(/meta_ads:local|local_fixture/)).not.toBeInTheDocument();

    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Testar captação' }), { button: 0, ctrlKey: false });
    const run = await screen.findByRole('button', { name: 'Executar teste local' });
    fireEvent.click(run);

    await waitFor(() => expect(ingest).toHaveBeenCalledTimes(1));
    expect(ingest.mock.calls[0][0]).toBe(connection.id);
    expect(ingest.mock.calls[0][1]).toEqual(expect.objectContaining({
      proactiveMessageTemplate: 'triagem_saude_v1',
      rawPayload: expect.objectContaining({ campaign_name: 'Negativas de plano de saúde' }),
    }));
    expect(await screen.findByText(/primeira abordagem agendada/)).toBeInTheDocument();
  });

  it('shows persisted campaign performance and imports a local snapshot on demand', async () => {
    mockManagerReads();
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'leadCaptures').mockResolvedValue({
      items: [], total: 0, hasMore: false, countsByProvider: {}, countsByState: {}, generatedAt: '2026-07-20T12:00:00Z',
    });
    const importSnapshot = vi.spyOn(klmApi, 'ingestAdsManagerSnapshot').mockResolvedValue({
      receipt: {
        id: 'import-1', connectionId: connection.id, provider: 'meta_ads', externalSyncId: 'sync-1',
        source: 'local_fixture', entityCount: 6, observationCount: 1,
        observedAt: '2026-07-20T12:00:00Z', receivedAt: '2026-07-20T12:00:00Z',
      },
      entities: [], observations: [], deduplicated: false,
    });
    renderPanel();

    expect(await screen.findByText('R$ 1.280,00')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Gestor de anúncios' }), { button: 0, ctrlKey: false });
    expect(await screen.findByText('Negativas de plano de saúde')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Carregar cenário local' }));

    await waitFor(() => expect(importSnapshot).toHaveBeenCalledTimes(1));
    expect(importSnapshot.mock.calls[0][0]).toBe(connection.id);
    expect(importSnapshot.mock.calls[0][1]).toEqual(expect.objectContaining({ source: 'local_fixture' }));
    expect(await screen.findByText(/Cenário de anúncios importado/)).toBeInTheDocument();
  });

  it('sends a manual spend entry with the manual origin and the same ingest contract', async () => {
    mockManagerReads();
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'leadCaptures').mockResolvedValue({
      items: [], total: 0, hasMore: false, countsByProvider: {}, countsByState: {}, generatedAt: '2026-07-20T12:00:00Z',
    });
    const importSnapshot = vi.spyOn(klmApi, 'ingestAdsManagerSnapshot').mockResolvedValue({
      receipt: {
        id: 'import-manual', connectionId: connection.id, provider: 'meta_ads', externalSyncId: 'manual-sync',
        source: 'manual', entityCount: 1, observationCount: 1,
        observedAt: '2026-07-20T12:00:00Z', receivedAt: '2026-07-20T12:00:00Z',
      },
      entities: [], observations: [], deduplicated: false,
    });
    renderPanel();

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Gestor de anúncios' }), { button: 0, ctrlKey: false });
    fireEvent.change(await screen.findByPlaceholderText('Nome da campanha'), { target: { value: 'Campanha manual julho' } });
    fireEvent.change(screen.getAllByPlaceholderText('0,00')[0], { target: { value: '980.50' } });

    fireEvent.click(screen.getByRole('button', { name: 'Registrar investimento' }));

    await waitFor(() => expect(importSnapshot).toHaveBeenCalledTimes(1));
    expect(importSnapshot.mock.calls[0][0]).toBe(connection.id);
    expect(importSnapshot.mock.calls[0][1]).toEqual(expect.objectContaining({
      source: 'manual',
      entities: [expect.objectContaining({
        entityType: 'campaign',
        displayName: 'Campanha manual julho',
        metrics: [expect.objectContaining({ spend: '980.50' })],
      })],
    }));
    expect(await screen.findByText(/Investimento manual registrado/)).toBeInTheDocument();
  });

  it('parses an uploaded CSV of spend rows and sends one manual snapshot per provider', async () => {
    mockManagerReads();
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'leadCaptures').mockResolvedValue({
      items: [], total: 0, hasMore: false, countsByProvider: {}, countsByState: {}, generatedAt: '2026-07-20T12:00:00Z',
    });
    const importSnapshot = vi.spyOn(klmApi, 'ingestAdsManagerSnapshot').mockResolvedValue({
      receipt: {
        id: 'import-csv', connectionId: connection.id, provider: 'meta_ads', externalSyncId: 'csv-sync',
        source: 'manual', entityCount: 1, observationCount: 1,
        observedAt: '2026-07-20T12:00:00Z', receivedAt: '2026-07-20T12:00:00Z',
      },
      entities: [], observations: [], deduplicated: false,
    });
    renderPanel();

    await screen.findByText('1 pronta(s)');
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Gestor de anúncios' }), { button: 0, ctrlKey: false });
    await screen.findByRole('button', { name: 'Selecionar arquivo CSV' });
    const csv = 'provider,campaign_id,campaign_name,period_start,period_end,spend,revenue\n'
      + 'meta_ads,camp-csv-1,Campanha CSV,2026-07-01,2026-07-20,750.00,9000.00\n';
    const file = new File([csv], 'spend.csv', { type: 'text/csv' });
    Object.defineProperty(file, 'text', { value: () => Promise.resolve(csv), configurable: true });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(importSnapshot).toHaveBeenCalledTimes(1));
    expect(importSnapshot.mock.calls[0][0]).toBe(connection.id);
    expect(importSnapshot.mock.calls[0][1]).toEqual(expect.objectContaining({
      source: 'manual',
      entities: [expect.objectContaining({
        externalId: 'camp-csv-1',
        displayName: 'Campanha CSV',
        metrics: [expect.objectContaining({ spend: '750.00', revenue: '9000.00' })],
      })],
    }));
    expect(await screen.findByText(/1 linha\(s\) de investimento importada\(s\)/)).toBeInTheDocument();
  });

  it('exposes retry, cancellation and immutable history for lead hydration', async () => {
    mockManagerReads({ hydrations: [hydrationJob] });
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'leadCaptures').mockResolvedValue({
      items: [], total: 0, hasMore: false, countsByProvider: {}, countsByState: {}, generatedAt: '2026-07-20T12:00:00Z',
    });
    vi.mocked(klmApi.leadHydrationEvents).mockResolvedValue([{
      id: 'event-1', hydrationJobId: hydrationJob.id, provider: 'meta_ads', externalLeadId: hydrationJob.externalLeadId,
      eventType: 'created', previousState: 'none', nextState: 'queued', errorCode: null, errorMessage: null,
      occurredAt: '2026-07-20T12:00:00Z',
    }]);
    const updateHydration = vi.spyOn(klmApi, 'updateLeadHydration').mockResolvedValue({ ...hydrationJob, state: 'queued' });
    renderPanel();

    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Captação e buscas' }), { button: 0, ctrlKey: false });
    expect(await screen.findByText('Lead lead-pending-1')).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Ver histórico da busca'));
    expect(await screen.findByText(/none → na fila/)).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Tentar buscar os dados novamente'));

    await waitFor(() => expect(updateHydration).toHaveBeenCalledWith(hydrationJob.id, 'retry'));
  });
});
