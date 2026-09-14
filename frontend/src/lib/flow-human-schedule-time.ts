/**
 * Converts the browser wall-time accepted by `datetime-local` into the
 * timezone-aware value required by MeetingCreate. Invalid or DST-skipped local
 * times fail closed rather than being silently shifted by the browser.
 */
export function humanScheduleLocalToIso(value: string): string | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute] = match;
  const parsed = new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute));
  if (Number.isNaN(parsed.getTime()) || parsed.getFullYear() !== Number(year) || parsed.getMonth() !== Number(month) - 1 || parsed.getDate() !== Number(day) || parsed.getHours() !== Number(hour) || parsed.getMinutes() !== Number(minute)) return null;
  return parsed.toISOString();
}

/** Hydrates a local input from the UTC/offset value persisted in a flow. */
export function humanScheduleIsoToLocal(value: unknown): string {
  if (typeof value !== 'string' || !value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}
