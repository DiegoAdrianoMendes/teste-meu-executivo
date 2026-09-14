/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ComponentProps, ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FieldHelp } from '@/components/ui/field-help';
import Campaigns from '@/pages/Campaigns';
import Onboarding from '@/pages/Onboarding';
import type { OnboardingStepId, WorkspaceOnboardingState } from '@/types';

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

const onboardingServiceMocks = vi.hoisted(() => ({
  fetchWhatsAppAutomationRules: vi.fn(),
  fetchWorkspaceOnboarding: vi.fn(),
  saveWhatsAppAutomationRules: vi.fn(),
  saveWorkspaceOnboarding: vi.fn(),
}));

const playbookServiceMocks = vi.hoisted(() => ({
  listPlaybooks: vi.fn(),
}));
const toastMock = vi.hoisted(() => vi.fn());

vi.mock('@/services/campaigns', () => campaignServiceMocks);
vi.mock('@/services/whatsapp', () => whatsappServiceMocks);
vi.mock('@/services/admin', () => onboardingServiceMocks);
vi.mock('@/services/playbooks', () => playbookServiceMocks);
vi.mock('@/klm/api', () => ({
  klmApi: {
    flows: klmApiMocks.flows,
    areas: klmApiMocks.areas,
    omnichannelConnections: klmApiMocks.omnichannelConnections,
  },
}));
vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ can: () => false }),
}));
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}));
vi.mock('@/components/common/PersonalizedFilterBuilder', () => ({
  PersonalizedFilterBuilder: () => <button type="button">Abrir filtros de campanhas</button>,
}));

const audienceList = {
  id: 'list-1',
  createdById: 'user-1',
  name: 'Clientes ativos',
  description: 'Lista de teste',
  source: 'manual',
  status: 'active',
  memberCount: 0,
  validCount: 0,
  invalidCount: 0,
  duplicateCount: 0,
  importMetadata: {},
  members: [],
  sensitiveDataHidden: false,
  createdAt: '2026-07-22T12:00:00Z',
  updatedAt: '2026-07-22T12:00:00Z',
};

const stepTitles: Record<OnboardingStepId, string> = {
  welcome: 'Boas-vindas e contexto',
  whatsapp: 'Conectar WhatsApp',
  areas: 'Confirmar áreas liberadas',
  template: 'Escolher template inicial',
  follow_up: 'Configurar follow-up e intervenção humana',
  playbook: 'Definir política comercial e honorários',
  integrations: 'Integrações opcionais',
  review: 'Revisão final e ativação',
};

let onboardingStep: OnboardingStepId = 'welcome';

function makeOnboarding(step: OnboardingStepId): WorkspaceOnboardingState {
  const stepIds = Object.keys(stepTitles) as OnboardingStepId[];
  return {
    currentStep: step,
    onboardingCompleted: false,
    onboardingProgress: 50,
    nextOnboardingStep: step,
    pendingRequiredSteps: ['whatsapp', 'review'],
    completedSteps: ['welcome'],
    skippedSteps: [],
    reviewCompleted: false,
    playbookId: null,
    integrations: [
      {
        key: 'whatsapp',
        label: 'WhatsApp',
        description: 'Canal oficial de atendimento.',
        required: true,
        status: 'not_started',
      },
      {
        key: 'email',
        label: 'E-mail',
        description: 'Envio de avisos e documentos por e-mail.',
        required: false,
        status: 'not_started',
      },
    ],
    steps: stepIds.map((id) => ({
      id,
      title: stepTitles[id],
      description: `Descrição de ${stepTitles[id]}`,
      required: id === 'whatsapp' || id === 'review',
      completed: id === 'welcome',
      skipped: false,
      blocked: false,
      status: id === 'welcome' ? 'completed' : 'pending',
    })),
    areaActivations: [
      {
        key: 'consumer',
        label: 'Direito do consumidor',
        description: 'Atendimento inicial de demandas de consumo.',
        enabled: true,
        selectedFlowTemplateId: 'flow-1',
        followUpProfile: 'balanced',
        humanInterventionMode: 'human_in_the_loop',
        checklist: ['Confirmar documentos'],
        flowOptions: [{
          id: 'flow-1',
          name: 'Triagem inicial',
          practiceArea: 'consumer',
          trigger: 'manual',
          isActive: true,
          nodeCount: 3,
          promptCount: 1,
        }],
      },
    ],
    notificationRecipients: [
      {
        id: 'recipient-1',
        name: 'Ana Gestora',
        email: 'ana@example.com',
        phone: '+5511999990000',
        role: 'custom',
      },
    ],
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

function TestProviders({ children, client = createQueryClient() }: { children: ReactNode; client?: QueryClient }) {
  return (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

function renderCampaigns(props: ComponentProps<typeof Campaigns> = {}, client = createQueryClient()) {
  return {
    client,
    ...render(
      <TestProviders client={client}>
        <Campaigns {...props} />
      </TestProviders>,
    ),
  };
}

function renderOnboarding(step: OnboardingStepId) {
  onboardingStep = step;
  return render(
    <TestProviders>
      <Onboarding />
    </TestProviders>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();

  campaignServiceMocks.fetchCampaigns.mockResolvedValue([]);
  campaignServiceMocks.fetchMetaCommercialCharges.mockResolvedValue(null);
  campaignServiceMocks.fetchOptIns.mockResolvedValue([]);
  campaignServiceMocks.fetchSuppressions.mockResolvedValue([]);
  campaignServiceMocks.fetchAudienceLists.mockResolvedValue([audienceList]);
  campaignServiceMocks.fetchAudienceList.mockResolvedValue(audienceList);
  campaignServiceMocks.fetchMetaTemplates.mockResolvedValue([]);
  whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValue([]);
  whatsappServiceMocks.fetchConversationAssignees.mockResolvedValue([]);
  klmApiMocks.flows.mockResolvedValue([]);
  klmApiMocks.areas.mockResolvedValue([
    { id: 'area-consumer', areaKey: 'consumer', name: 'Direito do consumidor', description: null, status: 'active', version: 'v1', readiness: 100, isActive: true, blueprint: {}, validatedAt: null },
    { id: 'area-frozen', areaKey: 'tributario', name: 'Tributário', description: null, status: 'blueprint', version: 'v1', readiness: 50, isActive: false, blueprint: {}, validatedAt: null },
  ]);
  klmApiMocks.omnichannelConnections.mockResolvedValue([]);

  onboardingStep = 'welcome';
  onboardingServiceMocks.fetchWorkspaceOnboarding.mockImplementation(async () => makeOnboarding(onboardingStep));
  onboardingServiceMocks.fetchWhatsAppAutomationRules.mockResolvedValue({
    enabled: false,
    onboardingCompleted: false,
    timezone: 'America/Sao_Paulo',
    businessHoursStart: 8,
    businessHoursEnd: 19,
    rules: [
      {
        id: 'welcome-default',
        label: 'Boas-vindas imediatas',
        enabled: false,
        triggerType: 'welcome',
        replyText: 'Sugestão de boas-vindas.',
        flowTemplateId: null,
        targetQueueStatus: null,
        targetColumnId: null,
        escalateToHuman: false,
        onlyFirstMessage: true,
        silenceWindowHours: 0,
      },
      {
        id: 'off-hours-default',
        label: 'Resposta fora do horário',
        enabled: false,
        triggerType: 'off_hours',
        replyText: 'Sugestão fora do horário.',
        flowTemplateId: null,
        targetQueueStatus: 'follow_up',
        targetColumnId: null,
        escalateToHuman: false,
        onlyFirstMessage: false,
        silenceWindowHours: 12,
      },
    ],
  });
  onboardingServiceMocks.saveWhatsAppAutomationRules.mockImplementation(async (payload) => payload);
  onboardingServiceMocks.saveWorkspaceOnboarding.mockImplementation(async (payload: { currentStep?: OnboardingStepId }) => (
    makeOnboarding(payload.currentStep ?? onboardingStep)
  ));
  playbookServiceMocks.listPlaybooks.mockResolvedValue([
    {
      id: 'playbook-1',
      name: 'Política comercial aprovada',
      state: 'published',
      currentVersion: 2,
      publishedVersion: 2,
    },
  ]);
});

afterEach(() => {
  cleanup();
});

describe('FieldHelp', () => {
  it('expõe um botão Info nomeado e um popover compreensível por leitores de tela', async () => {
    render(
      <FieldHelp
        label="Token de acesso"
        description="É a chave que autoriza uma integração."
        whereToFind="No painel do provedor, em Segurança."
      />,
    );

    const trigger = screen.getByRole('button', { name: 'Ajuda sobre Token de acesso' });
    expect(trigger).toHaveAttribute('type', 'button');
    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');

    fireEvent.click(trigger);

    const popover = await screen.findByRole('dialog', { name: 'Token de acesso' });
    expect(popover).toHaveTextContent('O que é: É a chave que autoriza uma integração.');
    expect(popover).toHaveTextContent('Onde encontrar: No painel do provedor, em Segurança.');
  });
});

describe('Campaigns: ajuda técnica e contexto de Conversas', () => {
  it('informa explicitamente que o opt-in não foi registrado quando a sessão expirou', async () => {
    campaignServiceMocks.saveOptIn.mockRejectedValue(
      Object.assign(new Error('Sua sessão expirou. Entre novamente para continuar com segurança.'), { status: 401 }),
    );
    const { container } = renderCampaigns();

    await screen.findByText('Cadastrar opt-in');
    const phone = container.querySelector<HTMLInputElement>('#consent-phone');
    expect(phone).not.toBeNull();
    const saveButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === 'Salvar opt-in');
    expect(saveButton).toBeDefined();
    fireEvent.change(phone!, { target: { value: '+55 11 99999-0000' } });
    fireEvent.click(saveButton!);

    await waitFor(() => expect(campaignServiceMocks.saveOptIn).toHaveBeenCalledOnce());
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({
      variant: 'destructive',
      title: 'Opt-in não registrado',
      description: 'Sua sessão expirou. Entre novamente para continuar com segurança.',
    }));
    expect(phone).toHaveValue('+55 11 99999-0000');
    expect(toastMock).not.toHaveBeenCalledWith(expect.objectContaining({ title: 'Opt-in atualizado.' }));
  });

  it('cobre com ajuda acessível todas as entradas técnicas e de configuração', async () => {
    const { container } = renderCampaigns();

    await screen.findByRole('button', { name: 'Ajuda sobre Arquivo CSV' });

    const expectedHelpLabels = [
      'Arquivo CSV',
      'Número oficial de saída',
      'Template aprovado da Meta',
      'Nome do modelo',
      'Categoria pretendida',
      'Texto do modelo',
      'Exemplos das variáveis',
      'Agendamento',
      'Flow após o disparo',
      'Início do flow',
      'Parâmetros do cabeçalho',
      'Parâmetros da mensagem',
      'Fontes da audiência',
      'Busca da audiência',
      'Etapa principal',
      'Classificação',
      'Responsável',
      'Fila operacional',
      'Incluir sem card',
      'Incluir bloqueados',
      'Status do consentimento',
      'Origem do consentimento',
      'Motivo da supressão',
      'Ativa',
      'Filtro avançado de campanhas',
    ];

    const helpTriggers = Array.from(container.querySelectorAll<HTMLButtonElement>('button[data-field-help]'));
    expect(helpTriggers.length).toBeGreaterThanOrEqual(expectedHelpLabels.length);
    const accessibleHelpLabels = new Set(helpTriggers.map((trigger) => trigger.getAttribute('aria-label')));
    for (const label of expectedHelpLabels) {
      expect(accessibleHelpLabels).toContain(`Ajuda sobre ${label}`);
    }
    for (const trigger of helpTriggers) {
      expect(trigger.getAttribute('aria-label')).toMatch(/^Ajuda sobre .+/);
    }

    expect(screen.getByRole('combobox', { name: 'Número oficial de saída' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Template aprovado da Meta' })).toBeInTheDocument();
    expect(screen.getByLabelText('Agendamento')).toHaveAttribute('type', 'datetime-local');
    expect(screen.getByRole('textbox', { name: 'Buscar na audiência' })).toBeInTheDocument();
  });

  it('oferece modelos apenas para áreas ativas e preenche o rascunho', async () => {
    renderCampaigns();

    expect(await screen.findByRole('combobox', { name: 'Área do banco de modelos' })).toHaveTextContent('Direito do consumidor');
    expect(screen.queryByText('Tributário')).not.toBeInTheDocument();
    expect(screen.getByText('30 modelos')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Usar modelo Atualização da reclamação' }));

    expect(screen.getByLabelText('Nome do modelo')).toHaveValue('atualizacao_reclamacao_consumidor_consumer');
    expect((screen.getByLabelText('Texto do modelo') as HTMLTextAreaElement).value).toContain('sua solicitação');

    fireEvent.change(screen.getByRole('textbox', { name: 'Buscar no banco de modelos' }), { target: { value: 'boleto' } });
    expect(screen.getByRole('button', { name: 'Usar modelo Boleto disponível' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Usar modelo Reunião confirmada' })).not.toBeInTheDocument();
  });

  it('apresenta modelos próprios para outras áreas jurídicas ativas', async () => {
    klmApiMocks.areas.mockResolvedValue([
      { id: 'area-bank', areaKey: 'bancario', name: 'Direito bancário', description: null, status: 'active', version: 'v1', readiness: 100, isActive: true, blueprint: {}, validatedAt: null },
      { id: 'area-family', areaKey: 'familia', name: 'Direito de família', description: null, status: 'active', version: 'v1', readiness: 100, isActive: true, blueprint: {}, validatedAt: null },
    ]);
    renderCampaigns();

    expect(await screen.findByRole('button', { name: 'Usar modelo Documentos da operação bancária' })).toBeInTheDocument();
    expect(screen.getByText('30 modelos')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('combobox', { name: 'Área do banco de modelos' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Direito de família' }));
    expect(await screen.findByRole('button', { name: 'Usar modelo Documentos da demanda familiar' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Usar modelo Documentos da operação bancária' })).not.toBeInTheDocument();
  });

  it('executa a análise do modelo no serviço de IA e mostra sua atribuição', async () => {
    campaignServiceMocks.analyzeMetaTemplateDraft.mockResolvedValue({
      predictedCategory: 'utility',
      approvalLikelihood: 'high',
      confidence: 0.91,
      costBand: 'Menor, categoria utility',
      recommendation: 'Preparar submissão',
      summary: 'Mensagem transacional com contexto claro.',
      signals: ['O prazo é esperado pelo cliente.', 'As variáveis têm exemplos.'],
      provider: 'openai',
      modelUsed: 'gpt-test',
    });
    renderCampaigns();

    fireEvent.change(await screen.findByLabelText('Texto do modelo'), { target: { value: 'Olá {{1}}, seus documentos foram recebidos.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Analisar com IA' }));

    await waitFor(() => expect(campaignServiceMocks.analyzeMetaTemplateDraft).toHaveBeenCalledWith(expect.objectContaining({
      body: 'Olá {{1}}, seus documentos foram recebidos.',
      practiceArea: 'Direito do consumidor',
    })));
    expect(await screen.findByText('Mensagem transacional com contexto claro.')).toBeInTheDocument();
    expect(screen.getByText(/Analisado por openai · gpt-test · confiança 91%/)).toBeInTheDocument();
  });

  it('organiza a criação da campanha em quatro etapas navegáveis', async () => {
    renderCampaigns();

    expect(await screen.findByRole('navigation', { name: 'Etapas da nova campanha' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Etapa 2 Automação Opcional/ })).toBeInTheDocument();
    expect(screen.getByLabelText('Número oficial de saída').closest('.hidden')).toBeNull();
    expect(screen.getByLabelText('Descrição').closest('.hidden')).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Etapa 2 Automação/ }));
    expect(screen.getByLabelText('Descrição').closest('.hidden')).toBeNull();
    expect(screen.getByLabelText('Número oficial de saída').closest('.hidden')).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Etapa 4 Revisão/ }));
    expect(screen.getByText('Revisão antes de criar')).toBeInTheDocument();
    expect(screen.getByText(/complete número oficial, template e título/i)).toBeInTheDocument();
  });

  it('atualiza o contato do sheet, usa callbacks e não cria links para fora do contexto', async () => {
    const onConversationFilter = vi.fn();
    const client = createQueryClient();
    const view = renderCampaigns({
      embedded: true,
      contextThreadId: 'thread-maria',
      contextMatterId: 'matter-maria',
      contextContactName: 'Maria Silva',
      onConversationFilter,
    }, client);

    expect(await screen.findByText('Você continua na conversa de Maria Silva')).toBeInTheDocument();
    const audienceSearch = screen.getByRole('textbox', { name: 'Buscar na audiência' });
    expect(audienceSearch).toHaveValue('Maria Silva');
    expect(view.container.querySelector('a[href^="/app/conversas"]')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Conversas com disparo' }));
    expect(onConversationFilter).toHaveBeenLastCalledWith('com_disparo');
    fireEvent.click(screen.getByRole('button', { name: 'Disparos sem retorno' }));
    expect(onConversationFilter).toHaveBeenLastCalledWith('disparo_sem_retorno');
    fireEvent.click(screen.getByRole('button', { name: 'Voltar ao histórico da conversa' }));
    expect(onConversationFilter).toHaveBeenLastCalledWith('todos');

    fireEvent.change(audienceSearch, { target: { value: 'busca manual' } });
    view.rerender(
      <TestProviders client={client}>
        <Campaigns
          embedded
          contextThreadId="thread-joana"
          contextMatterId="matter-joana"
          contextContactName="Joana Souza"
          onConversationFilter={onConversationFilter}
        />
      </TestProviders>,
    );

    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Buscar na audiência' })).toHaveValue('Joana Souza'));
    expect(screen.getByText('Você continua na conversa de Joana Souza')).toBeInTheDocument();
    expect(view.container.querySelector('a[href^="/app/conversas"]')).not.toBeInTheDocument();

    view.rerender(
      <TestProviders client={client}>
        <Campaigns embedded contextThreadId="thread-joana" contextContactName="Joana Souza" />
      </TestProviders>,
    );
    expect(screen.getByRole('button', { name: 'Conversas com disparo' })).toBeDisabled();
    expect(view.container.querySelector('a[href^="/app/conversas"]')).not.toBeInTheDocument();

    view.rerender(
      <TestProviders client={client}>
        <Campaigns />
      </TestProviders>,
    );
    expect(screen.getByRole('link', { name: 'Com disparo' })).toHaveAttribute('href', '/app/conversas?broadcast=with');
    expect(screen.getByRole('link', { name: 'Histórico de conversas' })).toHaveAttribute('href', '/app/conversas');
  });
});

describe('Onboarding: cobertura de ajuda técnica', () => {
  it('mantém ajuda acessível ao lado de cada entrada técnica ou de configuração', async () => {
    const cases: Array<{
      step: OnboardingStepId;
      helpLabels: string[];
      control?: { role: 'textbox' | 'combobox'; name: string };
    }> = [
      {
        step: 'welcome',
        helpLabels: ['Nome do contato de alerta', 'E-mail para alertas', 'WhatsApp para alertas'],
        control: { role: 'textbox', name: 'E-mail para alertas' },
      },
      { step: 'whatsapp', helpLabels: ['Conexão oficial do WhatsApp'] },
      { step: 'areas', helpLabels: ['Área liberada'] },
      {
        step: 'template',
        helpLabels: ['Template inicial'],
        control: { role: 'combobox', name: 'Template inicial' },
      },
      {
        step: 'follow_up',
        helpLabels: ['Perfil de follow-up', 'Intervenção humana'],
        control: { role: 'combobox', name: 'Perfil de follow-up' },
      },
      {
        step: 'playbook',
        helpLabels: ['Playbook publicado'],
        control: { role: 'combobox', name: 'Playbook publicado' },
      },
      { step: 'integrations', helpLabels: ['Integração E-mail'] },
    ];

    for (const testCase of cases) {
      const view = renderOnboarding(testCase.step);
      await screen.findByRole('heading', { name: stepTitles[testCase.step] });

      if (testCase.step === 'whatsapp') {
        expect(await screen.findByText('Escolha obrigatória do escritório')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Confirmar escolha de automação' })).toBeInTheDocument();
      }

      for (const label of testCase.helpLabels) {
        const help = await screen.findByRole('button', { name: `Ajuda sobre ${label}` });
        expect(help).toHaveAttribute('aria-haspopup', 'dialog');
        expect(help).toHaveAttribute('data-field-help', label);
      }

      if (testCase.control) {
        expect(screen.getByRole(testCase.control.role, { name: testCase.control.name })).toBeInTheDocument();
      }

      view.unmount();
      cleanup();
    }
  });
});
