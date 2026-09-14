import { parsePhoneNumberFromString, type CountryCode } from 'libphonenumber-js/max';

export const META_PHONE_EXAMPLE = '5561999999999';
export const META_PHONE_HELP = `Formato Meta: DDI + DDD + número, somente dígitos. Ex.: ${META_PHONE_EXAMPLE}.`;

export interface WhatsAppPhoneNumber {
  canonical: string;
  international: string;
}

export function parseWhatsAppPhone(
  value: unknown,
  defaultCountry: CountryCode = 'BR',
): WhatsAppPhoneNumber | null {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  const digits = raw.replace(/\D/g, '');
  if (!digits) return null;

  const candidates = raw.startsWith('+')
    ? [`+${digits}`]
    : raw.startsWith('00') && digits.length > 2
      ? [`+${digits.slice(2)}`]
      : digits.length >= 12
        ? [`+${digits}`]
        : [digits, `+${digits}`];

  for (const candidate of candidates) {
    const parsed = parsePhoneNumberFromString(
      candidate,
      candidate.startsWith('+') ? undefined : defaultCountry,
    );
    if (!parsed?.isValid() || parsed.ext) continue;
    const canonical = parsed.number.slice(1);
    if (canonical.length > 15) continue;
    return { canonical, international: parsed.formatInternational() };
  }
  return null;
}

export function normalizeWhatsAppPhone(value: unknown): string | null {
  return parseWhatsAppPhone(value)?.canonical ?? null;
}

export function requireWhatsAppPhone(value: unknown): string {
  const normalized = normalizeWhatsAppPhone(value);
  if (!normalized) {
    throw new Error(`Informe DDI, DDD e número válidos. ${META_PHONE_HELP}`);
  }
  return normalized;
}

export function formatWhatsAppPhoneDisplay(value: unknown): string {
  return parseWhatsAppPhone(value)?.international ?? String(value ?? '');
}
