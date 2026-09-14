/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import IntegrationsPage from '@/klm/pages/IntegrationsPage';
import {
  klmApi,
  type KlmAdsMeasurementConfig,
  type KlmGoogleDataManagerTrackingStatus,
  type KlmWebhookCenter,
} from '@/klm/api';

vi.mock('@/store/app-context', () => ({
  useOptionalAppStore: () => ({ can: () => true }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const emptyCenter: KlmWebhookCenter = {
  connectionCount: 0,
  configuredConnectionCount: 0,
  activeCredentialCount: 0,
  receiptWindowCount: 0,
  receiptTotalCount: 0,
  receiptMatchCount: 0,
  processedCount: 0,
  awaitingHydrationCount: 0,
  failedCount: 0,
  blockedCount: 0,
  idempotencyEnforced: true,
  externalGateOpen: false,
  connections: [],
  receipts: [],
  generatedAt: '2026-07-21T12:00:00Z',
};

function centerConnection(id: string, displayName: string): KlmWebhookCenter['connections'][number] {
  return {
    connection: {
      id,
      workspaceId: 'workspace-1',
      channel: 'facebook',
      provider: 'meta_ads',
      connectionKey: `meta_ads:${id}`,
      displayName,
      externalAccountId: 'act-123',
      transportMode: 'webhook',
      status: 'configured_local',
      isDefault: false,
      capabilities: {},
      limits: {},
      routingPolicy: {},
      health: {},
      configuration: {},
      credentialReference: null,
      webhookState: 'configured_local',
      version: 1,
      verifiedAt: null,
      connectedAt: null,
      lastSyncedAt: null,
      createdAt: '2026-07-21T12:00:00Z',
      updatedAt: '2026-07-21T12:00:00Z',
    },
    credential: null,
    setupProgress: 40,
    setupSteps: [],
    receiptCount: 0,
    processedCount: 0,
    awaitingHydrationCount: 0,
    failedCount: 0,
    blockedCount: 0,
    latestReceiptAt: null,
  };
}

function renderPage(entry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes><Route path="/app/integracoes" element={<IntegrationsPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('IntegrationsPage', () => {
  it('opens a provider-specific guided setup with measurable local progress', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue(emptyCenter);

    renderPage('/app/integracoes?tab=assistente&provider=tiktok_ads');

    expect(await screen.findByRole('heading', { name: /Integração guiada · TikTok Lead Ads/i })).toBeInTheDocument();
    expect(screen.getByText('LEAD')).toBeInTheDocument();
    expect(screen.getByText('Prontidão local')).toBeInTheDocument();
    expect(screen.getAllByText('0%').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Nenhuma ativação externa ocorre nesta fase/i)).toBeInTheDocument();
    expect(klmApi.webhookCenter).toHaveBeenCalledWith(expect.objectContaining({ provider: 'tiktok_ads' }));
  });

  it('shows an explicit failure instead of presenting unavailable KPIs as zero', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockRejectedValue(new Error('registro local indisponível'));

    renderPage('/app/integracoes');

    expect(await screen.findByRole('alert')).toHaveTextContent('registro local indisponível');
    expect(screen.getByText(/Nenhum indicador abaixo deve ser interpretado como zero/i)).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('shows externally blocked receipts separately from processing failures', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue({
      ...emptyCenter,
      receiptWindowCount: 1,
      receiptTotalCount: 1,
      receiptMatchCount: 1,
      blockedCount: 1,
      connections: [{ ...centerConnection('connection-a', 'Meta Saúde'), blockedCount: 1 }],
    });

    renderPage('/app/integracoes');

    expect(await screen.findByText('Bloqueados pelo gate')).toBeInTheDocument();
    expect(screen.getByText('aguardam liberação externa')).toBeInTheDocument();
    expect(await screen.findByText('1 bloqueado(s)')).toBeInTheDocument();
    expect(screen.queryByText('1 falha(s)')).not.toBeInTheDocument();
  });

  it('labels processed social receipts and links directly to their native conversation', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue({
      ...emptyCenter,
      receiptWindowCount: 1,
      receiptTotalCount: 1,
      receiptMatchCount: 1,
      processedCount: 1,
      receipts: [{
        id: 'receipt-social-1',
        connectionId: 'connection-social-1',
        provider: 'meta_instagram',
        connectionKey: 'instagram:principal',
        externalEventId: 'ig-event-1',
        externalLeadId: null,
        payloadSha256: 'a'.repeat(64),
        signatureScheme: 'hmac_sha256',
        signatureTimestamp: null,
        receivedAt: '2026-07-21T12:00:00Z',
        processingState: 'processed',
        leadCaptureReceiptId: null,
        leadCaptureState: null,
        hydrationJobId: null,
        hydrationState: null,
        threadId: 'thread-social-1',
        matterId: null,
        identityId: 'identity-social-1',
        errorCode: null,
      }],
    });

    renderPage('/app/integracoes?tab=eventos');

    expect(await screen.findByText('Processado', { exact: true })).toBeInTheDocument();
    expect(screen.queryByText('aguardando vínculo')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Conversa/i })).toHaveAttribute(
      'href',
      '/app/conversas?thread=thread-social-1',
    );
  });

  it('requires an explicit account when a provider has multiple connections', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue({
      ...emptyCenter,
      connectionCount: 2,
      connections: [centerConnection('connection-a', 'Meta Saúde'), centerConnection('connection-b', 'Meta Cirurgia')],
    });

    renderPage('/app/integracoes?tab=assistente&provider=meta_ads');

    expect(await screen.findByLabelText('Conexão que será configurada')).toBeInTheDocument();
    expect(screen.getByText(/Escolha explicitamente uma delas/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Proteger assinatura/i })).not.toBeInTheDocument();
  });

  it('clears an unsaved credential when its dialog closes', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue({
      ...emptyCenter,
      connectionCount: 1,
      connections: [centerConnection('connection-a', 'Meta Saúde')],
    });

    renderPage('/app/integracoes?tab=assistente&provider=meta_ads');
    fireEvent.click(await screen.findByRole('button', { name: /Proteger assinatura/i }));
    const secret = screen.getByLabelText('Segredo de assinatura');
    fireEvent.change(secret, { target: { value: 'temporary-secret-that-must-not-survive-close' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));

    fireEvent.click(await screen.findByRole('button', { name: /Proteger assinatura/i }));
    expect(screen.getByLabelText('Segredo de assinatura')).toHaveValue('');
  });

  const unconfiguredMeasurement: KlmAdsMeasurementConfig = {
    configured: false,
    datasetId: null,
    pixelId: null,
    accessTokenConfigured: false,
    accessTokenLast4: null,
    updatedAt: null,
  };

  const unconfiguredGoogleDm: KlmGoogleDataManagerTrackingStatus = {
    configured: false,
    customerId: null,
    hasDeveloperToken: false,
    hasAccessToken: false,
  };

  it('renders the Meta CAPI and Google Data Manager measurement panels as pending when unconfigured', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue(emptyCenter);
    vi.spyOn(klmApi, 'getAdsMeasurementConfig').mockResolvedValue(unconfiguredMeasurement);
    vi.spyOn(klmApi, 'googleDataManagerTrackingStatus').mockResolvedValue(unconfiguredGoogleDm);

    renderPage('/app/integracoes?tab=mensuracao');

    expect(await screen.findByText('Mensuração Meta (CAPI)')).toBeInTheDocument();
    expect(await screen.findByText('Mensuração Google (Data Manager)')).toBeInTheDocument();
    expect(await screen.findAllByText('Pendente')).toHaveLength(2);
  });

  it('saves the Google Data Manager measurement form and reflects the configured status', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue(emptyCenter);
    vi.spyOn(klmApi, 'getAdsMeasurementConfig').mockResolvedValue(unconfiguredMeasurement);
    vi.spyOn(klmApi, 'googleDataManagerTrackingStatus').mockResolvedValue(unconfiguredGoogleDm);
    const savedStatus: KlmGoogleDataManagerTrackingStatus = {
      configured: true,
      customerId: '123-456-7890',
      hasDeveloperToken: true,
      hasAccessToken: true,
    };
    const saveSpy = vi.spyOn(klmApi, 'saveGoogleDataManagerTracking').mockResolvedValue(savedStatus);

    renderPage('/app/integracoes?tab=mensuracao');

    fireEvent.change(await screen.findByLabelText('Customer ID (Google Ads)'), {
      target: { value: '123-456-7890' },
    });
    fireEvent.change(screen.getByLabelText('Developer token'), {
      target: { value: 'dev-token-secret' },
    });
    fireEvent.change(screen.getByLabelText('Access token'), {
      target: { value: 'access-token-secret' },
    });

    const saveButtons = screen.getAllByRole('button', { name: /Salvar mensuração/i });
    await waitFor(() => expect(saveButtons[1]).not.toBeDisabled());
    fireEvent.click(saveButtons[1]);

    await waitFor(() => expect(saveSpy).toHaveBeenCalledTimes(1));
    expect(saveSpy).toHaveBeenCalledWith({
      customerId: '123-456-7890',
      developerToken: 'dev-token-secret',
      accessToken: 'access-token-secret',
    });
    expect(await screen.findAllByText('Configurado')).not.toHaveLength(0);
  });

  it('never renders a raw Google Data Manager secret value on the page', async () => {
    vi.spyOn(klmApi, 'webhookCenter').mockResolvedValue(emptyCenter);
    vi.spyOn(klmApi, 'getAdsMeasurementConfig').mockResolvedValue(unconfiguredMeasurement);
    vi.spyOn(klmApi, 'googleDataManagerTrackingStatus').mockResolvedValue({
      configured: true,
      customerId: '123-456-7890',
      hasDeveloperToken: true,
      hasAccessToken: true,
    });

    renderPage('/app/integracoes?tab=mensuracao');

    await screen.findByLabelText('Customer ID (Google Ads)');
    expect(screen.getByLabelText('Developer token')).toHaveValue('');
    expect(screen.getByLabelText('Access token')).toHaveValue('');
    expect(document.body.textContent).not.toMatch(/dev-token-secret|access-token-secret/i);
  });
});
