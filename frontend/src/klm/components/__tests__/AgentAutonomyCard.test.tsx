/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { AgentAutonomyCard } from '@/klm/components/AgentAutonomyCard';
import { klmApi, type KlmAgent, type KlmAgentAutonomySetting } from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const renderWithClient = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

const AGENT_FIXTURE: KlmAgent = {
  key: 'cobranca',
  name: 'Cobrança',
  role: 'Agente de cobrança e negociação',
  group: 'vendas',
  state: 'active',
  qualityScore: 92,
  queueSize: 0,
  description: 'Negocia acordos de pagamento com o cliente.',
  tools: [],
  gate: 'Assinatura sempre humana.',
  permissions: {
    readMatter: true,
    writeCrm: true,
    createDraft: true,
    sendExternal: false,
    requestHuman: true,
  },
  modelPolicy: {
    strategy: 'cost',
    provider: 'anthropic',
    model: 'claude',
    taskClass: 'negotiation',
    modelApiEnabled: true,
    localModelEnabled: false,
    simulatorAvailable: true,
    channelDeliveryEnabled: false,
    externalEnabled: false,
  },
};

const SETTING_FIXTURE: KlmAgentAutonomySetting = {
  agentKey: 'cobranca',
  autonomyMode: 'hil_total',
  negotiationFloorPct: null,
  negotiationBandPct: null,
  etag: 'etag-1',
};

describe('AgentAutonomyCard (Frente B — autonomia HIL)', () => {
  it('renders the agent row with its current autonomy mode', async () => {
    vi.spyOn(klmApi, 'agentAutonomySettings').mockResolvedValue([SETTING_FIXTURE]);

    renderWithClient(<AgentAutonomyCard agents={[AGENT_FIXTURE]} canManage />);

    expect(await screen.findByText('Autonomia por agente')).toBeInTheDocument();
    expect(await screen.findByText('Cobrança')).toBeInTheDocument();
    expect(screen.getByText(/fora da banda pré-aprovada/i)).toBeInTheDocument();
  });

  it('saves the autonomy mode and negotiation band for an agent', async () => {
    vi.spyOn(klmApi, 'agentAutonomySettings').mockResolvedValue([SETTING_FIXTURE]);
    const setAutonomy = vi.spyOn(klmApi, 'setAgentAutonomy').mockResolvedValue({
      agentKey: 'cobranca',
      autonomyMode: 'autonomo',
      negotiationFloorPct: 0.1,
      negotiationBandPct: 0.15,
      etag: 'etag-2',
    });

    renderWithClient(<AgentAutonomyCard agents={[AGENT_FIXTURE]} canManage />);

    await screen.findByText('Cobrança');
    const modeTrigger = screen.getByRole('combobox', { name: 'Modo de autonomia de Cobrança' });
    fireEvent.click(modeTrigger);
    fireEvent.click(screen.getByRole('option', { name: 'Autônomo (banda)' }));

    const floorInput = screen.getByLabelText('Piso de negociação de Cobrança') as HTMLInputElement;
    const bandInput = screen.getByLabelText('Banda de negociação de Cobrança') as HTMLInputElement;
    fireEvent.change(floorInput, { target: { value: '10' } });
    fireEvent.change(bandInput, { target: { value: '15' } });

    const saveButton = screen.getByRole('button', { name: /salvar/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(setAutonomy).toHaveBeenCalledTimes(1);
    });
    const [agentKey, payload] = setAutonomy.mock.calls[0];
    expect(agentKey).toBe('cobranca');
    expect(payload.autonomyMode).toBe('autonomo');
    expect(payload.negotiationFloorPct).toBeCloseTo(0.1);
    expect(payload.negotiationBandPct).toBeCloseTo(0.15);
  });

  it('hides the save action for viewers without finance/agents management', async () => {
    vi.spyOn(klmApi, 'agentAutonomySettings').mockResolvedValue([SETTING_FIXTURE]);

    renderWithClient(<AgentAutonomyCard agents={[AGENT_FIXTURE]} canManage={false} />);

    await screen.findByText('Cobrança');
    expect(screen.queryByRole('button', { name: /salvar/i })).not.toBeInTheDocument();
    expect(screen.getByText('Somente leitura')).toBeInTheDocument();
  });
});
