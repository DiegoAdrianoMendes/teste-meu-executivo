import { API_BASE } from '@/services/api-origin';
import { apiFetch } from '@/services/http';

export interface PublicCommercialOffer {
  sku: string;
  name: string;
  description: string | null;
  family: 'crm' | 'legal' | 'internal';
  kind: 'base_plan' | 'solution';
  isRecommended: boolean;
  isQuoteOnly: boolean;
  priceMonthlyCents: number | null;
  currency: 'BRL';
  cta: string;
  highlights: string[];
}

export interface PublicCommercialCatalog {
  items: PublicCommercialOffer[];
  generatedAt: string;
  state: 'available' | 'not_published';
}

export interface CommercialContractLine {
  sku: string;
  contractedPriceCents: number;
  billingInterval: 'monthly' | 'annual';
  status: string;
}

export interface CommercialContract {
  id: string;
  workspaceId: string;
  workspaceLabel: string;
  status: string;
  currency: 'BRL';
  version: number;
  lines: CommercialContractLine[];
  price: {
    listPriceCents: number;
    contractedPriceCents: number;
    recurringPriceCents: number;
    invoiceCreditCents: number;
    amountDueCents: number;
  };
  createdAt: string;
  updatedAt: string;
}

export type CommercialRequestType =
  | 'change_base_plan'
  | 'activate_legal'
  | 'cancel_legal'
  | 'cancel_contract';

export interface CommercialAccountRequest {
  id: string;
  requestType: CommercialRequestType;
  targetSku: string | null;
  reason: string;
  status: 'pending' | 'resolved' | 'rejected' | 'cancelled';
  contractVersion: number;
  version: number;
  createdAt: string;
  resolvedAt: string | null;
  resolutionNote: string | null;
}

export interface CommercialAccountSummary {
  contract: CommercialContract;
  requests: CommercialAccountRequest[];
  automaticCheckoutAvailable: boolean;
  billingPortalAvailable: boolean;
  providerSubscriptionLinked: boolean;
  providerSubscriptionStatus: string | null;
  providerState: 'not_configured' | 'gate_closed' | 'partially_bound' | 'configured';
  providerMessage: string;
  activeProvider?: SaaSBillingProvider | null;
  availableProviders?: SaaSBillingProvider[];
}

export type SaaSBillingProvider = 'stripe' | 'asaas' | 'mercado_pago';

export interface CommercialCheckoutSession {
  intentId: string;
  status: 'session_created';
  checkoutUrl: string;
  provider: SaaSBillingProvider;
}

export interface CommercialProviderPriceBinding {
  id: string;
  sku: string;
  offerVersion: number;
  provider: SaaSBillingProvider;
  billingInterval: 'monthly' | 'annual';
  providerPriceId: string;
  status: 'active' | 'inactive';
  version: number;
  updatedAt: string;
}

export interface CommercialAccountPreview {
  requestType: CommercialRequestType;
  targetSku: string | null;
  contract: CommercialContract;
  revision: {
    currentVersion: number;
    targetVersion: number;
    currentPrice: { amountDueCents: number };
    price: { amountDueCents: number };
    warnings: string[];
    blockers: string[];
    eligible: boolean;
  } | null;
  warnings: string[];
  eligible: boolean;
}

export interface CommercialRequestPayload {
  requestType: CommercialRequestType;
  targetSku?: string | null;
  expectedContractVersion: number;
  reason: string;
}

async function publicFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { credentials: 'include' });
  if (!response.ok) throw new Error('Não foi possível carregar o catálogo comercial.');
  return response.json() as Promise<T>;
}

export const fetchPublicCommercialCatalog = () =>
  publicFetch<PublicCommercialCatalog>('/api/v1/commercial/offers');

export const fetchCommercialAccount = () =>
  apiFetch<CommercialAccountSummary>('/api/v1/commercial/account');

export const previewCommercialRequest = (payload: CommercialRequestPayload) =>
  apiFetch<CommercialAccountPreview>('/api/v1/commercial/account/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const createCommercialRequest = (payload: CommercialRequestPayload) =>
  apiFetch<CommercialAccountRequest>('/api/v1/commercial/account/requests', {
    method: 'POST',
    headers: {
      'Idempotency-Key': globalThis.crypto?.randomUUID?.()
        ?? `commercial-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    },
    body: JSON.stringify(payload),
  });

export const createCommercialCheckoutSession = (payload: {
  targetSku: string;
  expectedContractVersion: number;
  reason: string;
  provider?: SaaSBillingProvider;
}, idempotencyKey?: string) => apiFetch<CommercialCheckoutSession>('/api/v1/commercial/account/checkout-sessions', {
  method: 'POST',
  headers: {
    'Idempotency-Key': idempotencyKey ?? globalThis.crypto?.randomUUID?.()
      ?? `checkout-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  },
  body: JSON.stringify(payload),
});

export const createCommercialBillingPortal = () =>
  apiFetch<{ portalUrl: string }>('/api/v1/commercial/account/billing-portal', {
    method: 'POST',
  });

export const fetchPlatformCommercialProviderPriceBindings = () =>
  apiFetch<{ items: CommercialProviderPriceBinding[] }>(
    '/api/v1/platform/commercial/account-requests/provider-price-bindings',
  );

export const configurePlatformCommercialProviderPriceBinding = (payload: {
  sku: string;
  provider: SaaSBillingProvider;
  providerPriceId?: string;
  reason: string;
}) => apiFetch<CommercialProviderPriceBinding>(
  '/api/v1/platform/commercial/account-requests/provider-price-bindings',
  { method: 'POST', body: JSON.stringify(payload) },
);

export const fetchPlatformCommercialRequests = (workspaceId: string) =>
  apiFetch<{ items: CommercialAccountRequest[]; total: number }>(
    `/api/v1/platform/commercial/account-requests?workspaceId=${encodeURIComponent(workspaceId)}`,
  );

export const resolvePlatformCommercialRequest = (
  requestId: string,
  payload: { expectedVersion: number; status: 'resolved' | 'rejected' | 'cancelled'; note: string },
) => apiFetch<CommercialAccountRequest>(
  `/api/v1/platform/commercial/account-requests/${encodeURIComponent(requestId)}/resolve`,
  { method: 'POST', body: JSON.stringify(payload) },
);

export const executePlatformCommercialProviderChange = (
  requestId: string,
  payload: { expectedVersion: number; note: string },
) => apiFetch<CommercialAccountRequest>(
  `/api/v1/platform/commercial/account-requests/${encodeURIComponent(requestId)}/execute-provider-change`,
  { method: 'POST', body: JSON.stringify(payload) },
);
