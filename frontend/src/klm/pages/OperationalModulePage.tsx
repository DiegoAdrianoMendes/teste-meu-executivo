import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';
import {
  ArrowLeft,
  BookOpenCheck,
  CalendarClock,
  Check,
  ChevronRight,
  CircleAlert,
  Database,
  Filter,
  Loader2,
  LockKeyhole,
  Plus,
  RotateCw,
  Search,
  ShieldCheck,
  ShieldOff,
  Trophy,
  UserRoundCog,
  Users,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import { UserManagement } from '@/components/admin/UserManagement';
import { roleListLabel } from '@/lib/role-labels';
import { FinanceUpgradePauseNotice } from '@/components/finance/FinanceUpgradePauseNotice';
import { FinanceAgingCard } from '@/klm/components/FinanceAgingCard';
import { FinanceDunningCard } from '@/klm/components/FinanceDunningCard';
import { FinanceOverviewPanel } from '@/klm/components/FinanceOverviewPanel';
import { FinanceHarmonizationCard, ReconciliationPanel } from '@/klm/components/ReconciliationPanel';
import { RecurrencesPanel } from '@/klm/components/RecurrencesPanel';
import { ReferralsPanel } from '@/klm/components/ReferralsPanel';
import { FeeArrangementsPanel } from '@/klm/components/FeeArrangementsPanel';
import { ContractBillingProposalsPanel } from '@/klm/components/ContractBillingProposalsPanel';
import { FinanceEntryChargeRow } from '@/klm/components/FinanceEntryChargeRow';
import { NativeCRMOperationsPanel } from '@/klm/components/NativeCRMOperationsPanel';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { MessagingCadenceSettingsPanel } from '@/klm/components/MessagingCadenceSettingsPanel';
import { OwnerAlertChannelPanel } from '@/klm/components/OwnerAlertChannelPanel';
import {
  StaffAvailabilitySheet,
  type StaffAvailabilityApproval,
  type StaffAvailabilityRequestInput,
} from '@/klm/components/StaffAvailabilitySheet';
import { browserLocalDateTimeToIso, staffAvailabilityValueFromApi } from '@/klm/staff-availability-adapter';
import { LegalArtifactsPanel } from '@/klm/components/LegalArtifactsPanel';
import { ProcessMonitoringPanel } from '@/klm/components/ProcessMonitoringPanel';
import { klmApi, type KlmExternalActionIntent, type KlmFinanceCharge, type KlmFinanceEntry } from '@/klm/api';
import { formatBRL } from '@/lib/formatters';
import {
  fetchInvitationLink,
  fetchRoleCatalog,
  fetchTeamMembers,
  fetchWorkspaceAccessPackages,
  resendTeamInvitation,
  revokeTeamInvitation,
  saveAdminUser,
} from '@/services/admin';
import { useAppStore } from '@/store/app-context';
import type { AdminUser } from '@/types';
import { type ContextualPageProps, useContextualNavigate, useContextualSearchParams } from '@/klm/hooks/useContextualRouting';

type ModuleMetric = [label: string, value: string, detail: string];

type ModuleRow = {
  id?: string;
  title: string;
  meta: string;
  status: string;
  progress?: number;
  progressLabel?: string;
  sourceLabel: string;
};

type ModuleDefinition = {
  eyebrow: string;
  title: string;
  description: string;
  queueTitle: string;
};

type ModuleView = ModuleDefinition & {
  metrics: ModuleMetric[];
  rows: ModuleRow[];
  highlights: string[];
};

const definitions: Record<string, ModuleDefinition> = {
  contratos: {
    eyebrow: 'Documentos / Fechamento',
    title: 'Contratos e procurações',
    description: 'Casos persistidos nas etapas de proposta, contrato, pagamento e ganho, com acesso ao contexto integral do CRM jurídico.',
    queueTitle: 'Casos nas etapas de fechamento',
  },
  financeiro: {
    eyebrow: 'Financeiro / Honorários',
    title: 'Financeiro e cobrança',
    description: 'Recebíveis, vencimentos, pagamentos e lembretes registrados no financeiro local.',
    queueTitle: 'Lançamentos financeiros',
  },
  peticoes: {
    eyebrow: 'Jurídico / Produção',
    title: 'Petições',
    description: 'Casos persistidos disponíveis para produção jurídica, com score documental e contexto integral do CRM jurídico.',
    queueTitle: 'Casos disponíveis para produção',
  },
  configuracoes: {
    eyebrow: 'Sistema / Configuração',
    title: 'Configurações do Meu Executivo',
    description: 'Áreas, ferramentas, runtime, usuários, permissões e políticas do workspace.',
    queueTitle: 'Catálogo de configurações',
  },
  atividade: {
    eyebrow: 'Sistema / Auditoria',
    title: 'Atividade e trilha de auditoria',
    description: 'Eventos persistidos com ator, entidade, tipo e horário registrados pelo backend local.',
    queueTitle: 'Eventos registrados',
  },
  aprovacoes: {
    eyebrow: 'Governança / Humano no controle',
    title: 'Fila de aprovações',
    description: 'Solicitações persistidas que aguardam ou já receberam uma decisão humana explícita.',
    queueTitle: 'Solicitações de aprovação',
  },
};

const unsupportedDefinition: ModuleDefinition = {
  eyebrow: 'Meu Executivo / Módulo operacional',
  title: 'Módulo indisponível',
  description: 'Esta rota não possui uma fonte de dados operacional configurada.',
  queueTitle: 'Registros',
};

const processDefinition: ModuleDefinition = {
  eyebrow: 'Jurídico / Processos',
  title: 'Monitoramento processual',
  description: 'Processos vinculados aos cards, prazos, movimentações e rascunhos de atualização ao cliente.',
  queueTitle: 'Processos monitorados',
};

const settingsCatalog: ModuleRow[] = [
  {
    title: 'Módulos e ferramentas',
    meta: 'Capacidades nativas, ferramentas disponíveis e controles de execução.',
    status: 'Abrir',
    sourceLabel: 'Catálogo de configurações',
  },
  {
    title: 'Áudio IA',
    meta: 'Vozes, síntese, transcrição e controles de uso de áudio pelos agentes.',
    status: 'Abrir',
    sourceLabel: 'Catálogo de configurações',
  },
  {
    title: 'Identidade e marca',
    meta: 'Nome, cores, tema claro ou escuro e metadados da interface.',
    status: 'Configurar',
    sourceLabel: 'Catálogo de configurações',
  },
  {
    title: 'Usuários e permissões',
    meta: 'Contas, papéis, ações sensíveis e visibilidade de dados por usuário.',
    status: 'Gerenciar',
    sourceLabel: 'Catálogo de configurações',
  },
  {
    title: 'Políticas de agentes',
    meta: 'Ferramentas, handoffs, aprovações e limites por papel.',
    status: 'Configurar',
    sourceLabel: 'Catálogo de configurações',
  },
  {
    title: 'Integrações e credenciais',
    meta: 'APIs, MCPs, webhooks e gates para efeitos externos.',
    status: 'Configurar',
    sourceLabel: 'Catálogo de configurações',
  },
  {
    title: 'Retenção e auditoria',
    meta: 'Eventos, versões, evidências e exportações do workspace.',
    status: 'Configurar',
    sourceLabel: 'Catálogo de configurações',
  },
];

const settingsTabs: Record<string, string> = {
  'Identidade e marca': 'personalizacao',
  'Políticas de agentes': 'ia',
  'Integrações e credenciais': 'integracoes',
  'Retenção e auditoria': 'seguranca',
};

const formatCurrency = formatBRL;
const titleCase = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());

const statusTone = (status: string): 'red' | 'green' | 'gold' => {
  const normalized = status.toLowerCase();
  if (['bloq', 'atras', 'rejeit', 'cancel', 'erro'].some((term) => normalized.includes(term))) return 'red';
  if (['pago', 'aprov', 'conclu', 'ganho', 'ativo'].some((term) => normalized.includes(term))) return 'green';
  return 'gold';
};

export default function OperationalModulePage({ embedded = false, onNavigate, contextPath }: ContextualPageProps) {
  const { can, state } = useAppStore();
  const canManageIntegrations = can('integrations', 'manage');
  const canManageFinanceCharges = can('finance', 'manage');
  const location = useLocation();
  const navigate = useContextualNavigate({ embedded, onNavigate });
  const [searchParams] = useContextualSearchParams({ embedded, contextPath });
  const queryClient = useQueryClient();
  const contextualPathname = embedded && contextPath
    ? new URL(contextPath, 'http://klm.local').pathname
    : location.pathname;
  const slug = contextualPathname.split('/').filter(Boolean).at(-1) ?? '';
  const canResolveCurrentRow = slug === 'aprovacoes'
    ? can('aprovacoes', 'approve')
    : canManageIntegrations;
  const isProcessView = slug === 'atividade' && searchParams.get('view') === 'processos';
  const baseDefinition = isProcessView ? processDefinition : definitions[slug] ?? unsupportedDefinition;
  const isSupportedModule = Boolean(definitions[slug]);

  const financeQuery = useQuery({ queryKey: ['klm', 'finance'], queryFn: klmApi.financeOverview, enabled: slug === 'financeiro' });
  const financeChargesQuery = useQuery({
    queryKey: ['klm', 'finance-charges'],
    queryFn: () => klmApi.listFinanceCharges({ limit: 300 }),
    enabled: slug === 'financeiro',
  });
  const financeChargesByEntryId = useMemo(() => {
    const map = new Map<string, KlmFinanceCharge>();
    for (const charge of financeChargesQuery.data ?? []) map.set(charge.entryId, charge);
    return map;
  }, [financeChargesQuery.data]);
  const approvalsQuery = useQuery({ queryKey: ['klm', 'approvals'], queryFn: klmApi.approvals, enabled: slug === 'aprovacoes' });
  const activityQuery = useQuery({ queryKey: ['klm', 'activity'], queryFn: klmApi.activity, enabled: slug === 'atividade' && !isProcessView });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel, enabled: slug === 'contratos' || slug === 'peticoes' });
  const runtimeQuery = useQuery({ queryKey: ['klm', 'runtime'], queryFn: klmApi.runtime, enabled: slug === 'configuracoes' });
  const areasQuery = useQuery({ queryKey: ['klm', 'areas'], queryFn: klmApi.areas, enabled: slug === 'configuracoes' });
  const toolsQuery = useQuery({ queryKey: ['klm', 'tools'], queryFn: klmApi.tools, enabled: slug === 'configuracoes' });

  const [query, setQuery] = useState('');
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState('todos');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [draftRecord, setDraftRecord] = useState<KlmExternalActionIntent | null>(null);
  const [selectedRow, setSelectedRow] = useState<ModuleRow | null>(null);
  const [handledContextKey, setHandledContextKey] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const [settingsView, setSettingsView] = useState<'overview' | 'team'>('overview');
  const [teamActionFeedback, setTeamActionFeedback] = useState<string | null>(null);
  const [availabilityTarget, setAvailabilityTarget] = useState<AdminUser | null>(null);
  const [profileTarget, setProfileTarget] = useState<AdminUser | null>(null);
  const isDedicatedTeamView = slug === 'equipe';
  const isTeamView = isDedicatedTeamView || (slug === 'configuracoes' && settingsView === 'team');
  const activeWorkspaceRole = state?.session?.activeRole ?? state?.currentUser?.role ?? '';
  const canManageStaffAvailability = ['workspace_owner', 'workspace_admin', 'legal_manager'].includes(activeWorkspaceRole);
  const canApproveStaffAvailability = ['workspace_owner', 'workspace_admin', 'legal_manager'].includes(activeWorkspaceRole);

  const teamQuery = useQuery({
    queryKey: ['klm', 'team-members'],
    queryFn: fetchTeamMembers,
    enabled: isTeamView,
  });
  const rolesQuery = useQuery({
    queryKey: ['klm', 'role-catalog'],
    queryFn: fetchRoleCatalog,
    enabled: isTeamView,
  });
  const accessPackagesQuery = useQuery({
    queryKey: ['klm', 'workspace-access-packages'],
    queryFn: fetchWorkspaceAccessPackages,
    enabled: isTeamView,
  });
  const teamPerformanceQuery = useQuery({
    queryKey: ['klm', 'kpis', 30, null, 'team-page'],
    queryFn: () => klmApi.kpis(30, null),
    enabled: isTeamView && can('relatorios', 'view'),
    retry: false,
  });
  const availabilityStateQuery = useQuery({
    queryKey: ['klm', 'staff-availability', availabilityTarget?.id],
    queryFn: () => klmApi.staffAvailabilityState(availabilityTarget!.id),
    enabled: Boolean(isTeamView && availabilityTarget?.id),
  });
  const effectiveAvailabilityQuery = useQuery({
    queryKey: ['klm', 'staff-availability-effective', availabilityTarget?.id],
    queryFn: () => klmApi.staffEffectiveAvailability(availabilityTarget!.id),
    enabled: Boolean(isTeamView && availabilityTarget?.id),
  });
  const availabilityRequestsQuery = useQuery({
    queryKey: ['klm', 'staff-availability-requests', availabilityTarget?.id],
    queryFn: () => klmApi.staffAvailabilityRequests({ targetUserId: availabilityTarget!.id, status: 'pending' }),
    enabled: Boolean(isTeamView && availabilityTarget?.id),
  });
  const availabilityTeamsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-teams'],
    queryFn: klmApi.omnichannelTeams,
    enabled: Boolean(isTeamView && availabilityTarget?.id),
  });
  const saveTeamMemberMutation = useMutation({
    mutationFn: (user: AdminUser) => saveAdminUser(user),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'team-members'] });
    },
  });
  const createAvailabilityRequestMutation = useMutation({
    mutationFn: async (input: StaffAvailabilityRequestInput) => {
      const currentState = availabilityStateQuery.data ?? (await availabilityStateQuery.refetch()).data;
      return klmApi.createStaffAvailabilityRequest({
        targetUserId: input.staffId,
        expectedVersion: currentState?.profile?.version ?? 0,
        reason: input.requestNote || 'Atualização de disponibilidade solicitada na gestão da equipe.',
        proposedChange: {
        changeType: 'replace_configuration',
        mode: input.mode,
        timezone: input.timezone,
        weeklyAvailability: input.weeklyAvailability.map((day) => ({
          weekday: day.weekday,
          enabled: day.enabled,
          windows: day.windows.map((window) => ({ ...window })),
        })),
        exceptions: input.exceptions.map((exception) => ({
          id: exception.id,
          kind: exception.kind,
          name: exception.name,
          startsOn: exception.startsOn,
          endsOn: exception.endsOn,
          mode: exception.mode,
          windows: exception.windows.map((window) => ({ ...window })),
        })),
        absenceMessage: { ...input.absenceMessage },
        activation: input.activation.mode === 'now'
          ? { mode: 'now', startsAt: null, endsAt: null }
          : {
              mode: 'scheduled',
              startsAt: browserLocalDateTimeToIso(input.activation.startsAt),
              endsAt: browserLocalDateTimeToIso(input.activation.endsAt),
            },
        redirect: { ...input.redirect },
        automaticSwap: input.automaticSwap,
        },
      });
    },
    onSuccess: async (_, input) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability', input.staffId] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-effective', input.staffId] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-requests', input.staffId] }),
      ]);
      setTeamActionFeedback('Solicitação de escala registrada. A configuração só entra em vigor após a aprovação exigida.');
    },
  });
  const decideAvailabilityRequestMutation = useMutation({
    mutationFn: ({ requestId, decision }: { requestId: string; staffId: string; decision: 'approve' | 'reject' }) => (
      klmApi.decideStaffAvailabilityRequest(
        requestId,
        decision,
        decision === 'approve'
          ? 'Escala revisada e aprovada na gestão da equipe.'
          : 'Escala devolvida para revisão na gestão da equipe.',
      )
    ),
    onSuccess: async (_, input) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability', input.staffId] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-effective', input.staffId] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-requests', input.staffId] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] }),
      ]);
      setTeamActionFeedback(input.decision === 'approve' ? 'Escala aprovada e aplicada com auditoria.' : 'Solicitação rejeitada; a escala anterior foi preservada.');
    },
  });

  const refreshTeam = async () => {
    await queryClient.invalidateQueries({ queryKey: ['klm', 'team-members'] });
  };

  const handleResendInvitation = async (invitationId: string) => {
    setTeamActionFeedback(null);
    try {
      await resendTeamInvitation(invitationId);
      await refreshTeam();
      setTeamActionFeedback('Convite local rotacionado e reenviado.');
    } catch (error) {
      setTeamActionFeedback(
        error instanceof Error ? error.message : 'Não foi possível reenviar o convite.',
      );
      throw error;
    }
  };

  const handleRevokeInvitation = async (invitationId: string) => {
    setTeamActionFeedback(null);
    try {
      await revokeTeamInvitation(invitationId);
      await refreshTeam();
      setTeamActionFeedback('Convite revogado.');
    } catch (error) {
      setTeamActionFeedback(
        error instanceof Error ? error.message : 'Não foi possível revogar o convite.',
      );
      throw error;
    }
  };

  const definition = useMemo<ModuleView>(() => {
    const emptyView: ModuleView = { ...baseDefinition, metrics: [], rows: [], highlights: [] };

    if (slug === 'financeiro' && financeQuery.data) {
      const finance = financeQuery.data;
      return {
        ...emptyView,
        metrics: [
          ['Recebido', formatCurrency(finance.paidAmount), 'valor persistido'],
          ['A receber', formatCurrency(finance.totalOpenReceivables), `${finance.upcomingEntries.length} lançamento(s)`],
          ['Em atraso', formatCurrency(finance.overdueAmount), 'valor persistido'],
          ['Lembretes pendentes', String(finance.pendingReminders), `${finance.entriesDueSoon} vencimento(s) próximo(s)`],
        ],
        rows: finance.upcomingEntries.map((entry) => ({
          id: entry.id,
          title: entry.title,
          meta: `${formatCurrency(entry.amount)} · ${entry.dueDate ? `vence ${new Date(entry.dueDate).toLocaleDateString('pt-BR')}` : 'sem vencimento'} · ${entry.provider ?? 'registro local'}`,
          status: entry.status === 'paid'
            ? 'Pago'
            : entry.status === 'overdue'
              ? 'Em atraso'
              : entry.status === 'scheduled'
                ? 'Agendado'
                : entry.status === 'cancelled'
                  ? 'Cancelado'
                  : 'Pendente',
          sourceLabel: 'Financeiro local',
        })),
        highlights: finance.highlights.filter((highlight) => highlight.trim().length > 0),
      };
    }

    if (slug === 'aprovacoes' && approvalsQuery.data) {
      const approvals = approvalsQuery.data;
      const pending = approvals.filter((approval) => approval.status === 'pending');
      return {
        ...emptyView,
        metrics: [
          ['Pendentes', String(pending.length), `${pending.filter((item) => item.priority === 'alta').length} de prioridade alta`],
          ['Aprovadas', String(approvals.filter((item) => item.status === 'approved').length), 'decisões persistidas'],
          ['Rejeitadas', String(approvals.filter((item) => item.status === 'rejected').length), 'decisões persistidas'],
          ['Com ajustes', String(approvals.filter((item) => item.status === 'changes_requested').length), 'decisões persistidas'],
        ],
        rows: approvals.map((approval) => ({
          id: approval.id,
          title: approval.title,
          meta: `${approval.clientName || 'Cliente não exibido'} · ${approval.description ?? titleCase(approval.type)} · ${new Date(approval.requestedAt).toLocaleString('pt-BR')}`,
          status: approval.status === 'pending'
            ? approval.priority === 'alta' ? 'Pendente · alta' : 'Pendente'
            : approval.status === 'approved'
              ? 'Aprovada'
              : approval.status === 'rejected'
                ? 'Rejeitada'
                : 'Ajustes solicitados',
          sourceLabel: 'Fila de aprovações',
        })),
      };
    }

    if (slug === 'atividade' && activityQuery.data) {
      const events = activityQuery.data;
      return {
        ...emptyView,
        metrics: [
          ['Eventos', String(events.length), 'registros retornados'],
          ['Atores humanos', String(events.filter((event) => event.actorType === 'human').length), 'eventos registrados'],
          ['Agentes', String(events.filter((event) => event.actorType === 'agent').length), 'eventos registrados'],
          ['Sistema', String(events.filter((event) => event.actorType === 'system').length), 'eventos registrados'],
        ],
        rows: events.map((event) => ({
          id: event.id,
          title: event.summary,
          meta: `${event.actorLabel} · ${titleCase(event.eventType)} · ${new Date(event.occurredAt).toLocaleString('pt-BR')}`,
          status: event.actorType ? titleCase(event.actorType) : 'Ator não informado',
          sourceLabel: `${titleCase(event.entityType)}${event.entityId ? ` · ${event.entityId}` : ''}`,
        })),
      };
    }

    if (slug === 'contratos' && funnelQuery.data) {
      const cards = funnelQuery.data.cards.filter((card) => ['proposta', 'contrato', 'pagamento', 'ganho'].includes(card.stage));
      return {
        ...emptyView,
        metrics: [
          ['Propostas', String(cards.filter((card) => card.stage === 'proposta').length), 'casos persistidos'],
          ['Em contrato', String(cards.filter((card) => card.stage === 'contrato').length), 'casos persistidos'],
          ['Em pagamento', String(cards.filter((card) => card.stage === 'pagamento').length), 'casos persistidos'],
          ['Ganhos', String(cards.filter((card) => card.stage === 'ganho').length), 'casos persistidos'],
        ],
        rows: cards.map((card) => ({
          id: card.id,
          title: `${card.name} · ${titleCase(card.stage)}`,
          meta: `${titleCase(card.subject)} · ${card.object} · ${card.documents} documento(s)`,
          status: titleCase(card.microStatus || card.stage),
          progress: card.documentScore,
          progressLabel: 'Score documental',
          sourceLabel: 'Funil local',
        })),
      };
    }

    if (slug === 'peticoes' && funnelQuery.data) {
      const cards = [...funnelQuery.data.cards].sort((left, right) => right.documentScore - left.documentScore);
      const averageDocumentScore = cards.length
        ? Math.round(cards.reduce((total, card) => total + card.documentScore, 0) / cards.length)
        : 0;
      return {
        ...emptyView,
        metrics: [
          ['Casos', String(cards.length), 'registros no funil'],
          ['Docs ≥ 80%', String(cards.filter((card) => card.documentScore >= 80).length), 'score documental persistido'],
          ['Sem documentos', String(cards.filter((card) => card.documents === 0).length), 'registros no funil'],
          ['Score documental médio', `${averageDocumentScore}%`, 'média dos casos retornados'],
        ],
        rows: cards.map((card) => ({
          id: card.id,
          title: `${card.name} · ${card.object}`,
          meta: `${titleCase(card.subject)} · ${card.documents} documento(s) · etapa ${titleCase(card.stage)}`,
          status: titleCase(card.microStatus || card.stage),
          progress: card.documentScore,
          progressLabel: 'Score documental',
          sourceLabel: 'Funil local',
        })),
      };
    }

    if (slug === 'configuracoes' && runtimeQuery.data && areasQuery.data && toolsQuery.data) {
      const providerStates = Object.values(runtimeQuery.data.providers);
      return {
        ...emptyView,
        metrics: [
          ['Áreas ativas', String(areasQuery.data.filter((area) => area.isActive).length), `${areasQuery.data.length} registrada(s)`],
          ['Ferramentas', String(toolsQuery.data.length), `${toolsQuery.data.filter((tool) => tool.humanGate).length} com gate humano`],
          ['Provedores online', String(providerStates.filter((state) => state === 'online').length), `${providerStates.length} registrado(s)`],
          ['Ações externas', runtimeQuery.data.externalActionsEnabled ? 'Ativas' : 'Bloqueadas', `runtime ${runtimeQuery.data.mode}`],
        ],
        rows: settingsCatalog,
      };
    }

    return emptyView;
  }, [activityQuery.data, approvalsQuery.data, areasQuery.data, baseDefinition, financeQuery.data, funnelQuery.data, runtimeQuery.data, slug, toolsQuery.data]);

  const moduleIsPending = slug === 'financeiro'
    ? financeQuery.isPending
    : slug === 'aprovacoes'
      ? approvalsQuery.isPending
      : slug === 'atividade'
      ? isProcessView ? false : activityQuery.isPending
        : slug === 'contratos' || slug === 'peticoes'
          ? funnelQuery.isPending
          : slug === 'configuracoes'
            ? runtimeQuery.isPending || areasQuery.isPending || toolsQuery.isPending
            : false;
  const moduleIsError = slug === 'financeiro'
    ? financeQuery.isError
    : slug === 'aprovacoes'
      ? approvalsQuery.isError
      : slug === 'atividade'
      ? isProcessView ? false : activityQuery.isError
        : slug === 'contratos' || slug === 'peticoes'
          ? funnelQuery.isError
          : slug === 'configuracoes'
            ? runtimeQuery.isError || areasQuery.isError || toolsQuery.isError
            : false;
  const financeHasData = Boolean(financeQuery.data && (
    financeQuery.data.upcomingEntries.length
    || financeQuery.data.highlights.length
    || financeQuery.data.totalOpenReceivables
    || financeQuery.data.overdueAmount
    || financeQuery.data.scheduledAmount
    || financeQuery.data.paidAmount
    || financeQuery.data.pendingReminders
    || financeQuery.data.entriesDueSoon
  ));
  const moduleIsEmpty = !moduleIsPending && !moduleIsError && isSupportedModule && (
    slug === 'financeiro' ? !financeHasData : slug === 'configuracoes' ? false : definition.rows.length === 0
  );

  const personalizedFields = useMemo<PersonalizedFilterField<ModuleRow>[]>(() => [
    { key: 'title', label: 'Título', type: 'text' },
    { key: 'meta', label: 'Detalhes', type: 'text' },
    { key: 'status', label: 'Status', type: 'select', options: Array.from(new Set(definition.rows.map((row) => row.status))).sort().map((value) => ({ value, label: value })) },
    { key: 'sourceLabel', label: 'Fonte', type: 'select', options: Array.from(new Set(definition.rows.map((row) => row.sourceLabel).filter(Boolean))).sort().map((value) => ({ value, label: value })) },
  ], [definition.rows]);
  const rows = useMemo(() => definition.rows.filter((row) => {
    const matchesQuery = `${row.title} ${row.meta}`.toLowerCase().includes(query.toLowerCase());
    const matchesStatus = status === 'todos' || row.status === status;
    return matchesQuery && matchesStatus && matchesPersonalizedFilter(row, personalizedFilter, personalizedFields);
  }), [definition.rows, personalizedFields, personalizedFilter, query, status]);
  const statusOptions = useMemo(() => Array.from(new Set(definition.rows.map((row) => row.status))).sort(), [definition.rows]);

  const requestedMatterId = searchParams.get('matter');
  const requestedMeetingId = searchParams.get('meeting');
  const requestedAction = searchParams.get('action');
  const requestedRowId = requestedMeetingId ?? requestedMatterId;
  const requestedContextKey = requestedRowId ? `${slug}:${requestedRowId}:${requestedAction ?? 'detail'}` : null;
  const selectedBottleMatterId = slug === 'aprovacoes'
    ? approvalsQuery.data?.find((approval) => approval.id === selectedRow?.id)?.cardId ?? requestedMatterId ?? null
    : ['contratos', 'peticoes'].includes(slug)
      ? requestedMatterId ?? selectedRow?.id ?? null
      : null;
  const providerForModule: Record<string, string> = {
    contratos: 'zapsign',
    financeiro: 'asaas',
    peticoes: 'google_docs',
  };

  useEffect(() => {
    setQuery('');
    setStatus('todos');
    setPersonalizedFilter(EMPTY_PERSONALIZED_FILTER);
    setSelectedRow(null);
    setDecision(null);
    setDraftRecord(null);
    setSettingsView('overview');
    setHandledContextKey(null);
  }, [slug]);

  useEffect(() => {
    if (!requestedContextKey || handledContextKey === requestedContextKey) return;
    if (['contratos', 'peticoes'].includes(slug)) {
      setHandledContextKey(requestedContextKey);
      return;
    }
    const contextualRow = definition.rows.find((row) => row.id === requestedRowId);
    if (!contextualRow) return;
    setSelectedRow(contextualRow);
    setDecision(null);
    setHandledContextKey(requestedContextKey);
  }, [definition.rows, handledContextKey, requestedContextKey, requestedRowId, slug]);

  const draftMutation = useMutation({
    mutationFn: () => klmApi.createExternalIntent({
      matterId: selectedBottleMatterId,
      actionType: `${slug || 'operacao'}_draft_create`,
      provider: providerForModule[slug] ?? 'klm_local',
      idempotencyKey: `operational-draft:${slug}:${crypto.randomUUID()}`,
      payload: { module: slug, title: definition.title, externalActionsExecuted: 0 },
    }),
    onSuccess: (record) => {
      setDraftRecord(record);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
    },
    onError: () => setDecision('rascunho não registrado: o backend local recusou a ação'),
  });

  const decisionMutation = useMutation({
    mutationFn: async ({ status: nextStatus }: { status: 'approved' | 'rejected' }) => {
      if (!selectedRow) throw new Error('Item local indisponível');
      if (slug === 'aprovacoes' && selectedRow.id) {
        return klmApi.resolveApproval(
          selectedRow.id,
          nextStatus,
          nextStatus === 'approved' ? 'Aprovado pelo usuário no Meu Executivo local.' : 'Rejeitado pelo usuário no Meu Executivo local.',
        );
      }
      const intent = await klmApi.createExternalIntent({
        matterId: ['contratos', 'peticoes'].includes(slug) ? selectedRow.id ?? selectedBottleMatterId : selectedBottleMatterId,
        actionType: `${slug || 'operacao'}_row_decision`,
        provider: providerForModule[slug] ?? 'klm_local',
        idempotencyKey: `operational-decision:${slug}:${selectedRow.id ?? selectedRow.title}:${nextStatus}`,
        payload: {
          module: slug,
          rowId: selectedRow.id ?? null,
          title: selectedRow.title,
          status: selectedRow.status,
          decision: nextStatus,
          externalActionsExecuted: 0,
        },
      });
      return nextStatus === 'approved'
        ? klmApi.approveExternalIntentLocally(intent.id)
        : klmApi.rejectExternalIntentLocally(intent.id);
    },
    onSuccess: (_, variables) => {
      setDecision(variables.status === 'approved' ? 'aprovada' : 'rejeitada');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'approvals'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
    },
    onError: () => setDecision('não registrada: o backend local recusou a alteração'),
  });

  const refetchModuleData = () => {
    if (slug === 'financeiro') { void financeQuery.refetch(); void financeChargesQuery.refetch(); }
    if (slug === 'aprovacoes') void approvalsQuery.refetch();
    if (slug === 'atividade') void activityQuery.refetch();
    if (slug === 'contratos' || slug === 'peticoes') void funnelQuery.refetch();
    if (slug === 'configuracoes') {
      void runtimeQuery.refetch();
      void areasQuery.refetch();
      void toolsQuery.refetch();
    }
  };

  const currentTeamUser = (teamQuery.data ?? []).find((user) => user.id === state?.currentUser?.id) ?? null;
  const availabilityInitialValue = staffAvailabilityValueFromApi(
    availabilityStateQuery.data,
    effectiveAvailabilityQuery.data,
  );
  const pendingAvailabilityRequest = availabilityRequestsQuery.data?.[0] ?? null;
  const availabilityApproval: StaffAvailabilityApproval | null = pendingAvailabilityRequest ? {
    id: pendingAvailabilityRequest.id,
    status: pendingAvailabilityRequest.status === 'pending'
      ? 'pending'
      : pendingAvailabilityRequest.status === 'approved'
        ? 'approved'
        : 'rejected',
    requestedAt: pendingAvailabilityRequest.createdAt,
    requestedBy: pendingAvailabilityRequest.requestedByUserId === state?.currentUser?.id
      ? 'Solicitado por você'
      : 'Solicitado por outro usuário',
  } : null;
  const canDecidePendingAvailability = Boolean(
    pendingAvailabilityRequest
    && canApproveStaffAvailability
    && pendingAvailabilityRequest.requestedByUserId !== state?.currentUser?.id,
  );
  const availabilityRedirectOptions = [
    ...(teamQuery.data ?? [])
      .filter((user) => user.membershipStatus !== 'invited' && user.isActive && user.id !== availabilityTarget?.id)
      .map((user) => ({
        id: user.id,
        kind: 'user' as const,
        label: user.name,
        description: roleListLabel(user.roles),
      })),
    ...(availabilityTeamsQuery.data ?? [])
      .filter((team) => team.isActive)
      .map((team) => ({
        id: team.id,
        kind: 'team' as const,
        label: team.name,
        description: team.description ?? `${team.memberCount} membro(s)`,
      })),
  ];

  if (isTeamView) {
    const team = teamQuery.data ?? [];
    const activeUsers = team.filter((user) => user.isActive).length;
    const restrictedUsers = team.filter((user) => !user.canViewSensitiveLeadData).length;
    const admins = team.filter((user) => ['workspace_owner', 'workspace_admin'].includes(user.roles[0] ?? '')).length;
    const performanceRows = teamPerformanceQuery.data?.teamPerformance?.rows ?? [];
    const highestPerformanceScore = Math.max(1, ...performanceRows.map((item) => item.score));

    return (
      <div className="mx-auto max-w-[1220px] space-y-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            {!isDedicatedTeamView && <Button variant="ghost" size="sm" className="-ml-3 mb-2 h-7 text-2xs" onClick={() => setSettingsView('overview')}>
              <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />Voltar às configurações
            </Button>}
            <p className="text-2xs font-semibold uppercase text-[#0057d8]">{isDedicatedTeamView ? 'Operação / Equipe interna' : 'Sistema / Controle de acesso'}</p>
            <h2 className="mt-2 text-2xl font-semibold">{isDedicatedTeamView ? 'Gestão da equipe' : 'Usuários e permissões'}</h2>
            <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
              {isDedicatedTeamView
                ? 'Gerencie pessoas, horários, disponibilidade, produtividade e indicadores individuais do workspace.'
                : 'Crie acessos, atribua permissões por usuário e controle quem pode visualizar os dados sensíveis dos leads.'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {currentTeamUser ? (
              <Button variant="outline" className="h-9 text-2xs" onClick={() => setAvailabilityTarget(currentTeamUser)}>
                <CalendarClock className="mr-1.5 h-3.5 w-3.5" />Minha escala e ausência
              </Button>
            ) : null}
            <KlmPill tone="green">Proteção aplicada no servidor</KlmPill>
          </div>
        </div>

        {teamQuery.isPending || rolesQuery.isPending || accessPackagesQuery.isPending ? (
          <KlmPanel className="flex min-h-48 items-center justify-center gap-2 p-6 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando equipe e matriz de acesso...
          </KlmPanel>
        ) : teamQuery.isError || rolesQuery.isError || accessPackagesQuery.isError ? (
          <KlmPanel className="flex items-start gap-3 border-destructive/30 p-5">
            <ShieldOff className="mt-0.5 h-5 w-5 text-destructive" />
            <div className="flex-1">
              <h3 className="text-sm font-semibold">Não foi possível carregar a equipe</h3>
              <p className="mt-1 text-xs text-muted-foreground">Nenhuma contagem de usuário foi estimada. Confira o backend local e tente novamente.</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => { void teamQuery.refetch(); void rolesQuery.refetch(); void accessPackagesQuery.refetch(); }}>
              <RotateCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente
            </Button>
          </KlmPanel>
        ) : (
          <>
            {team.length > 0 ? (
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <KlmMetricCard label="Usuários" value={String(team.length)} delta="workspace atual" trend="neutral" />
                <KlmMetricCard label="Ativos" value={String(activeUsers)} delta="registros persistidos" trend="neutral" />
                <KlmMetricCard label="Dados ocultos" value={String(restrictedUsers)} delta="configuração por usuário" trend="neutral" />
                <KlmMetricCard label="Administradores" value={String(admins)} delta="papéis persistidos" trend="neutral" />
              </div>
            ) : (
              <KlmPanel className="flex items-start gap-3 p-5">
                <Users className="mt-0.5 h-5 w-5 text-muted-foreground" />
                <div>
                  <h3 className="text-sm font-semibold">Nenhum usuário retornado</h3>
                  <p className="mt-1 text-xs text-muted-foreground">A lista está vazia no workspace atual. Use a gestão abaixo para criar o primeiro acesso.</p>
                </div>
              </KlmPanel>
            )}

            {can('relatorios', 'view') && (
              <KlmPanel className="p-4 sm:p-5" data-testid="team-individual-performance">
                <KlmSectionTitle
                  title="Produtividade individual"
                  description="Indicadores dos últimos 30 dias, calculados com casos e entregas atribuídos a cada pessoa."
                  action={<div className="flex items-center gap-2"><KlmPill tone="blue">30 dias</KlmPill><Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => navigate('/app/kpis')}>Abrir KPIs</Button></div>}
                />
                {teamPerformanceQuery.isPending && <div className="mt-4 flex min-h-28 items-center justify-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Calculando produtividade...</div>}
                {teamPerformanceQuery.isError && <p className="mt-4 border border-destructive/30 p-4 text-xs text-muted-foreground">Não foi possível carregar os indicadores. Nenhuma pontuação estimada foi exibida.</p>}
                {teamPerformanceQuery.isSuccess && performanceRows.length > 0 && (
                  <div className="mt-4 divide-y border-y">
                    {performanceRows.map((row, index) => (
                      <div key={row.id} className="grid gap-3 py-3 sm:grid-cols-[36px_minmax(0,1fr)_repeat(3,100px)_80px] sm:items-center">
                        <span className="grid h-8 w-8 place-items-center bg-secondary text-2xs font-semibold">{index + 1}</span>
                        <div className="min-w-0"><strong className="block truncate text-xs">{row.name}</strong><span className="mt-1 block"><KlmProgress value={row.score / highestPerformanceScore * 100} tone={index === 0 ? 'green' : 'gold'} /></span></div>
                        <div><span className="block text-2xs text-muted-foreground">Casos</span><strong className="text-xs tabular-nums">{row.primaryCount}</strong></div>
                        <div><span className="block text-2xs text-muted-foreground">Concluídos</span><strong className="text-xs tabular-nums">{row.completedCount}</strong></div>
                        <div><span className="block text-2xs text-muted-foreground">Pendentes</span><strong className="text-xs tabular-nums">{row.pendingCount}</strong></div>
                        <div className="sm:text-right"><span className="flex items-center gap-1 text-2xs text-muted-foreground sm:justify-end"><Trophy className="h-3 w-3" />Score</span><strong className="text-xs tabular-nums">{row.score}</strong></div>
                      </div>
                    ))}
                  </div>
                )}
                {teamPerformanceQuery.isSuccess && !performanceRows.length && <p className="mt-4 border p-5 text-center text-xs text-muted-foreground">Os indicadores aparecerão quando houver casos ou entregas atribuídos às pessoas da equipe.</p>}
              </KlmPanel>
            )}

            <KlmPanel className="p-4 sm:p-5">
              <KlmSectionTitle
                title="Equipe do workspace"
                description="As alterações são persistidas pela API administrativa."
                action={<div className="flex items-center gap-2 text-2xs text-muted-foreground"><UserRoundCog className="h-3.5 w-3.5" />Permissões individuais</div>}
              />
              <div className="mt-4">
                <UserManagement
                  users={team}
                  roles={rolesQuery.data ?? []}
                  accessPackages={accessPackagesQuery.data ?? []}
                  onSave={(user) => saveTeamMemberMutation.mutateAsync(user)}
                  onResendInvitation={handleResendInvitation}
                  onRevokeInvitation={handleRevokeInvitation}
                  onGetInvitationLink={fetchInvitationLink}
                  onManageAvailability={canManageStaffAvailability ? setAvailabilityTarget : undefined}
                  onOpenProfile={setProfileTarget}
                />
                {teamActionFeedback && (
                  <p role="status" className="mt-3 text-2xs text-muted-foreground">
                    {teamActionFeedback}
                  </p>
                )}
              </div>
            </KlmPanel>
          </>
        )}

        <KlmPanel className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-4 w-4 text-emerald-700" />
            <div>
              <p className="text-xs font-semibold">Ocultação efetiva, não apenas visual</p>
              <p className="mt-1 text-2xs text-muted-foreground">Nome, CPF, telefone, e-mail e identificadores sociais são mascarados antes da resposta da API.</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-2xs text-muted-foreground"><Users className="h-3.5 w-3.5" />Escopo isolado por workspace</div>
        </KlmPanel>

        {availabilityTarget ? (
          <StaffAvailabilitySheet
            open
            onOpenChange={(open) => {
              if (!open) setAvailabilityTarget(null);
            }}
            staff={{
              id: availabilityTarget.id,
              name: availabilityTarget.name,
              role: roleListLabel(availabilityTarget.roles),
            }}
            initialValue={availabilityInitialValue}
            redirectOptions={availabilityRedirectOptions}
            approval={availabilityApproval}
            disabled={
              availabilityStateQuery.isError
              || effectiveAvailabilityQuery.isError
              || createAvailabilityRequestMutation.isPending
              || decideAvailabilityRequestMutation.isPending
            }
            onCreateRequest={async (input) => {
              await createAvailabilityRequestMutation.mutateAsync(input);
            }}
            onApprove={canDecidePendingAvailability ? async (input) => {
              await decideAvailabilityRequestMutation.mutateAsync({ ...input, decision: 'approve' });
            } : undefined}
            onReject={canDecidePendingAvailability ? async (input) => {
              await decideAvailabilityRequestMutation.mutateAsync({ ...input, decision: 'reject' });
            } : undefined}
          />
        ) : null}

        <Dialog open={Boolean(profileTarget)} onOpenChange={(open) => { if (!open) setProfileTarget(null); }}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>Indicadores de {profileTarget?.name}</DialogTitle>
              <DialogDescription>Produtividade individual dos últimos 30 dias, baseada somente em atribuições persistidas.</DialogDescription>
            </DialogHeader>
            {profileTarget && (() => {
              const row = performanceRows.find((item) => item.id === profileTarget.id || item.id.endsWith(`:${profileTarget.id}`));
              return (
                <div className="space-y-4">
                  <div className="flex items-center justify-between gap-4 border-y py-3">
                    <div><strong className="block text-sm">{profileTarget.name}</strong><span className="text-xs text-muted-foreground">{profileTarget.email} · {roleListLabel(profileTarget.roles)}</span></div>
                    <Badge variant={profileTarget.isActive ? 'default' : 'secondary'}>{profileTarget.isActive ? 'Ativo' : 'Suspenso'}</Badge>
                  </div>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <KlmMetricCard label="Casos" value={String(row?.primaryCount ?? 0)} delta="atribuídos" trend="neutral" />
                    <KlmMetricCard label="Concluídos" value={String(row?.completedCount ?? 0)} delta="30 dias" trend="neutral" />
                    <KlmMetricCard label="Pendentes" value={String(row?.pendingCount ?? 0)} delta="fila atual" trend="neutral" />
                    <KlmMetricCard label="Score" value={String(row?.score ?? 0)} delta="fórmula vigente" trend="neutral" />
                  </div>
                  {row ? <div className="border p-4"><div className="flex items-center justify-between text-xs"><span>Desempenho relativo da equipe</span><strong>{row.score} pontos</strong></div><div className="mt-3"><KlmProgress value={row.score / highestPerformanceScore * 100} tone="green" /></div></div> : <p className="border p-4 text-center text-xs text-muted-foreground">Ainda não há casos ou entregas atribuídos a esta pessoa no período.</p>}
                  <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => { setAvailabilityTarget(profileTarget); setProfileTarget(null); }}><CalendarClock className="mr-2 h-4 w-4" />Horários e disponibilidade</Button><Button onClick={() => navigate('/app/kpis')}>Abrir KPIs completos</Button></div>
                </div>
              );
            })()}
          </DialogContent>
        </Dialog>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">{definition.eyebrow}</p>
          <h2 className="mt-2 text-2xl font-semibold">{definition.title}</h2>
          <p className="mt-1 max-w-3xl text-xs text-muted-foreground">{definition.description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {slug === 'atividade' && <><Button variant={!isProcessView ? 'default' : 'outline'} className="h-9 text-xs" onClick={() => navigate('/app/atividade')}><Database className="mr-2 h-3.5 w-3.5" />Auditoria</Button><Button variant={isProcessView ? 'default' : 'outline'} className="h-9 text-xs" onClick={() => navigate('/app/atividade?view=processos')}><LockKeyhole className="mr-2 h-3.5 w-3.5" />Processos</Button></>}
          {slug === 'configuracoes' && <Button variant="outline" className="h-9 text-xs" onClick={() => navigate('/app/configuracoes/avancadas')}><UserRoundCog className="mr-2 h-3.5 w-3.5" />Central completa</Button>}
          {!isProcessView && <Button variant="outline" className="h-9 text-xs" disabled={moduleIsPending || moduleIsError || moduleIsEmpty} onClick={() => searchInputRef.current?.focus()}><Filter className="mr-2 h-3.5 w-3.5" />Filtrar fila</Button>}
          {!isProcessView && !['contratos', 'peticoes'].includes(slug) && <Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" disabled={!canManageIntegrations || draftMutation.isPending || !isSupportedModule} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Criar rascunho local'} onClick={() => draftMutation.mutate()}><Plus className="mr-2 h-3.5 w-3.5" />{draftMutation.isPending ? 'Registrando' : 'Novo rascunho local'}</Button>}
        </div>
      </div>

      {slug === 'financeiro' && (
        <>
          <FinanceUpgradePauseNotice showLaunchPaths />
          <Tabs defaultValue="visao-geral">
            <TabsList className="h-9 bg-secondary p-1">
              <TabsTrigger value="visao-geral" className="text-xs">Visão geral</TabsTrigger>
              <TabsTrigger value="conciliacao" className="text-xs">Conciliação</TabsTrigger>
              <TabsTrigger value="visao-financeira" className="text-xs">Visão Financeira</TabsTrigger>
              <TabsTrigger value="honorarios" className="text-xs">Honorários</TabsTrigger>
              <TabsTrigger value="indicacoes" className="text-xs">Indicações</TabsTrigger>
              <TabsTrigger value="cobranca" className="text-xs">Régua de cobrança</TabsTrigger>
              <TabsTrigger value="recorrencias" className="text-xs">Recorrências</TabsTrigger>
            </TabsList>

            <TabsContent value="visao-geral" className="mt-4 space-y-4">
              <NativeCRMOperationsPanel view="finance" />
              <FinanceAgingCard />
            </TabsContent>

            <TabsContent value="conciliacao" className="mt-4">
              <ReconciliationPanel canManage={canManageFinanceCharges} />
              <FinanceHarmonizationCard canManage={canManageFinanceCharges} />
            </TabsContent>

            <TabsContent value="visao-financeira" className="mt-4">
              <FinanceOverviewPanel />
            </TabsContent>

            <TabsContent value="honorarios" className="mt-4 space-y-6">
              <FeeArrangementsPanel canManage={canManageFinanceCharges} />
              <ContractBillingProposalsPanel canManage={canManageFinanceCharges} />
            </TabsContent>

            <TabsContent value="indicacoes" className="mt-4">
              <ReferralsPanel canManage={canManageFinanceCharges} />
            </TabsContent>

            <TabsContent value="cobranca" className="mt-4">
              <FinanceDunningCard canManage={canManageFinanceCharges} />
            </TabsContent>

            <TabsContent value="recorrencias" className="mt-4">
              <RecurrencesPanel canManage={canManageFinanceCharges} />
            </TabsContent>
          </Tabs>
        </>
      )}

      {['contratos', 'peticoes', 'aprovacoes'].includes(slug) && (
        <>
          <NativeModuleBar
            domain="crm"
            surface={definition.title}
            matterId={selectedBottleMatterId}
            capabilityIds={slug === 'contratos'
              ? ['products-estimates-quotes', 'tags-documents', 'opportunities-kanban']
              : slug === 'peticoes'
                ? ['cases-tickets', 'case-solutions-kb', 'tags-documents']
                : ['approvals-routing-escalation', 'ticket-approvals', 'audit-suspicious-activity']}
            returnTo={`/app/${slug}${selectedBottleMatterId ? `?matter=${selectedBottleMatterId}` : ''}`}
            onNavigate={embedded ? onNavigate : undefined}
          />
          <NativeCRMOperationsPanel view="matter" matterId={selectedBottleMatterId} />
        </>
      )}

      {slug === 'contratos' && <LegalArtifactsPanel title="Minutas de fechamento" kinds={['contract', 'power_of_attorney']} matterId={requestedMatterId} onNavigate={embedded ? onNavigate : undefined} />}
      {slug === 'peticoes' && <LegalArtifactsPanel title="Produção jurídica versionada" kinds={['petition']} matterId={requestedMatterId} onNavigate={embedded ? onNavigate : undefined} />}

      {['atividade', 'configuracoes'].includes(slug) && (
        <>
          <NativeModuleBar
            domain="crm"
            surface={definition.title}
            capabilityIds={slug === 'atividade'
              ? ['audit-suspicious-activity', 'notifications-sse', 'import-export']
              : ['users-rls-rbac', 'organization-settings', 'api-tokens-mcp']}
            returnTo={`/app/${slug}`}
            onNavigate={embedded ? onNavigate : undefined}
          />
          <NativeCRMOperationsPanel view="projection" />
        </>
      )}

      {slug === 'configuracoes' && <><OwnerAlertChannelPanel /><MessagingCadenceSettingsPanel /><KlmPanel className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5" data-testid="pricing-playbook-shortcut"><div className="flex min-w-0 items-start gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary"><BookOpenCheck className="h-4 w-4" /></span><div><h3 className="text-sm font-semibold">Política de honorários no Playbook</h3><p className="mt-1 max-w-2xl text-2xs leading-4 text-muted-foreground">A política comercial é preenchida no onboarding e governada no Playbook Studio, junto das propostas, objeções e regras dos agentes. Configurações não mantém uma cópia concorrente.</p></div></div><Button type="button" variant="outline" className="shrink-0" onClick={() => navigate('/app/playbooks')}><BookOpenCheck className="mr-2 h-4 w-4" />Abrir Playbook Studio</Button></KlmPanel></>}

      {draftRecord && (
        <KlmPanel className="flex items-center justify-between gap-4 border-[#1a9948]/40 bg-emerald-50 p-3 text-[#0b7435] dark:bg-emerald-950/25 dark:text-emerald-300">
          <div className="flex items-center gap-2 text-xs"><Check className="h-4 w-4" />Rascunho persistido com status {draftRecord.status}. Nenhum efeito externo foi produzido.</div>
          <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setDraftRecord(null)}>Fechar</Button>
        </KlmPanel>
      )}

      {(requestedMatterId || requestedMeetingId) && (
        <KlmPanel className="flex items-center justify-between gap-3 border-primary/35 bg-primary/10 p-3">
          <div><p className="text-2xs font-semibold">{requestedMeetingId ? 'Reunião solicitada por vínculo' : 'Caso solicitado por vínculo'}</p><p className="mt-1 text-2xs text-muted-foreground">Ação: {requestedMeetingId ? 'revisar agendamento' : requestedAction === 'procuracao' ? 'preparar procuração' : requestedAction === 'rascunho' ? 'preparar rascunho' : 'revisar contexto'} · identificador {(requestedMeetingId ?? requestedMatterId ?? '').slice(0, 8)}</p></div>
          <KlmPill tone="gold">Contexto da URL</KlmPill>
        </KlmPanel>
      )}

      {isProcessView ? (
        <ProcessMonitoringPanel matterId={requestedMatterId} canManageIntegrations={canManageIntegrations} onNavigate={embedded ? onNavigate : undefined} />
      ) : !isSupportedModule ? (
        <KlmPanel className="flex items-start gap-3 p-5">
          <CircleAlert className="mt-0.5 h-5 w-5 text-muted-foreground" />
          <div><h3 className="text-sm font-semibold">Módulo sem fonte configurada</h3><p className="mt-1 text-xs text-muted-foreground">Nenhum dado foi fabricado para esta rota.</p></div>
        </KlmPanel>
      ) : moduleIsPending ? (
        <KlmPanel className="flex min-h-52 items-center justify-center gap-2 p-6 text-xs text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />Carregando dados persistidos de {definition.title.toLowerCase()}...
        </KlmPanel>
      ) : moduleIsError ? (
        <KlmPanel className="flex flex-col gap-4 border-destructive/30 p-5 sm:flex-row sm:items-start">
          <CircleAlert className="h-5 w-5 shrink-0 text-destructive" />
          <div className="flex-1"><h3 className="text-sm font-semibold">Não foi possível carregar {definition.title.toLowerCase()}</h3><p className="mt-1 text-xs text-muted-foreground">Nenhuma fila ou métrica substituta foi exibida. Tente consultar novamente o backend local.</p></div>
          <Button variant="outline" size="sm" onClick={refetchModuleData}><RotateCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button>
        </KlmPanel>
      ) : moduleIsEmpty ? (
        <KlmPanel className="flex min-h-52 flex-col items-center justify-center p-8 text-center">
          <Database className="h-5 w-5 text-muted-foreground" />
          <h3 className="mt-3 text-sm font-semibold">Nenhum registro persistido nesta seção</h3>
          <p className="mt-1 max-w-md text-xs text-muted-foreground">A consulta foi concluída com sucesso e não retornou dados para {definition.title.toLowerCase()}.</p>
        </KlmPanel>
      ) : (
        <>
          {definition.metrics.length > 0 && (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {definition.metrics.map(([label, value, detail]) => <KlmMetricCard key={label} label={label} value={value} delta={detail} trend="neutral" />)}
            </div>
          )}

          <KlmPanel className="p-4">
            <KlmSectionTitle
              title={definition.queueTitle}
              description={slug === 'configuracoes' ? 'Selecione um domínio para gerenciar suas configurações.' : 'Somente registros retornados pela fonte persistida desta seção.'}
              action={<KlmPill tone={slug === 'configuracoes' ? 'gold' : 'green'}>{slug === 'configuracoes' ? 'Catálogo' : 'Registrado'}</KlmPill>}
            />
            <div className="mt-4 flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input ref={searchInputRef} className="h-9 bg-secondary pl-9 text-xs" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar nesta fila" />
              </div>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger aria-label="Filtrar registros por status" className="h-9 w-full text-xs sm:w-48"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="todos">Todos os status</SelectItem>
                  {statusOptions.map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}
                </SelectContent>
              </Select>
              <PersonalizedFilterBuilder scope={`operational-${slug}`} fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} className="h-9" />
            </div>
            <div className="mt-4 overflow-hidden rounded-lg border border-border">
              {rows.length === 0 ? (
                <div className="p-10 text-center text-xs text-muted-foreground">Nenhum registro persistido corresponde aos filtros atuais.</div>
              ) : rows.map((row) => {
                const financeEntry: KlmFinanceEntry | undefined = slug === 'financeiro'
                  ? financeQuery.data?.upcomingEntries.find((entry) => entry.id === row.id)
                  : undefined;
                return (
                <div key={row.id ?? row.title} className="border-b last:border-0">
                  <button
                    onClick={() => {
                      if (['contratos', 'peticoes'].includes(slug) && row.id) {
                        navigate(`/app/${slug}?matter=${encodeURIComponent(row.id)}`);
                        return;
                      }
                      if (slug === 'configuracoes' && row.title === 'Usuários e permissões') {
                        setSettingsView('team');
                        return;
                      }
                      if (slug === 'configuracoes' && row.title === 'Módulos e ferramentas') {
                        navigate('/app/ferramentas');
                        return;
                      }
                      if (slug === 'configuracoes' && row.title === 'Áudio IA') {
                        navigate('/app/audio');
                        return;
                      }
                      if (slug === 'configuracoes' && settingsTabs[row.title]) {
                        navigate(`/app/configuracoes/avancadas?tab=${settingsTabs[row.title]}`);
                        return;
                      }
                      setSelectedRow(row);
                      setDecision(null);
                    }}
                    className="grid w-full grid-cols-[1fr_auto] items-center gap-4 p-4 text-left hover:bg-secondary sm:grid-cols-[1.5fr_1fr_120px_24px]"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-semibold">{row.title}</span>
                      <span className="mt-1 block truncate text-2xs text-muted-foreground">{row.meta}</span>
                    </span>
                    <span className="hidden sm:block">
                      {typeof row.progress === 'number' ? (
                        <span className="block">
                          <span className="mb-1.5 block text-2xs text-muted-foreground">{row.progressLabel}</span>
                          <KlmProgress value={row.progress} tone="gold" />
                        </span>
                      ) : null}
                    </span>
                    <KlmPill tone={statusTone(row.status)}>{row.status}</KlmPill>
                    <ChevronRight className="hidden h-4 w-4 text-muted-foreground sm:block" />
                  </button>
                  {financeEntry && (
                    <FinanceEntryChargeRow
                      entry={financeEntry}
                      charge={financeChargesByEntryId.get(financeEntry.id)}
                      canManage={canManageFinanceCharges}
                    />
                  )}
                </div>
                );
              })}
            </div>
          </KlmPanel>

          {definition.highlights.length > 0 && (
            <div className="grid gap-4 lg:grid-cols-3">
              {definition.highlights.slice(0, 3).map((copy, index) => (
                <KlmPanel key={`${copy}-${index}`} className="p-4">
                  <div className="flex items-center gap-2"><Database className="h-4 w-4 text-[#0057d8]" /><h3 className="text-sm font-medium">Destaque persistido</h3></div>
                  <p className="mt-3 text-2xs leading-4 text-muted-foreground">{copy}</p>
                </KlmPanel>
              ))}
            </div>
          )}
        </>
      )}

      <Dialog open={Boolean(selectedRow)} onOpenChange={(open) => !open && setSelectedRow(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>{selectedRow?.title}</DialogTitle><DialogDescription>{selectedRow?.meta}</DialogDescription></DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border bg-secondary p-3"><span className="text-2xs text-muted-foreground">Status persistido</span><div className="mt-2">{selectedRow ? <KlmPill tone={statusTone(selectedRow.status)}>{selectedRow.status}</KlmPill> : null}</div></div>
            <div className="rounded-lg border bg-secondary p-3">
              <span className="text-2xs text-muted-foreground">{selectedRow?.progressLabel ?? 'Identificador'}</span>
              <strong className="mt-2 block break-all text-xs">{typeof selectedRow?.progress === 'number' ? `${selectedRow.progress}%` : selectedRow?.id ?? 'Item do catálogo'}</strong>
            </div>
          </div>
          <div className="flex items-start gap-3 rounded-lg border p-3">
            <Database className="mt-0.5 h-4 w-4 shrink-0 text-[#0057d8]" />
            <div><p className="text-2xs font-semibold">Origem</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{selectedRow?.sourceLabel}</p></div>
          </div>
          {decision && <div className="rounded-lg border border-emerald-600/30 bg-emerald-50 p-3 text-2xs text-emerald-800 dark:bg-emerald-950/25 dark:text-emerald-300">Decisão: {decision}.</div>}
          <DialogFooter><Button variant="outline" disabled={!canResolveCurrentRow || decisionMutation.isPending} onClick={() => decisionMutation.mutate({ status: 'rejected' })}>Rejeitar</Button><Button disabled={!canResolveCurrentRow || decisionMutation.isPending} onClick={() => decisionMutation.mutate({ status: 'approved' })} className="bg-[#031121] text-white hover:bg-[#031121]"><LockKeyhole className="mr-2 h-3.5 w-3.5" />{decisionMutation.isPending ? 'Registrando' : 'Aprovar localmente'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
