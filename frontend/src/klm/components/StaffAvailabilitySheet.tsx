import { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  CalendarClock,
  CalendarOff,
  CheckCircle2,
  Clock3,
  Copy,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Repeat2,
  Route,
  Send,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';

export type StaffWeekday = 0 | 1 | 2 | 3 | 4 | 5 | 6;

export type StaffAvailabilityWindow = {
  start: string;
  end: string;
};

export type StaffDayAvailability = {
  weekday: StaffWeekday;
  enabled: boolean;
  windows: StaffAvailabilityWindow[];
};

export type StaffAvailabilityExceptionKind = 'vacation' | 'holiday' | 'exception';
export type StaffAvailabilityExceptionMode = 'unavailable' | 'custom_hours';

export type StaffAvailabilityException = {
  id: string;
  kind: StaffAvailabilityExceptionKind;
  name: string;
  startsOn: string;
  endsOn: string;
  mode: StaffAvailabilityExceptionMode;
  windows: StaffAvailabilityWindow[];
};

export type StaffAvailabilityRedirect =
  | { kind: 'none'; targetId: null }
  | { kind: 'user' | 'team'; targetId: string };

export type StaffAvailabilityValue = {
  mode: 'available' | 'away';
  timezone: string;
  weeklyAvailability: StaffDayAvailability[];
  exceptions: StaffAvailabilityException[];
  absenceMessage: {
    enabled: boolean;
    text: string;
  };
  activation: {
    mode: 'now' | 'scheduled';
    startsAt: string | null;
    endsAt: string | null;
  };
  redirect: StaffAvailabilityRedirect;
  automaticSwap: boolean;
  requestNote: string;
};

export type StaffAvailabilityRequestInput = StaffAvailabilityValue & {
  staffId: string;
};

export type StaffAvailabilityDecisionInput = {
  requestId: string;
  staffId: string;
};

export type StaffAvailabilityApproval = {
  id: string;
  status: 'pending' | 'approved' | 'rejected';
  requestedAt?: string;
  requestedBy?: string;
};

export type StaffAvailabilityRedirectOption = {
  id: string;
  label: string;
  kind: 'user' | 'team';
  description?: string;
  disabled?: boolean;
};

export type StaffAvailabilityRequestHandler = (input: StaffAvailabilityRequestInput) => void | Promise<void>;
export type StaffAvailabilityDecisionHandler = (input: StaffAvailabilityDecisionInput) => void | Promise<void>;

export type StaffAvailabilitySheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  staff: {
    id: string;
    name: string;
    role?: string;
  };
  initialValue?: StaffAvailabilityValue;
  redirectOptions?: readonly StaffAvailabilityRedirectOption[];
  approval?: StaffAvailabilityApproval | null;
  disabled?: boolean;
  onCreateRequest: StaffAvailabilityRequestHandler;
  onApprove?: StaffAvailabilityDecisionHandler;
  onReject?: StaffAvailabilityDecisionHandler;
};

type ExceptionDraft = Omit<StaffAvailabilityException, 'id'>;
type ActionState = 'create' | 'approve' | 'reject' | null;

const WEEKDAYS: ReadonlyArray<{ value: StaffWeekday; label: string; shortLabel: string }> = [
  { value: 0, label: 'Segunda-feira', shortLabel: 'Seg' },
  { value: 1, label: 'Terça-feira', shortLabel: 'Ter' },
  { value: 2, label: 'Quarta-feira', shortLabel: 'Qua' },
  { value: 3, label: 'Quinta-feira', shortLabel: 'Qui' },
  { value: 4, label: 'Sexta-feira', shortLabel: 'Sex' },
  { value: 5, label: 'Sábado', shortLabel: 'Sáb' },
  { value: 6, label: 'Domingo', shortLabel: 'Dom' },
];

const EXCEPTION_LABELS: Record<StaffAvailabilityExceptionKind, string> = {
  vacation: 'Férias',
  holiday: 'Feriado',
  exception: 'Exceção',
};

function createWeeklyAvailability(): StaffDayAvailability[] {
  return WEEKDAYS.map(({ value }) => ({
    weekday: value,
    enabled: value < 5,
    windows: value < 5 ? [{ start: '09:00', end: '18:00' }] : [],
  }));
}

function createDefaultValue(): StaffAvailabilityValue {
  return {
    mode: 'available',
    timezone: 'America/Sao_Paulo',
    weeklyAvailability: createWeeklyAvailability(),
    exceptions: [],
    absenceMessage: {
      enabled: false,
      text: 'No momento estou ausente. Sua mensagem foi recebida e a equipe dará continuidade ao atendimento.',
    },
    activation: { mode: 'now', startsAt: null, endsAt: null },
    redirect: { kind: 'none', targetId: null },
    automaticSwap: false,
    requestNote: '',
  };
}

function createExceptionDraft(): ExceptionDraft {
  return {
    kind: 'vacation',
    name: '',
    startsOn: '',
    endsOn: '',
    mode: 'unavailable',
    windows: [{ start: '09:00', end: '18:00' }],
  };
}

function cloneValue(source?: StaffAvailabilityValue): StaffAvailabilityValue {
  const fallback = createDefaultValue();
  if (!source) return fallback;

  const sourceDays = new Map(source.weeklyAvailability.map((day) => [day.weekday, day]));
  return {
    ...source,
    weeklyAvailability: WEEKDAYS.map(({ value }) => {
      const day = sourceDays.get(value) ?? fallback.weeklyAvailability[value];
      return { ...day, weekday: value, windows: day.windows.map((window) => ({ ...window })) };
    }),
    exceptions: source.exceptions.map((item) => ({
      ...item,
      windows: item.windows.map((window) => ({ ...window })),
    })),
    absenceMessage: { ...source.absenceMessage },
    activation: { ...source.activation },
    redirect: { ...source.redirect } as StaffAvailabilityRedirect,
  };
}

function redirectValue(redirect: StaffAvailabilityRedirect): string {
  return redirect.kind === 'none' ? 'none' : `${redirect.kind}:${redirect.targetId}`;
}

function parseRedirect(value: string): StaffAvailabilityRedirect {
  if (value === 'none') return { kind: 'none', targetId: null };
  const separator = value.indexOf(':');
  const kind = value.slice(0, separator);
  const targetId = value.slice(separator + 1);
  if ((kind === 'user' || kind === 'team') && targetId) return { kind, targetId };
  return { kind: 'none', targetId: null };
}

function formatDate(value: string): string {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

function actionErrorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : 'Não foi possível concluir a ação. Tente novamente.';
}

function windowsAreValid(windows: StaffAvailabilityWindow[]): boolean {
  if (!windows.length) return false;
  const ordered = windows.slice().sort((left, right) => left.start.localeCompare(right.start));
  return ordered.every((window, index) => {
    if (!window.start || !window.end || window.start >= window.end) return false;
    return index === 0 || ordered[index - 1].end <= window.start;
  });
}

function nextAvailabilityWindow(windows: StaffAvailabilityWindow[]): StaffAvailabilityWindow {
  if (!windows.length) return { start: '09:00', end: '18:00' };
  const orderedEnds = windows.map((window) => window.end).filter(Boolean).sort();
  const lastEnd = orderedEnds[orderedEnds.length - 1];
  if (!lastEnd || !/^\d{2}:\d{2}$/.test(lastEnd)) return { start: '14:00', end: '18:00' };
  const [hours, minutes] = lastEnd.split(':').map(Number);
  const startMinutes = (hours * 60) + minutes;
  if (startMinutes >= 1439) return { start: '', end: '' };
  const endMinutes = Math.min(1439, startMinutes + 60);
  const end = `${String(Math.floor(endMinutes / 60)).padStart(2, '0')}:${String(endMinutes % 60).padStart(2, '0')}`;
  return { start: lastEnd, end };
}

function validateDraft(draft: StaffAvailabilityValue): string[] {
  const errors: string[] = [];
  const enabledDays = draft.weeklyAvailability.filter((day) => day.enabled);

  if (!enabledDays.length) errors.push('Ative ao menos um dia na disponibilidade semanal.');
  for (const day of enabledDays) {
    if (!windowsAreValid(day.windows)) {
      const label = WEEKDAYS.find((item) => item.value === day.weekday)?.label ?? 'Dia da semana';
      errors.push(`Revise os horários de ${label}: as faixas devem ser válidas e não podem se sobrepor.`);
    }
  }

  if (draft.activation.mode === 'scheduled') {
    if (!draft.activation.startsAt || !draft.activation.endsAt) {
      errors.push('Informe início e fim para agendar a alteração.');
    } else if (draft.activation.startsAt >= draft.activation.endsAt) {
      errors.push('O fim do agendamento deve ser posterior ao início.');
    }
  }

  if (draft.mode === 'away' && draft.absenceMessage.enabled && draft.absenceMessage.text.trim().length < 3) {
    errors.push('Escreva a mensagem de ausência ou desative o envio da mensagem.');
  }

  if (draft.automaticSwap && draft.redirect.kind === 'none') {
    errors.push('Escolha um usuário ou uma equipe para ativar a troca automática.');
  }

  for (const exception of draft.exceptions) {
    if (!exception.name.trim() || !exception.startsOn || !exception.endsOn || exception.startsOn > exception.endsOn) {
      errors.push(`Revise a exceção “${exception.name || EXCEPTION_LABELS[exception.kind]}”.`);
    }
    if (exception.mode === 'custom_hours' && !windowsAreValid(exception.windows)) {
      errors.push(`Revise o horário personalizado de “${exception.name || EXCEPTION_LABELS[exception.kind]}”.`);
    }
  }

  return errors;
}

function normalizeDraft(draft: StaffAvailabilityValue): StaffAvailabilityValue {
  return {
    ...cloneValue(draft),
    absenceMessage: { ...draft.absenceMessage, text: draft.absenceMessage.text.trim() },
    activation: draft.activation.mode === 'now'
      ? { mode: 'now', startsAt: null, endsAt: null }
      : { ...draft.activation },
    exceptions: draft.exceptions.map((item) => ({
      ...item,
      name: item.name.trim(),
      windows: item.mode === 'unavailable' ? [] : item.windows.map((window) => ({ ...window })),
    })),
    requestNote: draft.requestNote.trim(),
  };
}

export function StaffAvailabilitySheet({
  open,
  onOpenChange,
  staff,
  initialValue,
  redirectOptions = [],
  approval,
  disabled = false,
  onCreateRequest,
  onApprove,
  onReject,
}: StaffAvailabilitySheetProps) {
  const instanceId = useId();
  const [draft, setDraft] = useState<StaffAvailabilityValue>(() => cloneValue(initialValue));
  const [exceptionDraft, setExceptionDraft] = useState<ExceptionDraft>(createExceptionDraft);
  const [exceptionError, setExceptionError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [action, setAction] = useState<ActionState>(null);
  const [submittedLocally, setSubmittedLocally] = useState(false);
  const [localDecision, setLocalDecision] = useState<'approved' | 'rejected' | null>(null);
  const exceptionSequence = useRef(0);
  const errorSummaryRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setDraft(cloneValue(initialValue));
    setExceptionDraft(createExceptionDraft());
    setExceptionError(null);
    setValidationErrors([]);
    setActionError(null);
    setSubmittedLocally(false);
    setLocalDecision(null);
  }, [approval?.id, approval?.status, initialValue, open, staff.id]);

  useEffect(() => {
    if (validationErrors.length || actionError) errorSummaryRef.current?.focus();
  }, [actionError, validationErrors]);

  const users = useMemo(() => redirectOptions.filter((item) => item.kind === 'user'), [redirectOptions]);
  const teams = useMemo(() => redirectOptions.filter((item) => item.kind === 'team'), [redirectOptions]);
  const approvalStatus = localDecision ?? approval?.status;
  const waitingApproval = submittedLocally || approvalStatus === 'pending';
  const formLocked = disabled || action !== null || waitingApproval;

  const changeDraft = (updater: (current: StaffAvailabilityValue) => StaffAvailabilityValue) => {
    setDraft(updater);
    setValidationErrors([]);
    setActionError(null);
  };

  const updateDay = (weekday: StaffWeekday, updater: (day: StaffDayAvailability) => StaffDayAvailability) => {
    changeDraft((current) => ({
      ...current,
      weeklyAvailability: current.weeklyAvailability.map((day) => day.weekday === weekday ? updater(day) : day),
    }));
  };

  const applyMondayToWeekdays = () => {
    changeDraft((current) => {
      const monday = current.weeklyAvailability.find((day) => day.weekday === 0);
      if (!monday) return current;
      return {
        ...current,
        weeklyAvailability: current.weeklyAvailability.map((day) => day.weekday < 5
          ? { ...day, enabled: monday.enabled, windows: monday.windows.map((window) => ({ ...window })) }
          : day),
      };
    });
  };

  const addException = () => {
    if (!exceptionDraft.name.trim()) {
      setExceptionError('Dê um nome à ausência ou exceção.');
      return;
    }
    if (!exceptionDraft.startsOn || !exceptionDraft.endsOn) {
      setExceptionError('Informe a data inicial e a data final.');
      return;
    }
    if (exceptionDraft.startsOn > exceptionDraft.endsOn) {
      setExceptionError('A data final deve ser igual ou posterior à data inicial.');
      return;
    }
    if (exceptionDraft.mode === 'custom_hours' && !windowsAreValid(exceptionDraft.windows)) {
      setExceptionError('O horário personalizado precisa ter início anterior ao fim.');
      return;
    }

    exceptionSequence.current += 1;
    const item: StaffAvailabilityException = {
      ...exceptionDraft,
      id: `staff-exception-${staff.id}-${exceptionSequence.current}`,
      name: exceptionDraft.name.trim(),
      windows: exceptionDraft.mode === 'unavailable' ? [] : exceptionDraft.windows.map((window) => ({ ...window })),
    };
    changeDraft((current) => ({ ...current, exceptions: [...current.exceptions, item] }));
    setExceptionDraft(createExceptionDraft());
    setExceptionError(null);
  };

  const createRequest = async () => {
    const errors = validateDraft(draft);
    if (errors.length) {
      setValidationErrors(errors);
      return;
    }

    setAction('create');
    setActionError(null);
    try {
      const normalized = normalizeDraft(draft);
      await onCreateRequest({ staffId: staff.id, ...normalized });
      setDraft(normalized);
      setSubmittedLocally(true);
    } catch (error) {
      setActionError(actionErrorMessage(error));
    } finally {
      setAction(null);
    }
  };

  const decide = async (decision: 'approve' | 'reject') => {
    if (!approval) return;
    const handler = decision === 'approve' ? onApprove : onReject;
    if (!handler) return;

    setAction(decision);
    setActionError(null);
    try {
      await handler({ requestId: approval.id, staffId: staff.id });
      setLocalDecision(decision === 'approve' ? 'approved' : 'rejected');
    } catch (error) {
      setActionError(actionErrorMessage(error));
    } finally {
      setAction(null);
    }
  };

  const statusContent = waitingApproval
    ? {
      tone: 'gold' as const,
      icon: ShieldCheck,
      label: 'Aguardando aprovação',
      description: 'O pedido foi registrado e nenhuma alteração entra em vigor antes da decisão humana.',
    }
    : approvalStatus === 'approved'
      ? {
        tone: 'green' as const,
        icon: CheckCircle2,
        label: 'Pedido aprovado',
        description: 'A decisão foi confirmada. O sistema integrador pode aplicar a configuração aprovada.',
      }
      : approvalStatus === 'rejected'
        ? {
          tone: 'red' as const,
          icon: XCircle,
          label: 'Pedido rejeitado',
          description: 'A configuração não foi aplicada. Revise os dados antes de criar um novo pedido.',
        }
        : null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex h-dvh w-screen max-w-none flex-col overflow-hidden p-0 sm:w-[min(94vw,760px)] sm:max-w-[760px]">
        <SheetHeader className="shrink-0 border-b border-border px-4 py-4 pr-12 text-left sm:px-6 sm:py-5">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-md bg-emerald-700 text-white dark:bg-emerald-600">
              <CalendarClock className="h-4 w-4" aria-hidden="true" />
            </div>
            <KlmPill tone={draft.mode === 'available' ? 'green' : 'gold'}>
              {draft.mode === 'available' ? 'Disponível' : 'Ausente'}
            </KlmPill>
          </div>
          <SheetTitle>Disponibilidade de {staff.name}</SheetTitle>
          <SheetDescription>
            {staff.role ? `${staff.role} · ` : ''}Organize horários, ausências e cobertura sem sair desta tela. Alterações dependem de aprovação.
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-5 scrollbar-thin sm:px-6">
          {statusContent ? (
            <div
              role="status"
              aria-live="polite"
              className={cn(
                'flex items-start gap-3 rounded-md border p-4',
                statusContent.tone === 'gold' && 'border-primary/35 bg-primary/10',
                statusContent.tone === 'green' && 'border-emerald-600/25 bg-emerald-500/10',
                statusContent.tone === 'red' && 'border-red-700/25 bg-red-500/10',
              )}
            >
              <statusContent.icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <div className="min-w-0">
                <KlmPill tone={statusContent.tone}>{statusContent.label}</KlmPill>
                <p className="mt-2 text-2xs leading-4 text-muted-foreground">{statusContent.description}</p>
                {approval?.requestedBy || approval?.requestedAt ? (
                  <p className="mt-1 text-2xs text-muted-foreground">
                    {[approval.requestedBy, approval.requestedAt ? `enviado em ${new Date(approval.requestedAt).toLocaleString('pt-BR')}` : null].filter(Boolean).join(' · ')}
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {(validationErrors.length > 0 || actionError) ? (
            <div
              ref={errorSummaryRef}
              role="alert"
              tabIndex={-1}
              className="rounded-md border border-red-700/25 bg-red-500/10 p-3 text-2xs leading-4 text-[#a53e3c] outline-none dark:text-red-300"
            >
              <strong className="block">Revise antes de continuar</strong>
              {actionError ? <p className="mt-1">{actionError}</p> : null}
              {validationErrors.length ? (
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {validationErrors.map((error) => <li key={error}>{error}</li>)}
                </ul>
              ) : null}
            </div>
          ) : null}

          <section aria-labelledby={`${instanceId}-availability-status-title`} className="rounded-lg border border-border bg-card p-4">
            <div>
              <h3 id={`${instanceId}-availability-status-title`} className="text-sm font-semibold">Estado solicitado</h3>
              <p className="mt-1 text-2xs leading-4 text-muted-foreground">Escolha o estado com um clique. A solicitação só entra em vigor após aprovação.</p>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Estado de disponibilidade">
              <label
                className={cn(
                  'min-h-20 cursor-pointer rounded-md border p-3 text-left transition has-[:focus-visible]:outline-none has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
                  draft.mode === 'available' ? 'border-emerald-600/50 bg-emerald-500/10' : 'border-border hover:border-emerald-600/35',
                  formLocked && 'cursor-not-allowed opacity-50',
                )}
              >
                <input className="sr-only" type="radio" name={`${instanceId}-availability-mode`} value="available" checked={draft.mode === 'available'} disabled={formLocked} onChange={() => changeDraft((current) => ({ ...current, mode: 'available' }))} />
                <span className="flex items-center gap-2 text-xs font-semibold"><CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />Disponível</span>
                <span className="mt-1 block text-2xs leading-4 text-muted-foreground">Receber novos atendimentos conforme os horários semanais.</span>
              </label>
              <label
                className={cn(
                  'min-h-20 cursor-pointer rounded-md border p-3 text-left transition has-[:focus-visible]:outline-none has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
                  draft.mode === 'away' ? 'border-primary/60 bg-primary/10' : 'border-border hover:border-primary/40',
                  formLocked && 'cursor-not-allowed opacity-50',
                )}
              >
                <input className="sr-only" type="radio" name={`${instanceId}-availability-mode`} value="away" checked={draft.mode === 'away'} disabled={formLocked} onChange={() => changeDraft((current) => ({ ...current, mode: 'away' }))} />
                <span className="flex items-center gap-2 text-xs font-semibold"><CalendarOff className="h-4 w-4 text-[#0057d8]" aria-hidden="true" />Ausente</span>
                <span className="mt-1 block text-2xs leading-4 text-muted-foreground">Pausar novas atribuições e ativar a cobertura definida abaixo.</span>
              </label>
            </div>
          </section>

          <section aria-labelledby={`${instanceId}-activation-title`} className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-start gap-3">
              <Clock3 className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
              <div><h3 id={`${instanceId}-activation-title`} className="text-sm font-semibold">Agendamento</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">Aplique após a aprovação ou programe um período completo.</p></div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2" role="radiogroup" aria-label="Momento da alteração">
              {([
                { value: 'now' as const, label: 'Após aprovação' },
                { value: 'scheduled' as const, label: 'Agendar período' },
              ]).map((item) => (
                <label
                  key={item.value}
                  className={cn(
                    'flex min-h-10 cursor-pointer items-center justify-center rounded-md border px-3 py-2 text-2xs font-semibold has-[:focus-visible]:outline-none has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
                    draft.activation.mode === item.value ? 'border-[#031121] bg-[#031121] text-white dark:border-white dark:bg-white dark:text-black' : 'border-border bg-background hover:bg-secondary',
                    formLocked && 'cursor-not-allowed opacity-50',
                  )}
                >
                  <input
                    className="sr-only"
                    type="radio"
                    name={`${instanceId}-activation-mode`}
                    value={item.value}
                    checked={draft.activation.mode === item.value}
                    disabled={formLocked}
                    onChange={() => changeDraft((current) => ({
                      ...current,
                      activation: item.value === 'now'
                        ? { mode: 'now', startsAt: null, endsAt: null }
                        : { mode: 'scheduled', startsAt: current.activation.startsAt, endsAt: current.activation.endsAt },
                    }))}
                  />
                  {item.label}
                </label>
              ))}
            </div>
            {draft.activation.mode === 'scheduled' ? (
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5"><Label htmlFor={`${instanceId}-availability-start`}>Início</Label><Input id={`${instanceId}-availability-start`} type="datetime-local" disabled={formLocked} value={draft.activation.startsAt ?? ''} onChange={(event) => changeDraft((current) => ({ ...current, activation: { ...current.activation, startsAt: event.target.value || null } }))} /></div>
                <div className="space-y-1.5"><Label htmlFor={`${instanceId}-availability-end`}>Fim</Label><Input id={`${instanceId}-availability-end`} type="datetime-local" disabled={formLocked} value={draft.activation.endsAt ?? ''} onChange={(event) => changeDraft((current) => ({ ...current, activation: { ...current.activation, endsAt: event.target.value || null } }))} /></div>
              </div>
            ) : null}
          </section>

          <section aria-labelledby={`${instanceId}-weekly-title`} className="overflow-hidden rounded-lg border border-border bg-card">
            <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between">
              <div><h3 id={`${instanceId}-weekly-title`} className="text-sm font-semibold">Horários semanais</h3><p className="mt-1 text-2xs text-muted-foreground">Fuso: {draft.timezone}</p></div>
              <Button type="button" variant="outline" size="sm" className="h-9 text-2xs" disabled={formLocked} onClick={applyMondayToWeekdays}><Copy className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />Aplicar segunda a sexta</Button>
            </div>
            <div className="divide-y divide-border">
              {WEEKDAYS.map(({ value, label, shortLabel }) => {
                const day = draft.weeklyAvailability.find((item) => item.weekday === value)!;
                return (
                  <div key={value} className="grid gap-3 p-4 sm:grid-cols-[132px_minmax(0,1fr)]">
                    <div className="flex items-center gap-3 self-start sm:min-h-10">
                      <Switch className="data-[state=checked]:bg-emerald-700 dark:data-[state=checked]:bg-emerald-600" id={`${instanceId}-weekday-${value}`} checked={day.enabled} disabled={formLocked} onCheckedChange={(enabled) => updateDay(value, (current) => ({ ...current, enabled: Boolean(enabled), windows: enabled && !current.windows.length ? [{ start: '09:00', end: '18:00' }] : current.windows }))} />
                      <Label htmlFor={`${instanceId}-weekday-${value}`} className="text-2xs font-semibold"><span className="sm:hidden">{shortLabel}</span><span className="hidden sm:inline">{label}</span></Label>
                    </div>
                    {day.enabled ? (
                      <div className="space-y-2">
                        {day.windows.map((window, index) => (
                          <div key={`${value}-${index}`} className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_40px] items-center gap-2">
                            <Input aria-label={`${label}, início do horário ${index + 1}`} type="time" disabled={formLocked} value={window.start} onChange={(event) => updateDay(value, (current) => ({ ...current, windows: current.windows.map((item, itemIndex) => itemIndex === index ? { ...item, start: event.target.value } : item) }))} />
                            <span className="text-2xs text-muted-foreground">até</span>
                            <Input aria-label={`${label}, fim do horário ${index + 1}`} type="time" disabled={formLocked} value={window.end} onChange={(event) => updateDay(value, (current) => ({ ...current, windows: current.windows.map((item, itemIndex) => itemIndex === index ? { ...item, end: event.target.value } : item) }))} />
                            <Button type="button" variant="ghost" size="icon" className="h-10 w-10 text-muted-foreground hover:text-[#a53e3c]" disabled={formLocked} aria-label={`Remover horário ${index + 1} de ${label}`} onClick={() => updateDay(value, (current) => ({ ...current, windows: current.windows.filter((_, itemIndex) => itemIndex !== index) }))}><Trash2 className="h-3.5 w-3.5" aria-hidden="true" /></Button>
                          </div>
                        ))}
                        <Button type="button" variant="ghost" size="sm" className="h-8 px-2 text-2xs" disabled={formLocked} onClick={() => updateDay(value, (current) => ({ ...current, windows: [...current.windows, nextAvailabilityWindow(current.windows)] }))}><Plus className="mr-1 h-3 w-3" aria-hidden="true" />Adicionar faixa</Button>
                      </div>
                    ) : <p className="self-center text-2xs text-muted-foreground">Sem expediente</p>}
                  </div>
                );
              })}
            </div>
          </section>

          <section aria-labelledby={`${instanceId}-exceptions-title`} className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-start gap-3"><CalendarOff className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden="true" /><div><h3 id={`${instanceId}-exceptions-title`} className="text-sm font-semibold">Férias, feriados e exceções</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">Períodos indisponíveis têm prioridade sobre o horário semanal.</p></div></div>

            {draft.exceptions.length ? (
              <div className="mt-3 space-y-2" aria-label="Exceções adicionadas">
                {draft.exceptions.map((item) => (
                  <div key={item.id} className="flex items-start gap-3 rounded-md border border-border p-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2"><strong className="text-2xs">{item.name}</strong><KlmPill tone="muted">{EXCEPTION_LABELS[item.kind]}</KlmPill></div>
                      <p className="mt-1 text-2xs text-muted-foreground">{formatDate(item.startsOn)} a {formatDate(item.endsOn)} · {item.mode === 'unavailable' ? 'dia inteiro indisponível' : item.windows.map((window) => `${window.start}–${window.end}`).join(', ')}</p>
                    </div>
                    <Button type="button" variant="ghost" size="icon" className="h-9 w-9 shrink-0 text-muted-foreground hover:text-[#a53e3c]" disabled={formLocked} aria-label={`Remover ${item.name}`} onClick={() => changeDraft((current) => ({ ...current, exceptions: current.exceptions.filter((exception) => exception.id !== item.id) }))}><Trash2 className="h-3.5 w-3.5" aria-hidden="true" /></Button>
                  </div>
                ))}
              </div>
            ) : <p className="mt-3 rounded-md border border-dashed border-border p-3 text-center text-2xs text-muted-foreground">Nenhuma exceção adicionada.</p>}

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Tipo</Label>
                <Select disabled={formLocked} value={exceptionDraft.kind} onValueChange={(value) => { setExceptionDraft((current) => ({ ...current, kind: value as StaffAvailabilityExceptionKind })); setExceptionError(null); }}>
                  <SelectTrigger aria-label="Tipo de ausência ou exceção"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="vacation">Férias</SelectItem>
                    <SelectItem value="holiday">Feriado</SelectItem>
                    <SelectItem value="exception">Exceção</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5"><Label htmlFor={`${instanceId}-exception-name`}>Nome</Label><Input id={`${instanceId}-exception-name`} disabled={formLocked} value={exceptionDraft.name} placeholder="Ex.: Recesso de fim de ano" onChange={(event) => { setExceptionDraft((current) => ({ ...current, name: event.target.value })); setExceptionError(null); }} /></div>
              <div className="space-y-1.5"><Label htmlFor={`${instanceId}-exception-start`}>Data inicial</Label><Input id={`${instanceId}-exception-start`} type="date" disabled={formLocked} value={exceptionDraft.startsOn} onChange={(event) => { const startsOn = event.target.value; setExceptionDraft((current) => ({ ...current, startsOn, endsOn: !current.endsOn || current.endsOn < startsOn ? startsOn : current.endsOn })); setExceptionError(null); }} /></div>
              <div className="space-y-1.5"><Label htmlFor={`${instanceId}-exception-end`}>Data final</Label><Input id={`${instanceId}-exception-end`} type="date" disabled={formLocked} min={exceptionDraft.startsOn || undefined} value={exceptionDraft.endsOn} onChange={(event) => { setExceptionDraft((current) => ({ ...current, endsOn: event.target.value })); setExceptionError(null); }} /></div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label>Disponibilidade no período</Label>
                <Select disabled={formLocked} value={exceptionDraft.mode} onValueChange={(value) => { setExceptionDraft((current) => ({ ...current, mode: value as StaffAvailabilityExceptionMode })); setExceptionError(null); }}>
                  <SelectTrigger aria-label="Disponibilidade da exceção"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="unavailable">Indisponível o dia inteiro</SelectItem><SelectItem value="custom_hours">Usar horário personalizado</SelectItem></SelectContent>
                </Select>
              </div>
              {exceptionDraft.mode === 'custom_hours' ? (
                <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-2 sm:col-span-2">
                  <div className="space-y-1.5"><Label htmlFor={`${instanceId}-exception-time-start`}>Início</Label><Input id={`${instanceId}-exception-time-start`} type="time" disabled={formLocked} value={exceptionDraft.windows[0]?.start ?? ''} onChange={(event) => setExceptionDraft((current) => ({ ...current, windows: [{ start: event.target.value, end: current.windows[0]?.end ?? '' }] }))} /></div>
                  <span className="pb-3 text-2xs text-muted-foreground">até</span>
                  <div className="space-y-1.5"><Label htmlFor={`${instanceId}-exception-time-end`}>Fim</Label><Input id={`${instanceId}-exception-time-end`} type="time" disabled={formLocked} value={exceptionDraft.windows[0]?.end ?? ''} onChange={(event) => setExceptionDraft((current) => ({ ...current, windows: [{ start: current.windows[0]?.start ?? '', end: event.target.value }] }))} /></div>
                </div>
              ) : null}
            </div>
            {exceptionError ? <p role="alert" className="mt-3 text-2xs font-medium text-[#a53e3c] dark:text-red-400">{exceptionError}</p> : null}
            <Button type="button" variant="outline" className="mt-3 h-9 w-full text-2xs" disabled={formLocked} onClick={addException}><Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />Adicionar ausência ou exceção</Button>
          </section>

          <section aria-labelledby={`${instanceId}-message-title`} className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-3"><MessageSquareText className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden="true" /><div><h3 id={`${instanceId}-message-title`} className="text-sm font-semibold">Mensagem de ausência</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">Resposta preparada para informar continuidade sem prometer um prazo inexistente.</p></div></div>
              <Switch id={`${instanceId}-absence-message-enabled`} checked={draft.absenceMessage.enabled} disabled={formLocked} onCheckedChange={(enabled) => changeDraft((current) => ({ ...current, absenceMessage: { ...current.absenceMessage, enabled: Boolean(enabled) } }))} />
            </div>
            <Label htmlFor={`${instanceId}-absence-message-enabled`} className="sr-only">Ativar mensagem de ausência</Label>
            <div className="mt-3 space-y-1.5"><Label htmlFor={`${instanceId}-absence-message`}>Texto da mensagem</Label><Textarea id={`${instanceId}-absence-message`} rows={4} maxLength={800} disabled={formLocked || !draft.absenceMessage.enabled} value={draft.absenceMessage.text} onChange={(event) => changeDraft((current) => ({ ...current, absenceMessage: { ...current.absenceMessage, text: event.target.value } }))} /><p className="text-right text-2xs text-muted-foreground">{draft.absenceMessage.text.length}/800</p></div>
          </section>

          <section aria-labelledby={`${instanceId}-routing-title`} className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-start gap-3"><Route className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden="true" /><div><h3 id={`${instanceId}-routing-title`} className="text-sm font-semibold">Redirecionamento e cobertura</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">Encaminhe novos atendimentos para uma pessoa ou equipe sem trocar de tela.</p></div></div>
            <div className="mt-3 space-y-1.5">
              <Label>Destino durante a ausência</Label>
              <Select disabled={formLocked} value={redirectValue(draft.redirect)} onValueChange={(value) => changeDraft((current) => ({ ...current, redirect: parseRedirect(value), automaticSwap: value === 'none' ? false : current.automaticSwap }))}>
                <SelectTrigger aria-label="Destino durante a ausência"><SelectValue placeholder="Sem redirecionamento" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Sem redirecionamento</SelectItem>
                  {users.length ? <SelectGroup><SelectLabel>Usuários</SelectLabel>{users.map((option) => <SelectItem key={`user-${option.id}`} value={`user:${option.id}`} disabled={option.disabled}>{option.label}{option.description ? ` · ${option.description}` : ''}</SelectItem>)}</SelectGroup> : null}
                  {teams.length ? <SelectGroup><SelectLabel>Equipes</SelectLabel>{teams.map((option) => <SelectItem key={`team-${option.id}`} value={`team:${option.id}`} disabled={option.disabled}>{option.label}{option.description ? ` · ${option.description}` : ''}</SelectItem>)}</SelectGroup> : null}
                </SelectContent>
              </Select>
            </div>
            <div className="mt-3 flex items-start justify-between gap-4 rounded-md border border-border bg-secondary/40 p-3">
              <div className="flex items-start gap-3"><Repeat2 className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden="true" /><div><Label htmlFor={`${instanceId}-automatic-swap`} className="text-2xs font-semibold">Troca automática</Label><p className="mt-1 text-2xs leading-4 text-muted-foreground">Redistribui automaticamente enquanto a ausência estiver ativa; desligue para apenas registrar o destino.</p></div></div>
              <Switch id={`${instanceId}-automatic-swap`} checked={draft.automaticSwap} disabled={formLocked || draft.redirect.kind === 'none'} onCheckedChange={(enabled) => changeDraft((current) => ({ ...current, automaticSwap: Boolean(enabled) }))} />
            </div>
          </section>

          <section aria-labelledby={`${instanceId}-request-note-title`} className="rounded-lg border border-border bg-card p-4">
            <h3 id={`${instanceId}-request-note-title`} className="text-sm font-semibold">Nota para aprovação</h3>
            <p className="mt-1 text-2xs leading-4 text-muted-foreground">Contexto interno opcional para quem decidir o pedido.</p>
            <div className="mt-3"><Label htmlFor={`${instanceId}-request-note`} className="sr-only">Nota para aprovação</Label><Textarea id={`${instanceId}-request-note`} rows={3} maxLength={1000} disabled={formLocked} value={draft.requestNote} placeholder="Ex.: cobertura combinada com a equipe de plantão." onChange={(event) => changeDraft((current) => ({ ...current, requestNote: event.target.value }))} /></div>
          </section>
        </div>

        <SheetFooter className="shrink-0 gap-2 border-t border-border bg-card px-4 py-4 sm:px-6">
          <Button type="button" variant="outline" disabled={action !== null} onClick={() => onOpenChange(false)}>{waitingApproval || approvalStatus ? 'Fechar' : 'Cancelar'}</Button>
          {waitingApproval ? (
            approval?.status === 'pending' && (onApprove || onReject) ? (
              <>
                {onReject ? <Button type="button" variant="outline" className="border-red-700/30 text-[#a53e3c] hover:bg-red-500/10 hover:text-[#a53e3c]" disabled={action !== null || disabled} onClick={() => void decide('reject')}>{action === 'reject' ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> : <XCircle className="mr-2 h-4 w-4" aria-hidden="true" />}{action === 'reject' ? 'Rejeitando' : 'Rejeitar'}</Button> : null}
                {onApprove ? <Button type="button" className="bg-[#0b7435] text-white hover:bg-[#09612d]" disabled={action !== null || disabled} onClick={() => void decide('approve')}>{action === 'approve' ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> : <CheckCircle2 className="mr-2 h-4 w-4" aria-hidden="true" />}{action === 'approve' ? 'Aprovando' : 'Aprovar'}</Button> : null}
              </>
            ) : <Button type="button" disabled><ShieldCheck className="mr-2 h-4 w-4" aria-hidden="true" />Aguardando aprovação</Button>
          ) : (
            <Button type="button" className="bg-[#031121] text-white hover:bg-[#031121]" disabled={disabled || action !== null} onClick={() => void createRequest()}>{action === 'create' ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> : <Send className="mr-2 h-4 w-4" aria-hidden="true" />}{action === 'create' ? 'Criando pedido' : 'Criar pedido de alteração'}</Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
