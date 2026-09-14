import { apiFetch } from '@/services/http';

const PLAN_POLICIES_ROOT = '/api/v1/platform/plan-policies';

export const PLAN_POLICY_ENTITLEMENT_KEYS = [
  'tokens',
  'ai_executions',
  'ai_conversations',
  'audio_minutes',
  'storage_bytes',
  'files',
  'users',
  'whatsapp_numbers',
  'agents',
  'legal_areas',
  'automations',
  'digital_signature',
  'calendar',
  'email',
  'webhooks',
  'advanced_audit',
  'success_manager',
  'monitored_processes',
] as const;

export type PlanPolicyEntitlementKey = typeof PLAN_POLICY_ENTITLEMENT_KEYS[number];
export type PlanPolicyRolloutMode = 'existing_and_new' | 'new_only' | 'scheduled' | 'rule';
export type PlanPolicyWorkspaceStatus = 'provisioning' | 'active' | 'suspended';
export type PlanPolicyWorkspaceSetupMode = 'self_service' | 'assisted';
export type PlanPolicySubscriptionStatus =
  | 'trialing'
  | 'active'
  | 'past_due'
  | 'cancelled'
  | 'unpaid'
  | 'paused';

export interface PlanPolicyEntitlementSetting {
  enabled: boolean;
  limitQuantity: number | null;
  hardLimit: boolean;
}

export interface PlanPolicyParameters {
  maxMatters: number | null;
  maxWorkspaces: number | null;
  maxFilesPerConversation: number | null;
  maxOperationalRecipients: number | null;
  maxCampaignRecipientsPerMonth: number | null;
}

export interface PlanPolicySnapshot {
  schemaVersion: '1.0';
  name: string;
  description: string | null;
  isActive: boolean;
  isPublic: boolean;
  recommended: boolean;
  sortOrder: number;
  currency: string;
  priceMonthlyCents: number;
  priceAnnuallyCents: number;
  supportTier: string;
  integrations: string[];
  highlights: string[];
  parameters: PlanPolicyParameters;
  entitlements: Record<PlanPolicyEntitlementKey, PlanPolicyEntitlementSetting>;
}

export interface PlanPolicyPlanSummary {
  planId: string;
  planTier: string;
  planName: string;
  draftRevision: number | null;
  draftEtag: string | null;
  latestVersion: number | null;
  latestEffectiveAt: string | null;
}

export interface PlanPolicyEntitlementCatalogItem {
  key: PlanPolicyEntitlementKey;
  control: 'checkbox' | 'checkbox_and_number';
  supportsLimit: boolean;
  requiresFiniteLimit: boolean;
}

export interface PlanPolicyEditor {
  planId: string;
  planTier: string;
  source: 'draft' | 'published' | 'legacy';
  etag: string;
  draftRevision: number | null;
  publishedVersion: number | null;
  snapshot: PlanPolicySnapshot;
}

export interface PlanPolicyDraft {
  id: string;
  planId: string;
  revision: number;
  etag: string;
  snapshot: PlanPolicySnapshot;
  updatedByUserId: string;
  createdAt: string;
  updatedAt: string;
}

export interface PlanPolicyRolloutRule {
  workspaceIds: string[];
  excludeWorkspaceIds: string[];
  workspaceStatuses: PlanPolicyWorkspaceStatus[];
  setupModes: PlanPolicyWorkspaceSetupMode[];
  subscriptionStatuses: PlanPolicySubscriptionStatus[];
  workspaceCreatedFrom?: string;
  workspaceCreatedBefore?: string;
  workspaceIsActive?: boolean;
}

export interface PlanPolicyRolloutRequest {
  rolloutMode: PlanPolicyRolloutMode;
  effectiveAt?: string;
  rule?: PlanPolicyRolloutRule;
}

export interface PlanPolicyPreviewRequest extends PlanPolicyRolloutRequest {
  sampleLimit: number;
}

export interface PlanPolicyAccountPreview {
  workspaceId: string;
  name: string;
  slug: string;
  workspaceStatus: PlanPolicyWorkspaceStatus;
  subscriptionStatus: PlanPolicySubscriptionStatus;
  workspaceCreatedAt: string;
  currentPolicyVersion: number | null;
}

export interface PlanPolicyPreview {
  planId: string;
  rolloutMode: PlanPolicyRolloutMode;
  effectiveAt: string;
  rule: PlanPolicyRolloutRule | null;
  affectedCount: number;
  appliesImmediately: boolean;
  accounts: PlanPolicyAccountPreview[];
}

export interface PlanPolicyPublishRequest extends PlanPolicyRolloutRequest {
  snapshot?: PlanPolicySnapshot;
  draftEtag?: string;
  reason: string;
}

export interface PlanPolicyVersion {
  id: string;
  planId: string;
  version: number;
  schemaVersion: string;
  snapshot: PlanPolicySnapshot;
  snapshotHash: string;
  rolloutMode: PlanPolicyRolloutMode;
  effectiveAt: string;
  rolloutRule: PlanPolicyRolloutRule | null;
  publicationReason: string;
  publishedByUserId: string;
  publishedAt: string;
}

export interface PlanPolicyPublication {
  policy: PlanPolicyVersion;
  affectedCount: number;
  assignmentsCreated: number;
  idempotentReplay: boolean;
}

export interface PlanPolicyPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export function listPlanPolicies(signal?: AbortSignal): Promise<PlanPolicyPlanSummary[]> {
  return apiFetch(PLAN_POLICIES_ROOT, { signal });
}

export function listPlanPoliciesPage(
  limit = 10,
  offset = 0,
  signal?: AbortSignal,
): Promise<PlanPolicyPage<PlanPolicyPlanSummary>> {
  return apiFetch(`${PLAN_POLICIES_ROOT}/page?limit=${limit}&offset=${offset}`, { signal });
}

export function getPlanPolicyEntitlementCatalog(
  signal?: AbortSignal,
): Promise<PlanPolicyEntitlementCatalogItem[]> {
  return apiFetch(`${PLAN_POLICIES_ROOT}/entitlement-catalog`, { signal });
}

export function getPlanPolicyEditor(
  planId: string,
  signal?: AbortSignal,
): Promise<PlanPolicyEditor> {
  return apiFetch(`${PLAN_POLICIES_ROOT}/${encodeURIComponent(planId)}/editor`, { signal });
}

export function savePlanPolicyDraft(
  planId: string,
  snapshot: PlanPolicySnapshot,
  ifMatch?: string,
): Promise<PlanPolicyDraft> {
  const headers = new Headers();
  if (ifMatch) headers.set('If-Match', ifMatch);
  return apiFetch(`${PLAN_POLICIES_ROOT}/${encodeURIComponent(planId)}/draft`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ snapshot }),
  });
}

export function previewPlanPolicyRollout(
  planId: string,
  payload: PlanPolicyPreviewRequest,
): Promise<PlanPolicyPreview> {
  return apiFetch(`${PLAN_POLICIES_ROOT}/${encodeURIComponent(planId)}/preview`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function publishPlanPolicy(
  planId: string,
  payload: PlanPolicyPublishRequest,
  idempotencyKey: string,
): Promise<PlanPolicyPublication> {
  return apiFetch(`${PLAN_POLICIES_ROOT}/${encodeURIComponent(planId)}/publish`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function listPlanPolicyVersions(
  planId: string,
  signal?: AbortSignal,
): Promise<PlanPolicyVersion[]> {
  return apiFetch(`${PLAN_POLICIES_ROOT}/${encodeURIComponent(planId)}/versions`, { signal });
}

export function listPlanPolicyVersionsPage(
  planId: string,
  limit = 10,
  offset = 0,
  signal?: AbortSignal,
): Promise<PlanPolicyPage<PlanPolicyVersion>> {
  return apiFetch(`${PLAN_POLICIES_ROOT}/${encodeURIComponent(planId)}/versions/page?limit=${limit}&offset=${offset}`, { signal });
}

export function createPlanPolicyIdempotencyKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `plan-policy-${uuid}`;
  return `plan-policy-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
