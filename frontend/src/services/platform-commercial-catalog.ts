import { apiFetch } from '@/services/http';

const COMMERCIAL_ROOT = '/api/v1/platform/commercial';

export const COMMERCIAL_ENTITLEMENT_KEYS = [
  'tokens',
  'ai_executions',
  'ai_conversations',
  'campaign_recipients',
  'contacts',
  'audio_minutes',
  'storage_bytes',
  'files',
  'users',
  'workspaces',
  'whatsapp_numbers',
  'agents',
  'legal_suite',
  'legal_areas',
  'legal_cases',
  'djen_terms',
  'legal_ai_generations',
  'legal_documents',
  'legal_automations',
  'legal_templates',
  'legal_signatures',
  'protocol_folders',
  'legal_bulk_operations',
  'legal_audit',
  'legal_integrations',
  'automations',
  'active_tasks',
  'task_participants',
  'task_reviews',
  'review_rounds',
  'task_reminder_rules',
  'meeting_participants',
  'digital_signature',
  'calendar',
  'email',
  'webhooks',
  'advanced_audit',
  'success_manager',
  'monitored_processes',
] as const;

export type CommercialEntitlementKey = typeof COMMERCIAL_ENTITLEMENT_KEYS[number];
export type CommercialOfferFamily = 'crm' | 'legal' | 'internal';
export type CommercialOfferKind = 'base_plan' | 'solution';
export type CommercialBillingInterval = 'monthly' | 'annual';
export type CommercialEntitlementPeriod = 'monthly' | 'stock' | 'concurrent' | 'feature';
export type CommercialEntitlementEnforcement = 'hard' | 'grace';
export type CommercialEntitlementComposition = 'boolean_any' | 'maximum' | 'sum';
export type CommercialContractStatus =
  | 'draft'
  | 'trialing'
  | 'active'
  | 'past_due'
  | 'paused'
  | 'cancelled';
export type CommercialContractLineStatus = 'scheduled' | 'active' | 'suspended' | 'cancelled';
export type CommercialAdjustmentType =
  | 'percentage_discount'
  | 'fixed_discount'
  | 'negotiated_price'
  | 'courtesy_period'
  | 'invoice_credit';
export type CommercialAdjustmentSource = 'manual' | 'coupon' | 'benefit' | 'referral' | 'campaign';
export type CommercialRecordStatus = 'scheduled' | 'active' | 'expired' | 'revoked';
export type CommercialPortability =
  | 'preserve'
  | 'expire_on_offer_change'
  | 'recalculate'
  | 'manual_review';
export type EntitlementBonusMode = 'quantity_delta' | 'feature_grant';
export type EntitlementBonusSource = 'manual' | 'benefit' | 'campaign' | 'compensation';

export interface CommercialEntitlementSetting {
  enabled: boolean;
  unit: string;
  period: CommercialEntitlementPeriod;
  limitQuantity: number | null;
  enforcement: CommercialEntitlementEnforcement;
  graceQuantity: number | null;
  composition: CommercialEntitlementComposition;
}

interface CommercialOfferSnapshotBase {
  name: string;
  description: string | null;
  isPublic: boolean;
  isActive: boolean;
  isRecommended: boolean;
  isQuoteOnly: boolean;
  availableIntervals: CommercialBillingInterval[];
  sortOrder: number;
  currency: 'BRL';
  priceMonthlyCents: number | null;
  priceAnnualCents: number | null;
  cta: string;
}

export type CommercialEntitlementKeyV2 = Exclude<CommercialEntitlementKey, 'contacts'>;

export interface CommercialOfferSnapshotV2 extends CommercialOfferSnapshotBase {
  schemaVersion: '2.0';
  entitlements: Record<CommercialEntitlementKeyV2, CommercialEntitlementSetting>;
}

export interface CommercialOfferSnapshotV3 extends CommercialOfferSnapshotBase {
  schemaVersion: '3.0';
  entitlements: Record<CommercialEntitlementKey, CommercialEntitlementSetting>;
}

export type CommercialOfferSnapshot = CommercialOfferSnapshotV2 | CommercialOfferSnapshotV3;
export type CommercialOfferSnapshotWrite = CommercialOfferSnapshotV3;

export interface CommercialOfferVersion {
  id: string;
  offerId: string;
  sku: string;
  family: CommercialOfferFamily;
  kind: CommercialOfferKind;
  legacyTier: string | null;
  version: number;
  snapshot: CommercialOfferSnapshot;
  snapshotHash: string;
  publicationReason: string;
  publishedByUserId: string | null;
  publishedAt: string;
}

export interface CommercialFieldDiff {
  path: string;
  label: string;
  changeType: 'added' | 'removed' | 'changed';
  before: unknown;
  after: unknown;
}

export interface CommercialOfferDraft {
  id: string;
  offerId: string;
  sku: string;
  draftVersion: number;
  basePublishedVersion: number;
  snapshot: CommercialOfferSnapshot;
  snapshotHash: string;
  status: 'active' | 'discarded' | 'published';
  reason: string;
  updatedByUserId: string | null;
  updatedAt: string;
}

export interface CommercialWorkspaceProviderState {
  provider?: 'stripe' | 'asaas' | 'mercado_pago' | null;
  providerState: 'not_configured' | 'gate_closed' | 'partially_bound' | 'configured';
  providerMessage: string;
  customerLinked: boolean;
  subscriptionLinked: boolean;
  billingPortalAvailable: boolean;
  providerStatus: string | null;
}

export interface CommercialUnitEconomicsBlocker {
  code: string;
  message: string;
}

export interface CommercialUnitEconomicsGate {
  assessmentVersion: string;
  status: 'not_required' | 'insufficient_data' | 'unprofitable' | 'ready';
  eligible: boolean;
  requiresEvidence: boolean;
  evidenceSource: 'provider_confirmed_usage_plus_internal_rate_card';
  evaluatedAt: string;
  windowDays: number;
  minimumConfirmedCalls: number;
  minimumDistinctDays: number;
  minimumObservedTokens: number;
  observedCalls: number | null;
  observedDays: number | null;
  observedTokens: number | null;
  blockers: CommercialUnitEconomicsBlocker[];
  diagnostics: string[];
}

export interface CommercialOfferDraftPreview {
  draft: CommercialOfferDraft;
  published: CommercialOfferVersion;
  diff: CommercialFieldDiff[];
  warnings: string[];
  unitEconomics: CommercialUnitEconomicsGate;
  eligible: boolean;
}

export interface CommercialOfferRevisionHistory {
  sku: string;
  items: Array<{ offer: CommercialOfferVersion; diff: CommercialFieldDiff[] }>;
}

export interface CommercialOfferRestorePreview {
  sku: string;
  current: CommercialOfferVersion;
  target: CommercialOfferVersion;
  diff: CommercialFieldDiff[];
  warnings: string[];
  unitEconomics: CommercialUnitEconomicsGate;
  eligible: boolean;
}

export interface CommercialPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface CommercialOfferPublishRequest {
  sku: string;
  family: CommercialOfferFamily;
  kind: CommercialOfferKind;
  legacyTier: string | null;
  snapshot: CommercialOfferSnapshotWrite;
  reason: string;
}

export interface WorkspaceContractLineWrite {
  sku: string;
  policyVersionId?: string;
  quantity: number;
  contractedPriceCents?: number;
  billingInterval: CommercialBillingInterval;
  entitlementOverrides: Partial<Record<CommercialEntitlementKey, CommercialEntitlementSetting>>;
  effectiveFrom?: string;
  effectiveUntil?: string;
}

export interface WorkspaceContractPreviewRequest {
  workspaceId: string;
  lines: WorkspaceContractLineWrite[];
}

export interface WorkspaceContractCreateRequest extends WorkspaceContractPreviewRequest {
  status: CommercialContractStatus;
  reason: string;
}

export interface WorkspaceContractRevisionPreviewRequest {
  expectedVersion: number;
  lines: WorkspaceContractLineWrite[];
}

export interface WorkspaceContractRevisionRequest {
  lines: WorkspaceContractLineWrite[];
  reason: string;
}

export interface WorkspaceContractLine {
  id: string | null;
  offerId: string;
  policyVersionId: string;
  sku: string;
  offerFamily: CommercialOfferFamily;
  quantity: number;
  listPriceCents: number;
  contractedPriceCents: number;
  billingInterval: CommercialBillingInterval;
  entitlementOverrides: Partial<Record<CommercialEntitlementKey, CommercialEntitlementSetting>>;
  effectiveFrom: string;
  effectiveUntil: string | null;
  status: CommercialContractLineStatus;
  version: number;
}

export interface CommercialPricePreview {
  listPriceCents: number;
  contractedPriceCents: number;
  recurringPriceCents: number;
  invoiceCreditCents: number;
  amountDueCents: number;
}

export interface CapabilitySource {
  type: 'plan' | 'legal' | 'bonus' | 'override';
  id: string;
  contribution: number | boolean | null;
}

export interface EffectiveCapability {
  key: CommercialEntitlementKey;
  contracted: boolean;
  enabled: boolean;
  permitted: boolean;
  limit: number | null;
  graceLimit: number | null;
  used: number | null;
  remaining: number | null;
  unit: string;
  period: CommercialEntitlementPeriod;
  enforcement: CommercialEntitlementEnforcement;
  sources: CapabilitySource[];
  denialReason:
    | 'not_contracted'
    | 'permission_denied'
    | 'quota_exhausted'
    | 'suspended'
    | 'not_configured'
    | null;
  upgradeOffer: string | null;
}

export interface EffectiveCapabilityManifest {
  workspaceId: string;
  contractId: string;
  contractVersion: number;
  contractStatus: CommercialContractStatus;
  activeProducts: string[];
  capabilities: Record<CommercialEntitlementKey, EffectiveCapability>;
  generatedAt: string;
}

export interface WorkspaceContractPreview {
  workspaceId: string;
  lines: WorkspaceContractLine[];
  price: CommercialPricePreview;
  manifest: EffectiveCapabilityManifest;
  warnings: string[];
}

export interface WorkspaceContractRevisionPreview {
  contractId: string;
  workspaceId: string;
  currentVersion: number;
  targetVersion: number;
  currentLines: WorkspaceContractLine[];
  lines: WorkspaceContractLine[];
  currentPrice: CommercialPricePreview;
  price: CommercialPricePreview;
  manifest: EffectiveCapabilityManifest;
  changedFamilies: CommercialOfferFamily[];
  warnings: string[];
  blockers: string[];
  eligible: boolean;
}

export interface WorkspaceContract {
  id: string;
  workspaceId: string;
  workspaceLabel: string;
  status: CommercialContractStatus;
  currency: 'BRL';
  version: number;
  lines: WorkspaceContractLine[];
  price: CommercialPricePreview;
  createdAt: string;
  updatedAt: string;
  idempotentReplay: boolean;
}

export interface WorkspaceContractHistory {
  contractId: string;
  currentVersion: number;
  items: Array<{
    eventId: string;
    eventType: string;
    version: number;
    reason: string;
    actorUserId: string | null;
    occurredAt: string;
    snapshot: WorkspaceContract;
    diff: CommercialFieldDiff[];
  }>;
}

export interface WorkspaceContractRestorePreview {
  contractId: string;
  currentVersion: number;
  targetVersion: number;
  targetSnapshot: WorkspaceContract;
  revision: WorkspaceContractRevisionPreview;
  diff: CommercialFieldDiff[];
  preservesAdjustments: boolean;
  preservesBonuses: boolean;
}

export interface CommercialAdjustmentCreateRequest {
  contractLineId?: string;
  type: CommercialAdjustmentType;
  value: number;
  startsAt: string;
  endsAt?: string;
  remainingCycles?: number;
  stackGroup: string;
  stackable: boolean;
  portability: CommercialPortability;
  reason: string;
  source: CommercialAdjustmentSource;
}

export type CommercialAdjustmentUpdateRequest = CommercialAdjustmentCreateRequest;

export interface CommercialAdjustment {
  id: string;
  contractId: string;
  contractLineId: string | null;
  type: CommercialAdjustmentType;
  value: number;
  startsAt: string;
  endsAt: string | null;
  remainingCycles: number | null;
  stackGroup: string;
  stackable: boolean;
  portability: CommercialPortability;
  reason: string;
  source: CommercialAdjustmentSource;
  status: CommercialRecordStatus;
  version: number;
  createdAt: string;
}

export interface EntitlementBonusCreateRequest {
  entitlement: CommercialEntitlementKey;
  mode: EntitlementBonusMode;
  quantity?: number;
  startsAt: string;
  endsAt?: string;
  renewsEachPeriod: boolean;
  portability: CommercialPortability;
  reason: string;
  source: EntitlementBonusSource;
}

export type EntitlementBonusUpdateRequest = EntitlementBonusCreateRequest;

export interface EntitlementBonus {
  id: string;
  workspaceId: string;
  entitlement: CommercialEntitlementKey;
  mode: EntitlementBonusMode;
  quantity: number | null;
  startsAt: string;
  endsAt: string | null;
  renewsEachPeriod: boolean;
  portability: CommercialPortability;
  reason: string;
  source: EntitlementBonusSource;
  status: CommercialRecordStatus;
  version: number;
  createdAt: string;
}

export const COMMERCIAL_ENTITLEMENT_LABELS: Record<CommercialEntitlementKey, string> = {
  tokens: 'Tokens de IA',
  ai_executions: 'Execuções de IA',
  ai_conversations: 'Conversas de IA',
  campaign_recipients: 'Destinatários de campanhas',
  contacts: 'Contatos',
  audio_minutes: 'Minutos de áudio',
  storage_bytes: 'Armazenamento',
  files: 'Arquivos',
  users: 'Usuários',
  workspaces: 'Espaços de trabalho',
  whatsapp_numbers: 'Números de WhatsApp',
  agents: 'Agentes de IA',
  legal_suite: 'Jurídico 360',
  legal_areas: 'Áreas jurídicas',
  legal_cases: 'Casos jurídicos',
  djen_terms: 'Termos DJEN',
  legal_ai_generations: 'Gerações jurídicas com IA',
  legal_documents: 'Documentos jurídicos',
  legal_automations: 'Automações jurídicas',
  legal_templates: 'Modelos jurídicos',
  legal_signatures: 'Assinaturas jurídicas',
  protocol_folders: 'Pastas de protocolo',
  legal_bulk_operations: 'Operações jurídicas em massa',
  legal_audit: 'Auditoria jurídica',
  legal_integrations: 'Integrações jurídicas',
  automations: 'Automações',
  active_tasks: 'Tarefas ativas',
  task_participants: 'Participantes por tarefa',
  task_reviews: 'Revisores por tarefa',
  review_rounds: 'Rodadas de revisão',
  task_reminder_rules: 'Lembretes por tarefa',
  meeting_participants: 'Participantes por compromisso',
  digital_signature: 'Assinatura digital',
  calendar: 'Sincronização externa de calendário',
  email: 'E-mail',
  webhooks: 'Webhooks',
  advanced_audit: 'Auditoria avançada',
  success_manager: 'Gerente de sucesso',
  monitored_processes: 'Processos monitorados',
};

export function commercialMoney(value: number | null, currency = 'BRL'): string {
  if (value == null) return 'Sob consulta';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(value / 100);
}

export function commercialIdempotencyKey(scope: string): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  return `${scope}-${uuid ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

export function listCommercialOffers(
  options: { limit?: number; offset?: number; publicOnly?: boolean; signal?: AbortSignal } = {},
): Promise<CommercialPage<CommercialOfferVersion>> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
    publicOnly: String(options.publicOnly ?? false),
  });
  return apiFetch(`${COMMERCIAL_ROOT}/offers?${params}`, { signal: options.signal });
}

export function bootstrapCommercialOffers(idempotencyKey: string): Promise<{
  items: CommercialOfferVersion[];
  idempotentReplay: boolean;
}> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/bootstrap`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
  });
}

export function publishCommercialOffer(
  payload: CommercialOfferPublishRequest,
  idempotencyKey: string,
): Promise<{ offer: CommercialOfferVersion; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/publish`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function getCommercialOfferDraft(
  sku: string,
  signal?: AbortSignal,
): Promise<CommercialOfferDraft> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/draft`, { signal });
}

export function saveCommercialOfferDraft(
  sku: string,
  expectedDraftVersion: number | null,
  payload: {
    expectedPublishedVersion: number;
    snapshot: CommercialOfferSnapshotWrite;
    reason: string;
  },
  idempotencyKey: string,
): Promise<{ draft: CommercialOfferDraft; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/draft`, {
    method: 'PUT',
    headers: {
      'Idempotency-Key': idempotencyKey,
      ...(expectedDraftVersion == null ? {} : { 'If-Match': `"${expectedDraftVersion}"` }),
    },
    body: JSON.stringify(payload),
  });
}

export function discardCommercialOfferDraft(
  sku: string,
  expectedDraftVersion: number,
  reason: string,
  idempotencyKey: string,
): Promise<{ draft: CommercialOfferDraft; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/draft/discard`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${expectedDraftVersion}"`,
    },
    body: JSON.stringify({ reason }),
  });
}

export function previewCommercialOfferDraft(sku: string): Promise<CommercialOfferDraftPreview> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/draft/preview`, {
    method: 'POST',
  });
}

export function publishCommercialOfferDraft(
  sku: string,
  expectedDraftVersion: number,
  reason: string,
  idempotencyKey: string,
): Promise<{ offer: CommercialOfferVersion; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/draft/publish`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${expectedDraftVersion}"`,
    },
    body: JSON.stringify({ reason }),
  });
}

export function getCommercialOfferHistory(
  sku: string,
  signal?: AbortSignal,
): Promise<CommercialOfferRevisionHistory> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/history`, { signal });
}

export function previewCommercialOfferRestore(
  sku: string,
  targetVersion: number,
  expectedCurrentVersion: number,
): Promise<CommercialOfferRestorePreview> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/restore-preview`, {
    method: 'POST',
    body: JSON.stringify({ targetVersion, expectedCurrentVersion }),
  });
}

export function restoreCommercialOffer(
  sku: string,
  targetVersion: number,
  expectedCurrentVersion: number,
  reason: string,
  idempotencyKey: string,
): Promise<{ offer: CommercialOfferVersion; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/offers/${encodeURIComponent(sku)}/restore`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ targetVersion, expectedCurrentVersion, reason }),
  });
}

export function listCommercialContracts(
  options: { limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<CommercialPage<WorkspaceContract>> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 25),
    offset: String(options.offset ?? 0),
  });
  return apiFetch(`${COMMERCIAL_ROOT}/contracts?${params}`, { signal: options.signal });
}

export function getWorkspaceCommercialContract(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<WorkspaceContract> {
  return apiFetch(`${COMMERCIAL_ROOT}/workspaces/${encodeURIComponent(workspaceId)}/contract`, { signal });
}

export function getWorkspaceCommercialProviderState(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<CommercialWorkspaceProviderState> {
  return apiFetch(`${COMMERCIAL_ROOT}/workspaces/${encodeURIComponent(workspaceId)}/provider-state`, { signal });
}

export function createWorkspaceCommercialBillingPortal(
  workspaceId: string,
): Promise<{ portalUrl: string }> {
  return apiFetch(`${COMMERCIAL_ROOT}/workspaces/${encodeURIComponent(workspaceId)}/billing-portal`, {
    method: 'POST',
  });
}

export function getWorkspaceCapabilityManifest(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<EffectiveCapabilityManifest> {
  return apiFetch(`${COMMERCIAL_ROOT}/workspaces/${encodeURIComponent(workspaceId)}/manifest`, { signal });
}

export function previewWorkspaceCommercialContract(
  payload: WorkspaceContractPreviewRequest,
): Promise<WorkspaceContractPreview> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/preview`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function createWorkspaceCommercialContract(
  payload: WorkspaceContractCreateRequest,
  idempotencyKey: string,
): Promise<WorkspaceContract> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function previewWorkspaceCommercialContractRevision(
  contractId: string,
  payload: WorkspaceContractRevisionPreviewRequest,
): Promise<WorkspaceContractRevisionPreview> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/${encodeURIComponent(contractId)}/revision-preview`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function reviseWorkspaceCommercialContract(
  contractId: string,
  expectedVersion: number,
  payload: WorkspaceContractRevisionRequest,
  idempotencyKey: string,
): Promise<WorkspaceContract> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/${encodeURIComponent(contractId)}`, {
    method: 'PUT',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${expectedVersion}"`,
    },
    body: JSON.stringify(payload),
  });
}

export function getWorkspaceCommercialContractHistory(
  contractId: string,
  signal?: AbortSignal,
): Promise<WorkspaceContractHistory> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/${encodeURIComponent(contractId)}/history`, { signal });
}

export function previewWorkspaceCommercialContractRestore(
  contractId: string,
  targetVersion: number,
  expectedCurrentVersion: number,
): Promise<WorkspaceContractRestorePreview> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/${encodeURIComponent(contractId)}/restore-preview`, {
    method: 'POST',
    body: JSON.stringify({ targetVersion, expectedCurrentVersion }),
  });
}

export function restoreWorkspaceCommercialContract(
  contractId: string,
  targetVersion: number,
  expectedCurrentVersion: number,
  reason: string,
  idempotencyKey: string,
): Promise<WorkspaceContract> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/${encodeURIComponent(contractId)}/restore`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ targetVersion, expectedCurrentVersion, reason }),
  });
}

export function listCommercialAdjustments(
  contractId: string,
  signal?: AbortSignal,
): Promise<CommercialAdjustment[]> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/${encodeURIComponent(contractId)}/adjustments`, { signal });
}

export function addCommercialAdjustment(
  contractId: string,
  payload: CommercialAdjustmentCreateRequest,
  idempotencyKey: string,
): Promise<{ adjustment: CommercialAdjustment; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/contracts/${encodeURIComponent(contractId)}/adjustments`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function updateCommercialAdjustment(
  adjustmentId: string,
  expectedVersion: number,
  payload: CommercialAdjustmentUpdateRequest,
  idempotencyKey: string,
): Promise<{ adjustment: CommercialAdjustment; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/adjustments/${encodeURIComponent(adjustmentId)}`, {
    method: 'PUT',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${expectedVersion}"`,
    },
    body: JSON.stringify(payload),
  });
}

export function revokeCommercialAdjustment(
  adjustmentId: string,
  expectedVersion: number,
  reason: string,
  idempotencyKey: string,
): Promise<{ adjustment: CommercialAdjustment; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/adjustments/${encodeURIComponent(adjustmentId)}/revoke`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${expectedVersion}"`,
    },
    body: JSON.stringify({ reason }),
  });
}

export function listEntitlementBonuses(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<EntitlementBonus[]> {
  return apiFetch(`${COMMERCIAL_ROOT}/workspaces/${encodeURIComponent(workspaceId)}/bonuses`, { signal });
}

export function addEntitlementBonus(
  workspaceId: string,
  payload: EntitlementBonusCreateRequest,
  idempotencyKey: string,
): Promise<{ bonus: EntitlementBonus; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/workspaces/${encodeURIComponent(workspaceId)}/bonuses`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function updateEntitlementBonus(
  bonusId: string,
  expectedVersion: number,
  payload: EntitlementBonusUpdateRequest,
  idempotencyKey: string,
): Promise<{ bonus: EntitlementBonus; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/bonuses/${encodeURIComponent(bonusId)}`, {
    method: 'PUT',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${expectedVersion}"`,
    },
    body: JSON.stringify(payload),
  });
}

export function revokeEntitlementBonus(
  bonusId: string,
  expectedVersion: number,
  reason: string,
  idempotencyKey: string,
): Promise<{ bonus: EntitlementBonus; idempotentReplay: boolean }> {
  return apiFetch(`${COMMERCIAL_ROOT}/bonuses/${encodeURIComponent(bonusId)}/revoke`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${expectedVersion}"`,
    },
    body: JSON.stringify({ reason }),
  });
}
