import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowUpDown,
  AudioLines,
  Bell,
  Bot,
  CalendarPlus,
  CalendarClock,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleUserRound,
  ClipboardList,
  Clock3,
  Database,
  FilePlus2,
  FileSignature,
  FilterX,
  LockKeyhole,
  LoaderCircle,
  Megaphone,
  MessageCircleReply,
  MessageSquareText,
  MessagesSquare,
  Paperclip,
  PanelRight,
  PanelRightClose,
  PanelRightOpen,
  PhoneOutgoing,
  Pause,
  Play,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Star,
  TriangleAlert,
  Timer,
  UserRoundCheck,
  UsersRound,
  Workflow,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { OfficialMetaGroupSheet } from '@/klm/components/OfficialMetaGroupSheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { META_PHONE_HELP, normalizeWhatsAppPhone } from '@/lib/whatsapp-phone';
import { NativeInboxRoutingPanel } from '@/klm/components/NativeInboxRoutingPanel';
import { ContactAvatar } from '@/klm/components/ContactAvatar';
import { ConversationCasePanel } from '@/klm/components/ConversationCasePanel';
import { ConversationSchedulingDialog } from '@/klm/components/ConversationSchedulingDialog';
import { ConversationMessageSchedulingDialog } from '@/klm/components/ConversationMessageSchedulingDialog';
import { ConversationRichMessageDialog } from '@/klm/components/ConversationRichMessageDialog';
import { ConversationDocumentRequestSheet } from '@/klm/components/ConversationDocumentRequestSheet';
import { ConversationProposalPreparationSheet } from '@/klm/components/ConversationProposalPreparationSheet';
import { ConversationCopilotPanel } from '@/klm/components/ConversationCopilotPanel';
import { ConversationTemplatePicker } from '@/klm/components/ConversationTemplatePicker';
import { ConversationEvidenceSheet } from '@/klm/components/ConversationEvidenceSheet';
import {
  ConversationCollaborationBar,
  type ConversationMentionTarget,
  type ConversationMessageReference,
  type ConversationParticipant,
} from '@/klm/components/ConversationCollaborationBar';
import {
  StaffAvailabilitySheet,
  type StaffAvailabilityApproval,
  type StaffAvailabilityRequestInput,
  type StaffAvailabilityValue,
} from '@/klm/components/StaffAvailabilitySheet';
import {
  ConversationResponseWaitClock,
  conversationResponseWaitSeconds,
} from '@/klm/components/ConversationResponseMetricsSheet';
import AudioPage from '@/klm/pages/AudioPage';
import Campaigns from '@/pages/Campaigns';
import { browserLocalDateTimeToIso, staffAvailabilityValueFromApi } from '@/klm/staff-availability-adapter';
import { EffectivePlaybookLink } from '@/klm/components/playbooks/EffectivePlaybookLink';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import { PermissionGate } from '@/klm/components/PermissionGate';
import { cn } from '@/lib/utils';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import { useIsMobile } from '@/hooks/use-mobile';
import {
  bootstrapKlmLocalSession,
  klmApi,
  type KlmConversationParticipantCollection,
  type KlmConversationResponseMetric,
  type KlmConversationResponseState,
  type KlmFlow,
  type KlmFlowRun,
  type KlmFunnel,
  type KlmMatterDetail,
  type KlmOmnichannelContext,
  type KlmOmnichannelMessageRoute,
  type KlmStaffAvailabilityChangeRequest,
  type KlmThread,
  type KlmThreadFilterQuery,
} from '@/klm/api';
import { ApiError } from '@/services/http';
import { fetchWhatsAppConnections, startWhatsAppConversation } from '@/services/whatsapp';
import { fetchMetaTemplates } from '@/services/campaigns';
import { useAppStore } from '@/store/app-context';
import type { MetaTemplate, PermissionAction, PermissionModule } from '@/types';
import { resolveContactAvatar } from '@/klm/contact-avatar';

const ContextLeadPage = lazy(() => import('@/klm/pages/LeadPage'));
const ContextFunnelPage = lazy(() => import('@/klm/pages/FunnelPage'));
const ContextTrackingPage = lazy(() => import('@/klm/pages/TrackingPage'));
const ContextQualityPage = lazy(() => import('@/klm/pages/QualityPage'));
const ContextOperationalModulePage = lazy(() => import('@/klm/pages/OperationalModulePage'));
const ContextProposalPreviewPage = lazy(() => import('@/klm/pages/ProposalPreviewPage'));

type Contact = {
  id: string;
  initials: string;
  avatarUrl: string | null;
  avatarSource: string | null;
  name: string;
  legalAreaKey: string | null;
  legalArea: string;
  subtheme: string;
  object: string;
  status: string;
  lastAuthor: string;
  version?: number;
  score: number;
  tone: 'gold' | 'green' | 'red' | 'blue' | 'muted';
  channel: KlmThread['primaryChannel'];
  conversationKind: 'individual' | 'group';
  unreadCount: number;
  preview: string;
  sensitiveDataHidden?: boolean;
  broadcastState: 'none' | 'replied' | 'awaiting_reply' | 'delivery_failed';
  broadcastCampaignIds: string[];
  broadcastTitle?: string;
  statusCode: string;
  assignedHumanId: string | null;
  assignedTeamId: string | null;
  attendantLabel: string;
  teamLabel: string;
  agentKey: string | null;
  queue: string;
  threadPriority: number;
  priority: string;
  urgency: string;
  classification: string;
  stage: string;
  microStatus: string;
  leadSource: string;
  tags: string[];
  flowStatus: string;
  slaState: 'overdue' | 'on_time' | 'without_sla';
  lastMessageAt: string | null;
  isFavorite: boolean;
  responseMetric: KlmConversationResponseMetric | null;
  responseState: KlmConversationResponseState | null;
  responseWaitSeconds: number | null;
  responseOverdue: boolean;
};

type ConversationIndicatorItem = {
  key: string;
  title: string;
  detail: string;
  disabled?: boolean;
  onSelect?: () => void;
};

function ConversationIndicatorPopover({
  label,
  tone,
  title,
  emptyLabel,
  items,
  disabled = false,
}: {
  label: string;
  tone: 'gold' | 'green' | 'red' | 'blue' | 'muted';
  title: string;
  emptyLabel: string;
  items: ConversationIndicatorItem[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="rounded-full text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-65"
          disabled={disabled}
          aria-label={`${label}. Abrir lista`}
        >
          <KlmPill tone={tone} className="cursor-pointer gap-1 pr-1.5">
            {label}
            <ChevronDown className="h-3 w-3 shrink-0" aria-hidden="true" />
          </KlmPill>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(360px,calc(100vw-2rem))] p-0">
        <div className="border-b border-border px-3 py-2">
          <p className="text-xs font-semibold text-foreground">{title}</p>
          <p className="mt-0.5 text-2xs text-muted-foreground">{items.length} registro{items.length === 1 ? '' : 's'}</p>
        </div>
        <div className="max-h-72 overflow-y-auto p-1.5">
          {items.length ? items.map((item) => (
            <button
              key={item.key}
              type="button"
              disabled={item.disabled}
              className="flex w-full min-w-0 items-center justify-between gap-3 rounded-md px-2.5 py-2 text-left hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:opacity-60"
              onClick={() => {
                item.onSelect?.();
                if (item.onSelect) setOpen(false);
              }}
            >
              <span className="min-w-0">
                <span className="block truncate text-xs font-semibold text-foreground">{item.title}</span>
                <span className="mt-0.5 block truncate text-2xs text-muted-foreground">{item.detail}</span>
              </span>
              {item.onSelect ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /> : null}
            </button>
          )) : (
            <p className="px-2.5 py-5 text-center text-xs text-muted-foreground">{emptyLabel}</p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

type Message = {
  id: string;
  author: string;
  role: 'client' | 'sdr' | 'legal' | 'human' | 'internal';
  text: string;
  time: string;
  note?: string;
  deliveryState?: 'local' | 'indexed' | 'received' | 'blocked' | 'failed';
  attachments?: Array<{ id: string; name: string; fileType: string; localUrl?: string }>;
  ragPath?: string;
  revealAt?: string;
  typingStartsAt?: string;
  showTypingIndicator?: boolean;
  routeLabel?: string;
  channel?: KlmThread['primaryChannel'];
  contentLifecycleState: 'original' | 'edited' | 'deleted' | 'provider_unsupported';
  evidenceEventCount: number;
  evidenceRevisionCount: number;
  evidenceChainSha256?: string | null;
  replyTo?: ConversationMessageReference | null;
  mentions?: ConversationMentionTarget[];
};

type ConversationNoticeAction = {
  label: string;
  kind: 'flow' | 'proposal' | 'schedule';
  proposalPath?: string;
};

type ConversationContextWorkspace = {
  title: string;
  description: string;
  path: string;
};

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function isoToBrowserLocalDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const localDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localDate.toISOString().slice(0, 16);
}

function collaborationReference(metadata: Record<string, unknown>): ConversationMessageReference | null {
  const collaboration = recordValue(metadata.collaboration);
  const raw = recordValue(collaboration?.replyReference ?? metadata.replyReference);
  const id = raw?.id ?? collaboration?.replyToMessageId ?? metadata.replyToMessageId;
  if (typeof id !== 'string' || !id) return null;
  return {
    id,
    authorLabel: typeof raw?.authorLabel === 'string' ? raw.authorLabel : 'Mensagem anterior',
    body: typeof raw?.body === 'string' ? raw.body : 'Conteúdo preservado no histórico',
    occurredAtLabel: typeof raw?.occurredAtLabel === 'string' ? raw.occurredAtLabel : undefined,
  };
}

function collaborationMentions(metadata: Record<string, unknown>): ConversationMentionTarget[] {
  const collaboration = recordValue(metadata.collaboration);
  const rows = Array.isArray(collaboration?.mentions)
    ? collaboration.mentions
    : Array.isArray(metadata.mentions)
      ? metadata.mentions
      : [];
  return rows.flatMap((row) => {
    const value = recordValue(row);
    const id = value?.id ?? value?.targetId;
    const kind = value?.kind ?? value?.mentionType;
    const name = value?.name ?? value?.resolvedLabel;
    if (typeof id !== 'string' || (kind !== 'user' && kind !== 'team') || typeof name !== 'string') return [];
    return [{
      id,
      kind,
      name,
      handle: typeof value?.handle === 'string' ? value.handle : undefined,
      detail: typeof value?.detail === 'string' ? value.detail : undefined,
    } satisfies ConversationMentionTarget];
  });
}

class ConversationContextErrorBoundary extends Component<{
  children: ReactNode;
  onClose: () => void;
}, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <KlmPanel role="alert" className="m-4 p-6 text-center">
        <TriangleAlert className="mx-auto h-5 w-5 text-destructive" />
        <h3 className="mt-3 text-sm font-semibold">Esta ferramenta não pôde ser carregada</h3>
        <p className="mt-2 text-xs text-muted-foreground">A conversa continua preservada. Feche esta lâmina e tente novamente sem perder o atendimento.</p>
        <Button variant="outline" className="mt-4" onClick={this.props.onClose}>Voltar à conversa</Button>
      </KlmPanel>
    );
  }
}

function contextWorkspaceMeta(pathname: string, searchParams: URLSearchParams) {
  if (pathname === '/app/leads' && searchParams.get('panel') === 'rag') {
    return { title: 'Documentos e RAG', description: 'Fontes do dossiê e da conversa canônica, isoladas para este cliente e caso.' };
  }
  if (pathname === '/app/leads' && searchParams.get('panel') === 'tratamento') {
    return { title: 'Tratamento e valor econômico', description: 'Ciclos, medicamentos, premissas e projeções financeiras do caso.' };
  }
  const destinations: Record<string, { title: string; description: string }> = {
    '/app/leads': { title: 'Cliente 360', description: 'Card completo, dados, tarefas e contexto operacional deste atendimento.' },
    '/app/funil': { title: 'Funil e oportunidade', description: 'Posição comercial e jurídica do caso no funil, sem perder a conversa atual.' },
    '/app/rastreamento': { title: 'Rastreamento da origem', description: 'Campanha, conjunto, anúncio, criativo, jornada e eventos atribuídos a este lead.' },
    '/app/qualidade': { title: 'Qualidade do atendimento', description: 'Auditoria de resposta, entrega, cobertura e falhas desta conversa.' },
    '/app/contratos': { title: 'Contratos e procurações', description: 'Documentos, versões, assinatura e evidências vinculadas ao caso.' },
    '/app/financeiro': { title: 'Financeiro do caso', description: 'Honorários, cobranças, recebimentos e projeções desta oportunidade.' },
    '/app/peticoes': { title: 'Petições do caso', description: 'Peças, minutas, aprovações e histórico jurídico vinculados ao atendimento.' },
    '/app/atividade': { title: 'Histórico e auditoria', description: 'Eventos imutáveis, intervenções humanas, agentes e alterações do caso.' },
  };
  return destinations[pathname] ?? {
    title: 'Ferramenta contextual',
    description: 'Operação vinculada ao atendimento atual, sem abandonar a conversa.',
  };
}

function ConversationContextRoute({
  path,
  onNavigate,
}: {
  path: string;
  onNavigate: (path: string) => void;
}) {
  const pathname = new URL(path, 'http://klm.local').pathname;
  const contextualProps = { embedded: true, onNavigate, contextPath: path };
  let content;

  if (pathname === '/app/leads') content = <ContextLeadPage {...contextualProps} />;
  else if (pathname === '/app/funil') content = <ContextFunnelPage {...contextualProps} />;
  else if (pathname === '/app/rastreamento') content = <ContextTrackingPage {...contextualProps} />;
  else if (pathname === '/app/qualidade') content = <ContextQualityPage {...contextualProps} />;
  else if (['/app/contratos', '/app/financeiro', '/app/peticoes', '/app/atividade'].includes(pathname)) {
    content = <ContextOperationalModulePage {...contextualProps} />;
  } else if (pathname.startsWith('/proposta/')) content = <ContextProposalPreviewPage {...contextualProps} />;
  else content = <KlmPanel className="p-6 text-center text-xs text-muted-foreground">Esta ferramenta ainda não possui uma superfície contextual registrada.</KlmPanel>;

  return (
    <ConversationContextErrorBoundary onClose={() => onNavigate('/app/conversas')}>
      <Suspense fallback={<div className="flex min-h-48 items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Carregando ferramenta contextual</div>}>
        {content}
      </Suspense>
    </ConversationContextErrorBoundary>
  );
}

const flowStatusLabel: Record<string, string> = {
  active: 'Em execução',
  paused: 'Pausado',
  waiting_human: 'Aguardando humano',
  completed: 'Concluído',
  failed: 'Falhou',
  cancelled: 'Encerrado',
};

function flowCategory(flow: KlmFlow): { label: string; tone: 'gold' | 'green' | 'blue' | 'muted' } {
  const configured = String(flow.trigger_config?.category ?? '').toLocaleLowerCase('pt-BR');
  const searchable = `${flow.name} ${flow.description ?? ''} ${configured}`.toLocaleLowerCase('pt-BR');
  if (searchable.includes('recuper')) return { label: 'Recuperação', tone: 'blue' };
  if (searchable.includes('follow') || searchable.includes('document')) return { label: 'Follow-up', tone: 'gold' };
  if (flow.trigger === 'manual') return { label: 'Manual', tone: 'muted' };
  return { label: 'Atendimento', tone: 'green' };
}

function channelLabel(channel: KlmThread['primaryChannel']) {
  const labels: Record<KlmThread['primaryChannel'], string> = {
    whatsapp: 'WhatsApp',
    email: 'E-mail',
    form: 'Formulário',
    instagram: 'Instagram',
    facebook: 'Facebook',
    messenger: 'Messenger',
    tiktok: 'TikTok',
    webchat: 'Chat do site',
    audio: 'Áudio',
    internal: 'Interno',
  };
  return labels[channel];
}

function legalAreaLabel(area: string) {
  const labels: Record<string, string> = {
    saude_suplementar: 'Saúde',
    negativas_plano_saude: 'Saúde',
    erro_medico: 'Erro médico',
    bancario: 'Bancário',
    trabalhista: 'Trabalhista',
    previdenciario: 'Previdenciário',
    civel: 'Cível',
    consumidor: 'Consumidor',
    digital: 'Direito digital',
    propriedade_intelectual: 'Propriedade intelectual',
    seguros: 'Seguros',
    tributario: 'Tributário',
    empresarial: 'Empresarial',
    familia: 'Família',
    imobiliario: 'Imobiliário',
  };
  return labels[area] ?? area.replaceAll('_', ' ').replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase('pt-BR'));
}

type ConversationRoute = KlmOmnichannelContext['channels'][number];

function conversationRouteKey(route: ConversationRoute) {
  return `${route.endpointId}:${route.bindingId ?? 'none'}`;
}

function transportLabel(mode: string | null) {
  if (mode === 'coexistence') return 'Coexistência';
  if (mode === 'cloud_api') return 'Cloud API';
  if (mode === 'official_api') return 'API oficial';
  if (mode === 'oauth') return 'OAuth';
  if (mode === 'webhook') return 'Webhook';
  return mode?.replaceAll('_', ' ') || 'Canal local';
}

function conversationRouteLabel(route: ConversationRoute) {
  return `${channelLabel(route.channel)} · ${transportLabel(route.transportMode)}`;
}

function slaDueLabel(value: string | null) {
  if (!value) return 'Prazo de resposta não definido';
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return 'Prazo de resposta inválido';
  const formatted = new Date(value).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  return timestamp < Date.now() ? `Resposta atrasada desde ${formatted}` : `Responder até ${formatted}`;
}

function formatConversationWait(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return 'tempo indisponível';
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  if (days > 0) return `${days}d ${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}m`;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
  return `${minutes}m`;
}

type ConversationSort = 'operational' | 'response_wait' | 'score' | 'recent' | 'unread' | 'sla' | 'name';

function advancedThreadFilters(
  definition: PersonalizedFilterDefinition,
  sort: ConversationSort,
): KlmThreadFilterQuery {
  const filters: KlmThreadFilterQuery = { limit: 500 };
  const sortMap: Partial<Record<ConversationSort, KlmThreadFilterQuery['sortBy']>> = {
    score: 'score',
    recent: 'recent',
    unread: 'unread',
    sla: 'sla',
  };
  if (sortMap[sort]) {
    filters.sortBy = sortMap[sort];
    filters.sortDirection = sort === 'sla' ? 'asc' : 'desc';
  }
  if (definition.match !== 'all') return filters;

  definition.conditions.forEach((condition) => {
    const textValue = String(condition.value ?? '').trim();
    const numberValue = Number(condition.value);
    const exact = condition.operator === 'eq';
    if (condition.field === 'score' && Number.isFinite(numberValue)) {
      if (condition.operator === 'eq') { filters.scoreMin = numberValue; filters.scoreMax = numberValue; }
      if (condition.operator === 'gte') filters.scoreMin = numberValue;
      if (condition.operator === 'gt') filters.scoreMin = numberValue + 1;
      if (condition.operator === 'lte') filters.scoreMax = numberValue;
      if (condition.operator === 'lt') filters.scoreMax = numberValue - 1;
    }
    if (condition.field === 'threadPriority' && Number.isFinite(numberValue)) {
      if (condition.operator === 'eq') { filters.threadPriorityMin = numberValue; filters.threadPriorityMax = numberValue; }
      if (condition.operator === 'gte') filters.threadPriorityMin = numberValue;
      if (condition.operator === 'gt') filters.threadPriorityMin = numberValue + 1;
      if (condition.operator === 'lte') filters.threadPriorityMax = numberValue;
      if (condition.operator === 'lt') filters.threadPriorityMax = numberValue - 1;
    }
    if (condition.field === 'unreadCount' && Number.isFinite(numberValue)) {
      if (condition.operator === 'eq' && numberValue === 0) filters.hasUnread = false;
      if (['gt', 'gte'].includes(condition.operator) && numberValue <= 1) filters.hasUnread = true;
    }
    if (exact && condition.field === 'assignedHumanId' && textValue) filters.assignedHumanId = textValue;
    if (exact && condition.field === 'assignedTeamId' && textValue) filters.assignedTeamId = textValue;
    if (exact && condition.field === 'agentKey' && textValue) filters.ownerAgentKey = textValue;
    if (exact && condition.field === 'queue' && textValue) filters.queue = textValue;
    if (exact && condition.field === 'statusCode' && textValue) filters.status = textValue;
    if (exact && condition.field === 'channel' && textValue) filters.channel = textValue as KlmThread['primaryChannel'];
    if (exact && condition.field === 'priority' && textValue) filters.matterPriority = textValue;
    if (exact && condition.field === 'urgency' && textValue) filters.urgency = textValue;
    if (exact && condition.field === 'classification' && textValue) filters.classification = textValue;
    if (exact && condition.field === 'stage' && textValue) filters.columnId = textValue;
    if (exact && condition.field === 'leadSource' && textValue) filters.leadSource = textValue;
    if (exact && condition.field === 'flowStatus' && textValue && textValue !== 'none') filters.flowStatus = textValue;
    if (exact && condition.field === 'slaState' && ['overdue', 'on_time', 'without_sla'].includes(textValue)) filters.slaState = textValue as KlmThreadFilterQuery['slaState'];
    if (exact && condition.field === 'isFavorite') filters.isFavorite = Boolean(condition.value);
    if (['contains', 'eq'].includes(condition.operator) && condition.field === 'subtheme' && textValue) filters.demandType = textValue;
    if (condition.operator === 'contains' && condition.field === 'tags' && textValue) filters.tag = textValue;
    if (condition.field === 'lastMessageAt' && textValue) {
      if (['gt', 'gte', 'eq'].includes(condition.operator)) filters.lastMessageAfter = textValue;
      if (['lt', 'lte'].includes(condition.operator)) filters.lastMessageBefore = textValue;
    }
  });
  return filters;
}

function ContactList({
  contacts,
  selectedId,
  onSelect,
  filter,
  onFilter,
  query,
  onQuery,
  isLoading,
  isError,
  onRetry,
  campaignId,
  personalizedFilter,
  onPersonalizedFilter,
  sort,
  onSort,
  canRateLeads,
  ratingPendingThreadId,
  onRateLead,
  onOpenCase,
  onOpenOperations,
  channelConnectionState,
}: {
  contacts: Contact[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  filter: string;
  onFilter: (value: string) => void;
  query: string;
  onQuery: (value: string) => void;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  campaignId?: string | null;
  personalizedFilter: PersonalizedFilterDefinition;
  onPersonalizedFilter: (definition: PersonalizedFilterDefinition) => void;
  sort: ConversationSort;
  onSort: (sort: ConversationSort) => void;
  canRateLeads: boolean;
  ratingPendingThreadId: string | null;
  onRateLead: (contact: Contact, stars: number) => void;
  onOpenCase: (threadId: string) => void;
  onOpenOperations: (threadId: string) => void;
  channelConnectionState: 'loading' | 'connected' | 'disconnected' | 'unavailable';
}) {
  const personalizedFields = useMemo<PersonalizedFilterField<Contact>[]>(() => [
    { key: 'name', label: 'Nome', type: 'text' },
    { key: 'conversationKind', label: 'Tipo de conversa', type: 'select', options: [{ value: 'individual', label: 'Individual' }, { value: 'group', label: 'Grupo WhatsApp' }] },
    { key: 'score', label: 'Score do lead', type: 'number' },
    { key: 'assignedHumanId', label: 'Atendente humano', type: 'select', options: Array.from(new Map(contacts.filter((item) => item.assignedHumanId).map((item) => [item.assignedHumanId!, { value: item.assignedHumanId!, label: item.attendantLabel }])).values()) },
    { key: 'assignedTeamId', label: 'Equipe', type: 'select', options: Array.from(new Map(contacts.filter((item) => item.assignedTeamId).map((item) => [item.assignedTeamId!, { value: item.assignedTeamId!, label: item.teamLabel }])).values()) },
    { key: 'agentKey', label: 'Agente de IA responsável', type: 'select', options: Array.from(new Map(contacts.filter((item) => item.agentKey).map((item) => [item.agentKey!, { value: item.agentKey!, label: item.agentKey!.replaceAll('_', ' ') }])).values()) },
    { key: 'subtheme', label: 'Tipo / subtema', type: 'text' },
    { key: 'object', label: 'Demanda / objeto', type: 'text' },
    { key: 'statusCode', label: 'Status do atendimento', type: 'select', options: [['open', 'Aberto'], ['waiting_documents', 'Aguardando documentos'], ['waiting_human', 'Aguardando humano'], ['human_active', 'Humano atendendo'], ['closed', 'Encerrado'], ['blocked', 'Bloqueado']].map(([value, label]) => ({ value, label })) },
    { key: 'priority', label: 'Prioridade do caso', type: 'select', options: [['alta', 'Alta'], ['media', 'Média'], ['baixa', 'Baixa']].map(([value, label]) => ({ value, label })) },
    { key: 'urgency', label: 'Urgência', type: 'select', options: [['urgente', 'Urgente'], ['normal', 'Normal'], ['baixa', 'Baixa']].map(([value, label]) => ({ value, label })) },
    { key: 'classification', label: 'Classificação', type: 'select', options: ['Premium', 'Forte', 'Bom', 'Frio'].map((value) => ({ value, label: value })) },
    { key: 'stage', label: 'Etapa do funil', type: 'select', options: Array.from(new Map(contacts.map((item) => [item.stage, { value: item.stage, label: item.stage.replaceAll('_', ' ') }])).values()) },
    { key: 'microStatus', label: 'Microstatus', type: 'text' },
    { key: 'leadSource', label: 'Origem', type: 'select', options: Array.from(new Map(contacts.filter((item) => item.leadSource).map((item) => [item.leadSource, { value: item.leadSource, label: item.leadSource.replaceAll('_', ' ') }])).values()) },
    { key: 'tags', label: 'Tags', type: 'text', getValue: (item) => item.tags },
    { key: 'queue', label: 'Fila', type: 'select', options: Array.from(new Map(contacts.map((item) => [item.queue, { value: item.queue, label: item.queue.replaceAll('_', ' ') }])).values()) },
    { key: 'threadPriority', label: 'Prioridade operacional', type: 'number' },
    { key: 'unreadCount', label: 'Mensagens não lidas', type: 'number' },
    { key: 'flowStatus', label: 'Estado do fluxo', type: 'select', options: [['active', 'Em execução'], ['paused', 'Pausado'], ['waiting_human', 'Aguardando humano'], ['completed', 'Concluído'], ['failed', 'Falhou'], ['none', 'Sem fluxo']].map(([value, label]) => ({ value, label })) },
    { key: 'slaState', label: 'Prazo de resposta', type: 'select', options: [['overdue', 'Resposta atrasada'], ['on_time', 'No prazo'], ['without_sla', 'Sem prazo definido']].map(([value, label]) => ({ value, label })) },
    { key: 'responseState', label: 'Estado da resposta', type: 'select', options: [['cliente_aguardando_escritorio', 'Cliente aguarda o escritório'], ['escritorio_aguardando_cliente', 'Escritório aguarda o cliente'], ['respondido', 'Respondido'], ['sem_mensagem', 'Sem mensagem']].map(([value, label]) => ({ value, label })) },
    { key: 'responseWaitSeconds', label: 'Tempo sem resposta (segundos)', type: 'number' },
    { key: 'responseOverdue', label: 'Resposta atrasada', type: 'boolean' },
    { key: 'lastMessageAt', label: 'Última mensagem', type: 'date' },
    { key: 'isFavorite', label: 'Favorito', type: 'boolean' },
    {
      key: 'broadcastState',
      label: 'Estado do disparo',
      type: 'select',
      options: [
        { value: 'none', label: 'Sem disparo' },
        { value: 'awaiting_reply', label: 'Disparo sem retorno' },
        { value: 'replied', label: 'Disparo respondido' },
        { value: 'delivery_failed', label: 'Falha de entrega' },
      ],
    },
    {
      key: 'channel',
      label: 'Canal',
      type: 'select',
      options: [
        ['whatsapp', 'WhatsApp'], ['instagram', 'Instagram'], ['messenger', 'Messenger'],
        ['facebook', 'Facebook'], ['tiktok', 'TikTok'], ['email', 'E-mail'],
        ['form', 'Formulário'], ['webchat', 'Chat do site'], ['audio', 'Áudio'],
      ].map(([value, label]) => ({ value, label })),
    },
  ], [contacts]);
  const visibleContacts = useMemo(() => contacts.filter((contact) => {
    const normalizedQuery = query.trim().toLocaleLowerCase('pt-BR');
    const matchesQuery = !normalizedQuery || `${contact.name} ${contact.subtheme} ${contact.object} ${contact.status}`.toLocaleLowerCase('pt-BR').includes(normalizedQuery);
    if (!matchesQuery) return false;
    if (campaignId && !contact.broadcastCampaignIds.includes(campaignId)) return false;
    const matchesPreset = filter === 'todos'
      || (filter === 'medicamento' && contact.subtheme.toLowerCase().includes('medicamento'))
      || (filter === 'urgente' && contact.score >= 90)
      || (filter === 'docs' && contact.status.toLowerCase().includes('doc'))
      || (filter === 'com_disparo' && contact.broadcastState !== 'none')
      || (filter === 'sem_disparo' && contact.broadcastState === 'none')
      || (filter === 'disparo_sem_retorno' && contact.broadcastState === 'awaiting_reply')
      || (filter === 'cliente_aguardando' && contact.responseState === 'cliente_aguardando_escritorio')
      || (filter === 'escritorio_aguardando' && contact.responseState === 'escritorio_aguardando_cliente')
      || (filter === 'resposta_atrasada' && contact.responseOverdue);
    return matchesPreset && matchesPersonalizedFilter(contact, personalizedFilter, personalizedFields);
  }).sort((left, right) => {
    if (sort === 'score') return right.score - left.score;
    if (sort === 'response_wait') return (right.responseWaitSeconds ?? -1) - (left.responseWaitSeconds ?? -1);
    if (sort === 'recent') return String(right.lastMessageAt ?? '').localeCompare(String(left.lastMessageAt ?? ''));
    if (sort === 'unread') return right.unreadCount - left.unreadCount || right.score - left.score;
    if (sort === 'sla') {
      const leftDue = left.slaState === 'without_sla' ? Number.POSITIVE_INFINITY : Number(left.slaState === 'overdue' ? 0 : 1);
      const rightDue = right.slaState === 'without_sla' ? Number.POSITIVE_INFINITY : Number(right.slaState === 'overdue' ? 0 : 1);
      return leftDue - rightDue;
    }
    if (sort === 'name') return left.name.localeCompare(right.name, 'pt-BR');
    return 0;
  }), [campaignId, contacts, filter, personalizedFields, personalizedFilter, query, sort]);

  return (
    <KlmPanel className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="border-b border-border p-3">
        <div className="flex items-center justify-between">
          <div className="min-w-0"><h3 className="truncate text-sm font-semibold">Atendimentos</h3><p className="truncate text-2xs text-muted-foreground">{contacts.length} conversa{contacts.length === 1 ? '' : 's'} · {contacts.filter((contact) => contact.status.includes('aguardando humano')).length} aguardando humano</p></div>
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <div className="relative min-w-0 flex-1">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={query} onChange={(event) => onQuery(event.target.value)} disabled={isLoading || isError || !contacts.length} className="klm-touch-text-field h-8 bg-secondary pl-8 text-2xs" placeholder="Buscar atendimento" />
          </div>
          <PersonalizedFilterBuilder
            scope="conversations.inbox"
            fields={personalizedFields}
            value={personalizedFilter}
            onChange={onPersonalizedFilter}
            title="Filtros avançados da caixa de entrada"
            description="Combine score, atendente, equipe, tipo, prioridade, etapa, canal, origem, tags, prazo de resposta, estado e segundos de espera. Salve a visão para reutilizar."
            triggerLabel="Filtros avançados"
            compact
            disabled={isError}
          />
          <Select value={sort} onValueChange={(value: ConversationSort) => onSort(value)}>
            <SelectTrigger className="h-8 w-8 shrink-0 px-0 [&>svg:last-child]:hidden" aria-label="Ordenar conversas" title="Ordenar conversas"><ArrowUpDown className="mx-auto h-3.5 w-3.5" /><span className="sr-only"><SelectValue /></span></SelectTrigger>
            <SelectContent>
              <SelectItem value="operational">Ordem operacional</SelectItem><SelectItem value="score">Maior score</SelectItem>
              <SelectItem value="response_wait">Maior tempo sem resposta</SelectItem>
              <SelectItem value="recent">Mais recentes</SelectItem><SelectItem value="unread">Mais não lidas</SelectItem>
              <SelectItem value="sla">Respostas atrasadas primeiro</SelectItem><SelectItem value="name">Nome A-Z</SelectItem>
            </SelectContent>
          </Select>
          {personalizedFilter.conditions.length > 0 && <Button type="button" variant="ghost" size="icon" className="h-8 w-8 shrink-0" aria-label="Limpar filtros avançados" title="Limpar filtros avançados" onClick={() => onPersonalizedFilter(EMPTY_PERSONALIZED_FILTER)}><FilterX className="h-3.5 w-3.5" /></Button>}
        </div>
        <div className="mt-2 flex flex-nowrap gap-1 overflow-x-auto pb-1 scrollbar-thin">
          {[
            ['todos', 'Todos'],
            ['com_disparo', 'Com disparo'],
            ['sem_disparo', 'Sem disparo'],
            ['disparo_sem_retorno', 'Disparo sem retorno'],
            ['cliente_aguardando', 'Cliente aguarda'],
            ['escritorio_aguardando', 'Escritório aguarda'],
            ['resposta_atrasada', 'Resposta atrasada'],
            ['medicamento', 'Med.'],
            ['urgente', 'Urgentes'],
            ['docs', 'Docs'],
          ].map(([value, label]) => (
            <button key={value} disabled={isLoading || isError || !contacts.length} className={cn('min-h-5 shrink-0 rounded-full border px-2 py-0.5 text-2xs font-semibold disabled:cursor-not-allowed disabled:opacity-50', filter === value ? 'border-primary bg-primary text-white' : 'border-border bg-card text-muted-foreground')} onClick={() => onFilter(value)}>{label}</button>
          ))}
        </div>
      </div>
      <div data-testid="conversation-list-scroll" className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2 scrollbar-thin">
        {isLoading && <div className="space-y-2 p-1">{Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-14 w-full rounded-md" />)}</div>}
        {isError && <div className="px-3 py-8 text-center"><TriangleAlert className="mx-auto h-4 w-4 text-[#a53e3c]" /><p className="mt-2 text-2xs font-semibold">Não foi possível carregar as conversas</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">Nenhum dado alternativo será exibido.</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={onRetry}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button></div>}
        {!isLoading && !isError && contacts.length === 0 && <div className="px-3 py-10 text-center"><MessageSquareText className="mx-auto h-5 w-5 text-muted-foreground" /><p className="mt-2 text-2xs font-semibold">Nenhuma conversa ainda</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{channelConnectionState === 'connected' ? 'WhatsApp conectado. Novos atendimentos aparecerão aqui assim que a primeira mensagem chegar.' : channelConnectionState === 'loading' ? 'Verificando os canais conectados deste workspace.' : channelConnectionState === 'unavailable' ? 'Não foi possível verificar os canais deste workspace agora.' : 'Conecte um canal para receber novos atendimentos.'}</p>{channelConnectionState !== 'loading' && <Button asChild variant="outline" size="sm" className="mt-3 h-8 text-2xs"><Link to="/app/conexoes">{channelConnectionState === 'connected' ? 'Gerenciar conexão' : channelConnectionState === 'unavailable' ? 'Verificar conexões' : 'Conectar um canal'}</Link></Button>}</div>}
        {!isLoading && !isError && contacts.length > 0 && visibleContacts.length === 0 && <div className="px-3 py-10 text-center text-2xs text-muted-foreground">Nenhuma conversa corresponde aos filtros.</div>}
        {visibleContacts.map((contact) => {
          const leadStars = Math.max(0, Math.min(5, Math.round(contact.score / 20)));
          const ratingPending = ratingPendingThreadId === contact.id;
          const bellTone = contact.responseWaitSeconds === null || contact.responseWaitSeconds <= 300
            ? 'text-[#168244] bg-[#e5f6ea] border-[#a8dfba]'
            : contact.responseWaitSeconds <= 600
              ? 'text-[#8b6200] bg-[#fff4d8] border-[#e7c66f]'
              : 'text-[#b42318] bg-[#fff0ef] border-[#f3b4af]';
          return (
          <ContextMenu key={contact.id}>
            <ContextMenuTrigger asChild>
              <div
                data-testid="conversation-contact"
                role="button"
                tabIndex={0}
                aria-label={`Abrir conversa de ${contact.name}`}
                aria-pressed={selectedId === contact.id}
                onClick={() => onSelect(contact.id)}
                onDoubleClick={() => onOpenCase(contact.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(contact.id);
                  }
                }}
                title="Clique para abrir a conversa; duplo clique para editar no Caso 360"
                className={cn('relative w-full min-w-0 rounded-lg border p-2.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2', selectedId === contact.id ? 'border-[#0b7435] bg-[#eaf7ee] text-[#102418] shadow-sm ring-2 ring-[#0b7435]/15 dark:bg-[#0b2b19] dark:text-white' : 'border-border bg-secondary hover:border-primary')}
              >
            <div className="flex min-w-0 items-start gap-2.5">
              <ContactAvatar
                name={contact.name}
                initials={contact.initials}
                avatarUrl={contact.avatarUrl}
                className="h-8 w-8"
                fallbackClassName={selectedId === contact.id ? 'text-white' : 'text-primary'}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 break-words text-sm font-semibold leading-5">{contact.name}</span>
                  <span
                    className={cn('inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border', bellTone)}
                    title={contact.responseWaitSeconds === null ? 'Atendimento em dia' : 'Faixa de tempo de resposta. Abra a conversa para ver o tempo.'}
                    aria-label={contact.responseWaitSeconds === null ? 'Atendimento em dia' : 'Indicador de tempo de resposta; abra a conversa para ver o tempo'}
                  >
                    <Bell className="h-3.5 w-3.5 fill-current" />
                  </span>
                </div>
                <dl className={cn('mt-1 grid min-w-0 grid-cols-[52px_minmax(0,1fr)] gap-x-1.5 gap-y-0.5 text-2xs leading-4', selectedId === contact.id ? 'text-[#264e34] dark:text-white/80' : 'text-muted-foreground')}>
                  <dt className="font-medium opacity-75">Área</dt>
                  <dd className="min-w-0 break-words font-semibold text-foreground">{contact.legalArea}</dd>
                  <dt className="font-medium opacity-75">Subnicho</dt>
                  <dd className="min-w-0 break-words">{contact.subtheme}</dd>
                  <dt className="font-medium opacity-75">Produto</dt>
                  <dd className="min-w-0 break-words">{contact.object}</dd>
                </dl>
                <PermissionGate module="mensagens" action="send_message" actionLabel="alterar a classificação do lead" variant="readonly">
                <div
                  role="group"
                  aria-label={`Potencial de ${contact.name}: ${leadStars} de 5 estrelas`}
                  className="pointer-events-auto mt-1.5 flex min-w-0 items-center gap-1"
                >
                  <span className={cn('mr-0.5 text-2xs font-semibold', selectedId === contact.id ? 'text-[#315d40] dark:text-white/75' : 'text-muted-foreground')}>Potencial</span>
                  {[0, 1, 2, 3, 4, 5].map((stars) => (
                    <button
                      key={stars}
                      type="button"
                      aria-label={stars === 0 ? `Zerar potencial de ${contact.name}` : `Classificar potencial de ${contact.name} com ${stars} estrela${stars === 1 ? '' : 's'}`}
                      aria-pressed={leadStars === stars}
                      title={stars === 0 ? 'Sem classificação' : `${stars} de 5 estrelas`}
                      disabled={!canRateLeads || ratingPending}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (stars !== leadStars) onRateLead(contact, stars);
                      }}
                      onDoubleClick={(event) => event.stopPropagation()}
                      className={cn(
                        'inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-55',
                        stars === 0
                          ? leadStars === 0 ? 'bg-[#031121] text-white' : 'text-muted-foreground hover:bg-black/5 dark:hover:bg-white/10'
                          : stars <= leadStars ? 'text-[#b98516]' : 'text-[#aeb4ba] hover:text-[#b98516]',
                      )}
                    >
                      {stars === 0 ? <span className="text-2xs font-bold">0</span> : <Star className={cn('h-3 w-3', stars <= leadStars && 'fill-current')} />}
                    </button>
                  ))}
                  {ratingPending ? <LoaderCircle aria-label="Salvando potencial" className="ml-0.5 h-3 w-3 animate-spin text-muted-foreground" /> : null}
                </div>
                </PermissionGate>
              </div>
            </div>
              </div>
            </ContextMenuTrigger>
            <ContextMenuContent className="w-52">
              <ContextMenuLabel className="truncate text-2xs">{contact.name}</ContextMenuLabel>
              <ContextMenuItem className="text-xs" onSelect={() => onSelect(contact.id)}>
                <MessagesSquare className="mr-2 h-3.5 w-3.5" />Abrir conversa
              </ContextMenuItem>
              <ContextMenuItem className="text-xs" onSelect={() => onOpenCase(contact.id)}>
                <PanelRight className="mr-2 h-3.5 w-3.5" />Editar dados no Caso 360
              </ContextMenuItem>
              <ContextMenuSeparator />
              <ContextMenuItem className="text-xs" onSelect={() => onOpenOperations(contact.id)}>
                <UsersRound className="mr-2 h-3.5 w-3.5" />Transferir ou operar
              </ContextMenuItem>
            </ContextMenuContent>
          </ContextMenu>
          );
        })}
      </div>
    </KlmPanel>
  );
}

function MessageBubble({
  message,
  onOpenRag,
  onOpenEvidence,
  onReply,
  replyAvailable,
  replyUnavailableMessage,
}: {
  message: Message;
  onOpenRag: (path: string) => void;
  onOpenEvidence: (messageId: string) => void;
  onReply: (message: Message) => void;
  replyAvailable: boolean;
  replyUnavailableMessage: string;
}) {
  const isClient = message.role === 'client';
  const isHuman = message.role === 'human';
  const isInternal = message.role === 'internal';

  if (isInternal) {
    return (
      <div className="flex justify-center" data-message-profile="internal">
        <div className="w-full max-w-[94%] rounded-lg border border-amber-300/80 bg-amber-50 px-3 py-2.5 text-amber-950 shadow-sm dark:border-amber-700/70 dark:bg-amber-950/30 dark:text-amber-100">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1 text-2xs font-semibold uppercase">
              <LockKeyhole className="h-3 w-3" />
              Nota interna
            </span>
            <span className="text-2xs font-medium">{message.author}</span>
            <span className="ml-auto text-2xs opacity-65">{message.time}</span>
          </div>
          <p className="text-sm leading-5">{message.text}</p>
          <div className="mt-2 flex items-center gap-1 text-2xs font-medium opacity-70">
            <ShieldOff className="h-3 w-3" />
            <span>Visível somente para a equipe · não enviada ao cliente</span>
            <button
              type="button"
              className="ml-auto inline-flex items-center gap-1 font-semibold hover:underline disabled:cursor-not-allowed disabled:opacity-50 disabled:no-underline"
              disabled={!replyAvailable}
              onClick={() => onReply(message)}
              title={replyAvailable ? 'Responder com referência a esta nota' : replyUnavailableMessage}
            >
              <MessageCircleReply className="h-3 w-3" />Responder
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-1 font-semibold hover:underline"
              onClick={() => onOpenEvidence(message.id)}
              title="Ver histórico e integridade deste registro"
            >
              <ShieldCheck className="h-3 w-3" />Histórico
            </button>
          </div>
        </div>
      </div>
    );
  }

  const profile = isClient ? 'client' : isHuman ? 'lawyer' : 'bot';
  const ProfileIcon = isClient ? CircleUserRound : isHuman ? UserRoundCheck : Bot;
  return (
    <div className={cn('flex', isClient ? 'justify-start' : 'justify-end')} data-message-profile={profile}>
      <div
        className={cn(
          'max-w-[88%] rounded-lg border px-3 py-2.5 shadow-sm sm:max-w-[76%]',
          isClient && 'border-border bg-secondary dark:border-[#3a3f47] dark:bg-[#1a1d22]',
          isHuman && 'border-[#1a9948]/55 bg-[#d9fdd3] dark:border-emerald-800 dark:bg-emerald-950/70',
          !isClient && !isHuman && 'border-[#d5d9dd] bg-[#eef0f2] dark:border-[#34383d] dark:bg-[#202326]',
        )}
      >
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          <KlmPill tone={isClient ? 'gold' : isHuman ? 'green' : 'muted'}>
            <ProfileIcon className="mr-1 h-3 w-3" />
            {message.author}
          </KlmPill>
          {message.routeLabel ? <span className="max-w-[190px]" title={message.routeLabel}><KlmPill tone={message.channel === 'whatsapp' ? 'green' : ['instagram', 'facebook', 'messenger'].includes(message.channel ?? '') ? 'blue' : message.channel === 'tiktok' ? 'gold' : 'muted'} className="max-w-full truncate">{message.routeLabel}</KlmPill></span> : null}
          <span className="ml-auto text-2xs text-muted-foreground">{message.time}</span>
        </div>
        {message.replyTo ? (
          <div className="mb-2 min-w-0 rounded-md border-l-2 border-[#0057d8] bg-black/5 px-2 py-1.5 dark:bg-white/5" data-testid="message-reply-reference">
            <p className="truncate text-2xs font-semibold text-[#0057d8] dark:text-primary">Em resposta a {message.replyTo.authorLabel}</p>
            <p className="truncate text-2xs text-muted-foreground" title={message.replyTo.body}>{message.replyTo.body}</p>
          </div>
        ) : null}
        <p className="text-sm leading-5 text-foreground">{message.text}</p>
        {message.attachments?.length ? (
          <div className="mt-2 space-y-1.5 border-t border-current/10 pt-2">
            {message.attachments.map((attachment) => (
              <div key={attachment.id} className="flex min-w-0 items-center gap-2 rounded-md border border-border/80 bg-card/75 px-2 py-1.5 text-2xs">
                <Paperclip className="h-3 w-3 shrink-0 text-[#0057d8]" />
                <span className="min-w-0 flex-1 truncate" title={attachment.name}>{attachment.name}</span>
                <span className="shrink-0 text-muted-foreground">{attachment.fileType}</span>
                {attachment.localUrl && <a href={attachment.localUrl} target="_blank" rel="noreferrer" className="shrink-0 font-semibold text-[#0057d8] hover:underline">Abrir</a>}
              </div>
            ))}
            {message.ragPath && <button type="button" onClick={() => onOpenRag(message.ragPath!)} className="inline-flex items-center text-2xs font-semibold text-[#0057d8] hover:underline"><Database className="mr-1 h-3 w-3" />Ver fonte no RAG do caso</button>}
          </div>
        ) : null}
        <div className="mt-1.5 flex flex-wrap items-center justify-end gap-1 text-2xs text-muted-foreground">
          {message.contentLifecycleState === 'edited' ? <KlmPill tone="gold">Editada · {message.evidenceRevisionCount} versões</KlmPill> : null}
          {message.contentLifecycleState === 'deleted' ? <KlmPill tone="gold">Apagada no canal · original preservado</KlmPill> : null}
          {message.contentLifecycleState === 'provider_unsupported' ? <KlmPill tone="gold">Alteração sinalizada pelo provedor</KlmPill> : null}
          {message.note && <span>{message.note}</span>}
          {!isClient && message.deliveryState === 'indexed' && <CheckCheck className="h-3 w-3 text-[#1a9948]" />}
          {!isClient && message.deliveryState === 'local' && <Clock3 className="h-3 w-3 text-[#0057d8]" />}
          {!isClient && message.deliveryState === 'failed' && <XCircle className="h-3 w-3 text-red-600" />}
          <button
            type="button"
            className="ml-1 inline-flex items-center gap-1 font-semibold text-muted-foreground hover:text-foreground hover:underline disabled:cursor-not-allowed disabled:opacity-50 disabled:no-underline"
            disabled={!replyAvailable}
            onClick={() => onReply(message)}
            title={replyAvailable ? 'Responder com referência a esta mensagem' : replyUnavailableMessage}
          >
            <MessageCircleReply className="h-3 w-3" />Responder
          </button>
          <button
            type="button"
            className="ml-1 inline-flex items-center gap-1 font-semibold text-[#0057d8] hover:underline dark:text-primary"
            onClick={() => onOpenEvidence(message.id)}
            title="Ver original, alterações, hashes e arquivos preservados"
          >
            <ShieldCheck className="h-3 w-3" />
            {message.evidenceEventCount > 0 ? `${message.evidenceEventCount} registros` : 'Integridade'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ConversationInspector({
  contact,
  internalEntries,
  onTool,
  threadId,
  matterId,
  canEdit,
  canManageFlows,
  onUseResponse,
  onCollapse,
}: {
  contact: Contact;
  internalEntries: Array<[string, string]>;
  onTool: (tool: string) => void;
  threadId?: string | null;
  matterId?: string | null;
  canEdit: boolean;
  canManageFlows: boolean;
  onUseResponse: (content: string) => void;
  onCollapse: () => void;
}) {
  return (
    <KlmPanel data-testid="conversation-inspector" className="flex h-full min-h-0 flex-col overflow-hidden">
      <Tabs defaultValue="case" className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b p-2.5">
          <TabsList className="grid h-8 min-w-0 flex-1 grid-cols-2 bg-secondary p-1">
            <TabsTrigger value="case" className="text-2xs">Caso 360</TabsTrigger>
            <TabsTrigger value="operations" className="text-2xs"><MessageSquareText className="mr-1.5 h-3 w-3" />Operação</TabsTrigger>
          </TabsList>
          <Button type="button" variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={onCollapse} title="Recolher Caso 360" aria-label="Recolher painel Caso 360">
            <PanelRightClose className="h-4 w-4" />
          </Button>
        </div>
        <TabsContent value="case" className="m-0 min-h-0 flex-1 overflow-y-auto">
          <ConversationCasePanel threadId={threadId} matterId={matterId} contact={contact} internalEntries={internalEntries} onTool={onTool} />
        </TabsContent>
        <TabsContent value="operations" className="m-0 min-h-0 flex-1 overflow-y-auto">
          <NativeInboxRoutingPanel threadId={threadId} canEdit={canEdit} canManageFlows={canManageFlows} onUseResponse={onUseResponse} />
        </TabsContent>
      </Tabs>
    </KlmPanel>
  );
}

function useWideConversationInspector() {
  const [isWide, setIsWide] = useState(false);

  useEffect(() => {
    const media = window.matchMedia('(min-width: 1440px)');
    const update = () => setIsWide(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  return isWide;
}

export default function ConversationsPage() {
  const { can, state } = useAppStore();
  const navigate = useNavigate();
  const canSend = can('mensagens', 'send_message');
  const canManageFlows = can('automations', 'manage');
  const canEditFunnel = can('kanban', 'edit');
  const canManageConversationParticipants = can('users', 'manage');
  const canViewResponseMetrics = can('mensagens', 'view');
  const activeWorkspaceRole = state?.session?.activeRole ?? state?.currentUser?.role ?? '';
  const canManageStaffAvailability = ['workspace_owner', 'workspace_admin', 'legal_manager'].includes(activeWorkspaceRole);
  const canApproveStaffAvailability = ['workspace_owner', 'workspace_admin', 'legal_manager'].includes(activeWorkspaceRole);
  const isCompactConversation = useIsMobile(1024);
  const showDesktopInspector = useWideConversationInspector();
  const [desktopInspectorCollapsed, setDesktopInspectorCollapsed] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedThreadId = searchParams.get('thread');
  const queryClient = useQueryClient();
  const [newConversationOpen, setNewConversationOpen] = useState(false);
  const [filter, setFilter] = useState(() => {
    const requested = searchParams.get('broadcast');
    if (requested === 'with') return 'com_disparo';
    if (requested === 'without') return 'sem_disparo';
    if (requested === 'awaiting_reply') return 'disparo_sem_retorno';
    return 'todos';
  });
  const [query, setQuery] = useState('');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [conversationSort, setConversationSort] = useState<ConversationSort>('operational');
  const [responseClock, setResponseClock] = useState(() => Date.now());
  const serverThreadFilters = useMemo(
    () => advancedThreadFilters(personalizedFilter, conversationSort),
    [conversationSort, personalizedFilter],
  );
  const usesServerFilters = personalizedFilter.match === 'all' && personalizedFilter.conditions.some((condition) => (
    ['score', 'threadPriority', 'unreadCount', 'assignedHumanId', 'assignedTeamId', 'agentKey', 'queue', 'statusCode', 'channel', 'priority', 'urgency', 'classification', 'stage', 'leadSource', 'flowStatus', 'slaState', 'isFavorite', 'subtheme', 'tags', 'lastMessageAt'].includes(condition.field)
  )) || ['score', 'recent', 'unread', 'sla'].includes(conversationSort);
  const threadsQuery = useQuery({
    queryKey: usesServerFilters ? ['klm', 'threads', 'filtered', serverThreadFilters] : ['klm', 'threads'],
    queryFn: usesServerFilters ? () => klmApi.filteredThreads(serverThreadFilters) : klmApi.threads,
  });
  const whatsappConnectionsQuery = useQuery({
    queryKey: ['whatsapp-connections-page'],
    queryFn: fetchWhatsAppConnections,
    enabled: threadsQuery.isSuccess,
    retry: false,
  });
  const channelConnectionState: 'loading' | 'connected' | 'disconnected' | 'unavailable' =
    whatsappConnectionsQuery.isPending
      ? 'loading'
      : whatsappConnectionsQuery.isError
        ? 'unavailable'
        : (whatsappConnectionsQuery.data ?? []).some((connection) => connection.status === 'connected')
          ? 'connected'
          : 'disconnected';
  const responseMetricsQuery = useQuery({
    queryKey: ['klm', 'conversation-response-metrics', 'inbox'],
    queryFn: () => klmApi.conversationResponseMetrics({
      period: 'all_time',
      preset: 'all',
      sortBy: 'duration',
      sortDirection: 'desc',
      limit: 500,
    }),
    enabled: canViewResponseMetrics,
    retry: false,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const followUpsQuery = useQuery({ queryKey: ['klm', 'follow-ups'], queryFn: klmApi.followUps });
  const flowsQuery = useQuery({ queryKey: ['klm', 'flows'], queryFn: klmApi.flows });
  const agentsQuery = useQuery({ queryKey: ['klm', 'agents'], queryFn: klmApi.agents, retry: false });
  const [selectedId, setSelectedId] = useState<string | null>(() => requestedThreadId);
  // Seleção explícita (clique, nova conversa) ainda não refletida em `?thread=`: a navegação do
  // React Router é uma transição e, sob CPU lenta, leva segundos para propagar `searchParams`.
  // Nesse intervalo o efeito de seleção não pode ler "sem parâmetro" como "voltar à lista".
  const pendingSelectionRef = useRef<string | null>(null);
  const [newConversationConnectionId, setNewConversationConnectionId] = useState('');
  const [newConversationTemplateId, setNewConversationTemplateId] = useState('');
  const [newConversationPhone, setNewConversationPhone] = useState('');
  const normalizedNewConversationPhone = normalizeWhatsAppPhone(newConversationPhone);
  const [newConversationName, setNewConversationName] = useState('');
  const [newConversationHeaderParameters, setNewConversationHeaderParameters] = useState('');
  const [newConversationBodyParameters, setNewConversationBodyParameters] = useState('');
  const [humanMode, setHumanMode] = useState(false);
  const [composeMode, setComposeMode] = useState<'outbound' | 'internal'>('outbound');
  const [composerAssistantOpen, setComposerAssistantOpen] = useState(false);
  const [messagesByThread, setMessagesByThread] = useState<Record<string, Message[]>>({});
  const [outboundDrafts, setOutboundDrafts] = useState<Record<string, string>>({});
  const [internalDrafts, setInternalDrafts] = useState<Record<string, string>>({});
  const [referencesByThread, setReferencesByThread] = useState<Record<string, ConversationMessageReference | null>>({});
  const [mentionsByThread, setMentionsByThread] = useState<Record<string, ConversationMentionTarget[]>>({});
  const [routeByThread, setRouteByThread] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeAction, setNoticeAction] = useState<ConversationNoticeAction | null>(null);
  const [caseOpen, setCaseOpen] = useState(false);
  const [operationsOpen, setOperationsOpen] = useState(false);
  const [officialMetaGroupsOpen, setOfficialMetaGroupsOpen] = useState(false);
  const [broadcastsOpen, setBroadcastsOpen] = useState(false);
  const [templatePickerOpen, setTemplatePickerOpen] = useState(false);
  const [audioOpen, setAudioOpen] = useState(false);
  const [proposalPreviewPath, setProposalPreviewPath] = useState<string | null>(null);
  const [contextWorkspace, setContextWorkspace] = useState<ConversationContextWorkspace | null>(null);
  const [flowSheetOpen, setFlowSheetOpen] = useState(false);
  const [documentRequestOpen, setDocumentRequestOpen] = useState(false);
  const [proposalPreparationOpen, setProposalPreparationOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [messageScheduleOpen, setMessageScheduleOpen] = useState(false);
  const [richMessageOpen, setRichMessageOpen] = useState(false);
  const [metaWindowConfirmationOpen, setMetaWindowConfirmationOpen] = useState(false);
  const [followUpEditorOpen, setFollowUpEditorOpen] = useState(false);
  const [followUpDueAtDraft, setFollowUpDueAtDraft] = useState('');
  const [followUpReasonDraft, setFollowUpReasonDraft] = useState('');
  const [availabilityOpen, setAvailabilityOpen] = useState(false);
  const [evidenceMessageId, setEvidenceMessageId] = useState<string | null>(null);
  const [flowSearch, setFlowSearch] = useState('');
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [cadenceClock, setCadenceClock] = useState(() => Date.now());
  const newConversationTemplatesQuery = useQuery({
    queryKey: ['whatsapp-meta-templates', 'conversation-start', newConversationConnectionId],
    queryFn: () => fetchMetaTemplates(newConversationConnectionId),
    enabled: newConversationOpen && Boolean(newConversationConnectionId),
    retry: false,
  });
  const approvedNewConversationTemplates = useMemo(() => (
    (newConversationTemplatesQuery.data ?? []).filter((template: MetaTemplate) => template.status === 'approved')
  ), [newConversationTemplatesQuery.data]);
  const selectedNewConversationTemplate = approvedNewConversationTemplates.find(
    (template) => template.id === newConversationTemplateId,
  );
  const selectedNewConversationTemplatePreview = useMemo(() => (
    (selectedNewConversationTemplate?.components ?? [])
      .map((component) => String(component.text ?? component.body ?? ''))
      .filter(Boolean)
      .join('\n')
  ), [selectedNewConversationTemplate]);
  const startConversationMutation = useMutation({
    mutationFn: () => startWhatsAppConversation({
      connectionId: newConversationConnectionId,
      metaTemplateId: newConversationTemplateId,
      phone: normalizedNewConversationPhone ?? newConversationPhone,
      contactName: newConversationName.trim() || undefined,
      headerParameters: newConversationHeaderParameters.split('\n').map((value) => value.trim()).filter(Boolean),
      bodyParameters: newConversationBodyParameters.split('\n').map((value) => value.trim()).filter(Boolean),
    }),
    onSuccess: async (conversation) => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      const canonicalThreadId = [...(conversation?.messages ?? [])]
        .reverse()
        .map((message) => message?.messageMetadata?.canonical?.threadId)
        .find((value) => typeof value === 'string' && value.length > 0);
      if (canonicalThreadId) {
        pendingSelectionRef.current = canonicalThreadId;
        setSelectedId(canonicalThreadId);
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.set('thread', canonicalThreadId);
          return next;
        });
      }
      setNewConversationOpen(false);
      setNotice('Template enviado e conversa criada na Inbox.');
    },
  });
  const responseMetricsByThread = useMemo(() => new Map(
    (responseMetricsQuery.data?.items ?? []).map((metric) => [metric.threadId, metric]),
  ), [responseMetricsQuery.data?.items]);
  const contacts = useMemo(() => {
    if (!threadsQuery.data?.length) return [];
    const casesByMatter = new Map((funnelQuery.data?.cards ?? []).map((card) => [card.id, card]));
    const statusLabels: Record<string, string> = { open: 'aberto', waiting_documents: 'aguardando documentos', waiting_human: 'aguardando humano', human_active: 'humano atendendo', closed: 'encerrado', blocked: 'bloqueado' };
    const statusRank: Record<string, number> = { waiting_human: 0, human_active: 1, waiting_documents: 2, open: 3, blocked: 8, closed: 9 };
    return [...threadsQuery.data].sort((left, right) => (statusRank[left.status] ?? 5) - (statusRank[right.status] ?? 5) || right.score - left.score).map((thread) => {
      const responseMetric = responseMetricsByThread.get(thread.id) ?? null;
      const matter = thread.matterId ? casesByMatter.get(thread.matterId) : undefined;
      const broadcast = thread.threadMetadata.broadcast && typeof thread.threadMetadata.broadcast === 'object'
        ? thread.threadMetadata.broadcast as Record<string, unknown>
        : {};
      const broadcastCampaignIds = Array.isArray(broadcast.campaignIds)
        ? broadcast.campaignIds.map(String)
        : broadcast.lastCampaignId
          ? [String(broadcast.lastCampaignId)]
          : [];
      const broadcastState: Contact['broadcastState'] = broadcastCampaignIds.length === 0
        ? 'none'
        : broadcast.deliveryFailed
          ? 'delivery_failed'
          : broadcast.awaitingReply
            ? 'awaiting_reply'
            : 'replied';
      const name = matter?.name ?? String(thread.threadMetadata.clientName ?? 'Cliente sem identificação');
      const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'CL';
      const avatar = resolveContactAvatar(thread.threadMetadata, thread.primaryChannel);
      const normalizedStatus = statusLabels[thread.status] ?? thread.status.replaceAll('_', ' ');
      const assignedHumanName = thread.threadMetadata.assignedHumanName ? String(thread.threadMetadata.assignedHumanName) : null;
      const assignedTeamName = thread.threadMetadata.assignedTeamName ? String(thread.threadMetadata.assignedTeamName) : null;
      const legalAreaKey = [
        matter?.practiceArea,
        thread.threadMetadata.legalAreaKey,
        thread.threadMetadata.practiceArea,
      ].find((area): area is string => typeof area === 'string' && Boolean(area.trim()))?.trim() ?? null;
      const slaState: Contact['slaState'] = !thread.slaDueAt
        ? 'without_sla'
        : new Date(thread.slaDueAt).getTime() < Date.now()
          ? 'overdue'
          : 'on_time';
      return {
        id: thread.id,
        initials,
        avatarUrl: avatar?.url ?? null,
        avatarSource: avatar?.source ?? null,
        name,
        legalAreaKey,
        legalArea: legalAreaKey ? legalAreaLabel(legalAreaKey) : 'Área não informada',
        subtheme: matter?.subject.replaceAll('_', ' ') ?? thread.subject?.replaceAll('_', ' ') ?? 'Subtema não informado',
        object: matter?.object ?? thread.objectName ?? 'Objeto não informado',
        status: normalizedStatus,
        lastAuthor: thread.ownerAgentKey?.replaceAll('_', ' ') ?? 'Sem responsável',
        version: thread.version,
        score: thread.score,
        channel: thread.primaryChannel,
        conversationKind: thread.threadMetadata.conversationKind === 'group' || Boolean(thread.threadMetadata.groupId ?? thread.threadMetadata.providerGroupId) ? 'group' : 'individual',
        unreadCount: thread.unreadCount,
        preview: thread.lastMessagePreview ?? 'Sem mensagem registrada',
        broadcastState,
        broadcastCampaignIds,
        broadcastTitle: broadcast.lastCampaignTitle ? String(broadcast.lastCampaignTitle) : undefined,
        statusCode: thread.status,
        assignedHumanId: thread.assignedHumanId,
        assignedTeamId: thread.assignedTeamId ?? null,
        attendantLabel: assignedHumanName ?? (thread.assignedHumanId ? matter?.owner ?? `Atendente ${thread.assignedHumanId.slice(0, 8)}` : 'Sem atendente humano'),
        teamLabel: assignedTeamName ?? (thread.assignedTeamId ? `Equipe ${thread.assignedTeamId.slice(0, 8)}` : 'Sem equipe'),
        agentKey: thread.ownerAgentKey,
        queue: thread.queue ?? 'new',
        threadPriority: thread.priority ?? 0,
        priority: matter?.priority ?? 'media',
        urgency: matter?.urgency ?? 'normal',
        classification: matter?.classification ?? 'Bom',
        stage: matter?.stage ?? 'sem_etapa',
        microStatus: matter?.microStatus ?? 'sem_status',
        leadSource: matter?.source ?? '',
        tags: matter?.tags ?? [],
        flowStatus: matter?.activeFlow?.status ?? 'none',
        slaState,
        lastMessageAt: thread.lastMessageAt,
        isFavorite: Boolean(thread.isFavorite),
        responseMetric,
        responseState: responseMetric?.state ?? null,
        responseWaitSeconds: responseMetric && (responseMetric.state === 'cliente_aguardando_escritorio' || responseMetric.state === 'escritorio_aguardando_cliente')
          ? conversationResponseWaitSeconds(responseMetric, responseClock)
          : null,
        responseOverdue: Boolean(responseMetric?.isOverdue),
        tone: thread.status === 'blocked' ? 'red' : thread.status === 'human_active' ? 'blue' : thread.score >= 85 ? 'green' : thread.score >= 70 ? 'gold' : 'muted',
        sensitiveDataHidden: Boolean(thread.sensitiveDataHidden || matter?.sensitiveDataHidden),
      } satisfies Contact;
    });
  }, [funnelQuery.data?.cards, responseClock, responseMetricsByThread, threadsQuery.data]);
  const applyContactFilter = (value: string, campaignId?: string) => {
    setFilter(value);
    const next = new URLSearchParams(searchParams);
    if (campaignId) next.set('campaign', campaignId);
    else next.delete('campaign');
    if (value === 'com_disparo') next.set('broadcast', 'with');
    else if (value === 'sem_disparo') next.set('broadcast', 'without');
    else if (value === 'disparo_sem_retorno') next.set('broadcast', 'awaiting_reply');
    else next.delete('broadcast');
    setSearchParams(next, { replace: true });
  };
  const routedSelectedId = requestedThreadId && contacts.some((contact) => contact.id === requestedThreadId)
    ? requestedThreadId
    : null;
  const resolvedSelectedId = routedSelectedId ?? selectedId ?? (!isCompactConversation ? contacts[0]?.id ?? null : null);
  const selected = contacts.find((contact) => contact.id === resolvedSelectedId) ?? contacts[0];
  const selectedName = selected?.name ?? '';
  const selectedThread = threadsQuery.data?.find((thread) => thread.id === resolvedSelectedId);
  const selectedThreadId = selectedThread?.id;
  const selectedResponseMetric = selectedThreadId ? responseMetricsByThread.get(selectedThreadId) ?? null : null;
  const availabilityTargetUserId = canManageStaffAvailability && selectedThread?.assignedHumanId
    ? selectedThread.assignedHumanId
    : state?.currentUser?.id ?? null;
  const availabilityTargetName = availabilityTargetUserId === selectedThread?.assignedHumanId
    ? selected?.attendantLabel ?? 'Atendente responsável'
    : state?.currentUser?.name || state?.currentUser?.email || 'Usuário do workspace';
  const staffAvailabilityStateQuery = useQuery({
    queryKey: ['klm', 'staff-availability', availabilityTargetUserId],
    queryFn: () => klmApi.staffAvailabilityState(availabilityTargetUserId!),
    enabled: Boolean(availabilityOpen && availabilityTargetUserId),
  });
  const staffEffectiveAvailabilityQuery = useQuery({
    queryKey: ['klm', 'staff-availability-effective', availabilityTargetUserId],
    queryFn: () => klmApi.staffEffectiveAvailability(availabilityTargetUserId!),
    enabled: Boolean(availabilityOpen && availabilityTargetUserId),
  });
  const staffAvailabilityRequestsQuery = useQuery({
    queryKey: ['klm', 'staff-availability-requests', availabilityTargetUserId],
    queryFn: () => klmApi.staffAvailabilityRequests({ targetUserId: availabilityTargetUserId!, status: 'pending' }),
    enabled: Boolean(availabilityOpen && availabilityTargetUserId),
  });
  const selectedMatterQuery = useQuery<KlmMatterDetail>({
    queryKey: ['klm', 'matter', selectedThread?.matterId],
    queryFn: () => klmApi.matter(selectedThread!.matterId!),
    enabled: Boolean(selectedThread?.matterId && (documentRequestOpen || proposalPreparationOpen)),
    staleTime: 15_000,
  });
  const activeConversationKey = selectedThreadId ?? 'no-thread-selected';
  const activeConversationKeyRef = useRef(activeConversationKey);
  activeConversationKeyRef.current = activeConversationKey;
  const messagesQuery = useQuery({
    queryKey: ['klm', 'messages', selectedThreadId],
    queryFn: () => klmApi.messages(selectedThread!.id),
    enabled: Boolean(selectedThreadId),
  });
  useEffect(() => {
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const handleRealtimeEvent = (event: Event) => {
      const detail = (event as CustomEvent<{ event?: string; data?: Record<string, unknown> }>).detail;
      if (!detail?.event?.startsWith('OMNICHANNEL_')) return;

      const eventThreadId = typeof detail.data?.thread_id === 'string'
        ? detail.data.thread_id
        : null;
      const eventThreadIds = Array.isArray(detail.data?.thread_ids)
        ? detail.data.thread_ids.filter((value): value is string => typeof value === 'string')
        : [];
      const selectedThreadChanged = Boolean(selectedThreadId) && (
        eventThreadId === selectedThreadId
        || eventThreadIds.includes(selectedThreadId!)
      );

      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
        void queryClient.invalidateQueries({ queryKey: ['klm', 'conversation-response-metrics'] });
        if (selectedThreadChanged && selectedThreadId) {
          void queryClient.invalidateQueries({
            queryKey: ['klm', 'messages', selectedThreadId],
            exact: true,
          });
          void queryClient.invalidateQueries({
            queryKey: ['klm', 'omnichannel-context', selectedThreadId],
            exact: true,
          });
        }
      }, 150);
    };

    window.addEventListener('klm:realtime', handleRealtimeEvent);
    return () => {
      window.removeEventListener('klm:realtime', handleRealtimeEvent);
      if (refreshTimer !== null) clearTimeout(refreshTimer);
    };
  }, [queryClient, selectedThreadId]);
  const omnichannelContextQuery = useQuery({
    queryKey: ['klm', 'omnichannel-context', selectedThreadId],
    queryFn: () => klmApi.omnichannelContext(selectedThread!.id),
    enabled: Boolean(selectedThreadId),
    retry: false,
  });
  const teamsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-teams'],
    queryFn: klmApi.omnichannelTeams,
    enabled: Boolean(selectedThreadId),
    staleTime: 30_000,
  });
  const workspaceUsersQuery = useQuery({
    queryKey: ['klm', 'workspace-users', 'conversation-participants'],
    queryFn: klmApi.workspaceUsers,
    enabled: Boolean(selectedThreadId && canManageConversationParticipants),
    staleTime: 30_000,
    retry: false,
  });
  const conversationReferencesQuery = useQuery({
    queryKey: ['klm', 'conversation-references', selectedThreadId],
    queryFn: () => klmApi.conversationReferences(selectedThreadId!),
    enabled: Boolean(selectedThreadId),
    retry: false,
  });
  const conversationParticipantsQuery = useQuery({
    queryKey: ['klm', 'conversation-participants', selectedThreadId],
    queryFn: () => klmApi.conversationParticipants(selectedThreadId!),
    enabled: Boolean(selectedThreadId),
    retry: false,
  });
  const conversationCapabilitiesQuery = useQuery({
    queryKey: ['klm', 'conversation-capabilities', selectedThreadId],
    queryFn: () => klmApi.conversationCollaborationCapabilities(selectedThreadId!),
    enabled: Boolean(selectedThreadId),
    retry: false,
  });
  const outboundRoutes = useMemo(() => (
    (omnichannelContextQuery.data?.channels ?? []).filter((route) => (
      route.blockedAt === null
      && route.connectionKey
      && !['error', 'disconnected', 'revoked'].includes(route.connectionStatus ?? '')
    ))
  ), [omnichannelContextQuery.data?.channels]);
  const defaultOutboundRoute = outboundRoutes.find((route) => route.channel === selectedThread?.primaryChannel)
    ?? outboundRoutes[0];
  const activeRouteKey = routeByThread[activeConversationKey]
    ?? (defaultOutboundRoute ? conversationRouteKey(defaultOutboundRoute) : 'none');
  const selectedOutboundRoute = outboundRoutes.find((route) => conversationRouteKey(route) === activeRouteKey)
    ?? defaultOutboundRoute;
  const isOfficialWhatsAppRoute = selectedOutboundRoute?.channel === 'whatsapp'
    && [selectedOutboundRoute.provider, selectedOutboundRoute.transportMode, selectedOutboundRoute.connectionKey]
      .some((value) => /meta|cloud|official|coexist/i.test(String(value ?? '')));
  const officialWindowExpired = !selectedThread?.lastInboundAt
    || Date.now() - new Date(selectedThread.lastInboundAt).getTime() > 24 * 60 * 60 * 1000;
  const templateOnlyConversation = composeMode === 'outbound' && isOfficialWhatsAppRoute && officialWindowExpired;
  const selectedMetaGroupConnection = selectedOutboundRoute?.channel === 'whatsapp' && selectedOutboundRoute.connectionId
    ? {
        id: selectedOutboundRoute.connectionId,
        name: selectedOutboundRoute.connectionName ?? selectedOutboundRoute.connectionKey,
        provider: selectedOutboundRoute.provider,
        productMode: selectedOutboundRoute.transportMode,
        phoneNumberId: selectedOutboundRoute.externalAccountId,
      }
    : null;
  const flowRunsQuery = useQuery({
    queryKey: ['klm', 'flow-runs', selectedThread?.matterId],
    queryFn: () => klmApi.flowRuns(selectedThread!.matterId!, true),
    enabled: Boolean(selectedThread?.matterId),
  });
  const latestFlowRun = flowRunsQuery.data?.[0];
  const latestFlow = flowsQuery.data?.find((flow) => flow.id === latestFlowRun?.flow_id);
  const latestFlowId = latestFlow?.id;
  const visibleFlows = useMemo(() => {
    const normalizedSearch = flowSearch.trim().toLocaleLowerCase('pt-BR');
    return (flowsQuery.data ?? []).filter((flow) => !normalizedSearch || `${flow.name} ${flow.description ?? ''} ${flow.practice_area}`.toLocaleLowerCase('pt-BR').includes(normalizedSearch));
  }, [flowSearch, flowsQuery.data]);
  const selectedFlow = flowsQuery.data?.find((flow) => flow.id === selectedFlowId) ?? null;
  const selectedFlowRun = selectedFlow
    ? flowRunsQuery.data?.find((run) => (
        run.flow_id === selectedFlow.id
        && ['active', 'paused', 'waiting_human'].includes(run.status)
      )) ?? null
    : null;
  const internalEntries = useMemo<Array<[string, string]>>(() => {
    return (messagesQuery.data ?? []).filter((message) => (
      message.threadId === selectedThreadId && message.direction === 'internal'
    )).map((message) => [message.authorLabel, message.body] as [string, string]);
  }, [messagesQuery.data, selectedThreadId]);
  const dueFollowUps = (followUpsQuery.data ?? []).filter((followUp) => ['scheduled', 'due'].includes(followUp.status)).length;
  const selectedFollowUp = useMemo(() => (followUpsQuery.data ?? [])
    .filter((followUp) => followUp.threadId === selectedThreadId && ['scheduled', 'due'].includes(followUp.status))
    .sort((left, right) => left.dueAt.localeCompare(right.dueAt))[0], [followUpsQuery.data, selectedThreadId]);
  const selectedConversationPendencies = useMemo(() => {
    const items: Array<{ key: string; owner: 'escritorio' | 'cliente'; title: string; detail: string }> = [];
    if (selectedResponseMetric?.state === 'cliente_aguardando_escritorio') {
      items.push({ key: 'office-reply', owner: 'escritorio', title: 'Responder o cliente', detail: 'O cliente está aguardando uma resposta do escritório.' });
    }
    if (selectedResponseMetric?.state === 'escritorio_aguardando_cliente') {
      items.push({ key: 'client-reply', owner: 'cliente', title: 'Resposta do cliente', detail: 'O escritório está aguardando o retorno do cliente.' });
    }
    if (selectedFollowUp) {
      items.push({
        key: `follow-up-${selectedFollowUp.id}`,
        owner: 'escritorio',
        title: selectedFollowUp.reason || 'Realizar follow-up',
        detail: `Programado para ${new Date(selectedFollowUp.dueAt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}.`,
      });
    }
    if (latestFlowRun?.status === 'waiting_human') {
      items.push({ key: `flow-${latestFlowRun.id}`, owner: 'escritorio', title: 'Decisão humana no fluxo', detail: 'O fluxo está pausado aguardando uma ação da equipe.' });
    }
    return items;
  }, [latestFlowRun?.id, latestFlowRun?.status, selectedFollowUp, selectedResponseMetric?.state]);
  const overdueThreads = useMemo(() => (threadsQuery.data ?? []).filter((thread) => (
    thread.slaDueAt
    && !['closed', 'blocked'].includes(thread.status)
    && new Date(thread.slaDueAt).getTime() < Date.now()
  )), [threadsQuery.data]);
  const waitingOfficeMetrics = useMemo(() => (responseMetricsQuery.data?.items ?? []).filter((metric) => (
    metric.state === 'cliente_aguardando_escritorio'
  )), [responseMetricsQuery.data?.items]);
  const slaSummary = useMemo(() => {
    const tracked = (threadsQuery.data ?? []).filter((thread) => thread.slaDueAt && !['closed', 'blocked'].includes(thread.status));
    if (!tracked.length) return null;
    return overdueThreads.length
      ? { label: `${overdueThreads.length} resposta${overdueThreads.length === 1 ? '' : 's'} atrasada${overdueThreads.length === 1 ? '' : 's'}`, tone: 'red' as const }
      : { label: `${tracked.length} atendimento${tracked.length === 1 ? '' : 's'} dentro do prazo`, tone: 'green' as const };
  }, [overdueThreads.length, threadsQuery.data]);
  const messages = selectedThreadId ? messagesByThread[activeConversationKey] ?? [] : [];
  const visibleMessages = messages.filter((message) => !message.revealAt || new Date(message.revealAt).getTime() <= cadenceClock);
  const pendingCadenceMessage = messages
    .filter((message) => message.revealAt && new Date(message.revealAt).getTime() > cadenceClock)
    .sort((left, right) => String(left.revealAt).localeCompare(String(right.revealAt)))[0];
  const outboundDraft = outboundDrafts[activeConversationKey] ?? '';
  const internalDraft = internalDrafts[activeConversationKey] ?? '';
  const draft = composeMode === 'internal' ? internalDraft : outboundDraft;
  const selectedReference = referencesByThread[activeConversationKey] ?? null;
  const selectedMentions = useMemo(
    () => mentionsByThread[activeConversationKey] ?? [],
    [activeConversationKey, mentionsByThread],
  );
  const conversationKind = Boolean(conversationParticipantsQuery.data?.group)
    || selectedThread?.threadMetadata.conversationKind === 'group'
    || Boolean(selectedThread?.threadMetadata.groupId ?? selectedThread?.threadMetadata.providerGroupId)
    ? 'group' as const
    : 'individual' as const;
  const collaborationParticipants = useMemo<ConversationParticipant[]>(() => {
    if (!selected || !selectedThread) return [];
    const rows: ConversationParticipant[] = [{
      id: selectedThread.clientId ?? `contact:${selected.id}`,
      name: selected.name,
      roleLabel: 'Cliente',
      initials: selected.initials,
      presence: 'online',
    }];
    if (selectedThread.assignedHumanId) {
      rows.push({
        id: selectedThread.assignedHumanId,
        name: selected.attendantLabel,
        roleLabel: 'Responsável pelo atendimento',
        presence: humanMode ? 'online' : 'away',
      });
    }
    const currentUser = state?.currentUser;
    if (currentUser && !rows.some((row) => row.id === currentUser.id)) {
      rows.push({
        id: currentUser.id,
        name: currentUser.name || currentUser.email,
        handle: currentUser.email,
        roleLabel: 'Equipe do escritório',
        initials: currentUser.initials,
        presence: 'online',
      });
    }
    for (const participant of conversationParticipantsQuery.data?.participants ?? []) {
      if (rows.some((row) => row.id === participant.participantId)) continue;
      rows.push({
        id: participant.participantId,
        name: participant.resolvedLabel,
        roleLabel: participant.participantType === 'team'
          ? `Equipe · ${participant.role}`
          : participant.role,
        presence: 'offline',
      });
    }
    return rows;
  }, [conversationParticipantsQuery.data?.participants, humanMode, selected, selectedThread, state?.currentUser]);
  const collaborationMentionTargets = useMemo<ConversationMentionTarget[]>(() => {
    const rows: ConversationMentionTarget[] = [];
    const currentUser = state?.currentUser;
    if (currentUser?.id) {
      rows.push({
        id: currentUser.id,
        kind: 'user',
        name: currentUser.name || currentUser.email,
        handle: currentUser.email,
        detail: 'Você',
        initials: currentUser.initials,
      });
    }
    for (const user of workspaceUsersQuery.data ?? []) {
      if (!user.isActive || user.type === 'ai' || rows.some((row) => row.kind === 'user' && row.id === user.id)) continue;
      rows.push({
        id: user.id,
        kind: 'user',
        name: user.name || user.email || 'Usuário do workspace',
        handle: user.email ?? undefined,
        detail: user.role.replaceAll('_', ' '),
        avatarUrl: user.avatar ?? undefined,
        initials: user.initials,
      });
    }
    if (selectedThread?.assignedHumanId && !rows.some((row) => row.kind === 'user' && row.id === selectedThread.assignedHumanId)) {
      rows.push({
        id: selectedThread.assignedHumanId,
        kind: 'user',
        name: selected?.attendantLabel ?? 'Atendente responsável',
        detail: 'Responsável atual',
      });
    }
    for (const team of teamsQuery.data ?? []) {
      if (!team.isActive) continue;
      rows.push({
        id: team.id,
        kind: 'team',
        name: team.name,
        handle: team.slug,
        detail: team.description ?? undefined,
        memberCount: team.memberCount,
      });
    }
    return rows;
  }, [selected?.attendantLabel, selectedThread?.assignedHumanId, state?.currentUser, teamsQuery.data, workspaceUsersQuery.data]);
  const implicitMentions = useMemo(() => {
    const tokens = Array.from(draft.matchAll(/(^|\s)@([\p{L}\p{N}._-]+)/gu)).map((match) => match[2].toLocaleLowerCase('pt-BR'));
    if (!tokens.length) return [];
    const normalize = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR').replace(/[^\p{L}\p{N}]+/gu, '');
    return collaborationMentionTargets.filter((target) => {
      const candidates = [target.handle, target.name, target.name.split(/\s+/u)[0]]
        .filter((value): value is string => Boolean(value))
        .map(normalize);
      return tokens.some((token) => candidates.includes(normalize(token)) || candidates.some((candidate) => candidate.startsWith(normalize(token))));
    }).filter((target) => !selectedMentions.some((selected) => selected.kind === target.kind && selected.id === target.id));
  }, [collaborationMentionTargets, draft, selectedMentions]);
  const implicitAgentMentions = useMemo(() => {
    const tokens = Array.from(draft.matchAll(/(^|\s)@([\p{L}\p{N}._-]+)/gu)).map((match) => match[2].toLocaleLowerCase('pt-BR'));
    if (!tokens.length) return [];
    const normalize = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR').replace(/[^\p{L}\p{N}]+/gu, '');
    return (agentsQuery.data?.agents ?? []).filter((agent) => {
      if (agent.state !== 'active') return false;
      const candidates = [agent.key, agent.name, agent.name.split(/\s+/u)[0]].map(normalize);
      return tokens.some((token) => candidates.includes(normalize(token)));
    });
  }, [agentsQuery.data?.agents, draft]);
  const collaborationParticipantTargets = useMemo(() => {
    const existing = new Set((conversationParticipantsQuery.data?.participants ?? []).map((participant) => (
      `${participant.participantType}:${participant.participantId}`
    )));
    return collaborationMentionTargets.filter((target) => !existing.has(`${target.kind}:${target.id}`));
  }, [collaborationMentionTargets, conversationParticipantsQuery.data?.participants]);
  const routeCapabilities = selectedOutboundRoute?.capabilities ?? {};
  const rawGroupsCapability = routeCapabilities.groupMessaging ?? routeCapabilities.groups;
  const groupsCapability = recordValue(rawGroupsCapability);
  const groupsCapabilityState = String(groupsCapability?.state ?? '').toLocaleLowerCase('pt-BR');
  const providerGroupMessagingSupported = rawGroupsCapability === true
    || (
      groupsCapability?.supported === true
      && groupsCapability?.enabled === true
      && (!groupsCapabilityState || ['active', 'available', 'enabled', 'ready'].includes(groupsCapabilityState))
    );
  const routeUsesCoexistence = String(selectedOutboundRoute?.transportMode ?? '').toLocaleLowerCase('pt-BR').includes('coexist');
  const nativeCapabilityAvailable = (capability: string) => {
    const item = conversationCapabilitiesQuery.data?.capabilities.find((row) => row.scope === 'native' && row.capability === capability);
    return item ? item.status === 'supported' : true;
  };
  const nativeParticipantCapabilityAvailable = conversationCapabilitiesQuery.data?.capabilities.some((row) => (
    row.scope === 'native' && row.capability === 'group_participants' && row.status === 'supported'
  )) === true;
  const providerReplyAvailable = conversationKind === 'individual'
    || (providerGroupMessagingSupported && !routeUsesCoexistence);
  const providerReplyUnavailableMessage = routeUsesCoexistence
    ? 'Grupos não são habilitados pela API oficial em números no modo Coexistência.'
    : 'A conexão ainda não comprovou a capacidade de resposta em grupo.';
  const nativeReplyAvailable = nativeCapabilityAvailable('structured_references');
  const replyAvailableForMessage = (message: Message) => (
    canSend
    && nativeReplyAvailable
    && (message.role === 'internal' || providerReplyAvailable)
  );
  const replyUnavailableMessageFor = (message: Message) => {
    if (!canSend) return 'Seu perfil possui acesso somente de leitura às conversas.';
    if (!nativeReplyAvailable) return 'A colaboração nativa desta conversa não habilita referências estruturadas.';
    if (message.role !== 'internal' && !providerReplyAvailable) return providerReplyUnavailableMessage;
    return 'Resposta com referência indisponível.';
  };
  const providerGroupParticipantsCapability = conversationCapabilitiesQuery.data?.capabilities.find((row) => (
    row.scope === 'provider'
    && row.capability === 'group_participants'
    && (!selectedOutboundRoute?.provider || row.provider === selectedOutboundRoute.provider)
  ));
  const providerGroupManagementAvailable = Boolean(
    selectedOutboundRoute
    && !routeUsesCoexistence
    && providerGroupParticipantsCapability?.status === 'supported'
    && providerGroupParticipantsCapability.providerLinked
    && providerGroupParticipantsCapability.gateOpen
    && providerGroupParticipantsCapability.externalEffectVerified,
  );
  const providerGroupManagement = {
    available: providerGroupManagementAvailable,
    message: routeUsesCoexistence
      ? 'A rota em Coexistência não oferece gestão de grupos oficiais. A participação interna permanece somente na plataforma.'
      : providerGroupManagementAvailable
        ? 'A capacidade oficial está comprovada e continua separada da participação interna da plataforma.'
        : 'A integração não comprovou gestão oficial de participantes. A ação interna não altera WhatsApp nem outro provedor.',
  };
  const canManageNativeParticipants = Boolean(
    selectedThread
    && canManageConversationParticipants
    && nativeParticipantCapabilityAvailable,
  );
  const collaborationCapabilities = {
    reply: canSend && nativeReplyAvailable && (composeMode === 'internal' || providerReplyAvailable),
    mentionUsers: canSend && composeMode === 'internal' && nativeCapabilityAvailable('user_mentions'),
    mentionTeams: canSend && composeMode === 'internal' && nativeCapabilityAvailable('team_mentions'),
    viewParticipants: Boolean(selectedThread && nativeCapabilityAvailable('group_participants')),
  };
  const availabilityRedirectOptions = useMemo(() => collaborationMentionTargets
    .filter((target) => target.kind === 'team' || target.id !== availabilityTargetUserId)
    .map((target) => ({
      id: target.id,
      kind: target.kind,
      label: target.name,
      description: target.detail,
    })), [availabilityTargetUserId, collaborationMentionTargets]);
  const staffAvailabilityInitialValue = useMemo(() => staffAvailabilityValueFromApi(
    staffAvailabilityStateQuery.data,
    staffEffectiveAvailabilityQuery.data,
  ), [staffAvailabilityStateQuery.data, staffEffectiveAvailabilityQuery.data]);
  const pendingAvailabilityRequest = staffAvailabilityRequestsQuery.data?.[0] ?? null;
  const availabilityApproval: StaffAvailabilityApproval | null = pendingAvailabilityRequest ? {
    id: pendingAvailabilityRequest.id,
    status: pendingAvailabilityRequest.status === 'pending' ? 'pending' : pendingAvailabilityRequest.status === 'approved' ? 'approved' : 'rejected',
    requestedAt: pendingAvailabilityRequest.createdAt,
    requestedBy: pendingAvailabilityRequest.requestedByUserId === state?.currentUser?.id ? 'Solicitado por você' : 'Solicitado por outro usuário',
  } : null;
  const canDecidePendingAvailability = Boolean(
    pendingAvailabilityRequest
    && canApproveStaffAvailability
    && pendingAvailabilityRequest.requestedByUserId !== state?.currentUser?.id,
  );

  useEffect(() => {
    if (!pendingCadenceMessage?.revealAt) return;
    const interval = window.setInterval(() => setCadenceClock(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [activeConversationKey, pendingCadenceMessage?.revealAt]);

  useEffect(() => {
    if (!responseMetricsQuery.data?.items.some((metric) => (
      metric.state === 'cliente_aguardando_escritorio' || metric.state === 'escritorio_aguardando_cliente'
    ))) return;
    setResponseClock(Date.now());
    const interval = window.setInterval(() => setResponseClock(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [responseMetricsQuery.data?.generatedAt, responseMetricsQuery.data?.items]);

  const updateDraftForThread = (
    mode: 'outbound' | 'internal',
    threadKey: string,
    value: string | ((current: string) => string),
  ) => {
    const setter = mode === 'internal' ? setInternalDrafts : setOutboundDrafts;
    setter((current) => ({
      ...current,
      [threadKey]: typeof value === 'function' ? value(current[threadKey] ?? '') : value,
    }));
  };

  useEffect(() => {
    if (threadsQuery.isPending) return;
    setSelectedId((current) => {
      if (routedSelectedId) return routedSelectedId;
      if (current && current === pendingSelectionRef.current && contacts.some((contact) => contact.id === current)) return current;
      if (!requestedThreadId && isCompactConversation) return null;
      if (current && contacts.some((contact) => contact.id === current)) return current;
      return isCompactConversation ? null : contacts[0]?.id ?? null;
    });
  }, [contacts, isCompactConversation, requestedThreadId, routedSelectedId, threadsQuery.isPending]);

  useEffect(() => {
    // Qualquer mudança em `?thread=` encerra a janela de propagação, inclusive uma navegação
    // que a tenha superado: a URL volta a ser a única fonte de verdade da seleção.
    pendingSelectionRef.current = null;
  }, [requestedThreadId]);

  useEffect(() => {
    if (!selectedThreadId) return;
    const selectedMatterId = selectedThread?.matterId ?? null;
    if (routedSelectedId && routedSelectedId !== selectedThreadId) return;
    if (searchParams.get('thread') === selectedThreadId && searchParams.get('matter') === selectedMatterId) return;
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('thread', selectedThreadId);
    if (selectedMatterId) nextParams.set('matter', selectedMatterId);
    else nextParams.delete('matter');
    setSearchParams(nextParams, { replace: true });
  }, [routedSelectedId, searchParams, selectedThread?.matterId, selectedThreadId, setSearchParams]);

  useEffect(() => {
    setFlowSheetOpen(false);
    setOperationsOpen(false);
    setBroadcastsOpen(false);
    setTemplatePickerOpen(false);
    setAudioOpen(false);
    setDocumentRequestOpen(false);
    setProposalPreparationOpen(false);
    setScheduleOpen(false);
    setProposalPreviewPath(null);
    setContextWorkspace(null);
    setMessageScheduleOpen(false);
    setRichMessageOpen(false);
    setAvailabilityOpen(false);
    setEvidenceMessageId(null);
    setSelectedFlowId(null);
    setFlowSearch('');
  }, [selectedThreadId]);

  useEffect(() => {
    if (!flowSheetOpen) return;
    setSelectedFlowId((current) => {
      if (latestFlowId) return latestFlowId;
      if (current && flowsQuery.data?.some((flow) => flow.id === current)) return current;
      return flowsQuery.data?.find((flow) => flow.is_active)?.id ?? flowsQuery.data?.[0]?.id ?? null;
    });
  }, [flowSheetOpen, flowsQuery.data, latestFlowId]);

  useEffect(() => {
    const threadId = selectedThreadId;
    if (!threadId || !messagesQuery.data) return;
    const sourceMessages = new Map(messagesQuery.data.map((message) => [message.id, message]));
    const structuredReferences = new Map((conversationReferencesQuery.data ?? []).map((reference) => [reference.sourceMessageId, reference]));
    const mapped = messagesQuery.data.filter((message) => (
      message.threadId === threadId
      && (
        message.direction !== 'internal'
        || message.authorType === 'human'
        || message.messageMetadata.source === 'staff_note'
      )
    )).map((message) => {
      const metadata = message.messageMetadata;
      const channelEvent = typeof metadata.source === 'string' && metadata.source.endsWith('_webhook');
      const hasChannelIndex = Object.entries(metadata).some(([key, value]) => /messageId$/i.test(key) && Boolean(value));
      const indexedByChannel = hasChannelIndex && !channelEvent;
      const acceptedByProvider = ['sent', 'delivered', 'read'].includes(message.deliveryStatus);
      const rawAttachments = Array.isArray(metadata.attachments) ? metadata.attachments : [];
      const attachments = rawAttachments.flatMap((row, index) => {
        if (!row || typeof row !== 'object' || Array.isArray(row)) return [];
        const record = row as Record<string, unknown>;
        const name = typeof record.name === 'string' && record.name.trim() ? record.name : `Anexo ${index + 1}`;
        return [{
          id: String(record.id ?? `${message.id}-${index}`),
          name,
          fileType: typeof record.fileType === 'string' && record.fileType ? record.fileType : 'arquivo',
          localUrl: typeof record.localUrl === 'string' && /^https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?\//.test(record.localUrl) ? record.localUrl : undefined,
        }];
      });
      const deliveryState: Message['deliveryState'] = message.direction === 'inbound'
        ? 'received'
        : message.deliveryStatus === 'failed'
          ? 'failed'
          : message.deliveryStatus === 'blocked'
            ? 'blocked'
            : acceptedByProvider || indexedByChannel
              ? 'indexed'
              : 'local';
      let note = 'estado de entrega indisponível';
      if (message.direction === 'internal') note = 'somente equipe';
      else if (message.direction === 'inbound') note = channelEvent ? 'recebida pelo canal conectado' : 'recebida';
      else if (message.deliveryStatus === 'read') note = 'lida no canal';
      else if (message.deliveryStatus === 'delivered') note = 'entregue pelo canal';
      else if (message.deliveryStatus === 'sent') note = 'enviada pelo canal';
      else if (message.deliveryStatus === 'queued') note = 'aguardando envio pelo canal';
      else if (message.deliveryStatus === 'local_draft') note = 'rascunho local';
      else if (message.deliveryStatus === 'failed') note = 'falha local';
      else if (message.deliveryStatus === 'blocked') note = 'bloqueada';
      else if (indexedByChannel) note = 'indexada no atendimento';
      const cadence = message.messageMetadata?.deliveryCadence as Record<string, unknown> | undefined;
      const structuredReference = structuredReferences.get(message.id);
      const structuredTarget = structuredReference ? sourceMessages.get(structuredReference.targetMessageId) : undefined;
      const persistedMentions = structuredReference?.mentions.map((mention) => ({
        id: mention.targetId,
        kind: mention.mentionType,
        name: mention.resolvedLabel,
      } satisfies ConversationMentionTarget)) ?? [];
      const messageRoute = (omnichannelContextQuery.data?.channels ?? []).find((route) => (
        (message.bindingId && route.bindingId === message.bindingId)
        || (message.endpointId && route.endpointId === message.endpointId)
        || (message.connectionId && route.connectionId === message.connectionId)
      ));
      const routeLabel = message.direction === 'internal'
        ? undefined
        : messageRoute
          ? `${conversationRouteLabel(messageRoute)} · ${messageRoute.connectionName ?? messageRoute.connectionKey}`
          : channelLabel(message.channel);
      return {
        id: message.id,
        author: message.authorLabel,
        role: message.direction === 'internal' ? 'internal' : message.authorType === 'client' ? 'client' : message.authorType === 'human' ? 'human' : message.authorLabel.toLocaleLowerCase('pt-BR').includes('jurídico') ? 'legal' : 'sdr',
        text: message.body,
        time: new Date(message.occurredAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
        note,
        deliveryState,
        attachments,
        ragPath: attachments.length && selectedThread?.matterId ? `/app/leads?matter=${selectedThread.matterId}&panel=rag` : undefined,
        revealAt: typeof cadence?.notBefore === 'string' ? cadence.notBefore : undefined,
        typingStartsAt: typeof cadence?.typingStartsAt === 'string' ? cadence.typingStartsAt : undefined,
        showTypingIndicator: cadence?.showTypingIndicator === true,
        routeLabel,
        channel: message.channel,
        contentLifecycleState: message.contentLifecycleState ?? 'original',
        evidenceEventCount: message.evidenceEventCount ?? 0,
        evidenceRevisionCount: message.evidenceRevisionCount ?? 0,
        evidenceChainSha256: message.evidenceChainSha256,
        replyTo: structuredTarget ? {
          id: structuredTarget.id,
          authorLabel: structuredTarget.authorLabel,
          body: structuredTarget.body,
          occurredAtLabel: new Date(structuredTarget.occurredAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
        } : collaborationReference(metadata),
        mentions: persistedMentions.length ? persistedMentions : collaborationMentions(metadata),
      };
    }) satisfies Message[];
    setMessagesByThread((current) => ({ ...current, [threadId]: mapped }));
  }, [conversationReferencesQuery.data, messagesQuery.data, omnichannelContextQuery.data?.channels, selectedThread?.matterId, selectedThreadId]);

  useEffect(() => {
    setHumanMode(selectedThread?.status === 'human_active');
  }, [selectedThread?.id, selectedThread?.status]);

  const addConversationParticipantMutation = useMutation({
    mutationFn: ({ threadId, target }: { threadId: string; target: ConversationMentionTarget }) => (
      klmApi.addConversationParticipant(threadId, {
        participantType: target.kind,
        participantId: target.id,
        role: 'member',
      })
    ),
    onSuccess: async (participant, variables) => {
      const queryKey = ['klm', 'conversation-participants', variables.threadId] as const;
      queryClient.setQueryData<KlmConversationParticipantCollection>(queryKey, (current) => {
        if (!current || current.participants.some((item) => item.id === participant.id)) return current;
        return { ...current, participants: [...current.participants, participant] };
      });
      setNotice(`${variables.target.name} foi adicionado à participação interna do Meu Executivo. O grupo oficial do provedor não foi alterado.`);
      setNoticeAction(null);
      await queryClient.invalidateQueries({ queryKey });
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? ` ${error.message}` : '';
      setNotice(`Não foi possível adicionar o participante interno.${detail}`);
      setNoticeAction(null);
    },
  });

  const sendMutation = useMutation({
    mutationFn: async ({ threadId, body, mode, channel, route, collaborationMetadata, reference, mentions, assignmentTarget, agentTarget, matterId, expectedVersion }: {
      threadId: string;
      threadKey: string;
      body: string;
      mode: 'outbound' | 'internal';
      channel: KlmThread['primaryChannel'];
      route?: KlmOmnichannelMessageRoute;
      routeLabel?: string;
      collaborationMetadata?: Record<string, unknown>;
      reference?: ConversationMessageReference | null;
      mentions?: ConversationMentionTarget[];
      assignmentTarget?: ConversationMentionTarget | null;
      agentTarget?: { key: string; name: string } | null;
      matterId?: string | null;
      expectedVersion?: number;
    }) => {
      const authorLabel = state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace';
      const message = mode === 'internal'
        ? collaborationMetadata
          ? klmApi.sendInternalMessage(threadId, body, authorLabel, collaborationMetadata)
          : klmApi.sendInternalMessage(threadId, body, authorLabel)
        : collaborationMetadata
          ? klmApi.sendMessage(threadId, body, channel, authorLabel, route, collaborationMetadata)
          : klmApi.sendMessage(threadId, body, channel, authorLabel, route);
      const createdMessage = await message;
      let collaborationWarning: string | null = null;
      if (reference) {
        try {
          await klmApi.createConversationReference(threadId, {
            sourceMessageId: createdMessage.id,
            targetMessageId: reference.id,
            referenceType: 'reply',
            mentions: (mentions ?? []).map((mention) => ({ mentionType: mention.kind, targetId: mention.id })),
            idempotencyKey: `spa-reference:${threadId}:${createdMessage.id}`,
          });
        } catch {
          collaborationWarning = 'A mensagem foi salva, mas o vínculo estruturado será reconciliado a partir dos metadados preservados.';
        }
      } else if (mentions?.length) {
        try {
          await klmApi.attachConversationMentions(threadId, {
            sourceMessageId: createdMessage.id,
            mentions: mentions.map((mention) => ({ mentionType: mention.kind, targetId: mention.id })),
            idempotencyKey: `spa-mentions:${threadId}:${createdMessage.id}`,
          });
        } catch {
          collaborationWarning = 'A mensagem foi salva, mas as menções estruturadas serão reconciliadas a partir dos metadados preservados.';
        }
      }
      let assignmentWarning: string | null = null;
      if (assignmentTarget) {
        try {
          await klmApi.transferOmnichannelThread(threadId, {
            expectedVersion: expectedVersion ?? 1,
            toTeamId: assignmentTarget.kind === 'team' ? assignmentTarget.id : null,
            toUserId: assignmentTarget.kind === 'user' ? assignmentTarget.id : null,
            queue: null,
            reason: `Atribuição automática pela menção @${assignmentTarget.name}`,
            source: 'human',
          });
        } catch {
          assignmentWarning = `A mensagem foi salva, mas não foi possível atribuir a conversa a ${assignmentTarget.name}.`;
        }
      }
      let agentWarning: string | null = null;
      let agentActivated = false;
      if (agentTarget) {
        if (!matterId) {
          agentWarning = `A nota foi salva, mas o agente ${agentTarget.name} não foi acionado porque a conversa ainda não possui caso vinculado.`;
        } else {
          try {
            await klmApi.runAgent({
              agentKey: agentTarget.key,
              matterId,
              threadId,
              requestHuman: false,
              instructions: `Assuma a continuidade desta conversa conforme o playbook e a nota interna: ${body}`,
            });
            agentActivated = true;
          } catch {
            agentWarning = `A nota foi salva, mas não foi possível devolver a conversa ao agente ${agentTarget.name}.`;
          }
        }
      }
      return { message: createdMessage, agentActivated, collaborationWarning: [collaborationWarning, assignmentWarning, agentWarning].filter(Boolean).join(' ') || null };
    },
    onSuccess: (result, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', variables.threadId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'conversation-response-metrics'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'conversation-references', variables.threadId] });
      const savedNotice = variables.mode === 'internal'
        ? 'Nota salva no log privado do atendimento.'
        : `Envio via ${variables.routeLabel ?? channelLabel(variables.channel)} registrado. A entrega externa permanece bloqueada.`;
      setNotice(result.collaborationWarning ? `${savedNotice} ${result.collaborationWarning}` : savedNotice);
      setNoticeAction(null);
      setReferencesByThread((current) => ({ ...current, [variables.threadKey]: null }));
      setMentionsByThread((current) => ({ ...current, [variables.threadKey]: [] }));
      if (result.agentActivated) setHumanMode(false);
      if (activeConversationKeyRef.current === variables.threadKey) setComposeMode('outbound');
    },
    onError: (_, variables) => {
      updateDraftForThread(variables.mode, variables.threadKey, (current) => current.trim() ? current : variables.body);
      setNotice('Não foi possível salvar agora. O rascunho foi restaurado e nenhum canal externo foi acionado.');
    },
  });

  const createAvailabilityRequestMutation = useMutation({
    mutationFn: async (input: StaffAvailabilityRequestInput) => {
      const currentState = staffAvailabilityStateQuery.data ?? (await staffAvailabilityStateQuery.refetch()).data;
      return klmApi.createStaffAvailabilityRequest({
        targetUserId: input.staffId,
        expectedVersion: currentState?.profile?.version ?? 0,
        reason: input.requestNote || 'Atualização de disponibilidade solicitada pelo painel de conversas.',
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
    onSuccess: (_, input) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability', input.staffId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-effective', input.staffId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-requests', input.staffId] });
      setNotice('Solicitação completa registrada. A escala, a ausência e o redirecionamento só entrarão em vigor após aprovação.');
      setNoticeAction(null);
    },
  });

  const decideAvailabilityRequestMutation = useMutation({
    mutationFn: ({ requestId, decision }: {
      requestId: string;
      staffId: string;
      decision: 'approve' | 'reject';
    }) => klmApi.decideStaffAvailabilityRequest(
      requestId,
      decision,
      decision === 'approve'
        ? 'Configuração revisada e aprovada no painel contextual.'
        : 'Configuração devolvida para revisão no painel contextual.',
    ),
    onSuccess: (_, input) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability', input.staffId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-effective', input.staffId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'staff-availability-requests', input.staffId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'conversation-response-metrics'] });
      setNotice(input.decision === 'approve'
        ? 'Configuração aprovada e aplicada atomicamente com registro de auditoria.'
        : 'Solicitação rejeitada. A configuração anterior foi preservada.');
      setNoticeAction(null);
    },
  });

  const humanMutation = useMutation({
    mutationFn: ({ threadId, active }: { threadId: string; threadKey: string; active: boolean; silent?: boolean }) => klmApi.updateThread(threadId, { status: active ? 'human_active' : 'open' }),
    onSuccess: (_, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'conversation-response-metrics'] });
      if (!variables.silent) {
        setNotice(variables.active ? 'Você assumiu o atendimento.' : 'Atendimento devolvido à IA.');
        setNoticeAction(null);
      }
    },
    onError: (_, variables) => {
      if (activeConversationKeyRef.current === variables.threadKey) setHumanMode(!variables.active);
      setNotice('Não foi possível alterar o responsável. A tela voltou ao estado anterior.');
    },
  });

  const leadPotentialMutation = useMutation({
    mutationFn: ({ threadId, score, expectedVersion }: { threadId: string; score: number; expectedVersion?: number }) => klmApi.updateThread(threadId, {
      score,
      ...(expectedVersion ? { expectedVersion } : {}),
    }),
    onSuccess: (updatedThread) => {
      queryClient.setQueriesData<KlmThread[]>({ queryKey: ['klm', 'threads'] }, (current) => (
        current?.map((thread) => thread.id === updatedThread.id ? updatedThread : thread)
      ));
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      const stars = Math.max(0, Math.min(5, Math.round(updatedThread.score / 20)));
      setNotice(stars === 0 ? 'Potencial do lead removido. A alteração foi registrada.' : `Potencial do lead atualizado para ${stars} de 5 estrelas.`);
      setNoticeAction(null);
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? ` ${error.message}` : '';
      setNotice(`Não foi possível atualizar o potencial do lead.${detail}`);
      setNoticeAction(null);
    },
  });

  const followUpMutation = useMutation({
    mutationFn: ({ matterId: originMatterId, threadId: originThreadId, channel, dueAt, documents }: { matterId: string; threadId: string; threadKey: string; contactName: string; channel: KlmThread['primaryChannel']; dueAt: string; body: string; documents: string[]; followUpHours: number }) => klmApi.createFollowUp({
      matterId: originMatterId,
      threadId: originThreadId,
      channel,
      dueAt,
      reason: `Solicitar documentos: ${documents.join('; ')}. Execução externa bloqueada.`,
    }),
    onSuccess: (_, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'follow-ups'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'dashboard'] });
      updateDraftForThread('outbound', variables.threadKey, variables.body);
      if (activeConversationKeyRef.current === variables.threadKey) {
        setComposeMode('outbound');
        setDocumentRequestOpen(false);
      }
      setNotice(`Pedido de documentos preparado e follow-up local agendado para daqui a ${variables.followUpHours} hora${variables.followUpHours === 1 ? '' : 's'}. Nenhuma mensagem foi enviada.`);
      setNoticeAction(null);
    },
    onError: () => setNotice('Não foi possível programar o follow-up local.'),
  });

  const updateFollowUpMutation = useMutation({
    mutationFn: ({ id, dueAt, reason }: { id: string; dueAt: string; reason: string }) => klmApi.updateFollowUp(id, {
      dueAt: browserLocalDateTimeToIso(dueAt),
      reason: reason.trim() || null,
      status: 'scheduled',
    }),
    onSuccess: (updatedFollowUp) => {
      queryClient.setQueryData<Awaited<ReturnType<typeof klmApi.followUps>>>(
        ['klm', 'follow-ups'],
        (current) => current?.map((followUp) => (
          followUp.id === updatedFollowUp.id ? updatedFollowUp : followUp
        )),
      );
      void queryClient.invalidateQueries({ queryKey: ['klm', 'follow-ups'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'dashboard'] });
      setFollowUpEditorOpen(false);
      setNotice(`Follow-up reagendado para ${new Date(updatedFollowUp.dueAt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}.`);
      setNoticeAction(null);
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? ` ${error.message}` : '';
      setNotice(`Não foi possível atualizar o follow-up.${detail}`);
      setNoticeAction(null);
    },
  });

  const flowMutation = useMutation({
    mutationFn: ({ flowId, flowName, matterId, threadId }: { flowId: string; flowName: string; matterId: string; threadId: string }) => (
      klmApi.runFlow(flowId, matterId, {
        threadId,
        context: { source: 'conversation_flow_picker', flowName },
      })
    ),
    onSuccess: (run, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'flows'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'flow-runs', variables.matterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', variables.threadId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setFlowSheetOpen(false);
      const localSimulation = run.context?.klmRuntime === 'local_simulation';
      setNotice(localSimulation
        ? `Fluxo “${variables.flowName}” executado e registrado neste caso. O modo local percorreu ${Array.isArray(run.context?.trace) ? run.context.trace.length : 0} etapas e realizou 0 envios externos.`
        : `Fluxo “${variables.flowName}” ativado neste caso com status ${flowStatusLabel[run.status] ?? run.status}.`);
      setNoticeAction({ label: 'Reabrir fluxo', kind: 'flow' });
    },
    onError: () => {
      setFlowSheetOpen(true);
      setNotice('Não foi possível ativar o fluxo neste caso. Nenhuma mensagem externa foi enviada.');
      setNoticeAction(null);
    },
  });

  const flowRunControlMutation = useMutation({
    mutationFn: ({ runId, action }: { runId: string; action: 'pause' | 'resume' | 'cancel' }) => {
      if (action === 'pause') return klmApi.pauseFlowRun(runId);
      if (action === 'resume') return klmApi.resumeFlowRun(runId);
      return klmApi.cancelFlowRun(runId);
    },
    onSuccess: (run, variables) => {
      queryClient.setQueryData<KlmFlowRun[]>(
        ['klm', 'flow-runs', run.matter_id],
        (current) => current?.map((currentRun) => (
          currentRun.id === run.id ? run : currentRun
        )),
      );
      void queryClient.invalidateQueries({ queryKey: ['klm', 'flow-runs', run.matter_id] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      const actionLabel = variables.action === 'pause' ? 'pausado' : variables.action === 'resume' ? 'retomado' : 'encerrado';
      setNotice(`Fluxo ${actionLabel} neste caso. Nenhuma ação externa foi produzida por este controle.`);
      setNoticeAction({ label: 'Reabrir fluxo', kind: 'flow' });
    },
    onError: () => {
      setNotice('Não foi possível alterar esta execução. O estado anterior foi preservado.');
      setNoticeAction(null);
    },
  });

  const proposalMutation = useMutation({
    mutationFn: async ({ matterId: originMatterId, funnelSnapshot }: { matterId: string; threadId: string; threadKey: string; contactName: string; funnelSnapshot: KlmFunnel | undefined }) => {
      const operationId = crypto.randomUUID();
      const moveFrom = async (snapshot: KlmFunnel) => {
        const card = snapshot.cards.find((item) => item.id === originMatterId);
        if (!card) throw new Error('Caso não encontrado no funil local');
        if (card.stage === 'proposta') return;
        await klmApi.moveFunnel(originMatterId, 'proposta', {
          expectedBoardVersion: snapshot.boardVersion,
          sourceColumnId: card.stage,
          operationId,
        });
      };

      if (!funnelSnapshot) throw new Error('Funil local ainda não carregado');
      try {
        await moveFrom(funnelSnapshot);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 409) throw error;
        await moveFrom(await klmApi.funnel());
      }

      const matter = await klmApi.matter(originMatterId);
      const previousVersions = await klmApi.legalArtifacts({ matterId: originMatterId, kind: 'proposal' });
      const latest = previousVersions.sort((left, right) => right.version - left.version)[0];
      return klmApi.createLegalArtifact({
        matterId: originMatterId,
        kind: 'proposal',
        title: `Proposta de atuação · ${matter.healthObjectName || matter.demandType}`,
        content: {
          clientName: matter.client.name,
          demandLabel: matter.demandType,
          objectName: matter.healthObjectName,
          denialReason: matter.healthDenialReason,
          executiveSummary: matter.executiveSummary,
          strategyNotes: matter.strategyNotes,
          nextAction: matter.nextAction,
          financialConditions: {
            fixedFee: matter.revenueProjection?.projectionPremises?.fixedFee ?? matter.honorariumEstimate?.fixedFee ?? null,
            successPercentage: matter.revenueProjection?.projectionPremises?.successPercentage ?? matter.honorariumEstimate?.quotaLitisPercentage ?? null,
            projectedRevenue: matter.revenueProjection?.projectedRevenue ?? matter.honorariumEstimate?.totalEstimatedValue ?? null,
            reviewStatus: matter.revenueProjection?.projectionPremises?.reviewStatus ?? 'rascunho',
            paymentMode: matter.revenueProjection?.projectionPremises?.paymentMode ?? null,
            successBaseType: matter.revenueProjection?.projectionPremises?.successBaseType ?? null,
            economicObligation: matter.revenueProjection?.projectionPremises?.economicObligation ?? null,
            injunctionMilestoneFee: matter.revenueProjection?.projectionPremises?.injunctionMilestoneFee ?? null,
            obligationFee: matter.revenueProjection?.projectionPremises?.obligationFee ?? null,
          },
          institutionalEvidence: {
            source: 'workspace_owner_statement',
            casesCompleted: '+1.000',
            casesInProgress: '+300',
            injunctionAverage: '<24h',
            googleRating: '5,0',
            media: ['G1', 'R7', 'Estadão'],
          },
          externalEffectsExecuted: 0,
        },
        artifactKey: latest?.artifactKey,
        expectedVersion: latest?.version,
      });
    },
    onSuccess: (artifact, variables) => {
      const proposalPath = `/proposta/${variables.matterId}?artifact=${artifact.id}`;
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', variables.matterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'legal-artifacts'] });
      updateDraftForThread('outbound', variables.threadKey, `${variables.contactName}, preparei a proposta inicial para o seu caso: ${window.location.origin}${proposalPath}`);
      if (activeConversationKeyRef.current === variables.threadKey) {
        setComposeMode('outbound');
        setProposalPreparationOpen(false);
        setProposalPreviewPath(proposalPath);
      }
      setNotice(`Caso movido para Proposta e versão ${artifact.version} registrada. Nenhum link foi enviado ao cliente.`);
      setNoticeAction({ label: 'Visualizar proposta', kind: 'proposal', proposalPath });
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? ` ${error.message}` : '';
      setNotice(`Não foi possível preparar a proposta agora.${detail}`);
      setNoticeAction(null);
    },
  });

  const reconnectLocalData = async () => {
    setReconnecting(true);
    try {
      await bootstrapKlmLocalSession();
      const [threadsResult] = await Promise.all([
        threadsQuery.refetch(),
        funnelQuery.refetch(),
        followUpsQuery.refetch(),
      ]);
      setNotice(threadsResult.data?.length
        ? 'Conexão restabelecida. As conversas foram atualizadas.'
        : 'Conexão restabelecida. Ainda não há conversas neste workspace.');
      setNoticeAction(null);
    } catch {
      setNotice('O sistema ainda não respondeu. Nenhum dado alternativo foi exibido.');
    } finally {
      setReconnecting(false);
    }
  };

  const sendLocalMessage = () => {
    setNoticeAction(null);
    if (!canSend) {
      setNotice('Seu perfil possui acesso somente de leitura às conversas.');
      return;
    }
    const trimmed = draft.trim();
    if (!trimmed) {
      setNotice(composeMode === 'internal' ? 'Escreva a nota interna antes de salvar.' : 'Escreva uma resposta antes de enviar.');
      return;
    }
    if (!selectedThread) {
      setNotice('Selecione uma conversa persistida antes de salvar ou enviar.');
      return;
    }
    const isOfficialWhatsAppRoute = selectedOutboundRoute?.channel === 'whatsapp'
      && [
        selectedOutboundRoute.provider,
        selectedOutboundRoute.transportMode,
        selectedOutboundRoute.connectionKey,
      ].some((value) => /meta|cloud|official|coexist/i.test(String(value ?? '')));
    const lastInboundAt = selectedThread.lastInboundAt ? new Date(selectedThread.lastInboundAt).getTime() : null;
    const officialWindowExpired = lastInboundAt === null || Date.now() - lastInboundAt > 24 * 60 * 60 * 1000;
    const willBecomeInternal = selectedMentions.length > 0 || implicitMentions.length > 0 || implicitAgentMentions.length > 0;
    if (composeMode === 'outbound' && !willBecomeInternal && isOfficialWhatsAppRoute && officialWindowExpired) {
      setMetaWindowConfirmationOpen(true);
      return;
    }
    if (selectedReference && !nativeReplyAvailable) {
      setNotice('A colaboração nativa desta conversa não habilita referências estruturadas. A referência foi preservada e nada foi enviado.');
      setNoticeAction(null);
      return;
    }
    if (composeMode === 'outbound' && selectedReference && !providerReplyAvailable) {
      setNotice(`${providerReplyUnavailableMessage} A referência foi preservada e nada foi enviado.`);
      setNoticeAction(null);
      return;
    }
    const threadKey = activeConversationKey;
    const mentionsForSend = [...selectedMentions, ...implicitMentions];
    const assignmentTarget = implicitMentions.find((mention) => mention.kind === 'user' || mention.kind === 'team') ?? null;
    const agentTarget = implicitAgentMentions[0] ?? null;
    const messageMode = mentionsForSend.length || agentTarget ? 'internal' as const : composeMode;
    const collaborationMetadata = selectedReference || mentionsForSend.length || conversationKind === 'group'
      ? {
          collaboration: {
            schemaVersion: 'klm.conversation-collaboration.v1',
            conversationKind,
            replyToMessageId: selectedReference?.id ?? null,
            replyReference: selectedReference ? {
              id: selectedReference.id,
              authorLabel: selectedReference.authorLabel,
              body: selectedReference.body,
              occurredAtLabel: selectedReference.occurredAtLabel ?? null,
            } : null,
            mentions: mentionsForSend.map((mention) => ({
              mentionType: mention.kind,
              targetId: mention.id,
              resolvedLabel: mention.name,
              handle: mention.handle ?? null,
              detail: mention.detail ?? null,
            })),
          },
        }
      : undefined;
    updateDraftForThread(messageMode, threadKey, '');
    const outboundChannel = selectedOutboundRoute?.channel ?? selectedThread.primaryChannel;
    sendMutation.mutate({
      threadId: selectedThread.id,
      threadKey,
      body: trimmed,
      mode: messageMode,
      channel: outboundChannel,
      route: selectedOutboundRoute ? {
        endpointId: selectedOutboundRoute.endpointId,
        bindingId: selectedOutboundRoute.bindingId,
        connectionId: selectedOutboundRoute.connectionId,
        connectionKey: selectedOutboundRoute.connectionKey,
        transport: selectedOutboundRoute.transportMode,
      } : undefined,
      routeLabel: selectedOutboundRoute ? conversationRouteLabel(selectedOutboundRoute) : undefined,
      collaborationMetadata,
      reference: selectedReference,
      mentions: mentionsForSend,
      assignmentTarget,
      agentTarget: agentTarget ? { key: agentTarget.key, name: agentTarget.name } : null,
      matterId: selectedThread.matterId,
      expectedVersion: selectedThread.version ?? 1,
    });
    if (messageMode === 'outbound') {
      setHumanMode(true);
      if (selectedThread.status !== 'human_active') humanMutation.mutate({ threadId: selectedThread.id, threadKey, active: true, silent: true });
    }
  };

  const selectMessageReference = (message: Message) => {
    if (!canSend) {
      setNotice('Seu perfil possui acesso somente de leitura às conversas.');
      return;
    }
    if (!nativeReplyAvailable) {
      setNotice('A colaboração nativa desta conversa não habilita referências estruturadas.');
      setNoticeAction(null);
      return;
    }
    const nextMode = message.role === 'internal' ? 'internal' : 'outbound';
    if (nextMode === 'outbound' && !providerReplyAvailable) {
      setNotice(`${providerReplyUnavailableMessage} Use uma nota interna ou uma conexão Cloud API elegível.`);
      setNoticeAction(null);
      return;
    }
    setComposeMode(nextMode);
    setReferencesByThread((current) => ({
      ...current,
      [activeConversationKey]: {
        id: message.id,
        authorLabel: message.author,
        body: message.text,
        occurredAtLabel: message.time,
      },
    }));
    setNotice(`Mensagem de ${message.author} selecionada como referência.`);
    setNoticeAction(null);
  };

  const selectMention = (target: ConversationMentionTarget) => {
    setMentionsByThread((current) => {
      const existing = current[activeConversationKey] ?? [];
      if (existing.some((item) => item.kind === target.kind && item.id === target.id)) return current;
      return { ...current, [activeConversationKey]: [...existing, target] };
    });
  };

  const removeMention = (target: ConversationMentionTarget) => {
    setMentionsByThread((current) => ({
      ...current,
      [activeConversationKey]: (current[activeConversationKey] ?? []).filter((item) => (
        item.kind !== target.kind || item.id !== target.id
      )),
    }));
  };

  const addConversationParticipant = (target: ConversationMentionTarget) => {
    if (!selectedThread || !canManageNativeParticipants) {
      setNotice('Seu perfil não pode gerenciar a participação interna desta conversa.');
      setNoticeAction(null);
      return;
    }
    addConversationParticipantMutation.mutate({ threadId: selectedThread.id, target });
  };

  const toggleHumanMode = () => {
    setNoticeAction(null);
    if (!canSend) {
      setNotice('Seu perfil não pode assumir ou devolver atendimentos.');
      return;
    }
    if (!selectedThread) {
      setNotice('Selecione uma conversa persistida antes de assumir o atendimento.');
      return;
    }
    const next = !humanMode;
    setHumanMode(next);
    humanMutation.mutate({ threadId: selectedThread.id, threadKey: activeConversationKey, active: next });
  };

  const openFlowPicker = () => {
    setNoticeAction(null);
    if (!canManageFlows) {
      setNotice('Seu perfil não pode ativar fluxos.');
      return;
    }
    if (!selectedThread) {
      setNotice('Selecione uma conversa persistida antes de ativar um fluxo.');
      return;
    }
    if (!selectedThread.matterId) {
      setNotice('Esta conversa ainda não está vinculada a um caso. Vincule-a no CRM antes de ativar um fluxo.');
      return;
    }
    setFlowSearch('');
    setFlowSheetOpen(true);
  };

  const activateSelectedFlow = () => {
    if (!selectedThread?.matterId || !selectedFlow || !selectedFlow.is_active) return;
    setFlowSheetOpen(false);
    flowMutation.mutate({
      flowId: selectedFlow.id,
      flowName: selectedFlow.name,
      matterId: selectedThread.matterId,
      threadId: selectedThread.id,
    });
  };

  const openContextWorkspace = (title: string, description: string, path: string) => {
    const target = new URL(path, window.location.origin);
    if (selectedThread?.matterId && !target.searchParams.has('matter')) target.searchParams.set('matter', selectedThread.matterId);
    if (selectedThread?.id && !target.searchParams.has('thread')) target.searchParams.set('thread', selectedThread.id);
    setCaseOpen(false);
    setOperationsOpen(false);
    setBroadcastsOpen(false);
    setAudioOpen(false);
    setFlowSheetOpen(false);
    setDocumentRequestOpen(false);
    setProposalPreparationOpen(false);
    setScheduleOpen(false);
    setContextWorkspace({
      title,
      description,
      path: `${target.pathname}${target.search}${target.hash}`,
    });
  };

  const handleContextNavigate = (path: string) => {
    const target = new URL(path, window.location.origin);
    const targetPath = `${target.pathname}${target.search}${target.hash}`;
    if (target.pathname === '/app/conversas') {
      setContextWorkspace(null);
      setProposalPreviewPath(null);
      return;
    }
    if (target.pathname.startsWith('/proposta/')) {
      setContextWorkspace(null);
      setProposalPreviewPath(targetPath);
      return;
    }
    const metadata = contextWorkspaceMeta(target.pathname, target.searchParams);
    setProposalPreviewPath(null);
    openContextWorkspace(metadata.title, metadata.description, targetPath);
  };

  const runTool = (label: string) => {
    setNoticeAction(null);
    if (label === 'Ativar fluxo' && !canManageFlows) {
      setNotice('Seu perfil não pode ativar fluxos.');
      return;
    }
    if (label === 'Proposta' && !canEditFunnel) {
      setNotice('Seu perfil pode consultar a proposta, mas não mover o caso no funil.');
      return;
    }
    if (['Docs', 'Pedido de documentos', 'Agenda', 'Reunião online', 'Áudio IA', 'Nota interna'].includes(label) && !canSend) {
      setNotice('Seu perfil possui acesso somente de leitura a esta ferramenta.');
      return;
    }
    if (!selectedThread) {
      setNotice('Selecione uma conversa persistida antes de usar esta ferramenta.');
      return;
    }
    if (label === 'Operação' || label === 'Respostas prontas') {
      setOperationsOpen(true);
      return;
    }
    if (label === 'Disparos') {
      setTemplatePickerOpen(true);
      return;
    }
    const matterRequired = ['CRM', 'Atualização CRM', 'RAG', 'RAG contextual', 'Docs', 'Pedido de documentos', 'Agenda', 'Reunião online', 'Proposta', 'Proposta existente', 'Zapsign', 'Petição', 'Petição contextual', 'Ativar fluxo', 'Cliente 360', 'Rastreamento', 'Contratos', 'Financeiro', 'Calcular tratamento', 'Qualidade', 'Auditoria', 'Funil'].includes(label);
    if (matterRequired && !selectedThread.matterId) {
      setNotice('Esta conversa ainda não está vinculada a um caso. Vincule-a no CRM antes de executar a ferramenta.');
      return;
    }
    if (label === 'CRM' || label === 'Atualização CRM') {
      setCaseOpen(true);
      return;
    }
    if (label === 'RAG') {
      setCaseOpen(true);
      setNotice('O RAG deste cliente está disponível no Caso 360, sem sair da conversa.');
      return;
    }
    if (label === 'Cliente 360') {
      openContextWorkspace('Cliente 360', 'Card completo, dados, tarefas e contexto operacional deste atendimento.', `/app/leads?matter=${selectedThread.matterId}`);
      return;
    }
    if (label === 'Rastreamento') {
      openContextWorkspace('Rastreamento da origem', 'Campanha, conjunto, anúncio, criativo, jornada e eventos atribuídos a este lead.', `/app/rastreamento?matter=${selectedThread.matterId}`);
      return;
    }
    if (label === 'RAG contextual') {
      openContextWorkspace('Documentos e RAG', 'Fontes do dossiê e da conversa canônica, isoladas para este cliente e caso.', `/app/leads?matter=${selectedThread.matterId}&panel=rag`);
      return;
    }
    if (label === 'Contratos' || label === 'Zapsign') {
      openContextWorkspace('Contratos e procurações', 'Documentos, versões, assinatura e evidências vinculadas ao caso.', `/app/contratos?matter=${selectedThread.matterId}`);
      return;
    }
    if (label === 'Proposta existente') {
      setCaseOpen(false);
      setProposalPreviewPath(`/proposta/${selectedThread.matterId}`);
      return;
    }
    if (label === 'Financeiro') {
      openContextWorkspace('Financeiro do caso', 'Honorários, cobranças, recebimentos e projeções desta oportunidade.', `/app/financeiro?matter=${selectedThread.matterId}`);
      return;
    }
    if (label === 'Calcular tratamento') {
      openContextWorkspace('Tratamento e valor econômico', 'Ciclos, medicamentos, premissas e projeções financeiras do caso.', `/app/leads?matter=${selectedThread.matterId}&panel=tratamento`);
      return;
    }
    if (label === 'Qualidade') {
      openContextWorkspace('Qualidade do atendimento', 'Auditoria de resposta, entrega, cobertura e falhas desta conversa.', `/app/qualidade?matter=${selectedThread.matterId}&thread=${selectedThread.id}`);
      return;
    }
    if (label === 'Auditoria') {
      openContextWorkspace('Histórico e auditoria', 'Eventos imutáveis, intervenções humanas, agentes e alterações do caso.', `/app/atividade?matter=${selectedThread.matterId}&thread=${selectedThread.id}`);
      return;
    }
    if (label === 'Funil') {
      openContextWorkspace('Funil e oportunidade', 'Posição comercial e jurídica do caso no funil, sem perder a conversa atual.', `/app/funil?matter=${selectedThread.matterId}`);
      return;
    }
    if (label === 'Petição contextual' || label === 'Petição') {
      openContextWorkspace('Petições do caso', 'Peças, minutas, aprovações e histórico jurídico vinculados ao atendimento.', `/app/peticoes?matter=${selectedThread.matterId}&action=rascunho`);
      return;
    }
    if (label === 'Ativar fluxo') {
      setCaseOpen(false);
      openFlowPicker();
      return;
    }
    if (label === 'Nota interna') {
      setComposeMode('internal');
      setNotice('Modo nota interna ativado. O conteúdo ficará apenas no log privado.');
      return;
    }
    if (label === 'Docs' || label === 'Pedido de documentos') {
      setCaseOpen(false);
      setDocumentRequestOpen(true);
      return;
    }
    if (label === 'Agenda' || label === 'Reunião online') {
      setCaseOpen(false);
      setScheduleOpen(true);
      return;
    }
    if (label === 'Áudio IA') {
      setAudioOpen(true);
      return;
    }
    if (label === 'Proposta') {
      setCaseOpen(false);
      setProposalPreparationOpen(true);
      return;
    }
    setNotice(`A ferramenta “${label}” não possui uma ação persistida nesta tela.`);
  };

  const listVisibleMobile = resolvedSelectedId === null;
  const showConversationList = () => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('thread');
    nextParams.delete('matter');
    pendingSelectionRef.current = null;
    setSelectedId(null);
    setSearchParams(nextParams, { replace: true });
  };
  const selectConversation = (threadId: string) => {
    const thread = threadsQuery.data?.find((item) => item.id === threadId);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('thread', threadId);
    if (thread?.matterId) nextParams.set('matter', thread.matterId);
    else nextParams.delete('matter');
    pendingSelectionRef.current = threadId;
    setSelectedId(threadId);
    setSearchParams(nextParams, { replace: true });
  };
  const openContactCase = (threadId: string) => {
    const thread = threadsQuery.data?.find((item) => item.id === threadId);
    selectConversation(threadId);
    if (thread?.matterId) setCaseOpen(true);
    else setNotice('Esta conversa ainda não possui um caso vinculado para abrir o Caso 360.');
  };
  const openContactOperations = (threadId: string) => {
    selectConversation(threadId);
    setOperationsOpen(true);
  };
  const insertSavedResponse = (content: string) => {
    setComposeMode('outbound');
    updateDraftForThread('outbound', activeConversationKey, content);
    setNotice('Resposta pronta inserida no campo de mensagem para revisão.');
    setNoticeAction(null);
  };

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-[1600px] flex-col gap-2 overflow-hidden">
      <div data-testid="conversation-workspace-toolbar" className="flex min-h-8 shrink-0 flex-col gap-1 border-b border-border pb-1.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h2 className="mr-0.5 text-sm font-semibold">Central de atendimento</h2>
            {!threadsQuery.isError && <KlmPill tone="muted">{contacts.length} conversa{contacts.length === 1 ? '' : 's'}</KlmPill>}
            {slaSummary && (
              <ConversationIndicatorPopover
                label={slaSummary.label}
                tone={slaSummary.tone}
                title="Respostas atrasadas"
                emptyLabel="Nenhuma resposta atrasada."
                disabled={!overdueThreads.length}
                items={overdueThreads.map((thread) => {
                  const contact = contacts.find((item) => item.id === thread.id);
                  return {
                    key: thread.id,
                    title: contact?.name ?? `Conversa ${thread.id.slice(0, 8)}`,
                    detail: `${contact?.object ?? channelLabel(thread.primaryChannel)} · ${slaDueLabel(thread.slaDueAt)}`,
                    onSelect: () => selectConversation(thread.id),
                  };
                })}
              />
            )}
            <ConversationIndicatorPopover
              label={!canViewResponseMetrics ? 'Métricas sem permissão' : responseMetricsQuery.isError ? 'Tempos indisponíveis' : responseMetricsQuery.isPending ? 'Calculando esperas' : `${responseMetricsQuery.data.account.metrics.pending} cliente${responseMetricsQuery.data.account.metrics.pending === 1 ? '' : 's'} aguardando escritório`}
              tone={!canViewResponseMetrics ? 'muted' : responseMetricsQuery.isError ? 'red' : responseMetricsQuery.isPending ? 'gold' : waitingOfficeMetrics.length ? 'green' : 'muted'}
              title="Clientes aguardando o escritório"
              emptyLabel="Nenhum cliente aguarda resposta do escritório."
              disabled={!canViewResponseMetrics || responseMetricsQuery.isPending || responseMetricsQuery.isError}
              items={waitingOfficeMetrics.map((metric) => {
                const contact = contacts.find((item) => item.id === metric.threadId);
                const waitSeconds = conversationResponseWaitSeconds(metric, responseClock);
                return {
                  key: metric.threadId,
                  title: contact?.name ?? `Conversa ${metric.threadId.slice(0, 8)}`,
                  detail: `${contact?.object ?? channelLabel(metric.primaryChannel as KlmThread['primaryChannel'])} · aguardando há ${formatConversationWait(waitSeconds)}`,
                  onSelect: () => selectConversation(metric.threadId),
                };
              })}
            />
            <ConversationIndicatorPopover
              label={followUpsQuery.isError ? 'Follow-ups indisponíveis' : followUpsQuery.isPending ? 'Carregando follow-ups' : `${dueFollowUps} follow-up${dueFollowUps === 1 ? '' : 's'}`}
              tone={followUpsQuery.isError ? 'red' : followUpsQuery.isPending ? 'gold' : dueFollowUps ? 'red' : 'green'}
              title="Follow-ups abertos"
              emptyLabel="Nenhum follow-up aberto."
              disabled={followUpsQuery.isPending || followUpsQuery.isError}
              items={(followUpsQuery.data ?? [])
                .filter((followUp) => ['scheduled', 'due'].includes(followUp.status))
                .sort((left, right) => left.dueAt.localeCompare(right.dueAt))
                .map((followUp) => {
                  const contact = followUp.threadId ? contacts.find((item) => item.id === followUp.threadId) : null;
                  return {
                    key: followUp.id,
                    title: contact?.name ?? 'Follow-up sem conversa vinculada',
                    detail: `${followUp.reason || 'Retomar atendimento'} · ${new Date(followUp.dueAt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}`,
                    disabled: !followUp.threadId,
                    onSelect: followUp.threadId ? () => selectConversation(followUp.threadId!) : undefined,
                  };
                })}
            />
          </div>
          <p className="mt-0.5 truncate text-2xs text-muted-foreground">WhatsApp, caso, documentos, agenda e automações no mesmo atendimento.</p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 sm:justify-end">
          <PermissionGate module="mensagens" action="send_message" actionLabel="iniciar uma conversa">
            <Button
              size="sm"
              className="h-8 shrink-0 px-2.5 text-xs"
              onClick={() => {
                const defaultConnection = (whatsappConnectionsQuery.data ?? []).find((connection) => connection.status === 'connected');
                setNewConversationConnectionId(defaultConnection?.id ?? '');
                setNewConversationTemplateId('');
                setNewConversationOpen(true);
              }}
              disabled={channelConnectionState !== 'connected'}
              title={channelConnectionState === 'connected' ? 'Iniciar atendimento com template aprovado' : 'Conecte o WhatsApp antes de iniciar uma conversa'}
            >
              <PhoneOutgoing className="mr-1.5 h-3.5 w-3.5" />Nova conversa
            </Button>
          </PermissionGate>
          {selected?.legalAreaKey ? (
            <EffectivePlaybookLink
              selectors={{
                area: selected.legalAreaKey,
                channel: selectedThread?.primaryChannel,
                stage: selected.stage,
                agent: selectedThread?.ownerAgentKey,
                offer: selected.object,
              }}
              className="max-w-[280px]"
            />
          ) : (
            <div
              data-testid="conversation-policy-state"
              className="inline-flex min-h-9 max-w-[280px] items-center gap-2 rounded-md border border-border bg-secondary px-2.5 py-1.5 text-left text-muted-foreground"
              title={selected ? 'Informe a área jurídica para resolver a política efetiva' : 'Selecione uma conversa para consultar a política efetiva'}
            >
              <ShieldOff className="h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0">
                <span className="block text-[8px] font-semibold uppercase">Política efetiva</span>
                <strong className="block truncate text-[9px] font-semibold">
                  {selected ? 'Sem política · área não informada' : 'Sem contexto selecionado'}
                </strong>
              </span>
            </div>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-8 shrink-0 px-2.5 text-xs"
            onClick={() => setOfficialMetaGroupsOpen(true)}
            disabled={!selectedThread}
            aria-haspopup="dialog"
            title="Abrir grupos internos e oficiais desta rota WhatsApp (Groups API)"
          >
            <MessagesSquare className="mr-1.5 h-3.5 w-3.5" />Grupos WhatsApp
          </Button>
          <PermissionGate module="mensagens" action="send_message" actionLabel="disparar mensagens em massa">
            <Button variant="outline" size="sm" className="h-8 shrink-0 px-2.5 text-xs" onClick={() => runTool('Disparos')} disabled={!selectedThread}><Megaphone className="mr-1.5 h-3.5 w-3.5" />Disparos</Button>
          </PermissionGate>
        </div>
      </div>

      {(threadsQuery.isPending || reconnecting || threadsQuery.isError) && (
        <div role="status" data-testid="conversation-data-status" className="flex flex-col gap-3 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-3 text-amber-950 dark:border-amber-700/60 dark:bg-amber-950/25 dark:text-amber-100 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-2.5">
            {threadsQuery.isPending || reconnecting ? <LoaderCircle className="mt-0.5 h-4 w-4 shrink-0 animate-spin" /> : <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />}
            <div><p className="text-2xs font-semibold">{threadsQuery.isPending || reconnecting ? 'Carregando conversas persistidas' : 'Não foi possível carregar as conversas'}</p><p className="mt-1 text-2xs leading-4 opacity-80">{threadsQuery.isPending || reconnecting ? 'Aguardando o sistema; nenhum dado alternativo será inserido.' : 'A tela permanecerá vazia até que a API volte a responder.'}</p></div>
          </div>
          {!threadsQuery.isPending && <Button variant="outline" size="sm" className="h-8 shrink-0 border-amber-400 bg-white text-2xs text-amber-950 hover:bg-amber-100 dark:bg-transparent dark:text-amber-100" onClick={() => void reconnectLocalData()} disabled={reconnecting}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button>}
        </div>
      )}

      {threadsQuery.isSuccess && contacts.length === 0 && (
        <div role="status" data-testid="conversation-data-status" className="rounded-lg border border-dashed border-border bg-secondary/40 px-4 py-3 text-2xs leading-4 text-muted-foreground">
          <strong className="text-foreground">Nenhuma conversa persistida neste workspace.</strong>{' '}
          {channelConnectionState === 'connected'
            ? 'O WhatsApp está conectado; a primeira conversa aparecerá quando uma mensagem for recebida.'
            : channelConnectionState === 'loading'
              ? 'Verificando os canais conectados.'
              : channelConnectionState === 'unavailable'
                ? 'Não foi possível verificar os canais agora.'
                : 'Ainda não há um canal conectado para receber atendimentos.'}
        </div>
      )}

      {threadsQuery.isSuccess && contacts.length > 0 && funnelQuery.isError && (
        <div role="status" className="flex items-start gap-2 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-2 text-2xs leading-4 text-amber-950 dark:border-amber-700/60 dark:bg-amber-950/25 dark:text-amber-100">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />As conversas foram carregadas, mas os dados dos casos não responderam. A lista usa somente os metadados persistidos em cada conversa; ferramentas de caso exigem um card vinculado.
        </div>
      )}

      {notice && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs text-foreground">
          <span className="flex-1">{notice}</span>{noticeAction && <Button variant="outline" size="sm" className="h-7 shrink-0 text-2xs" onClick={() => {
            if (noticeAction.kind === 'flow') openFlowPicker();
            else if (noticeAction.kind === 'proposal' && noticeAction.proposalPath) setProposalPreviewPath(noticeAction.proposalPath);
            else if (noticeAction.kind === 'schedule') setScheduleOpen(true);
          }}>{noticeAction.label}</Button>}<Button variant="ghost" size="sm" className="h-7 shrink-0 text-2xs" onClick={() => { setNotice(null); setNoticeAction(null); }}>Fechar</Button>
        </div>
      )}

      <div className={cn('relative grid min-h-0 flex-1 gap-3', showDesktopInspector && !desktopInspectorCollapsed ? 'lg:grid-cols-[260px_minmax(480px,1fr)_320px]' : 'lg:grid-cols-[260px_minmax(0,1fr)]')}>
        <div className={cn('h-full min-h-0 min-w-0 overflow-hidden', !listVisibleMobile && 'hidden lg:block')}>
          <ContactList contacts={contacts} selectedId={resolvedSelectedId} onSelect={selectConversation} filter={filter} onFilter={applyContactFilter} query={query} onQuery={setQuery} isLoading={threadsQuery.isPending} isError={threadsQuery.isError} onRetry={() => void reconnectLocalData()} campaignId={searchParams.get('campaign')} personalizedFilter={personalizedFilter} onPersonalizedFilter={setPersonalizedFilter} sort={conversationSort} onSort={setConversationSort} canRateLeads={canSend} ratingPendingThreadId={leadPotentialMutation.isPending ? leadPotentialMutation.variables?.threadId ?? null : null} onRateLead={(contact, stars) => leadPotentialMutation.mutate({ threadId: contact.id, score: stars * 20, expectedVersion: contact.version })} onOpenCase={openContactCase} onOpenOperations={openContactOperations} channelConnectionState={channelConnectionState} />
        </div>

        {selected && selectedThread ? (
        <KlmPanel className={cn('h-full min-h-0 overflow-hidden', listVisibleMobile && 'hidden lg:flex', 'flex flex-col')}>
          <div data-testid="conversation-active-header" className="min-h-[48px] shrink-0 border-b border-border px-3 py-1.5">
            <div data-testid="conversation-active-identity" className="flex min-w-0 items-center gap-2.5 sm:gap-3">
              <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0 lg:hidden" onClick={showConversationList} title="Voltar às conversas"><ArrowLeft className="h-4 w-4" /></Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0 rounded-full p-0"
                aria-label={`Abrir Caso 360 de ${selected.name}`}
                title="Abrir Caso 360"
                onClick={() => setCaseOpen(true)}
              >
                <ContactAvatar name={selected.name} initials={selected.initials} avatarUrl={selected.avatarUrl} className="h-8 w-8" />
              </Button>
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <h3 className="min-w-0 basis-full break-words text-[15px] font-semibold leading-5 sm:basis-auto" title={selected.name}>{selected.name}</h3>
                  {selected.sensitiveDataHidden && <KlmPill tone="gold" className="shrink-0 whitespace-nowrap"><ShieldOff className="mr-1 h-3 w-3" />Dados ocultos</KlmPill>}
                </div>
                <div className="mt-0.5 flex min-w-0 items-center gap-2 text-2xs text-muted-foreground">
                  <span className="truncate">{channelLabel(selectedThread.primaryChannel)} · {selected.status} · {slaDueLabel(selectedThread.slaDueAt)}</span>
                </div>
                <ConversationResponseWaitClock metric={selectedResponseMetric} nowMs={responseClock} className="mt-0.5 w-fit max-w-full" />
              </div>
              <div className="hidden shrink-0 items-center gap-2 sm:flex">
                <div data-testid="conversation-one-click-actions" className="hidden items-center gap-1 md:flex">
                  <Popover>
                    <PopoverTrigger asChild>
                      <Button aria-label="Abrir pendências desta conversa" aria-haspopup="dialog" variant="outline" size="icon" className="relative h-8 w-8" title="Pendências do escritório e do cliente">
                        <Bell className="h-3.5 w-3.5" />
                        {selectedConversationPendencies.length > 0 && <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-[#b42318] px-1 text-[9px] font-bold text-white">{selectedConversationPendencies.length}</span>}
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent align="end" className="w-80 p-0" aria-label="Pendências desta conversa">
                      <div className="border-b px-3 py-2.5"><p className="text-xs font-semibold">Pendências da conversa</p><p className="mt-0.5 text-2xs text-muted-foreground">Responsabilidades atuais do escritório e do cliente.</p></div>
                      <div className="max-h-72 overflow-y-auto p-2 scrollbar-thin">
                        {selectedConversationPendencies.length ? selectedConversationPendencies.map((item) => (
                          <div key={item.key} className="mb-2 rounded-md border p-2.5 last:mb-0">
                            <div className="flex items-center justify-between gap-2"><p className="text-2xs font-semibold">{item.title}</p><KlmPill tone={item.owner === 'escritorio' ? 'red' : 'gold'}>{item.owner === 'escritorio' ? 'Nossa' : 'Cliente'}</KlmPill></div>
                            <p className="mt-1 text-2xs leading-4 text-muted-foreground">{item.detail}</p>
                          </div>
                        )) : <p className="px-2 py-5 text-center text-2xs text-muted-foreground">Nenhuma pendência registrada para esta conversa.</p>}
                      </div>
                    </PopoverContent>
                  </Popover>
                  <PermissionGate module="mensagens" action="send_message" actionLabel="agendar reunião">
                    <Button aria-label="Agendar reunião agora" aria-haspopup="dialog" variant="outline" size="icon" className="h-8 w-8" onClick={() => runTool('Reunião online')} title="Agendar reunião" disabled={!selectedThread.matterId}><CalendarPlus className="h-3.5 w-3.5" /></Button>
                  </PermissionGate>
                  <PermissionGate module="kanban" action="edit" actionLabel="preparar proposta">
                    <Button aria-label="Preparar proposta agora" aria-haspopup="dialog" variant="outline" size="icon" className="h-8 w-8" onClick={() => runTool('Proposta')} title="Preparar proposta" disabled={!selectedThread.matterId || proposalMutation.isPending}><FileSignature className="h-3.5 w-3.5" /></Button>
                  </PermissionGate>
                </div>
                <Button aria-label="Abrir operação do atendimento" variant="outline" size="icon" className="h-8 w-8" onClick={() => setOperationsOpen(true)} title="Transferir, responder, agendar e controlar atendimento"><UsersRound className="h-3.5 w-3.5" /></Button>
                <Button variant="outline" size="icon" className="h-8 w-8 xl:hidden" onClick={() => setCaseOpen(true)} title="Abrir painel Caso 360"><PanelRight className="h-4 w-4" /></Button>
                <PermissionGate module="mensagens" action="send_message" actionLabel="assumir este atendimento">
                  <Button className={cn('h-8 shrink-0 px-3 text-2xs', humanMode ? 'bg-[#1a9948] text-white hover:bg-[#0b7435]' : 'bg-[#031121] text-white hover:bg-[#031121]')} onClick={toggleHumanMode} title={humanMode ? 'Devolver o atendimento à IA' : 'Assumir este atendimento'} disabled={humanMutation.isPending}>
                    {humanMode ? <Bot className="mr-1.5 h-3.5 w-3.5" /> : <UserRoundCheck className="mr-1.5 h-3.5 w-3.5" />}
                    {humanMode ? 'Devolver à IA' : 'Assumir'}
                  </Button>
                </PermissionGate>
              </div>
            </div>
            <div data-testid="conversation-active-mobile-actions" className="relative z-30 mt-3 scroll-mb-32 grid grid-cols-[2.5rem_2.5rem_minmax(96px,auto)] gap-2 sm:hidden">
              <Button variant="outline" size="icon" className="h-9 w-10" onClick={() => setOperationsOpen(true)} title="Abrir operação"><UsersRound className="h-4 w-4" /></Button>
              <Button variant="outline" size="icon" className="h-9 w-10" onClick={() => setCaseOpen(true)} title="Abrir Caso 360"><PanelRight className="h-4 w-4" /></Button>
              <PermissionGate module="mensagens" action="send_message" actionLabel="assumir este atendimento">
                <Button className={cn('h-9 shrink-0 px-3 text-2xs', humanMode ? 'bg-[#1a9948] text-white hover:bg-[#0b7435]' : 'bg-[#031121] text-white hover:bg-[#031121]')} onClick={toggleHumanMode} title={humanMode ? 'Devolver o atendimento à IA' : 'Assumir este atendimento'} disabled={humanMutation.isPending}>
                  {humanMode ? <Bot className="mr-1.5 h-3.5 w-3.5" /> : <UserRoundCheck className="mr-1.5 h-3.5 w-3.5" />}
                  {humanMode ? 'Devolver à IA' : 'Assumir'}
                </Button>
              </PermissionGate>
            </div>
          </div>

          <div data-testid="conversation-message-history" className="min-h-[180px] flex-1 space-y-3 overflow-y-auto bg-[#fafaf9] p-3 dark:bg-[#0b0b0b] sm:p-4 scrollbar-thin">
            {selectedThread && messagesQuery.isPending && <div className="flex items-center justify-center gap-2 py-6 text-2xs text-muted-foreground"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />Carregando histórico local</div>}
            {selectedThread && messagesQuery.isError && <div className="mx-auto max-w-sm rounded-lg border border-red-200 bg-red-50 p-4 text-center text-red-900 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-100"><TriangleAlert className="mx-auto h-4 w-4" /><p className="mt-2 text-2xs font-semibold">Não foi possível carregar o histórico</p><p className="mt-1 text-2xs leading-4 opacity-75">Nenhuma mensagem alternativa será exibida.</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void messagesQuery.refetch()}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button></div>}
            {selectedThread && messagesQuery.isSuccess && visibleMessages.length === 0 && !pendingCadenceMessage && <div className="mx-auto max-w-sm px-4 py-10 text-center"><MessageSquareText className="mx-auto h-5 w-5 text-muted-foreground" /><p className="mt-2 text-2xs font-semibold">Nenhuma mensagem registrada</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">O histórico desta conversa está vazio na API.</p></div>}
            {visibleMessages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                replyAvailable={replyAvailableForMessage(message)}
                replyUnavailableMessage={replyUnavailableMessageFor(message)}
                onReply={selectMessageReference}
                onOpenRag={(path) => openContextWorkspace('Fonte no RAG', 'Documento ou trecho indexado que fundamenta esta mensagem.', path)}
                onOpenEvidence={setEvidenceMessageId}
              />
            ))}
            {pendingCadenceMessage ? (
              <div className="flex justify-end" data-testid="agent-typing-indicator" aria-live="polite">
                <div className="max-w-[76%] rounded-lg border border-[#d5d9dd] bg-[#eef0f2] px-3 py-2.5 shadow-sm dark:border-[#34383d] dark:bg-[#202326]">
                  <div className="flex items-center gap-2">
                    <Bot className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-2xs font-semibold">{pendingCadenceMessage.author}</span>
                    {pendingCadenceMessage.showTypingIndicator && (!pendingCadenceMessage.typingStartsAt || new Date(pendingCadenceMessage.typingStartsAt).getTime() <= cadenceClock) ? (
                      <span className="flex items-center gap-1 text-2xs text-muted-foreground"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" /><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:150ms]" /><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:300ms]" />digitando</span>
                    ) : <span className="text-2xs text-muted-foreground">preparando resposta</span>}
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          <div className="shrink-0 border-t border-border bg-card p-2.5">
            <div className="mb-1.5 flex min-w-0 flex-nowrap items-center gap-1.5 overflow-x-auto pb-0.5 scrollbar-thin">
              <PermissionGate module="mensagens" action="send_message" actionLabel="escrever ou responder mensagens">
                <div role="group" aria-label="Tipo de registro" className="inline-flex h-8 shrink-0 items-center rounded-md border bg-secondary p-0.5">
                  <button
                    type="button"
                    aria-pressed={composeMode === 'outbound'}
                    className={cn('inline-flex h-7 items-center gap-1 rounded px-2.5 text-2xs font-semibold transition-colors', composeMode === 'outbound' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}
                    onClick={() => setComposeMode('outbound')}
                  >
                    <MessageCircleReply className="h-3 w-3" />
                    Resposta
                  </button>
                  <button
                    type="button"
                    aria-pressed={composeMode === 'internal'}
                    className={cn('inline-flex h-7 items-center gap-1 rounded px-2.5 text-2xs font-semibold transition-colors', composeMode === 'internal' ? 'bg-amber-100 text-amber-950 shadow-sm dark:bg-amber-900/50 dark:text-amber-100' : 'text-muted-foreground hover:text-foreground')}
                    onClick={() => { setComposeMode('internal'); setNotice('Modo nota interna ativado. Este conteúdo nunca é enviado ao cliente.'); setNoticeAction(null); }}
                  >
                    <LockKeyhole className="h-3 w-3" />
                    Nota interna
                  </button>
                </div>
              </PermissionGate>
              {composeMode === 'outbound' && omnichannelContextQuery.isSuccess && outboundRoutes.length === 0 && (
                <KlmPill tone="gold">Sem rota endereçável</KlmPill>
              )}
              <PermissionGate module="mensagens" action="send_message" actionLabel="abrir respostas prontas">
                <button className="shrink-0" aria-label="Abrir respostas prontas" onClick={() => setOperationsOpen(true)}><KlmPill tone="gold"><MessageSquareText className="mr-1 h-3 w-3" />Respostas prontas</KlmPill></button>
              </PermissionGate>
              <KlmPill className="shrink-0" tone={composeMode === 'internal' ? 'blue' : humanMode ? 'green' : selectedThread.status === 'waiting_human' ? 'gold' : 'muted'}><CircleUserRound className="mr-1 h-3 w-3" />{composeMode === 'internal' ? 'Somente equipe' : humanMode ? 'Humano responde' : selectedThread.status === 'waiting_human' ? 'Aguardando humano' : selectedThread.ownerAgentKey ? `Agente: ${selectedThread.ownerAgentKey.replaceAll('_', ' ')}` : 'Sem responsável'}</KlmPill>
              {selectedFollowUp && (
                <button
                  type="button"
                  className="ml-auto inline-flex min-w-0 max-w-full shrink"
                  aria-label={`Editar follow-up de ${new Date(selectedFollowUp.dueAt).toLocaleString('pt-BR')}`}
                  title="Editar data e motivo do follow-up"
                  onClick={() => {
                    setFollowUpDueAtDraft(isoToBrowserLocalDateTime(selectedFollowUp.dueAt));
                    setFollowUpReasonDraft(selectedFollowUp.reason ?? '');
                    setFollowUpEditorOpen(true);
                  }}
                >
                  <KlmPill tone={selectedFollowUp.status === 'due' ? 'red' : 'blue'} className="max-w-full cursor-pointer whitespace-normal break-words text-left leading-4">
                    <CalendarClock className="mr-1 h-3 w-3 shrink-0" />
                    Follow-up: {new Date(selectedFollowUp.dueAt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}
                  </KlmPill>
                </button>
              )}
            </div>
            {composeMode === 'internal' && (
              <div role="note" className="mb-2 flex items-start gap-2 rounded-md border border-amber-300/80 bg-amber-50 px-2.5 py-2 text-amber-950 dark:border-amber-700/70 dark:bg-amber-950/30 dark:text-amber-100">
                <LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <div><p className="text-2xs font-semibold">Registro privado da equipe</p><p className="mt-0.5 text-2xs leading-3 opacity-75">A nota entra no histórico interno e nunca é enviada ao cliente ou ao WhatsApp.</p></div>
              </div>
            )}
            <ConversationCollaborationBar
              conversationKind={conversationKind}
              conversationLabel={conversationKind === 'group' ? String(conversationParticipantsQuery.data?.group?.name ?? selectedThread.threadMetadata.groupName ?? selected.subtheme ?? selected.name) : selected.name}
              reference={selectedReference}
              participants={collaborationParticipants}
              participantCount={conversationCapabilitiesQuery.data?.participantCount ?? (typeof selectedThread.threadMetadata.participantCount === 'number' ? selectedThread.threadMetadata.participantCount : collaborationParticipants.length)}
              participantTargets={collaborationParticipantTargets}
              canManageParticipants={canManageNativeParticipants}
              participantMutationKey={addConversationParticipantMutation.isPending && addConversationParticipantMutation.variables
                ? `${addConversationParticipantMutation.variables.target.kind}:${addConversationParticipantMutation.variables.target.id}`
                : null}
              providerGroupManagement={providerGroupManagement}
              mentionTargets={collaborationMentionTargets}
              selectedMentions={selectedMentions}
              capabilities={collaborationCapabilities}
              capabilityMessages={{
                reply: providerReplyUnavailableMessage,
                mentionUsers: composeMode === 'internal' ? 'Seu perfil não pode mencionar usuários.' : 'Menções da equipe ficam disponíveis em Nota interna.',
                mentionTeams: composeMode === 'internal' ? 'Seu perfil não pode mencionar equipes.' : 'Menções de equipes ficam disponíveis em Nota interna.',
              }}
              className="mb-1.5 rounded-md"
              onReply={() => setNotice('Referência pronta. Escreva a resposta e confirme o envio.')}
              onClearReference={() => setReferencesByThread((current) => ({ ...current, [activeConversationKey]: null }))}
              onParticipantAdd={addConversationParticipant}
              onMentionSelect={selectMention}
              onMentionRemove={removeMention}
            />
            <PermissionGate module="mensagens" action="send_message" actionLabel="escrever ou responder mensagens" variant="readonly" showReadonlyBadge>
              <div className="relative">
                {templateOnlyConversation ? (
                  <>
                    <div data-testid="conversation-template-window-closed" className="flex min-h-[76px] flex-wrap items-center justify-between gap-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-3 text-xs text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
                      <span>A janela de 24 horas encerrou. Só é possível enviar um modelo aprovado.</span>
                      <Button type="button" size="sm" onClick={() => setTemplatePickerOpen(true)}>Escolher modelo</Button>
                    </div>
                    {/* Mantém o contrato de automação/RTL sem oferecer digitação visível fora da janela. */}
                    <Textarea data-testid="conversation-composer" className="klm-touch-text-field sr-only" value={draft} onChange={(event) => updateDraftForThread('outbound', activeConversationKey, event.target.value)} aria-label="Mensagem da conversa" />
                  </>
                ) : <>
                <Textarea
                  data-testid="conversation-composer"
                  aria-label="Mensagem da conversa"
                  value={draft}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    if (composeMode === 'outbound' && /(^|\s)@[\p{L}\p{N}._-]+/u.test(nextValue)) {
                      setComposeMode('internal');
                      updateDraftForThread('internal', activeConversationKey, nextValue);
                      setNotice('Menção detectada: esta mensagem será registrada como nota interna.');
                      return;
                    }
                    if (composeMode === 'outbound' && /^\/\S*/u.test(nextValue)) {
                      setOperationsOpen(true);
                    }
                    updateDraftForThread(composeMode, activeConversationKey, nextValue);
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
                    event.preventDefault();
                    if (!draft.trim() || sendMutation.isPending) return;
                    sendLocalMessage();
                  }}
                  className={cn(
                    'klm-touch-text-field min-h-[76px] resize-none pb-10 pr-36 text-sm',
                    composeMode === 'internal'
                      ? 'border-amber-300/80 bg-amber-50/60 focus-visible:ring-amber-500 dark:border-amber-800 dark:bg-amber-950/20'
                      : 'bg-secondary',
                  )}
                  placeholder={composeMode === 'internal' ? 'Escreva uma nota privada para agentes e equipe...' : 'Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.'}
                />
                </>}
                <div className="absolute bottom-2 left-2 flex items-center gap-1">
                  <PermissionGate module="automations" action="manage" actionLabel="ativar fluxo">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 bg-background px-2 text-2xs shadow-sm"
                      onClick={openFlowPicker}
                      aria-haspopup="dialog"
                      disabled={!selectedThread.matterId || flowMutation.isPending}
                      title={selectedThread.matterId ? 'Ativar, pausar ou trocar o fluxo desta conversa' : 'Vincule a conversa a um caso para ativar um fluxo'}
                    >
                      <Workflow className="mr-1 h-3 w-3" />Fluxo
                    </Button>
                  </PermissionGate>
                  <PermissionGate module="mensagens" action="send_message" actionLabel="preparar disparo individual">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 bg-background px-2 text-2xs shadow-sm"
                      onClick={() => setTemplatePickerOpen(true)}
                      aria-haspopup="dialog"
                      disabled={!selectedThread}
                      title="Usar modelo aprovado, solicitar aprovação ou escolher uma automação programada"
                    >
                      <Megaphone className="mr-1 h-3 w-3" />Disparo individual
                    </Button>
                  </PermissionGate>
                </div>
                {composeMode === 'outbound' ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    data-testid="conversation-composer-attachment"
                    className="absolute bottom-2 right-[6.5rem] h-7 w-7 bg-background text-foreground shadow-sm hover:border-[#0057d8] hover:text-[#0057d8]"
                    onClick={() => setRichMessageOpen(true)}
                    aria-label="Anexar conteúdo"
                    title="Anexar arquivo, modelo oficial, botões, lista ou WhatsApp Flow"
                    aria-haspopup="dialog"
                    disabled={!selectedThread || !selectedOutboundRoute}
                  >
                    <Paperclip className="h-3.5 w-3.5" />
                    <span className="sr-only">Anexar conteúdo</span>
                  </Button>
                ) : null}
                {composeMode === 'outbound' ? (
                  <PermissionGate module="mensagens" action="send_message" actionLabel="agendar mensagem">
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      data-testid="conversation-composer-schedule"
                      className="absolute bottom-2 right-[4.5rem] h-7 w-7 bg-background text-foreground shadow-sm hover:border-[#0057d8] hover:text-[#0057d8]"
                      onClick={() => setMessageScheduleOpen(true)}
                      aria-label="Agendar mensagem"
                      title="Agendar esta mensagem para envio posterior"
                      aria-haspopup="dialog"
                      disabled={!selectedThread}
                    >
                      <CalendarClock className="h-3.5 w-3.5" />
                    </Button>
                  </PermissionGate>
                ) : null}
                {composeMode === 'outbound' ? (
                  <Popover open={composerAssistantOpen} onOpenChange={setComposerAssistantOpen}>
                    <PopoverTrigger asChild>
                      <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        data-testid="conversation-composer-assistant"
                        className="absolute bottom-2 right-10 h-7 w-7 border-[#c79224]/60 bg-background text-[#0057d8] shadow-sm hover:border-[#0057d8] hover:bg-[#c79224]/10"
                        aria-label="Abrir assistente Meu Executivo"
                        title="Abrir assistente Meu Executivo"
                        aria-haspopup="dialog"
                        disabled={!selectedThread}
                      >
                        <Sparkles className="h-3.5 w-3.5" />
                        <span className="sr-only">Assistente Meu Executivo</span>
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent
                      side="top"
                      align="end"
                      sideOffset={8}
                      aria-label="Assistente Meu Executivo no campo de resposta"
                      className="max-h-[70dvh] w-[min(380px,calc(100vw-2rem))] overflow-y-auto p-0 scrollbar-thin"
                    >
                      <ConversationCopilotPanel
                        threadId={selectedThread.id}
                        matterId={selectedThread.matterId}
                        canEdit={canSend}
                        canCreateTasks={canEditFunnel}
                        draft={outboundDraft}
                        onUseResponse={(content) => {
                          insertSavedResponse(content);
                          setComposerAssistantOpen(false);
                        }}
                        onOpenSchedule={() => {
                          setComposerAssistantOpen(false);
                          setScheduleOpen(true);
                        }}
                      />
                    </PopoverContent>
                  </Popover>
                ) : null}
                <PermissionGate module="mensagens" action="send_message" actionLabel={composeMode === 'internal' ? 'salvar nota interna' : 'enviar mensagem'}>
                  <Button
                    type="button"
                    size="icon"
                    data-testid="conversation-composer-send"
                    className={cn(
                      'absolute bottom-2 right-2 h-7 w-7 text-white shadow-sm',
                      composeMode === 'internal' ? 'bg-[#0057d8] hover:bg-[#0048b5]' : 'bg-[#031121] hover:bg-[#0b2138]',
                    )}
                    onClick={sendLocalMessage}
                    aria-label={composeMode === 'internal' ? 'Salvar nota interna' : 'Preparar envio'}
                    title={composeMode === 'internal' ? 'Salvar nota interna (Enter)' : selectedOutboundRoute ? `Registrar envio via ${conversationRouteLabel(selectedOutboundRoute)} (Enter)` : 'Salvar mensagem no histórico local (Enter)'}
                    disabled={!draft.trim() || sendMutation.isPending}
                  >
                    {sendMutation.isPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                  </Button>
                </PermissionGate>
              </div>
            </PermissionGate>
            <div className="mt-2 space-y-2">
              <div
                data-testid="conversation-context-actions"
                className="grid min-w-0 grid-cols-5 gap-1.5"
              >
                {[
                  ['CRM', ClipboardList, 'Atualização CRM'], ['Pedir docs', FilePlus2, 'Pedido de documentos'], ['Agendar', CalendarPlus, 'Reunião online'], ['Proposta', FileSignature, 'Proposta'], ['Áudio', AudioLines, 'Áudio IA'],
                ].map(([label, Icon, action]) => {
                  const requiresMatter = ['Atualização CRM', 'Ativar fluxo', 'Pedido de documentos', 'Reunião online', 'Proposta'].includes(action as string);
                  const permissionModule: PermissionModule | null = action === 'Ativar fluxo' ? 'automations' : action === 'Proposta' ? 'kanban' : action === 'Atualização CRM' ? null : 'mensagens';
                  const permissionAction: PermissionAction = action === 'Ativar fluxo' ? 'manage' : action === 'Proposta' ? 'edit' : 'send_message';
                  const pending = (action === 'Ativar fluxo' && flowMutation.isPending) || (action === 'Pedido de documentos' && followUpMutation.isPending) || (action === 'Proposta' && proposalMutation.isPending);
                  const disabledReason = requiresMatter && !selectedThread.matterId ? 'Vincule esta conversa a um caso antes de usar a ferramenta.' : action as string;
                  const opensContext = ['Operação', 'Atualização CRM', 'Ativar fluxo', 'Pedido de documentos', 'Reunião online', 'Proposta', 'Disparos', 'Áudio IA'].includes(action as string);
                  const button = <Button key={label as string} variant="outline" size="sm" className="h-8 min-w-0 justify-center px-1 text-2xs" onClick={() => runTool(action as string)} title={disabledReason} aria-haspopup={opensContext ? 'dialog' : undefined} disabled={(requiresMatter && !selectedThread.matterId) || pending}><Icon className="mr-1 h-3 w-3 shrink-0" /><span className="whitespace-nowrap">{action === 'Proposta' && proposalMutation.isPending ? 'Preparando' : label as string}</span></Button>;
                  return permissionModule ? (
                    <PermissionGate key={label as string} module={permissionModule} action={permissionAction} actionLabel={action as string}>
                      {button}
                    </PermissionGate>
                  ) : button;
                })}
              </div>
            </div>
          </div>
        </KlmPanel>
        ) : (
          <KlmPanel className={cn('flex h-full min-h-0 items-center justify-center overflow-hidden p-8 text-center', listVisibleMobile && 'hidden lg:flex')}>
            <div className="max-w-sm">
              {threadsQuery.isPending || reconnecting ? <LoaderCircle className="mx-auto h-6 w-6 animate-spin text-muted-foreground" /> : threadsQuery.isError ? <TriangleAlert className="mx-auto h-6 w-6 text-[#a53e3c]" /> : <MessageSquareText className="mx-auto h-6 w-6 text-muted-foreground" />}
              <p className="mt-3 text-sm font-semibold">{threadsQuery.isPending || reconnecting ? 'Carregando conversas' : threadsQuery.isError ? 'Histórico indisponível' : contacts.length ? 'Selecione uma conversa' : 'Nenhuma conversa persistida'}</p>
              <p className="mt-1 text-2xs leading-4 text-muted-foreground">{threadsQuery.isPending || reconnecting ? 'Aguardando o sistema.' : threadsQuery.isError ? 'Tente novamente para recuperar os atendimentos do workspace.' : contacts.length ? 'Escolha um atendimento na lista para abrir mensagens, caso, handoff e fluxos.' : 'As ferramentas serão habilitadas quando a API registrar um atendimento.'}</p>
              {threadsQuery.isError && <Button variant="outline" size="sm" className="mt-4 h-8 text-2xs" onClick={() => void reconnectLocalData()}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button>}
            </div>
          </KlmPanel>
        )}

        {showDesktopInspector && !desktopInspectorCollapsed && selected && selectedThread && <div className="min-h-0">
          <ConversationInspector
            contact={selected}
            internalEntries={internalEntries}
            onTool={runTool}
            threadId={selectedThread?.id}
            matterId={selectedThread?.matterId}
            canEdit={canSend}
            canManageFlows={canManageFlows}
            onUseResponse={insertSavedResponse}
            onCollapse={() => setDesktopInspectorCollapsed(true)}
          />
        </div>}
        {showDesktopInspector && desktopInspectorCollapsed && selected && selectedThread ? (
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="absolute right-0 top-1/2 z-30 hidden h-12 w-6 -translate-y-1/2 translate-x-1/2 rounded-l-md rounded-r-none border-r-0 bg-background shadow-sm lg:inline-flex"
            onClick={() => setDesktopInspectorCollapsed(false)}
            title="Mostrar Caso 360"
            aria-label="Mostrar painel Caso 360"
          >
            <PanelRightOpen className="h-3.5 w-3.5" />
          </Button>
        ) : null}
      </div>

      <OfficialMetaGroupSheet
        open={officialMetaGroupsOpen && Boolean(selectedThread)}
        onOpenChange={setOfficialMetaGroupsOpen}
        connection={selectedMetaGroupConnection}
        canConfigure={canManageConversationParticipants}
        canPrepareOperations={canSend}
      />

      <ConversationEvidenceSheet
        open={Boolean(evidenceMessageId && selectedThread)}
        onOpenChange={(open) => !open && setEvidenceMessageId(null)}
        threadId={selectedThread?.id ?? null}
        messageId={evidenceMessageId}
      />

      <Sheet open={operationsOpen && Boolean(selectedThread)} onOpenChange={setOperationsOpen}>
        <SheetContent className="flex w-[96vw] flex-col overflow-hidden p-0 sm:max-w-[520px]">
          <SheetHeader className="border-b border-border px-5 py-5 text-left">
            <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]"><UsersRound className="h-4 w-4" /></div>
            <SheetTitle className="text-lg font-semibold">Operação do atendimento</SheetTitle>
            <SheetDescription className="text-2xs leading-4">Transfira para pessoa, equipe ou fila; use respostas prontas; agende mensagens; prepare chamadas e controle automações sem sair da conversa.</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-3 scrollbar-thin">
            <NativeInboxRoutingPanel threadId={selectedThread?.id} canEdit={canSend} canManageFlows={canManageFlows} onUseResponse={(content) => { insertSavedResponse(content); setOperationsOpen(false); }} />
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={flowSheetOpen} onOpenChange={setFlowSheetOpen}>
        <SheetContent className="flex w-[94vw] flex-col overflow-hidden p-0 sm:max-w-[460px]">
          <SheetHeader className="border-b border-border px-5 py-5 text-left">
            <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]"><Workflow className="h-4 w-4" /></div>
            <SheetTitle className="text-lg font-semibold">Ativar fluxo no atendimento</SheetTitle>
            <SheetDescription className="text-2xs leading-4">Escolha um fluxo publicado na página de Fluxos. A execução será vinculada a {selectedName || 'esta conversa'} e ficará registrada no histórico interno.</SheetDescription>
          </SheetHeader>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 scrollbar-thin">
            {latestFlow && latestFlowRun && (
              <section className="mb-4 rounded-lg border border-[#1a9948]/25 bg-[#1a9948]/8 p-3" aria-label="Última execução do caso">
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#1a9948]" />
                  <div className="min-w-0 flex-1">
                    <p className="text-2xs font-semibold uppercase text-[#0b7435] dark:text-emerald-400">Última execução</p>
                    <p className="mt-1 truncate text-sm font-semibold">{latestFlow.name}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-2xs text-muted-foreground">
                      <span>{flowStatusLabel[latestFlowRun.status] ?? latestFlowRun.status}</span>
                      <span>·</span>
                      <span>{new Date(latestFlowRun.started_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>
                      {latestFlowRun.context?.klmRuntime === 'local_simulation' && <KlmPill tone="muted">0 envios externos</KlmPill>}
                    </div>
                  </div>
                </div>
              </section>
            )}

            <div className="relative mb-3">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={flowSearch} onChange={(event) => setFlowSearch(event.target.value)} className="h-9 pl-9 text-2xs" placeholder="Buscar follow-up, recuperação ou fluxo..." />
            </div>

            <div className="mb-2 flex items-center justify-between">
              <p className="text-2xs font-semibold uppercase text-muted-foreground">Fluxos disponíveis</p>
              <span className="text-2xs text-muted-foreground">{flowsQuery.isPending ? 'carregando' : flowsQuery.isError ? 'indisponível' : `${visibleFlows.length} no workspace`}</span>
            </div>
            <div className="space-y-2" role="listbox" aria-label="Fluxos disponíveis">
              {flowsQuery.isPending && <div className="flex items-center justify-center gap-2 py-8 text-2xs text-muted-foreground"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />Carregando catálogo</div>}
              {flowsQuery.isError && <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-center text-red-900 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-100"><p className="text-2xs font-semibold">Não foi possível carregar os fluxos</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void flowsQuery.refetch()}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button></div>}
              {flowsQuery.isSuccess && visibleFlows.length === 0 && <div className="rounded-lg border border-dashed p-5 text-center text-2xs text-muted-foreground">{flowSearch.trim() ? 'Nenhum fluxo corresponde à busca.' : 'Nenhum fluxo persistido neste workspace.'}</div>}
              {visibleFlows.map((flow) => {
                const category = flowCategory(flow);
                const isSelected = flow.id === selectedFlowId;
                return (
                  <button
                    key={flow.id}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => setSelectedFlowId(flow.id)}
                    className={cn('flex w-full items-start gap-3 rounded-lg border p-3 text-left transition', isSelected ? 'border-primary bg-primary/10 ring-1 ring-primary/20' : 'border-border bg-card hover:border-primary/55')}
                  >
                    <div className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-md', category.label === 'Recuperação' ? 'bg-[#031121]/10 text-[#031121] dark:text-blue-300' : category.label === 'Follow-up' ? 'bg-primary/15 text-[#0057d8] dark:text-primary' : 'bg-[#1a9948]/10 text-[#0b7435] dark:text-emerald-400')}><Workflow className="h-4 w-4" /></div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2"><p className="min-w-0 text-sm font-semibold leading-5">{flow.name}</p>{isSelected ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#1a9948]" /> : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />}</div>
                      <p className="mt-1 line-clamp-2 text-xs leading-4 text-muted-foreground">{flow.description ?? 'Descrição não cadastrada.'}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5"><KlmPill tone={category.tone}>{category.label}</KlmPill><KlmPill tone={flow.is_active ? 'green' : 'muted'}>{flow.is_active ? 'Publicado' : 'Rascunho'}</KlmPill><span className="text-2xs text-muted-foreground">{flow.nodes.length} etapas</span></div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="border-t border-border bg-card px-4 py-4">
            {selectedFlow && (
              <div className="mb-3 flex items-start gap-2 rounded-md bg-secondary p-2.5 text-2xs">
                <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8] dark:text-primary" />
                <p className="leading-4"><strong>{selectedFlow.name}</strong><br /><span className="text-muted-foreground">No ambiente atual, o runtime registra e percorre as etapas sem enviar mensagens ou acionar APIs externas.</span></p>
              </div>
            )}
            {!selectedFlow?.is_active && selectedFlow && <p className="mb-3 text-2xs font-medium text-[#a53e3c]">Este fluxo está como rascunho. Publique-o na página de Fluxos antes de ativar.</p>}
            {selectedFlowRun && <p className="mb-3 text-2xs font-medium text-[#0b7435] dark:text-emerald-400">Execução atual: {flowStatusLabel[selectedFlowRun.status] ?? selectedFlowRun.status}.</p>}
            <div className="grid grid-cols-[auto_1fr] gap-2">
              <Button variant="outline" className="h-9 px-3 text-2xs" onClick={() => void flowsQuery.refetch()} disabled={flowsQuery.isFetching}><RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', flowsQuery.isFetching && 'animate-spin')} />Atualizar</Button>
              {selectedFlowRun ? (
                <PermissionGate module="automations" action="manage" actionLabel="pausar ou retomar a execução do fluxo">
                  <Button
                    className="h-9 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
                    disabled={flowRunControlMutation.isPending}
                    onClick={() => flowRunControlMutation.mutate({
                      runId: selectedFlowRun.id,
                      action: selectedFlowRun.status === 'active' ? 'pause' : 'resume',
                    })}
                  >
                    {flowRunControlMutation.isPending ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : selectedFlowRun.status === 'active' ? <Pause className="mr-1.5 h-3.5 w-3.5" /> : <Play className="mr-1.5 h-3.5 w-3.5" />}
                    {flowRunControlMutation.isPending ? 'Atualizando' : selectedFlowRun.status === 'active' ? 'Pausar execução' : 'Retomar execução'}
                  </Button>
                </PermissionGate>
              ) : (
                <PermissionGate module="automations" action="manage" actionLabel="ativar este fluxo">
                  <Button className="h-9 bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={!selectedFlow?.is_active || flowMutation.isPending} onClick={activateSelectedFlow}>{flowMutation.isPending ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Workflow className="mr-1.5 h-3.5 w-3.5" />}{flowMutation.isPending ? 'Ativando' : latestFlowRun?.flow_id === selectedFlow?.id ? 'Executar novamente' : 'Ativar neste caso'}</Button>
                </PermissionGate>
              )}
            </div>
            {selectedFlowRun && (
              <PermissionGate module="automations" action="manage" actionLabel="encerrar a execução do fluxo">
                <Button
                  variant="ghost"
                  className="mt-2 h-8 w-full text-2xs text-destructive hover:text-destructive"
                  disabled={flowRunControlMutation.isPending}
                  onClick={() => flowRunControlMutation.mutate({ runId: selectedFlowRun.id, action: 'cancel' })}
                >
                  <XCircle className="mr-1.5 h-3.5 w-3.5" />Encerrar execução
                </Button>
              </PermissionGate>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={caseOpen && Boolean(selected && selectedThread)} onOpenChange={setCaseOpen}>
        <SheetContent className="flex w-[96vw] flex-col overflow-hidden p-0 sm:max-w-[660px]">
          <SheetHeader className="border-b border-border px-4 py-3 pr-12 text-left">
            <SheetTitle>Caso 360 e CRM</SheetTitle>
            <SheetDescription>Edite dados, documentos, agenda, financeiro, RAG e histórico sem abandonar a conversa.</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 p-3">
            {selected && selectedThread && <ConversationInspector
              contact={selected}
              internalEntries={internalEntries}
              onTool={runTool}
              threadId={selectedThread.id}
              matterId={selectedThread.matterId}
              canEdit={canSend}
              canManageFlows={canManageFlows}
              onUseResponse={insertSavedResponse}
              onCollapse={() => setCaseOpen(false)}
            />}
          </div>
        </SheetContent>
      </Sheet>

      <ConversationTemplatePicker
        open={templatePickerOpen && Boolean(selectedThread)}
        onOpenChange={setTemplatePickerOpen}
        threadId={selectedThread?.id ?? ''}
        matterId={selectedThread?.matterId}
        contactId={selectedThread?.clientId}
        contactName={selectedName}
        onCreateTemplate={(templateId?: string) => {
          setTemplatePickerOpen(false);
          const params = new URLSearchParams();
          // WhatsAppMetaTemplates consumes these deep-link flags. Keep the
          // editor route canonical while preserving a future copy contract.
          if (templateId) params.set('copy', templateId);
          else params.set('create', '1');
          params.set('return', `/app/conversas${selectedThread?.id ? `?thread=${encodeURIComponent(selectedThread.id)}` : ''}`);
          navigate(`/app/templates-meta?${params.toString()}`);
        }}
        onOpenCampaigns={() => {
          setTemplatePickerOpen(false);
          setBroadcastsOpen(true);
        }}
        onSent={({ body }) => {
          if (!selectedThread) return;
          const message: Message = {
            id: `quick-send-${Date.now()}`,
            author: state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace',
            role: 'human',
            text: body,
            time: new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
            note: 'enviado, entrega não confirmada',
            deliveryState: 'local',
            channel: selectedThread.primaryChannel,
            contentLifecycleState: 'original',
            evidenceEventCount: 0,
            evidenceRevisionCount: 0,
          };
          setMessagesByThread((current) => ({
            ...current,
            [activeConversationKey]: [...(current[activeConversationKey] ?? []), message],
          }));
          setNotice('Modelo enviado, entrega não confirmada. O status será atualizado quando houver confirmação do canal.');
          setNoticeAction(null);
          void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', selectedThread.id] });
          void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
        }}
        onOpenFlowBuilder={(href) => {
          const target = new URL(href, window.location.origin);
          const params = new URLSearchParams(target.search);
          params.set('return', '/app/flows');
          navigate(`/app/flows?${params.toString()}`);
        }}
      />

      <Sheet open={broadcastsOpen && Boolean(selectedThread)} onOpenChange={setBroadcastsOpen}>
        <SheetContent className="flex w-[98vw] flex-col overflow-hidden p-0 sm:max-w-[1320px]">
          <SheetHeader className="border-b border-border px-4 py-3 pr-12 text-left">
            <SheetTitle className="flex items-center gap-2"><Megaphone className="h-4 w-4 text-primary" />Disparos e listas</SheetTitle>
            <SheetDescription>Crie, acompanhe e filtre campanhas vinculadas ao contato sem sair da conversa atual.</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <Campaigns
              embedded
              contextThreadId={selectedThread?.id}
              contextMatterId={selectedThread?.matterId}
              contextContactName={selectedName}
              onConversationFilter={(nextFilter, campaignId) => {
                applyContactFilter(nextFilter, campaignId);
                setBroadcastsOpen(false);
              }}
            />
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={audioOpen && Boolean(selectedThread)} onOpenChange={setAudioOpen}>
        <SheetContent className="flex w-[98vw] flex-col overflow-hidden p-0 sm:max-w-[1180px]">
          <SheetHeader className="border-b border-border px-4 py-3 pr-12 text-left">
            <SheetTitle className="flex items-center gap-2"><AudioLines className="h-4 w-4 text-primary" />Áudio assistido</SheetTitle>
            <SheetDescription>Transcreva, revise e prepare respostas de áudio mantendo o contexto integral do atendimento.</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <AudioPage
              embedded
              threadId={selectedThread?.id}
              matterId={selectedThread?.matterId}
              onOpenSources={() => {
                setAudioOpen(false);
                setCaseOpen(true);
              }}
            />
          </div>
        </SheetContent>
      </Sheet>

      {selectedThread?.matterId ? <ConversationDocumentRequestSheet
        open={documentRequestOpen}
        onOpenChange={setDocumentRequestOpen}
        contactName={selectedName}
        matter={selectedMatterQuery.data}
        loading={selectedMatterQuery.isPending}
        submitting={followUpMutation.isPending}
        onPrepare={({ body, documents, followUpHours }) => followUpMutation.mutate({
          matterId: selectedThread.matterId!,
          threadId: selectedThread.id,
          threadKey: activeConversationKey,
          contactName: selectedName,
          channel: selectedThread.primaryChannel,
          dueAt: new Date(Date.now() + followUpHours * 60 * 60 * 1000).toISOString(),
          body,
          documents,
          followUpHours,
        })}
      /> : null}

      {selectedThread?.matterId ? <ConversationProposalPreparationSheet
        open={proposalPreparationOpen}
        onOpenChange={setProposalPreparationOpen}
        contactName={selectedName}
        matter={selectedMatterQuery.data}
        loading={selectedMatterQuery.isPending}
        failed={selectedMatterQuery.isError}
        submitting={proposalMutation.isPending}
        onRetry={() => void selectedMatterQuery.refetch()}
        onOpenCase={() => {
          setProposalPreparationOpen(false);
          setCaseOpen(true);
        }}
        onConfirm={() => proposalMutation.mutate({
          matterId: selectedThread.matterId!,
          threadId: selectedThread.id,
          threadKey: activeConversationKey,
          contactName: selectedName,
          funnelSnapshot: funnelQuery.data,
        })}
      /> : null}

      <Sheet open={Boolean(proposalPreviewPath && selectedThread)} onOpenChange={(open) => !open && setProposalPreviewPath(null)}>
        <SheetContent className="flex w-[98vw] flex-col overflow-hidden p-0 sm:max-w-[760px]">
          <SheetHeader className="border-b border-border px-4 py-3 pr-12 text-left">
            <SheetTitle className="flex items-center gap-2"><FileSignature className="h-4 w-4 text-primary" />Prévia da proposta</SheetTitle>
            <SheetDescription>A proposta permanece vinculada ao caso e é revisada sem retirar o atendente da conversa.</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto bg-background" aria-label="Prévia contextual da proposta">
            {proposalPreviewPath ? <ConversationContextRoute path={proposalPreviewPath} onNavigate={handleContextNavigate} /> : null}
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={Boolean(contextWorkspace && selectedThread)} onOpenChange={(open) => !open && setContextWorkspace(null)}>
        <SheetContent className="flex w-[98vw] flex-col overflow-hidden p-0 sm:max-w-[1320px]">
          <SheetHeader className="border-b border-border px-4 py-3 pr-12 text-left">
            <SheetTitle className="flex items-center gap-2"><PanelRight className="h-4 w-4 text-primary" />{contextWorkspace?.title}</SheetTitle>
            <SheetDescription>{contextWorkspace?.description}</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto bg-background p-4" aria-label={contextWorkspace ? `Contexto: ${contextWorkspace.title}` : 'Ferramenta contextual'}>
            {contextWorkspace ? <ConversationContextRoute path={contextWorkspace.path} onNavigate={handleContextNavigate} /> : null}
          </div>
        </SheetContent>
      </Sheet>

      {selected && selectedThread?.matterId ? (
        <ConversationSchedulingDialog
          open={scheduleOpen}
          onOpenChange={setScheduleOpen}
          matterId={selectedThread.matterId}
          contactName={selected.name}
          onBooked={(meeting) => {
            updateDraftForThread('outbound', activeConversationKey, `${selected.name}, reservei uma reunião online para ${new Date(meeting.startsAt).toLocaleString('pt-BR')}. Posso confirmar esse horário?`);
            setComposeMode('outbound');
            setNotice(`Reunião criada para ${new Date(meeting.startsAt).toLocaleString('pt-BR')}, sem convite externo.`);
            setNoticeAction({ label: 'Agendar outro horário', kind: 'schedule' });
          }}
          onLinkCreated={(_, absoluteUrl) => {
            updateDraftForThread('outbound', activeConversationKey, `${selected.name}, escolha o melhor horário disponível para nossa reunião on-line: ${absoluteUrl}`);
            setComposeMode('outbound');
            setNotice('Página de agendamento vinculada ao card e inserida na resposta. Nenhuma mensagem foi enviada automaticamente.');
            setNoticeAction({ label: 'Reabrir disponibilidade', kind: 'schedule' });
          }}
        />
      ) : null}

      <ConversationMessageSchedulingDialog
        open={messageScheduleOpen && Boolean(selectedThread)}
        onOpenChange={setMessageScheduleOpen}
        threadId={selectedThread?.id}
        initialBody={outboundDraft}
        canEdit={canSend}
        onScheduled={(message, consumedBody) => {
          if (outboundDraft.trim() === consumedBody) updateDraftForThread('outbound', activeConversationKey, '');
          setNotice(`Mensagem agendada para ${new Date(message.scheduledAt).toLocaleString('pt-BR')}.`);
          setNoticeAction(null);
        }}
      />

      {availabilityTargetUserId ? (
        <StaffAvailabilitySheet
          open={availabilityOpen}
          onOpenChange={setAvailabilityOpen}
          staff={{
            id: availabilityTargetUserId,
            name: availabilityTargetName,
            role: availabilityTargetUserId === selectedThread?.assignedHumanId
              ? 'Responsável pelo atendimento'
              : activeWorkspaceRole.replaceAll('_', ' '),
          }}
          initialValue={staffAvailabilityInitialValue}
          redirectOptions={availabilityRedirectOptions}
          approval={availabilityApproval}
          disabled={
            staffAvailabilityStateQuery.isError
            || staffEffectiveAvailabilityQuery.isError
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

      <ConversationRichMessageDialog
        open={richMessageOpen && Boolean(selectedThread)}
        onOpenChange={setRichMessageOpen}
        threadId={selectedThread?.id}
        matterId={selectedThread?.matterId}
        route={selectedOutboundRoute}
        routeCommand={selectedOutboundRoute ? {
          endpointId: selectedOutboundRoute.endpointId,
          bindingId: selectedOutboundRoute.bindingId,
          connectionId: selectedOutboundRoute.connectionId,
          connectionKey: selectedOutboundRoute.connectionKey,
          transport: selectedOutboundRoute.transportMode,
        } : {}}
        initialBody={outboundDraft}
        authorLabel={state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace'}
        canEdit={canSend}
        externalGateOpen={Boolean(omnichannelContextQuery.data?.externalGateOpen)}
        onCreated={(_, summary) => {
          if (outboundDraft.trim()) updateDraftForThread('outbound', activeConversationKey, '');
          setComposeMode('outbound');
          setNotice(`${summary} registrado no histórico pela rota ${selectedOutboundRoute ? conversationRouteLabel(selectedOutboundRoute) : 'selecionada'}. ${omnichannelContextQuery.data?.externalGateOpen ? 'A entrega seguirá a política da conexão.' : 'O gate local continua fechado; nenhuma API externa foi acionada.'}`);
          setNoticeAction(null);
        }}
      />

      <AlertDialog open={metaWindowConfirmationOpen} onOpenChange={setMetaWindowConfirmationOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Janela de atendimento encerrada</AlertDialogTitle>
            <AlertDialogDescription>
              Já se passaram mais de 24 horas desde a última mensagem do cliente. Pela regra da API oficial do WhatsApp, esta resposta precisa usar um modelo aprovado e será registrada como disparo. Deseja escolher o modelo e continuar?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setMetaWindowConfirmationOpen(false);
                setRichMessageOpen(true);
              }}
            >
              Escolher modelo e continuar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={newConversationOpen} onOpenChange={setNewConversationOpen}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-[620px]">
          <DialogHeader>
            <DialogTitle>Nova conversa no WhatsApp</DialogTitle>
            <DialogDescription>
              O primeiro contato usa obrigatoriamente um template aprovado pela Meta. A conversa será criada nesta Inbox e ficará vinculada ao workspace atual.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="new-conversation-connection">Número remetente</Label>
              <Select value={newConversationConnectionId} onValueChange={(value) => {
                setNewConversationConnectionId(value);
                setNewConversationTemplateId('');
              }}>
                <SelectTrigger id="new-conversation-connection"><SelectValue placeholder="Selecione a conexão" /></SelectTrigger>
                <SelectContent>
                  {(whatsappConnectionsQuery.data ?? []).filter((connection) => connection.status === 'connected').map((connection) => (
                    <SelectItem key={connection.id} value={connection.id}>{connection.displayPhoneNumber || connection.phoneNumber || `Conexão ${connection.id.slice(0, 8)}`}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-conversation-name">Nome do contato</Label>
              <Input id="new-conversation-name" value={newConversationName} onChange={(event) => setNewConversationName(event.target.value)} placeholder="Nome ou razão social" autoComplete="off" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-conversation-phone">Telefone com DDI</Label>
              <Input
                id="new-conversation-phone"
                value={newConversationPhone}
                onChange={(event) => setNewConversationPhone(event.target.value)}
                onBlur={() => {
                  if (normalizedNewConversationPhone) setNewConversationPhone(normalizedNewConversationPhone);
                }}
                placeholder="5561999999999"
                inputMode="tel"
                autoComplete="tel"
                aria-describedby="new-conversation-phone-help"
                aria-invalid={Boolean(newConversationPhone && !normalizedNewConversationPhone)}
              />
              <p id="new-conversation-phone-help" className="text-xs text-muted-foreground">
                {META_PHONE_HELP} Se você colar um número brasileiro formatado e com DDD, ajustaremos o +55 automaticamente.
              </p>
              {newConversationPhone && !normalizedNewConversationPhone ? (
                <p role="alert" className="text-xs text-destructive">O número precisa ter DDD e ser válido para WhatsApp.</p>
              ) : null}
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="new-conversation-template">Template aprovado</Label>
              <Select value={newConversationTemplateId} onValueChange={setNewConversationTemplateId} disabled={!newConversationConnectionId || newConversationTemplatesQuery.isPending}>
                <SelectTrigger id="new-conversation-template"><SelectValue placeholder={newConversationTemplatesQuery.isPending ? 'Carregando templates...' : 'Selecione o template'} /></SelectTrigger>
                <SelectContent>
                  {approvedNewConversationTemplates.map((template) => (
                    <SelectItem key={template.id} value={template.id}>{template.name} · {template.language} · {template.category || 'sem categoria'}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {newConversationTemplatesQuery.isError ? <p className="text-xs text-destructive">Não foi possível consultar os templates desta conexão.</p> : null}
              {newConversationTemplatesQuery.isSuccess && approvedNewConversationTemplates.length === 0 ? <p className="text-xs text-amber-700">Nenhum template aprovado está disponível. Sincronize ou aprove um template antes de iniciar.</p> : null}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-conversation-header-parameters">Variáveis do cabeçalho</Label>
              <Textarea id="new-conversation-header-parameters" value={newConversationHeaderParameters} onChange={(event) => setNewConversationHeaderParameters(event.target.value)} rows={3} placeholder="Uma variável por linha" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-conversation-body-parameters">Variáveis do corpo</Label>
              <Textarea id="new-conversation-body-parameters" value={newConversationBodyParameters} onChange={(event) => setNewConversationBodyParameters(event.target.value)} rows={3} placeholder="Uma variável por linha, na ordem {{1}}, {{2}}..." />
            </div>
            {selectedNewConversationTemplate ? (
              <div className="space-y-1 rounded-md border border-border bg-secondary/40 p-3 sm:col-span-2">
                <p className="text-xs font-semibold">Prévia do template</p>
                <p className="whitespace-pre-wrap text-xs text-muted-foreground">{selectedNewConversationTemplatePreview || `${selectedNewConversationTemplate.name} (${selectedNewConversationTemplate.language})`}</p>
              </div>
            ) : null}
            {startConversationMutation.isError ? (
              <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive sm:col-span-2">
                {startConversationMutation.error instanceof ApiError ? startConversationMutation.error.message : 'Não foi possível iniciar a conversa.'}
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setNewConversationOpen(false)} disabled={startConversationMutation.isPending}>Cancelar</Button>
            <Button
              type="button"
              onClick={() => startConversationMutation.mutate()}
              disabled={startConversationMutation.isPending || !newConversationConnectionId || !newConversationTemplateId || !normalizedNewConversationPhone}
            >
              {startConversationMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
              Enviar template e abrir conversa
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={followUpEditorOpen} onOpenChange={setFollowUpEditorOpen}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>Editar follow-up</DialogTitle>
            <DialogDescription>
              Atualize a próxima tentativa deste atendimento. A alteração ficará registrada no backend e disponível para a automação.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="conversation-follow-up-due-at">Data e hora</Label>
              <Input
                id="conversation-follow-up-due-at"
                type="datetime-local"
                value={followUpDueAtDraft}
                onChange={(event) => setFollowUpDueAtDraft(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="conversation-follow-up-reason">Motivo e orientação</Label>
              <Textarea
                id="conversation-follow-up-reason"
                value={followUpReasonDraft}
                onChange={(event) => setFollowUpReasonDraft(event.target.value)}
                rows={4}
                placeholder="Ex.: retomar após o envio dos documentos solicitados."
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setFollowUpEditorOpen(false)}
              disabled={updateFollowUpMutation.isPending}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              onClick={() => {
                if (!selectedFollowUp || !followUpDueAtDraft) return;
                updateFollowUpMutation.mutate({
                  id: selectedFollowUp.id,
                  dueAt: followUpDueAtDraft,
                  reason: followUpReasonDraft,
                });
              }}
              disabled={!selectedFollowUp || !followUpDueAtDraft || updateFollowUpMutation.isPending}
            >
              {updateFollowUpMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
              Salvar follow-up
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
