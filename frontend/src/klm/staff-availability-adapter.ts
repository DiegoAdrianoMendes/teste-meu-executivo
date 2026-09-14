import type {
  KlmStaffAvailabilityState,
  KlmStaffEffectiveAvailability,
} from '@/klm/api';
import type {
  StaffAvailabilityException,
  StaffAvailabilityValue,
} from '@/klm/components/StaffAvailabilitySheet';

function localDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function localTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '09:00';
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function localDateTime(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function exceptionValue(row: KlmStaffAvailabilityState['exceptions'][number]): StaffAvailabilityException {
  const customHours = row.kind === 'available_override';
  return {
    id: row.id,
    kind: row.kind === 'vacation' ? 'vacation' : row.kind === 'holiday' ? 'holiday' : 'exception',
    name: row.title,
    startsOn: localDate(row.startsAt),
    endsOn: localDate(row.endsAt),
    mode: customHours ? 'custom_hours' : 'unavailable',
    windows: customHours ? [{ start: localTime(row.startsAt), end: localTime(row.endsAt) }] : [],
  };
}

export function staffAvailabilityValueFromApi(
  state: KlmStaffAvailabilityState | undefined,
  effective: KlmStaffEffectiveAvailability | undefined,
): StaffAvailabilityValue | undefined {
  if (!state) return undefined;
  const profile = state.profile;
  const weeklyAvailability = Array.from({ length: 7 }, (_, weekday) => {
    const slots = state.recurringSlots.filter((slot) => slot.weekday === weekday);
    return {
      weekday: weekday as 0 | 1 | 2 | 3 | 4 | 5 | 6,
      enabled: slots.length > 0,
      windows: slots.map((slot) => ({ start: slot.startsAt.slice(0, 5), end: slot.endsAt.slice(0, 5) })),
    };
  });
  const scheduledStart = profile?.availabilityStartsAt ?? null;
  const scheduledEnd = profile?.availabilityEndsAt ?? null;
  const redirect = profile?.redirectKind === 'user' && profile.redirectUserId
    ? { kind: 'user' as const, targetId: profile.redirectUserId }
    : profile?.redirectKind === 'team' && profile.redirectTeamId
      ? { kind: 'team' as const, targetId: profile.redirectTeamId }
      : { kind: 'none' as const, targetId: null };

  return {
    mode: profile?.availabilityMode ?? (effective && !effective.isAvailable ? 'away' : 'available'),
    timezone: profile?.timezone ?? 'America/Sao_Paulo',
    weeklyAvailability,
    exceptions: state.exceptions.filter((row) => !row.deletedAt).map(exceptionValue),
    absenceMessage: {
      enabled: Boolean(profile?.absenceMessage),
      text: profile?.absenceMessage ?? 'No momento estou ausente. Sua mensagem foi recebida e a equipe dará continuidade ao atendimento.',
    },
    activation: scheduledStart && scheduledEnd
      ? { mode: 'scheduled', startsAt: localDateTime(scheduledStart), endsAt: localDateTime(scheduledEnd) }
      : { mode: 'now', startsAt: null, endsAt: null },
    redirect,
    automaticSwap: Boolean(profile?.autoReassignEnabled),
    requestNote: '',
  };
}

export function browserLocalDateTimeToIso(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
