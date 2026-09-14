import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ElementType,
} from 'react';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Activity,
  AlertCircle,
  Archive,
  ArrowDown,
  ArrowUp,
  Building2,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDot,
  Clock3,
  Download,
  FileText,
  Filter,
  Inbox,
  Link2,
  Loader2,
  MessageSquare,
  Paperclip,
  PhoneCall,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  UserRound,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';
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
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useToast } from '@/hooks/use-toast';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { canPlatform, isPlatformRoot } from '@/lib/platform-access';
import { resolveWebSocketBase } from '@/services/api-origin';
import { ApiError, apiFetch } from '@/services/http';
import { createPlatformExport } from '@/services/platform-exports';
import {
  getPlatformSupportAvailabilitySettings,
  getPlatformSupportEscalationSettings,
  updatePlatformSupportAvailabilitySettings,
  updatePlatformSupportEscalationSettings,
  type PlatformSupportAvailabilityDraft,
  type PlatformSupportEscalationDraft,
  type SupportAvailabilityDay,
  type SupportAvailabilityWeekday,
} from '@/services/platform-support-settings';
import { useAppStore } from '@/store/app-context';

type TicketType = 'problem' | 'instability' | 'suggestion';
type TicketPriority = 'low' | 'normal' | 'high' | 'urgent';
type TicketSeverity = 'low' | 'medium' | 'high' | 'critical';
type TicketQueue = 'support' | 'technical';
type TicketStatus =
  | 'new'
  | 'triage'
  | 'in_progress'
  | 'waiting_customer'
  | 'waiting_internal'
  | 'validating'
  | 'resolved'
  | 'closed'
  | 'cancelled';
type MessageVisibility = 'public' | 'internal';

interface SupportUser {
  id: string;
  name: string;
  avatarUrl?: string | null;
}

interface SupportTicket {
  id: string;
  publicNumber: string;
  workspaceId: string;
  workspaceName?: string | null;
  reporterUserId: string;
  assignedToUserId?: string | null;
  ticketType: TicketType;
  priority: TicketPriority;
  severity: TicketSeverity;
  queue: TicketQueue;
  status: TicketStatus;
  subject: string;
  description: string;
  route?: string | null;
  technicalContext: Record<string, unknown>;
  errorFingerprint?: string | null;
  linkedErrorLogId?: string | null;
  linkedIncidentId?: string | null;
  linkedReleaseId?: string | null;
  supportAccessRequestId?: string | null;
  duplicateOfTicketId?: string | null;
  version: number;
  firstResponseDueAt: string;
  resolutionDueAt: string;
  firstRespondedAt?: string | null;
  resolvedAt?: string | null;
  closedAt?: string | null;
  reopenedAt?: string | null;
  satisfactionScore?: number | null;
  satisfactionComment?: string | null;
  createdAt: string;
  updatedAt: string;
  reporter?: SupportUser | null;
  assignee?: SupportUser | null;
}

interface SupportMessage {
  id: string;
  authorUserId?: string | null;
  authorLabel: string;
  visibility: MessageVisibility;
  body: string;
  createdAt: string;
}

interface SupportAttachment {
  id: string;
  messageId?: string | null;
  storageObjectVersionId: string;
  originalFilename: string;
  contentType: string;
  byteSize?: number | null;
  state: string;
  avStatus: string;
  createdAt: string;
}

interface SupportEvent {
  id: string;
  actorUserId?: string | null;
  eventType: string;
  summary: string;
  beforeData: Record<string, unknown>;
  afterData: Record<string, unknown>;
  createdAt: string;
}

interface SupportTicketDetail extends SupportTicket {
  messages: SupportMessage[];
  attachments: SupportAttachment[];
  events: SupportEvent[];
  collaboratorIds: string[];
}

interface SupportTicketPage {
  items: SupportTicket[];
  nextCursor?: string | null;
}

interface Distribution {
  label: string;
  value: number;
}

interface DurationMetric {
  averageSeconds: number;
  medianSeconds: number;
  p90Seconds: number;
  p95Seconds: number;
}

interface SupportMetrics {
  periodDays: number;
  previousPeriodChangePct: Record<string, number>;
  new: number;
  open: number;
  resolved: number;
  reopened: number;
  overdue: number;
  nearDue: number;
  reopenRatePct: number;
  satisfactionAverage?: number | null;
  firstResponse: DurationMetric;
  resolution: DurationMetric;
  backlogByQueue: Distribution[];
  backlogBySeverity: Distribution[];
  backlogByAge: Distribution[];
  performanceByQueue: Distribution[];
  performanceByAssignee: Distribution[];
  instabilityByVersion: Distribution[];
  instabilityByPage: Distribution[];
  instabilityByBrowser: Distribution[];
  suggestionsByDestination: Distribution[];
  recurrenceByWorkspace: Distribution[];
  relatedIncidents: number;
}

interface SavedFilter {
  id: string;
  ownerUserId: string;
  name: string;
  filters: Record<string, unknown>;
  isDefault: boolean;
  createdAt: string;
  updatedAt: string;
}

interface TicketFilters {
  search: string;
  workspaceId: string;
  statuses: TicketStatus[];
  queues: TicketQueue[];
  severities: TicketSeverity[];
  ticketTypes: TicketType[];
  assignedToUserId: string;
  overdue: 'all' | 'yes' | 'no';
}

interface TicketPatch {
  status?: TicketStatus;
  priority?: TicketPriority;
  severity?: TicketSeverity;
  queue?: TicketQueue;
  assignedToUserId?: string;
  linkedErrorLogId?: string | null;
  linkedIncidentId?: string | null;
  linkedReleaseId?: string | null;
  supportAccessRequestId?: string | null;
}

interface PlatformSupportTicketsPanelProps {
  enabled?: boolean;
}

const QUERY_ROOT = ['platform-support'] as const;
const EMPTY_FILTERS: TicketFilters = {
  search: '',
  workspaceId: '',
  statuses: [],
  queues: [],
  severities: [],
  ticketTypes: [],
  assignedToUserId: '',
  overdue: 'all',
};

const STATUS_LABELS: Record<TicketStatus, string> = {
  new: 'Novo',
  triage: 'Em triagem',
  in_progress: 'Em atendimento',
  waiting_customer: 'Aguardando cliente',
  waiting_internal: 'Aguardando interno',
  validating: 'Em validação',
  resolved: 'Resolvido',
  closed: 'Encerrado',
  cancelled: 'Cancelado',
};

const STATUS_OPTIONS = Object.entries(STATUS_LABELS) as Array<[TicketStatus, string]>;
const QUEUE_LABELS: Record<TicketQueue, string> = {
  support: 'Atendimento',
  technical: 'Técnica',
};
const SEVERITY_LABELS: Record<TicketSeverity, string> = {
  low: 'Baixa',
  medium: 'Média',
  high: 'Alta',
  critical: 'Crítica',
};
const TYPE_LABELS: Record<TicketType, string> = {
  problem: 'Problema',
  instability: 'Instabilidade',
  suggestion: 'Sugestão',
};
const PRIORITY_LABELS: Record<TicketPriority, string> = {
  low: 'Baixa',
  normal: 'Normal',
  high: 'Alta',
  urgent: 'Urgente',
};

const STATUS_TRANSITIONS: Record<TicketStatus, TicketStatus[]> = {
  new: ['triage', 'in_progress', 'cancelled'],
  triage: ['in_progress', 'waiting_customer', 'waiting_internal', 'cancelled'],
  in_progress: ['waiting_customer', 'waiting_internal', 'validating', 'resolved', 'cancelled'],
  waiting_customer: ['in_progress', 'validating', 'resolved', 'cancelled'],
  waiting_internal: ['in_progress', 'validating', 'resolved', 'cancelled'],
  validating: ['in_progress', 'resolved', 'cancelled'],
  resolved: ['closed', 'in_progress'],
  closed: ['in_progress'],
  cancelled: ['in_progress'],
};

function formatDate(value?: string | null): string {
  if (!value) return 'Não informado';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Não informado';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
}

function formatDuration(totalSeconds?: number | null): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds)) return 'Sem dados';
  const seconds = Math.max(0, Math.round(totalSeconds));
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}min`;
  return `${Math.max(1, minutes)}min`;
}

function formatMetricDuration(totalSeconds?: number | null): string {
  return totalSeconds && totalSeconds > 0 ? formatDuration(totalSeconds) : 'Sem dados';
}

function formatBytes(value?: number | null): string {
  if (value == null) return 'Tamanho indisponível';
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function comparisonLabel(value?: number): string {
  if (value == null || !Number.isFinite(value)) return 'Sem comparação';
  if (value === 0) return 'Igual ao período anterior';
  return `${Math.abs(value).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}% ${value > 0 ? 'acima' : 'abaixo'} do anterior`;
}

function technicalValue(context: Record<string, unknown>, key: string): string {
  const value = context[key];
  return typeof value === 'string' && value.trim() ? value : 'Não informado';
}

function filterCount(filters: TicketFilters): number {
  return (
    Number(Boolean(filters.search.trim()))
    + Number(Boolean(filters.workspaceId))
    + filters.statuses.length
    + filters.queues.length
    + filters.severities.length
    + filters.ticketTypes.length
    + Number(Boolean(filters.assignedToUserId))
    + Number(filters.overdue !== 'all')
  );
}

function normalizeSavedFilters(filters: Record<string, unknown>): TicketFilters {
  const allowed = <T extends string>(value: unknown, options: readonly T[]): T[] => (
    Array.isArray(value)
      ? value.filter((item): item is T => typeof item === 'string' && options.includes(item as T))
      : []
  );
  return {
    search: typeof filters.search === 'string' ? filters.search : '',
    workspaceId: typeof filters.workspaceId === 'string' ? filters.workspaceId : '',
    statuses: allowed(filters.statuses, Object.keys(STATUS_LABELS) as TicketStatus[]),
    queues: allowed(filters.queues, Object.keys(QUEUE_LABELS) as TicketQueue[]),
    severities: allowed(filters.severities, Object.keys(SEVERITY_LABELS) as TicketSeverity[]),
    ticketTypes: allowed(filters.ticketTypes, Object.keys(TYPE_LABELS) as TicketType[]),
    assignedToUserId: typeof filters.assignedToUserId === 'string' ? filters.assignedToUserId : '',
    overdue: filters.overdue === 'yes' || filters.overdue === 'no' ? filters.overdue : 'all',
  };
}

function buildTicketQuery(filters: TicketFilters, cursor: string | null): string {
  const params = new URLSearchParams({ limit: '50' });
  if (cursor) params.set('cursor', cursor);
  if (filters.search.trim()) params.set('search', filters.search.trim());
  if (filters.workspaceId) params.set('workspaceId', filters.workspaceId);
  if (filters.assignedToUserId) params.set('assignedToUserId', filters.assignedToUserId);
  if (filters.overdue !== 'all') params.set('overdue', String(filters.overdue === 'yes'));
  filters.statuses.forEach((value) => params.append('statuses', value));
  filters.queues.forEach((value) => params.append('queues', value));
  filters.severities.forEach((value) => params.append('severities', value));
  filters.ticketTypes.forEach((value) => params.append('ticketTypes', value));
  return params.toString();
}

function replaceTicket(
  data: InfiniteData<SupportTicketPage> | undefined,
  ticketId: string,
  patch: Partial<SupportTicket>,
): InfiniteData<SupportTicketPage> | undefined {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      items: page.items.map((ticket) => (
        ticket.id === ticketId ? { ...ticket, ...patch } : ticket
      )),
    })),
  };
}

function severityVariant(severity: TicketSeverity): 'default' | 'secondary' | 'outline' | 'destructive' {
  if (severity === 'critical') return 'destructive';
  if (severity === 'high') return 'default';
  if (severity === 'medium') return 'secondary';
  return 'outline';
}

function statusVariant(status: TicketStatus): 'default' | 'secondary' | 'outline' | 'destructive' {
  if (status === 'cancelled') return 'destructive';
  if (status === 'resolved' || status === 'closed') return 'secondary';
  if (status === 'new' || status === 'triage') return 'default';
  return 'outline';
}

function slaState(ticket: SupportTicket): {
  label: string;
  detail: string;
  overdue: boolean;
} {
  const terminal = ['resolved', 'closed', 'cancelled'].includes(ticket.status);
  if (!ticket.firstRespondedAt) {
    const remaining = new Date(ticket.firstResponseDueAt).getTime() - Date.now();
    return {
      label: remaining < 0 ? '1ª resposta vencida' : '1ª resposta',
      detail: remaining < 0
        ? `${formatDuration(Math.abs(remaining) / 1000)} em atraso`
        : `${formatDuration(remaining / 1000)} restantes`,
      overdue: remaining < 0,
    };
  }
  if (!terminal) {
    const remaining = new Date(ticket.resolutionDueAt).getTime() - Date.now();
    return {
      label: remaining < 0 ? 'Resolução vencida' : 'Resolução',
      detail: remaining < 0
        ? `${formatDuration(Math.abs(remaining) / 1000)} em atraso`
        : `${formatDuration(remaining / 1000)} restantes`,
      overdue: remaining < 0,
    };
  }
  return {
    label: ticket.status === 'resolved' ? 'Resolvido' : 'Finalizado',
    detail: formatDate(ticket.resolvedAt ?? ticket.closedAt),
    overdue: false,
  };
}

function stageDurations(ticket: SupportTicketDetail): Array<{
  status: TicketStatus;
  start: string;
  end: string;
  seconds: number;
}> {
  const changes = [...ticket.events]
    .sort((left, right) => new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime())
    .filter((event) => typeof event.afterData.status === 'string');
  let currentStatus: TicketStatus = 'new';
  let startedAt = ticket.createdAt;
  const stages: Array<{ status: TicketStatus; start: string; end: string; seconds: number }> = [];
  changes.forEach((event) => {
    const nextStatus = event.afterData.status as TicketStatus;
    if (!(nextStatus in STATUS_LABELS)) return;
    if (event.eventType === 'created') {
      currentStatus = nextStatus;
      startedAt = event.createdAt;
      return;
    }
    if (nextStatus === currentStatus) return;
    stages.push({
      status: currentStatus,
      start: startedAt,
      end: event.createdAt,
      seconds: Math.max(0, (new Date(event.createdAt).getTime() - new Date(startedAt).getTime()) / 1000),
    });
    currentStatus = nextStatus;
    startedAt = event.createdAt;
  });
  const endedAt = ticket.closedAt ?? ticket.resolvedAt ?? new Date().toISOString();
  stages.push({
    status: currentStatus,
    start: startedAt,
    end: endedAt,
    seconds: Math.max(0, (new Date(endedAt).getTime() - new Date(startedAt).getTime()) / 1000),
  });
  return stages;
}

function FilterCheckboxGroup<T extends string>({
  label,
  values,
  options,
  onChange,
}: {
  label: string;
  values: T[];
  options: Array<[T, string]>;
  onChange: (values: T[]) => void;
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-semibold text-foreground">{label}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map(([value, optionLabel]) => {
          const checked = values.includes(value);
          return (
            <label key={value} className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
              <Checkbox
                checked={checked}
                onCheckedChange={(next) => {
                  onChange(next
                    ? [...values, value]
                    : values.filter((item) => item !== value));
                }}
              />
              <span>{optionLabel}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function MetricCard({
  title,
  value,
  comparison,
  icon: Icon,
  tone = 'neutral',
  loading,
}: {
  title: string;
  value: string;
  comparison?: number;
  icon: ElementType;
  tone?: 'neutral' | 'good' | 'warning' | 'danger';
  loading?: boolean;
}) {
  const comparisonTone = comparison == null || comparison === 0
    ? 'text-muted-foreground'
    : comparison > 0
      ? 'text-amber-700 dark:text-amber-300'
      : 'text-emerald-700 dark:text-emerald-300';
  const toneClass = {
    neutral: 'text-foreground',
    good: 'text-emerald-700 dark:text-emerald-300',
    warning: 'text-amber-700 dark:text-amber-300',
    danger: 'text-destructive',
  }[tone];
  return (
    <Card className="rounded-md">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className={`h-4 w-4 ${toneClass}`} aria-hidden="true" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <>
            <p className={`text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</p>
            {comparison != null && (
              <p className={`mt-1 flex items-center gap-1 text-xs ${comparisonTone}`}>
                {comparison > 0 ? <ArrowUp className="h-3 w-3" /> : comparison < 0 ? <ArrowDown className="h-3 w-3" /> : <CircleDot className="h-3 w-3" />}
                {comparisonLabel(comparison)}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function DistributionPanel({
  title,
  items,
  labels,
}: {
  title: string;
  items: Distribution[] | undefined;
  labels?: Record<string, string>;
}) {
  const rows = items?.slice(0, 5) ?? [];
  const maximum = Math.max(...rows.map((item) => item.value), 1);
  return (
    <section className="min-w-0 rounded-md border p-3" aria-label={title}>
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      {rows.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">Sem dados no período.</p>
      ) : (
        <div className="mt-3 space-y-2">
          {rows.map((item) => (
            <div key={item.label} className="min-w-0">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate" title={labels?.[item.label] ?? item.label}>
                  {labels?.[item.label] ?? item.label}
                </span>
                <span className="shrink-0 font-semibold tabular-nums">{item.value}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${Math.max(6, (item.value / maximum) * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

const SUPPORT_WEEKDAY_LABELS: Record<SupportAvailabilityWeekday, string> = {
  monday: 'Segunda-feira',
  tuesday: 'Terça-feira',
  wednesday: 'Quarta-feira',
  thursday: 'Quinta-feira',
  friday: 'Sexta-feira',
  saturday: 'Sábado',
  sunday: 'Domingo',
};

const SUPPORT_TIMEZONES = [
  ['America/Sao_Paulo', 'Brasília'],
  ['America/Manaus', 'Manaus'],
  ['America/Cuiaba', 'Cuiabá'],
  ['America/Rio_Branco', 'Rio Branco'],
  ['UTC', 'UTC'],
] as const;

function cloneAvailabilityDraft(
  settings: PlatformSupportAvailabilityDraft,
): PlatformSupportAvailabilityDraft {
  return {
    enabled: settings.enabled,
    timezone: settings.timezone,
    days: settings.days.map((day) => ({ ...day })),
    offlineMessage: settings.offlineMessage,
  };
}

function SupportAvailabilitySettingsSection({
  enabled,
  canManage,
}: {
  enabled: boolean;
  canManage: boolean;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<PlatformSupportAvailabilityDraft | null>(null);
  const [operationReason, setOperationReason] = useState('');
  const queryKey = [...QUERY_ROOT, 'availability-settings'] as const;
  const settingsQuery = useQuery({
    queryKey,
    queryFn: getPlatformSupportAvailabilitySettings,
    enabled,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (settingsQuery.data) {
      setDraft(cloneAvailabilityDraft(settingsQuery.data));
    }
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (nextDraft: PlatformSupportAvailabilityDraft) => (
      updatePlatformSupportAvailabilitySettings(
        nextDraft,
        settingsQuery.data?.etag ?? null,
        operationReason,
      )
    ),
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKey, saved);
      setDraft(cloneAvailabilityDraft(saved));
      setOperationReason('');
      toast({ title: 'Horário do atendimento atualizado.' });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void settingsQuery.refetch();
        toast({
          title: 'A configuração foi alterada por outra pessoa.',
          description: 'Os dados mais recentes foram recarregados.',
          variant: 'destructive',
        });
        return;
      }
      toast({
        title: 'Não foi possível atualizar o horário.',
        description: error instanceof Error ? error.message : 'Tente novamente.',
        variant: 'destructive',
      });
    },
  });

  const updateDay = (
    weekday: SupportAvailabilityWeekday,
    patch: Partial<SupportAvailabilityDay>,
  ) => {
    setDraft((current) => current
      ? {
        ...current,
        days: current.days.map((day) => (
          day.day === weekday ? { ...day, ...patch } : day
        )),
      }
      : current);
  };

  const configuredTimezone = draft?.timezone;
  const timezoneIsPreset = SUPPORT_TIMEZONES.some(([value]) => value === configuredTimezone);
  const status = settingsQuery.data;
  const stateLabel = !status?.enabled
    ? 'Desativado'
    : status.currentState === 'online'
      ? 'Online'
      : 'Offline';
  const draftIsValid = Boolean(
    draft
    && draft.offlineMessage.trim().length >= 3
    && draft.days.every((day) => (
      /^\d{2}:\d{2}$/.test(day.startTime)
      && /^\d{2}:\d{2}$/.test(day.endTime)
      && (!day.enabled || day.startTime < day.endTime)
    )),
  );

  return (
    <section className="border-b pb-4" aria-labelledby="support-availability-title">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <CalendarClock className="h-4 w-4 text-muted-foreground" />
            <h3 id="support-availability-title" className="text-sm font-semibold">
              Atendimento ao vivo
            </h3>
            {!settingsQuery.isLoading && !settingsQuery.isError && (
              <Badge
                variant={status?.currentState === 'online' && status.enabled ? 'secondary' : 'outline'}
                className="gap-1"
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    status?.currentState === 'online' && status.enabled
                      ? 'bg-emerald-600'
                      : 'bg-muted-foreground'
                  }`}
                />
                {stateLabel}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {status?.nextChangeAt
              ? `Próxima mudança em ${formatDate(status.nextChangeAt)}`
              : status?.timezone ?? 'Carregando configuração'}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-full gap-2 sm:w-auto"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          aria-controls="support-availability-settings"
        >
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          {expanded ? 'Recolher' : 'Configurar'}
        </Button>
      </div>

      {expanded && (
        <div id="support-availability-settings" className="mt-4 border-t pt-4">
          {settingsQuery.isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-28 w-full" />
            </div>
          ) : settingsQuery.isError || !draft ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" role="alert">
              <p className="text-sm text-destructive">
                Não foi possível carregar a configuração do atendimento.
              </p>
              <Button type="button" variant="outline" size="sm" onClick={() => void settingsQuery.refetch()}>
                Tentar novamente
              </Button>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(220px,320px)]">
                <div className="flex items-center justify-between gap-4 rounded-md border px-3 py-2">
                  <div>
                    <Label htmlFor="support-live-enabled">Atendimento habilitado</Label>
                    <p className="text-xs text-muted-foreground">{stateLabel}</p>
                  </div>
                  <Switch
                    id="support-live-enabled"
                    checked={draft.enabled}
                    onCheckedChange={(checked) => setDraft((current) => (
                      current ? { ...current, enabled: checked } : current
                    ))}
                    disabled={!canManage || saveMutation.isPending}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="support-live-timezone">Fuso horário</Label>
                  <Select
                    value={draft.timezone}
                    onValueChange={(timezone) => setDraft((current) => (
                      current ? { ...current, timezone } : current
                    ))}
                    disabled={!canManage || saveMutation.isPending}
                  >
                    <SelectTrigger id="support-live-timezone">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {!timezoneIsPreset && configuredTimezone && (
                        <SelectItem value={configuredTimezone}>{configuredTimezone}</SelectItem>
                      )}
                      {SUPPORT_TIMEZONES.map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label} ({value})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="divide-y rounded-md border">
                {draft.days.map((day) => (
                  <div
                    key={day.day}
                    className="grid gap-3 px-3 py-3 sm:grid-cols-[minmax(150px,1fr)_120px_120px] sm:items-end"
                  >
                    <div className="flex min-w-0 items-center gap-3 self-center">
                      <Checkbox
                        id={`support-live-${day.day}`}
                        checked={day.enabled}
                        onCheckedChange={(checked) => updateDay(day.day, { enabled: checked === true })}
                        disabled={!canManage || saveMutation.isPending}
                      />
                      <Label htmlFor={`support-live-${day.day}`} className="truncate">
                        {SUPPORT_WEEKDAY_LABELS[day.day]}
                      </Label>
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor={`support-live-${day.day}-start`} className="text-xs text-muted-foreground">
                        Início
                      </Label>
                      <Input
                        id={`support-live-${day.day}-start`}
                        type="time"
                        value={day.startTime}
                        onChange={(event) => updateDay(day.day, { startTime: event.target.value })}
                        required
                        disabled={!canManage || saveMutation.isPending}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor={`support-live-${day.day}-end`} className="text-xs text-muted-foreground">
                        Fim
                      </Label>
                      <Input
                        id={`support-live-${day.day}-end`}
                        type="time"
                        value={day.endTime}
                        onChange={(event) => updateDay(day.day, { endTime: event.target.value })}
                        required
                        disabled={!canManage || saveMutation.isPending}
                      />
                    </div>
                  </div>
                ))}
              </div>

              <div className="space-y-2">
                <Label htmlFor="support-live-message">Mensagem fora do horário</Label>
                <Textarea
                  id="support-live-message"
                  value={draft.offlineMessage}
                  onChange={(event) => setDraft((current) => (
                    current ? { ...current, offlineMessage: event.target.value } : current
                  ))}
                  maxLength={500}
                  minLength={3}
                  rows={3}
                  required
                  disabled={!canManage || saveMutation.isPending}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="support-live-reason">Motivo da alteração</Label>
                <Textarea
                  id="support-live-reason"
                  value={operationReason}
                  onChange={(event) => setOperationReason(event.target.value)}
                  minLength={8}
                  maxLength={500}
                  rows={2}
                  placeholder="Explique por que o horário ou a mensagem será alterado."
                  disabled={!canManage || saveMutation.isPending}
                />
              </div>

              <div className="flex items-center justify-end gap-3">
                {!canManage && (
                  <p className="mr-auto text-xs text-muted-foreground">Visualização somente leitura.</p>
                )}
                {canManage && (
                  <Button
                    type="button"
                    className="gap-2"
                    onClick={() => saveMutation.mutate(draft)}
                    disabled={saveMutation.isPending || !draftIsValid || operationReason.trim().length < 8}
                  >
                    {saveMutation.isPending
                      ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Save className="h-4 w-4" />}
                    Salvar configuração
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

const ESCALATION_WEEKDAYS: [SupportAvailabilityWeekday, string][] = [
  ['monday', 'Seg'],
  ['tuesday', 'Ter'],
  ['wednesday', 'Qua'],
  ['thursday', 'Qui'],
  ['friday', 'Sex'],
  ['saturday', 'Sáb'],
  ['sunday', 'Dom'],
];

function cloneEscalationDraft(
  settings: PlatformSupportEscalationDraft,
): PlatformSupportEscalationDraft {
  return {
    enabled: settings.enabled,
    schedulingUrl: settings.schedulingUrl ?? '',
    whatsappNumber: settings.whatsappNumber ?? '',
    businessDays: [...settings.businessDays],
    businessHoursStart: settings.businessHoursStart,
    businessHoursEnd: settings.businessHoursEnd,
  };
}

function SupportEscalationSettingsSection({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<PlatformSupportEscalationDraft | null>(null);
  const [operationReason, setOperationReason] = useState('');
  const queryKey = [...QUERY_ROOT, 'escalation-settings'] as const;
  const settingsQuery = useQuery({
    queryKey,
    queryFn: getPlatformSupportEscalationSettings,
    enabled: canManage,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (settingsQuery.data) {
      setDraft(cloneEscalationDraft(settingsQuery.data));
    }
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (nextDraft: PlatformSupportEscalationDraft) => (
      updatePlatformSupportEscalationSettings(
        {
          ...nextDraft,
          schedulingUrl: nextDraft.schedulingUrl?.trim() ? nextDraft.schedulingUrl.trim() : null,
          whatsappNumber: nextDraft.whatsappNumber?.trim() ? nextDraft.whatsappNumber.trim() : null,
        },
        settingsQuery.data?.etag ?? null,
        operationReason,
      )
    ),
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKey, saved);
      setDraft(cloneEscalationDraft(saved));
      setOperationReason('');
      toast({ title: 'Escalonamento humano atualizado.' });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void settingsQuery.refetch();
        toast({
          title: 'A configuração foi alterada por outra pessoa.',
          description: 'Os dados mais recentes foram recarregados.',
          variant: 'destructive',
        });
        return;
      }
      toast({
        title: 'Não foi possível atualizar o escalonamento humano.',
        description: error instanceof Error ? error.message : 'Tente novamente.',
        variant: 'destructive',
      });
    },
  });

  const toggleDay = (day: SupportAvailabilityWeekday) => {
    setDraft((current) => current
      ? {
        ...current,
        businessDays: current.businessDays.includes(day)
          ? current.businessDays.filter((value) => value !== day)
          : [...current.businessDays, day],
      }
      : current);
  };

  const draftIsValid = Boolean(
    draft
    && draft.businessDays.length > 0
    && /^\d{2}:\d{2}$/.test(draft.businessHoursStart)
    && /^\d{2}:\d{2}$/.test(draft.businessHoursEnd)
    && draft.businessHoursStart < draft.businessHoursEnd
    && (!draft.schedulingUrl?.trim() || /^https?:\/\//i.test(draft.schedulingUrl.trim())),
  );

  if (!canManage) return null;

  return (
    <section className="border-b pb-4" aria-labelledby="support-escalation-title">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <PhoneCall className="h-4 w-4 text-muted-foreground" />
            <h3 id="support-escalation-title" className="text-sm font-semibold">
              Escalonamento humano
            </h3>
            {!settingsQuery.isLoading && !settingsQuery.isError && (
              <Badge
                variant={settingsQuery.data?.enabled ? 'secondary' : 'outline'}
                className="gap-1"
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    settingsQuery.data?.enabled ? 'bg-emerald-600' : 'bg-muted-foreground'
                  }`}
                />
                {settingsQuery.data?.enabled ? 'Ativado' : 'Desativado'}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Bloco &quot;Falar com um humano&quot; na Central de SAC (agendamento + WhatsApp)
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-full gap-2 sm:w-auto"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          aria-controls="support-escalation-settings"
        >
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          {expanded ? 'Recolher' : 'Configurar'}
        </Button>
      </div>

      {expanded && (
        <div id="support-escalation-settings" className="mt-4 border-t pt-4">
          {settingsQuery.isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-28 w-full" />
            </div>
          ) : settingsQuery.isError || !draft ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" role="alert">
              <p className="text-sm text-destructive">
                Não foi possível carregar a configuração de escalonamento humano.
              </p>
              <Button type="button" variant="outline" size="sm" onClick={() => void settingsQuery.refetch()}>
                Tentar novamente
              </Button>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="flex items-center justify-between gap-4 rounded-md border px-3 py-2">
                <div>
                  <Label htmlFor="support-escalation-enabled">Escalonamento habilitado</Label>
                  <p className="text-xs text-muted-foreground">
                    Exibe o bloco &quot;Falar com um humano&quot; na Central de SAC
                  </p>
                </div>
                <Switch
                  id="support-escalation-enabled"
                  checked={draft.enabled}
                  onCheckedChange={(checked) => setDraft((current) => (
                    current ? { ...current, enabled: checked } : current
                  ))}
                  disabled={saveMutation.isPending}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="support-escalation-url">URL de agendamento (Cal.com/Calendly)</Label>
                  <Input
                    id="support-escalation-url"
                    placeholder="https://cal.com/sua-equipe/consulta"
                    value={draft.schedulingUrl ?? ''}
                    onChange={(event) => setDraft((current) => (
                      current ? { ...current, schedulingUrl: event.target.value } : current
                    ))}
                    disabled={saveMutation.isPending}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="support-escalation-whatsapp">WhatsApp</Label>
                  <Input
                    id="support-escalation-whatsapp"
                    placeholder="+55 61 99999-0000"
                    value={draft.whatsappNumber ?? ''}
                    onChange={(event) => setDraft((current) => (
                      current ? { ...current, whatsappNumber: event.target.value } : current
                    ))}
                    disabled={saveMutation.isPending}
                  />
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_120px_120px] sm:items-end">
                <div className="space-y-2">
                  <Label>Dias úteis</Label>
                  <div className="flex flex-wrap gap-2">
                    {ESCALATION_WEEKDAYS.map(([day, label]) => (
                      <Button
                        key={day}
                        type="button"
                        size="sm"
                        variant={draft.businessDays.includes(day) ? 'secondary' : 'outline'}
                        onClick={() => toggleDay(day)}
                        disabled={saveMutation.isPending}
                      >
                        {label}
                      </Button>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="support-escalation-start">Início</Label>
                  <Input
                    id="support-escalation-start"
                    type="time"
                    value={draft.businessHoursStart}
                    onChange={(event) => setDraft((current) => (
                      current ? { ...current, businessHoursStart: event.target.value } : current
                    ))}
                    disabled={saveMutation.isPending}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="support-escalation-end">Fim</Label>
                  <Input
                    id="support-escalation-end"
                    type="time"
                    value={draft.businessHoursEnd}
                    onChange={(event) => setDraft((current) => (
                      current ? { ...current, businessHoursEnd: event.target.value } : current
                    ))}
                    disabled={saveMutation.isPending}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="support-escalation-reason">Motivo da alteração</Label>
                <Textarea
                  id="support-escalation-reason"
                  value={operationReason}
                  onChange={(event) => setOperationReason(event.target.value)}
                  minLength={8}
                  maxLength={500}
                  rows={2}
                  placeholder="Explique por que o escalonamento humano será alterado."
                  disabled={saveMutation.isPending}
                />
              </div>

              <div className="flex justify-end">
                <Button
                  type="button"
                  size="sm"
                  className="gap-2"
                  disabled={!draftIsValid || saveMutation.isPending || operationReason.trim().length < 8}
                  onClick={() => draft && saveMutation.mutate(draft)}
                >
                  {saveMutation.isPending
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : <Save className="h-4 w-4" />}
                  Salvar
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function usePlatformSupportRealtime(enabled: boolean) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<'connecting' | 'connected' | 'reconnecting' | 'offline'>('offline');

  useEffect(() => {
    if (!enabled) {
      setStatus('offline');
      return;
    }
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    let retryAttempt = 0;

    const clearTimers = () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      reconnectTimer = null;
      heartbeatTimer = null;
    };

    const scheduleReconnect = () => {
      if (disposed || reconnectTimer) return;
      setStatus('reconnecting');
      const delay = Math.min(30_000, 1_000 * (2 ** retryAttempt));
      retryAttempt += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        void connect();
      }, delay);
    };

    const connect = async () => {
      if (disposed || socket) return;
      setStatus(retryAttempt > 0 ? 'reconnecting' : 'connecting');
      try {
        const { ticket } = await apiFetch<{ ticket: string; expiresIn: number }>(
          '/api/v1/platform/support/ws-ticket',
          { method: 'POST' },
        );
        if (disposed) return;
        const websocket = new WebSocket(
          `${resolveWebSocketBase(window.location.origin)}/api/v1/platform/ws`,
          ['klm.platform.support.v1', `ticket.${ticket}`],
        );
        socket = websocket;
        websocket.onopen = () => {
          if (disposed || socket !== websocket) {
            websocket.close();
            return;
          }
          retryAttempt = 0;
          setStatus('connected');
          void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'tickets'] });
          void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'ticket'] });
          void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'metrics'] });
          heartbeatTimer = setInterval(() => {
            if (websocket.readyState === WebSocket.OPEN) websocket.send('ping');
          }, 25_000);
        };
        websocket.onmessage = (event) => {
          if (disposed || socket !== websocket) return;
          try {
            const payload = JSON.parse(String(event.data)) as { event?: string };
            if (payload.event === 'CONNECTED' || payload.event === 'PONG') return;
            void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'tickets'] });
            void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'ticket'] });
            void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'metrics'] });
          } catch {
            void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'tickets'] });
            void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'ticket'] });
            void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'metrics'] });
          }
        };
        websocket.onerror = () => websocket.close();
        websocket.onclose = () => {
          if (socket === websocket) socket = null;
          if (heartbeatTimer) clearInterval(heartbeatTimer);
          heartbeatTimer = null;
          scheduleReconnect();
        };
      } catch {
        socket = null;
        scheduleReconnect();
      }
    };

    void connect();
    return () => {
      disposed = true;
      clearTimers();
      socket?.close();
      socket = null;
      setStatus('offline');
    };
  }, [enabled, queryClient]);

  return status;
}

/**
 * Incorporação das solicitações técnicas legadas como tickets de SAC.
 *
 * Chama `POST /api/v1/platform/support/migrate-technical-requests`, protegido
 * por `support.manage`. O serviço só migra o que ainda não foi migrado, então a
 * operação é idempotente — ainda assim pedimos confirmação explícita, porque é
 * escrita em massa sobre a base de tickets.
 */
export function SupportLegacyMigrationSection({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [lastResult, setLastResult] = useState<number | null>(null);
  const [operationReason, setOperationReason] = useState('');

  const migrateMutation = useMutation({
    mutationFn: () => apiFetch<{ migrated: number }>(
      '/api/v1/platform/support/migrate-technical-requests',
      {
        method: 'POST',
        headers: { 'X-Operation-Reason': encodeURIComponent(operationReason) },
      },
    ),
    onSuccess: (result) => {
      const migrated = result?.migrated ?? 0;
      setLastResult(migrated);
      setOperationReason('');
      setConfirmOpen(false);
      if (migrated > 0) {
        void queryClient.invalidateQueries({ queryKey: QUERY_ROOT });
      }
      toast({
        title: migrated > 0
          ? `${migrated} solicitação(ões) técnica(s) incorporada(s).`
          : 'Nada pendente para migrar.',
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível incorporar as solicitações técnicas.',
      description: error instanceof Error ? error.message : 'Tente novamente.',
    }),
  });

  if (!canManage) return null;

  return (
    <section className="border-b pb-4" aria-labelledby="support-legacy-migration-title">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Archive className="h-4 w-4 text-muted-foreground" />
            <h3 id="support-legacy-migration-title" className="text-sm font-semibold">
              Solicitações técnicas legadas
            </h3>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Incorpora as solicitações técnicas antigas como tickets de SAC. A operação é
            idempotente: registros já incorporados não são duplicados.
          </p>
          {lastResult !== null && (
            <p className="mt-2 text-xs font-medium" role="status">
              {lastResult > 0
                ? `${lastResult} registro(s) migrado(s) na última execução.`
                : 'Nada pendente para migrar.'}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          disabled={migrateMutation.isPending}
          onClick={() => setConfirmOpen(true)}
        >
          {migrateMutation.isPending
            ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            : <Archive className="mr-2 h-4 w-4" />}
          Incorporar solicitações técnicas
        </Button>
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Incorporar solicitações técnicas legadas?</AlertDialogTitle>
            <AlertDialogDescription>
              Esta é uma operação de dados em massa sobre a base de tickets. Ela é idempotente e
              não duplica registros já incorporados, mas afeta todos os workspaces de uma vez.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <Label htmlFor="support-migration-reason">Motivo da operação</Label>
            <Textarea
              id="support-migration-reason"
              value={operationReason}
              onChange={(event) => setOperationReason(event.target.value)}
              minLength={8}
              maxLength={500}
              rows={3}
              placeholder="Explique por que a incorporação em massa será executada."
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              disabled={operationReason.trim().length < 8 || migrateMutation.isPending}
              onClick={() => migrateMutation.mutate()}
            >
              Incorporar agora
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

export function PlatformSupportTicketsPanel({ enabled = true }: PlatformSupportTicketsPanelProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { state } = useAppStore();
  const canRespond = canPlatform(state.currentUser, 'support', 'respond');
  const canTriage = canPlatform(state.currentUser, 'support', 'triage');
  const canAssign = canPlatform(state.currentUser, 'support', 'assign');
  const canManageSupport = canPlatform(state.currentUser, 'support', 'manage');
  const isPlatformRootAdmin = isPlatformRoot(state.currentUser);
  const canExport = canPlatform(state.currentUser, 'support', 'export');
  const canViewCustomers = canPlatform(state.currentUser, 'customers', 'view');
  const canRequestSupportAccess = canPlatform(state.currentUser, 'support_access', 'request');
  const [periodDays, setPeriodDays] = useState('30');
  const [draftFilters, setDraftFilters] = useState<TicketFilters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<TicketFilters>(EMPTY_FILTERS);
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [selectedSavedFilterId, setSelectedSavedFilterId] = useState('');
  const [savedFilterName, setSavedFilterName] = useState('');
  const [savedFilterDefault, setSavedFilterDefault] = useState(false);
  const [messageBody, setMessageBody] = useState('');
  const [messageVisibility, setMessageVisibility] = useState<MessageVisibility>('public');
  const [linkedRecords, setLinkedRecords] = useState({
    linkedErrorLogId: '',
    linkedIncidentId: '',
    linkedReleaseId: '',
    supportAccessRequestId: '',
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [createReason, setCreateReason] = useState('');
  const [createDraft, setCreateDraft] = useState({
    workspaceId: '',
    reporterUserId: '',
    ticketType: 'problem' as TicketType,
    priority: 'normal' as TicketPriority,
    severity: 'medium' as TicketSeverity,
    queue: 'support' as TicketQueue,
    subject: '',
    description: '',
  });
  const defaultFilterApplied = useRef(false);
  const realtimeStatus = usePlatformSupportRealtime(enabled);

  const metricsQuery = useQuery({
    queryKey: [...QUERY_ROOT, 'metrics', periodDays],
    queryFn: () => apiFetch<SupportMetrics>(`/api/v1/platform/support/metrics?periodDays=${periodDays}`),
    enabled,
    staleTime: 30_000,
  });

  const ticketsQuery = useInfiniteQuery({
    queryKey: [...QUERY_ROOT, 'tickets', appliedFilters],
    queryFn: ({ pageParam }) => apiFetch<SupportTicketPage>(
      `/api/v1/platform/support/tickets?${buildTicketQuery(
        appliedFilters,
        typeof pageParam === 'string' ? pageParam : null,
      )}`,
    ),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    enabled,
    staleTime: 15_000,
  });

  const detailQuery = useQuery({
    queryKey: [...QUERY_ROOT, 'ticket', selectedTicketId],
    queryFn: () => apiFetch<SupportTicketDetail>(
      `/api/v1/platform/support/tickets/${encodeURIComponent(selectedTicketId!)}`,
    ),
    enabled: enabled && Boolean(selectedTicketId),
    staleTime: 10_000,
  });

  const savedFiltersQuery = useQuery({
    queryKey: [...QUERY_ROOT, 'saved-filters'],
    queryFn: () => apiFetch<SavedFilter[]>('/api/v1/platform/support/saved-filters'),
    enabled,
    staleTime: 30_000,
  });

  const tickets = useMemo(
    () => ticketsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [ticketsQuery.data],
  );
  const detail = detailQuery.data ?? null;
  useEffect(() => {
    if (selectedTicketId && tickets.some((ticket) => ticket.id === selectedTicketId)) return;
    setSelectedTicketId(tickets[0]?.id ?? null);
  }, [selectedTicketId, tickets]);

  useEffect(() => {
    if (!detail) return;
    setLinkedRecords({
      linkedErrorLogId: detail.linkedErrorLogId ?? '',
      linkedIncidentId: detail.linkedIncidentId ?? '',
      linkedReleaseId: detail.linkedReleaseId ?? '',
      supportAccessRequestId: detail.supportAccessRequestId ?? '',
    });
  }, [detail]);

  useEffect(() => {
    if (defaultFilterApplied.current || !savedFiltersQuery.data) return;
    defaultFilterApplied.current = true;
    const defaultFilter = savedFiltersQuery.data.find((filter) => filter.isDefault);
    if (!defaultFilter) return;
    const normalized = normalizeSavedFilters(defaultFilter.filters);
    setSelectedSavedFilterId(defaultFilter.id);
    setDraftFilters(normalized);
    setAppliedFilters(normalized);
  }, [savedFiltersQuery.data]);

  const createTicketMutation = useMutation({
    mutationFn: () => apiFetch<SupportTicket>('/api/v1/platform/support/tickets', {
      method: 'POST',
      headers: {
        'Idempotency-Key': crypto.randomUUID(),
        'X-Operation-Reason': encodeURIComponent(createReason),
      },
      body: JSON.stringify(createDraft),
    }),
    onSuccess: (ticket) => {
      setCreateOpen(false);
      setCreateReason('');
      setCreateDraft({
        workspaceId: '', reporterUserId: '', ticketType: 'problem', priority: 'normal',
        severity: 'medium', queue: 'support', subject: '', description: '',
      });
      setSelectedTicketId(ticket.id);
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'tickets'] });
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'metrics'] });
      toast({ title: `Ticket ${ticket.publicNumber} aberto.` });
    },
    onError: (error: Error) => toast({
      variant: 'destructive',
      title: 'Não foi possível abrir o ticket',
      description: error.message,
    }),
  });

  const patchMutation = useMutation({
    mutationFn: ({
      ticketId,
      expectedVersion,
      changes,
    }: {
      ticketId: string;
      expectedVersion: number;
      changes: TicketPatch;
    }) => apiFetch<SupportTicket>(
      `/api/v1/platform/support/tickets/${encodeURIComponent(ticketId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ expectedVersion, ...changes }),
      },
    ),
    onMutate: async ({ ticketId, changes }) => {
      await queryClient.cancelQueries({ queryKey: QUERY_ROOT });
      const listSnapshots = queryClient.getQueriesData<InfiniteData<SupportTicketPage>>({
        queryKey: [...QUERY_ROOT, 'tickets'],
      });
      const detailKey = [...QUERY_ROOT, 'ticket', ticketId];
      const detailSnapshot = queryClient.getQueryData<SupportTicketDetail>(detailKey);
      const optimisticPatch = {
        ...changes,
        version: (detailSnapshot?.version ?? 0) + 1,
        updatedAt: new Date().toISOString(),
      } as Partial<SupportTicket>;
      queryClient.setQueriesData<InfiniteData<SupportTicketPage>>(
        { queryKey: [...QUERY_ROOT, 'tickets'] },
        (current) => replaceTicket(current, ticketId, optimisticPatch),
      );
      queryClient.setQueryData<SupportTicketDetail>(detailKey, (current) => (
        current ? { ...current, ...optimisticPatch } : current
      ));
      return { listSnapshots, detailKey, detailSnapshot };
    },
    onError: (error, _variables, context) => {
      context?.listSnapshots.forEach(([key, value]) => queryClient.setQueryData(key, value));
      if (context?.detailSnapshot) {
        queryClient.setQueryData(context.detailKey, context.detailSnapshot);
      }
      toast({
        variant: 'destructive',
        title: error instanceof ApiError && error.status === 409
          ? 'Ticket atualizado em outra sessão'
          : 'Não foi possível atualizar o ticket',
        description: error instanceof Error ? error.message : 'Recarregue e tente novamente.',
      });
    },
    onSuccess: (updated) => {
      queryClient.setQueriesData<InfiniteData<SupportTicketPage>>(
        { queryKey: [...QUERY_ROOT, 'tickets'] },
        (current) => replaceTicket(current, updated.id, updated),
      );
      queryClient.setQueryData<SupportTicketDetail>(
        [...QUERY_ROOT, 'ticket', updated.id],
        (current) => current ? { ...current, ...updated } : current,
      );
      toast({ title: 'Ticket atualizado.' });
    },
    onSettled: (_data, _error, variables) => {
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'tickets'] });
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'ticket', variables.ticketId] });
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'metrics'] });
    },
  });

  const messageMutation = useMutation({
    mutationFn: ({
      ticketId,
      body,
      visibility,
    }: {
      ticketId: string;
      body: string;
      visibility: MessageVisibility;
    }) => apiFetch<SupportMessage>(
      `/api/v1/platform/support/tickets/${encodeURIComponent(ticketId)}/messages`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ body, visibility }),
      },
    ),
    onSuccess: (_message, variables) => {
      setMessageBody('');
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'ticket', variables.ticketId] });
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'tickets'] });
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'metrics'] });
      toast({
        title: variables.visibility === 'internal'
          ? 'Nota interna registrada.'
          : 'Resposta pública enviada.',
      });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível enviar a mensagem',
        description: error.message,
      });
    },
  });

  const saveFilterMutation = useMutation({
    mutationFn: () => apiFetch<SavedFilter>('/api/v1/platform/support/saved-filters', {
      method: 'POST',
      body: JSON.stringify({
        name: savedFilterName.trim(),
        filters: appliedFilters,
        isDefault: savedFilterDefault,
      }),
    }),
    onSuccess: (saved) => {
      setSavedFilterName('');
      setSavedFilterDefault(false);
      setSelectedSavedFilterId(saved.id);
      void queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'saved-filters'] });
      toast({ title: 'Filtro salvo.' });
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível salvar o filtro',
        description: error.message,
      });
    },
  });

  const refreshAll = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: QUERY_ROOT });
  }, [queryClient]);

  const applyFilters = () => {
    setAppliedFilters(draftFilters);
    setSelectedSavedFilterId('');
  };

  const clearFilters = () => {
    setDraftFilters(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
    setSelectedSavedFilterId('');
  };

  const applySavedFilter = (filterId: string) => {
    setSelectedSavedFilterId(filterId);
    const saved = savedFiltersQuery.data?.find((item) => item.id === filterId);
    if (!saved) return;
    const normalized = normalizeSavedFilters(saved.filters);
    setDraftFilters(normalized);
    setAppliedFilters(normalized);
  };

  const updateTicket = (changes: TicketPatch) => {
    if (!detail || patchMutation.isPending) return;
    patchMutation.mutate({
      ticketId: detail.id,
      expectedVersion: detail.version,
      changes,
    });
  };

  const saveLinkedRecords = () => {
    if (!detail) return;
    const changes: TicketPatch = {};
    (Object.keys(linkedRecords) as Array<keyof typeof linkedRecords>).forEach((key) => {
      const value = linkedRecords[key].trim();
      if (value !== (detail[key] ?? '')) changes[key] = value || null;
    });
    if (Object.keys(changes).length === 0) {
      toast({ title: 'Nenhum vínculo novo para salvar.' });
      return;
    }
    updateTicket(changes);
  };

  const metrics = metricsQuery.data;
  const activeFilterCount = filterCount(appliedFilters);
  const stages = useMemo(() => detail ? stageDurations(detail) : [], [detail]);
  const exportMutation = useMutation({
    mutationFn: () => createPlatformExport({
      dataset: 'tickets',
      format: 'csv',
      filters: {
        ...(appliedFilters.search.trim() ? { search: appliedFilters.search.trim() } : {}),
        ...(appliedFilters.workspaceId ? { workspaceId: appliedFilters.workspaceId } : {}),
        ...(appliedFilters.statuses.length ? { statuses: appliedFilters.statuses } : {}),
        ...(appliedFilters.queues.length ? { queues: appliedFilters.queues } : {}),
        ...(appliedFilters.severities.length ? { severities: appliedFilters.severities } : {}),
        ...(appliedFilters.ticketTypes.length ? { ticketTypes: appliedFilters.ticketTypes } : {}),
        ...(appliedFilters.assignedToUserId ? { assignedToUserId: appliedFilters.assignedToUserId } : {}),
        ...(appliedFilters.overdue !== 'all' ? { overdue: appliedFilters.overdue === 'yes' } : {}),
      },
      idempotencyKey: `support-tickets:${crypto.randomUUID()}`,
    }),
    onSuccess: () => {
      toast({ title: 'Exportação enfileirada na Central de Downloads.' });
      navigate('/blackbox/exports');
    },
    onError: (error) => toast({
      title: 'Não foi possível enfileirar a exportação.',
      description: error instanceof Error ? error.message : 'Tente novamente.',
      variant: 'destructive',
    }),
  });

  return (
    <TooltipProvider>
      <section className="space-y-4" aria-label="Administração da Central de SAC">
        <div className="flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold">Central de SAC</h2>
              <Badge
                variant={realtimeStatus === 'connected' ? 'secondary' : 'outline'}
                className="gap-1"
                aria-live="polite"
              >
                {realtimeStatus === 'connected' ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                {realtimeStatus === 'connected'
                  ? 'Tempo real ativo'
                  : realtimeStatus === 'offline'
                    ? 'Tempo real offline'
                    : 'Reconectando'}
              </Badge>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Fila unificada de atendimento e diagnóstico da plataforma.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {canRespond && canViewCustomers && (
              <Button className="gap-2" onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4" /> Abrir ticket
              </Button>
            )}
            <Label htmlFor="support-period" className="sr-only">Período dos indicadores</Label>
            <Select value={periodDays} onValueChange={setPeriodDays}>
              <SelectTrigger id="support-period" className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7">Últimos 7 dias</SelectItem>
                <SelectItem value="30">Últimos 30 dias</SelectItem>
                <SelectItem value="90">Últimos 90 dias</SelectItem>
              </SelectContent>
            </Select>
            {canExport && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => exportMutation.mutate()}
                    disabled={exportMutation.isPending}
                    aria-label="Exportar tickets filtrados"
                  >
                    {exportMutation.isPending
                      ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Download className="h-4 w-4" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Enfileirar CSV com os filtros atuais</TooltipContent>
              </Tooltip>
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="outline" size="icon" onClick={refreshAll} aria-label="Atualizar Central de SAC">
                  <RefreshCw className={`h-4 w-4 ${ticketsQuery.isFetching ? 'animate-spin' : ''}`} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Atualizar dados</TooltipContent>
            </Tooltip>
          </div>
        </div>

        <Dialog open={createOpen} onOpenChange={setCreateOpen}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>Abrir ticket pela equipe do SaaS</DialogTitle>
              <DialogDescription>
                O chamado será registrado para a conta e atribuído ao usuário selecionado, mantendo sua autoria interna na auditoria.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Conta</Label>
                <PlatformEntityCombobox entityType="accounts" purpose="support_ticket_create" value={createDraft.workspaceId || null} onChange={(workspaceId) => setCreateDraft((current) => ({ ...current, workspaceId: workspaceId ?? '', reporterUserId: '' }))} label="Conta do novo ticket" placeholder="Pesquisar conta" allowClear />
              </div>
              <div className="space-y-2">
                <Label>Solicitante</Label>
                <PlatformEntityCombobox entityType="users" purpose="support_ticket_reporter" workspaceId={createDraft.workspaceId || null} value={createDraft.reporterUserId || null} onChange={(reporterUserId) => setCreateDraft((current) => ({ ...current, reporterUserId: reporterUserId ?? '' }))} label="Solicitante do novo ticket" placeholder="Pesquisar usuário da conta" disabled={!createDraft.workspaceId} allowClear />
              </div>
              <div className="space-y-2"><Label>Tipo</Label><Select value={createDraft.ticketType} onValueChange={(ticketType: TicketType) => setCreateDraft((current) => ({ ...current, ticketType }))}><SelectTrigger aria-label="Tipo do novo ticket"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(TYPE_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-2"><Label>Fila</Label><Select value={createDraft.queue} onValueChange={(queue: TicketQueue) => setCreateDraft((current) => ({ ...current, queue }))}><SelectTrigger aria-label="Fila do novo ticket"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(QUEUE_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-2"><Label>Prioridade</Label><Select value={createDraft.priority} onValueChange={(priority: TicketPriority) => setCreateDraft((current) => ({ ...current, priority }))}><SelectTrigger aria-label="Prioridade do novo ticket"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(PRIORITY_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-2"><Label>Severidade</Label><Select value={createDraft.severity} onValueChange={(severity: TicketSeverity) => setCreateDraft((current) => ({ ...current, severity }))}><SelectTrigger aria-label="Severidade do novo ticket"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(SEVERITY_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-2 sm:col-span-2"><Label htmlFor="support-create-subject">Assunto</Label><Input id="support-create-subject" value={createDraft.subject} maxLength={240} onChange={(event) => setCreateDraft((current) => ({ ...current, subject: event.target.value }))} /></div>
              <div className="space-y-2 sm:col-span-2"><Label htmlFor="support-create-description">Descrição</Label><Textarea id="support-create-description" rows={5} value={createDraft.description} onChange={(event) => setCreateDraft((current) => ({ ...current, description: event.target.value }))} /></div>
              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="support-create-reason">Motivo da abertura administrativa</Label>
                <Textarea
                  id="support-create-reason"
                  rows={2}
                  minLength={8}
                  maxLength={500}
                  value={createReason}
                  onChange={(event) => setCreateReason(event.target.value)}
                  placeholder="Informe o contexto operacional ou o ticket que justifica a abertura."
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancelar</Button>
              <Button disabled={!createDraft.workspaceId || !createDraft.reporterUserId || createDraft.subject.trim().length < 3 || createDraft.description.trim().length < 10 || createReason.trim().length < 8 || createTicketMutation.isPending} onClick={() => createTicketMutation.mutate()}>
                {createTicketMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />} Criar ticket
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <SupportAvailabilitySettingsSection enabled={enabled} canManage={canManageSupport} />

        <SupportEscalationSettingsSection canManage={isPlatformRootAdmin} />

        <SupportLegacyMigrationSection canManage={canManageSupport} />

        {metricsQuery.isError && (
          <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3" role="alert">
            <p className="text-sm text-destructive">Não foi possível carregar os indicadores do SAC.</p>
            <Button variant="outline" size="sm" onClick={() => void metricsQuery.refetch()}>Tentar novamente</Button>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <MetricCard
            title="Novos tickets"
            value={`${metrics?.new ?? 0}`}
            comparison={metrics?.previousPeriodChangePct.new}
            icon={Inbox}
            loading={metricsQuery.isLoading}
          />
          <MetricCard
            title="Resolvidos"
            value={`${metrics?.resolved ?? 0}`}
            comparison={metrics?.previousPeriodChangePct.resolved}
            icon={CheckCircle2}
            tone="good"
            loading={metricsQuery.isLoading}
          />
          <MetricCard
            title="Reabertos"
            value={`${metrics?.reopened ?? 0}`}
            comparison={metrics?.previousPeriodChangePct.reopened}
            icon={RefreshCw}
            tone="warning"
            loading={metricsQuery.isLoading}
          />
          <MetricCard
            title="Backlog aberto"
            value={`${metrics?.open ?? 0}`}
            icon={Activity}
            loading={metricsQuery.isLoading}
          />
          <MetricCard
            title="SLA vencido"
            value={`${metrics?.overdue ?? 0}`}
            icon={AlertCircle}
            tone={(metrics?.overdue ?? 0) > 0 ? 'danger' : 'good'}
            loading={metricsQuery.isLoading}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <DistributionPanel
            title="Backlog por fila"
            items={metrics?.backlogByQueue}
            labels={QUEUE_LABELS}
          />
          <DistributionPanel
            title="Backlog por severidade"
            items={metrics?.backlogBySeverity}
            labels={SEVERITY_LABELS}
          />
          <DistributionPanel
            title="Resolvidos por responsável"
            items={metrics?.performanceByAssignee}
          />
          <DistributionPanel
            title="Instabilidades por página"
            items={metrics?.instabilityByPage}
          />
          <DistributionPanel
            title="Sugestões por destino"
            items={metrics?.suggestionsByDestination}
            labels={QUEUE_LABELS}
          />
          <DistributionPanel
            title="Contas com recorrência"
            items={metrics?.recurrenceByWorkspace}
          />
        </div>

        <div className="flex flex-col gap-2 border-y py-3 lg:flex-row lg:items-center">
          <form
            className="flex min-w-0 flex-1 gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              applyFilters();
            }}
          >
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Label htmlFor="support-search" className="sr-only">Buscar tickets</Label>
              <Input
                id="support-search"
                value={draftFilters.search}
                onChange={(event) => setDraftFilters((current) => ({ ...current, search: event.target.value }))}
                placeholder="Número, assunto, descrição ou fingerprint"
                className="pl-9"
              />
            </div>
            <Button type="submit" variant="secondary">Buscar</Button>
          </form>

          <div className="flex flex-wrap items-center gap-2">
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" className="gap-2">
                  <SlidersHorizontal className="h-4 w-4" />
                  Filtros avançados
                  {activeFilterCount > 0 && <Badge variant="secondary">{activeFilterCount}</Badge>}
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-[min(92vw,720px)] p-0">
                <div className="flex items-center justify-between border-b p-4">
                  <div>
                    <p className="text-sm font-semibold">Filtros personalizados</p>
                    <p className="text-xs text-muted-foreground">Combine critérios para esta fila.</p>
                  </div>
                  <Filter className="h-4 w-4 text-muted-foreground" />
                </div>
                <ScrollArea className="max-h-[65vh]">
                  <div className="grid gap-5 p-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="filter-workspace">Conta</Label>
                      {canViewCustomers ? (
                        <PlatformEntityCombobox entityType="accounts" purpose="support_ticket_filter" value={draftFilters.workspaceId || null} onChange={(workspaceId) => setDraftFilters((current) => ({ ...current, workspaceId: workspaceId ?? '' }))} label="Filtrar por conta" placeholder="Todas as contas" allowClear />
                      ) : (
                        <div id="filter-workspace" className="rounded-md border border-dashed p-3 text-xs text-muted-foreground" role="status">
                          Filtro por conta indisponível para este perfil. Solicite a permissão de consulta de clientes; identificadores internos não são aceitos manualmente.
                        </div>
                      )}
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="filter-assignee">Responsável</Label>
                      <PlatformEntityCombobox
                        entityType="staff"
                        purpose="support_ticket_filter"
                        value={draftFilters.assignedToUserId || null}
                        onChange={(assignedToUserId) => setDraftFilters((current) => ({
                          ...current,
                          assignedToUserId: assignedToUserId ?? '',
                        }))}
                        label="Filtrar por responsável"
                        placeholder="Todos os responsáveis"
                        allowClear
                      />
                    </div>
                    <FilterCheckboxGroup
                      label="Status"
                      values={draftFilters.statuses}
                      options={STATUS_OPTIONS}
                      onChange={(statuses) => setDraftFilters((current) => ({ ...current, statuses }))}
                    />
                    <FilterCheckboxGroup
                      label="Severidade"
                      values={draftFilters.severities}
                      options={Object.entries(SEVERITY_LABELS) as Array<[TicketSeverity, string]>}
                      onChange={(severities) => setDraftFilters((current) => ({ ...current, severities }))}
                    />
                    <FilterCheckboxGroup
                      label="Fila"
                      values={draftFilters.queues}
                      options={Object.entries(QUEUE_LABELS) as Array<[TicketQueue, string]>}
                      onChange={(queues) => setDraftFilters((current) => ({ ...current, queues }))}
                    />
                    <FilterCheckboxGroup
                      label="Tipo"
                      values={draftFilters.ticketTypes}
                      options={Object.entries(TYPE_LABELS) as Array<[TicketType, string]>}
                      onChange={(ticketTypes) => setDraftFilters((current) => ({ ...current, ticketTypes }))}
                    />
                    <div className="space-y-2 md:col-span-2">
                      <Label htmlFor="filter-overdue">Prazo</Label>
                      <Select
                        value={draftFilters.overdue}
                        onValueChange={(overdue: TicketFilters['overdue']) => setDraftFilters((current) => ({
                          ...current,
                          overdue,
                        }))}
                      >
                        <SelectTrigger id="filter-overdue"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">Todos os prazos</SelectItem>
                          <SelectItem value="yes">Somente SLA vencido</SelectItem>
                          <SelectItem value="no">Somente dentro do SLA</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </ScrollArea>
                <div className="flex items-center justify-between gap-2 border-t p-4">
                  <Button variant="ghost" onClick={clearFilters}>Limpar</Button>
                  <Button onClick={applyFilters}>Aplicar filtros</Button>
                </div>
              </PopoverContent>
            </Popover>

            <Select value={selectedSavedFilterId || 'none'} onValueChange={applySavedFilter}>
              <SelectTrigger className="w-[190px]" aria-label="Filtros salvos">
                <SelectValue placeholder="Filtros salvos" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none" disabled>Filtros salvos</SelectItem>
                {(savedFiltersQuery.data ?? []).map((filter) => (
                  <SelectItem key={filter.id} value={filter.id}>
                    {filter.name}{filter.isDefault ? ' · padrão' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" size="icon" aria-label="Salvar filtro atual">
                  <Save className="h-4 w-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-[min(92vw,360px)] space-y-4">
                <div>
                  <p className="text-sm font-semibold">Salvar filtro atual</p>
                  <p className="text-xs text-muted-foreground">{activeFilterCount} critérios avançados ativos.</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="saved-filter-name">Nome</Label>
                  <Input
                    id="saved-filter-name"
                    value={savedFilterName}
                    onChange={(event) => setSavedFilterName(event.target.value)}
                    placeholder="Ex.: Críticos sem resposta"
                  />
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={savedFilterDefault}
                    onCheckedChange={(checked) => setSavedFilterDefault(Boolean(checked))}
                  />
                  Usar como filtro padrão
                </label>
                <Button
                  className="w-full"
                  disabled={savedFilterName.trim().length < 2 || saveFilterMutation.isPending}
                  onClick={() => saveFilterMutation.mutate()}
                >
                  {saveFilterMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Salvar
                </Button>
              </PopoverContent>
            </Popover>

            {activeFilterCount > 0 && (
              <Button variant="ghost" size="icon" onClick={clearFilters} aria-label="Limpar filtros">
                <X className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>

        <div className="grid min-h-[680px] gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
          <section className="min-w-0 rounded-md border" aria-label="Fila de tickets">
            <div className="flex h-12 items-center justify-between border-b px-3">
              <div>
                <h3 className="text-sm font-semibold">Fila</h3>
                <p className="text-xs text-muted-foreground">{tickets.length} tickets carregados</p>
              </div>
              {ticketsQuery.isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Atualizando fila" />}
            </div>

            {ticketsQuery.isError ? (
              <div className="p-6 text-center" role="alert">
                <AlertCircle className="mx-auto h-6 w-6 text-destructive" />
                <p className="mt-2 text-sm font-medium">Falha ao carregar a fila</p>
                <Button className="mt-3" variant="outline" size="sm" onClick={() => void ticketsQuery.refetch()}>
                  Tentar novamente
                </Button>
              </div>
            ) : ticketsQuery.isLoading ? (
              <div className="space-y-3 p-3">
                {[0, 1, 2, 3, 4].map((item) => <Skeleton key={item} className="h-28 w-full" />)}
              </div>
            ) : tickets.length === 0 ? (
              <div className="px-6 py-16 text-center">
                <Inbox className="mx-auto h-8 w-8 text-muted-foreground" />
                <p className="mt-3 text-sm font-medium">Nenhum ticket nesta fila</p>
                <p className="mt-1 text-xs text-muted-foreground">Revise os filtros aplicados.</p>
              </div>
            ) : (
              <>
                <div className="max-h-[720px] overflow-y-auto">
                  {tickets.map((ticket) => {
                    const sla = slaState(ticket);
                    const assigneeName = ticket.assignee?.name
                      ?? (ticket.assignedToUserId ? 'Responsável atribuído' : 'Sem responsável');
                    return (
                      <button
                        key={ticket.id}
                        type="button"
                        onClick={() => setSelectedTicketId(ticket.id)}
                        aria-pressed={selectedTicketId === ticket.id}
                        className={`w-full border-b p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary ${
                          selectedTicketId === ticket.id ? 'bg-primary/5' : 'hover:bg-muted/40'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-xs font-semibold text-primary">{ticket.publicNumber}</p>
                            <p className="mt-1 line-clamp-2 text-sm font-medium">{ticket.subject}</p>
                          </div>
                          <Badge variant={severityVariant(ticket.severity)} className="shrink-0">
                            {SEVERITY_LABELS[ticket.severity]}
                          </Badge>
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          <Badge variant={statusVariant(ticket.status)}>{STATUS_LABELS[ticket.status]}</Badge>
                          <Badge variant="outline">{QUEUE_LABELS[ticket.queue]}</Badge>
                          <span className={`text-xs ${sla.overdue ? 'font-medium text-destructive' : 'text-muted-foreground'}`}>
                            {sla.detail}
                          </span>
                        </div>
                        <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                          <span className="min-w-0 truncate">
                            {ticket.workspaceName ?? 'Conta removida ou indisponível'}
                          </span>
                          <span className="shrink-0">{assigneeName}</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
                {ticketsQuery.hasNextPage && (
                  <div className="border-t p-3">
                    <Button
                      variant="outline"
                      className="w-full"
                      disabled={ticketsQuery.isFetchingNextPage}
                      onClick={() => void ticketsQuery.fetchNextPage()}
                    >
                      {ticketsQuery.isFetchingNextPage && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      Carregar mais
                    </Button>
                  </div>
                )}
              </>
            )}
          </section>

          <section className="min-w-0 rounded-md border" aria-label="Detalhe do ticket">
            {!selectedTicketId ? (
              <div className="flex min-h-[520px] items-center justify-center p-6 text-center">
                <div>
                  <MessageSquare className="mx-auto h-9 w-9 text-muted-foreground" />
                  <p className="mt-3 text-sm font-medium">Selecione um ticket da fila</p>
                </div>
              </div>
            ) : detailQuery.isLoading ? (
              <div className="space-y-4 p-5">
                <Skeleton className="h-7 w-52" />
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-96 w-full" />
              </div>
            ) : detailQuery.isError || !detail ? (
              <div className="flex min-h-[520px] items-center justify-center p-6 text-center" role="alert">
                <div>
                  <AlertCircle className="mx-auto h-8 w-8 text-destructive" />
                  <p className="mt-3 text-sm font-medium">Não foi possível abrir o ticket</p>
                  <Button className="mt-3" variant="outline" size="sm" onClick={() => void detailQuery.refetch()}>
                    Tentar novamente
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <header className="space-y-4 border-b p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-semibold text-primary">{detail.publicNumber}</span>
                        <Badge variant={statusVariant(detail.status)}>{STATUS_LABELS[detail.status]}</Badge>
                        <Badge variant={severityVariant(detail.severity)}>{SEVERITY_LABELS[detail.severity]}</Badge>
                        <Badge variant="outline">{TYPE_LABELS[detail.ticketType]}</Badge>
                      </div>
                      <h3 className="mt-2 text-base font-semibold">{detail.subject}</h3>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Aberto em {formatDate(detail.createdAt)} · versão de edição {detail.version}
                      </p>
                    </div>
                    <div className={`rounded-md border px-3 py-2 text-right ${slaState(detail).overdue ? 'border-destructive/40 bg-destructive/5' : 'bg-muted/30'}`}>
                      <p className="text-xs font-medium">{slaState(detail).label}</p>
                      <p className={`mt-0.5 text-sm font-semibold tabular-nums ${slaState(detail).overdue ? 'text-destructive' : ''}`}>
                        {slaState(detail).detail}
                      </p>
                    </div>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                    <div className="space-y-1.5">
                      <Label htmlFor="ticket-status">Status</Label>
                      <Select
                        value={detail.status}
                        disabled={!canTriage || patchMutation.isPending}
                        onValueChange={(status: TicketStatus) => updateTicket({ status })}
                      >
                        <SelectTrigger id="ticket-status"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value={detail.status}>{STATUS_LABELS[detail.status]}</SelectItem>
                          {STATUS_TRANSITIONS[detail.status].map((status) => (
                            <SelectItem key={status} value={status}>{STATUS_LABELS[status]}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="ticket-assignee">Responsável</Label>
                      <PlatformEntityCombobox entityType="staff" purpose="support_assignee" value={detail.assignedToUserId} onChange={(assignedToUserId) => { if (assignedToUserId) updateTicket({ assignedToUserId }); }} label="Atribuir responsável" placeholder="Pesquisar funcionário" allowClear={false} disabled={!canAssign || patchMutation.isPending} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="ticket-queue">Fila</Label>
                      <Select
                        value={detail.queue}
                        disabled={!canTriage || patchMutation.isPending}
                        onValueChange={(queue: TicketQueue) => updateTicket({ queue })}
                      >
                        <SelectTrigger id="ticket-queue"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(Object.entries(QUEUE_LABELS) as Array<[TicketQueue, string]>).map(([value, label]) => (
                            <SelectItem key={value} value={value}>{label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="ticket-severity">Severidade</Label>
                      <Select
                        value={detail.severity}
                        disabled={!canTriage || patchMutation.isPending}
                        onValueChange={(severity: TicketSeverity) => updateTicket({ severity })}
                      >
                        <SelectTrigger id="ticket-severity"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(Object.entries(SEVERITY_LABELS) as Array<[TicketSeverity, string]>).map(([value, label]) => (
                            <SelectItem key={value} value={value}>{label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="ticket-priority">Prioridade</Label>
                      <Select
                        value={detail.priority}
                        disabled={!canTriage || patchMutation.isPending}
                        onValueChange={(priority: TicketPriority) => updateTicket({ priority })}
                      >
                        <SelectTrigger id="ticket-priority"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(Object.entries(PRIORITY_LABELS) as Array<[TicketPriority, string]>).map(([value, label]) => (
                            <SelectItem key={value} value={value}>{label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </header>

                <Tabs defaultValue="conversation" className="min-w-0">
                  <TabsList className="mx-4 mt-4 grid h-auto w-[calc(100%-2rem)] grid-cols-3">
                    <TabsTrigger value="conversation" className="gap-1 px-1 text-xs sm:gap-2 sm:px-3 sm:text-sm">
                      <MessageSquare className="hidden h-4 w-4 sm:block" />
                      Conversa
                    </TabsTrigger>
                    <TabsTrigger value="diagnostic" className="gap-1 px-1 text-xs sm:gap-2 sm:px-3 sm:text-sm">
                      <Activity className="hidden h-4 w-4 sm:block" />
                      Diagnóstico
                    </TabsTrigger>
                    <TabsTrigger value="timeline" className="gap-1 px-1 text-xs sm:gap-2 sm:px-3 sm:text-sm">
                      <Clock3 className="hidden h-4 w-4 sm:block" />
                      Timeline
                    </TabsTrigger>
                  </TabsList>

                  <TabsContent value="conversation" className="m-0">
                    <div className="border-b p-4">
                      <p className="whitespace-pre-wrap text-sm leading-6">{detail.description}</p>
                    </div>
                    <div className="max-h-[520px] space-y-3 overflow-y-auto p-4" aria-live="polite">
                      {detail.messages.length === 0 ? (
                        <p className="py-10 text-center text-sm text-muted-foreground">Ainda não há mensagens neste ticket.</p>
                      ) : (
                        [...detail.messages]
                          .sort((left, right) => new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime())
                          .map((message) => (
                            <article
                              key={message.id}
                              className={`rounded-md border p-3 ${
                                message.visibility === 'internal'
                                  ? 'border-amber-300/60 bg-amber-50/70 dark:bg-amber-950/20'
                                  : 'bg-background'
                              }`}
                            >
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-semibold">{message.authorLabel}</span>
                                  <Badge variant={message.visibility === 'internal' ? 'outline' : 'secondary'}>
                                    {message.visibility === 'internal' ? 'Nota interna' : 'Pública'}
                                  </Badge>
                                </div>
                                <time className="text-xs text-muted-foreground" dateTime={message.createdAt}>
                                  {formatDate(message.createdAt)}
                                </time>
                              </div>
                              <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{message.body}</p>
                            </article>
                          ))
                      )}
                    </div>
                    {canRespond && (
                      <form
                        className="space-y-3 border-t bg-muted/20 p-4"
                        onSubmit={(event) => {
                          event.preventDefault();
                          if (!messageBody.trim()) return;
                          messageMutation.mutate({
                            ticketId: detail.id,
                            body: messageBody.trim(),
                            visibility: messageVisibility,
                          });
                        }}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="inline-flex rounded-md border bg-background p-1" role="group" aria-label="Visibilidade da mensagem">
                            <Button
                              type="button"
                              size="sm"
                              variant={messageVisibility === 'public' ? 'secondary' : 'ghost'}
                              onClick={() => setMessageVisibility('public')}
                            >
                              Resposta pública
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant={messageVisibility === 'internal' ? 'secondary' : 'ghost'}
                              onClick={() => setMessageVisibility('internal')}
                            >
                              Nota interna
                            </Button>
                          </div>
                          <span className="text-xs text-muted-foreground">
                            {messageVisibility === 'internal' ? 'Visível somente à equipe SaaS' : 'Visível à conta solicitante'}
                          </span>
                        </div>
                        <Label htmlFor="ticket-message" className="sr-only">
                          {messageVisibility === 'internal' ? 'Nota interna' : 'Resposta pública'}
                        </Label>
                        <Textarea
                          id="ticket-message"
                          value={messageBody}
                          onChange={(event) => setMessageBody(event.target.value)}
                          placeholder={messageVisibility === 'internal' ? 'Registre o diagnóstico interno...' : 'Escreva a resposta ao cliente...'}
                          rows={4}
                        />
                        <div className="flex justify-end">
                          <Button type="submit" disabled={!messageBody.trim() || messageMutation.isPending}>
                            {messageMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
                            {messageVisibility === 'internal' ? 'Registrar nota' : 'Enviar resposta'}
                          </Button>
                        </div>
                      </form>
                    )}
                  </TabsContent>

                  <TabsContent value="diagnostic" className="m-0 space-y-0">
                    <div className="grid gap-0 lg:grid-cols-2">
                      <div className="space-y-4 border-b p-4 lg:border-b-0 lg:border-r">
                        <div>
                          <h4 className="text-sm font-semibold">Conta e usuário</h4>
                          <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                            <div>
                              <dt className="text-xs text-muted-foreground">Conta</dt>
                              <dd className="mt-1 flex items-center gap-1.5 text-sm font-medium">
                                <Building2 className="h-4 w-4 text-muted-foreground" />
                                {detail.workspaceName ?? 'Conta removida ou indisponível'}
                              </dd>
                            </div>
                            <div>
                              <dt className="text-xs text-muted-foreground">Solicitante</dt>
                              <dd className="mt-1 flex items-center gap-1.5 text-sm font-medium">
                                <UserRound className="h-4 w-4 text-muted-foreground" />
                                {detail.reporter?.name ?? detail.reporterUserId}
                              </dd>
                            </div>
                          </dl>
                        </div>
                        <Separator />
                        <div>
                          <h4 className="text-sm font-semibold">Contexto técnico</h4>
                          <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                            <div><dt className="text-xs text-muted-foreground">Versão frontend</dt><dd className="mt-1 break-all text-sm">{technicalValue(detail.technicalContext, 'frontendVersion')}</dd></div>
                            <div><dt className="text-xs text-muted-foreground">Página</dt><dd className="mt-1 break-all text-sm">{detail.route ?? technicalValue(detail.technicalContext, 'page')}</dd></div>
                            <div><dt className="text-xs text-muted-foreground">Ação</dt><dd className="mt-1 break-all text-sm">{technicalValue(detail.technicalContext, 'action')}</dd></div>
                            <div><dt className="text-xs text-muted-foreground">Navegador</dt><dd className="mt-1 break-all text-sm">{technicalValue(detail.technicalContext, 'browser')}</dd></div>
                            <div><dt className="text-xs text-muted-foreground">Dispositivo</dt><dd className="mt-1 break-all text-sm">{technicalValue(detail.technicalContext, 'device')}</dd></div>
                            <div><dt className="text-xs text-muted-foreground">Viewport</dt><dd className="mt-1 break-all text-sm">{technicalValue(detail.technicalContext, 'viewport')}</dd></div>
                            <div><dt className="text-xs text-muted-foreground">Tempo real</dt><dd className="mt-1 break-all text-sm">{technicalValue(detail.technicalContext, 'realtimeStatus')}</dd></div>
                            <div><dt className="text-xs text-muted-foreground">Ocorrência</dt><dd className="mt-1 text-sm">{formatDate(typeof detail.technicalContext.occurredAt === 'string' ? detail.technicalContext.occurredAt : null)}</dd></div>
                          </dl>
                          {detail.errorFingerprint && (
                            <div className="mt-3 rounded-md border bg-muted/30 p-3">
                              <p className="text-xs text-muted-foreground">Fingerprint do erro</p>
                              <p className="mt-1 break-all font-mono text-xs">{detail.errorFingerprint}</p>
                            </div>
                          )}
                          {Array.isArray(detail.technicalContext.failedRequestIds) && detail.technicalContext.failedRequestIds.length > 0 && (
                            <div className="mt-3">
                              <p className="text-xs text-muted-foreground">Requisições com falha</p>
                              <div className="mt-2 flex flex-wrap gap-1.5">
                                {detail.technicalContext.failedRequestIds.map((requestId) => (
                                  <Badge key={String(requestId)} variant="outline" className="font-mono text-xs">
                                    {String(requestId)}
                                  </Badge>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="space-y-4 p-4">
                        <div>
                          <div className="flex items-center justify-between gap-2">
                            <h4 className="text-sm font-semibold">Anexos</h4>
                            <Badge variant="outline">{detail.attachments.length}</Badge>
                          </div>
                          {detail.attachments.length === 0 ? (
                            <p className="mt-3 text-sm text-muted-foreground">Nenhum anexo registrado.</p>
                          ) : (
                            <div className="mt-3 space-y-2">
                              {detail.attachments.map((attachment) => (
                                <div key={attachment.id} className="flex items-start gap-3 rounded-md border p-3">
                                  <Paperclip className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                                  <div className="min-w-0 flex-1">
                                    <p className="truncate text-sm font-medium">{attachment.originalFilename}</p>
                                    <p className="mt-1 text-xs text-muted-foreground">
                                      {formatBytes(attachment.byteSize)} · {attachment.contentType}
                                    </p>
                                    <div className="mt-2 flex flex-wrap gap-1.5">
                                      <Badge variant="outline">{attachment.state}</Badge>
                                      <Badge variant={attachment.avStatus === 'clean' ? 'secondary' : 'outline'}>
                                        Antivírus: {attachment.avStatus}
                                      </Badge>
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                        <Separator />
                        <div>
                          <h4 className="text-sm font-semibold">Erros e incidentes</h4>
                          <div className="mt-3 grid gap-3">
                            <div className="space-y-1.5">
                              <Label>Erro relacionado</Label>
                              <PlatformEntityCombobox entityType="errors" purpose="support_evidence" workspaceId={detail.workspaceId} value={linkedRecords.linkedErrorLogId || null} disabled={!canTriage} onChange={(value) => setLinkedRecords((current) => ({ ...current, linkedErrorLogId: value ?? '' }))} label="Selecionar erro relacionado" placeholder="Pesquisar código ou tipo de erro" />
                            </div>
                            <div className="space-y-1.5">
                              <Label>Incidente relacionado</Label>
                              <PlatformEntityCombobox entityType="incidents" purpose="support_evidence" value={linkedRecords.linkedIncidentId || null} disabled={!canTriage} onChange={(value) => setLinkedRecords((current) => ({ ...current, linkedIncidentId: value ?? '' }))} label="Selecionar incidente relacionado" placeholder="Pesquisar incidente" />
                            </div>
                            <div className="space-y-1.5">
                              <Label>Release relacionada</Label>
                              <PlatformEntityCombobox entityType="releases" purpose="support_evidence" value={linkedRecords.linkedReleaseId || null} disabled={!canTriage} onChange={(value) => setLinkedRecords((current) => ({ ...current, linkedReleaseId: value ?? '' }))} label="Selecionar release relacionada" placeholder="Pesquisar versão ou commit" />
                            </div>
                            <div className="flex flex-wrap justify-between gap-2">
                              <Button variant="outline" size="sm" onClick={() => navigate('/blackbox/errors')}>
                                <FileText className="mr-2 h-4 w-4" />
                                Abrir erros
                              </Button>
                              {canTriage && (
                                <Button size="sm" onClick={saveLinkedRecords} disabled={patchMutation.isPending}>
                                  <Link2 className="mr-2 h-4 w-4" />
                                  Salvar vínculos
                                </Button>
                              )}
                            </div>
                          </div>
                        </div>
                        <Separator />
                        <div>
                          <h4 className="text-sm font-semibold">Acesso assistido</h4>
                          <p className="mt-1 text-xs text-muted-foreground">
                            Exige solicitação, justificativa e aprovação. O ticket não concede acesso à conta.
                          </p>
                          <div className="mt-3 space-y-2">
                            <Label>Solicitação vinculada</Label>
                            <PlatformEntityCombobox entityType="support_access" purpose="support_evidence" workspaceId={detail.workspaceId} value={linkedRecords.supportAccessRequestId || null} disabled={!canTriage} onChange={(value) => setLinkedRecords((current) => ({ ...current, supportAccessRequestId: value ?? '' }))} label="Selecionar solicitação de acesso" placeholder="Pesquisar ticket de acesso" />
                          </div>
                          <div className="mt-3 flex flex-wrap justify-between gap-2">
                            {canRequestSupportAccess && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => navigate(
                                  `/blackbox/support-access?ticketId=${encodeURIComponent(detail.id)}&workspaceId=${encodeURIComponent(detail.workspaceId)}`,
                                )}
                              >
                                <ShieldCheck className="mr-2 h-4 w-4" />
                                Solicitar acesso assistido
                              </Button>
                            )}
                            {canTriage && linkedRecords.supportAccessRequestId.trim() && linkedRecords.supportAccessRequestId !== detail.supportAccessRequestId && (
                              <Button size="sm" onClick={saveLinkedRecords} disabled={patchMutation.isPending}>
                                Vincular solicitação
                              </Button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  </TabsContent>

                  <TabsContent value="timeline" className="m-0">
                    <div className="grid gap-0 lg:grid-cols-[0.8fr_1.2fr]">
                      <div className="border-b p-4 lg:border-b-0 lg:border-r">
                        <h4 className="text-sm font-semibold">SLA e tempo por etapa</h4>
                        <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                          <div className="rounded-md border p-3">
                            <dt className="text-xs text-muted-foreground">Primeira resposta</dt>
                            <dd className="mt-1 text-sm font-semibold">
                              {detail.firstRespondedAt
                                ? formatDuration((new Date(detail.firstRespondedAt).getTime() - new Date(detail.createdAt).getTime()) / 1000)
                                : `Prazo ${formatDate(detail.firstResponseDueAt)}`}
                            </dd>
                          </div>
                          <div className="rounded-md border p-3">
                            <dt className="text-xs text-muted-foreground">Resolução</dt>
                            <dd className="mt-1 text-sm font-semibold">
                              {detail.resolvedAt
                                ? formatDuration((new Date(detail.resolvedAt).getTime() - new Date(detail.createdAt).getTime()) / 1000)
                                : `Prazo ${formatDate(detail.resolutionDueAt)}`}
                            </dd>
                          </div>
                          <div className="rounded-md border p-3">
                            <dt className="text-xs text-muted-foreground">Mediana geral da 1ª resposta</dt>
                            <dd className="mt-1 text-sm font-semibold">{formatMetricDuration(metrics?.firstResponse.medianSeconds)}</dd>
                          </div>
                          <div className="rounded-md border p-3">
                            <dt className="text-xs text-muted-foreground">P95 geral de resolução</dt>
                            <dd className="mt-1 text-sm font-semibold">{formatMetricDuration(metrics?.resolution.p95Seconds)}</dd>
                          </div>
                        </dl>
                        <div className="mt-4 space-y-2">
                          {stages.map((stage, index) => (
                            <div key={`${stage.status}-${stage.start}-${index}`} className="flex items-center justify-between gap-3 border-b py-2 last:border-b-0">
                              <div className="min-w-0">
                                <p className="text-sm font-medium">{STATUS_LABELS[stage.status]}</p>
                                <p className="truncate text-xs text-muted-foreground">{formatDate(stage.start)} até {formatDate(stage.end)}</p>
                              </div>
                              <Badge variant="outline" className="shrink-0 tabular-nums">{formatDuration(stage.seconds)}</Badge>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="p-4">
                        <div className="flex items-center justify-between gap-2">
                          <h4 className="text-sm font-semibold">Trilha de auditoria</h4>
                          <Badge variant="outline">{detail.events.length} eventos</Badge>
                        </div>
                        <div className="mt-4 space-y-0">
                          {[...detail.events]
                            .sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime())
                            .map((event, index) => (
                              <article key={event.id} className="relative flex gap-3 pb-5">
                                {index < detail.events.length - 1 && (
                                  <span className="absolute left-[7px] top-5 h-[calc(100%-12px)] w-px bg-border" aria-hidden="true" />
                                )}
                                <span className="relative mt-1.5 h-4 w-4 shrink-0 rounded-full border-2 border-background bg-primary" aria-hidden="true" />
                                <div className="min-w-0 flex-1">
                                  <div className="flex flex-wrap items-center justify-between gap-2">
                                    <p className="text-sm font-medium">{event.summary}</p>
                                    <time className="text-xs text-muted-foreground" dateTime={event.createdAt}>{formatDate(event.createdAt)}</time>
                                  </div>
                                  <p className="mt-1 font-mono text-xs text-muted-foreground">{event.eventType}</p>
                                  {typeof event.beforeData.status === 'string' && typeof event.afterData.status === 'string' && (
                                    <p className="mt-2 text-xs text-muted-foreground">
                                      {STATUS_LABELS[event.beforeData.status as TicketStatus] ?? String(event.beforeData.status)}
                                      {' → '}
                                      {STATUS_LABELS[event.afterData.status as TicketStatus] ?? String(event.afterData.status)}
                                    </p>
                                  )}
                                </div>
                              </article>
                            ))}
                        </div>
                      </div>
                    </div>
                  </TabsContent>
                </Tabs>
              </>
            )}
          </section>
        </div>

        <div className="grid gap-3 border-t pt-4 md:grid-cols-3">
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">Tempo médio de 1ª resposta</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">{formatMetricDuration(metrics?.firstResponse.averageSeconds)}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">Tempo médio de resolução</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">{formatMetricDuration(metrics?.resolution.averageSeconds)}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">Satisfação média</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">
              {metrics?.satisfactionAverage != null ? `${metrics.satisfactionAverage.toFixed(1)} / 5` : 'Sem avaliações'}
            </p>
          </div>
        </div>
      </section>
    </TooltipProvider>
  );
}

export default PlatformSupportTicketsPanel;
