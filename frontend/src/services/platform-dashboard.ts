import { apiFetch } from '@/services/http';

export type PlatformDashboardScope = 'mine' | 'team' | 'platform';
export type PlatformDashboardQuality =
  | 'updated'
  | 'partial'
  | 'estimated'
  | 'stale'
  | 'unavailable'
  | 'insufficient_data';

export interface PlatformDashboardMetric {
  key: string;
  label: string;
  value: number | null;
  unit: 'count' | 'currency_brl' | 'percentage' | string;
  quality: PlatformDashboardQuality;
  source: string;
  updatedAt: string;
  href: string | null;
  description: string | null;
}

export interface PlatformDashboardWorkItem {
  id: string;
  sourceType: string;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  safeTitle: string;
  safeSummary: string;
  href: string;
  dueAt: string | null;
}

export interface PlatformDashboardAlert {
  id: string;
  severity: 'critical' | 'warning' | 'info';
  title: string;
  description: string;
  href: string | null;
}

export interface PlatformDashboardAction {
  key: string;
  label: string;
  href: string;
  module: string;
  action: string;
}

export interface PlatformDashboardSourceHealth {
  key: string;
  label: string;
  status: 'available' | 'partial' | 'unavailable';
  updatedAt: string;
  detail: string | null;
}

export interface PlatformDashboardHome {
  role: string;
  roleLabel: string;
  isPlatformRoot: boolean;
  scope: PlatformDashboardScope;
  availableScopes: PlatformDashboardScope[];
  teamId: string | null;
  teamName: string | null;
  periodDays: number;
  generatedAt: string;
  layout: {
    templateKey: string;
    templateVersion: number;
    mandatoryWidgets: string[];
    optionalWidgets: string[];
    hiddenWidgets: string[];
    widgetOrder: string[];
    preferenceEtag: string | null;
  };
  metrics: PlatformDashboardMetric[];
  workItems: PlatformDashboardWorkItem[];
  alerts: PlatformDashboardAlert[];
  actions: PlatformDashboardAction[];
  sourceHealth: PlatformDashboardSourceHealth[];
}

export function savePlatformDashboardPreference(input: {
  templateKey: string;
  templateVersion: number;
  hiddenWidgets: string[];
  widgetOrder: string[];
  etag: string | null;
}): Promise<{ etag: string }> {
  return apiFetch('/api/v1/platform/organization/dashboard-preference', {
    method: 'PUT',
    body: JSON.stringify({
      templateKey: input.templateKey,
      templateVersion: input.templateVersion,
      preferenceDelta: {
        hiddenWidgets: input.hiddenWidgets,
        widgetOrder: input.widgetOrder,
      },
      etag: input.etag,
    }),
  });
}

export function fetchPlatformDashboardHome(
  scope: PlatformDashboardScope,
  periodDays = 30,
): Promise<PlatformDashboardHome> {
  const params = new URLSearchParams({ scope, periodDays: String(periodDays) });
  return apiFetch(`/api/v1/platform/dashboard/home?${params.toString()}`);
}
