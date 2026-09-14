/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PlatformGrowthCrmPanel } from '@/components/platform/PlatformGrowthCrmPanel';
import type { GrowthFunnelMetrics, GrowthProspect } from '@/services/platform';

vi.mock('recharts', () => ({
  Bar: () => null,
  BarChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  Cell: () => null,
  Pie: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  PieChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const growthApi = vi.hoisted(() => ({
  fetchProspects: vi.fn(),
  fetchMetrics: vi.fn(),
  fetchProspect: vi.fn(),
  createProspect: vi.fn(),
  moveStage: vi.fn(),
  createActivity: vi.fn(),
  updateProspect: vi.fn(),
  fetchAssignees: vi.fn(),
  convertProspect: vi.fn(),
}));

const toastApi = vi.hoisted(() => ({ toast: vi.fn() }));

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>('@/services/platform');
  return {
    ...actual,
    fetchGrowthProspects: growthApi.fetchProspects,
    fetchGrowthFunnelMetrics: growthApi.fetchMetrics,
    fetchGrowthProspect: growthApi.fetchProspect,
    createGrowthProspect: growthApi.createProspect,
    moveGrowthProspectStage: growthApi.moveStage,
    createGrowthActivity: growthApi.createActivity,
    updateGrowthProspect: growthApi.updateProspect,
    fetchGrowthSalesAssignees: growthApi.fetchAssignees,
    convertGrowthProspectToWorkspace: growthApi.convertProspect,
  };
});

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastApi.toast }),
}));

const prospectId = '11111111-1111-4111-8111-111111111111';

const prospectAlpha: GrowthProspect = {
  id: prospectId,
  nome_escritorio: 'Escritório Alpha',
  contato_nome: 'Ana Contato',
  contato_email: 'ana@alpha.adv.br',
  contato_telefone: '61999990000',
  cidade: 'Brasília',
  uf: 'DF',
  tamanho_estimado: null,
  origem: 'outbound',
  estagio: 'prospect',
  valor_estimado_cents: 49700,
  valor_estimado_brl: 497,
  motivo_perda: null,
  notas: null,
  owner_user_id: null,
  owner_name: null,
  workspace_id: null,
  stage_entered_at: '2026-07-20T12:00:00Z',
  won_at: null,
  lost_at: null,
  created_at: '2026-07-20T12:00:00Z',
  updated_at: '2026-07-20T12:00:00Z',
  score: 70,
  next_action: 'Agendar demonstração',
  next_action_at: '2026-07-30T15:00:00Z',
  campaign: 'Inbound julho',
  utm_source: 'google',
  utm_medium: 'cpc',
  utm_campaign: 'crm-juridico',
  proposal_status: null,
  proposal_reference: null,
  proposal_value_cents: null,
};

const emptyBoard = {
  prospect: [prospectAlpha],
  contatado: [],
  demo_agendada: [],
  trial: [],
  proposta: [],
  pagante: [],
  perdido: [],
};

const metrics: GrowthFunnelMetrics = {
  total_prospects: 1,
  won: 0,
  lost: 0,
  win_rate_pct: 0,
  conversion_prospect_to_pagante_pct: 0,
  pipeline_estimado_cents: 49700,
  pipeline_estimado_brl: 497,
  by_stage: { prospect: { count: 1, valor_estimado_cents: 49700 } },
  by_origem: { outbound: 1 },
  avg_days_per_stage: {},
  period_days: 90,
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><PlatformGrowthCrmPanel enabled /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  growthApi.fetchProspects.mockReset().mockImplementation(({ estagio, offset = 0 }: { estagio?: string; offset?: number }) => {
    const items = estagio === 'prospect' ? emptyBoard.prospect : [];
    return Promise.resolve({
      items,
      count: items.length,
      total: items.length,
      limit: 25,
      offset,
      hasMore: false,
    });
  });
  growthApi.fetchMetrics.mockReset().mockResolvedValue(metrics);
  growthApi.fetchProspect.mockReset().mockResolvedValue({ ...prospectAlpha, activities: [], stage_events: [] });
  growthApi.createProspect.mockReset();
  growthApi.moveStage.mockReset().mockResolvedValue({ ...prospectAlpha, estagio: 'contatado' });
  growthApi.createActivity.mockReset();
  growthApi.updateProspect.mockReset().mockResolvedValue(prospectAlpha);
  growthApi.fetchAssignees.mockReset().mockResolvedValue([
    { id: 'seller-1', name: 'SDR Um', role_package: 'sales_sdr' },
  ]);
  growthApi.convertProspect.mockReset().mockResolvedValue({
    prospect_id: prospectId,
    workspace_id: 'workspace-new',
    workspace_name: 'Escritório Alpha',
    workspace_status: 'provisioning',
    invitation_id: 'invite-1',
    invitation_status: 'pending',
    already_converted: false,
  });
  toastApi.toast.mockReset();
});

afterEach(() => {
  cleanup();
});

describe('PlatformGrowthCrmPanel', () => {
  it('renders the kanban board grouped by stage', async () => {
    renderPanel();

    await waitFor(() => expect(screen.getByText('Escritório Alpha')).toBeInTheDocument());
    expect(screen.getByText('Prospect')).toBeInTheDocument();
    expect(screen.getByText('Contatado')).toBeInTheDocument();
  });

  it('paginates each stage independently through the server', async () => {
    growthApi.fetchProspects.mockImplementation(({ estagio, offset = 0 }: { estagio?: string; offset?: number }) => Promise.resolve({
      items: estagio === 'prospect' ? [{ ...prospectAlpha, id: `${prospectId}-${offset}`, nome_escritorio: `Conta ${offset + 1}` }] : [],
      count: estagio === 'prospect' ? 30 : 0,
      total: estagio === 'prospect' ? 30 : 0,
      limit: 25,
      offset,
      hasMore: estagio === 'prospect' && offset === 0,
    }));

    renderPanel();
    await screen.findByText('Conta 1');
    expect(screen.getByText('1–1 de 30')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));

    await waitFor(() => expect(growthApi.fetchProspects).toHaveBeenCalledWith(
      expect.objectContaining({ estagio: 'prospect', limit: 25, offset: 25 }),
    ));
    await screen.findByText('Conta 26');
  });

  it('renders funnel metrics', async () => {
    renderPanel();

    await waitFor(() => expect(screen.getByText('Win rate')).toBeInTheDocument());
    const percentages = await screen.findAllByText('0%');
    expect(percentages.length).toBeGreaterThan(0);
  });

  it('does not replace an unavailable KPI source with zero', async () => {
    growthApi.fetchMetrics.mockRejectedValue(new Error('fonte indisponível'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('Nenhum zero substitutivo');
    expect(screen.getAllByText('—')).toHaveLength(4);
    expect(screen.getAllByText('Indicador indisponível.')).toHaveLength(2);
  });

  it('moves a prospect to the next stage', async () => {
    renderPanel();

    await waitFor(() => expect(screen.getByText('Escritório Alpha')).toBeInTheDocument());
    const moveButton = screen.getByRole('button', { name: /Mover para Contatado/i });
    fireEvent.click(moveButton);

    await waitFor(() =>
      expect(growthApi.moveStage).toHaveBeenCalledWith(
        prospectId,
        expect.objectContaining({ toEstagio: 'contatado' }),
      ),
    );
  });

  it('opens the new prospect dialog and submits it', async () => {
    growthApi.createProspect.mockResolvedValue({ ...prospectAlpha, id: 'new-id', nome_escritorio: 'Escritório Novo' });
    renderPanel();

    await waitFor(() => expect(screen.getByText('Escritório Alpha')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Novo prospect/i }));

    const nameInput = await screen.findByLabelText(/Nome do escritório/i);
    fireEvent.change(nameInput, { target: { value: 'Escritório Novo' } });

    fireEvent.click(screen.getByRole('button', { name: /^Criar prospect$/i }));

    await waitFor(() => expect(growthApi.createProspect).toHaveBeenCalled());
  });

  it('requires a reason before marking a prospect as lost', async () => {
    growthApi.moveStage.mockResolvedValue({ ...prospectAlpha, estagio: 'perdido', motivo_perda: 'Sem orçamento.' });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Marcar como perdido' }));

    const confirm = screen.getByRole('button', { name: 'Confirmar perda' });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo da perda'), { target: { value: 'Sem orçamento neste trimestre.' } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(growthApi.moveStage).toHaveBeenCalledWith(prospectId, {
      toEstagio: 'perdido',
      motivo: 'Sem orçamento neste trimestre.',
    }));
  });

  it('edits prospect data from the detail drawer', async () => {
    renderPanel();
    fireEvent.click(await screen.findByText('Escritório Alpha'));
    fireEvent.click(await screen.findByRole('button', { name: 'Editar cadastro' }));
    fireEvent.change(screen.getByLabelText('Cidade'), { target: { value: 'Goiânia' } });
    fireEvent.change(screen.getByLabelText('Valor estimado (R$/mês)'), { target: { value: '799' } });
    fireEvent.change(screen.getByLabelText('Score (0–100)'), { target: { value: '91' } });
    fireEvent.change(screen.getByLabelText('Próxima ação'), { target: { value: 'Reunião com sócios' } });
    fireEvent.change(screen.getByLabelText('Valor da proposta (R$/mês)'), { target: { value: '1299' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar alterações' }));

    await waitFor(() => expect(growthApi.updateProspect).toHaveBeenCalledWith(
      prospectId,
      expect.objectContaining({
        cidade: 'Goiânia',
        valorEstimadoCents: 79900,
        score: 91,
        nextAction: 'Reunião com sócios',
        proposalValueCents: 129900,
      }),
    ));
  });

  it('converts a prospect into a workspace with an owner invitation', async () => {
    renderPanel();
    fireEvent.click(await screen.findByText('Escritório Alpha'));
    fireEvent.click(await screen.findByRole('button', { name: 'Converter em conta' }));
    const submit = screen.getByRole('button', { name: 'Criar conta e convidar titular' });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(growthApi.convertProspect).toHaveBeenCalledWith(prospectId, {
      workspaceName: 'Escritório Alpha',
      slug: undefined,
      ownerName: 'Ana Contato',
      ownerEmail: 'ana@alpha.adv.br',
      planKey: 'pro',
      setupMode: 'assisted',
    }));
  });
});
