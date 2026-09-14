/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentPlatformOperationsPanel } from '@/klm/components/AgentPlatformOperationsPanel';
import { klmApi } from '@/klm/api';

const WORKSPACE_ID = '11111111-1111-4111-8111-111111111111';

const page = <T,>(items: T[], offset = 0, total = items.length, limit = 25) => ({
  items,
  total,
  limit,
  offset,
  hasMore: offset + items.length < total,
});

const renderPanel = ({ canManage = true, isPlatformRoot = false, currentUserId, workspaceId = WORKSPACE_ID }: { canManage?: boolean; isPlatformRoot?: boolean; currentUserId?: string | null; workspaceId?: string | null } = {}) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AgentPlatformOperationsPanel canManage={canManage} isPlatformRoot={isPlatformRoot} currentUserId={currentUserId} workspaceId={workspaceId} />
  </QueryClientProvider>,
);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(klmApi, 'agentHumanOverview').mockResolvedValue({ open: 1, assigned: 1, acknowledged: 0, overdue: 0, escalated: 0, phone: { pending: 0, delivered: 1, failed: 0, notConfigured: 0 } });
  vi.spyOn(klmApi, 'agentHumanRoutingCatalog').mockResolvedValue({
    roles: [
      { key: 'operator', label: 'Operação', routableCount: 2 },
      { key: 'finance_manager', label: 'Gestão financeira', routableCount: 1 },
    ],
    skills: [{ key: 'tributario', label: 'Tributário', routableCount: 1 }],
    teams: [{ key: 'financeiro', label: 'Financeiro', routableCount: 1 }],
    directOwnerOnly: false,
    ownerFallback: { available: true, label: 'Dono do escritório', reason: 'O dono acompanha a escalada terminal.' },
  });
  vi.spyOn(klmApi, 'agentHumanRoutingPolicies').mockResolvedValue({
    version: 'human-routing-policies-2026-08-04.1',
    policies: [
      {
        key: 'finance_review_v1',
        version: 'human-routing-policies-2026-08-04.1',
        label: 'Financeiro → gestor financeiro → dono',
        description: 'Revisão financeira finita.',
        requiredRoles: ['finance_manager'],
        requiredSkills: ['financeiro'],
        maxHops: 4,
        available: true,
        specialistAvailable: true,
        matchedRoles: ['finance_manager'],
        matchedSkills: ['financeiro'],
        fallbackMode: 'finite_hierarchy',
      },
    ],
  });
  vi.spyOn(klmApi, 'agentWorkspaceOwnerAlertChannel').mockResolvedValue({ ownerWhatsappPhone: '5511999990000', configured: true });
  vi.spyOn(klmApi, 'agentPlatformNodes').mockResolvedValue([]);
  vi.spyOn(klmApi, 'agentPlatformRuns').mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0, hasMore: false });
  vi.spyOn(klmApi, 'agentPlatformCatalog').mockResolvedValue({
    policyVersion: 'agent-policy-v1', runtime: 'local', rolePackages: [
      { key: 'legal_operations', label: 'Legal Operations', description: 'Operação jurídica e automações.' },
      { key: 'finance', label: 'Financeiro', description: 'Operação financeira.' },
    ], agents: [{
      key: 'diretor_financeiro', version: 7, name: 'Diretor Financeiro', role: 'finance', group: 'finance', state: 'active', qualityScore: 1, queueSize: 0,
      description: 'Agente financeiro', tools: ['finance_read', 'finance_draft'], allowedModels: ['modelo-local'], defaultAutonomy: 'A2', gate: 'human_review',
      permissions: { readMatter: true, writeCrm: false, createDraft: true, sendExternal: false, requestHuman: true },
      modelPolicy: { strategy: 'approved', provider: 'local', model: 'modelo-local', taskClass: 'finance', modelApiEnabled: false, localModelEnabled: true, simulatorAvailable: true, channelDeliveryEnabled: false, externalEnabled: false },
    }],
  });
  vi.spyOn(klmApi, 'agentPlatformContextOptions').mockResolvedValue({ items: [] });
  vi.spyOn(klmApi, 'financeLookup').mockResolvedValue({ items: [], entityType: 'client', search: '', limit: 100, nextCursor: null });
  vi.spyOn(klmApi, 'listFinanceCostCenters').mockResolvedValue([]);
  vi.spyOn(klmApi, 'threads').mockResolvedValue([]);
  vi.spyOn(klmApi, 'matterTasks').mockResolvedValue([]);
});

describe('AgentPlatformOperationsPanel', () => {
  it('shows the assigned human, owner oversight and honest phone evidence', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([{
      id: 'decision-1',
      decisionKey: 'review:1',
      status: 'pending',
      assignedUserId: 'reviewer-1',
      assignedUserLabel: 'Revisora Jurídica',
      ownerUserLabel: 'Dono do Escritório',
      dueAt: new Date(Date.now() + 60_000).toISOString(),
      escalationLevel: 0,
      routingLevels: 3,
      purpose: 'Validar publicação jurídica',
      notificationDelivery: [
        { channel: 'in_app', recipientKind: 'assignee', status: 'delivered', attempts: 1 },
        { channel: 'phone', recipientKind: 'owner', status: 'not_configured', attempts: 0, lastErrorCode: 'phone_not_configured' },
      ],
    }]));
    renderPanel({ currentUserId: 'reviewer-1', canManage: false });
    expect(await screen.findByText('Revisora Jurídica')).toBeInTheDocument();
    expect(screen.getByText(/Dono acompanhando: Dono do Escritório/)).toBeInTheDocument();
    expect(screen.getByText(/Sistema: assignee: entregue \(1 tentativa\)/)).toBeInTheDocument();
    expect(screen.getByText(/Telefone: owner: não configurado \(0 tentativas; erro phone_not_configured\)/)).toBeInTheDocument();
    expect(screen.getByText(/Função e competência não expostas pela API desta decisão/)).toBeInTheDocument();
    expect(screen.getByText(/Rota efetiva: 3 níveis finitos; nível atual 1/)).toBeInTheDocument();
    expect(screen.getByText(/Finalidade: Validar publicação jurídica/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Revisora Jurídica/ }));
    expect(screen.getByText('Validar publicação jurídica')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Aprovar e retomar' })).toBeEnabled();
  });

  it('does not let an administrator decide an unassigned human call', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([{
      id: 'decision-other', decisionKey: 'review:other', status: 'pending',
      assignedUserId: 'reviewer-1', assignedUserLabel: 'Revisora Jurídica',
    }]));
    renderPanel({ currentUserId: 'admin-1', canManage: true });
    fireEvent.click(await screen.findByRole('button', { name: /Revisora Jurídica/ }));
    expect(screen.getByRole('button', { name: 'Aprovar e retomar' })).toBeDisabled();
    expect(screen.getByText(/só pode ser tomada pelo responsável atribuído, pelo dono/i)).toBeInTheDocument();
  });

  it('lets only the explicit platform root bypass the assignment in the UI', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([{
      id: 'decision-root', decisionKey: 'review:root', status: 'pending',
      assignedUserId: 'reviewer-1', assignedUserLabel: 'Revisora Jurídica',
    }]));
    renderPanel({ currentUserId: 'root-1', canManage: true, isPlatformRoot: true });
    fireEvent.click(await screen.findByRole('button', { name: /Revisora Jurídica/ }));
    expect(screen.getByRole('button', { name: 'Aprovar e retomar' })).toBeEnabled();
  });

  it('keeps unavailable runtime nodes out of active catalog and exposes the server reason', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformNodes').mockResolvedValue([{
      type: 'finance_payment', label: 'Pagamento externo', description: 'Cobrança', category: 'Financeiro',
      execution_mode: 'gated_external', side_effect: 'external', input_ports: [], output_ports: [], supports_dry_run: false, supports_error_route: true, config_version: 1,
      availability: 'unavailable', availabilityReason: 'Provedor financeiro não configurado.',
    }]);
    renderPanel();
    expect(await screen.findByText(/1 nó\(s\) indisponível/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/1 nó\(s\) indisponível/i));
    expect(screen.getByText('Provedor financeiro não configurado.')).toBeInTheDocument();
  });

  it('shows finite escalation, owner oversight and terminal states without enabling a second decision', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([{
      id: 'decision-terminal', decisionKey: 'review:terminal', status: 'expired',
      assignedUserLabel: 'Gestora Financeira', ownerUserLabel: 'Dono do Escritório',
      escalationLevel: 2, routingLevels: 4,
      phoneDelivery: [{ recipientKind: 'owner', status: 'failed', attempts: 3, lastErrorCode: 'provider_timeout' }],
    }]));
    renderPanel();
    expect(await screen.findByText('Últimos estados terminais')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Gestora Financeira/ }));
    expect(screen.getAllByText('Expirada')).toHaveLength(2);
    expect(screen.getByText('Encerrada; nenhum novo escalonamento')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Aprovar e retomar' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Reconhecer chamada' })).not.toBeInTheDocument();
  });

  it('creates a draft without accepting client-declared gates', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    const create = vi.spyOn(klmApi, 'createAgentDeployment').mockResolvedValue({ id: 'dep-1', status: 'draft', version: 1 });
    renderPanel();
    await screen.findByRole('option', { name: /Diretor Financeiro · v7/ });
    fireEvent.change(await screen.findByLabelText('Agente do deployment'), { target: { value: 'diretor_financeiro' } });
    expect(await screen.findByRole('option', { name: 'Financeiro → gestor financeiro → dono' })).toBeInTheDocument();
    expect(screen.getByLabelText('Modelo autorizado do agente')).toHaveValue('modelo-local');
    expect(screen.getByText('finance_read')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Políticas autorizadas do workspace' }));
    fireEvent.click(screen.getByRole('button', { name: 'Criar deployment' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ gates: {} }), WORKSPACE_ID));
    expect(create.mock.calls[0]?.[0]).toMatchObject({ agent_key: 'diretor_financeiro', role_package: 'legal_operations', version: 7, config: { definitionVersion: 7, rolePackage: 'legal_operations', model: 'modelo-local', tools: ['finance_read', 'finance_draft'], knowledgeSources: ['workspace_policy'], humanRoutingPolicy: 'finance_review_v1' } });
    expect(create.mock.calls[0]?.[0].config).not.toHaveProperty('testedAt');
    expect(screen.queryByLabelText('Chave do agente')).not.toBeInTheDocument();
  });

  it('exposes every server-calculated deployment gate and the configured runtime policy', async () => {
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([{
      id: 'dep-gates', agent_key: 'diretor_financeiro', version: 2, status: 'draft',
      config: {
        definitionVersion: 2,
        model: 'modelo-local',
        tools: ['finance_read'],
        knowledgeSources: ['finance-policy'],
        autonomyPolicy: 'A2',
        humanRoutingPolicy: 'financeiro → gestor → dono',
        budgetPolicy: { monthlyTokenLimit: 125000 },
        testEvidence: { kind: 'local_structural_dry_run', externalModelCalled: false },
      },
      gates: {
        definitionVerified: true, modelVerified: true, knowledgeVerified: true,
        autonomyVerified: true, policyVerified: false, entitlementVerified: true,
        toolsVerified: true, humanQueueVerified: true, budgetVerified: true, testPassed: false,
      },
      budgetUsage: { periodStart: '2026-08-01T00:00:00Z', usedTokens: 50000, limitTokens: 125000, remainingTokens: 75000, exhausted: false },
    }]));
    renderPanel();
    expect(await screen.findByText('diretor_financeiro')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Autonomia: A2')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Fila humana: financeiro → gestor → dono')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Teste: local_structural_dry_run · sem chamada externa')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Uso neste ciclo: 50.000 tokens · saldo 75.000')).toBeInTheDocument();
    const gates = screen.getByLabelText('Gates do deployment diretor_financeiro');
    expect(gates).toHaveTextContent('Definição: ok');
    expect(gates).toHaveTextContent('Política: pendente');
    expect(gates).toHaveTextContent('Teste: pendente');
    expect(screen.getByText(/Publicação permanece bloqueada/)).toBeInTheDocument();
  });

  it('does not claim validation when the server dry-run or queue gate is pending', async () => {
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([{
      id: 'dep-partial', agentKey: 'diretor_financeiro', version: 1, status: 'draft',
      config: { definitionVersion: 1, model: 'modelo-local', tools: ['finance_read'], humanRoutingPolicy: 'finance_review_v1', budgetPolicy: { monthlyTokenLimit: 1000 } },
      gates: { policyVerified: true, humanQueueVerified: false, budgetVerified: true, testPassed: false },
    }]));
    vi.spyOn(klmApi, 'validateAgentDeployment').mockResolvedValue({
      gates: { policyVerified: true, humanQueueVerified: false, budgetVerified: true, testPassed: false },
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Validar gates' }));
    expect(await screen.findByText(/Validação concluída parcialmente/)).toBeInTheDocument();
    expect(screen.queryByText(/dry-run local comprovados/)).not.toBeInTheDocument();
  });

  it('renders actual run cost and flags an exhausted deployment budget', async () => {
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([{
      id: 'dep-exhausted', agentKey: 'diretor_financeiro', version: 7, status: 'paused', config: { definitionVersion: 7, model: 'modelo-local', tools: ['finance_read'], budgetPolicy: { monthlyTokenLimit: 10 } }, gates: {},
      budgetUsage: { periodStart: '2026-08-01T00:00:00Z', usedTokens: 11, limitTokens: 10, remainingTokens: 0, exhausted: true },
    }]));
    vi.spyOn(klmApi, 'agentPlatformRuns').mockResolvedValue({ items: [{ id: 'run-1', agentName: 'Diretor Financeiro', deploymentId: 'dep-exhausted', contextType: 'finance', contextId: 'context-1', triggerSource: 'manual', status: 'paused_budget', attemptCount: 1, inputTokens: 3, outputTokens: 8, costUsd: '0.0125', startedAt: null, completedAt: null, createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-01T10:00:00Z' }], total: 1, limit: 25, offset: 0, hasMore: false });
    renderPanel();
    expect(await screen.findByText(/Orçamento do deployment esgotado/)).toBeInTheDocument();
    expect(screen.getByText(/Tokens: 11 \(entrada 3 · saída 8\) · Custo: US\$/)).toBeInTheDocument();
  });

  it('paginates agent runs with server offsets and exposes an accessible total', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    const runs = vi.spyOn(klmApi, 'agentPlatformRuns').mockImplementation((_workspaceId, limit, offset) => Promise.resolve({
      items: [{
        id: `run-${offset + 1}`, agentName: `Agente ${offset + 1}`, deploymentId: null,
        contextType: 'workspace', contextId: null, triggerSource: 'manual', status: 'completed',
        attemptCount: 1, inputTokens: 1, outputTokens: 2, costUsd: '0', startedAt: null,
        completedAt: null, createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-01T10:00:00Z',
      }],
      total: 51,
      limit,
      offset,
      hasMore: offset + limit < 51,
    }));
    renderPanel();

    expect(await screen.findByText('Agente 1')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Paginação de execuções de agentes' })).toHaveTextContent('Exibindo 1–1 de 51 execuções.');
    expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));
    await waitFor(() => expect(runs).toHaveBeenLastCalledWith(WORKSPACE_ID, 25, 25));
    expect(await screen.findByText('Agente 26')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Paginação de execuções de agentes' })).toHaveTextContent('Exibindo 26–26 de 51 execuções.');
    fireEvent.click(screen.getByRole('button', { name: 'Anterior' }));
    await waitFor(() => expect(runs).toHaveBeenLastCalledWith(WORKSPACE_ID, 25, 0));
  });

  it('paginates deployments and human calls through their server-side page contracts', async () => {
    const deployments = vi.spyOn(klmApi, 'agentPlatformDeployments').mockImplementation((_workspaceId, limit, offset) => Promise.resolve({
      items: [{ id: `deployment-${offset + 1}`, agentKey: `agente-${offset + 1}`, version: 1, status: 'draft', config: {}, gates: {} }],
      total: 26,
      limit,
      offset,
      hasMore: offset + limit < 26,
    }));
    const decisions = vi.spyOn(klmApi, 'agentPlatformDecisions').mockImplementation((_workspaceId, limit, offset) => Promise.resolve({
      items: [{ id: `decision-${offset + 1}`, status: 'pending', assignedUserLabel: `Responsável ${offset + 1}` }],
      total: 26,
      limit,
      offset,
      hasMore: offset + limit < 26,
    }));
    renderPanel();

    expect(await screen.findByText('agente-1')).toBeInTheDocument();
    expect(await screen.findByText('Responsável 1')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Paginação de deployments de agentes' })).toHaveTextContent('Exibindo 1–1 de 26 deployments.');
    expect(screen.getByRole('navigation', { name: 'Paginação de chamadas humanas' })).toHaveTextContent('Exibindo 1–1 de 26 chamadas.');
    fireEvent.click(screen.getByRole('button', { name: 'Próxima deployments' }));
    await waitFor(() => expect(deployments).toHaveBeenLastCalledWith(WORKSPACE_ID, 25, 25));
    expect(await screen.findByText('agente-26')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Próxima chamadas' }));
    await waitFor(() => expect(decisions).toHaveBeenLastCalledWith(WORKSPACE_ID, 25, 25));
    expect(await screen.findByText('Responsável 26')).toBeInTheDocument();
  });

  it('resets the execution page when the selected workspace changes', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    const runs = vi.spyOn(klmApi, 'agentPlatformRuns').mockImplementation((_workspaceId, limit, offset) => Promise.resolve({
      items: [{
        id: `run-${offset + 1}`, agentName: `Agente ${offset + 1}`, deploymentId: null,
        contextType: 'workspace', contextId: null, triggerSource: 'manual', status: 'completed',
        attemptCount: 1, inputTokens: 1, outputTokens: 2, costUsd: '0', startedAt: null,
        completedAt: null, createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-01T10:00:00Z',
      }],
      total: 51,
      limit,
      offset,
      hasMore: offset + limit < 51,
    }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <AgentPlatformOperationsPanel canManage workspaceId={WORKSPACE_ID} />
      </QueryClientProvider>,
    );
    await screen.findByText('Agente 1');
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));
    await waitFor(() => expect(runs).toHaveBeenLastCalledWith(WORKSPACE_ID, 25, 25));

    const nextWorkspace = '22222222-2222-4222-8222-222222222222';
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <AgentPlatformOperationsPanel canManage workspaceId={nextWorkspace} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(runs).toHaveBeenLastCalledWith(nextWorkspace, 25, 0));
  });

  it('creates a contextual run with guided workspace context and no UUID field', async () => {
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([{ id: 'dep-active', agentKey: 'diretor_financeiro', version: 7, status: 'active', config: {}, gates: {} }]));
    const createContextRun = vi.spyOn(klmApi, 'createAgentPlatformContextRun').mockResolvedValue({ id: 'run-context-1', status: 'queued', contextType: 'workspace', contextId: null });
    renderPanel({ isPlatformRoot: true });
    await screen.findByRole('option', { name: 'diretor_financeiro' });
    fireEvent.change(await screen.findByLabelText('Agente ativo para execução'), { target: { value: 'diretor_financeiro' } });
    const submit = screen.getByRole('button', { name: 'Criar execução governada' });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(createContextRun).toHaveBeenCalled(), { timeout: 1000 });
    expect(createContextRun).toHaveBeenCalledWith({ contextType: 'workspace', contextId: null, agentName: 'diretor_financeiro', triggerSource: 'manual_blackbox' }, WORKSPACE_ID);
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it('loads record context only through the selected workspace administrative lookup', async () => {
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([{ id: 'dep-active', agentKey: 'diretor_financeiro', version: 7, status: 'active', config: {}, gates: {} }]));
    const contextOptions = vi.spyOn(klmApi, 'agentPlatformContextOptions').mockResolvedValue({ items: [{ id: 'client-1', label: 'Cliente da conta selecionada' }] });
    const tenantLookup = vi.spyOn(klmApi, 'financeLookup');
    renderPanel({ isPlatformRoot: true });
    fireEvent.change(await screen.findByLabelText('Tipo de contexto da execução'), { target: { value: 'client' } });
    expect(await screen.findByRole('option', { name: 'Cliente da conta selecionada' })).toBeInTheDocument();
    expect(contextOptions).toHaveBeenCalledWith('client', WORKSPACE_ID);
    expect(tenantLookup).not.toHaveBeenCalled();
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it('calls a human by a catalogued function and skill without exposing an internal id', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    const request = vi.spyOn(klmApi, 'requestAgentHumanDecision').mockResolvedValue({ id: 'decision-2' });
    renderPanel();
    await screen.findByRole('option', { name: /Gestão financeira · 1 rota disponível/ });
    fireEvent.change(screen.getByLabelText('Função humana responsável'), { target: { value: 'finance_manager' } });
    fireEvent.change(screen.getByLabelText('Competência humana necessária'), { target: { value: 'tributario' } });
    fireEvent.change(screen.getByLabelText('Motivo da chamada humana'), { target: { value: 'Validar impacto tributário' } });
    expect(screen.getByText(/Rota solicitada: função finance_manager → competência tributario → até 4 níveis → dono/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Chamar humano correto' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({
      reason: 'Validar impacto tributário',
      requiredRoles: ['finance_manager'],
      requiredSkills: ['tributario'],
      maxHops: 4,
    }), WORKSPACE_ID));
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it('does not turn a typed skill search into an unverified routing value', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    const request = vi.spyOn(klmApi, 'requestAgentHumanDecision').mockResolvedValue({ id: 'decision-typed-search' });
    renderPanel();
    await screen.findByRole('option', { name: /Operação · 2 rotas disponíveis/ });
    fireEvent.change(screen.getByLabelText('Buscar competência humana'), { target: { value: 'competência inventada' } });
    expect(screen.queryByRole('option', { name: /competência inventada/i })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Motivo da chamada humana'), { target: { value: 'Preciso de validação operacional' } });
    fireEvent.click(screen.getByRole('button', { name: 'Chamar humano correto' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({ requiredRoles: ['operator'], requiredSkills: [] }), WORKSPACE_ID));
  });

  it('routes directly to the owner when no specialist function or team is active', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentHumanRoutingCatalog').mockResolvedValue({
      roles: [{ key: 'workspace_owner', label: 'Dono do escritório', routableCount: 1 }],
      skills: [], teams: [], directOwnerOnly: true,
      ownerFallback: { available: true, label: 'Dono do escritório', reason: 'Nenhuma função ou equipe especialista está ativa; a chamada seguirá diretamente para o dono.' },
    });
    const request = vi.spyOn(klmApi, 'requestAgentHumanDecision').mockResolvedValue({ id: 'decision-owner' });
    renderPanel();
    expect(await screen.findByText(/Nenhuma função ou equipe especialista está ativa/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Competência humana necessária')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo da chamada humana'), { target: { value: 'Decisão urgente sem especialista disponível' } });
    fireEvent.click(screen.getByRole('button', { name: 'Chamar humano correto' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({ requiredRoles: ['workspace_owner'], requiredSkills: [] }), WORKSPACE_ID));
  });

  it('does not create a wait when the current workspace is unavailable', async () => {
    vi.spyOn(klmApi, 'agentPlatformDeployments').mockResolvedValue(page([]));
    vi.spyOn(klmApi, 'agentPlatformDecisions').mockResolvedValue(page([]));
    const request = vi.spyOn(klmApi, 'requestAgentHumanDecision');
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentPlatformOperationsPanel canManage currentUserId="operator-1" workspaceId={null} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText('Motivo da chamada humana'), { target: { value: 'Validar impacto tributário' } });
    expect(screen.getByRole('button', { name: 'Chamar humano correto' })).toBeDisabled();
    expect(screen.getByText(/Selecione uma conta para carregar deployments/i)).toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });
});
