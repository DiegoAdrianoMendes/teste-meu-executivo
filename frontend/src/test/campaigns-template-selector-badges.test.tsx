/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Campaigns from '@/pages/Campaigns';
import type { MetaTemplate } from '@/types';

const campaignServiceMocks = vi.hoisted(() => ({
  addAudienceListMember: vi.fn(),
  analyzeMetaTemplateDraft: vi.fn(),
  createAudienceList: vi.fn(),
  createCampaign: vi.fn(),
  downloadAudienceListCsv: vi.fn(),
  fetchAudienceList: vi.fn(),
  fetchAudienceLists: vi.fn(),
  fetchCampaigns: vi.fn(),
  fetchMetaCommercialCharges: vi.fn(),
  fetchMetaTemplates: vi.fn(),
  fetchOptIns: vi.fn(),
  fetchSuppressions: vi.fn(),
  importAudienceListCsv: vi.fn(),
  launchCampaign: vi.fn(),
  pauseCampaign: vi.fn(),
  resumeCampaign: vi.fn(),
  saveOptIn: vi.fn(),
  saveSuppression: vi.fn(),
  stopCampaign: vi.fn(),
  syncMetaTemplates: vi.fn(),
  updateAudienceList: vi.fn(),
}));

const whatsappServiceMocks = vi.hoisted(() => ({
  fetchConversationAssignees: vi.fn(),
  fetchWhatsAppConnections: vi.fn(),
}));

const klmApiMocks = vi.hoisted(() => ({
  flows: vi.fn(),
  areas: vi.fn(),
  omnichannelConnections: vi.fn(),
}));

vi.mock('@/services/campaigns', () => campaignServiceMocks);
vi.mock('@/services/whatsapp', () => whatsappServiceMocks);
vi.mock('@/klm/api', () => ({
  klmApi: {
    flows: klmApiMocks.flows,
    areas: klmApiMocks.areas,
    omnichannelConnections: klmApiMocks.omnichannelConnections,
  },
}));
vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ can: () => true }),
}));
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));
vi.mock('@/components/common/PersonalizedFilterBuilder', () => ({
  PersonalizedFilterBuilder: () => <button type="button">Abrir filtros de campanhas</button>,
}));

function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

function renderCampaigns() {
  const client = createQueryClient();
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <Campaigns />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const approvedTemplate: MetaTemplate = {
  id: 'tpl-1',
  whatsappConnectionId: 'conn-1',
  externalTemplateId: 'ext-1',
  name: 'atualizacao_processo',
  category: 'marketing',
  language: 'pt_BR',
  status: 'approved',
  qualityRating: 'GREEN',
  components: [],
  rawPayload: {},
  lastSyncedAt: '2026-07-22T12:00:00Z',
  updatedAt: '2026-07-22T12:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  campaignServiceMocks.fetchCampaigns.mockResolvedValue([]);
  campaignServiceMocks.fetchMetaCommercialCharges.mockResolvedValue(null);
  campaignServiceMocks.fetchOptIns.mockResolvedValue([]);
  campaignServiceMocks.fetchSuppressions.mockResolvedValue([]);
  campaignServiceMocks.fetchAudienceLists.mockResolvedValue([]);
  campaignServiceMocks.fetchMetaTemplates.mockResolvedValue([approvedTemplate]);
  whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValue([
    { id: 'conn-1', connectionType: 'OFFICIAL_MANUAL', isDefault: true, phoneNumber: '5511999990000', status: 'connected' },
  ]);
  whatsappServiceMocks.fetchConversationAssignees.mockResolvedValue([]);
  klmApiMocks.flows.mockResolvedValue([]);
  klmApiMocks.areas.mockResolvedValue([]);
  klmApiMocks.omnichannelConnections.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
});

describe('Campaigns: seletor de template com badges de categoria e quality', () => {
  it('mantem o valor selecionado em uma linha e renderiza os badges fora do gatilho', async () => {
    renderCampaigns();

    const trigger = await screen.findByLabelText('Template aprovado da Meta');
    const classification = await screen.findByLabelText('Classificação do template selecionado');

    expect(trigger).toHaveTextContent('atualizacao_processo · pt_BR · approved');
    expect(trigger).toHaveClass('min-w-0', 'overflow-hidden');
    expect(within(trigger).queryByText('marketing (~7x)')).not.toBeInTheDocument();
    expect(within(classification).getByText('marketing (~7x)')).toBeInTheDocument();
    expect(within(classification).getByText(/qualidade green/i)).toBeInTheDocument();
  });

  it('mostra um campo por variável com exemplos, ignora templates não aprovados e envia arrays', async () => {
    const variableTemplate: MetaTemplate = {
      ...approvedTemplate,
      id: 'tpl-with-variables',
      name: 'aviso_prazo',
      components: [
        { type: 'HEADER', format: 'TEXT', text: 'Olá {{1}}', example: { header_text: ['Escritório Exemplo'] } },
        { type: 'BODY', text: 'Seu prazo é {{1}} e o link é {{2}}.', example: { body_text: [['30/07', 'https://exemplo.test']] } },
      ],
    };
    campaignServiceMocks.fetchMetaTemplates.mockResolvedValue([
      variableTemplate,
      { ...variableTemplate, id: 'tpl-pending', name: 'rascunho_pendente', status: 'pending' },
    ]);
    campaignServiceMocks.createCampaign.mockResolvedValue({});
    renderCampaigns();

    expect(await screen.findByText(/aviso_prazo/)).toBeInTheDocument();
    expect(screen.queryByText(/rascunho_pendente/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Etapa 2 Automação/ }));

    expect(screen.getByLabelText('Parâmetros do cabeçalho {{1}}')).toHaveAttribute('placeholder', 'Exemplo: Escritório Exemplo');
    expect(screen.getByText('Exemplo do template: Escritório Exemplo')).toBeInTheDocument();
    expect(screen.getByLabelText('Parâmetros da mensagem {{1}}')).toHaveAttribute('placeholder', 'Exemplo: 30/07');
    expect(screen.getByLabelText('Parâmetros da mensagem {{2}}')).toHaveAttribute('placeholder', 'Exemplo: https://exemplo.test');

    fireEvent.change(screen.getByLabelText('Parâmetros do cabeçalho {{1}}'), { target: { value: 'Meu escritório' } });
    fireEvent.change(screen.getByLabelText('Parâmetros da mensagem {{1}}'), { target: { value: '05/08' } });
    fireEvent.change(screen.getByLabelText('Parâmetros da mensagem {{2}}'), { target: { value: 'https://meu.exemplo/prazo' } });
    fireEvent.change(screen.getByLabelText('Título da campanha'), { target: { value: 'Aviso de prazo' } });
    fireEvent.click(screen.getByRole('button', { name: /Etapa 4 Revisão/ }));
    expect(screen.getByText('CRM')).toBeInTheDocument();
    expect(screen.queryByText(/0 lista\(s\)/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Criar campanha em rascunho' }));

    await waitFor(() => expect(campaignServiceMocks.createCampaign).toHaveBeenCalledOnce());
    expect(campaignServiceMocks.createCampaign).toHaveBeenCalledWith(expect.objectContaining({
      headerParameters: ['Meu escritório'],
      bodyParameters: ['05/08', 'https://meu.exemplo/prazo'],
    }));
    expect(screen.queryByText(/complete número oficial, template e título/i)).not.toBeInTheDocument();
  });
});
