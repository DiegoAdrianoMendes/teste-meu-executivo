import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CalendarDays,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Users,
} from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  klmApi,
  type KlmMeeting,
  type KlmMeetingEventType,
  type KlmMeetingNature,
  type KlmMeetingParticipantRsvp,
  type KlmMeetingParticipantWrite,
  type KlmMeetingWrite,
} from '@/klm/api';
import { ApiError, apiFetch } from '@/services/http';
import { SUPPORT_MODE_ROUTES, supportModeRoutePath } from '@/support-mode/routes';
import {
  getActiveSupportMode,
  type SupportModeSession,
} from '@/support-mode/session';

type MeetingConflict = {
  kind: 'blocking' | 'warning';
  code: 'MEE-007' | 'MEE-008' | 'MEE-019';
  message: string;
  participantUserIds: string[];
};

type TeamMemberOption = {
  userId: string;
  name: string;
  email: string;
  status: string;
  isActive: boolean;
};

type MatterOption = {
  id: string;
  caseNumber?: string | null;
  demandType?: string | null;
  subject?: string | null;
  client?: { name?: string | null } | null;
};

type TaskOption = {
  id: string;
  title: string;
  status: string;
};

type MeetingDraft = {
  id: string | null;
  version: number | null;
  title: string;
  startsAt: string;
  endsAt: string;
  status: KlmMeeting['status'];
  natureza: KlmMeetingNature;
  eventType: KlmMeetingEventType;
  matterId: string;
  linkedTaskId: string;
  location: string;
  meetingUrl: string;
  notes: string;
  participants: KlmMeetingParticipantWrite[];
  confirmParticipantConflicts: boolean;
  conflictReason: string;
};

const STATUS_LABEL: Record<KlmMeeting['status'], string> = {
  scheduled: 'Agendada',
  completed: 'Concluída',
  cancelled: 'Cancelada',
  no_show: 'Não compareceu',
};

const RSVP_LABEL: Record<KlmMeetingParticipantRsvp, string> = {
  pending: 'Aguardando',
  accepted: 'Confirmado',
  declined: 'Recusou',
  tentative: 'Talvez',
};

function activeSessionMatches(session: SupportModeSession): boolean {
  const active = getActiveSupportMode();
  const activeScopes = [...(active?.scopes ?? [])].sort();
  const expectedScopes = [...session.scopes].sort();
  return Boolean(
    active
    && active.grantId === session.grantId
    && active.workspaceId === session.workspaceId
    && active.expiresAt === session.expiresAt
    && activeScopes.length === expectedScopes.length
    && activeScopes.every((scope, index) => scope === expectedScopes[index])
    && ['root_direct', 'support_approved'].includes(active.sessionType ?? 'support_approved'),
  );
}

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? 'Data indisponível'
    : parsed.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

function toDateTimeLocal(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const local = new Date(parsed.getTime() - (parsed.getTimezoneOffset() * 60_000));
  return local.toISOString().slice(0, 16);
}

function defaultDraft(): MeetingDraft {
  const start = new Date(Date.now() + 60 * 60_000);
  start.setMinutes(Math.ceil(start.getMinutes() / 15) * 15, 0, 0);
  const end = new Date(start.getTime() + 60 * 60_000);
  return {
    id: null,
    version: null,
    title: '',
    startsAt: toDateTimeLocal(start.toISOString()),
    endsAt: toDateTimeLocal(end.toISOString()),
    status: 'scheduled',
    natureza: 'comercial',
    eventType: 'reuniao',
    matterId: '',
    linkedTaskId: '',
    location: '',
    meetingUrl: '',
    notes: '',
    participants: [],
    confirmParticipantConflicts: false,
    conflictReason: '',
  };
}

function meetingDraft(meeting: KlmMeeting): MeetingDraft {
  const participants = (meeting.participants ?? [])
    .filter((participant) => participant.active)
    .map((participant) => ({
      userId: participant.userId,
      role: participant.role,
      rsvp: participant.rsvp,
      reminderMinutesBefore: participant.reminderMinutesBefore,
    }));
  if (
    meeting.responsibleUserId
    && !participants.some((participant) => participant.role === 'organizer')
  ) {
    participants.unshift({
      userId: meeting.responsibleUserId,
      role: 'organizer',
      rsvp: 'pending',
      reminderMinutesBefore: null,
    });
  }
  return {
    id: meeting.id,
    version: meeting.version,
    title: meeting.title,
    startsAt: toDateTimeLocal(meeting.startsAt),
    endsAt: meeting.endsAt ? toDateTimeLocal(meeting.endsAt) : '',
    status: meeting.status,
    natureza: meeting.natureza ?? 'comercial',
    eventType: meeting.eventType ?? 'reuniao',
    matterId: meeting.matterId ?? '',
    linkedTaskId: meeting.linkedTaskId ?? '',
    location: meeting.location ?? '',
    meetingUrl: meeting.meetingUrl ?? '',
    notes: meeting.notes ?? '',
    participants,
    confirmParticipantConflicts: false,
    conflictReason: '',
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

function withOrganizer(
  participants: KlmMeetingParticipantWrite[],
  userId: string,
): KlmMeetingParticipantWrite[] {
  const previous = participants.find((participant) => participant.userId === userId);
  const others = participants.filter(
    (participant) => participant.role !== 'organizer' && participant.userId !== userId,
  );
  return userId
    ? [participantWithDefaults(userId, 'organizer', previous), ...others]
    : others;
}

function conflictFromError(error: unknown): MeetingConflict | null {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') return null;
  const detail = error.detail as Record<string, unknown>;
  const code = detail.code;
  if (code !== 'MEE-007' && code !== 'MEE-008' && code !== 'MEE-019') return null;
  return {
    kind: code === 'MEE-007' ? 'blocking' : 'warning',
    code,
    message: typeof detail.message === 'string'
      ? detail.message
      : 'Há conflito entre os compromissos das pessoas selecionadas.',
    participantUserIds: Array.isArray(detail.participantUserIds)
      ? detail.participantUserIds.filter((value): value is string => typeof value === 'string')
      : [],
  };
}

function matterLabel(matter: MatterOption): string {
  const context = matter.demandType || matter.subject || matter.caseNumber || 'Caso';
  const client = matter.client?.name?.trim();
  return client ? `${client} · ${context}` : context;
}

function meetingParticipantLabels(meeting: KlmMeeting): Map<string, string> {
  return new Map(
    (meeting.participants ?? []).map((participant) => [
      participant.userId,
      participant.labelSnapshot || 'Pessoa vinculada',
    ]),
  );
}

function ScopeNotice({
  scope,
  children,
}: {
  scope: string;
  children: string;
}) {
  return (
    <p className="text-xs text-muted-foreground">
      {children} Solicite também <code className="rounded bg-muted px-1 py-0.5">{scope}</code>.
    </p>
  );
}

export function SupportMeetingsSurface({ session }: { session: SupportModeSession }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const administrativeSession = activeSessionMatches(session);
  const canRead = session.scopes.includes('meetings:read');
  const canWrite = session.scopes.includes('meetings:write');
  const canReadMembers = session.scopes.includes('members:read');
  const canReadMatters = session.scopes.includes('matters:read');
  const canReadTasks = session.scopes.includes('tasks:read');
  const canReadLeadData = session.scopes.includes('lead_data:read');
  const [draft, setDraft] = useState<MeetingDraft | null>(null);
  const [participantSearch, setParticipantSearch] = useState('');
  const [taskSearch, setTaskSearch] = useState('');
  const [conflict, setConflict] = useState<MeetingConflict | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const meetingsQuery = useQuery({
    queryKey: ['support-mode', session.grantId, session.workspaceId, 'meetings'],
    queryFn: klmApi.meetings,
    enabled: administrativeSession && canRead,
    retry: false,
  });
  const membersQuery = useQuery({
    queryKey: ['support-mode', session.grantId, session.workspaceId, 'meeting-members'],
    queryFn: () => apiFetch<TeamMemberOption[]>(
      supportModeRoutePath(SUPPORT_MODE_ROUTES.teamMembersList),
    ),
    enabled: administrativeSession && canWrite && canReadMembers,
    retry: false,
  });
  const mattersQuery = useQuery({
    queryKey: ['support-mode', session.grantId, session.workspaceId, 'meeting-matters'],
    queryFn: () => apiFetch<MatterOption[]>(
      supportModeRoutePath(SUPPORT_MODE_ROUTES.mattersList),
    ),
    enabled: administrativeSession && canWrite && canReadMatters,
    retry: false,
  });
  const matterTasksQuery = useQuery({
    queryKey: [
      'support-mode',
      session.grantId,
      session.workspaceId,
      'meeting-tasks',
      draft?.matterId,
    ],
    queryFn: () => apiFetch<TaskOption[]>(
      supportModeRoutePath(
        SUPPORT_MODE_ROUTES.tasksForMatter,
        { matterId: draft!.matterId },
      ),
    ),
    enabled: Boolean(
      administrativeSession
      && canWrite
      && canReadTasks
      && draft?.matterId,
    ),
    retry: false,
  });

  const meetings = useMemo(() => meetingsQuery.data ?? [], [meetingsQuery.data]);
  const teamMembers = useMemo(
    () => (membersQuery.data ?? []).filter(
      (member) => member.isActive && member.status === 'active',
    ),
    [membersQuery.data],
  );
  const selectedMeeting = useMemo(
    () => meetings.find((meeting) => meeting.id === draft?.id) ?? null,
    [draft?.id, meetings],
  );
  const preservedLabels = useMemo(
    () => (selectedMeeting ? meetingParticipantLabels(selectedMeeting) : new Map<string, string>()),
    [selectedMeeting],
  );
  const memberLabels = useMemo(() => {
    const labels = new Map(preservedLabels);
    teamMembers.forEach((member) => labels.set(member.userId, member.name));
    return labels;
  }, [preservedLabels, teamMembers]);
  const mattersById = useMemo(
    () => new Map((mattersQuery.data ?? []).map((matter) => [matter.id, matter])),
    [mattersQuery.data],
  );
  const filteredTasks = useMemo(() => {
    const normalized = taskSearch.trim().toLocaleLowerCase('pt-BR');
    const tasks = matterTasksQuery.data ?? [];
    return normalized
      ? tasks.filter((task) => task.title.toLocaleLowerCase('pt-BR').includes(normalized))
      : tasks;
  }, [matterTasksQuery.data, taskSearch]);
  const participantCandidates = useMemo(() => {
    if (!draft) return [];
    const selected = new Set(draft.participants.map((participant) => participant.userId));
    const normalized = participantSearch.trim().toLocaleLowerCase('pt-BR');
    return teamMembers.filter((member) => (
      !selected.has(member.userId)
      && (!normalized
        || `${member.name} ${member.email}`.toLocaleLowerCase('pt-BR').includes(normalized))
    ));
  }, [draft, participantSearch, teamMembers]);
  const organizerId = draft?.participants.find(
    (participant) => participant.role === 'organizer',
  )?.userId ?? '';

  useEffect(() => {
    if (draft?.matterId && draft.linkedTaskId && matterTasksQuery.isSuccess) {
      const taskStillCompatible = matterTasksQuery.data.some(
        (task) => task.id === draft.linkedTaskId,
      );
      if (!taskStillCompatible && canReadTasks) {
        setDraft((current) => current ? { ...current, linkedTaskId: '' } : current);
      }
    }
  }, [
    canReadTasks,
    draft?.linkedTaskId,
    draft?.matterId,
    matterTasksQuery.data,
    matterTasksQuery.isSuccess,
  ]);

  const mutation = useMutation({
    mutationKey: ['support-mode', session.grantId, session.workspaceId, 'meeting-write'],
    mutationFn: async (payload: MeetingDraft) => {
      if (!activeSessionMatches(session)) {
        throw new Error('A sessão administrativa expirou ou foi substituída.');
      }
      const organizer = payload.participants.filter(
        (participant) => participant.role === 'organizer',
      );
      if (organizer.length !== 1) {
        throw new Error('Defina exatamente um organizador antes de salvar.');
      }
      const startsAt = new Date(payload.startsAt);
      const endsAt = new Date(payload.endsAt);
      if (
        Number.isNaN(startsAt.getTime())
        || Number.isNaN(endsAt.getTime())
        || endsAt <= startsAt
      ) {
        throw new Error('O término deve ser posterior ao início.');
      }
      const write: KlmMeetingWrite = {
        matterId: payload.matterId || null,
        responsibleUserId: organizer[0].userId,
        linkedTaskId: payload.linkedTaskId || null,
        title: payload.title.trim(),
        startsAt: startsAt.toISOString(),
        endsAt: endsAt.toISOString(),
        natureza: payload.natureza,
        eventType: payload.eventType,
        location: payload.location.trim() || null,
        meetingUrl: payload.meetingUrl.trim() || null,
        notes: payload.notes.trim() || null,
        participants: payload.participants,
        confirmParticipantConflicts: payload.confirmParticipantConflicts,
        conflictReason: payload.confirmParticipantConflicts
          ? payload.conflictReason.trim()
          : null,
      };
      if (payload.id) {
        if (!payload.version) throw new Error('Atualize a lista antes de editar este compromisso.');
        return klmApi.updateMeeting(payload.id, {
          ...write,
          status: payload.status,
          expectedVersion: payload.version,
        });
      }
      return klmApi.createMeeting(write);
    },
    onSuccess: async (meeting) => {
      if (!activeSessionMatches(session)) return;
      setDraft(null);
      setConflict(null);
      setFeedback('Compromisso salvo e enviado à trilha de auditoria.');
      await queryClient.invalidateQueries({
        queryKey: ['support-mode', session.grantId, session.workspaceId, 'meetings'],
      });
      if (!activeSessionMatches(session)) return;
      toast({ title: `${meeting.title} foi salvo.` });
    },
    onError: (error) => {
      if (!activeSessionMatches(session)) return;
      const parsed = conflictFromError(error);
      if (parsed) {
        setConflict(parsed);
        return;
      }
      toast({
        variant: 'destructive',
        title: 'Compromisso não salvo',
        description: error instanceof Error ? error.message : 'A operação não foi concluída.',
      });
    },
  });

  const updateConflictSensitiveDraft = (
    updater: (current: MeetingDraft) => MeetingDraft,
  ) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...updater(current),
        confirmParticipantConflicts: false,
        conflictReason: '',
      };
    });
    setConflict(null);
  };

  const openNew = () => {
    if (!canWrite || !canReadMembers) return;
    setFeedback(null);
    setConflict(null);
    setParticipantSearch('');
    setTaskSearch('');
    setDraft(defaultDraft());
  };

  const openExisting = (meeting: KlmMeeting) => {
    if (!canWrite) return;
    setFeedback(null);
    setConflict(null);
    setParticipantSearch('');
    setTaskSearch('');
    setDraft(meetingDraft(meeting));
  };

  const addParticipant = (
    userId: string,
    role: 'attendee' | 'observer',
  ) => {
    updateConflictSensitiveDraft((current) => ({
      ...current,
      participants: [
        ...current.participants,
        participantWithDefaults(userId, role),
      ],
    }));
    setParticipantSearch('');
  };

  const updateParticipant = (
    userId: string,
    changes: Partial<KlmMeetingParticipantWrite>,
  ) => {
    updateConflictSensitiveDraft((current) => ({
      ...current,
      participants: current.participants.map((participant) => (
        participant.userId === userId ? { ...participant, ...changes } : participant
      )),
    }));
  };

  const removeParticipant = (userId: string) => {
    updateConflictSensitiveDraft((current) => ({
      ...current,
      participants: current.participants.filter(
        (participant) => participant.userId !== userId,
      ),
    }));
  };

  if (!administrativeSession) {
    return (
      <Alert variant="destructive" data-testid="support-meetings-session-required">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Sessão administrativa ativa obrigatória</AlertTitle>
        <AlertDescription>
          Nenhum compromisso foi consultado. Ative uma sessão da mesma conta antes de continuar.
        </AlertDescription>
      </Alert>
    );
  }

  if (!canRead) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Leitura de compromissos não concedida</AlertTitle>
        <AlertDescription>
          Para editar com segurança sem apagar dados existentes, aprove também
          <code className="mx-1 rounded bg-muted px-1 py-0.5">meetings:read</code>.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-4" data-testid="support-meetings-surface">
      <Alert>
        <CalendarDays className="h-4 w-4" />
        <AlertTitle>Agenda interna autorizada</AlertTitle>
        <AlertDescription>
          Estes compromissos pertencem exclusivamente a {session.workspaceName}.
          Configurações de calendário exigem `configurations:*` e booking público não é permitido.
        </AlertDescription>
      </Alert>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Compromissos da conta</h2>
          <p className="text-xs text-muted-foreground">
            Pessoas e vínculos aparecem por rótulos; identificadores internos não são solicitados.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            size="icon"
            variant="outline"
            aria-label="Atualizar compromissos da conta"
            disabled={meetingsQuery.isFetching}
            onClick={() => void meetingsQuery.refetch()}
          >
            <RefreshCw className={`h-4 w-4 ${meetingsQuery.isFetching ? 'animate-spin' : ''}`} />
          </Button>
          {canWrite ? (
            <Button type="button" onClick={openNew} disabled={!canReadMembers}>
              <Plus className="mr-2 h-4 w-4" />Nova reunião
            </Button>
          ) : null}
        </div>
      </div>

      {canWrite && !canReadMembers ? (
        <ScopeNotice scope="members:read">
          Para criar um compromisso e definir seu organizador, falta o escopo de equipe.
        </ScopeNotice>
      ) : null}
      {canWrite && !canReadMatters ? (
        <ScopeNotice scope="matters:read">
          Casos existentes serão preservados, mas o seletor de caso está indisponível.
        </ScopeNotice>
      ) : null}
      {canWrite && !canReadTasks ? (
        <ScopeNotice scope="tasks:read">
          Tarefas existentes serão preservadas, mas o seletor de tarefa compatível está indisponível.
        </ScopeNotice>
      ) : null}
      {canWrite && !canReadLeadData ? (
        <ScopeNotice scope="lead_data:read">
          A criação continua disponível, mas editar compromissos existentes foi bloqueado para não sobrescrever conteúdo mascarado.
        </ScopeNotice>
      ) : null}

      {feedback ? (
        <Alert>
          <AlertTitle>Alteração concluída</AlertTitle>
          <AlertDescription>{feedback}</AlertDescription>
        </Alert>
      ) : null}
      {meetingsQuery.isPending ? (
        <div className="space-y-3" role="status" aria-live="polite">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <span className="sr-only">Carregando compromissos autorizados</span>
        </div>
      ) : null}
      {meetingsQuery.isError ? (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Agenda indisponível</AlertTitle>
          <AlertDescription>
            A sessão expirou, perdeu o escopo ou a API está indisponível. Nenhum vazio foi simulado.
          </AlertDescription>
        </Alert>
      ) : null}
      {meetingsQuery.isSuccess && meetings.length === 0 ? (
        <div className="rounded-lg border border-dashed p-10 text-center">
          <CalendarDays className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 font-medium">Nenhum compromisso encontrado</p>
          <p className="text-sm text-muted-foreground">A consulta foi concluída sem resultados.</p>
        </div>
      ) : null}

      <div className="space-y-3">
        {meetings.map((meeting) => {
          const labels = (meeting.participants ?? [])
            .filter((participant) => participant.active)
            .map((participant) => participant.labelSnapshot)
            .filter(Boolean);
          return (
            <article key={meeting.id} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">{STATUS_LABEL[meeting.status]}</Badge>
                    <Badge variant="secondary">{meeting.eventType ?? 'reuniao'}</Badge>
                  </div>
                  <h3 className="mt-2 truncate font-semibold">{meeting.title}</h3>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {formatDateTime(meeting.startsAt)}
                    {meeting.endsAt ? ` até ${formatDateTime(meeting.endsAt)}` : ''}
                  </p>
                  {labels.length ? (
                    <p className="mt-2 text-xs text-muted-foreground">
                      <Users className="mr-1 inline h-3.5 w-3.5" />
                      {labels.join(', ')}
                    </p>
                  ) : null}
                </div>
                {canWrite && canReadLeadData ? (
                  <Button type="button" size="sm" variant="outline" onClick={() => openExisting(meeting)}>
                    <Pencil className="mr-1.5 h-3.5 w-3.5" />Editar
                  </Button>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>

      <Dialog open={Boolean(draft)} onOpenChange={(open) => {
        if (!open && !mutation.isPending) {
          setDraft(null);
          setConflict(null);
        }
      }}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[780px]">
          <DialogHeader>
            <DialogTitle>{draft?.id ? 'Editar compromisso' : 'Nova reunião'}</DialogTitle>
            <DialogDescription>
              A operação usa a sessão administrativa atual e mantém a autoria do funcionário da plataforma.
            </DialogDescription>
          </DialogHeader>

          {draft ? (
            <div className="grid gap-4 py-2 sm:grid-cols-2">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="support-meeting-title">Título</Label>
                <Input
                  id="support-meeting-title"
                  value={draft.title}
                  onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                  maxLength={200}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="support-meeting-start">Início</Label>
                <Input
                  id="support-meeting-start"
                  type="datetime-local"
                  value={draft.startsAt}
                  onChange={(event) => updateConflictSensitiveDraft(
                    (current) => ({ ...current, startsAt: event.target.value }),
                  )}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="support-meeting-end">Término</Label>
                <Input
                  id="support-meeting-end"
                  type="datetime-local"
                  value={draft.endsAt}
                  onChange={(event) => updateConflictSensitiveDraft(
                    (current) => ({ ...current, endsAt: event.target.value }),
                  )}
                />
              </div>

              <div className="space-y-1.5">
                <Label>Natureza</Label>
                <Select
                  value={draft.natureza}
                  onValueChange={(value: KlmMeetingNature) => setDraft({ ...draft, natureza: value })}
                >
                  <SelectTrigger aria-label="Natureza do compromisso"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="comercial">Comercial</SelectItem>
                    <SelectItem value="juridico">Jurídico</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Tipo</Label>
                <Select
                  value={draft.eventType}
                  onValueChange={(value: KlmMeetingEventType) => setDraft({ ...draft, eventType: value })}
                >
                  <SelectTrigger aria-label="Tipo do compromisso"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="reuniao">Reunião</SelectItem>
                    <SelectItem value="audiencia">Audiência</SelectItem>
                    <SelectItem value="sustentacao">Sustentação</SelectItem>
                    <SelectItem value="prazo_interno">Prazo interno</SelectItem>
                    <SelectItem value="revisao">Revisão</SelectItem>
                    <SelectItem value="personalizado">Personalizado</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label>Caso</Label>
                {canReadMatters ? (
                  <>
                    {mattersQuery.isError ? (
                      <p role="alert" className="text-xs text-destructive">
                        Não foi possível carregar os casos; o vínculo existente foi preservado.
                      </p>
                    ) : (
                      <Select
                        value={draft.matterId || '__none__'}
                        disabled={
                          mattersQuery.isPending
                          || Boolean(draft.linkedTaskId && !canReadTasks)
                        }
                        onValueChange={(value) => updateConflictSensitiveDraft((current) => ({
                          ...current,
                          matterId: value === '__none__' ? '' : value,
                          linkedTaskId: '',
                        }))}
                      >
                        <SelectTrigger aria-label="Caso vinculado"><SelectValue placeholder="Sem caso" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">Sem caso vinculado</SelectItem>
                          {(mattersQuery.data ?? []).map((matter) => (
                            <SelectItem key={matter.id} value={matter.id}>{matterLabel(matter)}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </>
                ) : (
                  <p className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
                    {draft.matterId ? 'Caso vinculado preservado.' : 'Sem caso vinculado.'}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label>Tarefa do mesmo caso</Label>
                {canReadTasks && draft.matterId ? (
                  <>
                    <Input
                      value={taskSearch}
                      onChange={(event) => setTaskSearch(event.target.value)}
                      placeholder="Pesquisar tarefa pelo título"
                      aria-label="Pesquisar tarefa do caso"
                      disabled={matterTasksQuery.isPending || matterTasksQuery.isError}
                    />
                    {matterTasksQuery.isError ? (
                      <p role="alert" className="text-xs text-destructive">
                        Não foi possível carregar as tarefas; o vínculo existente foi preservado.
                      </p>
                    ) : (
                      <Select
                        value={draft.linkedTaskId || '__none__'}
                        disabled={matterTasksQuery.isPending}
                        onValueChange={(value) => setDraft({
                          ...draft,
                          linkedTaskId: value === '__none__' ? '' : value,
                        })}
                      >
                        <SelectTrigger aria-label="Tarefa vinculada"><SelectValue placeholder="Sem tarefa" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">Sem tarefa vinculada</SelectItem>
                          {filteredTasks.map((task) => (
                            <SelectItem key={task.id} value={task.id}>
                              {task.title} · {task.status === 'done' ? 'Concluída' : 'Em andamento'}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </>
                ) : (
                  <p className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
                    {draft.linkedTaskId
                      ? 'Tarefa compatível preservada.'
                      : draft.matterId
                        ? 'Seletor indisponível sem tasks:read.'
                        : 'Selecione um caso para vincular uma tarefa.'}
                  </p>
                )}
              </div>

              <section className="space-y-3 rounded-md border p-3 sm:col-span-2" aria-label="Participantes do compromisso">
                <div>
                  <p className="text-sm font-semibold">Pessoas</p>
                  <p className="text-xs text-muted-foreground">
                    Um organizador, vários participantes e observadores.
                  </p>
                </div>
                {canReadMembers ? (
                  <>
                    <div className="space-y-1.5">
                      <Label>Organizador</Label>
                      <Select
                        value={organizerId || '__none__'}
                        disabled={membersQuery.isPending || membersQuery.isError}
                        onValueChange={(value) => updateConflictSensitiveDraft((current) => ({
                          ...current,
                          participants: withOrganizer(
                            current.participants,
                            value === '__none__' ? '' : value,
                          ),
                        }))}
                      >
                        <SelectTrigger aria-label="Organizador do compromisso"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">Selecione o organizador</SelectItem>
                          {teamMembers.map((member) => (
                            <SelectItem key={member.userId} value={member.userId}>
                              {member.name} · {member.email}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="support-meeting-participant-search">Adicionar pessoas</Label>
                      <div className="relative">
                        <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                        <Input
                          id="support-meeting-participant-search"
                          className="pl-9"
                          value={participantSearch}
                          onChange={(event) => setParticipantSearch(event.target.value)}
                          placeholder="Pesquisar por nome ou e-mail"
                          disabled={!organizerId}
                        />
                      </div>
                    </div>
                    {organizerId && participantCandidates.length ? (
                      <div className="max-h-36 space-y-1 overflow-y-auto" role="list" aria-label="Pessoas disponíveis">
                        {participantCandidates.slice(0, 20).map((member) => (
                          <div key={member.userId} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2" role="listitem">
                            <span className="text-xs">{member.name} · {member.email}</span>
                            <div className="flex gap-1">
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => addParticipant(member.userId, 'attendee')}
                                aria-label={`Adicionar ${member.name} como participante`}
                              >
                                Participante
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                onClick={() => addParticipant(member.userId, 'observer')}
                                aria-label={`Adicionar ${member.name} como observador`}
                              >
                                Observador
                              </Button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    As pessoas já vinculadas serão preservadas. Para alterá-las, solicite `members:read`.
                  </p>
                )}

                <div className="space-y-2">
                  {draft.participants.map((participant) => {
                    const label = memberLabels.get(participant.userId) ?? 'Pessoa vinculada';
                    return (
                      <div key={participant.userId} className="grid gap-2 rounded-md border p-2 sm:grid-cols-[minmax(0,1fr)_150px_140px_auto]">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{label}</p>
                          <p className="text-xs text-muted-foreground">
                            {participant.role === 'organizer'
                              ? 'Organizador · conflito bloqueante'
                              : participant.role === 'attendee'
                                ? 'Participante · conflito confirmável'
                                : 'Observador'}
                          </p>
                        </div>
                        <Select
                          value={participant.role}
                          disabled={!canReadMembers || participant.role === 'organizer'}
                          onValueChange={(value: 'attendee' | 'observer') => updateParticipant(
                            participant.userId,
                            { role: value },
                          )}
                        >
                          <SelectTrigger aria-label={`Papel de ${label}`}><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {participant.role === 'organizer' ? (
                              <SelectItem value="organizer">Organizador</SelectItem>
                            ) : null}
                            <SelectItem value="attendee">Participante</SelectItem>
                            <SelectItem value="observer">Observador</SelectItem>
                          </SelectContent>
                        </Select>
                        <Select
                          value={participant.rsvp ?? 'pending'}
                          disabled={!canReadMembers}
                          onValueChange={(value: KlmMeetingParticipantRsvp) => updateParticipant(
                            participant.userId,
                            { rsvp: value },
                          )}
                        >
                          <SelectTrigger aria-label={`RSVP de ${label}`}><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {Object.entries(RSVP_LABEL).map(([value, text]) => (
                              <SelectItem key={value} value={value}>{text}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        {participant.role !== 'organizer' ? (
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            disabled={!canReadMembers}
                            aria-label={`Remover ${label}`}
                            onClick={() => removeParticipant(participant.userId)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        ) : <span />}
                      </div>
                    );
                  })}
                </div>
              </section>

              {conflict ? (
                <Alert
                  variant={conflict.kind === 'blocking' ? 'destructive' : 'default'}
                  className="sm:col-span-2"
                  aria-label={conflict.kind === 'blocking' ? 'Conflito bloqueante' : 'Aviso de conflito'}
                >
                  <AlertTriangle className="h-4 w-4" />
                  <AlertTitle>
                    {conflict.kind === 'blocking'
                      ? 'O organizador não está disponível'
                      : 'Participante com sobreposição'}
                  </AlertTitle>
                  <AlertDescription className="space-y-3">
                    <p>{conflict.message}</p>
                    {conflict.participantUserIds.length ? (
                      <p>
                        Pessoas afetadas: {conflict.participantUserIds.map(
                          (userId) => memberLabels.get(userId) ?? 'Pessoa vinculada',
                        ).join(', ')}.
                      </p>
                    ) : null}
                    {conflict.kind === 'blocking' ? (
                      <p>Escolha outro organizador ou horário. Este conflito não possui override.</p>
                    ) : (
                      <>
                        <label className="flex items-start gap-2">
                          <Checkbox
                            checked={draft.confirmParticipantConflicts}
                            onCheckedChange={(checked) => setDraft({
                              ...draft,
                              confirmParticipantConflicts: checked === true,
                              conflictReason: checked === true ? draft.conflictReason : '',
                            })}
                            aria-label="Confirmar sobreposição de participantes"
                          />
                          Confirmo que a sobreposição foi verificada.
                        </label>
                        {draft.confirmParticipantConflicts ? (
                          <div className="space-y-1.5">
                            <Label htmlFor="support-meeting-conflict-reason">Motivo da confirmação</Label>
                            <Textarea
                              id="support-meeting-conflict-reason"
                              value={draft.conflictReason}
                              onChange={(event) => setDraft({
                                ...draft,
                                conflictReason: event.target.value,
                              })}
                              minLength={8}
                              maxLength={500}
                            />
                            <p>Informe de 8 a 500 caracteres; o motivo será auditado.</p>
                          </div>
                        ) : null}
                      </>
                    )}
                  </AlertDescription>
                </Alert>
              ) : null}

              {draft.id ? (
                <div className="space-y-1.5">
                  <Label>Status</Label>
                  <Select
                    value={draft.status}
                    onValueChange={(value: KlmMeeting['status']) => setDraft({ ...draft, status: value })}
                  >
                    <SelectTrigger aria-label="Status do compromisso"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Object.entries(STATUS_LABEL).map(([value, text]) => (
                        <SelectItem key={value} value={value}>{text}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
              <div className="space-y-1.5">
                <Label htmlFor="support-meeting-location">Local</Label>
                <Input
                  id="support-meeting-location"
                  value={draft.location}
                  onChange={(event) => setDraft({ ...draft, location: event.target.value })}
                  maxLength={255}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="support-meeting-url">Link da reunião</Label>
                <Input
                  id="support-meeting-url"
                  type="url"
                  value={draft.meetingUrl}
                  onChange={(event) => setDraft({ ...draft, meetingUrl: event.target.value })}
                  maxLength={500}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="support-meeting-notes">Pauta e observações</Label>
                <Textarea
                  id="support-meeting-notes"
                  value={draft.notes}
                  onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
                  rows={3}
                  maxLength={4_000}
                />
              </div>
            </div>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setDraft(null)}
              disabled={mutation.isPending}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              onClick={() => draft && mutation.mutate(draft)}
              disabled={
                mutation.isPending
                || !draft?.title.trim()
                || !draft.startsAt
                || !draft.endsAt
                || !organizerId
                || conflict?.kind === 'blocking'
                || Boolean(
                  conflict?.kind === 'warning'
                  && (
                    !draft.confirmParticipantConflicts
                    || draft.conflictReason.trim().length < 8
                    || draft.conflictReason.trim().length > 500
                  )
                )
              }
            >
              {mutation.isPending
                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                : <Pencil className="mr-2 h-4 w-4" />}
              {draft?.id ? 'Salvar alterações' : 'Criar reunião'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
