/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const policyApi = vi.hoisted(() => ({
  createKey: vi.fn(),
  editor: vi.fn(),
  catalog: vi.fn(),
  list: vi.fn(),
  preview: vi.fn(),
  publish: vi.fn(),
  save: vi.fn(),
  versions: vi.fn(),
}));

vi.mock('@/services/plan-policies', async () => {
  const actual = await vi.importActual<typeof import('@/services/plan-policies')>('@/services/plan-policies');
  return {
    ...actual,
    createPlanPolicyIdempotencyKey: policyApi.createKey,
    getPlanPolicyEditor: policyApi.editor,
    getPlanPolicyEntitlementCatalog: policyApi.catalog,
    listPlanPoliciesPage: policyApi.list,
    listPlanPolicyVersionsPage: policyApi.versions,
    previewPlanPolicyRollout: policyApi.preview,
    publishPlanPolicy: policyApi.publish,
    savePlanPolicyDraft: policyApi.save,
  };
});

import { PlatformPlanPoliciesPanel } from '@/components/platform/PlatformPlanPoliciesPanel';
import {
  PLAN_POLICY_ENTITLEMENT_KEYS,
  type PlanPolicyDraft,
  type PlanPolicyEditor,
  type PlanPolicyEntitlementKey,
  type PlanPolicyPreview,
  type PlanPolicyPublication,
  type PlanPolicySnapshot,
  type PlanPolicyVersion,
} from '@/services/plan-policies';

const featureEntitlements = new Set<PlanPolicyEntitlementKey>([
  'digital_signature',
  'calendar',
  'email',
  'webhooks',
  'advanced_audit',
  'success_manager',
]);

function makeSnapshot(): PlanPolicySnapshot {
  return {
    schemaVersion: '1.0',
    name: 'Profissional',
    description: 'Plano completo para equipes jurídicas.',
    isActive: true,
    isPublic: true,
    recommended: true,
    sortOrder: 2,
    currency: 'BRL',
    priceMonthlyCents: 69700,
    priceAnnuallyCents: 669120,
    supportTier: 'priority',
    integrations: ['whatsapp'],
    highlights: ['Atendimento prioritário'],
    parameters: {
      maxMatters: 500,
      maxWorkspaces: 2,
      maxFilesPerConversation: 20,
      maxOperationalRecipients: 25,
      maxCampaignRecipientsPerMonth: 5_000,
    },
    entitlements: Object.fromEntries(PLAN_POLICY_ENTITLEMENT_KEYS.map((key) => [key, {
      enabled: true,
      limitQuantity: featureEntitlements.has(key) ? null : 100,
      hardLimit: true,
    }])) as PlanPolicySnapshot['entitlements'],
  };
}

const baseSnapshot = makeSnapshot();
const editor: PlanPolicyEditor = {
  planId: 'plan-pro',
  planTier: 'pro',
  source: 'draft',
  etag: 'draft-etag-1',
  draftRevision: 3,
  publishedVersion: 2,
  snapshot: baseSnapshot,
};

const version: PlanPolicyVersion = {
  id: 'version-2',
  planId: 'plan-pro',
  version: 2,
  schemaVersion: '1.0',
  snapshot: baseSnapshot,
  snapshotHash: 'snapshot-hash-2',
  rolloutMode: 'existing_and_new',
  effectiveAt: '2026-07-20T12:00:00Z',
  rolloutRule: null,
  publicationReason: 'Franquias revisadas para o lançamento.',
  publishedByUserId: 'platform-root-1',
  publishedAt: '2026-07-20T12:00:00Z',
};

function makePreview(overrides: Partial<PlanPolicyPreview> = {}): PlanPolicyPreview {
  return {
    planId: 'plan-pro',
    rolloutMode: 'existing_and_new',
    effectiveAt: '2026-07-22T12:00:00Z',
    rule: null,
    affectedCount: 2,
    appliesImmediately: true,
    accounts: [{
      workspaceId: 'workspace-1',
      name: 'Silva Advocacia',
      slug: 'silva-advocacia',
      workspaceStatus: 'active',
      subscriptionStatus: 'active',
      workspaceCreatedAt: '2026-03-10T12:00:00Z',
      currentPolicyVersion: 2,
    }],
    ...overrides,
  };
}

function makePublication(): PlanPolicyPublication {
  return {
    policy: { ...version, id: 'version-3', version: 3, publicationReason: 'Nova franquia de automações.' },
    affectedCount: 2,
    assignmentsCreated: 2,
    idempotentReplay: false,
  };
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <PlatformPlanPoliciesPanel canPublish />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  Object.values(policyApi).forEach((mock) => mock.mockReset());
  policyApi.createKey.mockReturnValue('plan-policy-attempt-1');
  policyApi.list.mockResolvedValue({
    items: [{
      planId: 'plan-pro',
      planTier: 'pro',
      planName: 'Profissional',
      draftRevision: 3,
      draftEtag: 'draft-etag-1',
      latestVersion: 2,
      latestEffectiveAt: '2026-07-20T12:00:00Z',
    }],
    total: 1,
    limit: 10,
    offset: 0,
    hasMore: false,
  });
  policyApi.catalog.mockResolvedValue(PLAN_POLICY_ENTITLEMENT_KEYS.map((key) => ({
    key,
    control: featureEntitlements.has(key) ? 'checkbox' : 'checkbox_and_number',
    supportsLimit: !featureEntitlements.has(key),
    requiresFiniteLimit: key === 'tokens',
  })));
  policyApi.editor.mockResolvedValue(editor);
  policyApi.versions.mockResolvedValue({ items: [version], total: 1, limit: 10, offset: 0, hasMore: false });
  policyApi.preview.mockImplementation(async (_planId, payload) => makePreview({
    rolloutMode: payload.rolloutMode,
    effectiveAt: payload.effectiveAt ?? '2026-07-22T12:00:00Z',
    rule: payload.rule ?? null,
    appliesImmediately: payload.rolloutMode !== 'scheduled',
  }));
  policyApi.save.mockImplementation(async (_planId, value): Promise<PlanPolicyDraft> => ({
    id: 'draft-1',
    planId: 'plan-pro',
    revision: 4,
    etag: 'draft-etag-2',
    snapshot: value,
    updatedByUserId: 'platform-root-1',
    createdAt: '2026-07-21T12:00:00Z',
    updatedAt: '2026-07-22T12:00:00Z',
  }));
  policyApi.publish.mockResolvedValue(makePublication());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformPlanPoliciesPanel', () => {
  it('edits the complete package identity, commercial limits and catalog copy', async () => {
    renderPanel();

    await screen.findByRole('heading', { name: 'Identidade do pacote' });
    fireEvent.change(screen.getByLabelText('Nome do pacote'), { target: { value: 'Profissional Plus' } });
    fireEvent.change(screen.getByLabelText('Descrição do pacote'), { target: { value: 'Pacote para operação jurídica completa.' } });
    fireEvent.change(screen.getByLabelText('Integrações incluídas'), { target: { value: 'WhatsApp\nGoogle Agenda\nWhatsApp' } });
    fireEvent.change(screen.getByLabelText('Benefícios exibidos'), { target: { value: 'Atendimento omnichannel\nTokens ampliados' } });
    fireEvent.change(screen.getByLabelText('Casos ativos'), { target: { value: '900' } });
    fireEvent.change(screen.getByLabelText('Destinatários de campanhas por mês'), { target: { value: '7500' } });
    fireEvent.change(screen.getByLabelText('Preço mensal (centavos)'), { target: { value: '99700' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));

    await waitFor(() => expect(policyApi.save).toHaveBeenCalledTimes(1));
    const savedSnapshot = policyApi.save.mock.calls[0][1] as PlanPolicySnapshot;
    expect(savedSnapshot.name).toBe('Profissional Plus');
    expect(savedSnapshot.description).toBe('Pacote para operação jurídica completa.');
    expect(savedSnapshot.integrations).toEqual(['WhatsApp', 'Google Agenda']);
    expect(savedSnapshot.highlights).toEqual(['Atendimento omnichannel', 'Tokens ampliados']);
    expect(savedSnapshot.parameters.maxMatters).toBe(900);
    expect(savedSnapshot.parameters.maxCampaignRecipientsPerMonth).toBe(7500);
    expect(savedSnapshot.priceMonthlyCents).toBe(99700);
  });

  it('edits translated capabilities and preserves hidden commercial fields in the draft', async () => {
    renderPanel();

    expect(await screen.findByRole('heading', { name: 'Capacidades' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Execuções de IA' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Assinatura digital' })).toBeChecked();
    expect(screen.queryByText('ai_executions')).not.toBeInTheDocument();
    expect(screen.queryByText(/R\$/)).not.toBeInTheDocument();
    expect(screen.queryByText('69700')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Quantidade de Execuções de IA'), {
      target: { value: '321' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));

    await waitFor(() => expect(policyApi.save).toHaveBeenCalledTimes(1));
    const [planId, savedSnapshot, ifMatch] = policyApi.save.mock.calls[0];
    expect(planId).toBe('plan-pro');
    expect(ifMatch).toBe('draft-etag-1');
    expect(savedSnapshot.entitlements.ai_executions.limitQuantity).toBe(321);
    expect(savedSnapshot.priceMonthlyCents).toBe(69700);
    expect(savedSnapshot.priceAnnuallyCents).toBe(669120);
    expect(savedSnapshot.parameters).toEqual(baseSnapshot.parameters);
  });

  it('keeps unsaved edits when the same editor etag is refetched', async () => {
    const { queryClient } = renderPanel();
    const quantity = await screen.findByLabelText('Quantidade de Execuções de IA');
    fireEvent.change(quantity, { target: { value: '321' } });

    queryClient.setQueryData(
      ['platform', 'plan-policies', 'plan-pro', 'editor'],
      { ...editor, snapshot: makeSnapshot() },
    );

    await waitFor(() => expect(screen.getByLabelText('Quantidade de Execuções de IA')).toHaveValue(321));
    expect(screen.getByText('Alterações não salvas')).toBeInTheDocument();
  });

  it('requires a current preview and explicit confirmation, then reuses the publication key', async () => {
    policyApi.publish
      .mockRejectedValueOnce(new Error('Falha transitória de publicação.'))
      .mockResolvedValueOnce(makePublication());
    renderPanel();

    await screen.findByText('Franquias revisadas para o lançamento.');
    fireEvent.change(screen.getByLabelText('Motivo da publicação'), {
      target: { value: 'Nova franquia de automações.' },
    });
    const publishTrigger = screen.getByRole('button', { name: 'Publicar versão' });
    expect(publishTrigger).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia' }));
    expect(await screen.findByText('2 conta(s) no alcance')).toBeInTheDocument();
    expect(screen.getByText('Amostra: 1')).toBeInTheDocument();
    expect(publishTrigger).toBeEnabled();

    fireEvent.click(publishTrigger);
    expect(policyApi.publish).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: 'Confirmar publicação' }));

    await waitFor(() => expect(policyApi.publish).toHaveBeenCalledTimes(1));
    expect(policyApi.publish).toHaveBeenLastCalledWith(
      'plan-pro',
      {
        rolloutMode: 'existing_and_new',
        draftEtag: 'draft-etag-1',
        reason: 'Nova franquia de automações.',
      },
      'plan-policy-attempt-1',
    );
    expect(await screen.findByText('Falha transitória de publicação.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Publicar versão' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirmar publicação' }));
    await waitFor(() => expect(policyApi.publish).toHaveBeenCalledTimes(2));
    expect(policyApi.publish.mock.calls[1][2]).toBe(policyApi.publish.mock.calls[0][2]);
    expect(policyApi.createKey).toHaveBeenCalledTimes(1);
  });

  it('serializes a structured account rule before previewing it', async () => {
    renderPanel();
    await screen.findByRole('heading', { name: 'Capacidades' });

    fireEvent.click(screen.getByRole('radio', { name: /Por regra/ }));
    expect(screen.getByText('Selecione ao menos um critério para a regra.')).toBeInTheDocument();
    const workspaceStatusGroup = screen.getByRole('group', { name: 'Status da conta' });
    fireEvent.click(within(workspaceStatusGroup).getByRole('checkbox', { name: 'Ativa' }));
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia' }));

    await waitFor(() => expect(policyApi.preview).toHaveBeenCalledWith('plan-pro', {
      rolloutMode: 'rule',
      rule: {
        workspaceIds: [],
        excludeWorkspaceIds: [],
        workspaceStatuses: ['active'],
        setupModes: [],
        subscriptionStatuses: [],
      },
      sampleLimit: 10,
    }));
  });

  it('sends scheduled local date and time as an ISO timestamp', async () => {
    renderPanel();
    await screen.findByRole('heading', { name: 'Capacidades' });

    fireEvent.click(screen.getByRole('radio', { name: /Agendado/ }));
    const previewButton = screen.getByRole('button', { name: 'Gerar prévia' });
    expect(previewButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Data e hora de vigência'), {
      target: { value: '2026-08-15T10:30' },
    });
    fireEvent.click(previewButton);

    await waitFor(() => expect(policyApi.preview).toHaveBeenCalledWith('plan-pro', {
      rolloutMode: 'scheduled',
      effectiveAt: new Date('2026-08-15T10:30').toISOString(),
      sampleLimit: 10,
    }));
  });
});
