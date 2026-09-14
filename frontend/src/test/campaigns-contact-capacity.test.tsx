/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Campaigns from '@/pages/Campaigns';
import { ApiError } from '@/services/http';
import type {
  EffectiveCapability,
  EffectiveCapabilityManifest,
} from '@/services/platform-commercial-catalog';
import type { WorkspaceCapabilityManifestStatus } from '@/services/workspace-capabilities';
import type { AudienceList, WhatsAppCampaign } from '@/types';

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
}));

const toastMock = vi.hoisted(() => vi.fn());
const appStoreState = vi.hoisted(() => ({
  manifestStatus: 'ready' as WorkspaceCapabilityManifestStatus,
  manifest: null as EffectiveCapabilityManifest | null,
  capability: null as EffectiveCapability | null,
}));

vi.mock('@/services/campaigns', () => campaignServiceMocks);
vi.mock('@/services/whatsapp', () => whatsappServiceMocks);
vi.mock('@/klm/api', () => ({
  klmApi: {
    flows: klmApiMocks.flows,
    areas: klmApiMocks.areas,
  },
}));
vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: (module: string) => module === 'configuracoes',
    capabilityManifest: appStoreState.manifest,
    capabilityManifestStatus: appStoreState.manifestStatus,
    getCapability: (key: string) => key === 'contacts' ? appStoreState.capability : null,
  }),
}));
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}));
vi.mock('@/components/common/PersonalizedFilterBuilder', () => ({
  PersonalizedFilterBuilder: () => <button type="button">Abrir filtros de campanhas</button>,
}));

const audienceList: AudienceList = {
  id: 'list-1',
  createdById: 'user-1',
  name: 'Lista importada',
  description: 'Pode conter telefones que ainda não são contatos canônicos.',
  source: 'csv',
  status: 'active',
  memberCount: 1,
  validCount: 1,
  invalidCount: 0,
  duplicateCount: 0,
  importMetadata: {},
  members: [{
    id: 'member-1',
    contactId: null,
    matterId: null,
    phone: '5511999990000',
    name: 'Contato da lista',
    email: null,
    status: 'valid',
    memberMetadata: {},
    createdAt: '2026-07-30T12:00:00Z',
  }],
  sensitiveDataHidden: false,
  createdAt: '2026-07-30T12:00:00Z',
  updatedAt: '2026-07-30T12:00:00Z',
};

const listCampaign: WhatsAppCampaign = {
  id: 'campaign-1',
  whatsappConnectionId: 'connection-1',
  createdById: 'user-1',
  followUpFlowId: null,
  flowStartMode: 'manual',
  title: 'Campanha com lista',
  description: 'Campanha existente continua visível.',
  status: 'draft',
  scheduledAt: null,
  launchedAt: null,
  completedAt: null,
  pausedAt: null,
  totalRecipients: 1,
  eligibleRecipients: 1,
  blockedRecipients: 0,
  sentCount: 0,
  deliveredCount: 0,
  readCount: 0,
  failedCount: 0,
  audience: {
    id: 'audience-1',
    filters: {
      audience_list_ids: ['list-1'],
      include_crm_contacts: false,
    },
    estimatedRecipients: 1,
    sampleRecipients: [],
  },
  templateSnapshot: {
    id: 'template-snapshot-1',
    sourceTemplateId: 'template-1',
    templateName: 'campanha_lista',
    category: 'marketing',
    language: 'pt_BR',
    status: 'approved',
    headerParameters: [],
    bodyParameters: [],
    components: [],
    rawPayload: {},
  },
  sampleRecipients: [],
  createdAt: '2026-07-30T12:00:00Z',
  updatedAt: '2026-07-30T12:00:00Z',
};

function contactsCapability(
  overrides: Partial<EffectiveCapability> = {},
): EffectiveCapability {
  return {
    key: 'contacts',
    contracted: true,
    enabled: true,
    permitted: true,
    limit: 1_000,
    graceLimit: null,
    used: 120,
    remaining: 880,
    unit: 'contacts',
    period: 'stock',
    enforcement: 'hard',
    sources: [{ type: 'plan', id: 'line-1', contribution: 1_000 }],
    denialReason: null,
    upgradeOffer: null,
    ...overrides,
  };
}

function setReadyCapability(capability: EffectiveCapability) {
  appStoreState.manifestStatus = 'ready';
  appStoreState.capability = capability;
  appStoreState.manifest = {
    workspaceId: 'workspace-1',
    contractId: 'contract-1',
    contractVersion: 3,
    contractStatus: 'active',
    activeProducts: ['crm_start'],
    capabilities: { contacts: capability } as EffectiveCapabilityManifest['capabilities'],
    generatedAt: '2026-07-30T15:00:00Z',
  };
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
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

beforeEach(() => {
  vi.clearAllMocks();
  setReadyCapability(contactsCapability());
  campaignServiceMocks.fetchCampaigns.mockResolvedValue([listCampaign]);
  campaignServiceMocks.fetchMetaCommercialCharges.mockResolvedValue(null);
  campaignServiceMocks.fetchOptIns.mockResolvedValue([]);
  campaignServiceMocks.fetchSuppressions.mockResolvedValue([]);
  campaignServiceMocks.fetchAudienceLists.mockResolvedValue([audienceList]);
  campaignServiceMocks.fetchAudienceList.mockResolvedValue(audienceList);
  campaignServiceMocks.fetchMetaTemplates.mockResolvedValue([]);
  campaignServiceMocks.importAudienceListCsv.mockResolvedValue({
    audienceList,
    imported: 1,
    updated: 0,
    invalid: 0,
    duplicates: 0,
    totalRows: 1,
    optInsRecorded: 0,
  });
  campaignServiceMocks.launchCampaign.mockResolvedValue(listCampaign);
  whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValue([]);
  whatsappServiceMocks.fetchConversationAssignees.mockResolvedValue([]);
  klmApiMocks.flows.mockResolvedValue([]);
  klmApiMocks.areas.mockResolvedValue([]);
});

afterEach(() => cleanup());

describe('Campaigns: capacidade de contatos', () => {
  it('mostra uso, limite e saldo e lança quando a quota está disponível', async () => {
    renderCampaigns();

    expect(await screen.findByText('Disponível')).toBeInTheDocument();
    expect(screen.getByText('Em uso').parentElement).toHaveTextContent('120');
    expect(screen.getByText('Limite contratado').parentElement).toHaveTextContent('1.000');
    expect(screen.getByText('Saldo regular').parentElement).toHaveTextContent('880');

    const launchButton = await screen.findByRole('button', { name: 'Lançar' });
    expect(launchButton).toBeEnabled();
    fireEvent.click(launchButton);

    await waitFor(() => expect(campaignServiceMocks.launchCampaign).toHaveBeenCalledWith(
      'campaign-1',
      { dryRun: false },
    ));
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Lançamento aceito para processamento.',
      description: expect.stringContaining('backend revalidou'),
    }));
  });

  it('avisa sobre a quota esgotada e deixa o backend decidir o lançamento no preflight', async () => {
    setReadyCapability(contactsCapability({
      permitted: false,
      used: 1_000,
      remaining: 0,
      denialReason: 'quota_exhausted',
      upgradeOffer: 'crm_advanced',
    }));
    renderCampaigns();

    expect(await screen.findByText('Limite atingido')).toBeInTheDocument();
    expect(screen.getByText('Campanha com lista')).toBeInTheDocument();
    expect(screen.getByText('Saldo regular').parentElement).toHaveTextContent('0');
    expect(screen.getByText(/O backend fará o preflight/)).toBeInTheDocument();
    const launchButton = screen.getByRole('button', { name: 'Lançar' });
    expect(launchButton).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Dry-run' })).toBeEnabled();
    expect(screen.getByRole('link', { name: 'Abrir sem retorno' })).toBeInTheDocument();
    fireEvent.click(launchButton);

    await waitFor(() => expect(campaignServiceMocks.launchCampaign).toHaveBeenCalledWith(
      'campaign-1',
      { dryRun: false },
    ));

    const fileInput = await screen.findByLabelText('Arquivo CSV da lista');
    fireEvent.change(fileInput, {
      target: { files: [new File(['telefone\\n5511999990000'], 'lista.csv', { type: 'text/csv' })] },
    });
    const importButton = screen.getByRole('button', { name: 'Importar' });
    expect(importButton).toBeEnabled();
    fireEvent.click(importButton);

    await waitFor(() => expect(campaignServiceMocks.importAudienceListCsv).toHaveBeenCalled());
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'CSV processado.',
      description: expect.stringContaining('não presume quantos contatos canônicos são novos'),
    }));
  });

  it('desabilita Lançar fora do rascunho e sem elegíveis, sem esconder o botão', async () => {
    campaignServiceMocks.fetchCampaigns.mockResolvedValue([
      listCampaign,
      { ...listCampaign, id: 'campaign-2', title: 'Campanha concluída', status: 'completed' },
      { ...listCampaign, id: 'campaign-3', title: 'Campanha vazia', eligibleRecipients: 0 },
    ]);
    renderCampaigns();

    const rowOf = (title: string) => {
      const row = screen.getByText(title).closest('.rounded-2xl');
      if (!(row instanceof HTMLElement)) throw new Error(`linha não encontrada: ${title}`);
      return within(row);
    };

    await screen.findByText('Campanha vazia');
    expect(rowOf(listCampaign.title).getByRole('button', { name: 'Lançar' })).toBeEnabled();

    const completedLaunch = rowOf('Campanha concluída').getByRole('button', { name: 'Lançar' });
    expect(completedLaunch).toBeDisabled();
    expect(completedLaunch).toHaveAttribute('title', expect.stringContaining('rascunho'));
    expect(rowOf('Campanha concluída').getByRole('button', { name: 'Dry-run' })).toBeEnabled();

    const emptyRow = rowOf('Campanha vazia');
    expect(emptyRow.getByRole('button', { name: 'Lançar' })).toBeDisabled();
    expect(emptyRow.getByRole('status')).toHaveTextContent(/nenhum destinatário elegível/i);
  });

  it('mostra erro com retry em vez de coluna vazia quando listas, flows, opt-ins e supressões falham (V17)', async () => {
    campaignServiceMocks.fetchAudienceLists.mockRejectedValue(new Error('listas indisponíveis'));
    campaignServiceMocks.fetchOptIns.mockRejectedValue(new Error('opt-ins indisponíveis'));
    campaignServiceMocks.fetchSuppressions.mockRejectedValue(new Error('supressões indisponíveis'));
    klmApiMocks.flows.mockRejectedValue(new Error('flows indisponíveis'));
    renderCampaigns();

    expect(await screen.findByRole('status', { name: 'Erro ao carregar listas' })).toHaveTextContent('Não foi possível carregar as listas.');
    expect(await screen.findByRole('status', { name: 'Erro ao carregar listas de audiência' })).toBeInTheDocument();
    expect(await screen.findByRole('status', { name: 'Erro ao carregar flows' })).toHaveTextContent(/não foi possível carregar os flows/i);
    const optInsError = await screen.findByRole('status', { name: 'Erro ao carregar opt-ins' });
    expect(await screen.findByRole('status', { name: 'Erro ao carregar supressões' })).toBeInTheDocument();
    expect(screen.queryByText('Crie a primeira lista para importar contatos.')).not.toBeInTheDocument();
    expect(screen.queryByText('Nenhum opt-in registrado.')).not.toBeInTheDocument();
    expect(screen.queryByText('Nenhuma supressão registrada.')).not.toBeInTheDocument();
    expect(screen.getByText('Campanha com lista')).toBeInTheDocument();

    fireEvent.click(within(optInsError).getByRole('button', { name: 'Tentar novamente' }));
    await waitFor(() => expect(campaignServiceMocks.fetchOptIns).toHaveBeenCalledTimes(2));
  });

  it('mostra erro com retry quando a lista selecionada falha (V17)', async () => {
    campaignServiceMocks.fetchAudienceList.mockRejectedValue(new Error('lista indisponível'));
    renderCampaigns();

    const selectedError = await screen.findByRole('status', { name: 'Erro ao carregar lista selecionada' });
    expect(selectedError).toHaveTextContent('Não foi possível carregar a lista selecionada.');
    expect(screen.queryByText('Selecione ou crie uma lista.')).not.toBeInTheDocument();

    fireEvent.click(within(selectedError).getByRole('button', { name: 'Tentar novamente' }));
    await waitFor(() => expect(campaignServiceMocks.fetchAudienceList).toHaveBeenCalledTimes(2));
  });

  it('diz que não há opt-ins nem supressões quando a consulta responde vazia (V17)', async () => {
    renderCampaigns();

    expect(await screen.findByText('Nenhum opt-in registrado.')).toBeInTheDocument();
    expect(screen.getByText('Nenhuma supressão registrada.')).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: /erro ao carregar/i })).not.toBeInTheDocument();
  });

  it('explica contrato ausente sem ocultar campanhas, listas ou dry-run', async () => {
    appStoreState.manifestStatus = 'not_configured';
    appStoreState.manifest = null;
    appStoreState.capability = null;
    renderCampaigns();

    expect(await screen.findByText('Contrato não configurado')).toBeInTheDocument();
    expect(screen.getByText('Campanha com lista')).toBeInTheDocument();
    expect(screen.getAllByText('Lista importada')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Lançar' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Dry-run' })).toBeEnabled();
    expect(screen.getByRole('link', { name: 'Revisar plano e capacidade' })).toHaveAttribute(
      'href',
      '/app/configuracoes',
    );
  });

  it('mantém hard-block quando a capacidade não foi contratada', async () => {
    setReadyCapability(contactsCapability({
      contracted: false,
      enabled: false,
      permitted: false,
      limit: null,
      used: null,
      remaining: null,
      denialReason: 'not_contracted',
    }));
    renderCampaigns();

    expect(await screen.findByText('Não contratada')).toBeInTheDocument();
    const launchButton = screen.getByRole('button', { name: 'Lançar' });
    expect(launchButton).toBeDisabled();
    expect(campaignServiceMocks.launchCampaign).not.toHaveBeenCalled();
  });

  it('mantém hard-block quando o manifesto está indisponível', async () => {
    appStoreState.manifestStatus = 'unavailable';
    appStoreState.manifest = null;
    appStoreState.capability = null;
    renderCampaigns();

    expect(await screen.findByText('Capacidade indisponível')).toBeInTheDocument();
    const launchButton = screen.getByRole('button', { name: 'Lançar' });
    expect(launchButton).toBeDisabled();
    expect(campaignServiceMocks.launchCampaign).not.toHaveBeenCalled();
  });

  it('envia também a audiência CRM com quota esgotada para o preflight do backend', async () => {
    setReadyCapability(contactsCapability({
      permitted: false,
      used: 1_000,
      remaining: 0,
      denialReason: 'quota_exhausted',
    }));
    campaignServiceMocks.fetchCampaigns.mockResolvedValue([{
      ...listCampaign,
      id: 'campaign-crm',
      title: 'Campanha somente CRM',
      audience: {
        ...listCampaign.audience!,
        filters: {
          audience_list_ids: [],
          include_crm_contacts: true,
        },
      },
    }]);
    renderCampaigns();

    const launchButton = await screen.findByRole('button', { name: 'Lançar' });
    expect(launchButton).toBeEnabled();
    fireEvent.click(launchButton);
    await waitFor(() => expect(campaignServiceMocks.launchCampaign).toHaveBeenCalledWith(
      'campaign-crm',
      { dryRun: false },
    ));
  });

  it('mostra falha persistente e não afirma zero falhas quando o lançamento aborta', async () => {
    campaignServiceMocks.launchCampaign.mockRejectedValueOnce(
      new ApiError(500, 'A materialização do contato falhou.', {
        code: 'WA-CAM-CONTACT-MATERIALIZATION',
      }),
    );
    renderCampaigns();

    fireEvent.click(await screen.findByRole('button', { name: 'Lançar' }));

    expect(await screen.findByText('Lançamento abortado')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('WA-CAM-CONTACT-MATERIALIZATION');
    expect(screen.getByRole('alert')).toHaveTextContent('Nenhum total de falhas foi confirmado');
    expect(
      screen.getAllByText('Falhas').some((label) => label.parentElement?.textContent?.includes('—')),
    ).toBe(true);
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({
      variant: 'destructive',
      title: 'Lançamento não concluído',
    }));
  });
});
