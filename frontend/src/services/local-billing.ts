import { apiFetch } from '@/services/http';

export type LocalPlanTier = 'free' | 'starter' | 'pro' | 'business' | 'enterprise';
export type LocalBillingInterval = 'monthly' | 'annually';
export type LocalSubscriptionStatus = 'trialing' | 'active' | 'past_due' | 'cancelled' | 'unpaid' | 'paused';

export interface LocalPlanEntitlement {
  entitlement: string;
  enabled: boolean;
  limitQuantity: number | null;
  hardLimit: boolean;
  source: string;
  configured: boolean;
}

export interface LocalPlanCatalogItem {
  id: string;
  name: string;
  tier: LocalPlanTier;
  description?: string | null;
  priceMonthlyCents: number;
  priceAnnuallyCents: number;
  currency: string;
  maxMatters?: number | null;
  isActive: boolean;
  isPublic: boolean;
  sortOrder: number;
  entitlements: LocalPlanEntitlement[];
}

export interface LocalSubscriptionAccess {
  workspaceId: string;
  subscriptionId?: string | null;
  state?: string | null;
  stateVersion: number;
  readAllowed: boolean;
  writeAllowed: boolean;
  reasonCode: string;
  evaluatedAt: string;
  trialEndsAt?: string | null;
  graceEndsAt?: string | null;
}

export interface LocalBillingSubscription {
  id: string;
  workspaceId: string;
  planId: string;
  planTier: LocalPlanTier;
  planName: string;
  status: LocalSubscriptionStatus;
  interval: LocalBillingInterval;
  currentPeriodStart?: string | null;
  currentPeriodEnd?: string | null;
  trialEnd?: string | null;
  cancelledAt?: string | null;
  amountCents: number;
  currency: string;
}

export interface LocalBillingIntent {
  id: string;
  workspaceId: string;
  requestedByUserId?: string | null;
  actionType: string;
  provider?: string | null;
  idempotencyKey: string;
  status: string;
  payload: Record<string, unknown>;
  reason: string;
  createdAt: string;
}

export interface LocalBillingPortal {
  workspaceId: string;
  subscription?: LocalBillingSubscription | null;
  access: LocalSubscriptionAccess;
  entitlements: LocalPlanEntitlement[];
  recentIntents: LocalBillingIntent[];
  simulated: boolean;
  externalEffects: number;
}

export interface LocalBillingCommandResult {
  action: string;
  replayed: boolean;
  intent: LocalBillingIntent;
  portal: LocalBillingPortal;
}

export type LocalBillingCommand =
  | 'trial'
  | 'checkout'
  | 'upgrade'
  | 'downgrade'
  | 'dunning'
  | 'suspend'
  | 'reactivate'
  | 'cancel';

export function createLocalIdempotencyKey(action: string): string {
  const nonce = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `spa:${action}:${nonce}`;
}

export function fetchLocalPlanCatalog(): Promise<LocalPlanCatalogItem[]> {
  return apiFetch<LocalPlanCatalogItem[]>('/api/v1/local-billing/catalog');
}

export function fetchLocalBillingPortal(): Promise<LocalBillingPortal> {
  return apiFetch<LocalBillingPortal>('/api/v1/local-billing/portal');
}

export function fetchLocalBillingIntents(limit = 50): Promise<LocalBillingIntent[]> {
  return apiFetch<LocalBillingIntent[]>(`/api/v1/local-billing/intents?limit=${limit}`);
}

export function executeLocalBillingCommand(
  command: LocalBillingCommand,
  payload: Record<string, unknown> = {},
): Promise<LocalBillingCommandResult> {
  return apiFetch<LocalBillingCommandResult>(`/api/v1/local-billing/${command}`, {
    method: 'POST',
    body: JSON.stringify({
      idempotencyKey: createLocalIdempotencyKey(command),
      ...payload,
    }),
  });
}

export function createLocalBillingExportIntent(payload: {
  exportFormat?: 'json' | 'csv' | 'zip';
  scopes?: string[];
  reason?: string;
} = {}): Promise<LocalBillingCommandResult> {
  return apiFetch<LocalBillingCommandResult>('/api/v1/local-billing/intents/export', {
    method: 'POST',
    body: JSON.stringify({
      idempotencyKey: createLocalIdempotencyKey('export'),
      exportFormat: payload.exportFormat ?? 'zip',
      scopes: payload.scopes ?? ['workspace'],
      reason: payload.reason || null,
    }),
  });
}

export function createLocalBillingClosureIntent(reason: string): Promise<LocalBillingCommandResult> {
  return apiFetch<LocalBillingCommandResult>('/api/v1/local-billing/intents/closure', {
    method: 'POST',
    body: JSON.stringify({
      idempotencyKey: createLocalIdempotencyKey('closure'),
      confirmClosure: true,
      reason,
    }),
  });
}
