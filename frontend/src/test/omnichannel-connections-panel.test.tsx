/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OmnichannelConnectionsPanel } from '@/klm/components/OmnichannelConnectionsPanel';
import { klmApi, type KlmChannelConnection } from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function connection(overrides: Partial<KlmChannelConnection>): KlmChannelConnection {
  return {
    id: 'connection', workspaceId: 'workspace', channel: 'whatsapp', provider: 'meta_cloud',
    connectionKey: 'whatsapp-principal', displayName: 'WhatsApp principal', externalAccountId: null,
    transportMode: 'cloud_api', status: 'configured_local', isDefault: true,
    capabilities: { inbound_messages: { supported: true, enabled: true, state: 'configuration_required' } },
    limits: {}, routingPolicy: {}, health: { state: 'configuration_incomplete', missingConfiguration: ['phoneNumberId'] },
    configuration: {}, credentialReference: null, webhookState: 'not_configured', version: 1,
    verifiedAt: null, connectedAt: null, lastSyncedAt: null,
    createdAt: '2026-07-20T12:00:00Z', updatedAt: '2026-07-20T12:00:00Z',
    ...overrides,
  };
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OmnichannelConnectionsPanel />
    </QueryClientProvider>,
  );
}

describe('OmnichannelConnectionsPanel', () => {
  it('shows multiple official numbers, coexistence and real missing configuration', async () => {
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([
      connection({ id: 'cloud', displayName: 'WhatsApp jurídico' }),
      connection({ id: 'coex', connectionKey: 'whatsapp-comercial', displayName: 'WhatsApp comercial', transportMode: 'coexistence', isDefault: false }),
    ]);
    renderPanel();

    expect(await screen.findByText('WhatsApp jurídico')).toBeInTheDocument();
    expect(screen.getByText('WhatsApp comercial')).toBeInTheDocument();
    expect(screen.getAllByText('2', { selector: 'strong' })).toHaveLength(3);
    expect(screen.getByText('1 em Coexistence')).toBeInTheDocument();
    expect(screen.getAllByText(/Pendente: phoneNumberId/)).toHaveLength(2);
  });

  it('creates a Coexistence connection without exposing an internal cost policy', async () => {
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([]);
    const create = vi.spyOn(klmApi, 'createOmnichannelConnection').mockResolvedValue(
      connection({ id: 'new-coex', displayName: 'Número coexistente', connectionKey: 'whatsapp-numero-coexistente', transportMode: 'coexistence' }),
    );
    renderPanel();

    fireEvent.click(await screen.findByRole('combobox', { name: 'Tipo de conexão' }));
    fireEvent.click(await screen.findByRole('option', { name: 'WhatsApp Coexistence' }));
    const name = screen.getByRole('textbox', { name: 'Nome interno da conexão' });
    fireEvent.change(name, { target: { value: 'Número coexistente' } });
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar conexão' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({
      channel: 'whatsapp',
      provider: 'meta_cloud',
      transportMode: 'coexistence',
      credentialReference: null,
    })));
    expect(create.mock.calls[0]?.[0]).not.toHaveProperty('costPolicy');
    expect(await screen.findByText(/salva no registro local/)).toBeInTheDocument();
  });
});
