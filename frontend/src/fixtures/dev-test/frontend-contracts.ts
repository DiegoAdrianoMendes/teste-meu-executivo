import {
  buildDefaultBrand,
  buildDefaultUserPreferences,
} from '@/branding/workspace-defaults';
import type {
  AgentEffectivePolicy,
  AreaDefinition,
  Brand,
  PlaybookSection,
  PlaybookVersion,
  ProposalVersion,
  UserPreferences,
} from '@/types/frontend-contracts';

export const DEV_TEST_FRONTEND_SCOPE = {
  workspaceId: 'workspace-dev-test-klm',
  userId: 'user-dev-test-owner',
} as const;

export interface DevTestFrontendContractFixtures {
  fixtureKind: 'dev-test';
  brand: Brand;
  userPreferences: UserPreferences;
  proposalVersion: ProposalVersion;
  agentEffectivePolicy: AgentEffectivePolicy;
  areaDefinition: AreaDefinition;
  playbookVersion: PlaybookVersion;
}

interface FixtureRuntime {
  DEV?: boolean;
  MODE?: string;
}

const FIXTURE_TIMESTAMP = '2026-07-19T12:00:00.000Z';

function playbookSections(): PlaybookSection[] {
  return [
    { key: 'identity_voice', title: 'Identidade e voz', enabled: true, content: { tone: 'claro, humano e responsável', forbiddenClaims: ['garantia de resultado'] } },
    { key: 'audience_intent', title: 'Público e intenção', enabled: true, content: { personas: ['paciente', 'familiar'], exclusionSignals: ['opt_out'] } },
    { key: 'discovery', title: 'Descoberta', enabled: true, content: { requiredQuestions: ['Qual é a urgência?', 'Houve negativa formal?'] } },
    { key: 'legal_triage', title: 'Triagem jurídica', enabled: true, content: { requiredEvidence: ['relatorio_medico', 'negativa'] } },
    { key: 'qualification', title: 'Qualificação', enabled: true, content: { minimumScore: 60, evidenceRequired: true } },
    { key: 'offer_fees', title: 'Oferta e honorários', enabled: true, content: { minimumInitialFee: 4000, currency: 'BRL' } },
    { key: 'objections', title: 'Objeções', enabled: true, content: { library: ['preco', 'preciso_pensar', 'falta_documentos'] } },
    { key: 'objection_handling', title: 'Contorno de objeção', enabled: true, content: { maximumAttempts: 2, escalateOnUncertainty: true } },
    { key: 'proposals', title: 'Propostas', enabled: true, content: { formats: ['pdf', 'whatsapp_vertical'], approvalRequired: true } },
    { key: 'follow_up', title: 'Follow-up', enabled: true, content: { policy: 'lead_never_unanswered', respectsOptOut: true } },
    { key: 'handoff', title: 'Handoff', enabled: true, content: { ownerAgent: 'orquestrador', ownerBridgeAgent: 'hermes', ownerBridgeDelegator: 'secretaria', humanTakeoverAvailable: true } },
    { key: 'post_sale', title: 'Pós-venda', enabled: true, content: { milestones: ['contrato', 'procuracao', 'documentos', 'peticao'] } },
    { key: 'experimentation', title: 'Experimentação', enabled: true, content: { externalExecution: false, rollbackRequired: true } },
  ];
}

/** Builds deterministic records for a requested dev/test scope. */
export function createDevTestFrontendContractFixtures(
  scope: { workspaceId: string; userId: string } = DEV_TEST_FRONTEND_SCOPE,
  runtime: FixtureRuntime = import.meta.env,
): DevTestFrontendContractFixtures {
  assertDevTestFixtureRuntime(runtime);
  const brandId = `brand:${scope.workspaceId}:1`;
  const areaDefinitionId = `area:${scope.workspaceId}:saude:1`;
  const playbookId = `playbook:${scope.workspaceId}:saude-comercial`;
  const playbookVersionId = `${playbookId}:1`;

  // Fonte única da identidade padrão: src/branding/workspace-defaults.ts.
  const brand: Brand = buildDefaultBrand(scope, FIXTURE_TIMESTAMP);

  const userPreferences: UserPreferences = buildDefaultUserPreferences(
    scope,
    FIXTURE_TIMESTAMP,
  );

  const playbookVersion: PlaybookVersion = {
    id: playbookVersionId,
    workspaceId: scope.workspaceId,
    playbookId,
    version: 1,
    status: 'published',
    name: 'Saúde comercial supervisionada',
    description: 'Jornada local de qualificação, proposta e handoff com gates humanos.',
    parentVersionId: null,
    scope: {
      areaDefinitionIds: [areaDefinitionId],
      channels: ['whatsapp', 'email', 'web'],
      stages: ['novo_lead', 'triagem_sdr', 'proposta', 'contrato'],
      agentKeys: ['sdr', 'juridico', 'hermes'],
      offerKeys: ['saude_inicial'],
    },
    sections: playbookSections(),
    autonomyByStage: [
      { stage: 'novo_lead', mode: 'ai_triage_human_meeting', hardGates: ['opt_out', 'legal_advice'] },
      { stage: 'proposta', mode: 'ai_close_guarded', hardGates: ['discount_outside_policy', 'signature', 'payment'] },
    ],
    testScenarioIds: ['scenario:saude:eligible', 'scenario:saude:human-gate'],
    changeSummary: 'Fixture inicial dos contratos frontend-first P2.',
    checksum: 'fixture-playbook-v1',
    createdAt: FIXTURE_TIMESTAMP,
    createdBy: scope.userId,
    approvedAt: FIXTURE_TIMESTAMP,
    approvedBy: scope.userId,
    publishedAt: FIXTURE_TIMESTAMP,
  };

  const areaDefinition: AreaDefinition = {
    id: areaDefinitionId,
    workspaceId: scope.workspaceId,
    key: 'saude',
    version: 1,
    status: 'active',
    label: 'Direito da Saúde',
    description: 'Área de saúde suplementar e acesso a tratamentos.',
    taxonomy: {
      themes: ['saude_suplementar'],
      subthemes: ['medicamento_alto_custo', 'tratamento', 'home_care', 'cirurgia', 'exame'],
      urgencySignals: ['risco_vida', 'tratamento_interrompido', 'prazo_medico'],
    },
    fields: [
      { key: 'healthObjectName', label: 'Medicamento ou tratamento', type: 'text', required: true },
      { key: 'medicalUrgencyHours', label: 'Urgência em horas', type: 'number', required: false },
      { key: 'hasFormalDenial', label: 'Negativa formal', type: 'boolean', required: true },
    ],
    documentRequirements: [
      { key: 'medical_report', label: 'Relatório médico', required: true, acceptedMimeTypes: ['application/pdf', 'image/jpeg', 'image/png'] },
      { key: 'formal_denial', label: 'Negativa formal', required: false, acceptedMimeTypes: ['application/pdf', 'image/jpeg', 'image/png'] },
    ],
    qualificationCriteria: ['urgencia_confirmada', 'prescricao_medica', 'parte_elegivel'],
    agentKeys: ['sdr', 'juridico', 'hermes', 'peticao'],
    pipelineStageKeys: ['novo_lead', 'triagem_sdr', 'documentos', 'reuniao', 'proposta', 'contrato'],
    defaultPlaybookId: playbookId,
    objectionLibraryIds: ['objections:saude:v1'],
    templateIds: ['templates:saude:v1'],
    proposalTemplateIds: ['proposal-template:saude:v1'],
    pricingPolicyId: 'pricing:saude:v1',
    flowIds: ['flow:saude:intake:v1'],
    ragCollectionIds: ['rag:saude:fixture'],
    approvalRuleIds: ['approval:saude:high-risk'],
    kpiKeys: ['speed_to_lead', 'qualification_rate', 'proposal_acceptance'],
    testScenarioIds: playbookVersion.testScenarioIds,
    createdAt: FIXTURE_TIMESTAMP,
    updatedAt: FIXTURE_TIMESTAMP,
  };

  const proposalVersion: ProposalVersion = {
    id: `proposal-version:${scope.workspaceId}:fixture:1`,
    workspaceId: scope.workspaceId,
    proposalId: `proposal:${scope.workspaceId}:fixture`,
    matterId: `matter:${scope.workspaceId}:fixture`,
    version: 1,
    status: 'approved',
    title: 'Proposta jurídica de demonstração',
    brandId,
    brandVersion: brand.version,
    playbookVersionId,
    formats: ['pdf', 'whatsapp_vertical'],
    sections: [
      { id: 'cover', kind: 'cover', title: 'Cuidado jurídico com clareza', body: 'Proposta de demonstração para ambiente local.' },
      { id: 'scope', kind: 'scope', title: 'Escopo', body: 'Análise documental, estratégia e acompanhamento jurídico.' },
      { id: 'pricing', kind: 'pricing', title: 'Investimento', body: 'Condições sujeitas à aprovação humana.' },
      { id: 'cta', kind: 'cta', title: 'Próximo passo', body: 'Revisar a proposta com a equipe responsável.' },
    ],
    pricing: {
      currency: 'BRL',
      initialFee: 4000,
      successFeePercentage: 20,
      intermediateFee: null,
      paymentOptions: ['À vista', '2 parcelas'],
    },
    callToAction: {
      label: 'Solicitar revisão humana',
      url: null,
    },
    validUntil: '2026-08-02T23:59:59.000Z',
    approval: {
      required: true,
      status: 'approved',
      decidedBy: scope.userId,
      decidedAt: FIXTURE_TIMESTAMP,
    },
    analytics: {
      views: 0,
      lastViewedAt: null,
      acceptedAt: null,
    },
    checksum: 'fixture-proposal-v1',
    createdAt: FIXTURE_TIMESTAMP,
    createdBy: scope.userId,
    publishedAt: null,
  };

  const agentEffectivePolicy: AgentEffectivePolicy = {
    id: `effective-policy:${scope.workspaceId}:sdr:saude`,
    workspaceId: scope.workspaceId,
    agentKey: 'sdr',
    areaDefinitionId,
    playbookVersionId,
    channel: 'whatsapp',
    stage: 'triagem_sdr',
    autonomyMode: 'ai_triage_human_meeting',
    allowedActions: ['summarize', 'ask_triage_question', 'classify_intent', 'recommend_next_step'],
    prohibitedActions: ['sign_contract', 'request_payment', 'publish_external_message_without_gate'],
    tools: ['case_context_read', 'conversation_history_read'],
    hardGates: ['legal_advice', 'signature', 'payment', 'high_risk', 'low_confidence'],
    financialLimits: {
      currency: 'BRL',
      minimumInitialFee: 4000,
      maximumDiscountPercentage: 0,
    },
    rules: [
      {
        id: 'rule:human-review-conversion',
        label: 'Conversão exige humano',
        decision: 'require_human',
        reason: 'O modo de autonomia da fixture exige reunião humana antes da conversão.',
        when: { action: 'convert_lead' },
        sourceRefs: [`${playbookVersionId}#autonomyByStage`],
      },
    ],
    fallback: {
      mode: 'human_queue',
      queueKey: 'legal_review',
      reason: 'Falha fechada para revisão humana.',
    },
    sourceVersionIds: [playbookVersionId, areaDefinitionId],
    computedAt: FIXTURE_TIMESTAMP,
  };

  return {
    fixtureKind: 'dev-test',
    brand,
    userPreferences,
    proposalVersion,
    agentEffectivePolicy,
    areaDefinition,
    playbookVersion,
  };
}

export function assertDevTestFixtureRuntime(
  runtime: FixtureRuntime = import.meta.env,
): void {
  if (!runtime.DEV && runtime.MODE !== 'test') {
    throw new Error('Fixtures frontend-first são exclusivas de desenvolvimento e teste.');
  }
}

export function loadDevTestFrontendContractFixtures(
  scope: { workspaceId: string; userId: string },
  runtime: FixtureRuntime = import.meta.env,
): DevTestFrontendContractFixtures {
  assertDevTestFixtureRuntime(runtime);
  return createDevTestFrontendContractFixtures(scope, runtime);
}

const defaultFixtures = createDevTestFrontendContractFixtures();

export const DEV_TEST_BRAND_FIXTURE = defaultFixtures.brand;
export const DEV_TEST_USER_PREFERENCES_FIXTURE = defaultFixtures.userPreferences;
export const DEV_TEST_PROPOSAL_VERSION_FIXTURE = defaultFixtures.proposalVersion;
export const DEV_TEST_AGENT_EFFECTIVE_POLICY_FIXTURE = defaultFixtures.agentEffectivePolicy;
export const DEV_TEST_AREA_DEFINITION_FIXTURE = defaultFixtures.areaDefinition;
export const DEV_TEST_PLAYBOOK_VERSION_FIXTURE = defaultFixtures.playbookVersion;
