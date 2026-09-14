import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  addDays,
  addHours,
  addMonths,
  addWeeks,
  differenceInMinutes,
  eachDayOfInterval,
  endOfDay,
  endOfMonth,
  endOfWeek,
  format,
  isAfter,
  isBefore,
  isSameDay,
  isSameMonth,
  isToday,
  parseISO,
  setHours,
  setMinutes,
  startOfDay,
  startOfMonth,
  startOfWeek,
  subDays,
  subMonths,
  subWeeks,
} from 'date-fns';
import { ptBR } from 'date-fns/locale';
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CalendarCheck2,
  CalendarClock,
  CalendarDays,
  CalendarX2,
  BrainCircuit,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CloudOff,
  ExternalLink,
  Filter,
  Link2,
  ListFilter,
  Loader2,
  MapPin,
  Minus,
  Pencil,
  Plus,
  RefreshCw,
  Radio,
  Search,
  Settings,
  Share2,
  Trash2,
  UserRound,
  Video,
} from 'lucide-react';

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { BookingLinkDialog } from '@/klm/components/BookingLinkDialog';
import { TaskCollaborationDrawer } from '@/klm/components/TaskCollaborationDrawer';
import { NativeCRMOperationsPanel } from '@/klm/components/NativeCRMOperationsPanel';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { OperationalCopilot } from '@/klm/components/OperationalCopilot';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmAgendaUnifiedEvent,
  type KlmFollowUp,
  type KlmMeeting,
  type KlmMeetingBriefing,
  type KlmMeetingEventType,
  type KlmMeetingNature,
  type KlmMeetingParticipantRsvp,
  type KlmMeetingParticipantWrite,
  type KlmMeetingWrite,
  type KlmOperationalCopilotAction,
} from '@/klm/api';
import {
  FOLLOW_UP_RECONCILIATION_MS,
  meetingReconciliationMs,
  type AgendaRealtimeStatus,
} from '@/klm/agenda-reconciliation';
import { cn } from '@/lib/utils';
import { fetchTeamMembers } from '@/services/admin';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import { ApiError } from '@/services/http';
import { useOptionalAppStore } from '@/store/app-context';

type CalendarView = 'month' | 'week' | 'day';
type AgendaListScope = 'upcoming' | 'history' | 'all';
type RealtimeStatus = AgendaRealtimeStatus;

function readRealtimeStatus(): RealtimeStatus {
  const current = document.documentElement.dataset.klmRealtimeStatus;
  return current === 'connected' || current === 'reconnecting' || current === 'offline'
    ? current
    : 'connecting';
}

type CalendarItem = {
  id: string;
  kind: 'meeting' | 'follow_up' | 'legal_event' | 'task_deadline';
  title: string;
  start: Date;
  end: Date;
  status: string;
  meeting?: KlmMeeting;
  followUp?: KlmFollowUp;
  matterName?: string;
  ownerName?: string;
  ownerId?: string;
  taskId?: string;
  natureza?: KlmMeetingNature;
  eventType?: KlmMeetingEventType;
  isInviolable?: boolean;
};

type MeetingDraft = {
  id?: string;
  version?: number;
  title: string;
  matterId: string;
  responsibleUserId: string;
  linkedTaskId: string;
  participants: KlmMeetingParticipantWrite[];
  confirmParticipantConflicts: boolean;
  conflictReason: string;
  startsAt: string;
  endsAt: string;
  status: KlmMeeting['status'];
  natureza: KlmMeetingNature;
  eventType: KlmMeetingEventType;
  countingDays: string;
  countingType: 'dias_uteis' | 'dias_corridos';
  location: string;
  meetingUrl: string;
  notes: string;
};

type MeetingConflictState = {
  kind: 'blocking' | 'warning';
  code: 'MEE-007' | 'MEE-008' | 'MEE-019';
  message: string;
  participantUserIds: string[];
};

type PositionedItem = CalendarItem & {
  lane: number;
  laneCount: number;
};

type AgendaRealtimeEvent = {
  id: string;
  event: 'MEETING_CREATED' | 'MEETING_UPDATED' | 'MEETING_CANCELLED' | 'MEETING_DELETED';
  meetingId: string | null;
  title: string | null;
  receivedAt: Date;
};

type AgendaMetric = {
  label: string;
  value: number;
  suffix?: string;
  previous: number;
  previousLabel: string;
  icon: typeof CalendarClock;
  mode?: 'relative' | 'points';
  betterWhenDown?: boolean;
};

const WEEK_STARTS_ON = 0 as const;
const START_HOUR = 7;
const END_HOUR = 21;
const REALTIME_INVALIDATION_DEBOUNCE_MS = 500;
const MIN_CONFLICT_REASON_LENGTH = 8;
const MAX_CONFLICT_REASON_LENGTH = 500;
const AGENDA_QUERY_NAMES = new Set([
  'meetings',
  'follow-ups',
  'funnel',
  'runtime',
  'team-members',
  'daily-briefing',
  'meeting-briefing',
  'matter-tasks',
  'unified-agenda',
]);
const AgendaVisibilityContext = createContext(true);

function useDocumentVisibility(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible');

  useEffect(() => {
    const handleVisibilityChange = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  return visible;
}

const HOUR_HEIGHT = 64;
const HOURS = Array.from({ length: END_HOUR - START_HOUR + 1 }, (_, index) => START_HOUR + index);
const WEEKDAY_LABELS = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];

function dateTimeLocal(value: Date | string) {
  const date = typeof value === 'string' ? parseISO(value) : value;
  return format(date, "yyyy-MM-dd'T'HH:mm");
}

/** Cores/etiquetas por tipo de evento (os 7 tipos da agenda unificada
 * comercial + jurídico). `badgeClass` colore a marcação no calendário e a
 * etiqueta na lista completa; `dotClass` alimenta a legenda. */
const EVENT_TYPE_META: Record<KlmMeetingEventType, { label: string; badgeClass: string; dotClass: string }> = {
  reuniao: { label: 'Reunião', badgeClass: 'border-[#031121]/25 bg-[#edf2f8] text-[#10243f] dark:bg-[#031121]/55 dark:text-blue-100', dotClass: 'bg-[#031121] dark:bg-blue-300' },
  audiencia: { label: 'Audiência', badgeClass: 'border-purple-700/25 bg-purple-50 text-purple-900 dark:bg-purple-500/15 dark:text-purple-300', dotClass: 'bg-purple-600' },
  sustentacao: { label: 'Sustentação oral', badgeClass: 'border-indigo-700/25 bg-indigo-50 text-indigo-900 dark:bg-indigo-500/15 dark:text-indigo-300', dotClass: 'bg-indigo-600' },
  prazo_interno: { label: 'Prazo interno', badgeClass: 'border-[#c3922f]/40 bg-[#fff8e8] text-[#6f4a05] dark:bg-[#c3922f]/15 dark:text-[#f1c46f]', dotClass: 'bg-[#c3922f]' },
  prazo_fatal: { label: 'Prazo fatal', badgeClass: 'border-red-700/25 bg-red-50 text-red-900 dark:bg-red-500/15 dark:text-red-300', dotClass: 'bg-red-600' },
  revisao: { label: 'Revisão', badgeClass: 'border-teal-700/25 bg-teal-50 text-teal-900 dark:bg-teal-500/15 dark:text-teal-300', dotClass: 'bg-teal-600' },
  personalizado: { label: 'Personalizado', badgeClass: 'border-slate-500/25 bg-slate-100 text-slate-900 dark:bg-slate-500/15 dark:text-slate-300', dotClass: 'bg-slate-500' },
};
const EVENT_TYPE_ORDER: KlmMeetingEventType[] = ['reuniao', 'audiencia', 'sustentacao', 'prazo_interno', 'prazo_fatal', 'revisao', 'personalizado'];
/** Natureza sugerida ao trocar o tipo de evento no formulário — o usuário
 * ainda pode sobrescrever manualmente no seletor de Natureza. */
const EVENT_TYPE_DEFAULT_NATURE: Record<KlmMeetingEventType, KlmMeetingNature> = {
  reuniao: 'comercial',
  audiencia: 'juridico',
  sustentacao: 'juridico',
  prazo_interno: 'juridico',
  prazo_fatal: 'juridico',
  revisao: 'juridico',
  personalizado: 'comercial',
};

/** Para tipo=prazo (interno ou fatal), a contagem informada some ao início
 * das observações — a `Meeting` não tem colunas dedicadas de contagem (só
 * `ProcessPublication`, calculada por `prazo_calculator.py`); aqui é
 * apoio manual do usuário ao agendar um prazo pela agenda. */
function buildDraftNotes(payload: MeetingDraft): string | null {
  const base = payload.notes.trim();
  if ((payload.eventType === 'prazo_interno' || payload.eventType === 'prazo_fatal') && payload.countingDays.trim()) {
    const countingLabel = payload.countingType === 'dias_uteis' ? 'dias úteis' : 'dias corridos';
    const countingLine = `Contagem do prazo: ${payload.countingDays.trim()} ${countingLabel}.`;
    return base ? `${countingLine}\n${base}` : countingLine;
  }
  return base || null;
}

function nextRoundedHour(source = new Date()) {
  const rounded = setMinutes(source, source.getMinutes() < 30 ? 30 : 0);
  return source.getMinutes() < 30 ? rounded : addHours(rounded, 1);
}

function emptyDraft(day = new Date(), explicitHour?: number): MeetingDraft {
  const now = new Date();
  const sameDay = isSameDay(day, now);
  const start = explicitHour !== undefined
    ? setMinutes(setHours(startOfDay(day), explicitHour), 0)
    : sameDay && isAfter(now, setHours(startOfDay(day), 9))
      ? nextRoundedHour(now)
      : setMinutes(setHours(startOfDay(day), 9), 0);
  return {
    title: '',
    matterId: '',
    responsibleUserId: '',
    linkedTaskId: '',
    participants: [],
    confirmParticipantConflicts: false,
    conflictReason: '',
    startsAt: dateTimeLocal(start),
    endsAt: dateTimeLocal(addHours(start, 1)),
    status: 'scheduled',
    natureza: 'comercial',
    eventType: 'reuniao',
    countingDays: '',
    countingType: 'dias_uteis',
    location: 'Google Meet',
    meetingUrl: '',
    notes: '',
  };
}

function meetingDraft(meeting: KlmMeeting): MeetingDraft {
  return {
    id: meeting.id,
    version: meeting.version,
    title: meeting.title,
    matterId: meeting.matterId ?? '',
    responsibleUserId: meeting.responsibleUserId ?? '',
    linkedTaskId: meeting.linkedTaskId ?? '',
    participants: (meeting.participants ?? [])
      .filter((participant) => participant.active)
      .map((participant) => ({
        userId: participant.userId,
        role: participant.role,
        rsvp: participant.rsvp,
        reminderMinutesBefore: participant.reminderMinutesBefore,
      })),
    confirmParticipantConflicts: false,
    conflictReason: '',
    startsAt: dateTimeLocal(meeting.startsAt),
    endsAt: dateTimeLocal(meeting.endsAt ?? addHours(parseISO(meeting.startsAt), 1)),
    status: meeting.status,
    natureza: meeting.natureza ?? 'comercial',
    eventType: meeting.eventType ?? 'reuniao',
    countingDays: '',
    countingType: 'dias_uteis',
    location: meeting.location ?? '',
    meetingUrl: meeting.meetingUrl ?? '',
    notes: meeting.notes ?? '',
  };
}

function participantWithDefaults(
  userId: string,
  role: KlmMeetingParticipantWrite['role'],
  previous?: KlmMeetingParticipantWrite,
): KlmMeetingParticipantWrite {
  return {
    userId,
    role,
    rsvp: previous?.rsvp ?? 'pending',
    reminderMinutesBefore: previous?.reminderMinutesBefore ?? null,
  };
}

function withMeetingOrganizer(
  participants: KlmMeetingParticipantWrite[],
  userId: string,
): KlmMeetingParticipantWrite[] {
  const previous = participants.find((participant) => participant.userId === userId);
  const collaborators = participants.filter(
    (participant) => participant.role !== 'organizer' && participant.userId !== userId,
  );
  return userId
    ? [participantWithDefaults(userId, 'organizer', previous), ...collaborators]
    : collaborators;
}

function meetingConflictFromError(error: unknown): MeetingConflictState | null {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') {
    return null;
  }
  const detail = error.detail as Record<string, unknown>;
  const code = detail.code;
  if (code !== 'MEE-007' && code !== 'MEE-008' && code !== 'MEE-019') return null;
  return {
    kind: code === 'MEE-007' ? 'blocking' : 'warning',
    code,
    message: typeof detail.message === 'string'
      ? detail.message
      : 'Há conflito de agenda entre os participantes selecionados.',
    participantUserIds: Array.isArray(detail.participantUserIds)
      ? detail.participantUserIds.filter((value): value is string => typeof value === 'string')
      : [],
  };
}

function itemTone(item: CalendarItem) {
  if (item.kind === 'follow_up') return 'border-[#c3922f]/35 bg-[#fff8e8] text-[#6f4a05] dark:bg-[#c3922f]/15 dark:text-[#f1c46f]';
  if (item.status === 'completed') return 'border-emerald-700/25 bg-emerald-50 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-300';
  if (item.status === 'cancelled') return 'border-border bg-secondary text-muted-foreground line-through';
  if (item.status === 'no_show') return 'border-red-700/25 bg-red-50 text-red-900 dark:bg-red-500/15 dark:text-red-300';
  // Tipo de evento (audiência, sustentação, prazo interno/fatal, revisão,
  // personalizado) sobrepõe a cor padrão de "reunião" quando o estado do
  // compromisso ainda é o normal/agendado — status terminal continua tendo
  // prioridade acima.
  if (item.eventType && item.eventType !== 'reuniao') return EVENT_TYPE_META[item.eventType].badgeClass;
  return 'border-[#031121]/25 bg-[#edf2f8] text-[#10243f] dark:bg-[#031121]/55 dark:text-blue-100';
}

function inDateRange(value: Date, start: Date, end: Date) {
  return !isBefore(value, start) && !isAfter(value, end);
}

function percentage(part: number, total: number) {
  return total > 0 ? Math.round((part / total) * 100) : 0;
}

function itemStatusLabel(item: CalendarItem) {
  if (item.kind === 'follow_up') {
    if (item.status === 'completed') return 'Follow-up concluído';
    if (['cancelled', 'suppressed'].includes(item.status)) return 'Follow-up encerrado';
    return 'Follow-up pendente';
  }
  if (item.kind === 'legal_event') {
    return item.isInviolable ? 'Prazo fatal confirmado' : (item.eventType ? EVENT_TYPE_META[item.eventType].label : 'Evento jurídico');
  }
  if (item.kind === 'task_deadline') return item.eventType === 'revisao' ? 'Prazo de revisão da tarefa' : 'Prazo operacional da tarefa';
  if (item.status === 'completed') return 'Concluída';
  if (item.status === 'cancelled') return 'Cancelada';
  if (item.status === 'no_show') return 'Não compareceu';
  return 'Agendada';
}

function metricTrend(metric: AgendaMetric) {
  const rawDelta = metric.mode === 'points'
    ? metric.value - metric.previous
    : metric.previous === 0
      ? (metric.value === 0 ? 0 : null)
      : ((metric.value - metric.previous) / metric.previous) * 100;
  const direction = rawDelta === null ? 1 : Math.sign(rawDelta);
  const favorable = direction === 0 || (metric.betterWhenDown ? direction < 0 : direction > 0);
  const label = rawDelta === null
    ? 'Novo'
    : `${rawDelta > 0 ? '+' : ''}${Math.round(rawDelta)}${metric.mode === 'points' ? ' p.p.' : '%'}`;
  return { rawDelta, direction, favorable, label };
}

function formatRangeTitle(anchor: Date, view: CalendarView) {
  if (view === 'month') {
    const label = format(anchor, "MMMM 'de' yyyy", { locale: ptBR });
    return label.charAt(0).toUpperCase() + label.slice(1);
  }
  if (view === 'day') {
    const label = format(anchor, "EEEE, d 'de' MMMM", { locale: ptBR });
    return label.charAt(0).toUpperCase() + label.slice(1);
  }
  const start = startOfWeek(anchor, { weekStartsOn: WEEK_STARTS_ON });
  const end = endOfWeek(anchor, { weekStartsOn: WEEK_STARTS_ON });
  return `${format(start, 'd MMM', { locale: ptBR })} – ${format(end, "d MMM 'de' yyyy", { locale: ptBR })}`;
}

function arrangeOverlaps(items: CalendarItem[]): PositionedItem[] {
  const sorted = [...items].sort((left, right) => left.start.getTime() - right.start.getTime());
  const result: PositionedItem[] = [];
  let index = 0;

  while (index < sorted.length) {
    const group: CalendarItem[] = [sorted[index]];
    let groupEnd = sorted[index].end.getTime();
    let cursor = index + 1;
    while (cursor < sorted.length && sorted[cursor].start.getTime() < groupEnd) {
      group.push(sorted[cursor]);
      groupEnd = Math.max(groupEnd, sorted[cursor].end.getTime());
      cursor += 1;
    }

    const laneEnds: number[] = [];
    const assignments = group.map((item) => {
      const start = item.start.getTime();
      let lane = laneEnds.findIndex((end) => end <= start);
      if (lane === -1) lane = laneEnds.length;
      laneEnds[lane] = item.end.getTime();
      return { item, lane };
    });
    result.push(...assignments.map(({ item, lane }) => ({ ...item, lane, laneCount: laneEnds.length })));
    index = cursor;
  }
  return result;
}

function DayAgendaGrid({
  days,
  items,
  onOpen,
  onCreate,
}: {
  days: Date[];
  items: CalendarItem[];
  onOpen: (item: CalendarItem) => void;
  onCreate: (day: Date, hour: number) => void;
}) {
  const bodyHeight = (END_HOUR - START_HOUR) * HOUR_HEIGHT;
  const now = new Date();

  return (
    <div className="overflow-x-auto">
      <div className={cn('min-w-[760px]', days.length === 1 && 'min-w-0')}>
        <div className="grid border-b border-border" style={{ gridTemplateColumns: `58px repeat(${days.length}, minmax(0, 1fr))` }}>
          <div className="border-r border-border" />
          {days.map((day) => (
            <button
              type="button"
              key={day.toISOString()}
              onClick={() => onCreate(day, 9)}
              className={cn('h-[58px] border-r border-border px-2 text-center transition hover:bg-secondary last:border-r-0', isToday(day) && 'bg-primary/10')}
              aria-label={`Criar compromisso em ${format(day, "d 'de' MMMM", { locale: ptBR })}`}
            >
              <span className="block text-2xs font-semibold uppercase text-muted-foreground">{format(day, 'EEE', { locale: ptBR })}</span>
              <span className={cn('mt-1 inline-flex h-7 min-w-7 items-center justify-center rounded-full px-1 text-sm font-semibold', isToday(day) && 'bg-[#031121] text-white dark:bg-primary dark:text-black')}>
                {format(day, 'd')}
              </span>
            </button>
          ))}
        </div>

        <div className="grid" style={{ gridTemplateColumns: `58px repeat(${days.length}, minmax(0, 1fr))` }}>
          <div className="relative border-r border-border" style={{ height: bodyHeight }}>
            {HOURS.slice(0, -1).map((hour) => (
              <span key={hour} className="absolute right-2 -translate-y-2 text-2xs font-medium text-muted-foreground" style={{ top: (hour - START_HOUR) * HOUR_HEIGHT }}>
                {`${String(hour).padStart(2, '0')}:00`}
              </span>
            ))}
          </div>

          {days.map((day) => {
            const dayItems = arrangeOverlaps(items.filter((item) => isSameDay(item.start, day)));
            const nowTop = ((now.getHours() + now.getMinutes() / 60) - START_HOUR) * HOUR_HEIGHT;
            return (
              <div key={day.toISOString()} className="relative border-r border-border last:border-r-0" style={{ height: bodyHeight }}>
                {HOURS.slice(0, -1).map((hour) => (
                  <button
                    type="button"
                    key={hour}
                    onClick={() => onCreate(day, hour)}
                    className="absolute inset-x-0 border-t border-border/80 transition hover:bg-primary/5"
                    style={{ top: (hour - START_HOUR) * HOUR_HEIGHT, height: HOUR_HEIGHT }}
                    aria-label={`Criar compromisso às ${hour}:00 em ${format(day, 'dd/MM/yyyy')}`}
                  />
                ))}
                {isToday(day) && nowTop >= 0 && nowTop <= bodyHeight && (
                  <div className="pointer-events-none absolute inset-x-0 z-20 flex items-center" style={{ top: nowTop }}>
                    <span className="h-2 w-2 -translate-x-1 rounded-full bg-red-500" />
                    <span className="h-px flex-1 bg-red-500" />
                  </div>
                )}
                {dayItems.map((item) => {
                  const clippedStart = Math.max(item.start.getHours() + item.start.getMinutes() / 60, START_HOUR);
                  const clippedEnd = Math.min(item.end.getHours() + item.end.getMinutes() / 60, END_HOUR);
                  const top = Math.max(0, (clippedStart - START_HOUR) * HOUR_HEIGHT);
                  const height = Math.max(30, (clippedEnd - clippedStart) * HOUR_HEIGHT - 3);
                  const width = 100 / item.laneCount;
                  return (
                    <MeetingContextHover key={`${item.kind}-${item.id}`} item={item}>
                      <button
                        type="button"
                        onClick={() => onOpen(item)}
                        className={cn('absolute z-10 overflow-hidden rounded border px-2 py-1 text-left text-2xs shadow-sm transition hover:z-30 hover:brightness-95 focus-visible:z-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', itemTone(item))}
                        style={{ top, height, left: `calc(${width * item.lane}% + 2px)`, width: `calc(${width}% - 4px)` }}
                        title={item.title}
                      >
                        <span className="block font-semibold">{format(item.start, 'HH:mm')}</span>
                        <span className="block truncate">{item.title}</span>
                        {height > 52 && item.matterName && <span className="mt-1 block truncate opacity-75">{item.matterName}</span>}
                      </button>
                    </MeetingContextHover>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function MonthGrid({
  anchor,
  selectedDay,
  items,
  onSelectDay,
  onOpen,
}: {
  anchor: Date;
  selectedDay: Date;
  items: CalendarItem[];
  onSelectDay: (day: Date) => void;
  onOpen: (item: CalendarItem) => void;
}) {
  const first = startOfWeek(startOfMonth(anchor), { weekStartsOn: WEEK_STARTS_ON });
  const last = endOfWeek(endOfMonth(anchor), { weekStartsOn: WEEK_STARTS_ON });
  const days = eachDayOfInterval({ start: first, end: last });

  return (
    <div className="overflow-x-auto">
      <div className="min-w-0">
        <div className="grid grid-cols-7 border-b border-border bg-secondary/55">
          {WEEKDAY_LABELS.map((label) => (
            <div key={label} className="border-r border-border px-2 py-2 text-center text-2xs font-semibold uppercase text-muted-foreground last:border-r-0">
              {label}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7">
          {days.map((day) => {
            const dayItems = items.filter((item) => isSameDay(item.start, day));
            return (
              <div
                key={day.toISOString()}
                className={cn(
                  'group min-h-[68px] min-w-0 border-b border-r border-border p-1 last:border-r-0 sm:min-h-[118px] sm:p-2',
                  !isSameMonth(day, anchor) && 'bg-secondary/35 text-muted-foreground',
                  isSameDay(day, selectedDay) && 'bg-primary/8 shadow-[inset_0_0_0_1px_hsl(var(--primary))]',
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelectDay(day)}
                  className={cn('mb-1 inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1 text-xs font-semibold transition hover:bg-secondary', isToday(day) && 'bg-[#031121] text-white hover:bg-[#031121] dark:bg-primary dark:text-black')}
                  aria-label={`Selecionar ${format(day, "d 'de' MMMM", { locale: ptBR })}`}
                >
                  {format(day, 'd')}
                </button>
                <div className="flex min-w-0 flex-wrap gap-1 sm:hidden">
                  {dayItems.slice(0, 3).map((item) => (
                    <MeetingContextHover key={`dot-${item.kind}-${item.id}`} item={item}>
                      <button
                        type="button"
                        onClick={() => onOpen(item)}
                        className={cn(
                          'h-2 w-2 rounded-full ring-1 ring-inset ring-black/10',
                          item.kind === 'follow_up' ? 'bg-primary' : item.status === 'completed' ? 'bg-emerald-600' : item.status === 'cancelled' ? 'bg-muted-foreground' : item.status === 'no_show' ? 'bg-red-600' : 'bg-[#031121] dark:bg-blue-300',
                        )}
                        aria-label={`${format(item.start, 'HH:mm')} · ${item.title}`}
                      />
                    </MeetingContextHover>
                  ))}
                  {dayItems.length > 3 && <span className="text-2xs font-semibold text-muted-foreground">+{dayItems.length - 3}</span>}
                </div>
                <div className="hidden space-y-1 sm:block">
                  {dayItems.slice(0, 3).map((item) => (
                    <MeetingContextHover key={`${item.kind}-${item.id}`} item={item}>
                      <button
                        type="button"
                        onClick={() => onOpen(item)}
                        className={cn('block h-6 w-full truncate rounded border px-1.5 text-left text-2xs font-medium transition hover:brightness-95 sm:text-2xs', itemTone(item))}
                        title={`${format(item.start, 'HH:mm')} · ${item.title}`}
                      >
                        <span className="font-semibold">{format(item.start, 'HH:mm')}</span> · {item.title}
                      </button>
                    </MeetingContextHover>
                  ))}
                  {dayItems.length > 3 && (
                    <button type="button" onClick={() => onSelectDay(day)} className="pl-1 text-2xs font-semibold text-muted-foreground hover:text-foreground">
                      +{dayItems.length - 3} compromissos
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function BriefingSnapshot({ briefing, compact = false }: { briefing: KlmMeetingBriefing; compact?: boolean }) {
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><p className="truncate text-xs font-semibold text-foreground">{briefing.clientName}</p><p className="mt-0.5 truncate text-2xs text-muted-foreground">{briefing.demandType ?? (briefing.linkedToMatter ? 'Caso em preparação' : 'Agendamento sem card')}</p></div>
        <KlmPill tone={briefing.linkedToMatter ? 'green' : 'gold'}>{briefing.linkedToMatter ? `Score ${briefing.leadScore ?? '—'}` : 'Vincular'}</KlmPill>
      </div>
      <p className={cn('text-2xs leading-4 text-muted-foreground', compact && 'line-clamp-3')}>{briefing.summary}</p>
      <div className="flex flex-wrap gap-1.5">
        {briefing.healthObjectName && <KlmPill>{briefing.healthObjectName}</KlmPill>}
        {briefing.medicalUrgencyHours && <KlmPill tone={briefing.medicalUrgencyHours <= 24 ? 'red' : 'gold'}>{briefing.medicalUrgencyHours}h de urgência</KlmPill>}
        {briefing.documentCount > 0 && <KlmPill tone="gold">Docs {briefing.readyDocumentCount}/{briefing.documentCount}</KlmPill>}
        {briefing.noShowRisk && <KlmPill tone={briefing.noShowRisk === 'high' ? 'red' : 'muted'}>Ausência {briefing.noShowRisk}</KlmPill>}
      </div>
      {briefing.warnings.length > 0 && <div className="space-y-1 border-t border-border pt-2">{briefing.warnings.slice(0, compact ? 2 : 4).map((warning) => <p key={warning} className="flex items-start gap-1.5 text-2xs leading-4 text-[#a53e3c]"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{warning}</p>)}</div>}
      {!compact && briefing.insights.length > 0 && <div className="space-y-1 border-t border-border pt-2">{briefing.insights.slice(0, 4).map((insight) => <p key={insight} className="text-2xs leading-4 text-muted-foreground">• {insight}</p>)}</div>}
      <div className="flex items-center justify-between gap-3 border-t border-border pt-2 text-2xs text-muted-foreground"><span>{briefing.generatedBy} · contexto local</span>{briefing.roomProvider && <span>{briefing.roomProvider}</span>}</div>
    </div>
  );
}

function MeetingContextHover({ item, children }: { item: CalendarItem; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const documentVisible = useContext(AgendaVisibilityContext);
  const briefingQuery = useQuery({
    queryKey: ['klm', 'meeting-briefing', item.meeting?.id],
    queryFn: () => klmApi.meetingBriefing(item.meeting!.id),
    enabled: documentVisible && open && Boolean(item.meeting),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  if (!item.meeting) return <>{children}</>;
  return (
    <HoverCard openDelay={280} closeDelay={120} onOpenChange={setOpen}>
      <HoverCardTrigger asChild>{children}</HoverCardTrigger>
      <HoverCardContent side="right" align="start" className="w-[340px] p-4">
        {briefingQuery.isLoading ? <div className="flex items-center py-5 text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Preparando contexto da reunião...</div> : briefingQuery.data ? <BriefingSnapshot briefing={briefingQuery.data} compact /> : <p className="py-4 text-2xs text-muted-foreground">Contexto indisponível neste momento.</p>}
      </HoverCardContent>
    </HoverCard>
  );
}

function AgendaItemRow({ item, onClick }: { item: CalendarItem; onClick: () => void }) {
  return (
    <MeetingContextHover item={item}>
      <button type="button" onClick={onClick} className="flex w-full min-w-0 items-start gap-3 border-t border-border px-4 py-3 text-left transition first:border-t-0 hover:bg-secondary/60">
        <span className={cn('mt-1 h-2.5 w-2.5 shrink-0 rounded-full', item.kind === 'follow_up' ? 'bg-primary' : item.status === 'completed' ? 'bg-emerald-600' : item.status === 'cancelled' ? 'bg-muted-foreground' : item.status === 'no_show' ? 'bg-red-600' : 'bg-[#031121] dark:bg-blue-300')} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center justify-between gap-2">
            <span className="truncate text-xs font-semibold text-foreground">{item.title}</span>
            <span className="shrink-0 text-2xs font-semibold text-muted-foreground">{format(item.start, 'HH:mm')}</span>
          </span>
          <span className="mt-0.5 block truncate text-2xs text-muted-foreground">
            {item.matterName ?? (item.kind === 'follow_up' ? 'Follow-up comercial' : item.meeting?.location ?? 'Sem local')}
          </span>
        </span>
      </button>
    </MeetingContextHover>
  );
}

export default function AgendaPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const appStore = useOptionalAppStore();
  const canEditAgenda = appStore?.can('kanban', 'edit') ?? true;
  const documentVisible = useDocumentVisibility();
  const [view, setView] = useState<CalendarView>('month');
  const [anchor, setAnchor] = useState(new Date());
  const [selectedDay, setSelectedDay] = useState(new Date());
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [ownerFilter, setOwnerFilter] = useState('all');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [showMeetings, setShowMeetings] = useState(true);
  const [showFollowUps, setShowFollowUps] = useState(true);
  const [showLegalEvents, setShowLegalEvents] = useState(true);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  // Toggle Tudo | Comercial | Jurídico — default pelo papel do usuário:
  // quem só enxerga o módulo jurídico abre em "Jurídico"; quem só enxerga
  // o CRM comercial abre em "Comercial"; perfis com os dois abrem em "Tudo".
  const [natureFilter, setNatureFilter] = useState<'todos' | KlmMeetingNature>(() => {
    const canLegal = appStore?.can('legal', 'view') ?? false;
    const canComercial = appStore?.can('kanban', 'view') ?? true;
    if (canLegal && !canComercial) return 'juridico';
    if (canComercial && !canLegal) return 'comercial';
    return 'todos';
  });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [draft, setDraft] = useState<MeetingDraft | null>(null);
  const [participantSearch, setParticipantSearch] = useState('');
  const [taskSearch, setTaskSearch] = useState('');
  const [meetingConflict, setMeetingConflict] = useState<MeetingConflictState | null>(null);
  const [followUpDetail, setFollowUpDetail] = useState<CalendarItem | null>(null);
  const [googleDialogOpen, setGoogleDialogOpen] = useState(false);
  const [bookingDialogOpen, setBookingDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KlmMeeting | null>(null);
  const [handledMeetingId, setHandledMeetingId] = useState<string | null>(null);
  const [handledCreateContext, setHandledCreateContext] = useState<string | null>(null);
  const [listScope, setListScope] = useState<AgendaListScope>('upcoming');
  const [visibleListCount, setVisibleListCount] = useState(20);
  const [realtimeStatus, setRealtimeStatus] = useState<RealtimeStatus>(readRealtimeStatus);
  const [realtimeEvents, setRealtimeEvents] = useState<AgendaRealtimeEvent[]>([]);
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(null);
  const draftIdentity = draft ? (draft.id ?? '__new__') : '__closed__';

  useEffect(() => {
    setParticipantSearch('');
    setTaskSearch('');
    setMeetingConflict(null);
  }, [draftIdentity]);

  const contextualPath = (path: string, extras: Record<string, string> = {}) => {
    const params = new URLSearchParams();
    const matterId = searchParams.get('matter');
    const threadId = searchParams.get('thread');
    if (matterId) params.set('matter', matterId);
    if (threadId) params.set('thread', threadId);
    Object.entries(extras).forEach(([key, value]) => params.set(key, value));
    const query = params.toString();
    return `${path}${query ? `?${query}` : ''}`;
  };

  const meetingsQuery = useQuery({
    queryKey: ['klm', 'meetings'],
    queryFn: klmApi.meetings,
    enabled: documentVisible,
    // WebSocket reduz a latência, mas não é uma fila durável. A reconciliação
    // periódica impede que um evento pós-commit perdido deixe a agenda obsoleta.
    refetchInterval: documentVisible ? meetingReconciliationMs(realtimeStatus) : false,
    refetchOnWindowFocus: false,
  });
  const followUpsQuery = useQuery({
    queryKey: ['klm', 'follow-ups'],
    queryFn: klmApi.followUps,
    enabled: documentVisible,
    refetchInterval: documentVisible ? FOLLOW_UP_RECONCILIATION_MS : false,
    refetchOnWindowFocus: false,
  });
  const funnelQuery = useQuery({
    queryKey: ['klm', 'funnel'],
    queryFn: klmApi.funnel,
    enabled: documentVisible,
    refetchOnWindowFocus: false,
  });
  const runtimeQuery = useQuery({
    queryKey: ['klm', 'runtime'],
    queryFn: klmApi.runtime,
    enabled: documentVisible,
    refetchOnWindowFocus: false,
  });
  const teamQuery = useQuery({
    queryKey: ['klm', 'team-members'],
    queryFn: fetchTeamMembers,
    enabled: documentVisible,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const dailyBriefingQuery = useQuery({
    queryKey: ['klm', 'daily-briefing', format(selectedDay, 'yyyy-MM-dd')],
    queryFn: () => klmApi.dailyBriefing(format(selectedDay, 'yyyy-MM-dd')),
    enabled: documentVisible,
    refetchOnWindowFocus: false,
  });
  const draftBriefingQuery = useQuery({
    queryKey: ['klm', 'meeting-briefing', draft?.id],
    queryFn: () => klmApi.meetingBriefing(draft!.id!),
    enabled: documentVisible && Boolean(draft?.id),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const matterTasksQuery = useQuery({
    queryKey: ['klm', 'matter-tasks', draft?.matterId],
    queryFn: () => klmApi.matterTasks(draft!.matterId),
    enabled: documentVisible && Boolean(draft?.matterId),
    retry: false,
    refetchOnWindowFocus: false,
  });
  // Janela visível do calendário (mês/semana/dia atual) — recorta a busca
  // do read-model unificado para o mesmo período que o usuário está vendo.
  const unifiedAgendaRange = useMemo(() => {
    if (view === 'day') return { start: startOfDay(anchor), end: endOfDay(anchor) };
    if (view === 'week') {
      return {
        start: startOfWeek(anchor, { weekStartsOn: WEEK_STARTS_ON }),
        end: endOfWeek(anchor, { weekStartsOn: WEEK_STARTS_ON }),
      };
    }
    return {
      start: startOfWeek(startOfMonth(anchor), { weekStartsOn: WEEK_STARTS_ON }),
      end: endOfWeek(endOfMonth(anchor), { weekStartsOn: WEEK_STARTS_ON }),
    };
  }, [anchor, view]);
  // Read-model unificado (`GET /agenda`, natureza=juridico) — fontes que
  // `GET /meetings` não devolve: prazo fatal confirmado e os três relógios
  // explícitos da tarefa (geral, individual e revisão). Nenhum prazo é
  // recalculado ou substituído por fallback no frontend.
  const unifiedLegalAgendaQuery = useQuery({
    queryKey: ['klm', 'unified-agenda', 'juridico', format(unifiedAgendaRange.start, 'yyyy-MM-dd'), format(unifiedAgendaRange.end, 'yyyy-MM-dd')],
    queryFn: () => klmApi.unifiedAgenda({
      natureza: 'juridico',
      start: unifiedAgendaRange.start.toISOString(),
      end: unifiedAgendaRange.end.toISOString(),
    }),
    enabled: documentVisible,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (documentVisible) return;
    void queryClient.cancelQueries({
      predicate: (query) => (
        query.queryKey[0] === 'klm'
        && typeof query.queryKey[1] === 'string'
        && AGENDA_QUERY_NAMES.has(query.queryKey[1])
      ),
    });
  }, [documentVisible, queryClient]);

  useEffect(() => {
    let invalidationTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleRealtimeInvalidation = () => {
      if (invalidationTimer !== null) clearTimeout(invalidationTimer);
      invalidationTimer = setTimeout(() => {
        invalidationTimer = null;
        void queryClient.invalidateQueries({ queryKey: ['klm', 'meetings'] });
        void queryClient.invalidateQueries({ queryKey: ['klm', 'daily-briefing'] });
      }, REALTIME_INVALIDATION_DEBOUNCE_MS);
    };
    const handleRealtimeStatus = (event: Event) => {
      const status = (event as CustomEvent<{ status?: RealtimeStatus }>).detail?.status;
      if (status) setRealtimeStatus(status);
    };
    const handleRealtimeEvent = (event: Event) => {
      const detail = (event as CustomEvent<{ event?: string; data?: Record<string, unknown> }>).detail;
      if (!detail?.event || !['MEETING_CREATED', 'MEETING_UPDATED', 'MEETING_CANCELLED', 'MEETING_DELETED'].includes(detail.event)) return;
      const receivedAt = new Date();
      setRealtimeEvents((current) => [{
        id: `${detail.event}-${receivedAt.getTime()}-${Math.random().toString(16).slice(2)}`,
        event: detail.event as AgendaRealtimeEvent['event'],
        meetingId: typeof detail.data?.meeting_id === 'string' ? detail.data.meeting_id : null,
        title: typeof detail.data?.title === 'string' ? detail.data.title : null,
        receivedAt,
      }, ...current].slice(0, 12));
      setLastRefreshAt(receivedAt);
      scheduleRealtimeInvalidation();
    };

    window.addEventListener('klm:realtime-status', handleRealtimeStatus);
    window.addEventListener('klm:realtime', handleRealtimeEvent);
    // A conexão pode mudar entre o render e o registro dos listeners.
    setRealtimeStatus(readRealtimeStatus());
    return () => {
      if (invalidationTimer !== null) clearTimeout(invalidationTimer);
      window.removeEventListener('klm:realtime-status', handleRealtimeStatus);
      window.removeEventListener('klm:realtime', handleRealtimeEvent);
    };
  }, [queryClient]);

  useEffect(() => {
    const refreshedAt = Math.max(meetingsQuery.dataUpdatedAt, followUpsQuery.dataUpdatedAt);
    if (refreshedAt > 0) setLastRefreshAt(new Date(refreshedAt));
  }, [followUpsQuery.dataUpdatedAt, meetingsQuery.dataUpdatedAt]);

  const saveMutation = useMutation({
    mutationFn: async (payload: MeetingDraft) => {
      const participants = withMeetingOrganizer(
        payload.participants,
        payload.responsibleUserId,
      );
      const write: KlmMeetingWrite = {
        matterId: payload.matterId || null,
        responsibleUserId: payload.responsibleUserId || null,
        linkedTaskId: payload.linkedTaskId || null,
        title: payload.title.trim(),
        startsAt: new Date(payload.startsAt).toISOString(),
        endsAt: payload.endsAt ? new Date(payload.endsAt).toISOString() : null,
        natureza: payload.natureza,
        eventType: payload.eventType,
        location: payload.location.trim() || null,
        meetingUrl: payload.meetingUrl.trim() || null,
        notes: buildDraftNotes(payload),
        recipientOverrides: [],
        participants,
        confirmParticipantConflicts: payload.confirmParticipantConflicts,
        conflictReason: payload.confirmParticipantConflicts
          ? payload.conflictReason.trim()
          : null,
      };
      if (payload.id) {
        if (!payload.version) throw new Error('Atualize a agenda antes de editar este compromisso.');
        return klmApi.updateMeeting(payload.id, {
          ...write,
          status: payload.status,
          expectedVersion: payload.version,
        });
      }
      return klmApi.createMeeting(write);
    },
    onSuccess: async (meeting) => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'meetings'] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'daily-briefing'] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'unified-agenda'] });
      setDraft(null);
      setMeetingConflict(null);
      setParticipantSearch('');
      setTaskSearch('');
      setAnchor(parseISO(meeting.startsAt));
      setSelectedDay(parseISO(meeting.startsAt));
      toast({ title: meeting.status === 'scheduled' ? 'Reunião salva na agenda.' : 'Reunião atualizada.' });
    },
    onError: (error: Error) => {
      const conflict = meetingConflictFromError(error);
      if (conflict) setMeetingConflict(conflict);
      toast({
        variant: 'destructive',
        title: conflict?.kind === 'blocking'
          ? 'Conflito bloqueante na agenda'
          : conflict?.kind === 'warning'
            ? 'Confirme a sobreposição para continuar'
            : 'Não foi possível salvar a reunião',
        description: conflict?.message ?? error.message,
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (meeting: KlmMeeting) => klmApi.deleteMeeting(meeting.id, meeting.version),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'meetings'] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'daily-briefing'] });
      setDeleteTarget(null);
      setDraft(null);
      toast({ title: 'Reunião excluída da agenda.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível excluir a reunião', description: error.message }),
  });

  const googleRefreshMutation = useMutation({
    mutationFn: async () => {
      const [meetingsResult, runtimeResult] = await Promise.all([
        meetingsQuery.refetch(),
        runtimeQuery.refetch(),
      ]);
      if (meetingsResult.error) throw meetingsResult.error;
      if (runtimeResult.error) throw runtimeResult.error;
      return meetingsResult.data?.length ?? 0;
    },
    onSuccess: (meetingCount) => {
      setLastRefreshAt(new Date());
      toast({
        title: 'Agenda local atualizada.',
        description: `${meetingCount} reunião(ões) recarregada(s). Nenhuma sincronização com o Google foi executada.`,
      });
    },
    onError: (error: Error) => toast({
      variant: 'destructive',
      title: 'Não foi possível atualizar a agenda local',
      description: error.message,
    }),
  });

  const mattersById = useMemo(() => new Map((funnelQuery.data?.cards ?? []).map((matter) => [matter.id, matter])), [funnelQuery.data]);
  const teamById = useMemo(() => new Map((teamQuery.data ?? []).map((member) => [member.id, member])), [teamQuery.data]);
  const activeTeamMembers = useMemo(
    () => (teamQuery.data ?? []).filter((member) => member.isActive),
    [teamQuery.data],
  );
  const participantCandidates = useMemo(() => {
    const selected = new Set((draft?.participants ?? []).map((participant) => participant.userId));
    if (draft?.responsibleUserId) selected.add(draft.responsibleUserId);
    const normalizedSearch = participantSearch.trim().toLocaleLowerCase('pt-BR');
    return activeTeamMembers.filter((member) => (
      !selected.has(member.id)
      && (
        !normalizedSearch
        || member.name.toLocaleLowerCase('pt-BR').includes(normalizedSearch)
        || member.email.toLocaleLowerCase('pt-BR').includes(normalizedSearch)
      )
    ));
  }, [activeTeamMembers, draft?.participants, draft?.responsibleUserId, participantSearch]);
  const matterTasks = useMemo(
    () => matterTasksQuery.data ?? [],
    [matterTasksQuery.data],
  );
  const selectedLinkedTask = draft?.linkedTaskId
    ? matterTasks.find((task) => task.id === draft.linkedTaskId)
    : undefined;
  const filteredMatterTasks = useMemo(() => {
    const normalizedSearch = taskSearch.trim().toLocaleLowerCase('pt-BR');
    return matterTasks.filter((task) => (
      task.id === draft?.linkedTaskId
      || !normalizedSearch
      || task.title.toLocaleLowerCase('pt-BR').includes(normalizedSearch)
    ));
  }, [draft?.linkedTaskId, matterTasks, taskSearch]);
  const conflictParticipantLabels = (meetingConflict?.participantUserIds ?? []).map(
    (userId) => teamById.get(userId)?.name ?? 'Participante interno',
  );

  const updateConflictSensitiveDraft = (
    update: (current: MeetingDraft) => MeetingDraft,
  ) => {
    setMeetingConflict(null);
    setDraft((current) => current ? {
      ...update(current),
      confirmParticipantConflicts: false,
      conflictReason: '',
    } : current);
  };

  const addMeetingParticipant = (
    userId: string,
    role: 'attendee' | 'observer',
  ) => {
    updateConflictSensitiveDraft((current) => {
      if (!current.responsibleUserId) return current;
      if (current.participants.some((participant) => participant.userId === userId)) {
        return current;
      }
      return {
        ...current,
        participants: [
          ...withMeetingOrganizer(current.participants, current.responsibleUserId),
          participantWithDefaults(userId, role),
        ],
      };
    });
    setParticipantSearch('');
  };

  const updateMeetingParticipant = (
    userId: string,
    patch: Partial<KlmMeetingParticipantWrite>,
  ) => {
    updateConflictSensitiveDraft((current) => {
      const participants = userId === current.responsibleUserId
        ? withMeetingOrganizer(current.participants, current.responsibleUserId)
        : current.participants;
      return {
        ...current,
        participants: participants.map((participant) => (
          participant.userId === userId ? { ...participant, ...patch } : participant
        )),
      };
    });
  };

  const removeMeetingParticipant = (userId: string) => {
    updateConflictSensitiveDraft((current) => ({
      ...current,
      participants: current.participants.filter(
        (participant) => participant.userId !== userId,
      ),
    }));
  };

  const allItems = useMemo<CalendarItem[]>(() => {
    const meetings = (meetingsQuery.data ?? []).map((meeting) => {
      const start = parseISO(meeting.startsAt);
      return {
        id: meeting.id,
        kind: 'meeting' as const,
        title: meeting.title.replace(/^Reunião online:\s*/i, ''),
        start,
        end: meeting.endsAt ? parseISO(meeting.endsAt) : addHours(start, 1),
        status: meeting.status,
        meeting,
        matterName: meeting.matterId ? mattersById.get(meeting.matterId)?.name : undefined,
        ownerName: meeting.responsibleUserId ? teamById.get(meeting.responsibleUserId)?.name : undefined,
        natureza: meeting.natureza ?? 'comercial',
        eventType: meeting.eventType ?? 'reuniao',
      };
    });
    const followUps = (followUpsQuery.data ?? []).map((followUp) => {
      const start = parseISO(followUp.dueAt);
      return {
        id: followUp.id,
        kind: 'follow_up' as const,
        title: followUp.reason || `Follow-up ${followUp.attempt}/${followUp.maxAttempts}`,
        start,
        end: addHours(start, 0.5),
        status: followUp.status,
        followUp,
        matterName: followUp.matterId ? mattersById.get(followUp.matterId)?.name : undefined,
      };
    });
    // Só fontes que `GET /meetings` não cobre. `meeting` é descartado para
    // impedir duplicidade, porque já veio da consulta de reuniões acima.
    const unifiedEvents = (unifiedLegalAgendaQuery.data ?? [])
      .filter((event): event is KlmAgendaUnifiedEvent => event.source !== 'meeting')
      .map((event) => {
        const start = parseISO(event.startsAt);
        const isTaskDeadline = event.source === 'task_due'
          || event.source === 'task_participant_due'
          || event.source === 'task_review_due';
        return {
          id: event.id,
          kind: isTaskDeadline ? 'task_deadline' as const : 'legal_event' as const,
          title: event.title,
          start,
          end: event.endsAt ? parseISO(event.endsAt) : addHours(start, 1),
          status: event.status ?? 'confirmado',
          matterName: [event.clientLabel, event.matterLabel].filter(Boolean).join(' · ')
            || (event.matterId ? mattersById.get(event.matterId)?.name : undefined),
          ownerName: event.responsibleLabel
            || (event.responsibleUserId ? teamById.get(event.responsibleUserId)?.name : undefined),
          ownerId: event.responsibleUserId ?? undefined,
          taskId: event.taskId ?? undefined,
          natureza: event.natureza,
          eventType: event.eventType,
          isInviolable: event.isInviolable,
        };
      });
    return [...meetings, ...followUps, ...unifiedEvents].sort((left, right) => left.start.getTime() - right.start.getTime());
  }, [followUpsQuery.data, mattersById, meetingsQuery.data, teamById, unifiedLegalAgendaQuery.data]);

  const personalizedFields = useMemo<PersonalizedFilterField<CalendarItem>[]>(() => [
    { key: 'kind', label: 'Tipo', type: 'select', options: [{ value: 'meeting', label: 'Reunião' }, { value: 'follow_up', label: 'Follow-up' }, { value: 'legal_event', label: 'Prazo fatal' }, { value: 'task_deadline', label: 'Prazo de tarefa' }] },
    { key: 'title', label: 'Título', type: 'text' },
    { key: 'status', label: 'Status', type: 'select', options: [...new Set(allItems.map((item) => item.status))].map((value) => ({ value, label: itemStatusLabel({ ...allItems.find((item) => item.status === value)!, status: value }) })) },
    { key: 'matterName', label: 'Lead ou caso', type: 'text' },
    { key: 'ownerName', label: 'Responsável', type: 'select', options: [...new Set(allItems.map((item) => item.ownerName).filter((value): value is string => Boolean(value)))].map((value) => ({ value, label: value })) },
    { key: 'start', label: 'Início', type: 'date' },
    { key: 'end', label: 'Término', type: 'date' },
    { key: 'meeting.location', label: 'Local ou plataforma', type: 'text' },
    { key: 'meeting.meetingUrl', label: 'Possui link de reunião', type: 'boolean', getValue: (item) => Boolean(item.meeting?.meetingUrl) },
    { key: 'meeting.matterId', label: 'Vinculado a card', type: 'boolean', getValue: (item) => Boolean(item.meeting?.matterId ?? item.followUp?.matterId ?? item.matterName) },
    { key: 'natureza', label: 'Natureza', type: 'select', options: [{ value: 'comercial', label: 'Comercial' }, { value: 'juridico', label: 'Jurídico' }], getValue: (item) => item.natureza ?? 'comercial' },
    { key: 'eventType', label: 'Tipo de evento', type: 'select', options: EVENT_TYPE_ORDER.map((type) => ({ value: type, label: EVENT_TYPE_META[type].label })), getValue: (item) => item.eventType ?? 'reuniao' },
  ], [allItems]);

  const filteredItems = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase('pt-BR');
    return allItems.filter((item) => {
      if (item.kind === 'meeting' && !showMeetings) return false;
      if (item.kind === 'follow_up' && !showFollowUps) return false;
      if ((item.kind === 'legal_event' || item.kind === 'task_deadline') && !showLegalEvents) return false;
      if (natureFilter !== 'todos') {
        const nature = item.kind === 'follow_up' ? 'comercial' : (item.natureza ?? 'comercial');
        if (nature !== natureFilter) return false;
      }
      if (statusFilter !== 'all' && item.status !== statusFilter) return false;
      if (ownerFilter !== 'all' && (item.meeting?.responsibleUserId ?? item.ownerId) !== ownerFilter) return false;
      if (!matchesPersonalizedFilter(item, personalizedFilter, personalizedFields)) return false;
      if (!normalizedSearch) return true;
      return [item.title, item.matterName, item.ownerName, item.meeting?.location, item.meeting?.notes]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase('pt-BR').includes(normalizedSearch));
    });
  }, [allItems, natureFilter, ownerFilter, personalizedFields, personalizedFilter, search, showFollowUps, showLegalEvents, showMeetings, statusFilter]);

  const todayItems = useMemo(() => filteredItems.filter((item) => isToday(item.start) && item.status !== 'cancelled'), [filteredItems]);
  const allTodayItems = useMemo(() => allItems.filter((item) => isToday(item.start) && item.status !== 'cancelled'), [allItems]);
  const selectedDayItems = useMemo(() => filteredItems.filter((item) => isSameDay(item.start, selectedDay) && item.status !== 'cancelled'), [filteredItems, selectedDay]);
  const now = new Date();
  const weekStart = startOfWeek(now, { weekStartsOn: WEEK_STARTS_ON });
  const weekEnd = endOfWeek(now, { weekStartsOn: WEEK_STARTS_ON });
  const previousWeekStart = subWeeks(weekStart, 1);
  const previousWeekEnd = endOfWeek(previousWeekStart, { weekStartsOn: WEEK_STARTS_ON });
  const previousComparableDay = subWeeks(now, 1);
  const previousDayItems = allItems.filter((item) => isSameDay(item.start, previousComparableDay) && item.status !== 'cancelled').length;
  const meetings = useMemo(() => meetingsQuery.data ?? [], [meetingsQuery.data]);
  const meetingsThisWeek = meetings.filter((meeting) => {
    const date = parseISO(meeting.startsAt);
    return !isBefore(date, weekStart) && !isAfter(date, weekEnd) && meeting.status !== 'cancelled';
  }).length;
  const meetingsPreviousWeek = meetings.filter((meeting) => {
    const date = parseISO(meeting.startsAt);
    return inDateRange(date, previousWeekStart, previousWeekEnd) && meeting.status !== 'cancelled';
  }).length;
  const currentBookingWindowStart = startOfDay(subDays(now, 6));
  const previousBookingWindowStart = startOfDay(subDays(now, 13));
  const previousBookingWindowEnd = endOfDay(subDays(now, 7));
  const bookingsLastSevenDays = meetings.filter((meeting) => meeting.createdAt && inDateRange(parseISO(meeting.createdAt), currentBookingWindowStart, now)).length;
  const bookingsPreviousSevenDays = meetings.filter((meeting) => meeting.createdAt && inDateRange(parseISO(meeting.createdAt), previousBookingWindowStart, previousBookingWindowEnd)).length;
  const currentThirtyDayStart = startOfDay(subDays(now, 29));
  const previousThirtyDayStart = startOfDay(subDays(now, 59));
  const previousThirtyDayEnd = endOfDay(subDays(now, 30));
  const currentThirtyDayMeetings = meetings.filter((meeting) => inDateRange(parseISO(meeting.startsAt), currentThirtyDayStart, now));
  const previousThirtyDayMeetings = meetings.filter((meeting) => inDateRange(parseISO(meeting.startsAt), previousThirtyDayStart, previousThirtyDayEnd));
  const currentEffectiveMeetings = currentThirtyDayMeetings.filter((meeting) => meeting.status !== 'cancelled');
  const previousEffectiveMeetings = previousThirtyDayMeetings.filter((meeting) => meeting.status !== 'cancelled');
  const currentCompletionRate = percentage(currentEffectiveMeetings.filter((meeting) => meeting.status === 'completed').length, currentEffectiveMeetings.length);
  const previousCompletionRate = percentage(previousEffectiveMeetings.filter((meeting) => meeting.status === 'completed').length, previousEffectiveMeetings.length);
  const currentCancellationRate = percentage(currentThirtyDayMeetings.filter((meeting) => meeting.status === 'cancelled').length, currentThirtyDayMeetings.length);
  const previousCancellationRate = percentage(previousThirtyDayMeetings.filter((meeting) => meeting.status === 'cancelled').length, previousThirtyDayMeetings.length);
  const currentNoShowRate = percentage(currentEffectiveMeetings.filter((meeting) => meeting.status === 'no_show').length, currentEffectiveMeetings.length);
  const previousNoShowRate = percentage(previousEffectiveMeetings.filter((meeting) => meeting.status === 'no_show').length, previousEffectiveMeetings.length);
  const pendingFollowUps = (followUpsQuery.data ?? []).filter((followUp) => !['completed', 'suppressed', 'cancelled'].includes(followUp.status)).length;
  const mappedGoogleEvents = meetings.filter((meeting) => Boolean(meeting.googleCalendarEventId)).length;
  const googleProviderState = runtimeQuery.data?.providers.googleWorkspace ?? 'offline';
  const googleConnected = googleProviderState === 'connected';
  const copilotContext = useMemo(() => ({
    selected_date: format(selectedDay, 'yyyy-MM-dd'),
    view,
    realtime_status: realtimeStatus,
    google_connected: googleConnected,
    counts: {
      meetings: meetings.length,
      filtered_items: filteredItems.length,
      today_items: allTodayItems.length,
      pending_follow_ups: pendingFollowUps,
      daily_total: dailyBriefingQuery.data?.total ?? 0,
      orphan_meetings: dailyBriefingQuery.data?.orphanCount ?? 0,
      missing_room: dailyBriefingQuery.data?.missingRoomCount ?? 0,
    },
  }), [
    allTodayItems.length,
    dailyBriefingQuery.data?.missingRoomCount,
    dailyBriefingQuery.data?.orphanCount,
    dailyBriefingQuery.data?.total,
    filteredItems.length,
    googleConnected,
    meetings.length,
    pendingFollowUps,
    realtimeStatus,
    selectedDay,
    view,
  ]);
  const agendaMetrics: AgendaMetric[] = [
    { label: 'Compromissos hoje', value: allTodayItems.length, previous: previousDayItems, previousLabel: 'mesmo dia da semana anterior', icon: CalendarClock },
    { label: 'Reuniões na semana', value: meetingsThisWeek, previous: meetingsPreviousWeek, previousLabel: 'semana anterior', icon: CalendarCheck2 },
    { label: 'Novos agendamentos', value: bookingsLastSevenDays, previous: bookingsPreviousSevenDays, previousLabel: '7 dias anteriores', icon: Activity },
    { label: 'Conclusão em 30 dias', value: currentCompletionRate, suffix: '%', previous: previousCompletionRate, previousLabel: '30 dias anteriores', icon: CheckCircle2, mode: 'points' },
    { label: 'Cancelamentos em 30 dias', value: currentCancellationRate, suffix: '%', previous: previousCancellationRate, previousLabel: '30 dias anteriores', icon: CalendarX2, mode: 'points', betterWhenDown: true },
    { label: 'No-show em 30 dias', value: currentNoShowRate, suffix: '%', previous: previousNoShowRate, previousLabel: '30 dias anteriores', icon: UserRound, mode: 'points', betterWhenDown: true },
  ];

  const upcomingSevenDayMeetings = meetings.filter((meeting) => {
    const start = parseISO(meeting.startsAt);
    return meeting.status === 'scheduled' && inDateRange(start, now, endOfDay(addDays(now, 7)));
  });
  const reservedHoursNextSevenDays = upcomingSevenDayMeetings.reduce((total, meeting) => {
    const start = parseISO(meeting.startsAt);
    const end = meeting.endsAt ? parseISO(meeting.endsAt) : addHours(start, 1);
    return total + Math.max(0, differenceInMinutes(end, start) / 60);
  }, 0);
  const busiestDayEntry = Object.entries(upcomingSevenDayMeetings.reduce<Record<string, number>>((days, meeting) => {
    const key = format(parseISO(meeting.startsAt), 'yyyy-MM-dd');
    days[key] = (days[key] ?? 0) + 1;
    return days;
  }, {})).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0];
  const busiestDayLabel = busiestDayEntry
    ? format(parseISO(`${busiestDayEntry[0]}T12:00:00`), "EEEE, d 'de' MMMM", { locale: ptBR })
    : 'nenhum dia sobrecarregado';

  const scopedListItems = useMemo(() => {
    const referenceTime = Date.now();
    const result = filteredItems.filter((item) => {
      const upcoming = item.start.getTime() >= referenceTime && !['cancelled', 'completed', 'no_show'].includes(item.status);
      if (listScope === 'upcoming') return upcoming;
      if (listScope === 'history') return !upcoming;
      return true;
    });
    return result.sort((left, right) => listScope === 'upcoming'
      ? left.start.getTime() - right.start.getTime()
      : right.start.getTime() - left.start.getTime());
  }, [filteredItems, listScope]);
  const visibleListItems = scopedListItems.slice(0, visibleListCount);
  const meetingsById = useMemo(() => new Map(meetings.map((meeting) => [meeting.id, meeting])), [meetings]);
  const bookingsLastDay = meetings.filter((meeting) => meeting.createdAt && inDateRange(parseISO(meeting.createdAt), subDays(now, 1), now)).length;
  const recentBookings = [...meetings]
    .filter((meeting) => meeting.createdAt)
    .sort((left, right) => parseISO(right.createdAt!).getTime() - parseISO(left.createdAt!).getTime())
    .slice(0, 4);

  useEffect(() => {
    setVisibleListCount(20);
  }, [listScope, ownerFilter, search, showFollowUps, showMeetings, statusFilter]);

  useEffect(() => {
    const requestedMeetingId = searchParams.get('meeting');
    if (!requestedMeetingId || requestedMeetingId === handledMeetingId || !meetingsQuery.data) return;
    const meeting = meetingsQuery.data.find((item) => item.id === requestedMeetingId);
    if (!meeting) return;
    const date = parseISO(meeting.startsAt);
    setAnchor(date);
    setSelectedDay(date);
    setDraft(meetingDraft(meeting));
    setHandledMeetingId(requestedMeetingId);
  }, [handledMeetingId, meetingsQuery.data, searchParams]);

  useEffect(() => {
    const requestedMatterId = searchParams.get('matter');
    if (!requestedMatterId || searchParams.get('action') !== 'new' || requestedMatterId === handledCreateContext || !funnelQuery.data) return;
    const matter = funnelQuery.data.cards.find((item) => item.id === requestedMatterId);
    setHandledCreateContext(requestedMatterId);
    if (!matter) {
      toast({ variant: 'destructive', title: 'Card não encontrado', description: 'O agendamento não foi criado nem vinculado a outro cliente.' });
      return;
    }
    const nextDraft = emptyDraft(new Date());
    setDraft({
      ...nextDraft,
      matterId: matter.id,
      title: `Reunião online: ${matter.name}`,
    });
    setSelectedDay(new Date(nextDraft.startsAt));
    setAnchor(new Date(nextDraft.startsAt));
  }, [funnelQuery.data, handledCreateContext, searchParams, toast]);

  const openItem = (item: CalendarItem) => {
    setSelectedDay(item.start);
    setAnchor(item.start);
    if (item.meeting) setDraft(meetingDraft(item.meeting));
    if (item.followUp) setFollowUpDetail(item);
    if (item.taskId) setSelectedTaskId(item.taskId);
  };

  const openCreate = useCallback((day: Date, hour?: number) => {
    setSelectedDay(day);
    const nextDraft = emptyDraft(day, hour);
    const requestedMatterId = searchParams.get('matter');
    const requestedMatter = requestedMatterId ? mattersById.get(requestedMatterId) : undefined;
    setDraft(requestedMatter ? {
      ...nextDraft,
      matterId: requestedMatter.id,
      title: `Reunião online: ${requestedMatter.name}`,
    } : nextDraft);
  }, [mattersById, searchParams]);

  useEffect(() => {
    if (searchParams.get('action') !== 'new' || !canEditAgenda) return;
    const requestedMatterId = searchParams.get('matter');
    if (requestedMatterId && !funnelQuery.data) return;
    openCreate(selectedDay);
    const next = new URLSearchParams(searchParams);
    next.delete('action');
    setSearchParams(next, { replace: true });
  }, [canEditAgenda, funnelQuery.data, openCreate, searchParams, selectedDay, setSearchParams]);

  const applyCopilotActions = async (actions: KlmOperationalCopilotAction[]) => {
    for (const action of actions) {
      const actionType = typeof action.payload.type === 'string' ? action.payload.type : '';
      if (actionType === 'agenda.open_create') {
        if (!canEditAgenda) throw new Error('Seu perfil não pode preparar reuniões.');
        const requestedDate = typeof action.payload.date === 'string'
          ? parseISO(`${action.payload.date}T12:00:00`)
          : selectedDay;
        const baseDay = Number.isNaN(requestedDate.getTime()) ? selectedDay : requestedDate;
        const requestedHour = Number(action.payload.hour);
        const requestedMinute = Number(action.payload.minute);
        const hour = Number.isInteger(requestedHour) && requestedHour >= 0 && requestedHour <= 23
          ? requestedHour
          : undefined;
        const minute = Number.isInteger(requestedMinute) && requestedMinute >= 0 && requestedMinute <= 59
          ? requestedMinute
          : 0;
        openCreate(baseDay, hour);
        if (hour !== undefined) {
          const start = setMinutes(setHours(startOfDay(baseDay), hour), minute);
          setDraft((current) => current ? {
            ...current,
            startsAt: dateTimeLocal(start),
            endsAt: dateTimeLocal(addHours(start, 1)),
          } : current);
          setAnchor(start);
          setSelectedDay(start);
        }
        continue;
      }
      if (actionType === 'agenda.open_booking_link') {
        if (!canEditAgenda) throw new Error('Seu perfil não pode preparar links de agendamento.');
        setBookingDialogOpen(true);
        continue;
      }
      if (actionType === 'agenda.open_settings') {
        navigate(contextualPath('/app/agenda/configuracoes'));
        continue;
      }
      if (actionType === 'agenda.refresh') {
        await googleRefreshMutation.mutateAsync();
        continue;
      }
      if (actionType === 'agenda.focus_daily_briefing') {
        document.getElementById('agenda-daily-briefing')
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        continue;
      }
      if (actionType === 'agenda.select_view') {
        const nextView = typeof action.payload.view === 'string'
          && ['month', 'week', 'day'].includes(action.payload.view)
          ? action.payload.view as CalendarView
          : 'month';
        setView(nextView);
      }
    }
    return 'A proposta foi aplicada à agenda. Nenhuma reunião foi salva nem sincronizada externamente.';
  };

  const navigatePeriod = (direction: -1 | 1) => {
    const next = view === 'month'
      ? (direction === 1 ? addMonths(anchor, 1) : subMonths(anchor, 1))
      : view === 'week'
        ? (direction === 1 ? addWeeks(anchor, 1) : subWeeks(anchor, 1))
        : addDays(anchor, direction);
    setAnchor(next);
    setSelectedDay(next);
  };

  const submitDraft = () => {
    if (!draft?.title.trim() || !draft.startsAt || !draft.endsAt) return;
    if (meetingConflict?.kind === 'blocking') {
      toast({
        variant: 'destructive',
        title: 'Conflito bloqueante',
        description: 'Escolha outro organizador ou horário antes de salvar.',
      });
      return;
    }
    if (
      meetingConflict?.kind === 'warning'
      && (
        !draft.confirmParticipantConflicts
        || draft.conflictReason.trim().length < MIN_CONFLICT_REASON_LENGTH
        || draft.conflictReason.length > MAX_CONFLICT_REASON_LENGTH
      )
    ) {
      toast({
        variant: 'destructive',
        title: 'Confirmação incompleta',
        description: `Confirme a sobreposição e informe um motivo entre ${MIN_CONFLICT_REASON_LENGTH} e ${MAX_CONFLICT_REASON_LENGTH} caracteres.`,
      });
      return;
    }
    const startsAt = new Date(draft.startsAt);
    const endsAt = new Date(draft.endsAt);
    if (!isAfter(endsAt, startsAt)) {
      toast({ variant: 'destructive', title: 'Horário inválido', description: 'O término precisa ser posterior ao início.' });
      return;
    }
    saveMutation.mutate(draft);
  };

  const weekDays = eachDayOfInterval({
    start: startOfWeek(anchor, { weekStartsOn: WEEK_STARTS_ON }),
    end: endOfWeek(anchor, { weekStartsOn: WEEK_STARTS_ON }),
  });
  const displayDays = view === 'day' ? [anchor] : weekDays;
  const selectedMatter = draft?.matterId ? mattersById.get(draft.matterId) : undefined;
  const isLoading = meetingsQuery.isLoading || followUpsQuery.isLoading;

  return (
    <AgendaVisibilityContext.Provider value={documentVisible}>
      <div className="mx-auto w-full max-w-[1680px] space-y-4">
      <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-2xs font-semibold uppercase text-primary">Operação / Secretaria</p>
            <KlmPill tone={googleConnected ? 'green' : 'muted'}>
              {googleConnected ? 'Google sincronizado' : 'Google desconectado'}
            </KlmPill>
          </div>
          <h2 className="mt-1 text-2xl font-semibold text-foreground">Agenda</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">Reuniões online, compromissos do time e follow-ups em uma visão única.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="icon" className="h-9 w-9" onClick={() => navigate(contextualPath('/app/agenda/configuracoes'))} aria-label="Configurar agenda" title="Configurar agenda">
            <Settings className="h-4 w-4" />
          </Button>
          <Button variant="outline" className="h-9" onClick={() => setBookingDialogOpen(true)}>
            <Share2 className="mr-2 h-4 w-4" />Criar link
          </Button>
          <Button variant="outline" className="h-9" onClick={() => setGoogleDialogOpen(true)}>
            {googleConnected ? <RefreshCw className="mr-2 h-4 w-4" /> : <CalendarDays className="mr-2 h-4 w-4" />}
            {googleConnected ? 'Sincronizar' : 'Conectar Google'}
          </Button>
          <Button className="h-9 bg-[#031121] text-white hover:bg-[#031121]" onClick={() => openCreate(selectedDay)}>
            <Plus className="mr-2 h-4 w-4" />
            Nova reunião
          </Button>
        </div>
      </header>

      <NativeModuleBar
        domain="crm"
        surface="Agenda e trabalho"
        capabilityIds={['tasks-calendar', 'task-boards-pipelines', 'notifications-sse']}
        returnTo={contextualPath('/app/agenda')}
      />

      <NativeCRMOperationsPanel view="work" />

      {searchParams.get('meeting') && (
        <KlmPanel className="flex items-center justify-between gap-3 border-primary/35 bg-primary/10 p-3">
          <div className="min-w-0">
            <p className="text-2xs font-semibold text-foreground">Reunião aberta a partir de Conversas</p>
            <p className="mt-1 truncate text-2xs text-muted-foreground">O contexto do atendimento foi preservado e o agendamento correspondente foi aberto para revisão.</p>
          </div>
          <KlmPill tone="gold" className="shrink-0">Contexto preservado</KlmPill>
        </KlmPanel>
      )}

      {searchParams.get('matter') && searchParams.get('action') === 'new' && (
        <KlmPanel className="flex items-center justify-between gap-3 border-primary/35 bg-primary/10 p-3">
          <div className="min-w-0">
            <p className="text-2xs font-semibold text-foreground">Nova reunião vinculada ao atendimento</p>
            <p className="mt-1 truncate text-2xs text-muted-foreground">O card selecionado em Conversas foi preservado no formulário de agendamento.</p>
          </div>
          <KlmPill tone="gold" className="shrink-0">Card vinculado</KlmPill>
        </KlmPanel>
      )}

      <section className="grid grid-cols-2 gap-2 lg:grid-cols-6" aria-label="Indicadores comparativos da agenda">
        {agendaMetrics.map((metric) => {
          const trend = metricTrend(metric);
          const TrendIcon = trend.direction > 0 ? ArrowUpRight : trend.direction < 0 ? ArrowDownRight : Minus;
          return (
            <article key={metric.label} className="min-h-[108px] rounded-lg border border-border bg-card px-3 py-3 shadow-[var(--shadow-card)] sm:px-4">
              <div className="flex items-start justify-between gap-2">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary text-foreground">
                  <metric.icon className="h-4 w-4" />
                </span>
                <span className={cn(
                  'inline-flex h-6 items-center gap-1 rounded px-1.5 text-2xs font-semibold',
                  trend.direction === 0 ? 'bg-secondary text-muted-foreground' : trend.favorable ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400' : 'bg-red-500/10 text-red-700 dark:text-red-400',
                )}>
                  <TrendIcon className="h-3 w-3" />{trend.label}
                </span>
              </div>
              <p className="mt-2 text-xs font-semibold text-muted-foreground">{metric.label}</p>
              <div className="mt-0.5 flex items-baseline gap-1">
                <strong className="text-2xl font-bold tabular-nums text-foreground">{metric.value}</strong>
                {metric.suffix && <span className="text-xs font-semibold text-foreground">{metric.suffix}</span>}
              </div>
              <p className="mt-1 truncate text-2xs text-muted-foreground" title={`Período anterior: ${metric.previous}${metric.suffix ?? ''} · ${metric.previousLabel}`}>
                Anterior: {metric.previous}{metric.suffix ?? ''} · {metric.previousLabel}
              </p>
            </article>
          );
        })}
      </section>

      <KlmPanel className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/12 text-primary"><CalendarDays className="h-4 w-4" /></span>
          <div className="min-w-0">
            <p className="text-2xs font-semibold text-foreground">Leitura dos próximos 7 dias</p>
            <p className="mt-0.5 text-2xs leading-4 text-muted-foreground">
              {upcomingSevenDayMeetings.length} reuniões · {reservedHoursNextSevenDays.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} horas reservadas · maior concentração: {busiestDayLabel}.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-2xs text-muted-foreground">
          <KlmPill tone={pendingFollowUps > 0 ? 'gold' : 'green'}>{pendingFollowUps} follow-ups abertos</KlmPill>
          <span>{googleConnected ? `${mappedGoogleEvents} no Google` : 'Agenda local'}</span>
        </div>
      </KlmPanel>

      <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_292px]">
        <KlmPanel className="min-w-0 overflow-hidden">
          <div className="flex flex-col gap-3 border-b border-border p-3 lg:flex-row lg:items-center lg:justify-between lg:p-4">
            <div className="flex min-w-0 items-center gap-2">
              <Button variant="outline" size="sm" className="h-8 px-3 text-xs" onClick={() => { const today = new Date(); setAnchor(today); setSelectedDay(today); }}>
                Hoje
              </Button>
              <div className="flex items-center rounded-md border border-border bg-card">
                <Button variant="ghost" size="icon" className="h-8 w-8 rounded-r-none" onClick={() => navigatePeriod(-1)} aria-label="Período anterior">
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon" className="h-8 w-8 rounded-l-none" onClick={() => navigatePeriod(1)} aria-label="Próximo período">
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
              <h2 className="min-w-0 truncate text-sm font-semibold text-foreground sm:text-base">{formatRangeTitle(anchor, view)}</h2>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex h-8 items-center rounded-md border border-border bg-secondary/50 p-0.5" aria-label="Visão da agenda: comercial ou jurídico">
                {([
                  ['todos', 'Tudo'],
                  ['comercial', 'Comercial'],
                  ['juridico', 'Jurídico'],
                ] as const).map(([value, label]) => (
                  <button
                    type="button"
                    key={value}
                    onClick={() => setNatureFilter(value)}
                    aria-pressed={natureFilter === value}
                    className={cn('h-7 rounded px-2.5 text-2xs font-semibold transition', natureFilter === value ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="relative min-w-[180px] flex-1 lg:w-[230px] lg:flex-none">
                <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar compromisso" className="h-8 pl-8 text-xs" />
              </div>
              <Button variant={filtersOpen ? 'secondary' : 'outline'} size="icon" className="h-8 w-8" onClick={() => setFiltersOpen((current) => !current)} aria-label="Filtros da agenda">
                <Filter className="h-4 w-4" />
              </Button>
              <PersonalizedFilterBuilder scope="agenda" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} compact />
              <div className="flex h-8 items-center rounded-md border border-border bg-secondary/50 p-0.5" aria-label="Visualização da agenda">
                {(['month', 'week', 'day'] as const).map((option) => (
                  <button
                    type="button"
                    key={option}
                    onClick={() => setView(option)}
                    aria-pressed={view === option}
                    className={cn('h-7 rounded px-2.5 text-2xs font-semibold transition', view === option ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}
                  >
                    {option === 'month' ? 'Mês' : option === 'week' ? 'Semana' : 'Dia'}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {filtersOpen && (
            <div className="flex flex-col gap-3 border-b border-border bg-secondary/35 px-4 py-3 lg:flex-row lg:items-center">
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2 text-xs font-medium text-foreground">
                  <Checkbox checked={showMeetings} onCheckedChange={(checked) => setShowMeetings(checked === true)} />
                  Reuniões
                </label>
                <label className="flex items-center gap-2 text-xs font-medium text-foreground">
                  <Checkbox checked={showFollowUps} onCheckedChange={(checked) => setShowFollowUps(checked === true)} />
                  Follow-ups
                </label>
                <label className="flex items-center gap-2 text-xs font-medium text-foreground">
                  <Checkbox checked={showLegalEvents} onCheckedChange={(checked) => setShowLegalEvents(checked === true)} />
                  Prazos fatais confirmados (jurídico)
                </label>
              </div>
              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="h-8 w-full text-xs lg:w-[160px]" aria-label="Filtrar por status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todos os status</SelectItem>
                  <SelectItem value="scheduled">Agendadas</SelectItem>
                  <SelectItem value="completed">Concluídas</SelectItem>
                  <SelectItem value="cancelled">Canceladas</SelectItem>
                  <SelectItem value="no_show">Não compareceu</SelectItem>
                  <SelectItem value="pending">Follow-up pendente</SelectItem>
                </SelectContent>
              </Select>
              <Select value={ownerFilter} onValueChange={setOwnerFilter}>
                <SelectTrigger className="h-8 w-full text-xs lg:w-[190px]" aria-label="Filtrar por responsável"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todos os responsáveis</SelectItem>
                  {(teamQuery.data ?? []).filter((member) => member.isActive).map((member) => <SelectItem key={member.id} value={member.id}>{member.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <span className="text-2xs text-muted-foreground lg:ml-auto">{filteredItems.length} itens visíveis</span>
            </div>
          )}

          {isLoading ? (
            <div className="min-h-[520px] space-y-2 p-4">
              {Array.from({ length: 7 }).map((_, index) => <Skeleton key={index} className="h-14 w-full rounded-md" />)}
            </div>
          ) : meetingsQuery.isError ? (
            <div className="flex min-h-[420px] flex-col items-center justify-center px-6 text-center">
              <CircleAlert className="h-7 w-7 text-red-600" />
              <p className="mt-3 text-sm font-semibold">Não foi possível carregar a agenda</p>
              <p className="mt-1 text-xs text-muted-foreground">O calendário não inventa compromissos quando o backend está indisponível.</p>
              <Button variant="outline" className="mt-4" onClick={() => meetingsQuery.refetch()}><RefreshCw className="mr-2 h-4 w-4" />Tentar novamente</Button>
            </div>
          ) : view === 'month' ? (
            <MonthGrid
              anchor={anchor}
              selectedDay={selectedDay}
              items={filteredItems}
              onSelectDay={(day) => { setSelectedDay(day); setAnchor(day); }}
              onOpen={openItem}
            />
          ) : (
            <DayAgendaGrid days={displayDays} items={filteredItems} onOpen={openItem} onCreate={openCreate} />
          )}

          {view === 'month' && (
            <div className="border-t border-border bg-secondary/30 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-semibold text-foreground">{format(selectedDay, "d 'de' MMMM", { locale: ptBR })}</p>
                <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => { setAnchor(selectedDay); setView('day'); }}>Ver dia</Button>
              </div>
              {selectedDayItems.length === 0 ? (
                <button type="button" onClick={() => openCreate(selectedDay)} className="mt-2 flex w-full items-center justify-center rounded-md border border-dashed border-border py-3 text-2xs font-medium text-muted-foreground hover:border-primary hover:text-foreground">
                  <Plus className="mr-1.5 h-3.5 w-3.5" />Adicionar compromisso
                </button>
              ) : (
                <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {selectedDayItems.slice(0, 6).map((item) => <AgendaItemRow key={`${item.kind}-${item.id}`} item={item} onClick={() => openItem(item)} />)}
                </div>
              )}
            </div>
          )}
        </KlmPanel>

        <aside className="grid gap-4 md:grid-cols-2 xl:block xl:space-y-4">
          <KlmPanel id="agenda-daily-briefing" className="scroll-mt-24 overflow-hidden">
            <div className="flex items-start justify-between gap-3 border-b border-border p-4">
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-xs font-semibold text-foreground">Booking em tempo real</p>
                  <span className={cn('h-2 w-2 rounded-full', realtimeStatus === 'connected' ? 'bg-emerald-500 shadow-[0_0_0_4px_rgba(16,185,129,0.12)]' : 'animate-pulse bg-primary')} />
                </div>
                <p className="mt-0.5 text-2xs text-muted-foreground">{bookingsLastDay} recebidos nas últimas 24h</p>
              </div>
              <KlmPill tone={realtimeStatus === 'connected' ? 'green' : 'gold'}>
                {realtimeStatus === 'connected' ? 'Ao vivo' : realtimeStatus === 'offline' ? 'Offline' : 'Reconectando'}
              </KlmPill>
            </div>
            <div className="border-b border-border bg-secondary/35 px-4 py-2">
              <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><Radio className="h-3 w-3" />{realtimeStatus === 'connected' ? 'Eventos chegam sem recarregar' : 'Fallback automático a cada 15s'}</span>
                <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => meetingsQuery.refetch()} aria-label="Atualizar booking agora">
                  <RefreshCw className={cn('h-3 w-3', meetingsQuery.isFetching && 'animate-spin')} />
                </Button>
              </div>
            </div>
            <div className="max-h-[240px] overflow-y-auto">
              {realtimeEvents.length > 0 ? realtimeEvents.slice(0, 5).map((entry) => {
                const meeting = entry.meetingId ? meetingsById.get(entry.meetingId) : undefined;
                const label = entry.event === 'MEETING_CREATED' ? 'Novo agendamento' : entry.event === 'MEETING_UPDATED' ? 'Agendamento alterado' : 'Agendamento removido';
                return (
                  <button
                    type="button"
                    key={entry.id}
                    onClick={() => meeting && setDraft(meetingDraft(meeting))}
                    disabled={!meeting}
                    className="flex w-full items-start gap-3 border-t border-border px-4 py-3 text-left first:border-t-0 enabled:hover:bg-secondary/60 disabled:cursor-default"
                  >
                    <span className={cn('mt-1.5 h-2 w-2 shrink-0 rounded-full', entry.event === 'MEETING_CREATED' ? 'bg-emerald-500' : entry.event === 'MEETING_UPDATED' ? 'bg-primary' : 'bg-muted-foreground')} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-2xs font-semibold text-foreground">{label}</span>
                      <span className="mt-0.5 block truncate text-2xs text-muted-foreground">{(meeting?.title ?? entry.title)?.replace(/^Reunião online:\s*/i, '') ?? 'Registro retirado da agenda'}</span>
                    </span>
                    <time className="shrink-0 text-2xs text-muted-foreground">{format(entry.receivedAt, 'HH:mm:ss')}</time>
                  </button>
                );
              }) : recentBookings.length > 0 ? recentBookings.map((meeting) => (
                <button type="button" key={meeting.id} onClick={() => setDraft(meetingDraft(meeting))} className="flex w-full items-start gap-3 border-t border-border px-4 py-3 text-left first:border-t-0 hover:bg-secondary/60">
                  <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[#031121] dark:bg-blue-300" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-2xs font-semibold text-foreground">{meeting.title.replace(/^Reunião online:\s*/i, '')}</span>
                    <span className="mt-0.5 block text-2xs text-muted-foreground">Agendado para {format(parseISO(meeting.startsAt), 'dd/MM · HH:mm')}</span>
                  </span>
                  <time className="shrink-0 text-2xs text-muted-foreground">{meeting.createdAt ? format(parseISO(meeting.createdAt), 'HH:mm') : ''}</time>
                </button>
              )) : (
                <p className="px-4 py-5 text-center text-2xs text-muted-foreground">Nenhum booking recebido neste histórico local.</p>
              )}
            </div>
            <div className="border-t border-border px-4 py-2 text-2xs text-muted-foreground">
              Última leitura: {lastRefreshAt ? format(lastRefreshAt, 'dd/MM/yyyy · HH:mm:ss') : 'aguardando dados'}
            </div>
          </KlmPanel>

          <KlmPanel className="overflow-hidden">
            <div className="flex items-start justify-between gap-3 border-b border-border p-4">
              <div>
                <div className="flex items-center gap-2"><BrainCircuit className="h-4 w-4 text-[#0057d8]" /><p className="text-xs font-semibold text-foreground">Resumo do dia</p></div>
                <p className="mt-1 text-2xs capitalize text-muted-foreground">{format(selectedDay, "EEEE, d 'de' MMMM", { locale: ptBR })}</p>
              </div>
              <KlmPill tone="gold">Hermes · local</KlmPill>
            </div>
            {dailyBriefingQuery.isLoading ? <div className="flex items-center px-4 py-6 text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Organizando as reuniões...</div> : dailyBriefingQuery.data ? (
              <div className="space-y-4 p-4">
                <p className="text-2xs leading-4 text-muted-foreground">{dailyBriefingQuery.data.summary}</p>
                <div className="grid grid-cols-3 gap-px overflow-hidden border border-border bg-border text-center">
                  {[['Reuniões', dailyBriefingQuery.data.total], ['Sem card', dailyBriefingQuery.data.orphanCount], ['Sem sala', dailyBriefingQuery.data.missingRoomCount]].map(([label, value]) => <div key={label as string} className="bg-card px-2 py-3"><strong className="block text-base font-medium">{value}</strong><span className="mt-0.5 block text-2xs text-muted-foreground">{label}</span></div>)}
                </div>
                {dailyBriefingQuery.data.priorities.length > 0 && <div className="space-y-1.5 border-t border-border pt-3">{dailyBriefingQuery.data.priorities.slice(0, 4).map((priority) => <p key={priority} className="flex items-start gap-1.5 text-2xs leading-4"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-[#a53e3c]" />{priority}</p>)}</div>}
                {dailyBriefingQuery.data.items[0] && <button type="button" onClick={() => { const meeting = meetingsById.get(dailyBriefingQuery.data!.items[0].meetingId); if (meeting) setDraft(meetingDraft(meeting)); }} className="flex w-full items-center justify-between gap-3 border-t border-border pt-3 text-left"><span className="min-w-0"><span className="block truncate text-2xs font-semibold">Próxima: {dailyBriefingQuery.data.items[0].clientName}</span><span className="mt-0.5 block text-2xs text-muted-foreground">{format(parseISO(dailyBriefingQuery.data.items[0].startsAt), 'HH:mm')} · {dailyBriefingQuery.data.items[0].readiness}</span></span><ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /></button>}
              </div>
            ) : <p className="px-4 py-6 text-2xs text-muted-foreground">Resumo indisponível.</p>}
          </KlmPanel>

          <KlmPanel className="overflow-hidden">
            <div className="flex items-start justify-between gap-3 border-b border-border p-4">
              <div>
                <p className="text-xs font-semibold text-foreground">Google Agenda</p>
                <p className="mt-0.5 text-2xs text-muted-foreground">Sincronização bidirecional</p>
              </div>
              <KlmPill tone={googleConnected ? 'green' : 'muted'}>{googleConnected ? 'Conectado' : 'Offline'}</KlmPill>
            </div>
            <div className="space-y-3 p-4">
              <div className="flex items-start gap-3">
                <span className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-md', googleConnected ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400' : 'bg-secondary text-muted-foreground')}>
                  {googleConnected ? <CalendarCheck2 className="h-4 w-4" /> : <CloudOff className="h-4 w-4" />}
                </span>
                <p className="text-2xs leading-4 text-muted-foreground">
                  {googleConnected ? `${mappedGoogleEvents} eventos vinculados ao calendário do workspace.` : 'A agenda local está funcional. Nenhum evento será enviado ao Google antes da sua aprovação e das credenciais OAuth.'}
                </p>
              </div>
              <Button variant="outline" className="h-8 w-full text-2xs" onClick={() => setGoogleDialogOpen(true)}>
                {googleConnected ? <RefreshCw className="mr-2 h-3.5 w-3.5" /> : <Link2 className="mr-2 h-3.5 w-3.5" />}
                {googleConnected ? 'Atualizar dados locais' : 'Preparar conexão'}
              </Button>
            </div>
          </KlmPanel>

          <KlmPanel className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <p className="text-xs font-semibold text-foreground">Hoje</p>
                <p className="text-2xs text-muted-foreground">{todayItems.length} compromissos</p>
              </div>
              <CalendarClock className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="max-h-[340px] overflow-y-auto">
              {todayItems.length ? todayItems.map((item) => <AgendaItemRow key={`${item.kind}-${item.id}`} item={item} onClick={() => openItem(item)} />) : (
                <button type="button" onClick={() => openCreate(new Date())} className="w-full px-4 py-6 text-center text-2xs text-muted-foreground hover:bg-secondary/50">Agenda livre hoje. Adicionar reunião.</button>
              )}
            </div>
          </KlmPanel>

          <KlmPanel className="overflow-hidden md:col-span-2 xl:col-span-1">
            <div className="border-b border-border px-4 py-3">
              <p className="text-xs font-semibold text-foreground">Legenda</p>
            </div>
            <div className="grid grid-cols-2 gap-3 p-4 text-2xs text-muted-foreground xl:grid-cols-1">
              <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-emerald-600" />Concluída</span>
              <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-muted-foreground" />Cancelada</span>
              <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-red-600" />Não compareceu</span>
            </div>
            <div className="border-t border-border px-4 py-3">
              <p className="text-xs font-semibold text-foreground">Tipos de evento</p>
            </div>
            <div className="grid grid-cols-2 gap-3 p-4 text-2xs text-muted-foreground xl:grid-cols-1">
              {EVENT_TYPE_ORDER.map((type) => (
                <span key={type} className="flex items-center gap-2"><span className={cn('h-2.5 w-2.5 rounded-full', EVENT_TYPE_META[type].dotClass)} />{EVENT_TYPE_META[type].label}</span>
              ))}
              <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-primary" />Follow-up</span>
            </div>
          </KlmPanel>
        </aside>
      </div>

      <KlmPanel className="overflow-hidden" aria-label="Lista completa da agenda">
        <div className="flex flex-col gap-3 border-b border-border p-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary text-foreground"><ListFilter className="h-4 w-4" /></span>
            <div>
              <h2 className="text-sm font-semibold text-foreground">Lista completa</h2>
              <p className="mt-0.5 text-2xs text-muted-foreground">Todos os compromissos filtrados, sem o limite visual do calendário.</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex h-8 items-center rounded-md border border-border bg-secondary/50 p-0.5" aria-label="Período da lista">
              {([
                ['upcoming', 'Próximos'],
                ['history', 'Histórico'],
                ['all', 'Todos'],
              ] as const).map(([scope, label]) => (
                <button
                  type="button"
                  key={scope}
                  onClick={() => setListScope(scope)}
                  aria-pressed={listScope === scope}
                  className={cn('h-7 rounded px-2.5 text-2xs font-semibold transition', listScope === scope ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="text-2xs text-muted-foreground">{visibleListItems.length} de {scopedListItems.length}</span>
          </div>
        </div>

        <div className="hidden grid-cols-[150px_minmax(220px,1.3fr)_minmax(130px,.8fr)_minmax(120px,.7fr)_120px_100px] gap-3 border-b border-border bg-secondary/35 px-4 py-2 text-2xs font-semibold uppercase text-muted-foreground md:grid">
          <span>Data e hora</span><span>Cliente / compromisso</span><span>Responsável</span><span>Canal ou local</span><span>Status</span><span>Sincronização</span>
        </div>
        <div className="divide-y divide-border">
          {visibleListItems.length > 0 ? visibleListItems.map((item) => (
            <MeetingContextHover key={`list-${item.kind}-${item.id}`} item={item}>
              <button
                type="button"
                onClick={() => openItem(item)}
                className="flex w-full flex-col gap-2 px-4 py-3 text-left transition hover:bg-secondary/45 md:grid md:grid-cols-[150px_minmax(220px,1.3fr)_minmax(130px,.8fr)_minmax(120px,.7fr)_120px_100px] md:items-center md:gap-3"
              >
              <span className="flex items-center justify-between gap-3 md:block">
                <span className="text-2xs font-semibold text-foreground">{format(item.start, 'dd/MM/yyyy')}</span>
                <span className="text-2xs text-muted-foreground md:ml-1">{format(item.start, 'HH:mm')}</span>
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-semibold text-foreground">{item.matterName ?? item.title}</span>
                <span className="mt-0.5 block truncate text-2xs text-muted-foreground">{item.matterName ? item.title : item.kind === 'meeting' ? 'Reunião sem caso vinculado' : item.kind === 'legal_event' ? 'Controladoria processual' : 'Follow-up comercial'}</span>
              </span>
              <span className="hidden truncate text-2xs text-foreground md:block">{item.ownerName ?? (item.kind === 'follow_up' ? 'Fluxo comercial' : 'Não definido')}</span>
              <span className="hidden truncate text-2xs text-muted-foreground md:block">{item.kind === 'meeting' ? item.meeting?.location ?? (item.meeting?.meetingUrl ? 'Online' : 'Não informado') : item.kind === 'legal_event' ? 'Controladoria' : item.kind === 'task_deadline' ? 'Tarefas' : item.followUp?.channel ?? 'Automação'}</span>
              <span className="flex flex-col items-start gap-1">
                {item.eventType && item.eventType !== 'reuniao' && (
                  <span className={cn('inline-flex w-fit items-center rounded border px-1.5 py-0.5 text-2xs font-semibold', EVENT_TYPE_META[item.eventType].badgeClass)}>
                    {EVENT_TYPE_META[item.eventType].label}{item.isInviolable ? ' · inviolável' : ''}
                  </span>
                )}
                <span className={cn(
                  'inline-flex w-fit items-center rounded px-2 py-1 text-2xs font-semibold',
                  item.kind === 'follow_up' ? 'bg-primary/12 text-[#79520a] dark:text-primary' : item.status === 'completed' ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400' : item.status === 'cancelled' ? 'bg-secondary text-muted-foreground' : item.status === 'no_show' ? 'bg-red-500/10 text-red-700 dark:text-red-300' : 'bg-[#031121]/10 text-[#031121] dark:bg-blue-400/15 dark:text-blue-200',
                )}>{itemStatusLabel(item)}</span>
              </span>
              <span className="hidden text-2xs text-muted-foreground md:block">{item.kind === 'meeting' ? item.meeting?.googleCalendarEventId ? 'Google' : 'Somente plataforma' : item.kind === 'legal_event' ? 'Controladoria' : item.kind === 'task_deadline' ? 'Tarefa persistida' : 'Fluxo interno'}</span>
              <span className="flex items-center justify-between gap-3 text-2xs text-muted-foreground md:hidden">
                <span>{item.ownerName ?? (item.kind === 'follow_up' ? 'Fluxo comercial' : 'Sem responsável')}</span>
                <span>{item.kind === 'meeting' ? item.meeting?.location ?? 'Somente plataforma' : item.kind === 'legal_event' ? 'Controladoria' : item.kind === 'task_deadline' ? 'Tarefas' : item.followUp?.channel ?? 'Automação'}</span>
              </span>
              </button>
            </MeetingContextHover>
          )) : (
            <div className="px-6 py-10 text-center">
              <CalendarClock className="mx-auto h-6 w-6 text-muted-foreground" />
              <p className="mt-2 text-xs font-semibold text-foreground">Nenhum item neste período</p>
              <p className="mt-1 text-2xs text-muted-foreground">Altere o período ou os filtros da agenda.</p>
            </div>
          )}
        </div>
        {visibleListItems.length < scopedListItems.length && (
          <div className="border-t border-border bg-secondary/25 p-3 text-center">
            <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => setVisibleListCount((current) => current + 20)}>
              Mostrar mais {Math.min(20, scopedListItems.length - visibleListItems.length)} itens
            </Button>
          </div>
        )}
      </KlmPanel>

      <Dialog open={Boolean(draft)} onOpenChange={(open) => { if (!open && !saveMutation.isPending) setDraft(null); }}>
        <DialogContent className="flex max-h-[92vh] flex-col overflow-hidden sm:max-w-[720px]">
          <DialogHeader>
            <DialogTitle>{draft?.id ? 'Detalhes da reunião' : 'Nova reunião'}</DialogTitle>
            <DialogDescription>{draft?.id ? 'Edite o compromisso e mantenha o histórico operacional atualizado.' : 'A primeira reunião vinculada a um caso deve permanecer online.'}</DialogDescription>
          </DialogHeader>
          {draft?.id && (
            <div className={cn('flex shrink-0 items-center justify-between gap-3 border-y border-border py-3', draft.meetingUrl ? 'text-foreground' : 'text-[#a53e3c]')}>
              <div className="flex min-w-0 items-center gap-3">
                <span className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-md', draft.meetingUrl ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400' : 'bg-red-500/10 text-red-700')}><Video className="h-4 w-4" /></span>
                <div className="min-w-0"><p className="text-xs font-semibold">{draft.meetingUrl ? 'Sala on-line pronta' : 'Sala on-line pendente'}</p><p className="mt-0.5 truncate text-2xs text-muted-foreground">{draft.meetingUrl || 'Cole abaixo o endereço do Google Meet ou Microsoft Teams.'}</p></div>
              </div>
              {draft.meetingUrl && <Button size="sm" className="h-8 shrink-0 bg-[#031121] text-2xs text-white hover:bg-[#031121]" asChild><a href={draft.meetingUrl} target="_blank" rel="noreferrer"><ExternalLink className="mr-1.5 h-3.5 w-3.5" />Entrar</a></Button>}
            </div>
          )}
          {draft && (
            <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto py-1 pr-1 sm:grid-cols-2">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="agenda-title">Título</Label>
                <Input id="agenda-title" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="Ex.: Reunião inicial com cliente" autoFocus />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="agenda-start">Início</Label>
                <Input id="agenda-start" type="datetime-local" value={draft.startsAt} onChange={(event) => {
                  const nextStart = event.target.value;
                  const currentDuration = draft.endsAt ? Math.max(30, differenceInMinutes(new Date(draft.endsAt), new Date(draft.startsAt))) : 60;
                  updateConflictSensitiveDraft((current) => ({
                    ...current,
                    startsAt: nextStart,
                    endsAt: nextStart
                      ? dateTimeLocal(addHours(new Date(nextStart), currentDuration / 60))
                      : current.endsAt,
                  }));
                }} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="agenda-end">Término</Label>
                <Input id="agenda-end" type="datetime-local" value={draft.endsAt} onChange={(event) => updateConflictSensitiveDraft((current) => ({ ...current, endsAt: event.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label>Tipo de evento</Label>
                <Select value={draft.eventType} onValueChange={(value: KlmMeetingEventType) => setDraft({ ...draft, eventType: value, natureza: EVENT_TYPE_DEFAULT_NATURE[value] })}>
                  <SelectTrigger aria-label="Tipo de evento"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {EVENT_TYPE_ORDER.map((type) => <SelectItem key={type} value={type}>{EVENT_TYPE_META[type].label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Natureza</Label>
                <Select value={draft.natureza} onValueChange={(value: KlmMeetingNature) => setDraft({ ...draft, natureza: value })}>
                  <SelectTrigger aria-label="Natureza do evento"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="comercial">Comercial</SelectItem>
                    <SelectItem value="juridico">Jurídico</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {(draft.eventType === 'prazo_interno' || draft.eventType === 'prazo_fatal') && (
                <div className="space-y-2 rounded-md border border-[#c3922f]/35 bg-[#fff8e8] p-3 dark:bg-[#c3922f]/10 sm:col-span-2">
                  <p className="text-2xs font-semibold text-[#6f4a05] dark:text-[#f1c46f]">Contagem do prazo (interno × fatal)</p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="agenda-counting-days">Dias de contagem</Label>
                      <Input id="agenda-counting-days" type="number" min={0} value={draft.countingDays} onChange={(event) => setDraft({ ...draft, countingDays: event.target.value })} placeholder="Ex.: 15" />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Tipo de contagem</Label>
                      <Select value={draft.countingType} onValueChange={(value: 'dias_uteis' | 'dias_corridos') => setDraft({ ...draft, countingType: value })}>
                        <SelectTrigger aria-label="Tipo de contagem do prazo"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="dias_uteis">Dias úteis</SelectItem>
                          <SelectItem value="dias_corridos">Dias corridos</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <p className="text-2xs leading-4 text-[#6f4a05] dark:text-[#f1c46f]">
                    {draft.eventType === 'prazo_interno'
                      ? 'Prazo interno do escritório — data de controle antes do prazo fatal, para revisão e produção da peça.'
                      : 'Prazo fatal — trate como inviolável. Prazos fatais confirmados pela controladoria processual já entram automaticamente pela leitura unificada da agenda.'}
                  </p>
                </div>
              )}
              <div className="space-y-1.5">
                <Label>Caso vinculado</Label>
                <Select value={draft.matterId || '__none__'} onValueChange={(value) => {
                  setTaskSearch('');
                  updateConflictSensitiveDraft((current) => ({
                    ...current,
                    matterId: value === '__none__' ? '' : value,
                    linkedTaskId: '',
                  }));
                }}>
                  <SelectTrigger aria-label="Caso vinculado"><SelectValue placeholder="Sem caso vinculado" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Sem caso vinculado</SelectItem>
                    {(funnelQuery.data?.cards ?? []).map((matter) => <SelectItem key={matter.id} value={matter.id}>{matter.name} · {matter.subject}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Organizador</Label>
                <Select value={draft.responsibleUserId || '__none__'} onValueChange={(value) => {
                  const organizerId = value === '__none__' ? '' : value;
                  updateConflictSensitiveDraft((current) => ({
                    ...current,
                    responsibleUserId: organizerId,
                    participants: withMeetingOrganizer(current.participants, organizerId),
                  }));
                }}>
                  <SelectTrigger aria-label="Organizador do compromisso"><SelectValue placeholder="Sem organizador" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Sem organizador definido</SelectItem>
                    {activeTeamMembers.map((member) => <SelectItem key={member.id} value={member.id}>{member.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <section className="space-y-3 rounded-md border border-border p-3 sm:col-span-2" aria-label="Tarefa vinculada ao compromisso">
                <div>
                  <p className="text-xs font-semibold">Tarefa do mesmo caso</p>
                  <p className="mt-1 text-2xs text-muted-foreground">
                    O vínculo usa tarefas persistidas do caso selecionado. Nenhum identificador técnico precisa ser informado.
                  </p>
                </div>
                {!draft.matterId ? (
                  <p className="text-2xs text-muted-foreground">Selecione um caso para pesquisar suas tarefas.</p>
                ) : (
                  <>
                    <Input
                      value={taskSearch}
                      onChange={(event) => setTaskSearch(event.target.value)}
                      aria-label="Pesquisar tarefa do caso"
                      placeholder="Pesquisar pelo título da tarefa"
                      disabled={matterTasksQuery.isLoading || matterTasksQuery.isError}
                    />
                    {matterTasksQuery.isLoading ? (
                      <p className="flex items-center gap-2 text-2xs text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />Carregando tarefas do caso…
                      </p>
                    ) : matterTasksQuery.isError ? (
                      <p role="alert" className="text-2xs text-destructive">
                        Não foi possível carregar as tarefas. O vínculo atual será preservado até uma nova tentativa.
                      </p>
                    ) : (
                      <Select
                        value={draft.linkedTaskId || '__none__'}
                        onValueChange={(value) => setDraft({
                          ...draft,
                          linkedTaskId: value === '__none__' ? '' : value,
                        })}
                      >
                        <SelectTrigger aria-label="Tarefa vinculada">
                          <SelectValue placeholder="Sem tarefa vinculada" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">Sem tarefa vinculada</SelectItem>
                          {filteredMatterTasks.map((task) => (
                            <SelectItem key={task.id} value={task.id}>
                              {task.title} · {task.status === 'done' ? 'Concluída' : 'Em andamento'}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                    {!matterTasksQuery.isLoading && !matterTasksQuery.isError && matterTasks.length === 0 ? (
                      <p className="text-2xs text-muted-foreground">Este caso ainda não possui tarefas vinculáveis.</p>
                    ) : null}
                    {draft.linkedTaskId ? (
                      <p className="text-2xs text-muted-foreground">
                        {selectedLinkedTask
                          ? `Vínculo selecionado: ${selectedLinkedTask.title}.`
                          : 'A tarefa vinculada não está disponível nesta consulta; o vínculo não será trocado silenciosamente.'}
                      </p>
                    ) : null}
                  </>
                )}
              </section>
              <section className="space-y-3 rounded-md border border-border p-3 sm:col-span-2" aria-label="Participantes internos do compromisso">
                <div>
                  <p className="text-xs font-semibold">Participantes internos</p>
                  <p className="mt-1 text-2xs text-muted-foreground">
                    Um organizador, vários participantes e observadores. Destinatários externos continuam separados.
                  </p>
                </div>
                {teamQuery.isLoading ? (
                  <p className="flex items-center gap-2 text-2xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />Carregando equipe…
                  </p>
                ) : teamQuery.isError ? (
                  <p role="alert" className="text-2xs text-destructive">
                    A equipe está indisponível. Os participantes atuais serão preservados.
                  </p>
                ) : (
                  <>
                    {draft.responsibleUserId ? (
                      <div className="grid gap-2 rounded-md border bg-secondary/25 p-3 sm:grid-cols-[minmax(0,1fr)_170px_170px]">
                        <div className="min-w-0">
                          <p className="truncate text-2xs font-semibold">
                            {teamById.get(draft.responsibleUserId)?.name ?? 'Organizador atual'}
                          </p>
                          <p className="text-2xs text-muted-foreground">Organizador · conflito bloqueante</p>
                        </div>
                        <Select
                          value={draft.participants.find((participant) => participant.userId === draft.responsibleUserId)?.rsvp ?? 'pending'}
                          onValueChange={(value: KlmMeetingParticipantRsvp) => updateMeetingParticipant(
                            draft.responsibleUserId,
                            { rsvp: value },
                          )}
                        >
                          <SelectTrigger aria-label="RSVP do organizador"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="pending">Aguardando RSVP</SelectItem>
                            <SelectItem value="accepted">Confirmado</SelectItem>
                            <SelectItem value="tentative">Talvez</SelectItem>
                            <SelectItem value="declined">Recusou</SelectItem>
                          </SelectContent>
                        </Select>
                        <Select
                          value={String(draft.participants.find((participant) => participant.userId === draft.responsibleUserId)?.reminderMinutesBefore ?? '__none__')}
                          onValueChange={(value) => updateMeetingParticipant(
                            draft.responsibleUserId,
                            { reminderMinutesBefore: value === '__none__' ? null : Number(value) },
                          )}
                        >
                          <SelectTrigger aria-label="Lembrete do organizador"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__none__">Sem lembrete</SelectItem>
                            <SelectItem value="10">10 minutos antes</SelectItem>
                            <SelectItem value="30">30 minutos antes</SelectItem>
                            <SelectItem value="60">1 hora antes</SelectItem>
                            <SelectItem value="1440">1 dia antes</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    ) : (
                      <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-2xs text-amber-800 dark:text-amber-300">
                        Defina o organizador antes de adicionar outros participantes.
                      </p>
                    )}

                    <Input
                      value={participantSearch}
                      onChange={(event) => setParticipantSearch(event.target.value)}
                      aria-label="Pesquisar participante interno"
                      placeholder="Pesquisar pessoa por nome ou e-mail"
                      disabled={!draft.responsibleUserId}
                    />
                    {draft.responsibleUserId && participantCandidates.length > 0 ? (
                      <div className="max-h-36 space-y-1 overflow-y-auto" role="list" aria-label="Pessoas disponíveis para o compromisso">
                        {participantCandidates.slice(0, 20).map((member) => (
                          <div key={member.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2" role="listitem">
                            <div className="min-w-0">
                              <p className="truncate text-2xs font-semibold">{member.name}</p>
                              <p className="truncate text-2xs text-muted-foreground">{member.email}</p>
                            </div>
                            <div className="flex gap-1">
                              <Button type="button" size="sm" variant="outline" className="h-7 text-2xs" onClick={() => addMeetingParticipant(member.id, 'attendee')} aria-label={`Adicionar ${member.name} como participante`}>
                                Participante
                              </Button>
                              <Button type="button" size="sm" variant="ghost" className="h-7 text-2xs" onClick={() => addMeetingParticipant(member.id, 'observer')} aria-label={`Adicionar ${member.name} como observador`}>
                                Observador
                              </Button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : draft.responsibleUserId && participantSearch.trim() ? (
                      <p className="text-2xs text-muted-foreground">Nenhuma pessoa ativa encontrada para esta busca.</p>
                    ) : null}

                    {draft.participants.filter((participant) => participant.role !== 'organizer').map((participant) => {
                      const member = teamById.get(participant.userId);
                      return (
                        <div key={participant.userId} className="grid gap-2 rounded-md border p-3 sm:grid-cols-[minmax(0,1fr)_150px_150px_auto]">
                          <div className="min-w-0">
                            <p className="truncate text-2xs font-semibold">{member?.name ?? 'Participante preservado'}</p>
                            <p className="text-2xs text-muted-foreground">
                              {participant.role === 'observer' ? 'Observador · não bloqueia conflito' : 'Participante · conflito exige confirmação'}
                            </p>
                          </div>
                          <Select
                            value={participant.role}
                            onValueChange={(value: 'attendee' | 'observer') => updateMeetingParticipant(participant.userId, { role: value })}
                          >
                            <SelectTrigger aria-label={`Papel de ${member?.name ?? 'participante'}`}><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="attendee">Participante</SelectItem>
                              <SelectItem value="observer">Observador</SelectItem>
                            </SelectContent>
                          </Select>
                          <Select
                            value={participant.rsvp ?? 'pending'}
                            onValueChange={(value: KlmMeetingParticipantRsvp) => updateMeetingParticipant(participant.userId, { rsvp: value })}
                          >
                            <SelectTrigger aria-label={`RSVP de ${member?.name ?? 'participante'}`}><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="pending">Aguardando RSVP</SelectItem>
                              <SelectItem value="accepted">Confirmado</SelectItem>
                              <SelectItem value="tentative">Talvez</SelectItem>
                              <SelectItem value="declined">Recusou</SelectItem>
                            </SelectContent>
                          </Select>
                          <div className="flex gap-1">
                            <Select
                              value={String(participant.reminderMinutesBefore ?? '__none__')}
                              onValueChange={(value) => updateMeetingParticipant(participant.userId, { reminderMinutesBefore: value === '__none__' ? null : Number(value) })}
                            >
                              <SelectTrigger className="min-w-[135px]" aria-label={`Lembrete de ${member?.name ?? 'participante'}`}><SelectValue /></SelectTrigger>
                              <SelectContent>
                                <SelectItem value="__none__">Sem lembrete</SelectItem>
                                <SelectItem value="10">10 min antes</SelectItem>
                                <SelectItem value="30">30 min antes</SelectItem>
                                <SelectItem value="60">1 hora antes</SelectItem>
                                <SelectItem value="1440">1 dia antes</SelectItem>
                              </SelectContent>
                            </Select>
                            <Button type="button" variant="ghost" size="icon" className="h-9 w-9" aria-label={`Remover ${member?.name ?? 'participante'}`} onClick={() => removeMeetingParticipant(participant.userId)}>
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                  </>
                )}
              </section>
              {meetingConflict ? (
                <section
                  role="alert"
                  className={cn(
                    'space-y-3 rounded-md border p-3 sm:col-span-2',
                    meetingConflict.kind === 'blocking'
                      ? 'border-red-500/35 bg-red-500/10 text-red-800 dark:text-red-300'
                      : 'border-amber-500/35 bg-amber-500/10 text-amber-900 dark:text-amber-300',
                  )}
                  aria-label={meetingConflict.kind === 'blocking' ? 'Conflito bloqueante' : 'Aviso de conflito'}
                >
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                      <p className="text-xs font-semibold">
                        {meetingConflict.kind === 'blocking'
                          ? 'O organizador não está disponível'
                          : 'Participante com sobreposição de agenda'}
                      </p>
                      <p className="mt-1 text-2xs leading-4">{meetingConflict.message}</p>
                      {conflictParticipantLabels.length > 0 ? (
                        <p className="mt-1 text-2xs">
                          Pessoas afetadas: {conflictParticipantLabels.join(', ')}.
                        </p>
                      ) : null}
                    </div>
                  </div>
                  {meetingConflict.kind === 'blocking' ? (
                    <p className="text-2xs font-medium">
                      Escolha outro organizador ou horário. Este conflito não possui override.
                    </p>
                  ) : (
                    <>
                      <label className="flex items-start gap-2 text-2xs">
                        <Checkbox
                          checked={draft.confirmParticipantConflicts}
                          onCheckedChange={(checked) => setDraft({
                            ...draft,
                            confirmParticipantConflicts: checked === true,
                            conflictReason: checked === true ? draft.conflictReason : '',
                          })}
                          aria-label="Confirmar sobreposição de participantes"
                        />
                        Confirmo que a sobreposição foi verificada e autorizada.
                      </label>
                      {draft.confirmParticipantConflicts ? (
                        <div className="space-y-1.5">
                          <Label htmlFor="agenda-conflict-reason">Motivo da confirmação</Label>
                          <Textarea
                            id="agenda-conflict-reason"
                            value={draft.conflictReason}
                            onChange={(event) => setDraft({ ...draft, conflictReason: event.target.value })}
                            placeholder="Explique quem confirmou e por que a sobreposição é aceitável"
                            maxLength={MAX_CONFLICT_REASON_LENGTH}
                            rows={3}
                          />
                          <p className="text-2xs">
                            Entre {MIN_CONFLICT_REASON_LENGTH} e {MAX_CONFLICT_REASON_LENGTH} caracteres. O motivo será auditado junto com o compromisso.
                          </p>
                        </div>
                      ) : null}
                    </>
                  )}
                </section>
              ) : null}
              <div className="space-y-1.5">
                <Label htmlFor="agenda-location">Local</Label>
                <div className="relative"><MapPin className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input id="agenda-location" className="pl-9" value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} placeholder="Google Meet ou escritório" /></div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="agenda-url">Link da reunião</Label>
                <div className="relative"><Video className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input id="agenda-url" className="pl-9" value={draft.meetingUrl} onChange={(event) => setDraft({ ...draft, meetingUrl: event.target.value })} placeholder="https://meet.google.com/..." /></div>
              </div>
              {draft.id && (
                <div className="space-y-1.5 sm:col-span-2">
                  <Label>Status</Label>
                  <Select value={draft.status} onValueChange={(value: KlmMeeting['status']) => setDraft({ ...draft, status: value })}>
                    <SelectTrigger aria-label="Status da reunião"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="scheduled">Agendada</SelectItem>
                      <SelectItem value="completed">Concluída</SelectItem>
                      <SelectItem value="cancelled">Cancelada</SelectItem>
                      <SelectItem value="no_show">Não compareceu</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="agenda-notes">Pauta e observações internas</Label>
                <Textarea id="agenda-notes" value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} placeholder="Objetivo, documentos para revisar e próximos passos" rows={4} />
              </div>
              {selectedMatter && (
                <div className="flex items-center gap-3 rounded-md border border-border bg-secondary/45 px-3 py-2 sm:col-span-2">
                  <UserRound className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <p className="min-w-0 truncate text-2xs text-muted-foreground">{selectedMatter.name} · {selectedMatter.subject} · etapa {selectedMatter.stage}</p>
                </div>
              )}
              {draft.id && !draft.matterId && (
                <div className="flex items-start gap-3 border-l-2 border-[#c3922f] bg-[#fff8e8] px-3 py-3 text-[#6f4a05] dark:bg-[#c3922f]/10 dark:text-[#f1c46f] sm:col-span-2">
                  <Link2 className="mt-0.5 h-4 w-4 shrink-0" /><div><p className="text-2xs font-semibold">Agendamento órfão</p><p className="mt-1 text-2xs leading-4">Selecione um card existente acima para incorporar o contexto do lead e alimentar o RAG.</p></div>
                </div>
              )}
              {draft.id && (
                <div className="border-t border-border pt-4 sm:col-span-2">
                  <div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><BrainCircuit className="h-4 w-4 text-[#0057d8]" /><p className="text-xs font-semibold">Briefing para a reunião</p></div><KlmPill tone="gold">IA · contexto local</KlmPill></div>
                  {draftBriefingQuery.isLoading ? <div className="flex items-center py-4 text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Preparando resumo e insights...</div> : draftBriefingQuery.data ? <BriefingSnapshot briefing={draftBriefingQuery.data} /> : <p className="py-4 text-2xs text-muted-foreground">Não foi possível carregar o contexto deste agendamento.</p>}
                </div>
              )}
            </div>
          )}
          <DialogFooter className="shrink-0 gap-2 border-t border-border pt-3 sm:justify-between">
            <div className="flex gap-2">
              {draft?.id && (
                <Button variant="outline" className="text-red-700 hover:bg-red-50 hover:text-red-800 dark:text-red-400" onClick={() => setDeleteTarget(meetings.find((meeting) => meeting.id === draft.id) ?? null)}>
                  <Trash2 className="mr-2 h-4 w-4" />Excluir
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setDraft(null)} disabled={saveMutation.isPending}>Cancelar</Button>
              <Button
                onClick={submitDraft}
                disabled={
                  saveMutation.isPending
                  || !draft?.title.trim()
                  || !draft.startsAt
                  || !draft.endsAt
                  || meetingConflict?.kind === 'blocking'
                  || Boolean(
                    meetingConflict?.kind === 'warning'
                    && (
                      !draft.confirmParticipantConflicts
                      || draft.conflictReason.trim().length < MIN_CONFLICT_REASON_LENGTH
                      || draft.conflictReason.length > MAX_CONFLICT_REASON_LENGTH
                    ),
                  )
                }
              >
                {saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Pencil className="mr-2 h-4 w-4" />}
                {draft?.id ? 'Salvar alterações' : 'Criar reunião'}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(followUpDetail)} onOpenChange={(open) => { if (!open) setFollowUpDetail(null); }}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>Follow-up programado</DialogTitle>
            <DialogDescription>Acompanhamento comercial associado ao histórico do caso.</DialogDescription>
          </DialogHeader>
          {followUpDetail?.followUp && (
            <div className="space-y-3 py-2">
              <div className="rounded-md border border-border bg-secondary/40 p-3">
                <p className="text-sm font-semibold">{followUpDetail.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">{format(followUpDetail.start, "d 'de' MMMM 'às' HH:mm", { locale: ptBR })}</p>
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div><p className="text-2xs text-muted-foreground">Canal</p><p className="mt-1 font-medium capitalize">{followUpDetail.followUp.channel}</p></div>
                <div><p className="text-2xs text-muted-foreground">Tentativa</p><p className="mt-1 font-medium">{followUpDetail.followUp.attempt} de {followUpDetail.followUp.maxAttempts}</p></div>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setFollowUpDetail(null)}>Fechar</Button>
            {followUpDetail?.followUp?.threadId && <Button onClick={() => {
              const params = new URLSearchParams({ thread: followUpDetail.followUp!.threadId! });
              if (followUpDetail.followUp?.matterId) params.set('matter', followUpDetail.followUp.matterId);
              navigate(`/app/conversas?${params.toString()}`);
            }}>Abrir conversa</Button>}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={googleDialogOpen} onOpenChange={setGoogleDialogOpen}>
        <DialogContent className="sm:max-w-[560px]">
          <DialogHeader>
            <DialogTitle>Google Agenda</DialogTitle>
            <DialogDescription>Conexão oficial por OAuth, sem compartilhar senha da conta Google.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className={cn('flex items-start gap-3 rounded-md border p-3', googleConnected ? 'border-emerald-600/25 bg-emerald-50 dark:bg-emerald-500/10' : 'border-primary/30 bg-primary/8')}>
              {googleConnected ? <CalendarCheck2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700 dark:text-emerald-400" /> : <CloudOff className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />}
              <div>
                <p className="text-xs font-semibold">{googleConnected ? 'Calendário conectado' : 'Conexão externa ainda bloqueada'}</p>
                <p className="mt-1 text-2xs leading-4 text-muted-foreground">
                  {googleConnected ? 'Novas reuniões podem ser vinculadas ao Google Agenda.' : 'A interface e a agenda local estão prontas. O OAuth só será ligado depois da sua aprovação das APIs e do cadastro do Client ID e Client Secret.'}
                </p>
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {[
                ['Sincronização nos dois sentidos', 'Alterações na plataforma e no Google reconciliadas.'],
                ['Google Meet', 'Link criado quando a conta conectada autorizar.'],
                ['Calendário por usuário', 'Cada responsável escolhe o calendário de trabalho.'],
                ['Trilha de auditoria', 'Criação, alteração e conflitos ficam registrados.'],
              ].map(([title, description]) => (
                <div key={title} className="rounded-md border border-border p-3">
                  <p className="text-2xs font-semibold text-foreground">{title}</p>
                  <p className="mt-1 text-2xs leading-4 text-muted-foreground">{description}</p>
                </div>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setGoogleDialogOpen(false)}>Fechar</Button>
            {googleConnected ? (
              <Button onClick={() => googleRefreshMutation.mutate()} disabled={googleRefreshMutation.isPending}>
                <RefreshCw className={cn('mr-2 h-4 w-4', googleRefreshMutation.isPending && 'animate-spin')} />
                {googleRefreshMutation.isPending ? 'Atualizando dados locais' : 'Atualizar agenda local'}
              </Button>
            ) : (
              <Button onClick={() => { setGoogleDialogOpen(false); navigate(contextualPath('/app/agenda/configuracoes')); }}>
                <Settings className="mr-2 h-4 w-4" />Abrir configurações
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <BookingLinkDialog
        open={bookingDialogOpen}
        onOpenChange={setBookingDialogOpen}
        matterOptions={(funnelQuery.data?.cards ?? []).map((matter) => ({ id: matter.id, label: `${matter.name} · ${matter.subject}` }))}
        defaultMaxUses={1}
      />

      <TaskCollaborationDrawer
        taskId={selectedTaskId}
        contextLabels={{
          matterLabel: allItems.find((item) => item.taskId === selectedTaskId)?.matterName,
        }}
        open={Boolean(selectedTaskId)}
        onOpenChange={(nextOpen) => { if (!nextOpen) setSelectedTaskId(null); }}
      />

      <OperationalCopilot
        domain="agenda"
        title="Copiloto de Agenda"
        subtitle="Reuniões, disponibilidade e briefing"
        welcome="Diga o que precisa organizar. Eu abro rascunhos, localizo configurações e levo ao resumo certo; você confirma qualquer criação."
        placeholder="Ex.: prepare uma reunião amanhã às 14h e mostre o resumo do dia."
        contextKey={`agenda:${format(selectedDay, 'yyyy-MM-dd')}:${view}`}
        contextSnapshot={copilotContext}
        workspaceId={appStore?.state?.activeWorkspace?.id}
        userId={appStore?.state?.currentUser?.id}
        canApply={appStore?.can('lista', 'view') ?? true}
        onApply={applyCopilotActions}
      />

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(open) => { if (!open && !deleteMutation.isPending) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir esta reunião?</AlertDialogTitle>
            <AlertDialogDescription>Ela será removida da agenda local. Esta ação não tenta apagar eventos externos enquanto o Google estiver desconectado.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Manter reunião</AlertDialogCancel>
            <AlertDialogAction className="bg-red-700 text-white hover:bg-red-800" disabled={deleteMutation.isPending} onClick={(event) => { event.preventDefault(); if (deleteTarget) deleteMutation.mutate(deleteTarget); }}>
              {deleteMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Excluir
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      </div>
    </AgendaVisibilityContext.Provider>
  );
}
