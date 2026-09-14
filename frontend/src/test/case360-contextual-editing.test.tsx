/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ConversationCasePanel } from '@/klm/components/ConversationCasePanel';
import { klmApi, type KlmMatterDetail } from '@/klm/api';
import { ApiError } from '@/services/http';

const permissionMock = vi.hoisted(() => vi.fn((_module: string, _action: string) => true));
const toastMock = vi.hoisted(() => vi.fn());

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ can: permissionMock }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}));

vi.mock('@/klm/components/ConversationAuditPanel', () => ({
  ConversationAuditPanel: () => <div data-testid="conversation-audit-placeholder" />,
}));

vi.mock('@/klm/components/MatterFlowJourney', () => ({
  MatterFlowJourney: () => <div data-testid="matter-flow-placeholder" />,
}));

const matterId = '428c5996-f1ab-420f-ba3f-24d20280d452';

function matterDetail(overrides: Partial<KlmMatterDetail> = {}): KlmMatterDetail {
  return {
    id: matterId,
    columnId: 'triagem_sdr',
    microStatus: 'qualificado',
    demandType: 'negativa_plano_saude',
    leadSource: 'google_ads',
    tags: ['oncologia'],
    executiveSummary: 'Negativa em análise.',
    healthSubtheme: 'medicamento_alto_custo',
    healthSubthemeCustom: null,
    healthObjectName: 'Zolgensma',
    healthInsurer: 'Plano local',
    healthDenialReason: 'Fora do rol',
    medicalUrgencyHours: 24,
    economicValueAnnual: 100_000,
    leadScore: 94,
    manualContextRevision: 0,
    manualContextUpdatedAt: null,
    nextAction: 'Validar documentos',
    nextDeadline: '2030-07-18T14:00:00Z',
    docScore: 88,
    productionScore: 40,
    claimValue: 100_000,
    priority: 'alta',
    urgency: 'urgente',
    classification: 'Forte',
    client: {
      id: 'client-local',
      name: 'Ana Paula Ribeiro',
      email: 'ana@example.com',
      phone: '+5561999999999',
    },
    documents: [],
    timeline: [],
    knowledgeChunks: [],
    revenueProjection: null,
    honorariumEstimate: null,
    campaignAttribution: {
      source: 'google_ads',
      medium: 'cpc',
      campaign: 'oncologia-julho',
      channel: 'google_search',
    },
    ...overrides,
  };
}

function mockPanelQueries(matter = matterDetail()) {
  vi.spyOn(klmApi, 'matter').mockResolvedValue(matter);
  vi.spyOn(klmApi, 'meetingsForMatter').mockResolvedValue([]);
  vi.spyOn(klmApi, 'bookingLinksForMatter').mockResolvedValue([]);
  vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([]);
  vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([]);
  vi.spyOn(klmApi, 'listFinanceCharges').mockResolvedValue([]);
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConversationCasePanel
        threadId="thread-local"
        matterId={matterId}
        contact={{ name: 'Ana Paula Ribeiro', score: 94, subtheme: 'Medicamento', object: 'Zolgensma' }}
        internalEntries={[]}
        onTool={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
  toastMock.mockReset();
});

describe('Case 360 contextual editing', () => {
  it('exposes the initial operational fields through local, typed editors', async () => {
    mockPanelQueries();
    renderPanel();

    await screen.findByTestId('conversation-case-360');
    [
      'Nome',
      'WhatsApp',
      'E-mail',
      'Etapa e microstatus',
      'Prioridade e urgência',
      'Próxima ação',
      'Prazo',
      'Origem',
      'Canal',
      'Mídia',
      'Campanha',
      'Demanda',
      'Subtema',
      'Tags',
    ].forEach((label) => expect(screen.getByRole('button', { name: `Editar ${label}` })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Editar Prazo' }));
    expect(screen.getByLabelText('Prazo')).toHaveAttribute('type', 'datetime-local');
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar edição de Prazo' }));

    fireEvent.click(screen.getByRole('button', { name: 'Editar Prioridade e urgência' }));
    expect(screen.getByRole('combobox', { name: 'Prioridade e urgência: primeira opção' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Prioridade e urgência: segunda opção' })).toBeInTheDocument();
    expect(screen.getByText('sem correção manual')).toBeInTheDocument();
  });

  it('enforces both edit permissions before opening or persisting an editor', async () => {
    permissionMock.mockImplementation((module: string, action: string) => (
      !(module === 'lead_data' && action === 'edit')
    ));
    mockPanelQueries();
    const updateCase360 = vi.spyOn(klmApi, 'updateCase360');
    renderPanel();

    const phone = await screen.findByRole('button', { name: 'WhatsApp' });
    expect(phone).toBeDisabled();
    expect(phone).toHaveAttribute('data-read-only', 'true');
    expect(phone).toHaveAttribute('title', 'Somente leitura: sem permissão para editar whatsapp');

    fireEvent.doubleClick(phone);
    expect(screen.queryByRole('textbox', { name: 'WhatsApp' })).not.toBeInTheDocument();
    expect(updateCase360).not.toHaveBeenCalled();
    expect(permissionMock).toHaveBeenCalledWith('kanban', 'edit');
    expect(permissionMock).toHaveBeenCalledWith('lead_data', 'edit');
  });

  it('persists a field through the real Case 360 API contract and renders its revision', async () => {
    mockPanelQueries();
    const updated = matterDetail({
      manualContextRevision: 1,
      manualContextUpdatedAt: '2026-07-22T18:00:00Z',
      client: { ...matterDetail().client, phone: '+5511987654321' },
    });
    const updateCase360 = vi.spyOn(klmApi, 'updateCase360').mockResolvedValue(updated);
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Editar WhatsApp' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'WhatsApp' }), { target: { value: '+5511987654321' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar WhatsApp' }));

    await waitFor(() => expect(updateCase360).toHaveBeenCalledWith(matterId, {
      client: { phone: '+5511987654321' },
      matter: {},
    }));
    expect(await screen.findByText('+5511987654321')).toBeInTheDocument();
    expect(await screen.findByText(/^v1 ·/)).toBeInTheDocument();
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Dado atualizado' }));
  });

  it('routes closed-domain stage changes and attribution corrections to their audited endpoints', async () => {
    mockPanelQueries();
    const moved = matterDetail({
      columnId: 'documentos',
      microStatus: 'aguardando_docs',
      manualContextRevision: 1,
    });
    const attributed = matterDetail({
      leadSource: 'meta_ads',
      manualContextRevision: 2,
      campaignAttribution: {
        ...matterDetail().campaignAttribution!,
        source: 'meta_ads',
      },
    });
    const moveMatter = vi.spyOn(klmApi, 'moveMatter').mockResolvedValue(moved);
    const updateCase360 = vi.spyOn(klmApi, 'updateCase360').mockResolvedValue(attributed);
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Editar Etapa e microstatus' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Etapa e microstatus: primeira opção' }), { target: { value: 'documentos' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Etapa e microstatus: segunda opção' }), { target: { value: 'aguardando_docs' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar Etapa e microstatus' }));

    await waitFor(() => expect(moveMatter).toHaveBeenCalledWith(matterId, {
      columnId: 'documentos',
      microStatus: 'aguardando_docs',
    }));
    fireEvent.click(await screen.findByRole('button', { name: 'Editar Origem' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Origem' }), { target: { value: 'meta_ads' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar Origem' }));

    await waitFor(() => expect(updateCase360).toHaveBeenCalledWith(matterId, {
      attribution: { source: 'meta_ads' },
      matter: { leadSource: 'meta_ads' },
    }));
  });

  it('keeps the user draft open when the backend rejects a concurrent revision', async () => {
    mockPanelQueries();
    vi.spyOn(klmApi, 'updateCase360').mockRejectedValue(new ApiError(409, 'O caso foi alterado por outra pessoa.'));
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Editar E-mail' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'E-mail' }), { target: { value: 'novo@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar E-mail' }));

    expect((await screen.findAllByText('O caso foi alterado por outra pessoa.')).length).toBeGreaterThan(0);
    expect(screen.getByRole('textbox', { name: 'E-mail' })).toHaveValue('novo@example.com');
    expect(screen.getByRole('button', { name: 'Salvar E-mail' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar edição de E-mail' }));
    expect(screen.getByText('ana@example.com')).toBeInTheDocument();
  });
});
