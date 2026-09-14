import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  AudioLines,
  BellRing,
  Bot,
  Boxes,
  CalendarDays,
  Check,
  ChevronRight,
  ClipboardList,
  CloudOff,
  CreditCard,
  FileCheck2,
  FileSearch,
  FileText,
  Filter,
  Flag,
  Gauge,
  Headphones,
  History,
  Link2,
  ListFilter,
  Loader2,
  Mail,
  MessageSquareText,
  MessagesSquare,
  MonitorCheck,
  PenLine,
  PlugZap,
  Search,
  Send,
  ShieldCheck,
  Tags,
  UserRoundCog,
  Workflow,
  RefreshCw,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { KlmMetricCard, KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmNativeCapability,
  type KlmNativeCapabilityDisposition,
} from '@/klm/api';
import { nativeDisplayText, nativeDomainLabels } from '@/klm/native-display';
import { KLM_NATIVE_DOMAINS, isKlmNativeDomain, type KlmNativeDomain } from '@/klm/native-domains';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';

const chatTools: Array<{
  label: string;
  detail: string;
  icon: typeof Tags;
  path: string;
  blockedReason?: string;
}> = [
  { label: 'Etiqueta', detail: 'Classificar lead', icon: Tags, path: '/app/leads' },
  { label: 'Nota interna', detail: 'Registro privado', icon: PenLine, path: '/app/conversas' },
  { label: 'Tarefa', detail: 'Dono e prazo', icon: ClipboardList, path: '/app/agenda' },
  { label: 'Lembrete', detail: 'Follow-up', icon: BellRing, path: '/app/agenda' },
  { label: 'Resposta salva', detail: 'Playbook', icon: MessageSquareText, path: '/app/conversas' },
  { label: 'Agendar', detail: 'Reunião online', icon: CalendarDays, path: '/app/agenda' },
  { label: 'Disparo', detail: 'Sequência', icon: Send, path: '/app/disparos' },
  { label: 'Transferir', detail: 'Agente ou humano', icon: UserRoundCog, path: '/app/conversas' },
  { label: 'Marcar perdido', detail: 'Motivo', icon: Flag, path: '/app/funil' },
  { label: 'Contrato', detail: 'Assinatura', icon: FileCheck2, path: '/app/contratos' },
  { label: 'Procuração', detail: 'Dados', icon: FileText, path: '/app/contratos' },
  { label: 'Pagamento', detail: 'Cobrança', icon: CreditCard, path: '/app/financeiro' },
  { label: 'Docs / RAG', detail: 'Anexos', icon: FileSearch, path: '/app/leads' },
  { label: 'Áudio IA', detail: 'Sob demanda', icon: AudioLines, path: '/app/audio' },
  { label: 'Transcrever', detail: 'Áudio', icon: Headphones, path: '/app/audio' },
  { label: 'Resumo', detail: 'Copiloto', icon: Bot, path: '/app/conversas' },
  { label: 'Avaliação', detail: 'Pós-caso', icon: Check, path: '/app/qualidade' },
  { label: 'Remarketing', detail: 'Audiência', icon: ListFilter, path: '/app/rastreamento' },
  { label: 'Monitorar', detail: 'Processo e prazo', icon: MonitorCheck, path: '/app/atividade?view=processos' },
  { label: 'Histórico', detail: 'Auditoria', icon: History, path: '/app/atividade' },
];

const toolPresentation = [
  { key: 'whatsapp_official', title: 'WhatsApp oficial e múltiplos números', description: 'Caixas, prazos, distribuição, templates e atribuição de agente.', tags: ['Meta', 'multi', 'prazos'], state: 'Contrato local', icon: MessagesSquare, path: '/app/conversas?channel=whatsapp' },
  { key: 'email_inbound', title: 'E-mail inbound e RAG', description: 'Thread ligada ao lead e anexos encaminhados ao RAG do caso.', tags: ['e-mail', 'RAG', 'deduplicação'], state: 'Interface pronta', icon: Mail, path: '/app/conversas?channel=email' },
  { key: 'commercial_kanban', title: 'Kanban comercial automático', description: 'Etapa, dono, documentos, probabilidade e próximo passo atualizados pelo CRM.', tags: ['funil', 'CRM', 'score'], state: 'Interface pronta', icon: Gauge, path: '/app/funil' },
  { key: 'saved_replies', title: 'Respostas salvas e playbooks', description: 'Biblioteca por área, subtema, objeção, documento, proposta e no-show.', tags: ['templates', 'IA', 'tom'], state: 'Interface pronta', icon: MessageSquareText, path: '/app/conversas' },
  { key: 'calendar', title: 'Agenda e lembretes', description: 'Reunião online, confirmação 24h/1h, remarcação e recuperação de no-show.', tags: ['agenda', 'Meet', 'secretaria'], state: 'Contrato local', icon: CalendarDays, path: '/app/agenda' },
  { key: 'signature', title: 'Contratos e procurações', description: 'Coleta paralegal, revisão, assinatura, pendências e reenvio.', tags: ['ZapSign', 'docs', 'gate'], state: 'Contrato local', icon: FileCheck2, path: '/app/contratos' },
  { key: 'process_monitor', title: 'Monitoramento processual', description: 'Cadastro processual, eventos, prazos e minuta versionada de atualização ao cliente.', tags: ['andamento', 'prazos', 'cliente'], state: 'Interface pronta', icon: MonitorCheck, path: '/app/atividade?view=processos' },
  { key: 'campaigns', title: 'Disparos e listas', description: 'Segmentação, listas, importação, supressão, opt-out, limites, flows e receita atribuída.', tags: ['campanha', 'supressão', 'ROI'], state: 'Nativo', icon: Send, path: '/app/disparos' },
  { key: 'audio', title: 'Áudio e transcrição', description: 'Voz somente após insistência, transcrição, resumo e handoff humano.', tags: ['voz', 'copiloto', 'gate'], state: 'Contrato local', icon: AudioLines, path: '/app/audio' },
  { key: 'integrations', title: 'Central de integrações', description: 'APIs, MCPs e webhooks cadastrados no onboarding, com credenciais protegidas e teste local.', tags: ['API', 'MCP', 'webhook'], state: 'Nativo local', icon: PlugZap, path: '/app/integracoes' },
  { key: 'client_360', title: 'Cliente 360', description: 'Conversas, documentos, CRM, financeiro, contratos, reuniões e andamento.', tags: ['timeline', 'RAG', '360'], state: 'Interface pronta', icon: Link2, path: '/app/leads' },
  { key: 'ai_quality', title: 'Qualidade e treino IA', description: 'Evals, correções, versões, replay e publicação sob aprovação.', tags: ['QA', 'eval', 'prompt'], state: 'Interface pronta', icon: Workflow, path: '/app/qualidade' },
];

const benchmarks = [
  ['Atendimento comercial', 'WhatsApp, CRM, organização, automação, múltiplos atendentes e listas.'],
  ['Operação jurídica', 'API oficial, lead score, agenda, monitoramento, assinatura e calendários.'],
  ['Fechamento assistido', 'IA conversa, qualifica, agenda e conduz o fechamento pelo WhatsApp.'],
  ['Gestão omnichannel', 'Atendimento, IA, leads, tarefas, conversas e resultados centralizados.'],
];

const dispositionLabels: Record<KlmNativeCapabilityDisposition, string> = {
  discovered: 'Sem classificação',
  approved_native: 'Aprovada nativa',
  approved_embedded: 'Aprovada incorporada',
  approved_full_app: 'Aprovada integralmente',
  implemented: 'Implementada',
  not_applicable: 'Preservada',
  blocked_license: 'Bloqueada por licença',
  blocked_dependency: 'Bloqueada por dependência',
  blocked_external_gate: 'Gate externo',
};

const exposureLabels: Record<KlmNativeCapability['exposure'], string> = {
  none: 'não exposta',
  bridge: 'compatibilidade interna',
  native: 'nativa',
  microfrontend: 'componente incorporado',
  full_app: 'experiência integral',
};

const nativeModeLabels: Record<KlmNativeCapability['mode'], string> = {
  local_reconciled: 'reconciliada no núcleo nativo',
  local_command: 'comando canônico',
  local_canonical: 'fonte nativa',
  local_equivalent: 'equivalente nativo',
  controlled_configuration: 'configuração e simulação',
  responsive_substitution: 'experiência responsiva',
};

type CapabilityFilter = 'all' | 'implemented' | 'approved' | 'blocked' | 'preserved';

function matchesCapabilityFilter(
  disposition: KlmNativeCapabilityDisposition,
  filter: CapabilityFilter,
) {
  if (filter === 'all') return true;
  if (filter === 'implemented') return disposition === 'implemented';
  if (filter === 'approved') return disposition.startsWith('approved_');
  if (filter === 'blocked') return disposition.startsWith('blocked_');
  return disposition === 'not_applicable';
}

export default function ToolsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const toolsQuery = useQuery({ queryKey: ['klm', 'tools'], queryFn: klmApi.tools });
  const [activeTab, setActiveTab] = useState('native');
  const capabilityQuery = useQuery({
    queryKey: ['klm', 'capabilities'],
    queryFn: klmApi.capabilities,
    enabled: activeTab === 'native' || activeTab === 'coverage',
  });
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('todas');
  const [catalogPersonalizedFilter, setCatalogPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [capabilityText, setCapabilityText] = useState('');
  const [capabilityDomain, setCapabilityDomain] = useState<'all' | KlmNativeDomain>('all');
  const [capabilityFilter, setCapabilityFilter] = useState<CapabilityFilter>('all');
  const [coveragePersonalizedFilter, setCoveragePersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  useEffect(() => {
    const requestedTab = searchParams.get('tab');
    if (requestedTab && ['catalogo', 'toolbar', 'native', 'coverage', 'benchmark'].includes(requestedTab)) {
      setActiveTab(requestedTab);
    }
    const requestedDomain = searchParams.get('domain');
    if (requestedDomain && isKlmNativeDomain(requestedDomain)) {
      setCapabilityDomain(requestedDomain);
    }
    const requestedCapability = searchParams.get('capability');
    if (requestedCapability) setCapabilityText(requestedCapability);
  }, [searchParams]);
  const capabilities = useMemo(() => (toolsQuery.data ?? []).map((tool) => {
    const presentation = toolPresentation.find((item) => item.key === tool.key);
    return {
      key: tool.key,
      title: nativeDisplayText(tool.name),
      description: presentation?.description ?? `Capacidade ${tool.category} disponível no contrato local do Meu Executivo.`,
      tags: tool.capabilities.map((capability) => nativeDisplayText(capability.replaceAll('_', ' '))),
      state: tool.state === 'local_ready' ? 'Interface pronta' : tool.state === 'contract_local' ? 'Contrato local' : tool.state === 'blocked' ? 'Bloqueada' : 'Planejado',
      icon: presentation?.icon ?? PlugZap,
      humanGate: tool.humanGate,
      path: presentation?.path,
    };
  }), [toolsQuery.data]);
  const catalogPersonalizedFields = useMemo<PersonalizedFilterField<(typeof capabilities)[number]>[]>(() => [
    { key: 'title', label: 'Ferramenta', type: 'text' },
    { key: 'description', label: 'Descrição', type: 'text' },
    { key: 'state', label: 'Estado', type: 'select', options: [...new Set(capabilities.map((item) => item.state))].map((value) => ({ value, label: value })) },
    { key: 'humanGate', label: 'Gate humano', type: 'boolean' },
    { key: 'tags', label: 'Capacidade ou tag', type: 'text' },
    { key: 'path', label: 'Possui experiência nativa', type: 'boolean', getValue: (item) => Boolean(item.path) },
  ], [capabilities]);
  const filtered = useMemo(() => capabilities.filter((item) => {
    const searchable = `${item.title} ${item.description} ${item.tags.join(' ')}`.toLowerCase();
    const matchesText = searchable.includes(query.toLowerCase());
    const matchesState = filter === 'todas' || item.state.toLowerCase().includes(filter);
    return matchesText && matchesState && matchesPersonalizedFilter(item, catalogPersonalizedFilter, catalogPersonalizedFields);
  }), [capabilities, catalogPersonalizedFields, catalogPersonalizedFilter, filter, query]);
  const humanGates = capabilities.filter((item) => item.humanGate).length;
  const filteredNativeCapabilities = useMemo(() => {
    const normalizedQuery = capabilityText.trim().toLowerCase();
    return (capabilityQuery.data?.capabilities ?? []).filter((capability) => {
      const text = `${capability.id} ${capability.label} ${capability.description} ${capability.category} ${capability.surface} ${capability.reason}`.toLowerCase();
      return (capabilityDomain === 'all' || capability.domain === capabilityDomain)
        && matchesCapabilityFilter(capability.disposition, capabilityFilter)
        && (!normalizedQuery || text.includes(normalizedQuery));
    });
  }, [capabilityDomain, capabilityFilter, capabilityQuery.data?.capabilities, capabilityText]);
  const coveragePersonalizedFields = useMemo<PersonalizedFilterField<KlmNativeCapability>[]>(() => [
    { key: 'label', label: 'Capacidade', type: 'text' },
    { key: 'description', label: 'Descrição', type: 'text' },
    { key: 'domain', label: 'Domínio', type: 'select', options: KLM_NATIVE_DOMAINS.map((value) => ({ value, label: nativeDomainLabels[value] })) },
    { key: 'disposition', label: 'Implementação', type: 'select', options: Object.entries(dispositionLabels).map(([value, label]) => ({ value, label })) },
    { key: 'exposure', label: 'Exposição', type: 'select', options: Object.entries(exposureLabels).map(([value, label]) => ({ value, label })) },
    { key: 'mode', label: 'Modo', type: 'select', options: Object.entries(nativeModeLabels).map(([value, label]) => ({ value, label })) },
    { key: 'verified', label: 'Verificada', type: 'boolean' },
    { key: 'nativeRoute', label: 'Possui rota nativa', type: 'boolean', getValue: (item) => Boolean(item.nativeRoute) },
  ], []);
  const visibleNativeCapabilities = useMemo(
    () => filteredNativeCapabilities.filter((item) => matchesPersonalizedFilter(item, coveragePersonalizedFilter, coveragePersonalizedFields)),
    [coveragePersonalizedFields, coveragePersonalizedFilter, filteredNativeCapabilities],
  );

  const nativeModules = useMemo(() => (
    KLM_NATIVE_DOMAINS.map((domain) => {
      const rows = (capabilityQuery.data?.capabilities ?? []).filter((capability) => capability.domain === domain);
      return {
        domain,
        label: nativeDomainLabels[domain],
        rows,
        nativeRoutes: rows.filter((capability) => Boolean(capability.nativeRoute)).length,
        verified: rows.filter((capability) => capability.verified).length,
        available: rows.filter((capability) => !capability.disposition.startsWith('blocked_')).length,
        controlled: rows.filter((capability) => capability.disposition.startsWith('blocked_')).length,
      };
    })
  ), [capabilityQuery.data?.capabilities]);

  const openLocalSurface = (path: string, tool: string) => {
    const separator = path.includes('?') ? '&' : '?';
    navigate(`${path}${separator}source=tools&tool=${encodeURIComponent(tool)}`);
  };

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Operação premium</p>
          <h1 className="mt-2 text-2xl font-semibold">Ferramentas</h1>
          <p className="mt-1 max-w-2xl text-xs leading-4 text-muted-foreground">Módulos nativos, capacidades incorporadas e ferramentas da operação jurídica.</p>
        </div>
        <KlmPill tone="black">benchmark funcional concluído</KlmPill>
      </header>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KlmMetricCard label="Ferramentas catalogadas" value={toolsQuery.isSuccess ? String(capabilities.length) : '—'} delta={toolsQuery.isSuccess ? 'contrato local carregado' : toolsQuery.isError ? 'catálogo indisponível' : 'carregando contrato'} trend="up" />
        <KlmMetricCard label="Acionáveis no chat" value={String(chatTools.length)} delta="desktop e mobile" trend="up" />
        <KlmMetricCard label="Gates humanos" value={String(humanGates)} delta="ações sensíveis" trend="neutral" />
        <KlmMetricCard label="Conexões externas" value="0" delta="bloqueadas nesta fase" trend="neutral" />
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsList className="h-9 w-full justify-start overflow-x-auto bg-secondary p-1 sm:w-auto">
          <TabsTrigger value="catalogo" className="text-xs">Catálogo</TabsTrigger>
          <TabsTrigger value="toolbar" className="text-xs">Barra do atendimento</TabsTrigger>
          <TabsTrigger value="native" className="text-xs">Módulos nativos</TabsTrigger>
          <TabsTrigger value="coverage" className="text-xs">Cobertura nativa</TabsTrigger>
          <TabsTrigger value="benchmark" className="text-xs">Benchmark</TabsTrigger>
        </TabsList>

        <TabsContent value="catalogo" className="space-y-4">
          <KlmPanel className="p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input value={query} onChange={(event) => setQuery(event.target.value)} className="h-9 bg-secondary pl-9 text-xs" placeholder="Buscar ferramenta, módulo ou capacidade" />
              </div>
              <div className="flex gap-2 overflow-x-auto">
                {['todas', 'interface', 'contrato', 'bloqueada'].map((item) => (
                  <Button key={item} size="sm" variant={filter === item ? 'default' : 'outline'} onClick={() => setFilter(item)} className="h-9 shrink-0 text-2xs capitalize">
                    {item === 'todas' ? <Filter className="mr-1.5 h-3.5 w-3.5" /> : null}{item}
                  </Button>
                ))}
                <PersonalizedFilterBuilder scope="tools-catalog" fields={catalogPersonalizedFields} value={catalogPersonalizedFilter} onChange={setCatalogPersonalizedFilter} className="h-9" />
              </div>
            </div>
          </KlmPanel>

          {toolsQuery.isPending ? (
            <KlmPanel className="flex min-h-48 items-center justify-center text-2xs text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando catálogo operacional...
            </KlmPanel>
          ) : toolsQuery.isError ? (
            <KlmPanel className="p-6 text-center">
              <CloudOff className="mx-auto h-6 w-6 text-muted-foreground" />
              <p className="mt-3 text-xs font-semibold">Não foi possível carregar as ferramentas</p>
              <p className="mt-1 text-2xs text-muted-foreground">Nenhum catálogo demonstrativo será usado no lugar do endpoint local.</p>
              <Button variant="outline" size="sm" className="mt-4 h-8 text-2xs" onClick={() => void toolsQuery.refetch()}>Tentar novamente</Button>
            </KlmPanel>
          ) : <div className="grid gap-3 lg:grid-cols-2">
            {filtered.map((item) => {
              const Icon = item.icon;
              const blocked = !item.path;
              const blockedReason = 'Nenhuma superfície local foi vinculada a esta capacidade.';
              return (
                <KlmPanel key={item.title} className="group p-4 transition hover:border-primary">
                  <div className="flex items-start gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#031121] text-primary"><Icon className="h-4 w-4" /></div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <h2 className="text-sm font-semibold">{item.title}</h2>
                        <KlmPill tone={item.state === 'Interface pronta' ? 'green' : item.state === 'Bloqueada' ? 'red' : 'gold'}>{item.state}</KlmPill>
                      </div>
                      <p className="mt-2 text-2xs leading-4 text-muted-foreground">{item.description}</p>
                      <div className="mt-3 flex flex-wrap items-center gap-1.5">
                        {item.tags.map((tag) => <KlmPill key={tag} tone="muted">{tag}</KlmPill>)}
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={blocked}
                          title={blocked ? blockedReason : `Abrir ${item.title} na superfície operacional da plataforma`}
                          onClick={() => item.path && openLocalSurface(item.path, item.key)}
                          className="ml-auto h-7 px-2 text-2xs"
                        >
                          {blocked ? 'Superfície pendente' : item.state === 'Bloqueada' ? 'Configurar recurso' : 'Abrir recurso'} <ChevronRight className="ml-1 h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                  </div>
                </KlmPanel>
              );
            })}
            {!filtered.length && <KlmPanel className="p-8 text-center text-2xs text-muted-foreground">Nenhuma ferramenta corresponde aos filtros atuais.</KlmPanel>}
          </div>}
        </TabsContent>

        <TabsContent value="toolbar">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Barra do atendimento" description="Ferramentas disponíveis na conversa sem trocar de contexto." action={<KlmPill tone="green">{chatTools.length} ações</KlmPill>} />
            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
              {chatTools.map((tool) => {
                const Icon = tool.icon;
                const blocked = Boolean(tool.blockedReason);
                return (
                  <button
                    key={tool.label}
                    type="button"
                    disabled={blocked}
                    title={tool.blockedReason ?? `Abrir ${tool.label} no fluxo operacional`}
                    onClick={() => openLocalSurface(tool.path, tool.label)}
                    className="flex min-h-[76px] items-center gap-3 rounded-lg border border-border bg-secondary/60 p-3 text-left transition hover:border-primary hover:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    <Icon className="h-4 w-4 shrink-0 text-[#0057d8]" />
                    <span className="min-w-0"><span className="block text-xs font-semibold">{tool.label}</span><span className="mt-1 block text-2xs text-muted-foreground">{blocked ? 'Conector externo pendente' : tool.detail}</span></span>
                  </button>
                );
              })}
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="native" className="space-y-4">
          <KlmPanel className="overflow-hidden">
            <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="flex items-center gap-2 text-sm font-semibold"><Boxes className="h-4 w-4 text-[#0057d8]" />Arquitetura nativa</p>
                <p className="mt-1 text-2xs text-muted-foreground">Domínios canônicos, isolamento por workspace, auditoria e comandos idempotentes em uma única experiência.</p>
              </div>
              <div className="flex items-center gap-2">
                <KlmPill tone={nativeModules.every((module) => module.rows.length > 0) ? 'green' : 'gold'}>
                  {nativeModules.filter((module) => module.rows.length > 0).length}/4 módulos catalogados
                </KlmPill>
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => void capabilityQuery.refetch()} disabled={capabilityQuery.isFetching} title="Atualizar registro nativo">
                  <RefreshCw className={`h-3.5 w-3.5 ${capabilityQuery.isFetching ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </div>

            {capabilityQuery.isPending ? (
              <div className="flex min-h-40 items-center justify-center text-2xs text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Consultando o registro canônico...</div>
            ) : capabilityQuery.isError ? (
              <div className="p-6 text-center"><CloudOff className="mx-auto h-6 w-6 text-muted-foreground" /><p className="mt-3 text-xs font-semibold">Não foi possível consultar os módulos nativos</p><Button variant="outline" size="sm" className="mt-4 h-8 text-2xs" onClick={() => void capabilityQuery.refetch()}>Tentar novamente</Button></div>
            ) : (
              <div className="grid lg:grid-cols-2">
                {nativeModules.map((module) => {
                  return (
                    <article key={module.domain} className="border-b border-border p-4 lg:[&:nth-child(odd)]:border-r lg:[&:nth-last-child(-n+2)]:border-b-0">
                      <div className="flex items-start gap-3">
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center bg-[#031121] text-primary"><Boxes className="h-4 w-4" /></span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-start justify-between gap-2"><div><h2 className="text-sm font-semibold">{module.label}</h2><p className="mt-0.5 text-2xs text-muted-foreground">núcleo canônico · auditoria local · isolamento por workspace</p></div><KlmPill tone={module.rows.length > 0 ? 'green' : 'red'}>{module.rows.length > 0 ? 'operacional' : 'sem contrato'}</KlmPill></div>
                          <p className="mt-3 text-2xs leading-4 text-muted-foreground">Capacidades incorporadas ao domínio jurídico da plataforma, com rotas internas e governança uniforme.</p>
                          <div className="mt-3 flex flex-wrap gap-1.5">
                            <KlmPill tone="black">{module.rows.length} catalogadas</KlmPill>
                            <KlmPill tone="green">{module.nativeRoutes} rotas nativas</KlmPill>
                            <KlmPill tone={module.verified === module.rows.length ? 'green' : 'gold'}>{module.verified} verificadas</KlmPill>
                            <KlmPill tone="green">{module.available} disponíveis</KlmPill>
                            <KlmPill tone={module.controlled ? 'gold' : 'green'}>{module.controlled} efeitos controlados</KlmPill>
                          </div>
                          <div className="mt-4 flex flex-wrap gap-2">
                            <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => { setCapabilityDomain(module.domain); setCapabilityFilter('all'); setActiveTab('coverage'); }}>Ver capacidades</Button>
                          </div>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </KlmPanel>

          <KlmPanel className="flex items-start gap-3 p-4"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" /><div><p className="text-2xs font-semibold">Fronteira operacional nativa</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">A SPA usa exclusivamente rotas nativas, dados com escopo de workspace e comandos auditáveis. Canais externos, APIs de modelos e AWS seguem bloqueados nesta fase local.</p></div></KlmPanel>
        </TabsContent>

        <TabsContent value="coverage" className="space-y-4">
          {capabilityQuery.isPending ? (
            <KlmPanel className="flex min-h-48 items-center justify-center text-2xs text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando matriz de incorporação...
            </KlmPanel>
          ) : capabilityQuery.isError ? (
            <KlmPanel className="p-6 text-center">
              <CloudOff className="mx-auto h-6 w-6 text-muted-foreground" />
              <p className="mt-3 text-xs font-semibold">Não foi possível carregar a matriz nativa</p>
              <p className="mt-1 text-2xs text-muted-foreground">O runtime pode continuar saudável mesmo quando o ledger não está disponível.</p>
              <Button variant="outline" size="sm" className="mt-4 h-8 text-2xs" onClick={() => void capabilityQuery.refetch()}>Tentar novamente</Button>
            </KlmPanel>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <KlmMetricCard label="Capacidades" value={String(capabilityQuery.data?.summary.total ?? 0)} delta="registro público" trend="up" />
                <KlmMetricCard label="Disponíveis" value={String(capabilityQuery.data?.summary.available ?? 0)} delta="rotas nativas" trend="up" />
                <KlmMetricCard label="Verificadas" value={String(capabilityQuery.data?.summary.verified ?? 0)} delta="operação local" trend="up" />
                <KlmMetricCard label="Efeitos externos" value={String(capabilityQuery.data?.summary.externalEffectVerified ?? 0)} delta="bloqueados até aprovação" trend="neutral" />
              </div>

              <KlmPanel data-testid="native-qa-verification" className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start">
                <ShieldCheck className={`mt-0.5 h-4 w-4 shrink-0 ${capabilityQuery.data?.verification.state === 'verified' ? 'text-emerald-700' : 'text-amber-700'}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-2xs font-semibold">Integridade da incorporação</p>
                    <KlmPill tone={capabilityQuery.data?.verification.state === 'verified' ? 'green' : capabilityQuery.data?.verification.state === 'partial' ? 'gold' : 'red'}>
                      {capabilityQuery.data?.verification.state === 'verified' ? 'verificada' : capabilityQuery.data?.verification.state === 'partial' ? 'parcial' : 'não verificada'}
                    </KlmPill>
                  </div>
                  <p className="mt-1 text-2xs leading-4 text-muted-foreground">{nativeDisplayText(capabilityQuery.data?.verification.reason)}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <KlmPill tone="black">{capabilityQuery.data?.verification.checks ?? 0} verificações</KlmPill>
                    <KlmPill tone="muted">escopo {capabilityQuery.data?.scope ?? 'Meu Executivo'}</KlmPill>
                  </div>
                </div>
              </KlmPanel>

              <KlmPanel className="p-4">
                <KlmSectionTitle
                  title="Matriz de incorporação"
                  description="Cada capacidade tem contrato, experiência, rota e evidência próprios dentro da plataforma."
                  action={(
                    <div className="flex flex-wrap justify-end gap-2">
                      <KlmPill tone={(capabilityQuery.data?.summary.available ?? 0) === (capabilityQuery.data?.summary.total ?? 0) ? 'green' : 'gold'}>{capabilityQuery.data?.summary.available ?? 0}/{capabilityQuery.data?.summary.total ?? 0} disponíveis · {capabilityQuery.data?.summary.verified ?? 0} verificadas</KlmPill>
                      <Button variant="outline" size="sm" className="h-7 text-2xs" onClick={() => navigate('/app/ferramentas/inventario')}>
                        Ver 3.708 funções <ChevronRight className="ml-1 h-3 w-3" />
                      </Button>
                    </div>
                  )}
                />
                <div className="mt-4 flex flex-col gap-3">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input value={capabilityText} onChange={(event) => setCapabilityText(event.target.value)} className="h-9 bg-secondary pl-9 text-xs" placeholder="Buscar capacidade, superfície ou justificativa" />
                  </div>
                  <div className="flex gap-2 overflow-x-auto pb-1">
                    {(['all', ...KLM_NATIVE_DOMAINS] as const).map((domain) => (
                      <Button key={domain} size="sm" variant={capabilityDomain === domain ? 'default' : 'outline'} className="h-8 shrink-0 text-2xs" onClick={() => setCapabilityDomain(domain)}>
                        {domain === 'all' ? 'Todos os domínios' : nativeDomainLabels[domain]}
                      </Button>
                    ))}
                    <PersonalizedFilterBuilder scope="native-capabilities" fields={coveragePersonalizedFields} value={coveragePersonalizedFilter} onChange={setCoveragePersonalizedFilter} />
                  </div>
                  <div className="flex gap-2 overflow-x-auto pb-1">
                    {(['all', 'implemented', 'approved', 'blocked', 'preserved'] as CapabilityFilter[]).map((state) => (
                      <Button key={state} size="sm" variant={capabilityFilter === state ? 'secondary' : 'ghost'} className="h-7 shrink-0 text-2xs" onClick={() => setCapabilityFilter(state)}>
                        {{ all: 'Todos os estados', implemented: 'Implementadas', approved: 'Aprovadas', blocked: 'Bloqueadas', preserved: 'Preservadas' }[state]}
                      </Button>
                    ))}
                  </div>
                </div>
              </KlmPanel>

              <div className="space-y-2">
                {visibleNativeCapabilities.map((capability) => {
                  const blocked = capability.disposition.startsWith('blocked_');
                  const exposedInKlm = Boolean(capability.nativeRoute);
                  const dispositionTone = capability.disposition === 'implemented'
                    ? 'green'
                    : blocked
                      ? 'red'
                      : capability.disposition === 'not_applicable'
                        ? 'muted'
                        : 'gold';
                  return (
                    <article key={capability.id} data-testid={`native-capability-${capability.id}`} className="grid gap-4 border border-border bg-card p-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(230px,.65fr)_auto] lg:items-center">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <KlmPill tone="black">{nativeDomainLabels[capability.domain]}</KlmPill>
                          <KlmPill tone={dispositionTone}>{dispositionLabels[capability.disposition]}</KlmPill>
                          <KlmPill tone="muted">disponibilidade: {exposureLabels[capability.exposure]}</KlmPill>
                          <KlmPill tone="muted">{nativeModeLabels[capability.mode]}</KlmPill>
                          <KlmPill tone={capability.verified ? 'green' : 'gold'}>{capability.verified ? 'operação verificada' : 'validação pendente'}</KlmPill>
                        </div>
                        <h3 className="mt-3 text-sm font-semibold">{nativeDisplayText(capability.label)}</h3>
                        <p className="mt-1 text-2xs leading-4 text-muted-foreground">{nativeDisplayText(capability.description)}</p>
                      </div>
                      <div className="min-w-0 border-l-2 border-primary/40 pl-3">
                        <p className="text-2xs font-semibold uppercase text-[#0057d8]">{nativeDisplayText(capability.surface)}</p>
                        <p className="mt-1 text-2xs leading-4 text-muted-foreground">
                          {nativeDisplayText(capability.reason)}
                        </p>
                        <p className="mt-1 text-2xs leading-4 text-muted-foreground/80">{capability.verified ? 'Disponível na experiência nativa.' : 'Validação funcional em andamento.'}</p>
                      </div>
                      <div className="flex flex-wrap gap-2 lg:w-[132px] lg:flex-col">
                        {exposedInKlm && (
                          <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => navigate(capability.nativeRoute)}>
                            <Link2 className="mr-1.5 h-3 w-3" />Abrir recurso
                          </Button>
                        )}
                        {!exposedInKlm && <KlmPill tone="gold">rota nativa pendente</KlmPill>}
                        {blocked && <KlmPill tone="gold">efeito externo controlado</KlmPill>}
                      </div>
                    </article>
                  );
                })}
                {!visibleNativeCapabilities.length && (
                  <KlmPanel className="p-8 text-center text-2xs text-muted-foreground">Nenhuma capacidade corresponde aos filtros atuais.</KlmPanel>
                )}
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="benchmark">
          <div className="grid gap-4 lg:grid-cols-[1.2fr_.8fr]">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Benchmark transformado em requisitos" description="Cobertura funcional incorporada ao desenho do Meu Executivo." />
              <div className="mt-4 divide-y divide-border rounded-lg border">
                {benchmarks.map(([name, description]) => <div key={name} className="grid gap-1 p-4 sm:grid-cols-[110px_1fr]"><strong className="text-xs">{name}</strong><span className="text-2xs leading-4 text-muted-foreground">{description}</span></div>)}
              </div>
            </KlmPanel>
            <KlmPanel tone="black" className="p-5">
              <CloudOff className="h-5 w-5 text-primary" />
              <h2 className="mt-4 text-lg font-semibold">Gate de integrações</h2>
              <p className="mt-2 text-2xs leading-4 text-white/60">WhatsApp, ZapSign, Asaas, Ads, e-mail, calendários, Docs e voz permanecem sem credenciais e sem chamadas externas.</p>
              <div className="mt-5 space-y-2 text-2xs">
                {['Contrato de interface', 'Dados salvos com segurança', 'Auditoria de ação', 'Aprovação antes de ativar'].map((item) => <div key={item} className="flex items-center gap-2 rounded-md border border-white/10 p-2.5"><Check className="h-3.5 w-3.5 text-primary" />{item}</div>)}
              </div>
            </KlmPanel>
          </div>
        </TabsContent>
      </Tabs>

    </div>
  );
}
