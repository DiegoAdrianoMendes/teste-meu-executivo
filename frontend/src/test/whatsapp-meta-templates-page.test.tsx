/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import WhatsAppMetaTemplates from '@/pages/WhatsAppMetaTemplates';
import type { UnifiedTemplate } from '@/types';

const campaignServiceMocks = vi.hoisted(() => ({
  fetchUnifiedTemplates: vi.fn(),
  createTemplateDraft: vi.fn(),
  updateTemplateDraft: vi.fn(),
  deleteTemplateDraft: vi.fn(),
  removeLocalTemplateRecord: vi.fn(),
  syncMetaTemplates: vi.fn(),
  submitTemplateDraft: vi.fn(),
  fetchPendingTemplateInternalApprovals: vi.fn(),
  approveTemplateInternalSubmission: vi.fn(),
  cloneRejectedTemplateAsDraft: vi.fn(),
  analyzeMetaTemplateDraft: vi.fn(),
  fetchUtilityTemplateCatalog: vi.fn(),
  retryUtilityTemplateCatalogItem: vi.fn(),
}));

const whatsappServiceMocks = vi.hoisted(() => ({
  fetchWhatsAppConnections: vi.fn(),
  fetchWhatsAppExternalDeliveryGate: vi.fn(),
}));

vi.mock('@/services/campaigns', () => campaignServiceMocks);
vi.mock('@/services/whatsapp', () => whatsappServiceMocks);
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

function renderPage() {
  const client = createQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <WhatsAppMetaTemplates />
    </QueryClientProvider>,
  );
}

const approvedSynced: UnifiedTemplate = {
  id: 'tpl-approved',
  whatsappConnectionId: 'conn-1',
  externalTemplateId: 'ext-1',
  name: 'sincronizado_aprovado',
  category: 'utility',
  language: 'pt_BR',
  status: 'approved',
  qualityRating: 'green',
  components: [],
  isDraft: false,
  localStatus: null,
  validationErrors: [],
  metaSubmissionAvailable: false,
  createdAt: '2026-07-20T12:00:00Z',
  updatedAt: '2026-07-20T12:00:00Z',
  lastSyncedAt: '2026-07-22T12:00:00Z',
};

const localDraft: UnifiedTemplate = {
  id: 'tpl-draft',
  whatsappConnectionId: 'conn-1',
  externalTemplateId: null,
  name: 'meu_rascunho',
  category: 'marketing',
  language: 'pt_BR',
  status: 'pending',
  qualityRating: null,
  components: [],
  isDraft: true,
  localStatus: 'draft',
  validationErrors: [],
  metaSubmissionAvailable: false,
  createdAt: '2026-07-22T12:00:00Z',
  updatedAt: '2026-07-22T12:00:00Z',
  lastSyncedAt: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([approvedSynced, localDraft]);
  campaignServiceMocks.fetchPendingTemplateInternalApprovals.mockResolvedValue([]);
  campaignServiceMocks.fetchUtilityTemplateCatalog.mockResolvedValue({
    catalogVersion: '2026.09.1',
    connectionId: 'conn-1',
    gateOpen: false,
    items: [{
      key: 'confirmacao_consulta',
      catalogVersion: '2026.09.1',
      itemVersion: 1,
      metaName: 'meu_executivo_confirmacao_consulta_v1',
      category: 'utility',
      language: 'pt_BR',
      body: 'Olá, {{1}}. Confirmamos sua consulta em {{2}}.',
      variables: [
        { position: 1, key: 'nome_cliente', example: 'Maria' },
        { position: 2, key: 'data', example: '18/08/2026' },
      ],
      practiceAreas: ['todas'],
      templateId: 'catalog-template-1',
      externalTemplateId: null,
      status: 'pending',
      provisioningStatus: 'catalog_pending_gate',
      attempt: 1,
      canRetry: false,
    }],
  });
  whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValue([
    { id: 'conn-1', connectionType: 'OFFICIAL_MANUAL', isDefault: true, phoneNumber: '5511999990000', status: 'connected' },
  ]);
  whatsappServiceMocks.fetchWhatsAppExternalDeliveryGate.mockResolvedValue(false);
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
});

describe('WhatsAppMetaTemplates: estados da lista unificada', () => {
  it('mostra a biblioteca por WABA sem fingir submissão quando o gate está fechado', async () => {
    renderPage();

    expect(await screen.findByText('Biblioteca de utilidade')).toBeInTheDocument();
    expect(await screen.findByText('confirmacao consulta')).toBeInTheDocument();
    expect(screen.getByText('Pendente: ambiente bloqueado')).toBeInTheDocument();
    expect(screen.queryByText('Aprovado pela Meta')).not.toBeInTheDocument();
  });

  it('renderiza templates sincronizados e rascunhos locais com badges de status distintos', async () => {
    renderPage();

    expect(await screen.findByText('sincronizado_aprovado')).toBeInTheDocument();
    expect(await screen.findByText('meu_rascunho')).toBeInTheDocument();
    expect(screen.getByText('Aprovado')).toBeInTheDocument();
    expect(screen.getByText('Rascunho')).toBeInTheDocument();
    expect(screen.getByText(/submissão real à meta ainda não está liberada/i)).toBeInTheDocument();
  });

  it('renderiza rascunho em estado de catálogo e estado desconhecido sem derrubar a tela (V1)', async () => {
    const catalogFailedDraft: UnifiedTemplate = {
      ...localDraft,
      id: 'tpl-catalog-failed',
      name: 'meu_executivo_encerramento_processo_v1',
      category: 'utility',
      localStatus: 'catalog_failed',
    };
    const unknownStatusDraft = {
      ...localDraft,
      id: 'tpl-unknown-status',
      name: 'rascunho_estado_desconhecido',
      localStatus: 'estado_futuro',
    } as unknown as UnifiedTemplate;
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([
      approvedSynced,
      catalogFailedDraft,
      unknownStatusDraft,
    ]);

    renderPage();

    expect(await screen.findByText('meu_executivo_encerramento_processo_v1')).toBeInTheDocument();
    expect(screen.getByText('Falhou no catálogo')).toBeInTheDocument();
    expect(screen.getByText('rascunho_estado_desconhecido')).toBeInTheDocument();
    expect(screen.getByText('Rascunho')).toBeInTheDocument();
    expect(screen.getByText('sincronizado_aprovado')).toBeInTheDocument();
  });

  it('mostra estado vazio quando não há nenhum template', async () => {
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/nenhum template ainda/i)).toBeInTheDocument();
  });

  it('não exibe alerta de bloqueio com lista vazia e gate do ambiente aberto', async () => {
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([]);
    whatsappServiceMocks.fetchWhatsAppExternalDeliveryGate.mockResolvedValue(true);
    renderPage();

    expect(await screen.findByText(/nenhum template ainda/i)).toBeInTheDocument();
    await waitFor(() => expect(whatsappServiceMocks.fetchWhatsAppExternalDeliveryGate).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/submissão real à meta ainda não está liberada/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/não foi possível confirmar se as entregas externas/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sincronizar com a meta/i })).toBeEnabled();
  });

  it('avisa que o gate está incerto e desabilita só o sync quando a consulta falha', async () => {
    whatsappServiceMocks.fetchWhatsAppExternalDeliveryGate.mockRejectedValueOnce(new Error('gate indisponível'));
    renderPage();

    expect(await screen.findByText(/não foi possível confirmar se as entregas externas/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sincronizar com a meta/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /novo rascunho/i })).toBeEnabled();
    expect(screen.queryByText(/submissão real à meta ainda não está liberada/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));

    await waitFor(() => expect(whatsappServiceMocks.fetchWhatsAppExternalDeliveryGate).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/submissão real à meta ainda não está liberada/i)).toBeInTheDocument();
    expect(screen.queryByText(/não foi possível confirmar se as entregas externas/i)).not.toBeInTheDocument();
  });

  it('mostra o estado externo após submit 200 mesmo quando a origem continua marcada como rascunho', async () => {
    const submittedTemplate: UnifiedTemplate = {
      ...localDraft,
      externalTemplateId: 'meta-template-123',
      status: 'pending',
      localStatus: 'submission_prepared',
      isDraft: true,
    };
    whatsappServiceMocks.fetchWhatsAppExternalDeliveryGate.mockResolvedValue(true);
    campaignServiceMocks.fetchUnifiedTemplates
      .mockResolvedValueOnce([localDraft])
      .mockResolvedValue([submittedTemplate]);
    campaignServiceMocks.submitTemplateDraft.mockResolvedValue({
      template: submittedTemplate,
      submittedToMeta: true,
      message: 'Template submetido à Meta.',
    });
    renderPage();

    expect(await screen.findByText('meu_rascunho')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^submeter$/i }));

    await waitFor(() => expect(campaignServiceMocks.submitTemplateDraft).toHaveBeenCalledWith('tpl-draft'));
    expect(await screen.findByText('Pendente na Meta')).toBeInTheDocument();
    expect(screen.getByText('Enviado à Meta · ID meta-template-123')).toBeInTheDocument();
    expect(screen.queryByText('Rascunho local')).not.toBeInTheDocument();
    expect(screen.queryByText('Preparado p/ Meta')).not.toBeInTheDocument();
  });

  it('separa órfãos da lista ativa e só remove o registro local após confirmação', async () => {
    const removedAtMeta: UnifiedTemplate = {
      ...approvedSynced,
      id: 'tpl-removed-at-meta',
      name: 'removido_na_meta',
      status: 'rejected',
      removedAtSourceAt: '2026-08-09T22:00:00Z',
    };
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([approvedSynced, removedAtMeta]);
    campaignServiceMocks.removeLocalTemplateRecord.mockResolvedValue(undefined);
    renderPage();

    expect(await screen.findByText('Histórico — removidos na Meta')).toBeInTheDocument();
    expect(screen.getByText('removido_na_meta')).toBeInTheDocument();
    expect(screen.getByText('Removido na Meta')).toBeInTheDocument();
    expect(screen.getByText(/1 template\(s\)/i)).toBeInTheDocument();

    const removeButtons = screen.getAllByRole('button', { name: /remover registro local/i });
    fireEvent.click(removeButtons[removeButtons.length - 1]);
    expect(screen.getByText(/esta ação não exclui nem altera o template na meta/i)).toBeInTheDocument();
    expect(campaignServiceMocks.removeLocalTemplateRecord).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /confirmar remoção local/i }));
    await waitFor(() =>
      expect(campaignServiceMocks.removeLocalTemplateRecord).toHaveBeenCalledWith('tpl-removed-at-meta'),
    );
  });
});

describe('WhatsAppMetaTemplates: builder de rascunho', () => {
  it('abre uma cópia editável para template aprovado por deep-link', async () => {
    window.history.replaceState({}, '', '/app/templates-meta?copy=tpl-approved');
    renderPage();

    expect(await screen.findByText('Criar cópia de template')).toBeInTheDocument();
    expect(screen.getByDisplayValue('sincronizado_aprovado_v2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /salvar rascunho/i })).toBeInTheDocument();
    expect(screen.getByText(/esta cópia é um novo rascunho/i)).toBeInTheDocument();
  });

  it('abre uma cópia editável para rascunho por deep-link sem entrar no modo de atualização', async () => {
    window.history.replaceState({}, '', '/app/templates-meta?copy=tpl-draft');
    renderPage();

    expect(await screen.findByText('Criar cópia de template')).toBeInTheDocument();
    expect(screen.getByDisplayValue('meu_rascunho_v2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /salvar rascunho/i })).toBeInTheDocument();
  });

  it('valida em linha e mostra a prévia do balão ao digitar', async () => {
    renderPage();
    await screen.findByText('sincronizado_aprovado');

    fireEvent.click(screen.getByRole('button', { name: /novo rascunho/i }));

    await screen.findByText('Novo rascunho de template');
    // Corpo vazio: deve reprovar o botão de salvar com o alerta inline.
    expect(screen.getByText(/antes de salvar, ajuste/i)).toBeInTheDocument();
    expect(screen.getByText(/o corpo da mensagem é obrigatório/i)).toBeInTheDocument();

    const bodyField = screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.');
    fireEvent.change(bodyField, { target: { value: 'Olá, seu processo teve atualização.' } });

    // Prévia do balão reflete o texto digitado.
    expect(await screen.findByTestId('template-preview-body')).toHaveTextContent('Olá, seu processo teve atualização.');
  });

  it('reprova variáveis fora de sequência antes de permitir salvar', async () => {
    renderPage();
    await screen.findByText('sincronizado_aprovado');

    fireEvent.click(screen.getByRole('button', { name: /novo rascunho/i }));
    await screen.findByText('Novo rascunho de template');

    const nameField = screen.getByPlaceholderText('atualizacao_processo');
    fireEvent.change(nameField, { target: { value: 'teste_variavel' } });

    const bodyField = screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.');
    fireEvent.change(bodyField, { target: { value: 'Olá {{1}}, seu prazo é {{3}}.' } });

    expect(await screen.findByText(/sequenciais a partir de \{\{1\}\}/i)).toBeInTheDocument();
  });

  it('chama o serviço de criação quando o rascunho é válido', async () => {
    campaignServiceMocks.createTemplateDraft.mockResolvedValue({ ...localDraft, id: 'tpl-novo' });
    renderPage();
    await screen.findByText('sincronizado_aprovado');

    fireEvent.click(screen.getByRole('button', { name: /novo rascunho/i }));
    await screen.findByText('Novo rascunho de template');

    fireEvent.change(screen.getByPlaceholderText('atualizacao_processo'), { target: { value: 'novo_template' } });
    fireEvent.change(screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.'), {
      target: { value: 'Olá, tudo bem?' },
    });

    fireEvent.click(screen.getByRole('button', { name: /salvar rascunho/i }));

    await waitFor(() => expect(campaignServiceMocks.createTemplateDraft).toHaveBeenCalledTimes(1));
    expect(campaignServiceMocks.createTemplateDraft).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'novo_template', whatsappConnectionId: 'conn-1' }),
    );
  });

  it('envia exemplos completos para a Meta quando o corpo possui variáveis', async () => {
    campaignServiceMocks.createTemplateDraft.mockResolvedValue({ ...localDraft, id: 'tpl-com-exemplos' });
    renderPage();
    await screen.findByText('sincronizado_aprovado');

    fireEvent.click(screen.getByRole('button', { name: /novo rascunho/i }));
    await screen.findByText('Novo rascunho de template');
    fireEvent.change(screen.getByPlaceholderText('atualizacao_processo'), { target: { value: 'template_com_variaveis' } });
    fireEvent.change(screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.'), {
      target: { value: 'Olá {{1}}, o processo {{2}} foi atualizado.' },
    });
    fireEvent.change(screen.getByLabelText('Exemplo da variável 1 do corpo'), { target: { value: 'Kleist' } });
    fireEvent.change(screen.getByLabelText('Exemplo da variável 2 do corpo'), { target: { value: '0001234-56.2026.8.07.0001' } });
    fireEvent.click(screen.getByRole('button', { name: /salvar rascunho/i }));

    await waitFor(() => expect(campaignServiceMocks.createTemplateDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        components: expect.objectContaining({
          bodyText: 'Olá {{1}}, o processo {{2}} foi atualizado.',
          bodyExamples: ['Kleist', '0001234-56.2026.8.07.0001'],
        }),
      }),
    ));
  });

  it('oferece formatação WhatsApp, emoji e variável no ponto selecionado', async () => {
    renderPage();
    await screen.findByText('sincronizado_aprovado');
    fireEvent.click(screen.getByRole('button', { name: /novo rascunho/i }));
    await screen.findByText('Novo rascunho de template');

    const bodyField = screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.') as HTMLTextAreaElement;
    fireEvent.change(bodyField, { target: { value: 'Olá mundo' } });
    bodyField.setSelectionRange(4, 9);
    fireEvent.click(screen.getByRole('button', { name: 'Negrito' }));
    expect(bodyField).toHaveValue('Olá *mundo*');
    fireEvent.click(screen.getByRole('button', { name: 'Inserir variável' }));
    expect(bodyField).toHaveValue('Olá *mundo*{{1}}');
  });

  it('normaliza a categoria em maiúsculas da Meta ao editar o rascunho clonado (V31)', async () => {
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([{ ...localDraft, category: 'UTILITY' }]);
    campaignServiceMocks.updateTemplateDraft.mockResolvedValue({ ...localDraft, category: 'utility' });
    renderPage();
    await screen.findByText('meu_rascunho');
    fireEvent.click(screen.getByRole('button', { name: /^editar$/i }));
    await screen.findByText('Editar rascunho de template');
    fireEvent.change(screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.'), {
      target: { value: 'Olá, tudo certo com o seu caso. Responda se precisar de ajuda.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /salvar altera\u00e7\u00f5es/i }));

    await waitFor(() => expect(campaignServiceMocks.updateTemplateDraft).toHaveBeenCalledWith(
      localDraft.id,
      expect.objectContaining({ category: 'utility' }),
    ));
  });

  it('sugere resposta rápida para follow-up sem botão e exibe contadores', async () => {
    renderPage();
    await screen.findByText('sincronizado_aprovado');
    fireEvent.click(screen.getByRole('button', { name: /novo rascunho/i }));
    await screen.findByText('Novo rascunho de template');
    fireEvent.change(screen.getByPlaceholderText('atualizacao_processo'), { target: { value: 'follow_up_documentos' } });
    fireEvent.change(screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.'), { target: { value: 'Retomada do follow-up' } });
    expect(await screen.findByText(/sugestão para follow-up/i)).toBeInTheDocument();
    expect(screen.getByText(/0\/60 caracteres/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /adicionar.*quero continuar/i }));
    expect(screen.getByDisplayValue('Quero continuar')).toBeInTheDocument();
  });

  it('exibe categoria sugerida, sinais de marketing e risco OAB da análise estruturada', async () => {
    campaignServiceMocks.analyzeMetaTemplateDraft.mockResolvedValue({
      predictedCategory: 'marketing',
      metaCategorySuggested: 'marketing',
      marketingSignals: ['urgência comercial'],
      oabRisk: { level: 'high', signals: ['promessa_resultado'], requiresAcknowledgement: true },
      approvalLikelihood: 'low', confidence: 0.9, costBand: 'marketing', recommendation: 'Revisar antes de enviar.', summary: 'Risco alto.', signals: [], provider: 'fake', modelUsed: 'fake',
    });
    renderPage();
    await screen.findByText('sincronizado_aprovado');
    fireEvent.click(screen.getByRole('button', { name: /^editar$/i }));
    await screen.findByText('Editar rascunho de template');
    fireEvent.change(screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.'), { target: { value: 'Texto longo o bastante para análise.' } });
    fireEvent.click(screen.getByRole('button', { name: /analisar categoria e risco/i }));
    await waitFor(() => expect(campaignServiceMocks.analyzeMetaTemplateDraft).toHaveBeenCalledWith(expect.objectContaining({
      templateId: 'tpl-draft',
      categoryIntent: 'marketing',
    })));
    expect(await screen.findByText(/sugestão meta: marketing · risco oab high/i)).toBeInTheDocument();
    expect(screen.getByText(/sinais de marketing: urgência comercial/i)).toBeInTheDocument();
    expect(screen.getByText(/sinais éticos: promessa_resultado/i)).toBeInTheDocument();
  });

  it('salva um novo rascunho antes de analisar para obter templateId auditável', async () => {
    campaignServiceMocks.createTemplateDraft.mockResolvedValue({ ...localDraft, id: 'tpl-created-for-analysis' });
    campaignServiceMocks.analyzeMetaTemplateDraft.mockResolvedValue({
      predictedCategory: 'authentication', metaCategorySuggested: 'authentication', marketingSignals: [],
      oabRisk: { level: 'low', signals: [], requiresAcknowledgement: false }, approvalLikelihood: 'high',
      confidence: 0.8, costBand: 'authentication', recommendation: 'OK', summary: 'OK', signals: [], provider: 'fake', modelUsed: 'fake',
    });
    renderPage();
    await screen.findByText('sincronizado_aprovado');
    fireEvent.click(screen.getByRole('button', { name: /novo rascunho/i }));
    await screen.findByText('Novo rascunho de template');
    fireEvent.change(screen.getByPlaceholderText('atualizacao_processo'), { target: { value: 'codigo_login' } });
    fireEvent.change(screen.getByPlaceholderText('Seu processo {{1}} teve atualização em {{2}}.'), { target: { value: 'Seu código de acesso é {{1}}.' } });
    fireEvent.click(screen.getByRole('button', { name: /analisar categoria e risco/i }));
    await waitFor(() => expect(campaignServiceMocks.createTemplateDraft).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(campaignServiceMocks.analyzeMetaTemplateDraft).toHaveBeenCalledWith(expect.objectContaining({
      templateId: 'tpl-created-for-analysis', categoryIntent: 'utility',
    })));
  });
});

describe('WhatsAppMetaTemplates: aprovação interna (W2.2)', () => {
  it('mostra a fila de aprovação interna e aprova ao clicar', async () => {
    const pendingDraft: UnifiedTemplate = {
      ...localDraft,
      id: 'tpl-pending-approval',
      name: 'aguardando_aprovacao',
      localStatus: 'pending_internal_approval',
    };
    campaignServiceMocks.fetchPendingTemplateInternalApprovals.mockResolvedValue([pendingDraft]);
    campaignServiceMocks.approveTemplateInternalSubmission.mockResolvedValue({
      template: { ...pendingDraft, localStatus: 'submission_prepared' },
      submittedToMeta: false,
      message: 'Aprovado.',
    });
    renderPage();

    expect(await screen.findByText(/aguardando sua aprovação/i)).toBeInTheDocument();
    expect(screen.getByText('aguardando_aprovacao')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /aprovar submissão/i }));

    await waitFor(() =>
      expect(campaignServiceMocks.approveTemplateInternalSubmission).toHaveBeenCalledWith('tpl-pending-approval'),
    );
  });
});

describe('WhatsAppMetaTemplates: UX de rejeição e recategorização (W2.2)', () => {
  it('traduz o motivo de rejeição e oferece corrigir e reenviar', async () => {
    const rejected: UnifiedTemplate = {
      ...approvedSynced,
      id: 'tpl-rejected',
      name: 'template_rejeitado',
      status: 'rejected',
      rejectedReason: 'INVALID_FORMAT',
      isDraft: false,
      localStatus: null,
    };
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([rejected]);
    campaignServiceMocks.cloneRejectedTemplateAsDraft.mockResolvedValue({
      ...localDraft,
      id: 'tpl-rejected-clone',
      name: 'template_rejeitado_v2',
    });
    renderPage();

    expect(await screen.findByText(/rejeitado: formato inválido/i)).toBeInTheDocument();
    expect(screen.getByText(/revise os placeholders/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /corrigir e reenviar/i }));

    await waitFor(() =>
      expect(campaignServiceMocks.cloneRejectedTemplateAsDraft).toHaveBeenCalledWith('tpl-rejected'),
    );
  });

  it('mostra o alerta de recategorização com o impacto de custo', async () => {
    const recategorized: UnifiedTemplate = {
      ...approvedSynced,
      id: 'tpl-recat',
      name: 'template_recategorizado',
      category: 'marketing',
      previousCategory: 'utility',
    };
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([recategorized]);
    renderPage();

    expect(await screen.findByText(/a meta recategorizou este template/i)).toBeInTheDocument();
    expect(screen.getByText(/cerca de 7x mais caro/i)).toBeInTheDocument();
  });

  it('exibe rejeição e motivo para template enviado cuja origem continua sendo local', async () => {
    const rejectedSubmittedDraft: UnifiedTemplate = {
      ...localDraft,
      externalTemplateId: 'meta-rejected-123',
      status: 'rejected',
      localStatus: 'submission_prepared',
      rejectedReason: 'INVALID_FORMAT',
      isDraft: true,
    };
    campaignServiceMocks.fetchUnifiedTemplates.mockResolvedValue([rejectedSubmittedDraft]);
    renderPage();

    expect(await screen.findByText('Rejeitado')).toBeInTheDocument();
    expect(screen.getByText(/rejeitado: formato inválido/i)).toBeInTheDocument();
    expect(screen.getByText('Enviado à Meta · ID meta-rejected-123')).toBeInTheDocument();
    expect(screen.queryByText('Rascunho local')).not.toBeInTheDocument();
    expect(screen.queryByText('Preparado p/ Meta')).not.toBeInTheDocument();
  });
});
