import { apiFetch } from '@/services/http';

export type SupportAvailabilityWeekday =
  | 'monday'
  | 'tuesday'
  | 'wednesday'
  | 'thursday'
  | 'friday'
  | 'saturday'
  | 'sunday';

export interface SupportAvailabilityDay {
  day: SupportAvailabilityWeekday;
  enabled: boolean;
  startTime: string;
  endTime: string;
}

export interface PlatformSupportAvailabilitySettings {
  enabled: boolean;
  timezone: string;
  days: SupportAvailabilityDay[];
  offlineMessage: string;
  etag?: string | null;
  version: number;
  updatedAt?: string | null;
  currentState: 'online' | 'offline';
  nextChangeAt?: string | null;
}

export interface PlatformSupportAvailabilityStatus {
  state: 'online' | 'offline';
  message: string;
}

export type PlatformSupportAvailabilityDraft = Pick<
  PlatformSupportAvailabilitySettings,
  'enabled' | 'timezone' | 'days' | 'offlineMessage'
>;

export function getPlatformSupportAvailabilitySettings() {
  return apiFetch<PlatformSupportAvailabilitySettings>(
    '/api/v1/platform/support/availability-settings',
  );
}

export function getPlatformSupportAvailabilityStatus() {
  return apiFetch<PlatformSupportAvailabilityStatus>(
    '/api/v1/support/availability',
  );
}

export function updatePlatformSupportAvailabilitySettings(
  settings: PlatformSupportAvailabilityDraft,
  expectedEtag: string | null,
  reason: string,
) {
  return apiFetch<PlatformSupportAvailabilitySettings>(
    '/api/v1/platform/support/availability-settings',
    {
      method: 'PUT',
      headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
      body: JSON.stringify({
        ...settings,
        expectedEtag,
      }),
    },
  );
}

export type SupportEscalationBusinessDay = SupportAvailabilityWeekday;

export interface PlatformSupportEscalationSettings {
  enabled: boolean;
  schedulingUrl?: string | null;
  whatsappNumber?: string | null;
  businessDays: SupportEscalationBusinessDay[];
  businessHoursStart: string;
  businessHoursEnd: string;
  etag?: string | null;
  version: number;
  updatedAt?: string | null;
}

export interface PlatformSupportEscalationStatus {
  availableNow: boolean;
  schedulingUrl?: string | null;
  whatsappLink?: string | null;
  nextWindow?: string | null;
}

export type PlatformSupportEscalationDraft = Pick<
  PlatformSupportEscalationSettings,
  'enabled' | 'schedulingUrl' | 'whatsappNumber' | 'businessDays' | 'businessHoursStart' | 'businessHoursEnd'
>;

export function getPlatformSupportEscalationSettings() {
  return apiFetch<PlatformSupportEscalationSettings>(
    '/api/v1/platform/support/escalation-settings',
  );
}

export function updatePlatformSupportEscalationSettings(
  settings: PlatformSupportEscalationDraft,
  expectedEtag: string | null,
  reason: string,
) {
  return apiFetch<PlatformSupportEscalationSettings>(
    '/api/v1/platform/support/escalation-settings',
    {
      method: 'PUT',
      headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
      body: JSON.stringify({
        ...settings,
        expectedEtag,
      }),
    },
  );
}

export function getSupportEscalationStatus() {
  return apiFetch<PlatformSupportEscalationStatus>(
    '/api/v1/support/escalation-status',
  );
}
