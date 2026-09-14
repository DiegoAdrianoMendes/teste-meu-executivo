import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { DragDropContext, Draggable, Droppable, type DropResult } from '@hello-pangea/dnd';
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  CalendarPlus,
  CheckSquare2,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  ExternalLink,
  FileSearch,
  FileSignature,
  Filter,
  GripVertical,
  KanbanSquare,
  List,
  Loader2,
  MessageCircle,
  MessageSquareText,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  ShieldAlert,
  Sparkles,
  Tags,
  UserRoundCheck,
  Workflow,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from '@/components/ui/context-menu';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { NativeCRMOperationsPanel } from '@/klm/components/NativeCRMOperationsPanel';
import { FunnelCardWorkspaceSheet } from '@/klm/components/FunnelCardWorkspaceSheet';
import { CrmViewNav } from '@/klm/components/CrmViewNav';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { WinCelebration } from '@/klm/components/WinCelebration';
import { CreateFeeArrangementDialog } from '@/klm/components/CreateFeeArrangementDialog';
import { reorderFunnelCards } from '@/klm/lib/funnel-dnd';
import { useFunnelFinanceSummary } from '@/klm/api/useFunnelFinanceSummary';
import { cn } from '@/lib/utils';
import { klmApi, type KlmFunnel, type KlmFunnelMoveResult } from '@/klm/api';
import { ApiError } from '@/services/http';
import { useAppStore } from '@/store/app-context';
import { formatBRL } from '@/lib/formatters';
import { META_PHONE_HELP, normalizeWhatsAppPhone } from '@/lib/whatsapp-phone';
import { PermissionGate } from '@/klm/components/PermissionGate';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import {
  type ContextualPageProps,
  useContextualNavigate,
  useContextualSearchParams,
} from '@/klm/hooks/useContextualRouting';

function useFunnelCompact() {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1279px)');
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return compact;
}

export type FunnelCard = {
  id: string;
  name: string;
  subject: string;
  object: string;
  source: string;
  owner: string;
  probability: number;
  value: number;
  sla: string;
  docs: number;
  documentScore: number;
  productionScore: number;
  stage: string;
  order: number;
  threadId: string | null;
  threadStatus: string | null;
  channel: string | null;
  unreadMessages: number;
  lastMessagePreview: string | null;
  lastMessageAt: string | null;
  nextAction: string | null;
  nextDeadline: string | null;
  priority: string;
  urgency: string;
  classification: string;
  tags: string[];
  openTasks: number;
  isBlocked: boolean;
  needsApproval: boolean;
  projectedRevenue: number;
  upcomingMeetingAt: string | null;
  meetingUrl: string | null;
  activeFlow: null | {
    runId: string;
    flowId: string;
    name: string;
    status: string;
    progress: number;
    completedSteps: number;
    totalSteps: number;
    currentStep: string | null;
    errorStatus: string | null;
  };
  updatedAt: string | null;
};

const stageColors = [
  'bg-blue-500',
  'bg-primary',
  'bg-amber-500',
  'bg-violet-500',
  'bg-orange-500',
  'bg-cyan-600',
  'bg-fuchsia-600',
  'bg-emerald-600',
  'bg-red-600',
] as const;

function formatDateTime(value: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function cardAgeDays(value: string | null): number {
  if (!value) return 0;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? Math.max(0, Math.floor((Date.now() - timestamp) / 86_400_000)) : 0;
}

/** Etapa de ganho/assinado do funil comercial (ver oss_native_experience_service.py). */
const WON_STAGE_ID = 'ganho';
const WON_CELEBRATION_STORAGE_KEY = 'klm-funnel-won-celebrated';

/** Etapas em que o card reage ao estado financeiro do caso (Frente C). */
const BILLING_STAGES = new Set(['contrato', 'pagamento']);

function DragPortal({ active, children }: { active: boolean; children: ReactNode }) {
  if (!active || typeof document === 'undefined') return children;
  return createPortal(children, document.body);
}

const FINANCE_BADGE_LABEL: Record<'cobrando' | 'vencido' | 'pago', string> = {
  cobrando: 'Cobrando',
  vencido: 'Vencido',
  pago: 'Pago',
};

function readCelebratedCaseIds(): Set<string> {
  try {
    const raw = window.localStorage.getItem(WON_CELEBRATION_STORAGE_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function markCaseCelebrated(id: string): void {
  try {
    const next = readCelebratedCaseIds();
    next.add(id);
    window.localStorage.setItem(WON_CELEBRATION_STORAGE_KEY, JSON.stringify([...next]));
  } catch {
    // localStorage indisponível: a celebração pode reaparecer na próxima sessão, sem risco.
  }
}

type MoveVariables = {
  id: string;
  stage: string;
  position: number;
  sourceStage: string;
  beforeId?: string;
  afterId?: string;
  expectedBoardVersion: number;
  previousCards: FunnelCard[];
};

type CardAutomationState = Pick<KlmFunnelMoveResult, 'automationStatus' | 'agentsTriggered' | 'automationMessage' | 'approvalId'> & {
  pending?: boolean;
};

export default function FunnelPage({ embedded = false, onNavigate, contextPath }: ContextualPageProps) {
  const navigate = useContextualNavigate({ embedded, onNavigate });
  const [searchParams, setSearchParams] = useContextualSearchParams({ embedded, contextPath });
  const requestedView = searchParams.get('view') === 'tabela' ? 'tabela' : 'kanban';
  const { can } = useAppStore();
  const isCompact = useFunnelCompact();
  const canEdit = can('kanban', 'edit');
  const canViewFinance = can('finance', 'view');
  const canManageFinance = can('finance', 'manage');
  const queryClient = useQueryClient();
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const { summaryByMatterId: financeSummaryByMatterId } = useFunnelFinanceSummary(canViewFinance);
  const [arrangementDialogCard, setArrangementDialogCard] = useState<FunnelCard | null>(null);
  const requestedStage = searchParams.get('stage')?.trim() ?? '';
  const stages = useMemo(() => (funnelQuery.data?.stages ?? []).map((stage, index) => ({
    id: stage.id,
    label: stage.label,
    color: stageColors[index % stageColors.length],
  })), [funnelQuery.data?.stages]);
  const stageFilter = requestedStage && stages.some((stage) => stage.id === requestedStage) ? requestedStage : 'todos';
  const [cards, setCards] = useState<FunnelCard[]>([]);
  const [query, setQuery] = useState('');
  const [source, setSource] = useState('todas');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [selectedId, setSelectedId] = useState(() => searchParams.get('matter') ?? '');
  const [notice, setNotice] = useState('Arraste os cards entre etapas ou use espaço e setas pelo teclado.');
  const [taskMatterId, setTaskMatterId] = useState<string | null>(null);
  const [taskTitle, setTaskTitle] = useState('');
  const [taskPriority, setTaskPriority] = useState<'baixa' | 'media' | 'alta'>('media');
  const [celebratedLabel, setCelebratedLabel] = useState<string | null>(null);
  const [cardAutomation, setCardAutomation] = useState<Record<string, CardAutomationState>>({});
  const [workspaceCardId, setWorkspaceCardId] = useState<string | null>(null);
  const [columnSort, setColumnSort] = useState<'manual' | 'prazo' | 'prioridade' | 'valor' | 'atividade'>('manual');
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedCardIds, setSelectedCardIds] = useState<Set<string>>(new Set());
  const [bulkStage, setBulkStage] = useState('');
  const [bulkPriority, setBulkPriority] = useState('');
  const [bulkTag, setBulkTag] = useState('');
  const [wipLimit, setWipLimit] = useState<'0' | '3' | '5' | '10'>('5');
  const [quickLeadStage, setQuickLeadStage] = useState<string | null>(null);
  const [quickLeadName, setQuickLeadName] = useState('');
  const [quickLeadPhone, setQuickLeadPhone] = useState('');
  const normalizedQuickLeadPhone = quickLeadPhone.trim()
    ? normalizeWhatsAppPhone(quickLeadPhone)
    : null;
  const [quickLeadDemand, setQuickLeadDemand] = useState('Novo atendimento');
  const [quickLeadSource, setQuickLeadSource] = useState('manual');
  const sourceOptions = useMemo(() => [...new Set(cards.map((card) => card.source).filter(Boolean))].sort((left, right) => left.localeCompare(right, 'pt-BR')), [cards]);
  const personalizedFields = useMemo<PersonalizedFilterField<FunnelCard>[]>(() => [
    { key: 'name', label: 'Lead', type: 'text' },
    { key: 'subject', label: 'Subtema', type: 'text' },
    { key: 'object', label: 'Objeto do caso', type: 'text' },
    { key: 'source', label: 'Origem', type: 'select', options: sourceOptions.map((value) => ({ value, label: value })) },
    { key: 'owner', label: 'Responsável', type: 'select', options: [...new Set(cards.map((card) => card.owner).filter(Boolean))].map((value) => ({ value, label: value })) },
    { key: 'stage', label: 'Etapa', type: 'select', options: stages.map((stage) => ({ value: stage.id, label: stage.label })) },
    { key: 'probability', label: 'Probabilidade (%)', type: 'number' },
    { key: 'value', label: 'Honorários iniciais', type: 'number' },
    { key: 'docs', label: 'Documentos', type: 'number' },
    { key: 'sla', label: 'Prazo de resposta', type: 'text' },
    { key: 'priority', label: 'Prioridade', type: 'select', options: [...new Set(cards.map((card) => card.priority).filter(Boolean))].map((value) => ({ value, label: value })) },
    { key: 'urgency', label: 'Urgência', type: 'select', options: [...new Set(cards.map((card) => card.urgency).filter(Boolean))].map((value) => ({ value, label: value })) },
    { key: 'channel', label: 'Canal', type: 'select', options: [...new Set(cards.map((card) => card.channel).filter((value): value is string => Boolean(value)))].map((value) => ({ value, label: value })) },
    { key: 'unreadMessages', label: 'Não lidas', type: 'number' },
    { key: 'openTasks', label: 'Tarefas abertas', type: 'number' },
    { key: 'tags', label: 'Tags', type: 'text', getValue: (card) => card.tags.join(' ') },
    { key: 'nextAction', label: 'Próxima ação', type: 'text' },
    { key: 'flowProgress', label: 'Progresso do flow', type: 'number', getValue: (card) => card.activeFlow?.progress ?? 0 },
  ], [cards, sourceOptions, stages]);
  const bottleneck = useMemo(() => stages
    .map((stage) => ({ ...stage, count: cards.filter((card) => card.stage === stage.id).length }))
    .sort((a, b) => b.count - a.count)[0], [cards, stages]);
  const filtered = useMemo(() => cards.filter((card) => {
    const matchesSearch = `${card.name} ${card.subject} ${card.object} ${card.owner}`.toLowerCase().includes(query.toLowerCase());
    const matchesSource = source === 'todas' || card.source === source;
    const matchesStage = stageFilter === 'todos' || card.stage === stageFilter;
    return matchesSearch && matchesSource && matchesStage
      && matchesPersonalizedFilter(card, personalizedFilter, personalizedFields);
  }), [cards, personalizedFields, personalizedFilter, query, source, stageFilter]);
  const selected = cards.find((card) => card.id === selectedId && filtered.some((item) => item.id === card.id)) ?? filtered[0] ?? null;
  const visibleStages = stageFilter === 'todos' ? stages : stages.filter((stage) => stage.id === stageFilter);
  const workspaceCard = cards.find((card) => card.id === workspaceCardId) ?? null;
  const sortStageCards = (items: FunnelCard[]) => [...items].sort((left, right) => {
    if (columnSort === 'prazo') return String(left.nextDeadline || '9999').localeCompare(String(right.nextDeadline || '9999'));
    if (columnSort === 'prioridade') return ({ urgente: 0, alta: 1, normal: 2, baixa: 3 }[left.priority] ?? 2) - ({ urgente: 0, alta: 1, normal: 2, baixa: 3 }[right.priority] ?? 2);
    if (columnSort === 'valor') return right.projectedRevenue - left.projectedRevenue;
    if (columnSort === 'atividade') return String(right.updatedAt || '').localeCompare(String(left.updatedAt || ''));
    return left.order - right.order;
  });

  const setStageFilter = (stage: string) => {
    const nextParams = new URLSearchParams(searchParams);
    if (stage === 'todos') nextParams.delete('stage');
    else nextParams.set('stage', stage);
    setSearchParams(nextParams);
  };

  const reviewBottleneck = () => {
    if (!bottleneck || bottleneck.count === 0) {
      setNotice('Não há concentração suficiente de cards para apontar um gargalo agora.');
      return;
    }
    setStageFilter(bottleneck.id);
    setNotice(`Gargalo atual: ${bottleneck.label} concentra ${bottleneck.count} lead${bottleneck.count === 1 ? '' : 's'}. O quadro foi filtrado para revisão.`);
  };

  useEffect(() => {
    if (!funnelQuery.data) return;
    const persistedCards = funnelQuery.data.cards.map((card, index) => ({
      id: card.id,
      name: card.name,
      subject: card.subject.replaceAll('_', ' '),
      object: card.object,
      source: card.source,
      owner: card.owner,
      probability: card.probability,
      value: card.initialFee,
      sla: card.slaDueAt ? new Date(card.slaDueAt).toLocaleString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : 'sem prazo',
      docs: card.documents,
      documentScore: card.documentScore,
      productionScore: card.productionScore ?? 0,
      stage: card.stage,
      order: card.cardOrder ?? index,
      threadId: card.threadId ?? null,
      threadStatus: card.threadStatus ?? null,
      channel: card.channel ?? null,
      unreadMessages: card.unreadMessages ?? 0,
      lastMessagePreview: card.lastMessagePreview ?? null,
      lastMessageAt: card.lastMessageAt ?? null,
      nextAction: card.nextAction ?? null,
      nextDeadline: card.nextDeadline ?? null,
      priority: card.priority ?? 'normal',
      urgency: card.urgency ?? 'normal',
      classification: card.classification ?? 'não classificado',
      tags: card.tags ?? [],
      openTasks: card.openTasks ?? 0,
      isBlocked: card.isBlocked ?? false,
      needsApproval: card.needsApproval ?? false,
      projectedRevenue: card.projectedRevenue ?? 0,
      upcomingMeetingAt: card.upcomingMeetingAt ?? null,
      meetingUrl: card.meetingUrl ?? null,
      activeFlow: card.activeFlow ?? null,
      updatedAt: card.updatedAt ?? null,
    })).sort((a, b) => {
      const stageDelta = stages.findIndex((stage) => stage.id === a.stage) - stages.findIndex((stage) => stage.id === b.stage);
      return stageDelta || a.order - b.order;
    });
    setCards(persistedCards);
    setSelectedId((current) => {
      const currentCard = persistedCards.find((card) => card.id === current);
      if (currentCard && (!requestedStage || currentCard.stage === requestedStage)) return current;
      return persistedCards.find((card) => !requestedStage || card.stage === requestedStage)?.id ?? persistedCards[0]?.id ?? '';
    });
  }, [funnelQuery.data, requestedStage, stages]);

  useEffect(() => {
    if (source !== 'todas' && !sourceOptions.includes(source)) setSource('todas');
  }, [source, sourceOptions]);

  useEffect(() => {
    if (!selected?.id || searchParams.get('matter') === selected.id) return;
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('matter', selected.id);
    setSearchParams(nextParams);
  }, [searchParams, selected?.id, setSearchParams]);

  useEffect(() => {
    const refreshFromRealtime = (event: Event) => {
      const detail = (event as CustomEvent<{ event?: string }>).detail;
      if (detail?.event === 'CARD_MOVED') {
        void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      }
    };
    window.addEventListener('klm:realtime', refreshFromRealtime);
    return () => window.removeEventListener('klm:realtime', refreshFromRealtime);
  }, [queryClient]);

  const moveMutation = useMutation({
    mutationFn: ({ id, stage, position, sourceStage, beforeId, afterId, expectedBoardVersion }: MoveVariables) => klmApi.moveFunnel(id, stage, {
      position,
      sourceColumnId: sourceStage,
      beforeId,
      afterId,
      expectedBoardVersion,
    }),
    onMutate: (variables) => {
      setCardAutomation((current) => ({ ...current, [variables.id]: { pending: true, agentsTriggered: ['crm'] } }));
    },
    onSuccess: (result) => {
      setCardAutomation((current) => ({
        ...current,
        [result.matterId]: {
          pending: false,
          automationStatus: result.automationStatus ?? (result.agentDispatched ? 'completed' : 'not_started'),
          agentsTriggered: result.agentsTriggered ?? [],
          automationMessage: result.automationMessage,
          approvalId: result.approvalId,
        },
      }));
      setNotice(result.automationMessage || `Movimento salvo: ${stages.find((stage) => stage.id === result.columnId)?.label ?? result.columnId}, posição ${(result.cardOrder ?? 0) + 1}.`);
      queryClient.setQueryData<KlmFunnel>(['klm', 'funnel'], (current) => current ? { ...current, boardVersion: result.boardVersion } : current);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
    },
    onError: (error, variables) => {
      setCards(variables.previousCards);
      setCardAutomation((current) => { const next = { ...current }; delete next[variables.id]; return next; });
      if (error instanceof ApiError && error.status === 409) {
        setNotice('Outro operador atualizou o funil. A versão mais recente está sendo carregada; repita o movimento nela.');
        void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
        return;
      }
      setNotice(`Não foi possível salvar o movimento. A posição anterior foi restaurada. ${error instanceof Error ? error.message : ''}`.trim());
    },
  });

  const taskMutation = useMutation({
    mutationFn: ({ matterId, title, priority }: { matterId: string; title: string; priority: 'baixa' | 'media' | 'alta' }) => klmApi.createMatterTask(matterId, {
      title,
      priority,
      idempotencyKey: `funnel-task:${crypto.randomUUID()}`,
    }),
    onSuccess: (_, variables) => {
      setNotice(`Tarefa “${variables.title}” criada e vinculada ao card.`);
      setTaskMatterId(null);
      setTaskTitle('');
      setTaskPriority('media');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', variables.matterId] });
    },
    onError: (error) => setNotice(`Não foi possível criar a tarefa. ${error instanceof Error ? error.message : ''}`.trim()),
  });

  const bulkMoveMutation = useMutation({
    mutationFn: async (targetStage: string) => {
      let boardVersion = funnelQuery.data?.boardVersion ?? 0;
      let position = cards.filter((card) => card.stage === targetStage && !selectedCardIds.has(card.id)).length;
      const moved: KlmFunnelMoveResult[] = [];
      for (const id of selectedCardIds) {
        const card = cards.find((item) => item.id === id);
        if (!card || card.stage === targetStage) continue;
        const result = await klmApi.moveFunnel(id, targetStage, {
          position,
          sourceColumnId: card.stage,
          expectedBoardVersion: boardVersion,
          operationId: `bulk:${crypto.randomUUID()}`,
        });
        moved.push(result);
        boardVersion = result.boardVersion;
        position += 1;
      }
      return moved;
    },
    onSuccess: async (results) => {
      setNotice(`${results.length} card${results.length === 1 ? '' : 's'} movido${results.length === 1 ? '' : 's'}; o CRM iniciou a orquestração de cada transição.`);
      setSelectedCardIds(new Set());
      setSelectionMode(false);
      setBulkStage('');
      await queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
    },
    onError: (error) => {
      setNotice(`A movimentação em lote foi interrompida. Atualize o quadro antes de repetir. ${error instanceof Error ? error.message : ''}`.trim());
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
    },
  });

  const quickLeadMutation = useMutation({
    mutationFn: async () => {
      const client = await klmApi.createClient({
        name: quickLeadName.trim(),
        phone: quickLeadPhone.trim() ? normalizedQuickLeadPhone : null,
      });
      return klmApi.createMatter({ clientId: client.id, demandType: quickLeadDemand.trim(), leadSource: quickLeadSource.trim() || 'manual', columnId: quickLeadStage! });
    },
    onSuccess: async (matter) => {
      setNotice(`Lead ${quickLeadName.trim()} criado em ${stages.find((stage) => stage.id === quickLeadStage)?.label ?? 'etapa selecionada'}.`);
      setQuickLeadStage(null); setQuickLeadName(''); setQuickLeadPhone(''); setQuickLeadDemand('Novo atendimento'); setQuickLeadSource('manual');
      setSelectedId(matter.id);
      await queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
    },
    onError: (error) => setNotice(`Não foi possível criar o lead. ${error instanceof Error ? error.message : ''}`.trim()),
  });

  const bulkEditMutation = useMutation({
    mutationFn: async () => Promise.all([...selectedCardIds].map((id) => {
      const card = cards.find((item) => item.id === id);
      return klmApi.updateMatter(id, {
        ...(bulkPriority ? { priority: bulkPriority } : {}),
        ...(bulkTag.trim() ? { tags: [...new Set([...(card?.tags ?? []), bulkTag.trim()])] } : {}),
      });
    })),
    onSuccess: async () => {
      setNotice(`${selectedCardIds.size} card${selectedCardIds.size === 1 ? '' : 's'} atualizado${selectedCardIds.size === 1 ? '' : 's'} em lote.`);
      setBulkPriority(''); setBulkTag(''); setSelectedCardIds(new Set()); setSelectionMode(false);
      await queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
    },
    onError: (error) => setNotice(`Não foi possível concluir a edição em lote. ${error instanceof Error ? error.message : ''}`.trim()),
  });

  const openCardPath = (card: FunnelCard, path: string) => {
    const params = new URLSearchParams();
    params.set('matter', card.id);
    if (card.threadId) params.set('thread', card.threadId);
    navigate(`${path}?${params.toString()}`);
  };

  const openTaskDialog = (card: FunnelCard) => {
    setSelectedId(card.id);
    setTaskMatterId(card.id);
    setTaskTitle(card.nextAction || `Próxima ação · ${card.name}`);
  };

  const openMatterDocuments = (card: FunnelCard) => {
    const params = new URLSearchParams({ matter: card.id, panel: 'rag' });
    if (card.threadId) params.set('thread', card.threadId);
    navigate(`/app/leads?${params.toString()}`);
  };

  const openMeeting = (meetingUrl: string | null) => {
    if (!meetingUrl) return;
    window.open(meetingUrl, '_blank', 'noopener,noreferrer');
  };

  const commitMove = (id: string, targetStage: string, nextCards: FunnelCard[], previousCards: FunnelCard[]) => {
    setCards(nextCards);
    setSelectedId(id);
    const position = nextCards.filter((card) => card.stage === targetStage).findIndex((card) => card.id === id);
    const movedCard = previousCards.find((card) => card.id === id);
    const sourceStage = movedCard?.stage ?? targetStage;
    if (targetStage === WON_STAGE_ID && sourceStage !== WON_STAGE_ID) {
      const celebrated = readCelebratedCaseIds();
      if (!celebrated.has(id)) {
        markCaseCelebrated(id);
        setCelebratedLabel(movedCard?.name ?? 'Caso movido para ganho');
      }
    }
    const targetCards = nextCards.filter((card) => card.stage === targetStage);
    moveMutation.mutate({
      id,
      stage: targetStage,
      position: Math.max(position, 0),
      sourceStage,
      beforeId: targetCards[position + 1]?.id,
      afterId: targetCards[position - 1]?.id,
      expectedBoardVersion: funnelQuery.data?.boardVersion ?? 0,
      previousCards,
    });
  };

  const handleDragEnd = (result: DropResult) => {
    if (!funnelQuery.isSuccess) {
      setNotice('O funil local precisa estar carregado antes de mover um card.');
      return;
    }
    if (!canEdit) {
      setNotice('Seu perfil pode consultar o funil, mas não mover leads.');
      return;
    }
    if (!result.destination) {
      setNotice('Movimento cancelado; o card permaneceu na etapa original.');
      return;
    }
    const { draggableId, source: origin, destination } = result;
    if (origin.droppableId === destination.droppableId && origin.index === destination.index) return;
    const previousCards = cards;
    const visibleCardIds = filtered.filter((card) => card.stage === destination.droppableId).map((card) => card.id);
    const nextCards = reorderFunnelCards(cards, draggableId, {
      stage: destination.droppableId,
      index: destination.index,
      visibleCardIds,
    });
    commitMove(draggableId, destination.droppableId, nextCards, previousCards);
  };

  const moveCardToStage = (id: string, targetStage: string) => {
    const card = cards.find((item) => item.id === id);
    if (!card || card.stage === targetStage) return;
    const visibleCardIds = cards.filter((item) => item.stage === targetStage).map((item) => item.id);
    const nextCards = reorderFunnelCards(cards, id, {
      stage: targetStage,
      index: visibleCardIds.length,
      visibleCardIds,
    });
    commitMove(id, targetStage, nextCards, cards);
  };

  const moveCard = (id: string, direction: -1 | 1) => {
    const card = cards.find((item) => item.id === id);
    if (!card) return;
    const currentIndex = stages.findIndex((stage) => stage.id === card.stage);
    if (currentIndex < 0) return;
    const targetStage = stages[Math.max(0, Math.min(stages.length - 1, currentIndex + direction))].id;
    moveCardToStage(id, targetStage);
  };

  return (
    <div className="mx-auto max-w-[1420px] space-y-5">
      <WinCelebration label={celebratedLabel} onDone={() => setCelebratedLabel(null)} />
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Comercial / Pipeline</p>
          <h2 className="mt-2 text-2xl font-semibold">Funil de vendas</h2>
          <p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Pipeline conforme as etapas retornadas pelo CRM local, com responsável, prazo de resposta, documentos e probabilidade.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select value={stageFilter} onValueChange={setStageFilter}><SelectTrigger className="h-9 w-[190px] text-xs" aria-label="Filtrar por etapa"><Filter className="mr-2 h-3.5 w-3.5" /><SelectValue /></SelectTrigger><SelectContent><SelectItem value="todos">Todas as etapas</SelectItem>{stages.map((stage) => <SelectItem key={stage.id} value={stage.id}>{stage.label}</SelectItem>)}</SelectContent></Select>
          <PersonalizedFilterBuilder scope="sales-funnel" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} className="h-9" />
          <Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" onClick={reviewBottleneck}><Sparkles className="mr-2 h-3.5 w-3.5" />Revisar gargalos</Button>
        </div>
      </header>

      <CrmViewNav active={requestedView === 'tabela' ? 'list' : 'kanban'} matterId={selected?.id} />

      <NativeCRMOperationsPanel view="funnel" />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        <KlmMetricCard label="Conversas" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.conversations ?? 0) : '—'} delta={funnelQuery.isError ? 'Indisponível' : 'dados atuais'} trend="neutral" loading={funnelQuery.isPending} />
        <KlmMetricCard label="Qualificados" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.qualified ?? 0) : '—'} delta={funnelQuery.isError ? 'Indisponível' : 'definição do sistema'} trend="neutral" loading={funnelQuery.isPending} />
        <KlmMetricCard label="Reuniões online" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.meetings ?? 0) : '—'} delta={funnelQuery.isError ? 'Indisponível' : 'dados atuais'} trend="neutral" loading={funnelQuery.isPending} />
        <KlmMetricCard label="Pagos" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.paid ?? 0) : '—'} delta={funnelQuery.isError ? 'Indisponível' : 'financeiro local'} trend="neutral" loading={funnelQuery.isPending} />
        <KlmMetricCard label="Pipeline projetado" value={funnelQuery.isSuccess ? formatBRL(funnelQuery.data.metrics.pipelineValue ?? 0) : '—'} delta="honorários projetados" trend="neutral" loading={funnelQuery.isPending} />
        <KlmMetricCard label="Não lidas" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.unread ?? 0) : '—'} delta="exigem resposta" trend={(funnelQuery.data?.metrics.unread ?? 0) > 0 ? 'down' : 'neutral'} loading={funnelQuery.isPending} />
        <KlmMetricCard label="Tarefas abertas" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.openTasks ?? 0) : '—'} delta="próximas ações" trend="neutral" loading={funnelQuery.isPending} />
        <KlmMetricCard label="Flows ativos" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.activeFlows ?? 0) : '—'} delta="execução em tempo real" trend="neutral" loading={funnelQuery.isPending} />
        <KlmMetricCard label="Respostas atrasadas" value={funnelQuery.isSuccess ? String(funnelQuery.data.metrics.overdueSla ?? 0) : '—'} delta="precisam de intervenção" trend={(funnelQuery.data?.metrics.overdueSla ?? 0) > 0 ? 'down' : 'neutral'} loading={funnelQuery.isPending} />
      </div>

      <KlmPanel className="p-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="relative flex-1"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} className="h-9 bg-secondary pl-9 text-xs max-xl:text-base" placeholder="Lead, objeto, subtema ou responsável" /></div>
          <Select value={source} onValueChange={setSource}><SelectTrigger className="h-9 w-full text-xs lg:w-48"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="todas">Todas as origens</SelectItem>{sourceOptions.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select>
          <KlmPill tone={funnelQuery.isError ? 'red' : canEdit && funnelQuery.isSuccess ? 'green' : 'gold'}>CRM: {funnelQuery.isPending ? 'carregando' : funnelQuery.isError ? 'indisponível' : canEdit ? 'conectado' : 'conectado · leitura'}</KlmPill>
        </div>
      </KlmPanel>

      <div data-testid="funnel-status-announcement" aria-live="polite" className="flex min-h-9 items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-2xs text-foreground">
        <GripVertical className="h-3.5 w-3.5 shrink-0 text-[#0057d8]" />
        <span>{moveMutation.isPending ? 'Salvando nova posição no CRM local...' : notice}</span>
      </div>

      {isCompact && (
        <div className="space-y-3" data-testid="funnel-mobile-list">
          {!visibleStages.length && !funnelQuery.isPending && (
            <div className="rounded-lg border border-dashed bg-card p-5 text-center" role="status">
              <p className="text-xs font-semibold">Nenhuma etapa disponível</p>
              <p className="mt-1 text-2xs text-muted-foreground">
                Ajuste os filtros ou confirme a configuração do funil para exibir os leads.
              </p>
            </div>
          )}
          {visibleStages.map((stage) => {
            const stageCards = sortStageCards(filtered.filter((card) => card.stage === stage.id));
            return <section key={stage.id} className="rounded-lg border bg-secondary/40" data-testid={`funnel-mobile-stage-${stage.id}`}>
              <div className="flex items-center justify-between border-b px-3 py-2.5"><div className="flex items-center gap-2"><span className={cn('h-2 w-2 rounded-full', stage.color)} /><h2 className="text-xs font-semibold">{stage.label}</h2></div><KlmPill tone="muted">{stageCards.length}</KlmPill></div>
              <div className="divide-y">{stageCards.length ? stageCards.map((card) => <button key={card.id} type="button" data-testid={`funnel-card-${card.id}`} data-card-id={card.id} className="block w-full p-3 text-left active:bg-primary/10" onClick={() => { setSelectedId(card.id); setWorkspaceCardId(card.id); }}>
                <div className="flex items-start justify-between gap-2"><span className="min-w-0 truncate text-xs font-semibold">{card.name}</span><span className="shrink-0 text-2xs font-semibold tabular-nums">{formatBRL(card.projectedRevenue)}</span></div>
                <p className="mt-1 line-clamp-1 text-2xs text-muted-foreground">{card.subject} · {card.object}</p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5"><KlmPill tone={card.priority === 'alta' || card.urgency === 'critica' ? 'red' : 'muted'}>{card.priority}</KlmPill><span className="text-2xs text-muted-foreground">{card.owner}</span>{card.unreadMessages > 0 && <KlmPill tone="red">{card.unreadMessages} não lidas</KlmPill>}</div>
                <p className="mt-2 line-clamp-1 text-2xs text-muted-foreground">Próxima: {card.nextAction || 'Definir próxima ação'}</p>
              </button>) : <p className="p-3 text-2xs text-muted-foreground">Nenhum lead nesta etapa.</p>}</div>
            </section>;
          })}
        </div>
      )}

      <Tabs value={requestedView} onValueChange={(view) => { const nextParams = new URLSearchParams(searchParams); nextParams.set('view', view); setSearchParams(nextParams); }} className={cn('space-y-4', isCompact && 'hidden')}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <TabsList className="h-9 bg-secondary p-1"><TabsTrigger value="kanban" className="text-xs"><KanbanSquare className="mr-2 h-3.5 w-3.5" />Kanban</TabsTrigger><TabsTrigger value="tabela" className="text-xs"><List className="mr-2 h-3.5 w-3.5" />Tabela</TabsTrigger></TabsList>
          {requestedView === 'kanban' && <div className="flex flex-wrap items-center gap-2">
            <Button variant={selectionMode ? 'secondary' : 'outline'} size="sm" className="h-9 text-xs" onClick={() => { setSelectionMode((current) => !current); setSelectedCardIds(new Set()); }}>Selecionar cards</Button>
            <Select value={wipLimit} onValueChange={(value) => setWipLimit(value as typeof wipLimit)}><SelectTrigger className="h-9 w-[118px] bg-card text-xs" aria-label="Limite por etapa"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="0">Sem limite</SelectItem><SelectItem value="3">Limite 3</SelectItem><SelectItem value="5">Limite 5</SelectItem><SelectItem value="10">Limite 10</SelectItem></SelectContent></Select>
            <Select value={columnSort} onValueChange={(value) => setColumnSort(value as typeof columnSort)}><SelectTrigger className="h-9 w-[190px] bg-card text-xs" aria-label="Ordenar cards"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="manual">Ordem manual</SelectItem><SelectItem value="prazo">Prazo mais próximo</SelectItem><SelectItem value="prioridade">Maior prioridade</SelectItem><SelectItem value="valor">Maior projeção</SelectItem><SelectItem value="atividade">Atividade recente</SelectItem></SelectContent></Select>
          </div>}
        </div>
        {selectionMode && <KlmPanel className="flex flex-wrap items-center gap-2 p-3"><strong className="mr-auto text-xs">{selectedCardIds.size} selecionado{selectedCardIds.size === 1 ? '' : 's'}</strong><Input value={bulkTag} onChange={(event) => setBulkTag(event.target.value)} className="h-9 w-[150px] text-xs" placeholder="Adicionar etiqueta" /><Select value={bulkPriority} onValueChange={setBulkPriority}><SelectTrigger className="h-9 w-[140px] text-xs" aria-label="Definir prioridade"><SelectValue placeholder="Prioridade" /></SelectTrigger><SelectContent><SelectItem value="baixa">Baixa</SelectItem><SelectItem value="normal">Normal</SelectItem><SelectItem value="alta">Alta</SelectItem><SelectItem value="urgente">Urgente</SelectItem></SelectContent></Select><Button variant="outline" size="sm" className="h-9" disabled={!selectedCardIds.size || (!bulkPriority && !bulkTag.trim()) || bulkEditMutation.isPending} onClick={() => bulkEditMutation.mutate()}>{bulkEditMutation.isPending ? 'Aplicando...' : 'Aplicar'}</Button><Select value={bulkStage} onValueChange={setBulkStage}><SelectTrigger className="h-9 w-[180px] text-xs" aria-label="Mover selecionados para"><SelectValue placeholder="Mover para etapa..." /></SelectTrigger><SelectContent>{stages.map((stage) => <SelectItem key={stage.id} value={stage.id}>{stage.label}</SelectItem>)}</SelectContent></Select><Button size="sm" className="h-9" disabled={!selectedCardIds.size || !bulkStage || bulkMoveMutation.isPending} onClick={() => bulkMoveMutation.mutate(bulkStage)}>{bulkMoveMutation.isPending ? 'Movendo...' : 'Mover'}</Button></KlmPanel>}
        <TabsContent value="kanban" className="m-0">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
            <DragDropContext
              dragHandleUsageInstructions="Pressione espaço para levantar o card. Use as setas para mover. Pressione espaço novamente para soltar ou Escape para cancelar."
              onDragStart={(start, provided) => {
                const card = cards.find((item) => item.id === start.draggableId);
                provided.announce(`Card ${card?.name ?? start.draggableId} selecionado. Use as setas para escolher a posição.`);
              }}
              onDragUpdate={(update, provided) => {
                if (!update.destination) return;
                const stage = stages.find((item) => item.id === update.destination?.droppableId);
                provided.announce(`Posição ${update.destination.index + 1} na etapa ${stage?.label ?? update.destination.droppableId}.`);
              }}
              onDragEnd={(result, provided) => {
                handleDragEnd(result);
                if (!result.destination) {
                  provided.announce('Movimento cancelado.');
                  return;
                }
                const stage = stages.find((item) => item.id === result.destination?.droppableId);
                provided.announce(`Card movido para ${stage?.label ?? result.destination.droppableId}, posição ${result.destination.index + 1}.`);
              }}
            >
              <div className="overflow-x-auto pb-3 [scrollbar-gutter:stable]">
                <div className="flex w-max gap-3">
                  {visibleStages.map((stage) => {
                    const stageCards = sortStageCards(filtered.filter((card) => card.stage === stage.id));
                    const stageValue = stageCards.reduce((sum, card) => sum + card.value, 0);
                    const overWipLimit = wipLimit !== '0' && stageCards.length > Number(wipLimit);
                    return (
                      <section key={stage.id} data-testid={`funnel-column-${stage.id}`} className={cn("flex min-h-[540px] w-[82vw] max-w-[320px] shrink-0 snap-start flex-col rounded-lg border bg-secondary/55 p-2.5 sm:w-[44vw] lg:w-[300px]", overWipLimit ? 'border-red-400' : 'border-border')}>
                        <div className="mb-3 flex items-center justify-between"><div className="flex items-center gap-2"><span className={cn('h-2 w-2 rounded-full', stage.color)} /><h2 className="text-sm font-semibold">{stage.label}</h2></div><KlmPill tone={overWipLimit ? 'red' : 'muted'}>{stageCards.length}{wipLimit !== '0' ? `/${wipLimit}` : ''}</KlmPill></div>
                        {overWipLimit && <p className="mb-2 text-2xs font-semibold text-red-600">Limite excedido; redistribua antes de adicionar trabalho.</p>}
                        <p className="mb-3 text-2xs text-muted-foreground">{formatBRL(stageValue)}</p>
                        <Droppable droppableId={stage.id}>
                          {(dropProvided, dropSnapshot) => (
                            <div
                              ref={dropProvided.innerRef}
                              {...dropProvided.droppableProps}
                              className={cn('min-h-[450px] flex-1 space-y-2 rounded-md transition-colors', dropSnapshot.isDraggingOver && 'bg-primary/10 ring-1 ring-primary/30')}
                            >
                              {stageCards.map((card, index) => (
                                <Draggable key={card.id} draggableId={card.id} index={index} isDragDisabled={selectionMode || !canEdit || !funnelQuery.isSuccess || moveMutation.isPending}>
                                  {(dragProvided, dragSnapshot) => (
                                    <DragPortal active={dragSnapshot.isDragging}>
                                    <ContextMenu>
                                      <ContextMenuTrigger asChild>
                                    <article
                                      ref={dragProvided.innerRef}
                                      {...dragProvided.draggableProps}
                                      {...dragProvided.dragHandleProps}
                                      data-testid={`funnel-card-${card.id}`}
                                      data-card-id={card.id}
                                      aria-label={`${card.name}. Arraste para mover entre etapas ou use espaço e as setas.`}
                                      onClick={() => { setSelectedId(card.id); setWorkspaceCardId(card.id); }}
                                      onDoubleClick={() => openCardPath(card, '/app/leads')}
                                      className={cn(
                                        'group relative w-full touch-none select-none rounded-lg border bg-card p-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-primary',
                                        canEdit && funnelQuery.isSuccess && !moveMutation.isPending && 'cursor-grab active:cursor-grabbing',
                                        selectedId === card.id && 'border-primary ring-1 ring-primary/20',
                                        dragSnapshot.isDragging && 'rotate-1 border-primary shadow-xl',
                                      )}
                                    >
                                      {selectionMode && <span className="absolute left-2 top-2 z-30 flex h-7 w-7 items-center justify-center rounded-sm bg-card shadow-sm" onClick={(event) => event.stopPropagation()}><Checkbox checked={selectedCardIds.has(card.id)} onCheckedChange={(checked) => setSelectedCardIds((current) => { const next = new Set(current); if (checked) next.add(card.id); else next.delete(card.id); return next; })} aria-label={`Selecionar ${card.name}`} /></span>}
                                      <button type="button" title="Editar card" aria-label={`Editar ${card.name}`} className="absolute right-10 top-2 z-20 hidden h-7 w-7 items-center justify-center rounded-sm bg-card text-muted-foreground shadow-sm hover:bg-secondary hover:text-foreground group-hover:flex" onClick={(event) => { event.stopPropagation(); setSelectedId(card.id); setWorkspaceCardId(card.id); }}><Pencil className="h-3.5 w-3.5" /></button>
                                      <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0">
                                          <div className="flex min-w-0 items-center gap-1.5">
                                            <strong className="truncate text-xs">{card.name}</strong>
                                            {card.unreadMessages > 0 && <KlmPill tone="red" className="min-h-5 px-1.5 py-0.5">{card.unreadMessages}</KlmPill>}
                                          </div>
                                          <p className="mt-1 truncate text-2xs text-muted-foreground">{card.subject}</p>
                                        </div>
                                        <div className="relative z-10 flex shrink-0 items-center gap-0.5 text-muted-foreground">
                                          <span
                                            data-testid={`funnel-drag-handle-${card.id}`}
                                            title={`Arrastar ${card.name}`}
                                            aria-hidden="true"
                                            className="pointer-events-none flex h-7 w-7 select-none items-center justify-center rounded-sm"
                                            onClick={(event) => event.stopPropagation()}
                                          >
                                            <GripVertical data-testid={`funnel-grip-icon-${card.id}`} className="pointer-events-none h-3.5 w-3.5" />
                                          </span>
                                          <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                              <button
                                                type="button"
                                                aria-label={`Ações de ${card.name}`}
                                                title={`Ações de ${card.name}`}
                                                className="flex h-7 w-7 items-center justify-center rounded-sm hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                                                onClick={(event) => event.stopPropagation()}
                                              >
                                                <MoreHorizontal className="h-3.5 w-3.5" />
                                              </button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
                                              <DropdownMenuItem disabled={!card.threadId} onSelect={() => openCardPath(card, '/app/conversas')}><MessageSquareText className="mr-2 h-3.5 w-3.5" />Abrir conversa</DropdownMenuItem>
                                              <DropdownMenuItem onSelect={() => openCardPath(card, '/app/leads')}><ExternalLink className="mr-2 h-3.5 w-3.5" />Abrir Cliente 360</DropdownMenuItem>
                                              <DropdownMenuItem onSelect={() => openMatterDocuments(card)}><FileSearch className="mr-2 h-3.5 w-3.5" />Documentos e RAG</DropdownMenuItem>
                                              <DropdownMenuSeparator />
                                              <DropdownMenuItem onSelect={() => openCardPath(card, '/app/agenda')}><CalendarPlus className="mr-2 h-3.5 w-3.5" />Agendar reunião</DropdownMenuItem>
                                              <DropdownMenuItem onSelect={() => navigate(`/proposta/${card.id}`)}><FileSignature className="mr-2 h-3.5 w-3.5" />Gerar proposta</DropdownMenuItem>
                                              <DropdownMenuItem onSelect={() => openTaskDialog(card)}><CheckSquare2 className="mr-2 h-3.5 w-3.5" />Criar tarefa</DropdownMenuItem>
                                              <DropdownMenuItem onSelect={() => openCardPath(card, '/app/flows')}><Workflow className="mr-2 h-3.5 w-3.5" />Flows do caso</DropdownMenuItem>
                                            </DropdownMenuContent>
                                          </DropdownMenu>
                                        </div>
                                      </div>

                                      <p className="mt-1 line-clamp-2 min-h-7 text-2xs font-medium leading-3.5">{card.object || 'Objeto ainda não informado'}</p>
                                      <div className="mt-2 flex flex-wrap gap-1">
                                        <KlmPill tone="blue" className="min-h-5 px-1.5 py-0.5">{card.channel || card.source}</KlmPill>
                                        <KlmPill tone={card.priority === 'alta' || card.urgency === 'critica' ? 'red' : 'muted'} className="min-h-5 px-1.5 py-0.5">{card.priority}</KlmPill>
                                        {card.tags.slice(0, 2).map((tag) => <KlmPill key={tag} tone="muted" className="min-h-5 max-w-[112px] truncate px-1.5 py-0.5">{tag}</KlmPill>)}
                                        {card.tags.length > 2 && <KlmPill tone="muted" className="min-h-5 px-1.5 py-0.5">+{card.tags.length - 2}</KlmPill>}
                                        {cardAgeDays(card.updatedAt) >= 7 && <KlmPill tone={cardAgeDays(card.updatedAt) >= 14 ? 'red' : 'gold'} className="min-h-5 px-1.5 py-0.5">{cardAgeDays(card.updatedAt)}d sem atividade</KlmPill>}
                                      </div>

                                      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-2xs">
                                        <span className="text-muted-foreground">Entrada</span><strong className="text-right">{formatBRL(card.value)}</strong>
                                        <span className="text-muted-foreground">Projeção</span><strong className="text-right">{formatBRL(card.projectedRevenue)}</strong>
                                        <span className="text-muted-foreground">Documentos</span><strong className="text-right"><span>{card.docs} documentos</span><span className="ml-1 text-muted-foreground">· {card.documentScore}%</span></strong>
                                        <span className="text-muted-foreground">Tarefas</span><strong className="text-right">{card.openTasks} aberta{card.openTasks === 1 ? '' : 's'}</strong>
                                        <span className="text-muted-foreground">Prazo</span><strong className="truncate text-right">{card.sla}</strong>
                                      </div>

                                      <div className="mt-2 border-t pt-2">
                                        <div className="flex items-start gap-1.5 text-2xs">
                                          <CheckSquare2 className="mt-0.5 h-3 w-3 shrink-0 text-[#0057d8]" />
                                          <span className="line-clamp-2 leading-3"><span className="text-muted-foreground">Próxima: </span>{card.nextAction || 'Definir próxima ação'}</span>
                                        </div>
                                        {card.nextDeadline && <p className="mt-1 pl-4.5 text-2xs text-muted-foreground">{formatDateTime(card.nextDeadline)}</p>}
                                      </div>

                                      {card.activeFlow && (
                                        <div className="mt-2 border-t pt-2">
                                          <div className="mb-1 flex items-center justify-between gap-2 text-2xs"><span className="truncate font-semibold">{card.activeFlow.name}</span><span>{Math.round(card.activeFlow.progress)}%</span></div>
                                          <KlmProgress value={card.activeFlow.progress} tone={card.activeFlow.errorStatus ? 'red' : 'green'} />
                                          <p className="mt-1 truncate text-2xs text-muted-foreground">{card.activeFlow.currentStep || card.activeFlow.status}</p>
                                        </div>
                                      )}

                                      {card.lastMessagePreview && <p className="mt-2 line-clamp-2 border-t pt-2 text-2xs italic leading-3 text-muted-foreground">{card.lastMessagePreview}</p>}
                                      {(card.isBlocked || card.needsApproval) && <div className="mt-2 flex items-center gap-1 text-2xs font-semibold text-red-600"><ShieldAlert className="h-3 w-3" />{card.isBlocked ? 'Card bloqueado' : 'Aprovação humana pendente'}</div>}

                                      {cardAutomation[card.id] && (
                                        <div className={cn(
                                          'mt-2 border-t pt-2 text-2xs',
                                          cardAutomation[card.id].automationStatus === 'failed' ? 'text-[#a53e3c]' : cardAutomation[card.id].automationStatus === 'waiting_human' ? 'text-amber-700 dark:text-amber-400' : 'text-[#0057d8] dark:text-primary',
                                        )} data-testid={`funnel-automation-${card.id}`}>
                                          <div className="flex items-center gap-1.5 font-semibold">
                                            {cardAutomation[card.id].pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : cardAutomation[card.id].automationStatus === 'failed' ? <ShieldAlert className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                                            <span>{cardAutomation[card.id].pending ? 'CRM analisando a nova etapa' : cardAutomation[card.id].automationStatus === 'queued' ? 'Orquestração enfileirada' : cardAutomation[card.id].automationStatus === 'processing' ? 'Orquestração em processamento' : cardAutomation[card.id].automationStatus === 'waiting_human' ? 'Aguardando decisão humana' : cardAutomation[card.id].automationStatus === 'failed' ? 'Automação requer atenção' : 'Orquestração concluída'}</span>
                                          </div>
                                          {cardAutomation[card.id].agentsTriggered?.length ? <p className="mt-1 truncate text-muted-foreground">Agentes: {cardAutomation[card.id].agentsTriggered?.join(' → ')}</p> : null}
                                        </div>
                                      )}

                                      {BILLING_STAGES.has(card.stage) && canViewFinance && (() => {
                                        const financeSummary = financeSummaryByMatterId.get(card.id);
                                        return (
                                          <div className="relative z-10 mt-2 border-t pt-2 text-2xs" data-testid={`funnel-finance-reaction-${card.id}`}>
                                            {financeSummary?.badge && (
                                              <div className="mb-1.5 flex items-center gap-1.5">
                                                <span className="text-muted-foreground">Financeiro</span>
                                                <KlmPill tone={financeSummary.badge === 'vencido' ? 'red' : financeSummary.badge === 'cobrando' ? 'gold' : 'green'} className="min-h-5 px-1.5 py-0.5">
                                                  {FINANCE_BADGE_LABEL[financeSummary.badge]}
                                                </KlmPill>
                                              </div>
                                            )}
                                            {financeSummary?.hasPendingProposal ? (
                                              <button
                                                type="button"
                                                className="flex items-center gap-1 text-left font-semibold text-[#0057d8] hover:underline dark:text-primary"
                                                onClick={(event) => { event.stopPropagation(); navigate('/app/aprovacoes'); }}
                                              >
                                                <ShieldAlert className="h-3 w-3 shrink-0" />proposta aguardando aprovação do financeiro
                                              </button>
                                            ) : !financeSummary?.hasStructure ? (
                                              <Button
                                                type="button"
                                                size="sm"
                                                variant="outline"
                                                className="h-7 w-full text-2xs"
                                                disabled={!canManageFinance}
                                                title={!canManageFinance ? 'Permissão finance.manage necessária' : undefined}
                                                onClick={(event) => { event.stopPropagation(); setArrangementDialogCard(card); }}
                                              >
                                                <CircleDollarSign className="mr-1.5 h-3 w-3" />Criar cobrança/arranjo
                                              </Button>
                                            ) : null}
                                          </div>
                                        );
                                      })()}

                                      <div className="relative z-10 mt-3 grid grid-cols-4 gap-1 border-t pt-2">
                                        <button type="button" disabled={!card.threadId} title="Abrir conversa" aria-label={`Abrir conversa de ${card.name}`} className="flex h-8 items-center justify-center rounded-sm border hover:border-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40" onClick={(event) => { event.stopPropagation(); openCardPath(card, '/app/conversas'); }}><MessageSquareText className="h-3.5 w-3.5" /></button>
                                        <button type="button" title="Abrir Cliente 360" aria-label={`Abrir Cliente 360 de ${card.name}`} className="flex h-8 items-center justify-center rounded-sm border hover:border-primary hover:bg-primary/10" onClick={(event) => { event.stopPropagation(); openCardPath(card, '/app/leads'); }}><ExternalLink className="h-3.5 w-3.5" /></button>
                                        <button type="button" title="Agendar reunião" aria-label={`Agendar reunião para ${card.name}`} className="flex h-8 items-center justify-center rounded-sm border hover:border-primary hover:bg-primary/10" onClick={(event) => { event.stopPropagation(); openCardPath(card, '/app/agenda'); }}><CalendarPlus className="h-3.5 w-3.5" /></button>
                                        <button type="button" disabled={!canEdit} title="Criar tarefa" aria-label={`Criar tarefa para ${card.name}`} className="flex h-8 items-center justify-center rounded-sm border hover:border-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40" onClick={(event) => { event.stopPropagation(); openTaskDialog(card); }}><CheckSquare2 className="h-3.5 w-3.5" /></button>
                                      </div>
                                    </article>
                                      </ContextMenuTrigger>
                                      <ContextMenuContent className="w-52">
                                        <ContextMenuItem disabled={!card.threadId} onSelect={() => openCardPath(card, '/app/conversas')}><MessageSquareText className="mr-2 h-3.5 w-3.5" />Abrir conversa</ContextMenuItem>
                                        <ContextMenuItem onSelect={() => openCardPath(card, '/app/leads')}><ExternalLink className="mr-2 h-3.5 w-3.5" />Abrir Cliente 360</ContextMenuItem>
                                        <ContextMenuItem onSelect={() => openMatterDocuments(card)}><FileSearch className="mr-2 h-3.5 w-3.5" />Documentos e RAG</ContextMenuItem>
                                        <ContextMenuSeparator />
                                        <ContextMenuItem onSelect={() => openCardPath(card, '/app/agenda')}><CalendarPlus className="mr-2 h-3.5 w-3.5" />Agendar reunião</ContextMenuItem>
                                        <ContextMenuItem disabled={!canEdit} onSelect={() => openTaskDialog(card)}><CheckSquare2 className="mr-2 h-3.5 w-3.5" />Criar tarefa</ContextMenuItem>
                                      </ContextMenuContent>
                                    </ContextMenu>
                                    </DragPortal>
                                  )}
                                </Draggable>
                              ))}
                              {dropProvided.placeholder}
                              {!stageCards.length && !dropSnapshot.isDraggingOver && <div className="flex h-24 items-center justify-center rounded-md border border-dashed text-2xs text-muted-foreground">Solte um lead aqui</div>}
                            </div>
                          )}
                        </Droppable>
                        <Button type="button" variant="ghost" size="sm" className="mt-2 h-8 justify-start text-2xs" disabled={!canEdit || overWipLimit} onClick={() => setQuickLeadStage(stage.id)}><Plus className="mr-1.5 h-3.5 w-3.5" />Adicionar lead</Button>
                      </section>
                    );
                  })}
                </div>
              </div>
            </DragDropContext>

            {selected ? (
              <KlmPanel className="h-fit p-4 xl:sticky xl:top-20">
                <KlmSectionTitle title="Lead selecionado" description="Operação completa sem sair do funil." action={<KlmPill tone={selected.probability >= 80 ? 'green' : 'gold'}>{selected.probability}%</KlmPill>} />
                <div className="mt-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0"><h2 className="truncate text-sm font-semibold">{selected.name}</h2><p className="mt-1 text-2xs text-muted-foreground">{selected.subject} · {selected.object}</p></div>
                    {selected.unreadMessages > 0 && <KlmPill tone="red">{selected.unreadMessages} não lidas</KlmPill>}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1">{selected.tags.map((tag) => <KlmPill key={tag} tone="muted">{tag}</KlmPill>)}</div>
                </div>

                <div className="mt-4 divide-y border-y text-2xs">
                  {[
                    [MessageCircle, 'Origem / canal', `${selected.source} · ${selected.channel || '—'}`],
                    [UserRoundCheck, 'Responsável', selected.owner],
                    [FileSearch, 'Documentos', `${selected.docs} arquivo(s) · ${selected.documentScore}% completo`],
                    [CheckSquare2, 'Próxima ação', selected.nextAction || 'Não definida'],
                    [Clock3, 'Prazo de resposta', selected.nextDeadline ? formatDateTime(selected.nextDeadline) : selected.sla],
                    [CircleDollarSign, 'Entrada / projeção', `${formatBRL(selected.value)} · ${formatBRL(selected.projectedRevenue)}`],
                  ].map(([Icon, label, value]) => {
                    const ItemIcon = Icon as typeof MessageCircle;
                    return <div key={String(label)} className="flex items-center gap-3 py-2.5"><ItemIcon className="h-3.5 w-3.5 shrink-0 text-[#0057d8]" /><span className="min-w-0"><span className="block text-2xs text-muted-foreground">{String(label)}</span><strong className="mt-0.5 block break-words text-2xs">{String(value)}</strong></span></div>;
                  })}
                </div>

                {selected.activeFlow && (
                  <div className="mt-4 border-b pb-4">
                    <div className="flex items-center justify-between gap-2 text-2xs"><span className="min-w-0 truncate font-semibold">{selected.activeFlow.name}</span><KlmPill tone={selected.activeFlow.errorStatus ? 'red' : 'green'}>{Math.round(selected.activeFlow.progress)}%</KlmPill></div>
                    <div className="mt-2"><KlmProgress value={selected.activeFlow.progress} tone={selected.activeFlow.errorStatus ? 'red' : 'green'} /></div>
                    <p className="mt-2 text-2xs text-muted-foreground">Passo atual: {selected.activeFlow.currentStep || selected.activeFlow.status} · {selected.activeFlow.completedSteps}/{selected.activeFlow.totalSteps}</p>
                  </div>
                )}

                {selected.upcomingMeetingAt && (
                  <div className="mt-4 flex items-center justify-between gap-3 border-b pb-4 text-2xs">
                    <span><span className="block text-2xs text-muted-foreground">Próxima reunião</span><strong>{formatDateTime(selected.upcomingMeetingAt)}</strong></span>
                    <Button size="sm" variant="outline" disabled={!selected.meetingUrl} onClick={() => openMeeting(selected.meetingUrl)} className="h-8 px-2 text-2xs"><ExternalLink className="mr-1.5 h-3 w-3" />Entrar</Button>
                  </div>
                )}

                {(selected.isBlocked || selected.needsApproval) && <div className="mt-3 flex items-center gap-2 bg-red-500/10 p-2 text-2xs font-semibold text-red-600"><ShieldAlert className="h-3.5 w-3.5" />{selected.isBlocked ? 'Card bloqueado' : 'Aprovação humana pendente'}</div>}

                <PermissionGate module="kanban" action="edit" actionLabel="mover o lead de etapa" className="mt-4 block">
                  <Select value={selected.stage} onValueChange={(stage) => moveCardToStage(selected.id, stage)} disabled={!funnelQuery.isSuccess || moveMutation.isPending}>
                    <SelectTrigger className="h-9 w-full text-2xs" aria-label="Mover lead para etapa"><SelectValue /></SelectTrigger>
                    <SelectContent>{stages.map((stage) => <SelectItem key={stage.id} value={stage.id}>{stage.label}</SelectItem>)}</SelectContent>
                  </Select>
                </PermissionGate>
                <PermissionGate module="kanban" action="edit" actionLabel="mover o lead de etapa" className="mt-2 grid grid-cols-2 gap-2">
                  <div className="col-span-2 grid grid-cols-2 gap-2"><Button variant="outline" size="sm" disabled={selected.stage === stages[0]?.id || moveMutation.isPending} onClick={() => moveCard(selected.id, -1)} className="text-2xs"><ArrowLeft className="mr-1.5 h-3 w-3" />Voltar etapa</Button><Button size="sm" disabled={selected.stage === stages.at(-1)?.id || moveMutation.isPending} onClick={() => moveCard(selected.id, 1)} className="bg-[#031121] text-2xs text-white hover:bg-[#031121]">Avançar <ArrowRight className="ml-1.5 h-3 w-3" /></Button></div>
                </PermissionGate>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <Button variant="outline" size="sm" disabled={!selected.threadId} onClick={() => openCardPath(selected, '/app/conversas')} className="text-2xs"><MessageSquareText className="mr-1.5 h-3 w-3" />Conversa</Button>
                  <Button variant="outline" size="sm" onClick={() => openCardPath(selected, '/app/leads')} className="text-2xs"><ExternalLink className="mr-1.5 h-3 w-3" />Cliente 360</Button>
                  <Button variant="outline" size="sm" onClick={() => openMatterDocuments(selected)} className="text-2xs"><FileSearch className="mr-1.5 h-3 w-3" />Documentos</Button>
                  <Button variant="outline" size="sm" onClick={() => openCardPath(selected, '/app/agenda')} className="text-2xs"><CalendarPlus className="mr-1.5 h-3 w-3" />Agendar</Button>
                  <Button variant="outline" size="sm" onClick={() => navigate(`/proposta/${selected.id}`)} className="text-2xs"><FileSignature className="mr-1.5 h-3 w-3" />Proposta</Button>
                  <PermissionGate module="kanban" action="edit" actionLabel="criar tarefas neste card">
                    <Button variant="outline" size="sm" onClick={() => openTaskDialog(selected)} className="text-2xs"><CheckSquare2 className="mr-1.5 h-3 w-3" />Tarefa</Button>
                  </PermissionGate>
                  <Button variant="outline" size="sm" onClick={() => openCardPath(selected, '/app/flows')} className="text-2xs"><Workflow className="mr-1.5 h-3 w-3" />Flows</Button>
                  <Button variant="outline" size="sm" onClick={() => openCardPath(selected, '/app/financeiro')} className="text-2xs"><CircleDollarSign className="mr-1.5 h-3 w-3" />Financeiro</Button>
                  <Button variant="outline" size="sm" onClick={() => openCardPath(selected, '/app/rastreamento')} className="col-span-2 text-2xs"><Tags className="mr-1.5 h-3 w-3" />Origem e rastreamento</Button>
                </div>
              </KlmPanel>
            ) : (
              <KlmPanel className="h-fit p-5 xl:sticky xl:top-20">
                <KlmSectionTitle title="Lead selecionado" description="Os detalhes aparecem após selecionar um card." />
                {funnelQuery.isPending && <div className="mt-5 space-y-2"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-1/2" /><Skeleton className="h-3 w-2/3" /></div>}
                {funnelQuery.isError && <p className="mt-5 text-2xs text-[#a53e3c]">O funil está indisponível.</p>}
                {funnelQuery.isSuccess && (
                  <div className="mt-5 flex flex-col items-center gap-2 text-center">
                    <p className="text-2xs text-muted-foreground">Nenhum lead neste funil ainda.</p>
                    <Button size="sm" variant="outline" className="h-8 text-2xs" onClick={() => navigate('/app/conversas')}>Iniciar uma conversa</Button>
                  </div>
                )}
              </KlmPanel>
            )}
          </div>
        </TabsContent>
        <TabsContent value="tabela" className="m-0">
          <KlmPanel className="overflow-x-auto">
            <table className="w-full min-w-[1180px] text-left text-2xs">
              <thead>
                <tr className="border-b bg-secondary">
                  <th className="p-3">Lead</th>
                  <th className="w-[112px] p-3">Etapa</th>
                  <th className="p-3">Subtema / objeto</th>
                  <th className="p-3">Origem / canal</th>
                  <th className="p-3">Próxima ação</th>
                  <th className="p-3">Tarefas</th>
                  <th className="p-3">Flow</th>
                  <th className="p-3">Prob.</th>
                  <th className="p-3">Projeção</th>
                  <th className="p-3 text-right">Ações</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((card) => (
                  <tr key={card.id} className="border-b last:border-0 hover:bg-secondary">
                    <td className="p-3">
                      <button type="button" className="max-w-[180px] text-left font-semibold hover:text-[#0057d8] hover:underline" onClick={() => openCardPath(card, '/app/leads')}>{card.name}</button>
                      {card.unreadMessages > 0 && <span className="ml-2 inline-flex min-w-5 justify-center rounded-full bg-red-600 px-1.5 py-0.5 text-2xs font-bold text-white">{card.unreadMessages}</span>}
                    </td>
                    <td className="w-[112px] p-3"><KlmPill tone="gold" className="min-w-[72px] justify-center whitespace-nowrap">{stages.find((stage) => stage.id === card.stage)?.label}</KlmPill></td>
                    <td className="max-w-[240px] p-3"><span className="block font-medium">{card.subject}</span><span className="line-clamp-1 text-muted-foreground">{card.object}</span></td>
                    <td className="p-3"><span className="block">{card.source}</span><span className="text-muted-foreground">{card.channel || '—'}</span></td>
                    <td className="max-w-[220px] p-3"><span className="line-clamp-2">{card.nextAction || 'Não definida'}</span><span className="text-2xs text-muted-foreground">{formatDateTime(card.nextDeadline)}</span></td>
                    <td className="p-3"><KlmPill tone={card.openTasks > 0 ? 'blue' : 'muted'}>{card.openTasks}</KlmPill></td>
                    <td className="min-w-[130px] p-3">{card.activeFlow ? <><div className="mb-1 flex justify-between gap-2 text-2xs"><span className="max-w-[90px] truncate">{card.activeFlow.name}</span><span>{Math.round(card.activeFlow.progress)}%</span></div><KlmProgress value={card.activeFlow.progress} tone={card.activeFlow.errorStatus ? 'red' : 'green'} /></> : <span className="text-muted-foreground">—</span>}</td>
                    <td className="p-3">{card.probability}%</td>
                    <td className="p-3 font-semibold tabular-nums">{formatBRL(card.projectedRevenue)}</td>
                    <td className="p-3">
                      <div className="flex justify-end gap-1">
                        <button type="button" disabled={!card.threadId} title="Abrir conversa" aria-label={`Abrir conversa de ${card.name}`} className="flex h-8 w-8 items-center justify-center rounded-sm border hover:border-primary hover:bg-primary/10 disabled:opacity-40" onClick={() => openCardPath(card, '/app/conversas')}><MessageSquareText className="h-3.5 w-3.5" /></button>
                        <button type="button" title="Abrir Cliente 360" aria-label={`Abrir Cliente 360 de ${card.name}`} className="flex h-8 w-8 items-center justify-center rounded-sm border hover:border-primary hover:bg-primary/10" onClick={() => openCardPath(card, '/app/leads')}><ExternalLink className="h-3.5 w-3.5" /></button>
                        <button type="button" disabled={!canEdit} title="Criar tarefa" aria-label={`Criar tarefa para ${card.name}`} className="flex h-8 w-8 items-center justify-center rounded-sm border hover:border-primary hover:bg-primary/10 disabled:opacity-40" onClick={() => openTaskDialog(card)}><CheckSquare2 className="h-3.5 w-3.5" /></button>
                      </div>
                    </td>
                  </tr>
                ))}
                {funnelQuery.isPending && Array.from({ length: 5 }).map((_, index) => (
                  <tr key={index} className="border-b last:border-0"><td colSpan={10} className="p-3"><Skeleton className="h-4 w-full" /></td></tr>
                ))}
                {!funnelQuery.isPending && !filtered.length && (
                  <tr><td colSpan={10} className="p-8 text-center text-2xs text-muted-foreground">
                    {funnelQuery.isError ? 'Funil indisponível.' : query || source !== 'todas' ? 'Nenhum lead corresponde aos filtros.' : (
                      <div className="flex flex-col items-center gap-2">
                        <span>Nenhum lead neste funil ainda.</span>
                        <Button size="sm" variant="outline" className="h-8 text-2xs" onClick={() => navigate('/app/conversas')}>Iniciar uma conversa</Button>
                      </div>
                    )}
                  </td></tr>
                )}
              </tbody>
            </table>
          </KlmPanel>
        </TabsContent>
      </Tabs>

      <FunnelCardWorkspaceSheet
        card={workspaceCard}
        open={Boolean(workspaceCard)}
        onOpenChange={(open) => { if (!open) setWorkspaceCardId(null); }}
        stageLabel={stages.find((stage) => stage.id === workspaceCard?.stage)?.label ?? 'Etapa'}
        stageOptions={stages.map((stage) => ({ id: stage.id, label: stage.label }))}
        onStageChange={(stage) => { if (workspaceCard) moveCardToStage(workspaceCard.id, stage); }}
        movePending={moveMutation.isPending}
        canEdit={canEdit}
      />

      <Dialog open={Boolean(quickLeadStage)} onOpenChange={(open) => { if (!open) setQuickLeadStage(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>Novo lead</DialogTitle><DialogDescription>Crie o contato diretamente em {stages.find((stage) => stage.id === quickLeadStage)?.label ?? 'esta etapa'}.</DialogDescription></DialogHeader>
          <div className="space-y-3 py-2">
            <label className="block space-y-1 text-xs font-semibold">Nome<Input value={quickLeadName} onChange={(event) => setQuickLeadName(event.target.value)} autoFocus maxLength={180} className="max-xl:text-base" /></label>
            <label className="block space-y-1 text-xs font-semibold">Telefone<Input aria-label="Telefone" value={quickLeadPhone} onChange={(event) => setQuickLeadPhone(event.target.value)} onBlur={() => normalizedQuickLeadPhone && setQuickLeadPhone(normalizedQuickLeadPhone)} placeholder="5561999999999" inputMode="tel" autoComplete="tel" aria-describedby="quick-lead-phone-help" aria-invalid={Boolean(quickLeadPhone.trim() && !normalizedQuickLeadPhone)} className="max-xl:text-base" /><span id="quick-lead-phone-help" className="block font-normal text-muted-foreground">{META_PHONE_HELP} O número será corrigido automaticamente antes do cadastro.</span></label>
            <label className="block space-y-1 text-xs font-semibold">Assunto inicial<Input value={quickLeadDemand} onChange={(event) => setQuickLeadDemand(event.target.value)} maxLength={240} className="max-xl:text-base" /></label>
            <label className="block space-y-1 text-xs font-semibold">Origem<Input value={quickLeadSource} onChange={(event) => setQuickLeadSource(event.target.value)} maxLength={80} className="max-xl:text-base" /></label>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setQuickLeadStage(null)}>Cancelar</Button><Button disabled={!quickLeadName.trim() || !quickLeadDemand.trim() || Boolean(quickLeadPhone.trim() && !normalizedQuickLeadPhone) || quickLeadMutation.isPending} onClick={() => quickLeadMutation.mutate()}>{quickLeadMutation.isPending ? 'Criando...' : 'Criar lead'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(taskMatterId)} onOpenChange={(open) => {
        if (open) return;
        setTaskMatterId(null);
        setTaskTitle('');
        setTaskPriority('media');
      }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Criar tarefa no card</DialogTitle>
            <DialogDescription>A tarefa entra no Cliente 360, no painel de trabalho e na trilha de auditoria.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div><label htmlFor="funnel-task-title" className="text-2xs font-semibold">Título</label><Input id="funnel-task-title" value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} className="mt-1 max-xl:text-base" maxLength={240} autoFocus /></div>
            <div><label htmlFor="funnel-task-priority" className="text-2xs font-semibold">Prioridade</label><Select value={taskPriority} onValueChange={(value) => setTaskPriority(value as typeof taskPriority)}><SelectTrigger id="funnel-task-priority" className="mt-1"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="baixa">Baixa</SelectItem><SelectItem value="media">Média</SelectItem><SelectItem value="alta">Alta</SelectItem></SelectContent></Select></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTaskMatterId(null)}>Cancelar</Button>
            <Button disabled={!taskMatterId || !taskTitle.trim() || taskMutation.isPending} onClick={() => taskMatterId && taskMutation.mutate({ matterId: taskMatterId, title: taskTitle.trim(), priority: taskPriority })}>{taskMutation.isPending ? 'Criando...' : 'Criar tarefa'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {arrangementDialogCard && (
        <CreateFeeArrangementDialog
          matterId={arrangementDialogCard.id}
          matterName={arrangementDialogCard.name}
          open={Boolean(arrangementDialogCard)}
          onOpenChange={(open) => { if (!open) setArrangementDialogCard(null); }}
          canManage={canManageFinance}
        />
      )}
    </div>
  );
}
