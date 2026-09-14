import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  BadgeCheck,
  Check,
  ChevronRight,
  CircleAlert,
  FileSearch,
  Gauge,
  History,
  LoaderCircle,
  LockKeyhole,
  Network,
  Pause,
  Play,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  UserCheck,
  Workflow,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { AgentAutonomyCard } from '@/klm/components/AgentAutonomyCard';
import { NativeAgentOperationsPanel } from '@/klm/components/NativeAgentOperationsPanel';
import { HumanDecisionDrawer } from '@/klm/components/HumanDecisionDrawer';
import { AgentRunDrawer } from '@/klm/components/AgentRunDrawer';
import { AgentPlatformOperationsPanel } from '@/klm/components/AgentPlatformOperationsPanel';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { EffectivePlaybookLink } from '@/klm/components/playbooks/EffectivePlaybookLink';
import { klmApi, type KlmAgent, type KlmAgentRegistry, type KlmApproval } from '@/klm/api';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';

const stateLabel: Record<KlmAgent['state'], string> = {
  active: 'Pronto',
  paused: 'Pausado',
  review: 'Revisar',
  attention: 'Atenção',
  frozen: 'Área congelada',
};

const runStatusLabel: Record<string, string> = {
  queued: 'Na fila',
  running: 'Executando',
  completed: 'Concluído',
  waiting_human: 'Decisão humana',
  failed: 'Falhou',
  cancelled: 'Cancelado',
};

const prettify = (value: string) => value
  .replaceAll('_', ' ')
  .replace(/\b\w/g, (letter) => letter.toUpperCase());

const stateTone = (state: KlmAgent['state']) => (
  state === 'active' ? 'green' : state === 'attention' ? 'red' : state === 'paused' || state === 'frozen' ? 'muted' : 'gold'
);

const fullNumber = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });

export default function AgentsPage() {
  const { can, state } = useAppStore();
  // The tenant session is the only workspace source in /app.  Unlike the
  // Blackbox, this surface deliberately has no cross-workspace selector.
  const activeWorkspaceId = state.activeWorkspace?.id ?? null;
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const agentsQuery = useQuery({ queryKey: ['klm', 'agents'], queryFn: klmApi.agents });
  const areasQuery = useQuery({ queryKey: ['klm', 'areas'], queryFn: klmApi.areas });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const threadsQuery = useQuery({ queryKey: ['klm', 'threads'], queryFn: klmApi.threads });
  const approvalsQuery = useQuery({ queryKey: ['klm', 'approvals'], queryFn: klmApi.approvals });
  const platformDeploymentsQuery = useQuery({ queryKey: ['agent-platform', 'deployments', activeWorkspaceId], queryFn: () => klmApi.agentPlatformDeployments(activeWorkspaceId as string), enabled: can('agents', 'view') && Boolean(activeWorkspaceId), retry: false });
  const platformNodesQuery = useQuery({ queryKey: ['agent-platform', 'nodes'], queryFn: klmApi.agentPlatformNodes, enabled: can('agents', 'view'), retry: false });
  const humanOverviewQuery = useQuery({ queryKey: ['agent-platform', 'human-overview', activeWorkspaceId], queryFn: () => klmApi.agentHumanOverview(activeWorkspaceId ?? undefined), enabled: can('agents', 'view') && Boolean(activeWorkspaceId), retry: false, refetchInterval: 30_000 });
  const platformDecisionsQuery = useQuery({ queryKey: ['agent-platform', 'decisions', activeWorkspaceId], queryFn: () => klmApi.agentPlatformDecisions(activeWorkspaceId as string), enabled: can('agents', 'view') && Boolean(activeWorkspaceId), retry: false });
  const [selectedId, setSelectedId] = useState('orquestrador');
  const [query, setQuery] = useState('');
  const [areaFilter, setAreaFilter] = useState('all');
  const [internalDraft, setInternalDraft] = useState('');
  const [selectedApproval, setSelectedApproval] = useState<KlmApproval | null>(null);
  const [runDrawer, setRunDrawer] = useState<{ agentName: string; status: string; summary?: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestedMatterId = searchParams.get('matter');
  const requestedThreadId = searchParams.get('thread');

  const registry = agentsQuery.data;
  const agents = useMemo(
    () => [...(registry?.agents ?? []), ...(registry?.specializedAgents ?? [])],
    [registry?.agents, registry?.specializedAgents],
  );
  const requestedThread = threadsQuery.data?.find((thread) => thread.id === requestedThreadId);
  const selectedMatter = useMemo(
    () => {
      const cards = funnelQuery.data?.cards ?? [];
      if (requestedMatterId) {
        if (requestedThreadId && (!requestedThread || requestedThread.matterId !== requestedMatterId)) return undefined;
        return cards.find((card) => card.id === requestedMatterId);
      }
      if (requestedThreadId) {
        return requestedThread?.matterId ? cards.find((card) => card.id === requestedThread.matterId) : undefined;
      }
      return undefined;
    },
    [funnelQuery.data?.cards, requestedMatterId, requestedThread, requestedThreadId],
  );
  const selectedThread = useMemo(
    () => {
      if (requestedThreadId) {
        if (!requestedThread || (requestedMatterId && requestedThread.matterId !== requestedMatterId)) return undefined;
        return requestedThread;
      }
      if (!selectedMatter) return undefined;
      return threadsQuery.data?.find((thread) => thread.matterId === selectedMatter.id);
    },
    [requestedMatterId, requestedThread, requestedThreadId, selectedMatter, threadsQuery.data],
  );
  const contextMatterId = selectedMatter?.id ?? requestedMatterId ?? selectedThread?.matterId ?? null;
  const contextThreadId = selectedThread?.id ?? requestedThreadId;
  const contextualPath = (path: string, extras: Record<string, string> = {}) => {
    const params = new URLSearchParams();
    if (contextMatterId) params.set('matter', contextMatterId);
    if (contextThreadId) params.set('thread', contextThreadId);
    Object.entries(extras).forEach(([key, value]) => params.set(key, value));
    const query = params.toString();
    return `${path}${query ? `?${query}` : ''}`;
  };
  const messagesQuery = useQuery({
    queryKey: ['klm', 'messages', selectedThread?.id],
    queryFn: () => klmApi.messages(selectedThread!.id),
    enabled: Boolean(selectedThread?.id),
  });
  const runsQuery = useQuery({
    queryKey: ['klm', 'agent-runs', selectedMatter?.id],
    queryFn: () => klmApi.agentRuns(selectedMatter!.id),
    enabled: Boolean(selectedMatter?.id),
  });
  const selected = agents.find((agent) => agent.key === selectedId) ?? agents[0];
  const filteredAgents = useMemo(
    () => agents.filter((agent) => {
      const matchesArea = areaFilter === 'all'
        || (areaFilter === 'core' ? !agent.areaKey : agent.areaKey === areaFilter);
      const haystack = `${agent.name} ${agent.role} ${agent.group} ${agent.areaKey ?? ''} ${agent.productKey ?? ''}`.toLowerCase();
      return matchesArea && haystack.includes(query.toLowerCase());
    }),
    [agents, areaFilter, query],
  );
  const internalMessages = useMemo(
    () => (messagesQuery.data ?? []).filter((message) => message.direction === 'internal'),
    [messagesQuery.data],
  );
  const pendingApprovals = useMemo(
    () => (approvalsQuery.data ?? []).filter((approval) => approval.status === 'pending'),
    [approvalsQuery.data],
  );
  const selectedRuns = useMemo(
    () => (runsQuery.data ?? []).filter((run) => run.agentName === selected?.key).slice(0, 4),
    [runsQuery.data, selected?.key],
  );
  const hierarchyRows = useMemo(() => {
    const parentByKey = registry?.governance?.parentByAgentKey ?? {};
    return agents.map((agent) => {
      const path = [agent.key];
      let parent = parentByKey[agent.key];
      while (parent && path.length < 8) {
        path.unshift(parent);
        parent = parentByKey[parent];
      }
      return { agent, path };
    }).sort((left, right) => left.path.join('/').localeCompare(right.path.join('/'), 'pt-BR'));
  }, [agents, registry?.governance?.parentByAgentKey]);

  const refreshAgentSurfaces = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'agents'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'agent-runs'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'messages'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'approvals'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
  };

  const stateMutation = useMutation({
    mutationFn: ({ agent, state }: { agent: KlmAgent; state: 'active' | 'paused' }) =>
      klmApi.setAgentState(agent.key, state, state === 'paused' ? 'Pausa manual registrada na interface local.' : 'Retomada manual registrada na interface local.'),
    onSuccess: (updated) => {
      queryClient.setQueryData<KlmAgentRegistry>(['klm', 'agents'], (current) => current ? {
        ...current,
        agents: current.agents.map((agent) => agent.key === updated.key ? updated : agent),
        specializedAgents: (current.specializedAgents ?? []).map((agent) => agent.key === updated.key ? updated : agent),
        metrics: {
          ...current.metrics,
          paused: [...current.agents, ...(current.specializedAgents ?? [])].filter((agent) => (agent.key === updated.key ? updated.state : agent.state) === 'paused').length,
          active: [...current.agents, ...(current.specializedAgents ?? [])].filter((agent) => !['paused', 'frozen'].includes(agent.key === updated.key ? updated.state : agent.state)).length,
        },
      } : current);
      setNotice(`${updated.name} ${updated.state === 'paused' ? 'pausado' : 'retomado'} com registro na auditoria local.`);
    },
    onError: () => setNotice('O backend local recusou a mudança de estado.'),
  });

  const toolsMutation = useMutation({
    mutationFn: ({ agent, tools }: { agent: KlmAgent; tools: string[] }) =>
      klmApi.setAgentTools(agent.key, tools, 'Vínculo ajustado pelo painel local de governança.'),
    onSuccess: (updated) => {
      queryClient.setQueryData<KlmAgentRegistry>(['klm', 'agents'], (current) => current ? {
        ...current,
        agents: current.agents.map((agent) => agent.key === updated.key ? updated : agent),
        specializedAgents: (current.specializedAgents ?? []).map((agent) => agent.key === updated.key ? updated : agent),
      } : current);
      setNotice(`Ferramentas de ${updated.name} atualizadas e auditadas no workspace.`);
    },
    onError: () => setNotice('A alteração foi recusada: ferramenta desconhecida, externa ou versão desatualizada.'),
  });

  const runMutation = useMutation({
    mutationFn: (agent: KlmAgent) => {
      if (!selectedMatter) throw new Error('Caso local indisponível');
      const executionAgent = agent.key === 'hermes'
        ? agents.find((candidate) => candidate.key === 'secretaria')
        : agent;
      if (!executionAgent) throw new Error('Secretaria indisponível para a ponte humana');
      const requiresOwnerDecision = ['secretaria', 'peticao', 'cobranca'].includes(executionAgent.key);
      return klmApi.runAgent({
        agentKey: executionAgent.key,
        matterId: selectedMatter.id,
        threadId: selectedThread?.id,
        requestHuman: requiresOwnerDecision,
        instructions: agent.key === 'hermes'
          ? 'Registrar a necessidade e colocar a conversa na fila do atendente pelo Orquestrador; o Hermes apenas informa o dono.'
          : requiresOwnerDecision
          ? 'Validar a exceção; o Orquestrador encaminha a conversa ao atendente humano e notifica o dono pelo Hermes.'
          : 'Executar o contrato do agente com as evidências atuais do caso.',
      });
    },
    onMutate: (agent) => setRunDrawer({ agentName: agent.name, status: 'running', summary: 'Execução local em andamento; aguardando retorno auditável.' }),
    onSuccess: (result) => {
      const failed = result.runs.some((run) => run.status === 'failed');
      setRunDrawer((current) => current ? { ...current, status: failed ? 'failed' : 'completed', summary: `${result.runs.length} etapa(s) registrada(s); 0 ações externas.` } : current);
      refreshAgentSurfaces();
      const handoff = result.runs.some((run) => run.agentName === 'orquestrador' && run.status === 'waiting_human') ? ' com a conversa colocada na fila do atendente pelo Orquestrador' : '';
      const failures = result.runs.filter((run) => run.status === 'failed').length;
      setNotice(failures
        ? `${failures} execução(ões) falharam sem fallback local; revise o provider ou orçamento. 0 ações externas.`
        : `${result.runs.length} execução(ões) concluída(s)${handoff} em ${result.runtime === 'model_api' ? 'modelos via API' : 'simulador local'}; 0 ações externas.`);
    },
    onError: () => { setRunDrawer((current) => current ? { ...current, status: 'failed', summary: 'A execução foi recusada. Nenhuma ação externa foi executada.' } : current); setNotice('A execução foi recusada. Verifique se o agente está pausado e se o caso possui conversa vinculada.'); },
  });

  const teamMutation = useMutation({
    mutationFn: () => {
      if (!selectedMatter) throw new Error('Caso local indisponível');
      return klmApi.simulateIntake(selectedMatter.id, selectedThread?.id);
    },
    onSuccess: (result) => {
      refreshAgentSurfaces();
      setNotice(`Equipe roteada: ${result.runs.length} etapas auditadas; Orquestrador coordenou a fila humana, Hermes apenas informou o dono e nenhuma ação externa foi executada.`);
    },
    onError: () => setNotice('A equipe não pôde ser simulada. Confirme se a área do caso foi liberada para esta conta.'),
  });

  const internalMutation = useMutation({
    mutationFn: (body: string) => {
      if (!selectedThread) throw new Error('Conversa local indisponível');
      return klmApi.sendInternalMessage(
        selectedThread.id,
        body,
        state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace',
      );
    },
    onSuccess: () => {
      setInternalDraft('');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', selectedThread?.id] });
    },
    onError: () => setNotice('A orientação interna não foi registrada.'),
  });

  const approvalMutation = useMutation({
    mutationFn: ({ approval, status }: { approval: KlmApproval; status: 'approved' | 'rejected' }) =>
      klmApi.resolveApproval(approval.id, status, status === 'approved' ? 'Aprovado no Meu Executivo local.' : 'Rejeitado no Meu Executivo local.'),
    onSuccess: (_, variables) => {
      setSelectedApproval(null);
      refreshAgentSurfaces();
      setNotice(`Decisão ${variables.status === 'approved' ? 'aprovada' : 'rejeitada'} e registrada localmente.`);
    },
    onError: () => setNotice('A decisão não pôde ser registrada.'),
  });

  if (agentsQuery.isError) {
    return <KlmPanel className="mx-auto max-w-[760px] p-8 text-center"><CircleAlert className="mx-auto h-6 w-6 text-[#a53e3c]" /><h2 className="mt-4 text-lg font-semibold">Não foi possível carregar os agentes</h2><p className="mt-2 text-xs text-muted-foreground">Nenhum estado de demonstração será exibido como se fosse o runtime real.</p><Button variant="outline" className="mt-5" onClick={() => void agentsQuery.refetch()}><History className="mr-2 h-4 w-4" />Tentar novamente</Button></KlmPanel>;
  }

  if (!selected) {
    return <KlmPanel className="mx-auto max-w-[1220px] p-8 text-center text-xs text-muted-foreground"><LoaderCircle className="mx-auto mb-3 h-5 w-5 animate-spin" />Carregando registro local dos agentes...</KlmPanel>;
  }

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Governança agêntica</p>
          <h2 className="mt-2 text-2xl font-semibold">Controle dos agentes</h2>
          <p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">O Orquestrador distribui trabalho e coloca exceções na fila do atendente humano. Hermes apenas leva decisões e alertas ao dono.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" className="h-9 text-xs" onClick={() => navigate(contextualPath('/app/qualidade'))}><Gauge className="mr-2 h-3.5 w-3.5" />Qualidade dos agentes</Button>
          <EffectivePlaybookLink
            selectors={{ area: selected?.areaKey ?? 'geral', agent: selected?.key }}
            className="max-w-[330px]"
          />
          <KlmPill tone={registry?.metrics.modelApisEnabled ? 'green' : 'gold'}>{registry?.metrics.modelApisEnabled ? 'modelos via API' : 'API-first · aguardando ativação'}</KlmPill>
          <Button variant="outline" className="h-9 text-xs" title="Executa somente o simulador local, sem consumir APIs" disabled={teamMutation.isPending || !selectedMatter} onClick={() => teamMutation.mutate()}>
            {teamMutation.isPending ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Workflow className="mr-2 h-3.5 w-3.5" />}Simular time
          </Button>
          <Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" onClick={() => navigate(contextualPath('/app/aprovacoes'))}><UserCheck className="mr-2 h-3.5 w-3.5" />Fila de aprovação</Button>
        </div>
      </header>

      {notice && <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <NativeModuleBar
        domain="automation"
        surface="Controle dos agentes"
        returnTo={contextualPath('/app/agentes')}
        capabilityIds={['agents-rbac', 'handoff-inbox-transfer', 'mcp-tools-agent-association', 'knowledge-faq']}
      />
      <NativeAgentOperationsPanel view="agents" canManage={can('agents', 'manage')} />
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-2xs" data-testid="agent-platform-status">
        <span className="font-semibold">Catálogo de runtime</span>
        {platformNodesQuery.isPending || platformDeploymentsQuery.isPending ? <span className="text-muted-foreground">Carregando…</span> : platformNodesQuery.isError || platformDeploymentsQuery.isError ? <span className="text-red-600">Indisponível ou sem permissão</span> : <span className="text-emerald-700 dark:text-emerald-400">{platformNodesQuery.data?.length ?? 0} nós · {platformDeploymentsQuery.data?.total ?? 0} deployments</span>}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <KlmMetricCard label="Agentes modelados" value={String(agents.length)} delta={`${registry?.metrics.specializedModeled ?? 0} especializados`} trend="up" />
        <KlmMetricCard label="Qualidade média" value={`${registry?.metrics.qualityAverage ?? 0}/100`} delta="evals e revisões" trend="up" />
        <KlmMetricCard label="Decisões pendentes" value={String(pendingApprovals.length)} delta={`${registry?.metrics.waitingHuman ?? 0} runs aguardando`} trend="neutral" />
        <KlmMetricCard label="Áreas liberadas" value={String(areasQuery.data?.filter((area) => area.isActive).length ?? 0)} delta={`${registry?.metrics.specializedEnabled ?? 0} agentes disponíveis`} trend="neutral" />
        <KlmMetricCard label="Tokens de modelos" value={fullNumber.format(registry?.metrics.modelTokens ?? 0)} delta={registry?.metrics.modelApisEnabled ? 'uso acumulado' : 'ativação pendente'} trend="neutral" />
        <KlmMetricCard label="Execuções registradas" value={fullNumber.format(registry?.metrics.runs ?? 0)} delta={registry?.metrics.modelApisEnabled ? 'uso via API' : 'simulação local'} trend="neutral" />
      </div>
      <section className="rounded-lg border bg-card p-4" aria-label="Acompanhamento da fila humana">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-sm font-semibold">Fila humana do escritório</h2><p className="text-2xs text-muted-foreground">O responsável é chamado por função e a hierarquia termina no dono. Telefone só aparece entregue após recibo do provedor.</p></div><KlmPill tone={humanOverviewQuery.isError ? 'red' : humanOverviewQuery.isPending ? 'gold' : 'green'}>{humanOverviewQuery.isError ? 'indisponível' : humanOverviewQuery.isPending ? 'carregando' : 'sincronizada'}</KlmPill></div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">{[
          ['Abertas', humanOverviewQuery.data?.open ?? '—'],
          ['Atribuídas', humanOverviewQuery.data?.assigned ?? '—'],
          ['Reconhecidas', humanOverviewQuery.data?.acknowledged ?? '—'],
          ['Vencidas', humanOverviewQuery.data?.overdue ?? '—'],
          ['Escaladas', humanOverviewQuery.data?.escalated ?? '—'],
          ['Telefone', humanOverviewQuery.data ? `${humanOverviewQuery.data.phone.delivered} entregues · ${humanOverviewQuery.data.phone.pending} pendentes · ${humanOverviewQuery.data.phone.failed + humanOverviewQuery.data.phone.notConfigured} atenção` : '—'],
          ['Decisões carregadas', platformDecisionsQuery.data?.total ?? '—'],
        ].map(([label, value]) => <div key={String(label)} className="rounded-md border p-2.5"><span className="block text-[10px] text-muted-foreground">{label}</span><strong className="mt-1 block text-sm">{String(value)}</strong></div>)}</div>
      </section>
      <AgentPlatformOperationsPanel canManage={can('agents', 'manage')} currentUserId={state.currentUser?.id ?? null} workspaceId={activeWorkspaceId} />

      <div className="grid border-y border-border md:grid-cols-3">
        <div className="border-b border-border py-3 md:border-b-0 md:border-r md:pr-5">
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Coordenação interna</p>
          <p className="mt-1 text-xs font-semibold">Orquestrador</p>
          <p className="mt-1 text-2xs leading-4 text-muted-foreground">Confere alterações manuais, escolhe área e produto, delega somente ao filho autorizado e acompanha falhas.</p>
        </div>
        <div className="border-b border-border py-3 md:border-b-0 md:border-r md:px-5">
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Escalonamento controlado</p>
          <p className="mt-1 text-xs font-semibold">Secretaria</p>
          <p className="mt-1 text-2xs leading-4 text-muted-foreground">Consolida a exceção, elimina contexto desnecessário e é a única agente autorizada a encaminhar ao Hermes.</p>
        </div>
        <div className="py-3 md:pl-5">
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Humano no controle</p>
          <p className="mt-1 text-xs font-semibold">Hermes</p>
          <p className="mt-1 text-2xs leading-4 text-muted-foreground">É a ponte com o dono pelo WhatsApp autorizado. Não delega, não escolhe especialistas e não substitui o Orquestrador.</p>
        </div>
      </div>

      <Tabs defaultValue="equipe" className="space-y-4">
        <TabsList className="h-9 w-full justify-start overflow-x-auto bg-secondary p-1 sm:w-auto">
          <TabsTrigger className="text-xs" value="equipe">Equipe</TabsTrigger>
          <TabsTrigger className="text-xs" value="hierarquia">Hierarquia</TabsTrigger>
          <TabsTrigger className="text-xs" value="interno">Conversa interna</TabsTrigger>
          <TabsTrigger className="text-xs" value="fila">Aprovações</TabsTrigger>
          <TabsTrigger className="text-xs" value="permissoes">Permissões</TabsTrigger>
          <TabsTrigger className="text-xs" value="autonomia">Autonomia</TabsTrigger>
          <TabsTrigger className="text-xs" value="motor">Motor IA</TabsTrigger>
        </TabsList>

        <TabsContent value="equipe">
          <div className="grid gap-4 xl:grid-cols-[.85fr_1.15fr]">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Equipe da área" description={registry?.policyVersion ?? 'Registro local'} action={<KlmPill tone="green">{agents.length} papéis</KlmPill>} />
              <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_190px]">
                <div className="relative"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} className="h-9 bg-secondary pl-9 text-xs" placeholder="Buscar agente, papel ou produto" /></div>
                <select value={areaFilter} onChange={(event) => setAreaFilter(event.target.value)} className="h-9 rounded-md border border-input bg-background px-3 text-2xs" aria-label="Filtrar agentes por área">
                  <option value="all">Todos os agentes</option>
                  <option value="core">Núcleo compartilhado</option>
                  {(areasQuery.data ?? []).map((area) => <option key={area.areaKey} value={area.areaKey}>{area.name}{area.isActive ? '' : ' · congelada'}</option>)}
                </select>
              </div>
              <div className="mt-3 max-h-[620px] space-y-2 overflow-y-auto pr-1">
                {filteredAgents.map((agent) => (
                  <button key={agent.key} onClick={() => setSelectedId(agent.key)} className={cn('grid w-full grid-cols-[36px_1fr_auto] items-center gap-3 rounded-lg border p-3 text-left transition', selected.key === agent.key ? 'border-primary bg-primary/10' : 'border-border hover:border-primary/60')}>
                    <span className="flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-2xs font-semibold text-primary">{agent.name.split(' ').map((word) => word[0]).join('').slice(0, 2)}</span>
                    <span className="min-w-0"><strong className="block truncate text-xs">{agent.name}</strong><span className="mt-0.5 block truncate text-2xs text-muted-foreground">{agent.areaKey ? `${prettify(agent.areaKey)} · ${agent.productKey ? prettify(agent.productKey) : 'área'}` : `${agent.role} · núcleo`}</span></span>
                    <KlmPill tone={stateTone(agent.state)}>{stateLabel[agent.state]}</KlmPill>
                  </button>
                ))}
              </div>
            </KlmPanel>

            <div className="space-y-4">
              <KlmPanel tone="black" className="p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div><p className="text-2xs font-semibold uppercase text-primary">{selected.areaKey ? prettify(selected.areaKey) : selected.group}</p><h2 className="mt-2 text-lg font-semibold">{selected.name}</h2><p className="mt-1 text-2xs text-white/50">{selected.role}{selected.productKey ? ` · ${prettify(selected.productKey)}` : ''}</p></div>
                  <div className="flex items-center gap-2"><KlmPill tone={selected.state === 'frozen' ? 'muted' : 'green'}>{selected.state === 'frozen' ? 'área congelada' : `score ${selected.qualityScore}`}</KlmPill><Button size="icon" variant="outline" className="h-8 w-8 border-white/20 bg-transparent text-white hover:bg-white/10" disabled={stateMutation.isPending || selected.state === 'frozen'} title={selected.state === 'paused' ? 'Retomar agente' : selected.state === 'frozen' ? 'Libere a área para operar este agente' : 'Pausar agente'} onClick={() => stateMutation.mutate({ agent: selected, state: selected.state === 'paused' ? 'active' : 'paused' })}>{selected.state === 'paused' ? <Play className="h-3.5 w-3.5" /> : selected.state === 'frozen' ? <LockKeyhole className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}</Button></div>
                </div>
                <p className="mt-5 text-xs leading-5 text-white/70">{selected.description}</p>
                {selected.parentAgentKey && <div className="mt-4 grid grid-cols-2 gap-2 rounded-lg border border-white/10 bg-white/5 p-3 text-2xs"><div><span className="block text-white/40">Recebe delegação de</span><strong className="mt-1 block text-white">{prettify(selected.parentAgentKey)}</strong></div><div><span className="block text-white/40">Escopo exato</span><strong className="mt-1 block text-white">{selected.productKey ? prettify(selected.productKey) : prettify(selected.areaKey ?? 'núcleo')}</strong></div></div>}
                <div className="mt-5 rounded-lg border border-primary/25 bg-primary/10 p-3"><div className="flex items-center gap-2 text-2xs font-semibold text-primary"><LockKeyhole className="h-3.5 w-3.5" />Gate obrigatório</div><p className="mt-2 text-2xs leading-4 text-white/65">{selected.gate}</p></div>
                <Button className="mt-4 w-full bg-primary text-2xs text-primary-foreground hover:bg-primary/90" disabled={runMutation.isPending || selected.state === 'paused' || selected.state === 'frozen' || !selectedMatter} onClick={() => runMutation.mutate(selected)}>{runMutation.isPending ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : selected.state === 'frozen' ? <LockKeyhole className="mr-2 h-3.5 w-3.5" /> : <Play className="mr-2 h-3.5 w-3.5" />}{selected.state === 'frozen' ? 'Área precisa ser liberada' : selected.key === 'hermes' ? 'Acionar via Secretaria' : 'Executar no caso atual'}</Button>
              </KlmPanel>

              <div className="grid gap-4 lg:grid-cols-2">
                <KlmPanel className="p-4">
                  <KlmSectionTitle title="Ferramentas permitidas" description="Escopo local efetivo; cada alteração gera versão e auditoria." />
                  <div className="mt-3 max-h-[360px] space-y-2 overflow-y-auto pr-1">
                    {Array.from(new Set(agents.flatMap((agent) => agent.tools))).sort().map((tool) => {
                      const enabled = selected.tools.includes(tool);
                      return <label key={tool} className="flex cursor-pointer items-center justify-between rounded-md border p-2.5 text-2xs hover:border-primary/60">
                        <span className="flex min-w-0 items-center gap-2"><input type="checkbox" aria-label={`${enabled ? 'Desativar' : 'Ativar'} ferramenta ${prettify(tool)} para ${selected.name}`} checked={enabled} disabled={toolsMutation.isPending} onChange={(event) => {
                          const tools = event.target.checked ? [...selected.tools, tool] : selected.tools.filter((item) => item !== tool);
                          toolsMutation.mutate({ agent: selected, tools });
                        }} className="h-3.5 w-3.5 accent-primary" /><span className="truncate">{prettify(tool)}</span></span>
                        <KlmPill tone={enabled ? 'green' : 'muted'}>{enabled ? 'ativo' : 'inativo'}</KlmPill>
                      </label>;
                    })}
                  </div>
                  <p className="mt-3 text-2xs leading-4 text-muted-foreground">Apenas capacidades locais conhecidas podem ser vinculadas. Transporte externo continua bloqueado até aprovação.</p>
                </KlmPanel>
                <KlmPanel className="p-4">
                  <KlmSectionTitle title="Execuções recentes" description={`${prettify(selected.modelPolicy.taskClass)} · roteamento por custo`} />
                  <div className="mt-3 space-y-2">
                    {selectedRuns.length ? selectedRuns.map((run) => <div key={run.id} className="rounded-md border p-2.5"><div className="flex items-center justify-between gap-2"><span className="text-2xs font-semibold">{runStatusLabel[run.status] ?? prettify(run.status)}</span><KlmPill tone={run.status === 'completed' ? 'green' : run.status === 'waiting_human' ? 'gold' : run.status === 'failed' ? 'red' : 'muted'}>{new Date(run.createdAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</KlmPill></div><p className="mt-1.5 line-clamp-2 text-2xs leading-3 text-muted-foreground">{String(run.outputPayload?.summary ?? 'Execução registrada no histórico.')}</p><div className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-2xs text-muted-foreground"><span>{run.aiProvider ?? 'sem provider'} · {run.modelUsed ?? 'sem modelo'}</span><strong className="font-semibold text-foreground">{fullNumber.format(run.inputTokens + run.outputTokens)} tokens</strong></div></div>) : <div className="rounded-md border border-dashed p-5 text-center text-2xs text-muted-foreground">Nenhuma execução deste agente no caso atual.</div>}
                  </div>
                  <Button variant="outline" className="mt-4 w-full text-xs" onClick={() => navigate(contextualPath('/app/qualidade'))}><Gauge className="mr-2 h-3.5 w-3.5" />Abrir Qualidade IA</Button>
                </KlmPanel>
              </div>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="hierarquia" className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Cadeia de autoridade" description="Cada linha mostra o caminho completo permitido até o agente." action={<KlmPill tone="black"><Network className="mr-1 h-3 w-3" />{registry?.governance?.version ?? 'política vigente'}</KlmPill>} />
              <div className="mt-4 max-h-[660px] space-y-2 overflow-y-auto pr-1">
                {hierarchyRows.map(({ agent, path }) => (
                  <button key={agent.key} onClick={() => { setSelectedId(agent.key); setAreaFilter(agent.areaKey ?? 'core'); }} className="grid w-full gap-3 rounded-lg border p-3 text-left hover:border-[#0057d8]/60 sm:grid-cols-[1fr_auto] sm:items-center">
                    <span className="min-w-0"><span className="flex flex-wrap items-center gap-1.5">{path.map((key, index) => <span key={`${agent.key}-${key}`} className="flex items-center gap-1.5"><KlmPill tone={key === 'orquestrador' ? 'black' : key === 'hermes' ? 'gold' : key === agent.key ? (agent.state === 'frozen' ? 'muted' : 'green') : 'muted'}>{prettify(key)}</KlmPill>{index < path.length - 1 && <ChevronRight className="h-3 w-3 text-muted-foreground" />}</span>)}</span><span className="mt-2 block text-2xs text-muted-foreground">{agent.productKey ? `Produto: ${prettify(agent.productKey)}` : agent.areaKey ? `Área: ${prettify(agent.areaKey)}` : 'Núcleo compartilhado'} · {agent.tools.length} ferramentas</span></span>
                    <KlmPill tone={stateTone(agent.state)}>{stateLabel[agent.state]}</KlmPill>
                  </button>
                ))}
              </div>
            </KlmPanel>

            <div className="space-y-4">
              <KlmPanel tone="black" className="p-5">
                <ShieldCheck className="h-5 w-5 text-primary" />
                <h2 className="mt-4 text-lg font-semibold">Contrato de governança</h2>
                <p className="mt-2 text-2xs leading-5 text-white/60">As regras abaixo são avaliadas antes de montar contexto, consultar memória ou chamar qualquer modelo.</p>
                {registry?.governance?.policyDigest && <p className="mt-2 break-all font-mono text-2xs leading-4 text-white/45" title={registry.governance.policyDigest}>SHA-256 {registry.governance.policyDigest.slice(0, 16)}…</p>}
                <div className="mt-5 space-y-2">
                  {[
                    ['Correção manual prevalece', registry?.governance?.manualOverrideWins],
                    ['Conta isolada', registry?.governance?.crossWorkspaceAccessDenied],
                    ['Cliente isolado', registry?.governance?.crossClientAccessDenied],
                    ['Área isolada', registry?.governance?.crossAreaAccessDisabled],
                    ['Produto isolado', registry?.governance?.crossProductAccessDisabled],
                    ['Menor privilégio', registry?.governance?.leastPrivilegeTools],
                    ['Orquestrador coordena a fila humana', registry?.governance?.coordinationAgentKey === 'orquestrador'],
                    ['Atalho direto ao Hermes bloqueado', registry?.governance?.delegationRules?.directOwnerBridgeInvocationDisabled],
                    ['Profundidade exata', registry?.governance?.delegationRules?.depthValidated],
                    ['Envio autônomo externo', registry?.governance?.externalActionsAllowed],
                  ].map(([label, enabled]) => <div key={String(label)} className="flex items-center justify-between border-b border-white/10 py-2 text-2xs"><span className="text-white/60">{String(label)}</span><strong className={enabled ? 'text-emerald-400' : String(label) === 'Envio autônomo externo' ? 'text-emerald-400' : 'text-red-300'}>{String(label) === 'Envio autônomo externo' ? (enabled ? 'Permitido' : 'Bloqueado') : enabled ? 'Ativo' : 'Falha'}</strong></div>)}
                </div>
              </KlmPanel>
              <KlmPanel className="p-4">
                <KlmSectionTitle title="Decisões sempre humanas" description="Nenhum agente pode executar estas ações." />
                <div className="mt-3 flex flex-wrap gap-2">{(registry?.governance?.hardHumanGates ?? []).map((gate) => <KlmPill key={gate} tone="gold">{prettify(gate)}</KlmPill>)}</div>
              </KlmPanel>
              <KlmPanel className="p-4">
                <KlmSectionTitle title="Controle independente" description="Só humano ou sistema inicia estes fiscais." />
                <div className="mt-3 space-y-2">{(registry?.governance?.independentControlAgents ?? []).map((key) => <div key={key} className="flex items-center justify-between rounded-md border p-2.5 text-2xs"><span>{prettify(key)}</span><KlmPill tone="green">independente</KlmPill></div>)}</div>
              </KlmPanel>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="interno">
          <KlmPanel className="overflow-hidden">
            <div className="border-b p-4"><KlmSectionTitle title={`# ${selectedMatter?.name ?? 'nenhum caso selecionado'} · ${selectedMatter?.object ?? 'selecione um card'}`} description="Registro interno auditável do caso selecionado" action={<KlmPill tone={selectedMatter ? 'green' : 'muted'}>{new Set(internalMessages.map((message) => message.authorLabel)).size} participantes</KlmPill>} /></div>
            <div className="grid min-h-[560px] lg:grid-cols-[1fr_280px]">
              <div className="flex min-h-0 flex-col border-b lg:border-b-0 lg:border-r">
                <div className="flex-1 space-y-1 overflow-y-auto p-4 sm:p-6">
                  {internalMessages.length ? internalMessages.map((message) => <div key={message.id} className="group flex gap-3 rounded-lg p-3 hover:bg-secondary"><div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[#031121] text-2xs font-semibold text-primary">{message.authorLabel.split(' ').map((word) => word[0]).join('').slice(0, 2)}</div><div className="min-w-0"><div className="flex flex-wrap items-baseline gap-2"><strong className="text-xs">{message.authorLabel}</strong><span className="text-2xs text-muted-foreground">{new Date(message.occurredAt).toLocaleString('pt-BR', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}</span>{message.authorType === 'agent' && <KlmPill tone="gold">{message.messageMetadata.executionMode === 'model_api' ? 'Agente · API' : 'Agente · simulador'}</KlmPill>}</div><p className="mt-1 text-xs leading-5 text-foreground/80">{message.body}</p></div></div>) : <div className="p-10 text-center text-xs text-muted-foreground">Ainda não há mensagens internas neste caso.</div>}
                </div>
                <div className="border-t p-3"><div className="flex gap-2"><Textarea value={internalDraft} onChange={(event) => setInternalDraft(event.target.value)} placeholder="Registrar orientação interna..." className="min-h-[42px] resize-none text-xs" /><Button onClick={() => internalDraft.trim() && internalMutation.mutate(internalDraft.trim())} disabled={!internalDraft.trim() || internalMutation.isPending || !selectedThread} size="icon" className="h-10 w-10 shrink-0 bg-[#031121] text-white hover:bg-[#031121]" title="Registrar mensagem interna">{internalMutation.isPending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}</Button></div></div>
              </div>
              <aside className="space-y-4 bg-secondary/50 p-4">
                <h3 className="text-sm font-semibold">Contexto compartilhado</h3>
                {[
                  ['Lead', selectedMatter?.name ?? 'Indisponível'],
                  ['Subtema', selectedMatter ? prettify(selectedMatter.subject) : 'Indisponível'],
                  ['Objeto', selectedMatter?.object ?? 'Indisponível'],
                  ['Nota', selectedMatter ? `${selectedMatter.probability}%` : 'Indisponível'],
                  ['Documentos', selectedMatter ? `${selectedMatter.documents} · score ${selectedMatter.documentScore}%` : 'Indisponível'],
                  ['Dono', selectedThread?.ownerAgentKey ? prettify(selectedThread.ownerAgentKey) : 'Sem dono'],
                ].map(([label, value]) => <div key={label} className="border-b pb-2"><span className="block text-2xs text-muted-foreground">{label}</span><strong className="mt-0.5 block text-2xs">{value}</strong></div>)}
                <Button variant="outline" className="w-full text-xs" disabled={!selectedMatter} onClick={() => selectedMatter && navigate(contextualPath('/app/leads'))}><FileSearch className="mr-2 h-3.5 w-3.5" />Abrir caso e fontes</Button>
              </aside>
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="fila">
          <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Fila de decisões dos agentes" description="Exceções entram na fila do atendente; Hermes informa o dono sem assumir a conversa" action={<KlmPill tone={pendingApprovals.length ? 'red' : 'green'}>{pendingApprovals.length} pendentes</KlmPill>} />
              <div className="mt-4 divide-y rounded-lg border">
                {pendingApprovals.length ? pendingApprovals.slice(0, 10).map((approval) => <button key={approval.id} onClick={() => setSelectedApproval(approval)} className="grid w-full grid-cols-[1fr_auto] items-center gap-3 p-4 text-left hover:bg-secondary"><span className="min-w-0"><span className="flex flex-wrap items-center gap-2"><KlmPill tone="black">{approval.type === 'agent_handoff' ? 'Hermes' : prettify(approval.type)}</KlmPill><strong className="text-xs">{approval.title}</strong></span><span className="mt-2 block text-2xs leading-4 text-muted-foreground">{approval.description ?? `${approval.clientName} · decisão local`}</span></span><span className="flex items-center gap-2"><KlmPill tone={approval.priority === 'alta' ? 'red' : approval.priority === 'media' ? 'gold' : 'muted'}>{prettify(approval.priority)}</KlmPill><ChevronRight className="h-4 w-4 text-muted-foreground" /></span></button>) : <div className="p-10 text-center text-xs text-muted-foreground">Nenhuma decisão pendente.</div>}
              </div>
            </KlmPanel>
            <KlmPanel tone="black" className="p-5"><Sparkles className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Síntese do Hermes</h2><p className="mt-2 text-2xs leading-5 text-white/60">{pendingApprovals.length ? `${pendingApprovals.length} decisões aguardam você. A prioridade permanece na fila interna, sem disparos ou alterações externas.` : 'A fila humana está limpa. A equipe segue operando apenas sobre registros auditáveis.'}</p><div className="mt-5 space-y-2">{[['Pendências', String(pendingApprovals.length)], ['Runs locais', String(registry?.metrics.runs ?? 0)], ['Aguardando humano', String(registry?.metrics.waitingHuman ?? 0)], ['Ações externas', '0']].map(([label, value]) => <div key={label} className="flex justify-between border-b border-white/10 py-2 text-2xs"><span className="text-white/50">{label}</span><strong>{value}</strong></div>)}</div><Button variant="outline" className="mt-5 w-full border-white/20 bg-transparent text-xs text-white hover:bg-white/10" onClick={() => navigate(contextualPath('/app/atividade'))}><History className="mr-2 h-3.5 w-3.5" />Abrir auditoria</Button></KlmPanel>
          </div>
        </TabsContent>

        <TabsContent value="permissoes">
          <KlmPanel className="p-4">
          <KlmSectionTitle title="Matriz de permissões" description={`${registry?.policyVersion ?? 'Política local'} · todos os envios externos bloqueados`} action={<KlmPill tone="green"><ShieldCheck className="mr-1 h-3 w-3" />Orquestrador → atendente</KlmPill>} />
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[780px] border-collapse text-left text-2xs"><thead><tr className="border-b text-muted-foreground"><th className="p-3 font-semibold">Agente</th><th className="p-3 font-semibold">Ler caso</th><th className="p-3 font-semibold">Editar CRM</th><th className="p-3 font-semibold">Criar rascunho</th><th className="p-3 font-semibold">Enviar externo</th><th className="p-3 font-semibold">Solicitar humano</th></tr></thead><tbody>{agents.map((agent) => <tr key={agent.key} className="border-b last:border-0"><td className="p-3 font-semibold">{agent.name}</td><td className="p-3">{agent.permissions.readMatter ? <BadgeCheck className="h-4 w-4 text-emerald-600" /> : <LockKeyhole className="h-4 w-4 text-muted-foreground" />}</td><td className="p-3">{agent.permissions.writeCrm ? <BadgeCheck className="h-4 w-4 text-emerald-600" /> : <LockKeyhole className="h-4 w-4 text-muted-foreground" />}</td><td className="p-3">{agent.permissions.createDraft ? <BadgeCheck className="h-4 w-4 text-emerald-600" /> : <LockKeyhole className="h-4 w-4 text-muted-foreground" />}</td><td className="p-3"><LockKeyhole className="h-4 w-4 text-red-500" /></td><td className="p-3">{agent.permissions.requestHuman ? <CircleAlert className="h-4 w-4 text-primary" /> : <LockKeyhole className="h-4 w-4 text-muted-foreground" />}</td></tr>)}</tbody></table>
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="autonomia" className="space-y-4">
          <AgentAutonomyCard agents={agents} canManage={can('agents', 'manage')} />
        </TabsContent>

        <TabsContent value="motor" className="space-y-4">
          <NativeAgentOperationsPanel view="knowledge" canManage={can('agents', 'manage')} />
          <div className="grid gap-4 xl:grid-cols-2">
            <NativeAgentOperationsPanel view="tools" canManage={can('agents', 'manage')} />
            <NativeAgentOperationsPanel view="people" canManage={can('agents', 'manage')} />
          </div>
        </TabsContent>
      </Tabs>

      <HumanDecisionDrawer open={Boolean(selectedApproval)} onOpenChange={(open) => !open && setSelectedApproval(null)} title={selectedApproval?.title ?? 'Decisão humana'} description={`${selectedApproval?.clientName ?? ''} · prioridade ${selectedApproval ? prettify(selectedApproval.priority) : ''}`} context={[{ label: 'Descrição', value: selectedApproval?.description ?? 'Sem observação adicional.' }, { label: 'Gate', value: 'Decisão interna; conectores e envios continuam bloqueados.' }]} disabled={approvalMutation.isPending || !selectedApproval} onReject={() => selectedApproval && approvalMutation.mutate({ approval: selectedApproval, status: 'rejected' })} onApprove={() => selectedApproval && approvalMutation.mutate({ approval: selectedApproval, status: 'approved' })} approveLabel="Aprovar localmente" />
      <AgentRunDrawer open={Boolean(runDrawer)} onOpenChange={(open) => !open && setRunDrawer(null)} agentName={runDrawer?.agentName ?? 'agente'} status={runDrawer?.status ?? 'queued'} summary={runDrawer?.summary} />
    </div>
  );
}
