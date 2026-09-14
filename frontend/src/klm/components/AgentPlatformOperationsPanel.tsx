import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { KlmPanel, KlmPill, KlmSectionTitle } from './Primitives';
import { HumanDecisionDrawer } from './HumanDecisionDrawer';
import { klmApi, type KlmAgent, type KlmAgentPlatformDecision, type KlmAgentPlatformDeployment, type KlmFlowNodeCatalogEntry } from '@/klm/api';

type DeploymentGate =
  | 'definitionVerified'
  | 'roleVerified'
  | 'modelVerified'
  | 'knowledgeVerified'
  | 'autonomyVerified'
  | 'policyVerified'
  | 'entitlementVerified'
  | 'toolsVerified'
  | 'humanQueueVerified'
  | 'budgetVerified'
  | 'testPassed';

type Deployment = KlmAgentPlatformDeployment;
type Decision = KlmAgentPlatformDecision;

const deploymentGateLabels: Record<DeploymentGate, string> = {
  definitionVerified: 'Definição',
  roleVerified: 'Função',
  modelVerified: 'Modelo',
  knowledgeVerified: 'Conhecimento',
  autonomyVerified: 'Autonomia',
  policyVerified: 'Política',
  entitlementVerified: 'Contrato',
  toolsVerified: 'Ferramentas',
  humanQueueVerified: 'Fila humana',
  budgetVerified: 'Orçamento',
  testPassed: 'Teste',
};

const deploymentGateKeys = Object.keys(deploymentGateLabels) as DeploymentGate[];

function tokenCount(value: number | null | undefined) {
  return typeof value === 'number' ? value.toLocaleString('pt-BR') : 'não definido';
}

function currencyUsd(value: number | string | null | undefined) {
  const numberValue = typeof value === 'string' ? Number(value) : value;
  return typeof numberValue === 'number' && Number.isFinite(numberValue)
    ? numberValue.toLocaleString('pt-BR', { style: 'currency', currency: 'USD' })
    : 'não informado';
}

function deploymentGateSummary(item: Deployment) {
  const passed = deploymentGateKeys.filter((key) => item.gates?.[key] === true);
  return { passed, missing: deploymentGateKeys.filter((key) => !passed.includes(key)) };
}

const decisionStatusLabel: Record<string, string> = {
  pending: 'Aguardando decisão',
  decided: 'Decidida',
  expired: 'Expirada',
  cancelled: 'Cancelada',
  failed: 'Falhou',
};

const deliveryStatusLabel: Record<string, string> = {
  pending: 'pendente',
  delivered: 'entregue',
  failed: 'falhou',
  not_configured: 'não configurado',
};

const knowledgeSourceOptions = [
  { value: 'workspace_policy', label: 'Políticas autorizadas do workspace' },
  { value: 'authorized_documents', label: 'Documentos autorizados' },
  { value: 'matter_context', label: 'Contexto de casos autorizados' },
  { value: 'finance_ledger', label: 'Razão financeiro autorizado' },
];

const AGENT_RUNS_PAGE_SIZE = 25;
const AGENT_PLATFORM_PAGE_SIZE = 25;

function deliverySummary(deliveries: Decision['notificationDelivery'], channel: string) {
  const matches = (deliveries ?? []).filter((delivery) => delivery.channel === channel);
  if (matches.length === 0) return 'evidência não exposta pela API';
  return matches.map((delivery) => `${delivery.recipientKind}: ${deliveryStatusLabel[delivery.status] ?? delivery.status} (${delivery.attempts} tentativa${delivery.attempts === 1 ? '' : 's'}${delivery.deliveredAt ? `; recebido ${new Date(delivery.deliveredAt).toLocaleString('pt-BR')}` : ''}${delivery.providerReceiptId ? '; recibo confirmado' : ''}${delivery.lastErrorCode ? `; erro ${delivery.lastErrorCode}` : ''})`).join(' · ');
}

function phoneSummary(item: Decision) {
  const deliveries = item.notificationDelivery?.filter((delivery) => delivery.channel === 'phone')
    ?? item.phoneDelivery?.map((delivery) => ({ ...delivery, channel: 'phone' }));
  if (!deliveries?.length) return 'sem evidência de chamada telefônica';
  return deliveries.map((delivery) => `${delivery.recipientKind}: ${deliveryStatusLabel[delivery.status] ?? delivery.status} (${delivery.attempts} tentativa${delivery.attempts === 1 ? '' : 's'}${delivery.providerReceiptId ? '; recibo confirmado' : ''}${delivery.lastErrorCode ? `; erro ${delivery.lastErrorCode}` : ''})`).join(' · ');
}

function nextEscalationLabel(item: Decision) {
  if (item.status !== 'pending') return 'Encerrada; nenhum novo escalonamento';
  if (item.nextEscalation) {
    const destination = item.nextEscalation.userLabel ?? item.nextEscalation.roleLabel ?? 'próximo nível autorizado';
    return `${destination}${item.nextEscalation.at ? ` em ${new Date(item.nextEscalation.at).toLocaleString('pt-BR')}` : ''}`;
  }
  const current = item.escalationLevel ?? 0;
  const total = item.routingLevels ?? 0;
  if (total > current + 1) return `Nível ${current + 2} de ${total}, após o SLA atual`;
  return 'Dono do escritório é o destino terminal';
}

function routingPathLabel(item: Decision) {
  const labels = (item.routingPathLabels ?? []).map((entry) => typeof entry === 'string' ? entry : entry.label ?? null).filter((label): label is string => Boolean(label?.trim()));
  return labels.length ? labels.join(' → ') : `${item.routingLevels ?? 0} níveis finitos; nível atual ${(item.escalationLevel ?? 0) + 1}`;
}

function requestedRoutingLabel(item: Decision) {
  const roles = (item.requiredRoles ?? []).filter(Boolean).join(', ');
  const skills = (item.requiredSkills ?? []).filter(Boolean).join(', ');
  if (!roles && !skills) return 'Função e competência não expostas pela API desta decisão.';
  return `Função: ${roles || 'qualquer função autorizada'} · competência: ${skills || 'geral'}`;
}

function decisionPurpose(item: Decision) {
  const purpose = item.purpose;
  return typeof purpose === 'string' && purpose.trim() ? purpose.trim() : 'Decisão humana do agente';
}

function nodeAvailability(node: KlmFlowNodeCatalogEntry) {
  const unavailable = node.availability === 'unavailable';
  return {
    unavailable,
    reason: node.availabilityReason ?? node.availability_reason ?? (unavailable ? 'Indisponível no ambiente atual.' : null),
  };
}

export function AgentPlatformOperationsPanel({ canManage, isPlatformRoot = false, currentUserId = null, workspaceId = null }: { canManage: boolean; isPlatformRoot?: boolean; currentUserId?: string | null; workspaceId?: string | null }) {
  const queryClient = useQueryClient();
  const [agentKey, setAgentKey] = useState('');
  const [model, setModel] = useState('');
  const [rolePackage, setRolePackage] = useState('legal_operations');
  const [tools, setTools] = useState<string[]>([]);
  const [knowledgeSources, setKnowledgeSources] = useState<string[]>([]);
  const [autonomyPolicy, setAutonomyPolicy] = useState('A1');
  const [humanRoutingPolicy, setHumanRoutingPolicy] = useState('');
  const [budgetLimit, setBudgetLimit] = useState('100000');
  const [runContextType, setRunContextType] = useState<'matter' | 'client' | 'contract' | 'conversation' | 'task' | 'finance' | 'workspace' | 'platform'>('workspace');
  const [runContextId, setRunContextId] = useState('');
  const [runAgentName, setRunAgentName] = useState('');
  const [runTriggerSource, setRunTriggerSource] = useState('manual_blackbox');
  const [humanRole, setHumanRole] = useState('');
  const [humanSkill, setHumanSkill] = useState('');
  const [humanSkillSearch, setHumanSkillSearch] = useState('');
  const [humanReason, setHumanReason] = useState('');
  const [humanInput, setHumanInput] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Decision | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [nodeSearch, setNodeSearch] = useState('');
  const [runOffset, setRunOffset] = useState(0);
  const [deploymentOffset, setDeploymentOffset] = useState(0);
  const [decisionOffset, setDecisionOffset] = useState(0);
  const deployments = useQuery({ queryKey: ['agent-platform', 'deployments', workspaceId, deploymentOffset], queryFn: () => klmApi.agentPlatformDeployments(workspaceId as string, AGENT_PLATFORM_PAGE_SIZE, deploymentOffset), retry: false, enabled: Boolean(workspaceId) });
  const agentCatalog = useQuery({ queryKey: ['agent-platform', 'catalog', workspaceId], queryFn: () => klmApi.agentPlatformCatalog(workspaceId ?? undefined), retry: false, enabled: Boolean(workspaceId) });
  const runContextNeedsRecord = !['workspace', 'platform'].includes(runContextType);
  const contextOptions = useQuery({
    queryKey: ['agent-platform', 'run-context', runContextType, workspaceId],
    queryFn: () => klmApi.agentPlatformContextOptions(runContextType as Exclude<typeof runContextType, 'workspace' | 'platform'>, workspaceId as string),
    retry: false,
    enabled: Boolean(workspaceId) && runContextNeedsRecord,
  });
  const decisions = useQuery({ queryKey: ['agent-platform', 'decisions', workspaceId, decisionOffset], queryFn: () => klmApi.agentPlatformDecisions(workspaceId as string, AGENT_PLATFORM_PAGE_SIZE, decisionOffset), retry: false, enabled: Boolean(workspaceId), refetchInterval: 2_000 });
  const humanOverview = useQuery({ queryKey: ['agent-platform', 'human-overview', workspaceId], queryFn: () => klmApi.agentHumanOverview(workspaceId ?? undefined), retry: false, enabled: Boolean(workspaceId), refetchInterval: 2_000 });
  const humanRoutingCatalog = useQuery({ queryKey: ['agent-platform', 'human-routing-catalog', workspaceId], queryFn: () => klmApi.agentHumanRoutingCatalog(workspaceId ?? undefined), retry: false, enabled: Boolean(workspaceId) });
  const humanRoutingPolicies = useQuery({ queryKey: ['agent-platform', 'human-routing-policies', workspaceId], queryFn: () => klmApi.agentHumanRoutingPolicies(workspaceId ?? undefined), retry: false, enabled: Boolean(workspaceId) });
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['agent-platform', 'deployments', workspaceId] }),
    queryClient.invalidateQueries({ queryKey: ['agent-platform', 'catalog', workspaceId] }),
    queryClient.invalidateQueries({ queryKey: ['agent-platform', 'decisions', workspaceId] }),
    queryClient.invalidateQueries({ queryKey: ['agent-platform', 'human-overview', workspaceId] }),
    queryClient.invalidateQueries({ queryKey: ['agent-platform', 'runs', workspaceId] }),
  ]);
  useEffect(() => {
    setRunOffset(0);
    setDeploymentOffset(0);
    setDecisionOffset(0);
  }, [workspaceId]);
  useEffect(() => {
    const available = humanRoutingPolicies.data?.policies.filter((item) => item.available) ?? [];
    if (!available.some((item) => item.key === humanRoutingPolicy)) {
      setHumanRoutingPolicy(available[0]?.key ?? '');
    }
  }, [humanRoutingPolicies.data, humanRoutingPolicy]);
  const runs = useQuery({ queryKey: ['agent-platform', 'runs', workspaceId, runOffset], queryFn: () => klmApi.agentPlatformRuns(workspaceId as string, AGENT_RUNS_PAGE_SIZE, runOffset), retry: false, enabled: Boolean(workspaceId), refetchInterval: 2_000 });
  // Keep the callback lazy so React Query refreshes only after the operation
  // has a terminal result.
  const cancelRun = useMutation({ mutationFn: (id: string) => klmApi.cancelAgentRun(id, workspaceId as string), onSuccess: () => refresh() });
  const retryRun = useMutation({ mutationFn: (id: string) => klmApi.retryAgentRun(id, workspaceId as string), onSuccess: () => refresh() });
  const createContextRun = useMutation({
    mutationFn: () => klmApi.createAgentPlatformContextRun({ contextType: runContextType, contextId: ['workspace', 'platform'].includes(runContextType) ? null : runContextId || null, agentName: runAgentName, triggerSource: runTriggerSource }, workspaceId as string),
    onSuccess: async (result) => { setNotice(result.status === 'waiting_human' ? 'Orçamento atingido; o humano correto e o dono foram chamados para revisão.' : 'Execução criada e encaminhada ao worker com o contexto selecionado.'); setRunContextId(''); await refresh(); },
    onError: () => setNotice('A execução não foi criada. Revise o agente, o contexto e a disponibilidade do deployment.'),
  });
  const provideInput = useMutation({ mutationFn: () => klmApi.provideAgentRunHumanInput(selected?.agentRunId as string, humanInput, workspaceId as string), onSuccess: async () => { setNotice('Dados recebidos e execução reenfileirada no checkpoint correto.'); setHumanInput({}); await refresh(); }, onError: () => setNotice('Os dados não puderam ser aplicados ao checkpoint desta execução.') });
  const ownerChannel = useQuery({ queryKey: ['agent-platform', 'owner-alert-channel', workspaceId], queryFn: () => klmApi.agentWorkspaceOwnerAlertChannel(workspaceId as string), retry: false, enabled: Boolean(workspaceId) });
  const nodes = useQuery({ queryKey: ['agent-platform', 'nodes'], queryFn: klmApi.agentPlatformNodes, retry: false });
  const create = useMutation({
    mutationFn: () => {
      const selectedDefinition = (agentCatalog.data?.agents ?? []).find((item) => item.key === agentKey);
      if (!selectedDefinition) throw new Error('agent_definition_not_selected');
      return klmApi.createAgentDeployment({ agent_key: selectedDefinition.key, role_package: rolePackage, version: selectedDefinition.version ?? 1, config: { definitionVersion: selectedDefinition.version ?? 1, rolePackage, model, tools, knowledgeSources, autonomyPolicy, humanRoutingPolicy, budgetPolicy: { monthlyTokenLimit: Number(budgetLimit) } }, gates: {} }, workspaceId ?? undefined);
    },
    onSuccess: async () => { setAgentKey(''); setModel(''); setRolePackage('legal_operations'); setTools([]); setKnowledgeSources([]); setAutonomyPolicy('A1'); setHumanRoutingPolicy(''); setNotice('Deployment criado como rascunho; nenhum gate foi aceito do navegador.'); await refresh(); },
    onError: () => setNotice('Não foi possível criar o deployment.'),
  });
  const publish = useMutation({ mutationFn: (id: string) => klmApi.publishAgentDeployment(id, workspaceId ?? undefined), onSuccess: refresh });
  const pause = useMutation({ mutationFn: (id: string) => klmApi.pauseAgentDeployment(id, workspaceId ?? undefined), onSuccess: refresh });
  const validate = useMutation({ mutationFn: (id: string) => klmApi.validateAgentDeployment(id, workspaceId ?? undefined), onSuccess: async (result) => { const gates = (result.gates ?? {}) as Record<string, unknown>; const proved = deploymentGateKeys.every((key) => gates[key] === true); setNotice(proved ? 'Política, fila humana, orçamento e dry-run local comprovados pelo servidor.' : 'Validação concluída parcialmente; os gates pendentes continuam bloqueando a publicação.'); await refresh(); }, onError: () => setNotice('O deployment ainda não passou pelos gates do servidor.') });
  const acknowledge = useMutation({ mutationFn: (id: string) => klmApi.acknowledgeAgentDecision(id, workspaceId ?? undefined), onSuccess: async () => { setNotice('Chamada reconhecida.'); await refresh(); }, onError: () => setNotice('A chamada não pôde ser reconhecida por este usuário.') });
  const requestHuman = useMutation({
    mutationFn: () => klmApi.requestAgentHumanDecision({ reason: humanReason.trim(), requiredRoles: effectiveHumanRole ? [effectiveHumanRole] : [], requiredSkills: effectiveHumanSkill ? [effectiveHumanSkill] : [], slaMinutes: 60, maxHops: 4 }, workspaceId ?? undefined),
    onSuccess: async () => { setHumanReason(''); setHumanSkill(''); setHumanSkillSearch(''); setNotice('Responsável chamado; o dono acompanha no sistema e por telefone quando configurado.'); await refresh(); },
    onError: () => setNotice('Nenhum humano compatível pôde ser chamado; o sistema não entrou em espera.'),
  });
  const resolve = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'approved' | 'rejected' }) => klmApi.resolveAgentDecision(id, { decision }, workspaceId ?? undefined),
    onSuccess: async (result) => { setSelected(null); setNotice(result.resumedFlowRunId ? 'Decisão registrada e fluxo retomado.' : 'Decisão registrada; o estado final foi persistido.'); await refresh(); },
    onError: () => setNotice('A decisão já foi consumida, expirou ou pertence a outro responsável.'),
  });
  const deploymentPage = deployments.data ?? { items: [], total: 0, limit: AGENT_PLATFORM_PAGE_SIZE, offset: 0, hasMore: false };
  const decisionPage = decisions.data ?? { items: [], total: 0, limit: AGENT_PLATFORM_PAGE_SIZE, offset: 0, hasMore: false };
  const deploymentItems = deploymentPage.items;
  const decisionItems = decisionPage.items;
  const pending = decisionItems.filter((item) => item.status === 'pending');
  const terminal = decisionItems.filter((item) => item.status !== 'pending').slice(0, 8);
  const ownerNeedsAttention = Boolean(humanOverview.data && (humanOverview.data.overdue > 0 || humanOverview.data.phone.failed > 0 || humanOverview.data.phone.notConfigured > 0));
  const matchingNodes = (nodes.data ?? []).filter((node) => JSON.stringify(node).toLowerCase().includes(nodeSearch.toLowerCase().trim()));
  const visibleNodes = matchingNodes.filter((node) => !nodeAvailability(node).unavailable);
  const unavailableNodes = matchingNodes.filter((node) => nodeAvailability(node).unavailable);
  const selectedAgent = (agentCatalog.data?.agents ?? []).find((item) => item.key === agentKey) as KlmAgent | undefined;
  const availableModels = selectedAgent?.allowedModels ?? [];
  const availableTools = selectedAgent?.tools ?? [];
  const activeRunAgents = deploymentItems.filter((item) => item.status === 'active').map((item) => item.agentKey ?? item.agent_key).filter((item): item is string => Boolean(item));
  const runContextOptions = contextOptions.data?.items ?? [];
  const runContextLoading = runContextNeedsRecord && contextOptions.isPending;
  const runContextError = runContextNeedsRecord && contextOptions.isError;
  const routingRoles = humanRoutingCatalog.data?.roles ?? [];
  const routingSkills = humanRoutingCatalog.data?.skills ?? [];
  const routingTeams = humanRoutingCatalog.data?.teams ?? [];
  const effectiveHumanRole = routingRoles.some((item) => item.key === humanRole)
    ? humanRole
    : routingRoles[0]?.key ?? '';
  const effectiveHumanSkill = routingSkills.some((item) => item.key === humanSkill)
    ? humanSkill
    : '';
  const matchingHumanSkills = routingSkills.filter((item) => item.label.toLocaleLowerCase('pt-BR').includes(humanSkillSearch.trim().toLocaleLowerCase('pt-BR')) || item.key.includes(humanSkillSearch.trim().toLowerCase()));
  const canRequestHuman = Boolean(workspaceId)
    && !humanRoutingCatalog.isPending
    && !humanRoutingCatalog.isError
    && Boolean(effectiveHumanRole)
    && humanReason.trim().length >= 8;
  const selectAgent = (nextAgentKey: string) => {
    const definition = (agentCatalog.data?.agents ?? []).find((item) => item.key === nextAgentKey);
    setAgentKey(nextAgentKey);
    setModel(definition?.allowedModels?.[0] ?? '');
    setTools([...(definition?.tools ?? [])]);
    setAutonomyPolicy(definition?.defaultAutonomy ?? 'A1');
  };
  const toggleSelection = (value: string, current: string[], setCurrent: (value: string[]) => void) => {
    setCurrent(current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  };
  const canDecideSelected = Boolean(selected && (isPlatformRoot || (currentUserId && (selected.assignedUserId === currentUserId || selected.ownerUserId === currentUserId))));
  const decisionDisabledReason = !selected || selected.status !== 'pending'
    ? 'Esta decisão não está mais pendente.'
    : !currentUserId && !isPlatformRoot
      ? 'Não foi possível confirmar a sessão atual. Atualize a página antes de decidir.'
      : !canDecideSelected
        ? 'A decisão só pode ser tomada pelo responsável atribuído, pelo dono do escritório ou pelo Super Admin.'
        : undefined;
  return <KlmPanel className="p-4" data-testid="agent-platform-operations">
    <KlmSectionTitle title="Deployments e chamadas humanas" description="Operação real do runtime; nenhuma ação externa é presumida como entregue." />
    {!workspaceId && <p className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs" role="status">Selecione uma conta para carregar deployments, execuções e decisões. O runtime não usa um workspace implícito.</p>}
    {notice && <p className="mt-3 rounded-md border bg-muted/40 p-2 text-xs" role="status">{notice}</p>}
    <section aria-label="Acompanhamento do dono do escritório" className={`mt-4 rounded-lg border p-3 ${ownerNeedsAttention ? 'border-amber-500/60 bg-amber-500/5' : ''}`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div><h3 className="text-xs font-semibold">Acompanhamento do dono</h3><p className="mt-1 text-[10px] text-muted-foreground">Toda chamada humana entra nesta visão. O sistema diferencia registro interno de entrega confirmada por telefone.</p></div>
        <KlmPill tone={humanOverview.isError || ownerChannel.isError ? 'red' : ownerNeedsAttention ? 'gold' : 'green'}>{humanOverview.isPending || ownerChannel.isPending ? 'carregando' : humanOverview.isError || ownerChannel.isError ? 'indisponível' : ownerNeedsAttention ? 'requer atenção' : 'acompanhamento ativo'}</KlmPill>
      </div>
      {humanOverview.data ? <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {[['Abertas', humanOverview.data.open], ['Reconhecidas', humanOverview.data.acknowledged], ['Vencidas', humanOverview.data.overdue], ['Escaladas', humanOverview.data.escalated], ['Telefone entregue', humanOverview.data.phone.delivered], ['Telefone com atenção', humanOverview.data.phone.failed + humanOverview.data.phone.notConfigured]].map(([label, value]) => <div key={String(label)} className="rounded-md border bg-background p-2"><span className="block text-[10px] text-muted-foreground">{label}</span><strong className="text-sm">{value}</strong></div>)}
      </div> : null}
      <p className="mt-2 text-[10px] text-muted-foreground">Canal telefônico do dono: {ownerChannel.isError ? 'não foi possível consultar' : ownerChannel.data?.configured ? 'configurado; cada chamada ainda exige recibo individual' : 'não configurado — o alerta no sistema permanece obrigatório'}.</p>
    </section>
    <section aria-label="Catálogo de nós do runtime" className="mt-4 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-xs font-semibold">Catálogo de nós autorizados</h3><p className="text-[10px] text-muted-foreground">Somente nós retornados pelo endpoint seguro; nenhum executor genérico é criado aqui.</p></div><Input aria-label="Buscar nó do runtime" value={nodeSearch} onChange={(event) => setNodeSearch(event.target.value)} placeholder="Buscar por tipo, categoria ou risco" className="h-9 max-w-xs text-xs" /></div>
      {nodes.isError ? <p className="mt-3 text-xs text-destructive">Catálogo indisponível ou sem permissão.</p> : <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{visibleNodes.map((item, index) => { const label = String(item.label ?? item.type ?? `Nó ${index + 1}`); return <article key={`${label}-${index}`} className="rounded-md border p-3 text-xs"><strong className="block truncate">{label}</strong><p className="mt-1 text-[10px] text-muted-foreground">Tipo: {item.type} · Categoria: {item.category}</p><p className="text-[10px] text-muted-foreground">Risco: {String((item as Record<string, unknown>).risk ?? 'não informado')} · Modo: {String((item as Record<string, unknown>).mode ?? (item as Record<string, unknown>).executionMode ?? 'não informado')}</p><p className="text-[10px] text-muted-foreground">Permissão/gate: {String((item as Record<string, unknown>).permission ?? (item as Record<string, unknown>).gate ?? 'servidor')}</p></article>; })}</div>}
      {!nodes.isPending && !nodes.isError && visibleNodes.length === 0 && <p className="mt-3 text-xs text-muted-foreground">Nenhum nó corresponde à busca.</p>}
      {unavailableNodes.length > 0 ? <details className="mt-3 rounded-md border border-amber-500/40 p-2 text-xs"><summary className="cursor-pointer font-medium">{unavailableNodes.length} nó(s) indisponível(is) neste ambiente</summary><div className="mt-2 space-y-2">{unavailableNodes.map((node) => <div key={node.type} aria-disabled="true" className="rounded border bg-muted/40 p-2 text-muted-foreground"><strong>{node.label}</strong><span className="block text-[10px]">{nodeAvailability(node).reason}</span></div>)}</div></details> : null}
    </section>
    {canManage && <form aria-label="Novo deployment governado" className="mt-4 grid gap-2 rounded-lg border p-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={(event) => { event.preventDefault(); if (workspaceId && selectedAgent) create.mutate(); }}>
      <label className="text-xs">Agente
        <select aria-label="Agente do deployment" value={agentKey} onChange={(event) => selectAgent(event.target.value)} disabled={agentCatalog.isPending || agentCatalog.isError} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm">
          <option value="">{agentCatalog.isPending ? 'Carregando catálogo…' : agentCatalog.isError ? 'Catálogo indisponível' : 'Selecione um agente'}</option>
          {(agentCatalog.data?.agents ?? []).map((item) => <option key={item.key} value={item.key}>{item.name} · v{item.version ?? 1}</option>)}
        </select>
      </label>
      <label className="text-xs">Modelo autorizado
        <select aria-label="Modelo autorizado do agente" value={model} onChange={(event) => setModel(event.target.value)} disabled={!selectedAgent} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm">
          <option value="">Selecione um modelo</option>
          {availableModels.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </label>
      <label className="text-xs">Função operacional do agente
        <select aria-label="Função operacional do agente" value={rolePackage} onChange={(event) => setRolePackage(event.target.value)} disabled={agentCatalog.isPending || agentCatalog.isError} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm">
          {(agentCatalog.data?.rolePackages ?? []).map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select>
      </label>
      <fieldset aria-label="Ferramentas autorizadas" className="rounded-md border p-2 text-xs"><legend className="px-1">Ferramentas autorizadas</legend>{!selectedAgent ? <p className="text-[10px] text-muted-foreground">Selecione o agente para carregar as ferramentas permitidas.</p> : availableTools.length === 0 ? <p className="text-[10px] text-muted-foreground">Este agente não possui ferramenta vinculável.</p> : <div className="grid gap-1">{availableTools.map((item) => <label key={item} className="flex min-h-8 items-center gap-2"><input type="checkbox" checked={tools.includes(item)} onChange={() => toggleSelection(item, tools, setTools)} />{item}</label>)}</div>}</fieldset>
      <fieldset className="rounded-md border p-2 text-xs"><legend className="px-1">Fontes de conhecimento</legend><div className="grid gap-1">{knowledgeSourceOptions.map((item) => <label key={item.value} className="flex min-h-8 items-center gap-2"><input type="checkbox" checked={knowledgeSources.includes(item.value)} onChange={() => toggleSelection(item.value, knowledgeSources, setKnowledgeSources)} />{item.label}</label>)}</div></fieldset>
      <label className="text-xs">Autonomia permitida
        <select aria-label="Autonomia permitida" value={autonomyPolicy} onChange={(event) => setAutonomyPolicy(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm">
          <option value="A0">A0 — somente preparação</option>
          <option value="A1">A1 — rascunhos internos</option>
          <option value="A2">A2 — operação local aprovada</option>
          <option value="A3">A3 — requer gate humano</option>
          <option value="A4">A4 — reservado a políticas futuras</option>
        </select>
      </label>
      <label className="text-xs">Roteamento humano
        <select aria-label="Política de roteamento humano" value={humanRoutingPolicy} onChange={(event) => setHumanRoutingPolicy(event.target.value)} disabled={humanRoutingPolicies.isPending || humanRoutingPolicies.isError} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm">
          <option value="">{humanRoutingPolicies.isPending ? 'Carregando políticas…' : humanRoutingPolicies.isError ? 'Catálogo de políticas indisponível' : 'Selecione uma política publicada'}</option>
          {(humanRoutingPolicies.data?.policies ?? []).filter((item) => item.available).map((item) => <option key={item.key} value={item.key}>{item.label}{item.specialistAvailable ? '' : ' · direto ao dono'}</option>)}
        </select>
      </label>
      <Input aria-label="Orçamento mensal de tokens" type="number" min="1" value={budgetLimit} onChange={(event) => setBudgetLimit(event.target.value)} className="min-h-11" />
      <p className="flex min-h-11 items-center rounded-md border px-3 text-xs text-muted-foreground sm:col-span-2">Versão da definição: {selectedAgent?.version ?? 'selecionar agente'}. O servidor confere agente, ferramentas permitidas, entitlement, fallback humano e orçamento; campos preenchidos aqui nunca aprovam gates.</p>
      <p className="rounded-md border bg-muted/30 p-2 text-[10px] text-muted-foreground sm:col-span-2 lg:col-span-3">Catálogo do servidor: {humanRoutingPolicies.data?.version ?? 'não carregado'}. A política selecionada é revalidada contra pessoas disponíveis e dono terminal; a decisão só aguarda após as notificações duráveis.</p>
      <Button type="submit" className="min-h-11" disabled={!workspaceId || !selectedAgent || !rolePackage || !model || tools.length === 0 || knowledgeSources.length === 0 || !humanRoutingPolicy || Number(budgetLimit) <= 0 || create.isPending}>Criar deployment</Button>
    </form>}
    {canManage && <form aria-label="Nova execução contextual do agente" className="mt-4 grid gap-2 rounded-lg border p-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={(event) => { event.preventDefault(); if (workspaceId && runAgentName && (!runContextNeedsRecord || runContextId)) createContextRun.mutate(); }}>
      <div className="sm:col-span-2 lg:col-span-3"><h3 className="text-xs font-semibold">Executar agente com contexto</h3><p className="mt-1 text-[10px] text-muted-foreground">A execução nasce em fila, com contexto explícito e chave de idempotência gerada pelo sistema. Não há UUID, payload bruto ou ferramenta acionada diretamente.</p></div>
      <label className="text-xs">Agente ativo
        <select aria-label="Agente ativo para execução" value={runAgentName} onChange={(event) => setRunAgentName(event.target.value)} disabled={deployments.isPending || activeRunAgents.length === 0} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm"><option value="">{deployments.isPending ? 'Carregando deployments…' : activeRunAgents.length ? 'Selecione um agente ativo' : 'Nenhum deployment ativo'}</option>{activeRunAgents.map((item) => <option key={item} value={item}>{item}</option>)}</select>
      </label>
      <label className="text-xs">Tipo de contexto
        <select aria-label="Tipo de contexto da execução" value={runContextType} onChange={(event) => { const next = event.target.value as typeof runContextType; setRunContextType(next); setRunContextId(''); }} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm"><option value="workspace">Workspace atual</option><option value="client">Cliente</option><option value="contract">Contrato</option><option value="matter">Caso</option><option value="conversation">Conversa</option><option value="task">Tarefa</option><option value="finance">Centro de custo financeiro</option>{isPlatformRoot ? <option value="platform">Plataforma</option> : null}</select>
      </label>
      <label className="text-xs">Origem
        <select aria-label="Origem da execução" value={runTriggerSource} onChange={(event) => setRunTriggerSource(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm"><option value="manual_blackbox">Ação manual no Blackbox</option><option value="manual_app">Ação manual no app</option><option value="workflow_retry">Reprocessamento autorizado</option></select>
      </label>
      {runContextNeedsRecord ? <label className="text-xs">{runContextType === 'task' ? 'Tarefa' : 'Registro de contexto'}
        <select aria-label="Registro de contexto da execução" value={runContextId} onChange={(event) => setRunContextId(event.target.value)} disabled={runContextLoading || runContextError} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm"><option value="">{runContextLoading ? 'Carregando registros…' : runContextError ? 'Registros indisponíveis' : 'Selecione o registro'}</option>{runContextOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      </label> : <p className="flex min-h-11 items-center rounded-md border px-3 text-xs text-muted-foreground">O contexto {runContextType === 'platform' ? 'da plataforma' : 'do workspace'} é aplicado pelo servidor; nenhum ID é necessário.</p>}
      {runContextError ? <p className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive sm:col-span-2 lg:col-span-3">Não foi possível carregar os registros deste contexto. A execução fica bloqueada; não há campo técnico como alternativa.</p> : null}
      <Button type="submit" className="min-h-11" disabled={!workspaceId || !runAgentName || (runContextNeedsRecord && !runContextId) || runContextLoading || runContextError || createContextRun.isPending}>{createContextRun.isPending ? 'Criando execução…' : 'Criar execução governada'}</Button>
    </form>}
    {canManage && <form className="mt-4 grid gap-2 rounded-lg border p-3 sm:grid-cols-2 lg:grid-cols-4" onSubmit={(event) => { event.preventDefault(); if (canRequestHuman) requestHuman.mutate(); }}>
      <div className="sm:col-span-2 lg:col-span-4"><h3 className="text-xs font-semibold">Chamar humano correto</h3><p className="mt-1 text-[10px] text-muted-foreground">Funções, competências e equipes vêm do workspace selecionado. Nenhum identificador técnico, pessoa inexistente ou competência digitada é enviado ao runtime.</p></div>
      <label className="text-xs">Função responsável<select aria-label="Função humana responsável" value={effectiveHumanRole} onChange={(event) => setHumanRole(event.target.value)} disabled={humanRoutingCatalog.isPending || humanRoutingCatalog.isError || routingRoles.length === 0} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3"><option value="">{humanRoutingCatalog.isPending ? 'Carregando funções…' : humanRoutingCatalog.isError ? 'Funções indisponíveis' : 'Selecione uma função'}</option>{routingRoles.map((item) => <option key={item.key} value={item.key}>{item.label} · {item.routableCount} rota{item.routableCount === 1 ? '' : 's'} {item.routableCount === 1 ? 'disponível' : 'disponíveis'}</option>)}</select></label>
      <div className="grid gap-2"><label className="text-xs">Buscar competência<Input aria-label="Buscar competência humana" value={humanSkillSearch} onChange={(event) => setHumanSkillSearch(event.target.value)} placeholder="Filtrar competências" disabled={humanRoutingCatalog.isPending || humanRoutingCatalog.isError || routingSkills.length === 0} className="mt-1 min-h-11" /></label><label className="text-xs">Competência/equipe<select aria-label="Competência humana necessária" value={effectiveHumanSkill} onChange={(event) => setHumanSkill(event.target.value)} disabled={humanRoutingCatalog.isPending || humanRoutingCatalog.isError || routingSkills.length === 0 || humanRoutingCatalog.data?.directOwnerOnly} className="mt-1 min-h-11 w-full rounded-md border border-input bg-background px-3"><option value="">{humanRoutingCatalog.data?.directOwnerOnly ? 'Encaminhar diretamente ao dono' : routingSkills.length ? 'Sem competência específica' : 'Nenhuma competência roteável'}</option>{matchingHumanSkills.map((item) => <option key={item.key} value={item.key}>{item.label} · {item.routableCount} rota{item.routableCount === 1 ? '' : 's'} {item.routableCount === 1 ? 'disponível' : 'disponíveis'}</option>)}</select></label></div>
      <label className="text-xs sm:col-span-2 lg:col-span-1">Motivo<Input aria-label="Motivo da chamada humana" value={humanReason} onChange={(event) => setHumanReason(event.target.value)} minLength={8} placeholder="O que precisa ser decidido" className="mt-1 min-h-11" /></label>
      <Button type="submit" className="min-h-11 self-end" disabled={!canRequestHuman || requestHuman.isPending}>{requestHuman.isPending ? 'Chamando…' : 'Chamar humano correto'}</Button>
      {humanRoutingCatalog.isError ? <p className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-[10px] text-destructive sm:col-span-2 lg:col-span-4">Não foi possível carregar uma rota humana verificável. A chamada está bloqueada; o sistema não cria espera sem destinatário.</p> : null}
      {humanRoutingCatalog.data?.directOwnerOnly ? <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[10px] text-muted-foreground sm:col-span-2 lg:col-span-4">{humanRoutingCatalog.data.ownerFallback.reason} Nenhuma competência será enviada como valor fictício.</p> : <p className="text-[10px] text-muted-foreground sm:col-span-2 lg:col-span-4">Equipes roteáveis agora: {routingTeams.length ? routingTeams.map((item) => `${item.label} (${item.routableCount})`).join(' · ') : 'nenhuma equipe especializada disponível'}. Rota solicitada: função {effectiveHumanRole || 'não disponível'} → competência {effectiveHumanSkill || 'geral'} → até 4 níveis → dono. A espera só nasce após notificações duráveis.</p>}
    </form>}
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      <section aria-label="Deployments dos agentes" className="space-y-2">
        <h3 className="text-xs font-semibold">Deployments</h3>
        {deployments.isPending ? <p className="text-xs text-muted-foreground">Carregando…</p> : deployments.isError ? <p className="text-xs text-destructive">Deployments indisponíveis.</p> : deploymentItems.length === 0 ? <p className="text-xs text-muted-foreground">Nenhum deployment criado.</p> : deploymentItems.map((item) => {
          const gateSummary = deploymentGateSummary(item);
          const testEvidence = item.config?.testEvidence ?? { kind: item.gates?.testKind, externalModelCalled: item.gates?.externalModelCalled };
          const budgetUsage = item.budgetUsage;
          return <article key={item.id} className="rounded-lg border p-3">
            <div className="flex items-start justify-between gap-3"><span className="min-w-0"><strong className="block truncate text-xs">{item.agentKey ?? item.agent_key}</strong><span className="text-[10px] text-muted-foreground">Definição {item.config?.definitionVersion ?? item.version} · deployment v{item.version} · modelo {item.config?.model ?? 'não informado'}</span></span>
              <span className="flex shrink-0 items-center gap-2"><KlmPill tone={item.status === 'active' ? 'green' : item.status === 'paused' ? 'gold' : 'muted'}>{item.status}</KlmPill>{canManage && (item.status === 'active' ? <Button size="sm" variant="outline" onClick={() => pause.mutate(item.id)}>Pausar</Button> : gateSummary.missing.length === 0 ? <Button size="sm" onClick={() => publish.mutate(item.id)}>Publicar</Button> : <Button size="sm" variant="outline" onClick={() => validate.mutate(item.id)}>Validar gates</Button>)}</span>
            </div>
            <dl className="mt-2 grid gap-1 text-[10px] text-muted-foreground sm:grid-cols-2"><div><dt className="inline font-medium text-foreground">Função: </dt><dd className="inline">{item.rolePackage ?? item.config?.rolePackage ?? 'não informada'}</dd></div><div><dt className="inline font-medium text-foreground">Ferramentas: </dt><dd className="inline">{item.config?.tools?.join(', ') || 'não informado'}</dd></div><div><dt className="inline font-medium text-foreground">Conhecimento: </dt><dd className="inline">{item.config?.knowledgeSources?.join(', ') || 'sem fontes vinculadas'}</dd></div><div><dt className="inline font-medium text-foreground">Autonomia: </dt><dd className="inline">{item.config?.autonomyPolicy ?? 'não informada'}</dd></div><div><dt className="inline font-medium text-foreground">Fila humana: </dt><dd className="inline">{item.config?.humanRoutingPolicy ?? 'não informada'}</dd></div><div><dt className="inline font-medium text-foreground">Orçamento mensal: </dt><dd className="inline">{tokenCount(budgetUsage?.limitTokens ?? item.config?.budgetPolicy?.monthlyTokenLimit)} tokens</dd></div><div><dt className="inline font-medium text-foreground">Uso neste ciclo: </dt><dd className="inline">{tokenCount(budgetUsage?.usedTokens)} tokens · saldo {tokenCount(budgetUsage?.remainingTokens)}</dd></div><div><dt className="inline font-medium text-foreground">Ciclo iniciado: </dt><dd className="inline">{budgetUsage?.periodStart ? new Date(budgetUsage.periodStart).toLocaleDateString('pt-BR') : 'não informado'}</dd></div><div><dt className="inline font-medium text-foreground">Teste: </dt><dd className="inline">{testEvidence.kind ?? 'ainda não validado'}{testEvidence.externalModelCalled === false ? ' · sem chamada externa' : ''}</dd></div></dl>
            {budgetUsage?.exhausted ? <p className="mt-2 rounded-md border border-amber-500/50 bg-amber-500/5 p-2 text-[10px] text-amber-800 dark:text-amber-300" role="status">Orçamento do deployment esgotado. Novas execuções ficam pausadas até a renovação do ciclo ou alteração autorizada do orçamento.</p> : null}
            <div className="mt-3 flex flex-wrap gap-1" aria-label={`Gates do deployment ${item.agentKey ?? item.agent_key}`}>
              {deploymentGateKeys.map((gate) => <KlmPill key={gate} tone={item.gates?.[gate] === true ? 'green' : 'gold'}>{deploymentGateLabels[gate]}: {item.gates?.[gate] === true ? 'ok' : 'pendente'}</KlmPill>)}
            </div>
            <p className="mt-2 text-[10px] text-muted-foreground">{gateSummary.missing.length === 0 ? `Todos os ${gateSummary.passed.length} gates foram calculados pelo servidor${item.gates?.verifiedAt ? ` em ${new Date(item.gates.verifiedAt).toLocaleString('pt-BR')}` : ''}.` : `Pendente: ${gateSummary.missing.map((gate) => deploymentGateLabels[gate]).join(', ')}. Publicação permanece bloqueada.`}</p>
          </article>;
        })}
        {deploymentPage.total > 0 ? <nav aria-label="Paginação de deployments de agentes" className="flex flex-wrap items-center justify-between gap-2 border-t pt-2">
          <p className="text-[10px] text-muted-foreground">Exibindo {deploymentPage.offset + 1}–{deploymentPage.offset + deploymentPage.items.length} de {deploymentPage.total} deployments.</p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setDeploymentOffset((current) => Math.max(0, current - AGENT_PLATFORM_PAGE_SIZE))} disabled={deployments.isFetching || deploymentPage.offset === 0}>Anterior deployments</Button>
            <Button size="sm" variant="outline" onClick={() => setDeploymentOffset((current) => current + AGENT_PLATFORM_PAGE_SIZE)} disabled={deployments.isFetching || !deploymentPage.hasMore}>Próxima deployments</Button>
          </div>
        </nav> : null}
      </section>
      <section aria-label="Execuções recentes dos agentes" className="space-y-2">
        <h3 className="text-xs font-semibold">Execuções recentes</h3>
        {runs.isPending ? <p className="text-xs text-muted-foreground">Carregando execuções…</p> : runs.isError ? <p className="text-xs text-destructive">Execuções indisponíveis.</p> : (runs.data?.items ?? []).length === 0 ? <p className="text-xs text-muted-foreground">Nenhuma execução registrada para esta conta.</p> : (runs.data?.items ?? []).map((run) => <article key={run.id} data-agent-run-id={run.id} className="rounded-lg border p-3 text-xs"><div className="flex items-center justify-between gap-2"><strong className="truncate">{run.agentName}</strong><KlmPill tone={run.status === 'completed' ? 'green' : run.status === 'failed' ? 'red' : 'gold'}>{run.status}</KlmPill></div><p className="mt-1 text-[10px] text-muted-foreground">Origem: {run.triggerSource} · Tentativas: {run.attemptCount} · Criado: {new Date(run.createdAt).toLocaleString('pt-BR')}</p><p className="mt-1 text-[10px] text-muted-foreground">Deployment: {run.deploymentId ?? 'não vinculado'} · Tokens: {tokenCount((run.inputTokens ?? 0) + (run.outputTokens ?? 0))} (entrada {tokenCount(run.inputTokens)} · saída {tokenCount(run.outputTokens)}) · Custo: {currencyUsd(run.costUsd)}</p><div className="mt-2 flex gap-2">{canManage && ['failed', 'cancelled', 'paused_budget'].includes(run.status) ? <Button size="sm" variant="outline" onClick={() => retryRun.mutate(run.id)}>Reenfileirar</Button> : null}{canManage && !['completed', 'failed', 'cancelled'].includes(run.status) ? <Button size="sm" variant="outline" onClick={() => cancelRun.mutate(run.id)}>Cancelar</Button> : null}</div></article>)}
        {runs.data && runs.data.total > 0 ? <nav aria-label="Paginação de execuções de agentes" className="flex flex-wrap items-center justify-between gap-2 border-t pt-2">
          <p className="text-[10px] text-muted-foreground">Exibindo {runs.data.offset + 1}–{runs.data.offset + runs.data.items.length} de {runs.data.total} execuções.</p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setRunOffset((current) => Math.max(0, current - AGENT_RUNS_PAGE_SIZE))} disabled={runs.isFetching || runs.data.offset === 0}>Anterior</Button>
            <Button size="sm" variant="outline" onClick={() => setRunOffset((current) => current + AGENT_RUNS_PAGE_SIZE)} disabled={runs.isFetching || !runs.data.hasMore}>Próxima</Button>
          </div>
        </nav> : null}
      </section>
      <section aria-label="Intervenções humanas pendentes" className="space-y-2">
        <h3 className="text-xs font-semibold">Chamadas humanas</h3>
        {decisions.isPending ? <p className="text-xs text-muted-foreground">Carregando…</p> : decisions.isError ? <p className="text-xs text-destructive">Fila humana indisponível.</p> : pending.length === 0 ? <p className="text-xs text-muted-foreground">Nenhuma chamada pendente.</p> : pending.map((item) => <button key={item.id} type="button" data-agent-run-id={item.agentRunId ?? undefined} onClick={() => setSelected(item)} className="block min-h-11 w-full rounded-lg border p-3 text-left hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="flex items-center justify-between gap-2"><strong className="text-xs">{item.assignedUserLabel ?? 'Responsável não disponível'}</strong><KlmPill tone={item.escalationLevel ? 'gold' : 'green'}>{item.escalationLevel ? `Nível ${item.escalationLevel + 1}` : 'Nível 1'}</KlmPill></span>
          <span className="mt-1 block text-[10px] text-muted-foreground">Finalidade: {decisionPurpose(item)}</span>
          <span className="mt-1 block text-[10px] text-muted-foreground">{requestedRoutingLabel(item)}</span>
          <span className="mt-1 block text-[10px] text-muted-foreground">Rota efetiva: {routingPathLabel(item)} · fallback: {item.ownerUserLabel ?? 'dono do escritório'}</span>
          <span className="mt-1 block text-[10px] text-muted-foreground">SLA: {item.dueAt ? new Date(item.dueAt).toLocaleString('pt-BR') : 'indisponível'} · Próximo: {nextEscalationLabel(item)}</span>
          <span className="mt-1 block text-[10px] text-muted-foreground">Sistema: {deliverySummary(item.notificationDelivery, 'in_app')}</span>
          <span className="mt-1 block text-[10px] text-muted-foreground">Telefone: {phoneSummary(item)}</span>
          <span className="mt-1 block text-[10px] text-muted-foreground">Dono acompanhando: {item.ownerUserLabel ?? 'indisponível'}</span>
        </button>)}
        {terminal.length > 0 ? <div className="pt-2"><h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Últimos estados terminais</h4>{terminal.map((item) => <button key={item.id} type="button" onClick={() => setSelected(item)} className="mb-2 block min-h-11 w-full rounded-lg border p-3 text-left hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><span className="flex items-center justify-between gap-2"><strong className="text-xs">{item.assignedUserLabel ?? 'Responsável não disponível'}</strong><KlmPill tone={item.status === 'decided' ? 'green' : item.status === 'expired' || item.status === 'failed' ? 'red' : 'muted'}>{decisionStatusLabel[item.status] ?? item.status}</KlmPill></span><span className="mt-1 block text-[10px] text-muted-foreground">{nextEscalationLabel(item)} · Telefone: {phoneSummary(item)}</span></button>)}</div> : null}
        {decisionPage.total > 0 ? <nav aria-label="Paginação de chamadas humanas" className="flex flex-wrap items-center justify-between gap-2 border-t pt-2">
          <p className="text-[10px] text-muted-foreground">Exibindo {decisionPage.offset + 1}–{decisionPage.offset + decisionPage.items.length} de {decisionPage.total} chamadas.</p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setDecisionOffset((current) => Math.max(0, current - AGENT_PLATFORM_PAGE_SIZE))} disabled={decisions.isFetching || decisionPage.offset === 0}>Anterior chamadas</Button>
            <Button size="sm" variant="outline" onClick={() => setDecisionOffset((current) => current + AGENT_PLATFORM_PAGE_SIZE)} disabled={decisions.isFetching || !decisionPage.hasMore}>Próxima chamadas</Button>
          </div>
        </nav> : null}
      </section>
    </div>
    <HumanDecisionDrawer open={Boolean(selected)} onOpenChange={(open) => !open && setSelected(null)} title="Decisão humana do agente" description={selected ? `${decisionPurpose(selected)}. A decisão é single-use e retoma somente o checkpoint vinculado.` : 'A decisão é single-use e retoma somente o checkpoint vinculado.'} onApprove={() => selected && resolve.mutate({ id: selected.id, decision: 'approved' })} onReject={() => selected && resolve.mutate({ id: selected.id, decision: 'rejected' })} approveLabel="Aprovar e retomar" disabled={Boolean(decisionDisabledReason) || resolve.isPending} disabledReason={decisionDisabledReason} context={selected ? [
      { label: 'Finalidade', value: decisionPurpose(selected) },
      { label: 'Responsável', value: selected.assignedUserLabel ?? 'Indisponível' },
      { label: 'Dono acompanhando', value: selected.ownerUserLabel ?? 'Indisponível' },
      { label: 'Prazo', value: selected.dueAt ? new Date(selected.dueAt).toLocaleString('pt-BR') : 'Indisponível' },
      { label: 'Estado', value: decisionStatusLabel[selected.status] ?? selected.status },
      { label: 'Hierarquia', value: routingPathLabel(selected) },
      { label: 'Próximo escalonamento', value: nextEscalationLabel(selected) },
      { label: 'Política solicitada', value: requestedRoutingLabel(selected) },
      { label: 'Fallback obrigatório', value: selected.ownerUserLabel ?? 'Dono do escritório' },
      { label: 'Notificação no sistema', value: deliverySummary(selected.notificationDelivery, 'in_app') },
      { label: 'Telefone', value: phoneSummary(selected) },
    ] : []}>
      {selected?.agentRunId && (selected.inputFields ?? []).length > 0 && selected.status === 'pending' && canDecideSelected ? <div className="mt-4 rounded-lg border p-3"><p className="text-xs font-semibold">Dados solicitados pela execução</p><div className="mt-2 grid gap-2 sm:grid-cols-2">{(selected.inputFields ?? []).map((field) => <label key={field} className="text-xs">{field}<Input className="mt-1 min-h-11" value={humanInput[field] ?? ''} onChange={(event) => setHumanInput((current) => ({ ...current, [field]: event.target.value }))} /></label>)}</div><Button className="mt-3 min-h-11" onClick={() => provideInput.mutate()} disabled={provideInput.isPending}>{provideInput.isPending ? 'Enviando…' : 'Enviar dados e retomar'}</Button></div> : null}
      {selected?.status === 'pending' && canDecideSelected && !selected.acknowledgedAt ? <Button className="mt-3" variant="outline" onClick={() => acknowledge.mutate(selected.id)} disabled={acknowledge.isPending}>Reconhecer chamada</Button> : null}
    </HumanDecisionDrawer>
  </KlmPanel>;
}
