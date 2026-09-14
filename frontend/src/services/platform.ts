import { apiBlob, apiFetch } from '@/services/http';
import type {
  BlogIndexingSettings,
  AiTokenPackage,
  AiTokenPackageUpsert,
  BlogPost,
  BlogPostSummary,
  BlogSettings,
  BlogTaxonomy,
  EditorialAssistRequestPayload,
  EditorialAssistResponse,
  GeneratedImageResponse,
  GoogleBusinessConnectionUpdate,
  GoogleBusinessProfileConnection,
  GoogleBusinessPublishJob,
  GoogleBusinessPublishRequest,
  PlatformDocumentEnvelope,
  PlatformOfficialWhatsAppActiveUpdate,
  PlatformOfficialWhatsAppConnectManual,
  PlatformOfficialWhatsAppConnection,
  PlatformOfficialWhatsAppEmbeddedCallback,
  PlatformOfficialWhatsAppRole,
  PlatformOfficialWhatsAppTestResult,
  PlatformOfficialWhatsAppTestSend,
  PlatformSiteDocumentEnvelope,
  PlatformRolePackage,
  PlatformStaffUser,
  PlatformStaffPage,
  PlatformWorkspaceSummary,
  PublicFaqDocument,
  PublicHomeDocument,
  PublicLegalDocument,
  PublicMediaLibrary,
  PublicSiteSettings,
  SeoAutofillResponse,
  SiteMediaAsset,
  SiteMediaConfirm,
  SiteMediaPresign,
  WorkspacePlanCatalogItem,
  WorkspaceAiCostPolicyUpdate,
  PlatformAgentBillingReconciliationDecision,
  PlatformAgentBillingReconciliationPage,
  PlatformAgentBillingReconciliationRun,
  PlatformAiCostSummary,
  PlatformAiModelsOverview,
  WorkspaceAiTokenPackageRequest,
  MetaCommercialRateCard,
  MetaCommercialRateCardUpsert,
  WorkspaceOverviewResponse,
  EntitlementOverrideWriteItem,
  EntitlementOverrideWriteResult,
  PlatformFinanceHarmonizationQuota,
  BlackboxSessionManifest,
  PlatformConfigurationDocument,
  PlatformConfigurationVersionPage,
  UtilityTemplateCatalog,
} from '@/types';

export async function fetchBlackboxSessionManifest(): Promise<BlackboxSessionManifest> {
  return apiFetch('/api/v1/platform/session-manifest');
}

export function fetchPlatformConfiguration(namespace: string): Promise<PlatformConfigurationDocument> {
  return apiFetch(`/api/v1/platform/configurations/${encodeURIComponent(namespace)}`);
}

export function fetchPlatformConfigurationVersionPage(
  namespace: string,
  input: { limit?: number; offset?: number } = {},
): Promise<PlatformConfigurationVersionPage> {
  const params = new URLSearchParams({
    limit: String(input.limit ?? 10),
    offset: String(input.offset ?? 0),
  });
  return apiFetch(`/api/v1/platform/configurations/${encodeURIComponent(namespace)}/versions/page?${params}`);
}

export function savePlatformConfiguration(
  namespace: string,
  payload: Record<string, unknown>,
  options: { schemaVersion: string; reason: string | null; etag: string | null },
): Promise<PlatformConfigurationDocument> {
  return apiFetch(`/api/v1/platform/configurations/${encodeURIComponent(namespace)}`, {
    method: 'PUT',
    headers: options.etag ? { 'If-Match': `"${options.etag}"` } : undefined,
    body: JSON.stringify({
      payload,
      schemaVersion: options.schemaVersion,
      reason: options.reason,
    }),
  });
}

export function rollbackPlatformConfiguration(
  namespace: string,
  version: number,
  etag: string,
): Promise<PlatformConfigurationDocument> {
  return apiFetch(
    `/api/v1/platform/configurations/${encodeURIComponent(namespace)}/rollback/${version}`,
    { method: 'POST', headers: { 'If-Match': `"${etag}"` } },
  );
}

export type PlatformGovernanceModule =
  | 'audit'
  | 'compliance'
  | 'data_governance'
  | 'feature_flags'
  | 'integrations'
  | 'logs'
  | 'releases'
  | 'security';

export interface PlatformAuditEventRecord {
  id: string;
  eventType: string;
  outcome: string;
  actorUserId: string | null;
  actorName: string;
  actorRole: string | null;
  isPlatformRoot: boolean;
  subjectUserId: string | null;
  administrativeSessionId: string | null;
  workspaceId: string | null;
  workspaceLabelSnapshot?: string | null;
  module: string | null;
  action: string;
  resourceType: string | null;
  resourceId: string | null;
  correlationId: string | null;
  reason: string | null;
  before: unknown;
  after: unknown;
  result: string;
  details: Record<string, unknown>;
  occurredAt: string;
  changedPaths?: string[];
  restorable: boolean;
  fieldDiffs?: Array<{
    path: string;
    label: string;
    changeType: 'added' | 'removed' | 'changed';
    before: unknown;
    after: unknown;
    sensitive: boolean;
  }>;
}

export function fetchPlatformAuditEvents(params: URLSearchParams): Promise<{
  items: PlatformAuditEventRecord[];
  count: number;
  limit: number;
  offset: number;
  nextCursor: string | null;
  hasMore: boolean;
}> {
  const canonical = new URLSearchParams();
  canonical.set('limit', params.get('limit') ?? '100');
  if (params.get('search')) canonical.set('search', params.get('search')!);
  if (params.get('outcome')) canonical.set('result', params.get('outcome')!);
  if (params.get('cursor')) canonical.set('cursor', params.get('cursor')!);
  for (const key of [
    'module',
    'workspaceId',
    'actorId',
    'actorRole',
    'action',
    'resourceType',
    'correlationId',
    'operationId',
    'from',
    'to',
    'restorable',
  ]) {
    if (params.get(key)) canonical.set(key, params.get(key)!);
  }
  return apiFetch<{
    items: Array<{
      id: string;
      actorId: string | null;
      actorRoleSnapshot: string;
      isPlatformRoot: boolean;
      subjectUserId: string | null;
      administrativeSessionId: string | null;
      workspaceId: string | null;
      workspaceLabelSnapshot: string | null;
      module: string;
      action: string;
      resourceType: string;
      resourceId: string | null;
      correlationId: string;
      reason: string;
      before: unknown;
      after: unknown;
      changedPaths: string[];
      fieldDiffs: PlatformAuditEventRecord['fieldDiffs'];
      restorable: boolean;
      result: string;
      errorCode: string | null;
      occurredAt: string;
    }>;
    nextCursor: string | null;
    hasMore: boolean;
  }>(`/api/v1/platform/audit-events?${canonical.toString()}`).then((page) => ({
    items: page.items.map((event) => ({
      id: event.id,
      eventType: event.action,
      outcome: event.result,
      actorUserId: event.actorId,
      actorName: event.actorRoleSnapshot,
      actorRole: event.actorRoleSnapshot,
      isPlatformRoot: event.isPlatformRoot,
      subjectUserId: event.subjectUserId,
      administrativeSessionId: event.administrativeSessionId,
      workspaceId: event.workspaceId,
      workspaceLabelSnapshot: event.workspaceLabelSnapshot,
      module: event.module,
      action: event.action,
      resourceType: event.resourceType,
      resourceId: event.resourceId,
      correlationId: event.correlationId,
      reason: event.reason,
      before: event.before,
      after: event.after,
      changedPaths: event.changedPaths,
      fieldDiffs: event.fieldDiffs,
      restorable: event.restorable,
      result: event.result,
      details: {
        errorCode: event.errorCode,
        changedPaths: event.changedPaths,
        fieldDiffs: event.fieldDiffs,
      },
      occurredAt: event.occurredAt,
    })),
    count: page.items.length,
    limit: Number(canonical.get('limit')),
    offset: 0,
    nextCursor: page.nextCursor,
    hasMore: page.hasMore,
  }));
}

export interface PlatformOperationalLogRecord {
  id: string;
  errorCode: string;
  severity: string;
  status: string;
  exceptionType: string;
  message: string;
  endpoint: string | null;
  method: string | null;
  requestId: string | null;
  workspaceId: string | null;
  occurrences: number;
  firstSeenAt: string;
  lastSeenAt: string;
}

export function fetchPlatformOperationalLogs(params: URLSearchParams): Promise<{
  items: PlatformOperationalLogRecord[];
  count: number;
  limit: number;
  nextCursor: string | null;
  hasMore: boolean;
}> {
  const query = params.toString();
  return apiFetch(`/api/v1/platform/governance/operational-logs${query ? `?${query}` : ''}`);
}

export interface PlatformFeatureFlagCatalog {
  flags: Record<string, boolean>;
  etag: string | null;
  currentVersion: number;
  updatedAt: string | null;
}

export function fetchPlatformFeatureFlags(): Promise<PlatformFeatureFlagCatalog> {
  return apiFetch('/api/v1/platform/governance/feature-flags/catalog');
}

export function updatePlatformFeatureFlag(
  key: string,
  enabled: boolean,
  reason: string,
  expectedEtag: string | null,
): Promise<PlatformFeatureFlagCatalog> {
  return apiFetch(`/api/v1/platform/governance/feature-flags/${encodeURIComponent(key)}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled, reason, expectedEtag }),
  });
}

export interface PlatformSystemStatus {
  status: string;
  timestamp: string;
  version: string;
  components: Record<string, { status: string; latency_ms?: number | null; workers?: number; note?: string; error?: string }>;
  message: string;
  historyRecorded: boolean;
}

export interface PlatformStatusHistory {
  state: 'ready' | 'collecting' | 'unavailable';
  periodDays: number;
  observations: number;
  uptimePercent: number | null;
  firstObservedAt: string | null;
  lastObservedAt: string | null;
  daysObserved: number;
  latestStatus: string | null;
  daily: Array<{ date: string; status: string; observations: number; uptimePercent: number | null }>;
  incidents: Array<{ startedAt: string; endedAt: string | null; ongoing: boolean; status: string; observations: number; components: string[] }>;
}

export interface PlatformPeriodicTaskOverview {
  available: boolean;
  generatedAt: string;
  graceSeconds: number;
  summary: { total: number; failing: number; late: number; neverRun: number; healthy: number } | null;
  tasks: Array<{
    scheduleName: string;
    taskName: string;
    scheduleDescription: string;
    status: string;
    lastFinishedAt: string | null;
    lastDurationMs: number | null;
    errorType: string | null;
    successCount: number;
    errorCount: number;
    isLate: boolean | null;
    expectedNextRunAt: string | null;
  }>;
}

export function fetchPlatformSystemStatus(): Promise<PlatformSystemStatus> {
  return apiFetch('/api/v1/status');
}

export function fetchPlatformStatusHistory(): Promise<PlatformStatusHistory> {
  return apiFetch('/api/v1/status/history');
}

export function fetchPlatformPeriodicTasks(): Promise<PlatformPeriodicTaskOverview> {
  return apiFetch('/api/v1/status/tasks');
}

export interface PlatformManagedIncident {
  id: string;
  title: string;
  summary: string;
  severity: 'sev1' | 'sev2' | 'sev3' | 'sev4';
  status: 'investigating' | 'identified' | 'monitoring' | 'resolved';
  components: string[];
  ownerUserId: string | null;
  createdByUserId: string;
  updates: Array<{ status: string; note: string; actorId: string; occurredAt: string }>;
  startedAt: string;
  resolvedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export function fetchPlatformIncidents(query: { cursor?: string; limit?: number } = {}): Promise<{
  items: PlatformManagedIncident[];
  openCount: number;
  nextCursor: string | null;
  hasMore: boolean;
}> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 25) });
  if (query.cursor) params.set('cursor', query.cursor);
  return apiFetch(`/api/v1/platform/governance/security/incidents?${params.toString()}`);
}

export function createPlatformIncident(body: {
  title: string; summary: string; severity: PlatformManagedIncident['severity']; components: string[];
}): Promise<PlatformManagedIncident> {
  return apiFetch('/api/v1/platform/governance/security/incidents', { method: 'POST', body: JSON.stringify(body) });
}

export function updatePlatformIncident(
  incidentId: string,
  body: { status?: PlatformManagedIncident['status']; note?: string; severity?: PlatformManagedIncident['severity'] },
): Promise<PlatformManagedIncident> {
  return apiFetch(`/api/v1/platform/governance/security/incidents/${incidentId}`, { method: 'PATCH', body: JSON.stringify(body) });
}

export interface PlatformSecurityOverview {
  generatedAt: string;
  summary: { activeUserSessions: number; activeAdministrativeSessions: number; mfaEnabledUsers: number; deniedOrFailedEvents: number };
  sessions: Array<{
    id: string; userId: string; userName: string; userEmail: string | null; isPlatformStaff: boolean;
    deviceLabel: string; deviceType: string; browser: string; operatingSystem: string;
    lastSeenAt: string; expiresAt: string;
  }>;
  limit: number;
  nextCursor: string | null;
  hasMore: boolean;
}

export function fetchPlatformSecurityOverview(query: { limit?: number; cursor?: string | null } = {}): Promise<PlatformSecurityOverview> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 50) });
  if (query.cursor) params.set('cursor', query.cursor);
  return apiFetch(`/api/v1/platform/governance/security/overview?${params.toString()}`);
}

export function revokePlatformSecuritySession(sessionId: string): Promise<{ ok: boolean; revoked: boolean }> {
  return apiFetch(`/api/v1/platform/governance/security/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
}

export interface PlatformGovernanceOperationalOverview {
  module: 'compliance' | 'data_governance' | 'releases';
  generatedAt: string;
  state: 'observed' | 'insufficient_data' | 'configured' | 'not_configured';
  policy: { configured: boolean; currentVersion: number; updatedAt: string | null; hasOwner: boolean; hasReviewCycle: boolean };
  metrics: Array<{ key: string; label: string; value: string | number; quality: string }>;
  facts: Array<{ label: string; value: string }>;
}

export function fetchPlatformGovernanceOperationalOverview(
  module: 'compliance' | 'data_governance' | 'releases',
): Promise<PlatformGovernanceOperationalOverview> {
  return apiFetch(`/api/v1/platform/governance/${module}/overview`);
}

export interface PlatformIntegrationsOverview {
  generatedAt: string;
  summary: { total: number; connected: number; credentialsOnly: number; notConfigured: number };
  providers: Array<{ key: string; label: string; state: string; evidenceLevel: string; detail: string }>;
}

export function fetchPlatformIntegrationsOverview(): Promise<PlatformIntegrationsOverview> {
  return apiFetch('/api/v1/platform/governance/integrations/overview');
}

export function fetchPlatformGovernanceDocument(
  module: PlatformGovernanceModule,
): Promise<PlatformConfigurationDocument> {
  return apiFetch(`/api/v1/platform/governance/${module}`);
}

export function fetchPlatformGovernanceVersionPage(
  module: PlatformGovernanceModule,
  input: { limit?: number; offset?: number } = {},
): Promise<PlatformConfigurationVersionPage> {
  const params = new URLSearchParams({
    limit: String(input.limit ?? 10),
    offset: String(input.offset ?? 0),
  });
  return apiFetch(`/api/v1/platform/governance/${module}/versions/page?${params}`);
}

export function savePlatformGovernanceDocument(
  module: PlatformGovernanceModule,
  payload: Record<string, unknown>,
  options: { reason: string; etag: string | null },
): Promise<PlatformConfigurationDocument> {
  return apiFetch(`/api/v1/platform/governance/${module}`, {
    method: 'PUT',
    headers: options.etag ? { 'If-Match': `"${options.etag}"` } : undefined,
    body: JSON.stringify({ payload, schemaVersion: '1.0', reason: options.reason }),
  });
}

export function rollbackPlatformGovernanceDocument(
  module: PlatformGovernanceModule,
  version: number,
  etag: string,
): Promise<PlatformConfigurationDocument> {
  return apiFetch(`/api/v1/platform/governance/${module}/rollback/${version}`, {
    method: 'POST',
    headers: { 'If-Match': `"${etag}"` },
  });
}

export async function fetchPlatformStaffPackages(): Promise<PlatformRolePackage[]> {
  return apiFetch('/api/v1/platform/staff/packages');
}

export async function fetchPlatformStaffPage(input: {
  search?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<PlatformStaffPage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  if (input.search?.trim()) query.set('search', input.search.trim());
  return apiFetch(`/api/v1/platform/staff/page?${query.toString()}`);
}

export async function createPlatformStaff(payload: {
  name: string;
  email: string;
  rolePackage: string;
  permissionGrants?: Array<{ module: string; action: string }>;
  permissionDenials?: Array<{ module: string; action: string }>;
}): Promise<PlatformStaffUser> {
  return apiFetch('/api/v1/platform/staff', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updatePlatformStaff(
  userId: string,
  payload: {
    reason: string;
    name?: string;
    rolePackage?: string;
    accessActive?: boolean;
    permissionGrants?: Array<{ module: string; action: string }>;
    permissionDenials?: Array<{ module: string; action: string }>;
  },
): Promise<PlatformStaffUser> {
  return apiFetch(`/api/v1/platform/staff/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function revokePlatformStaffSessions(
  userId: string,
  reason: string,
): Promise<{ ok: boolean; revokedCount: number }> {
  return apiFetch(`/api/v1/platform/staff/${userId}/sessions/revoke`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export interface PlatformTemporaryPasswordResult {
  userId: string;
  requiresPasswordChange: true;
  revokedSessionCount: number;
}

export async function setPlatformStaffTemporaryPassword(
  userId: string,
  temporaryPassword: string,
  reason: string,
): Promise<PlatformTemporaryPasswordResult> {
  return apiFetch(`/api/v1/platform/staff/${encodeURIComponent(userId)}/temporary-password`, {
    method: 'POST',
    body: JSON.stringify({ temporaryPassword, reason }),
  });
}

export async function fetchPlatformOverview(): Promise<Record<string, any>> {
  return apiFetch('/api/v1/platform/overview');
}

export interface AiOperationsSummary {
  generated_at: string;
  window_days: number;
  executions: number;
  cost_usd: string;
  fallback_count: number;
  fallback_rate: number;
  billing_unknown: number;
  by_class_provider: Array<{
    task_class: string;
    provider: string;
    executions: number;
    fallback_count: number;
    cost_usd: string;
    billing_unknown: number;
  }>;
  by_agent: Array<{ agent_name: string; executions: number; cost_usd: string }>;
  by_day: Array<{ day: string; executions: number; cost_usd: string; fallback_count: number }>;
  by_workspace: Array<{
    workspace_id: string;
    executions: number;
    cost_usd: string;
    fallback_count: number;
  }>;
  route_alerts: Array<{
    route_key: string;
    provider: string;
    model: string;
    failures: number;
    last_failure_at: string | null;
  }>;
}

export interface AiOperationsEvent {
  id: string;
  workspace_id: string;
  created_at: string;
  agent_name: string;
  task_class: string;
  provider: string;
  model: string;
  outcome: string;
  route_key: string;
  fallback_used: boolean;
  cache_hit: boolean;
  billing_unknown: boolean;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string | null;
  estimated_cost_usd: string | null;
  operation: string;
}

export interface AiOperationsEventPage {
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  items: AiOperationsEvent[];
}

export async function fetchAiOperationsSummary(days = 7): Promise<AiOperationsSummary> {
  return apiFetch(`/api/v1/platform/ai-operations/summary?days=${days}`);
}

export async function fetchAiOperationsEvents(params: {
  days?: number;
  limit?: number;
  offset?: number;
  onlyFallbacks?: boolean;
} = {}): Promise<AiOperationsEventPage> {
  const q = new URLSearchParams();
  q.set('days', String(params.days ?? 7));
  q.set('limit', String(params.limit ?? 50));
  q.set('offset', String(params.offset ?? 0));
  if (params.onlyFallbacks) q.set('only_fallbacks', 'true');
  return apiFetch(`/api/v1/platform/ai-operations/events?${q.toString()}`);
}

export async function fetchPlatformMrr(): Promise<Record<string, any>> {
  return apiFetch('/api/v1/platform/mrr');
}

export async function fetchPlatformTokenUsage(
  days = 30,
  workspaceId?: string | null,
): Promise<Record<string, any>> {
  const params = new URLSearchParams({ days: String(days) });
  if (workspaceId) params.set('workspace_id', workspaceId);
  return apiFetch(`/api/v1/platform/token-usage?${params.toString()}`);
}

export async function fetchPlatformAwsCosts(): Promise<Record<string, any>> {
  return apiFetch('/api/v1/platform/aws-costs');
}

export async function fetchPlatformMetaHealth(
  days = 7,
  workspaceId?: string | null,
  limit = 20,
): Promise<Record<string, any>> {
  const params = new URLSearchParams({ days: String(days), limit: String(limit) });
  if (workspaceId) params.set('workspaceId', workspaceId);
  return apiFetch(`/api/v1/platform/meta-health?${params.toString()}`);
}

export async function fetchPlatformMetaCommercialRateCards(
  includeInactive = true,
): Promise<MetaCommercialRateCard[]> {
  return apiFetch(`/api/v1/platform/meta-commercial-rate-cards?includeInactive=${String(includeInactive)}`);
}

export async function savePlatformMetaCommercialRateCard(
  rateCardKey: string,
  payload: MetaCommercialRateCardUpsert,
): Promise<MetaCommercialRateCard> {
  return apiFetch(`/api/v1/platform/meta-commercial-rate-cards/${encodeURIComponent(rateCardKey)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformAiCosts(
  days = 30,
  workspaceId?: string | null,
): Promise<PlatformAiCostSummary> {
  const params = new URLSearchParams({ days: String(days) });
  if (workspaceId) params.set('workspaceId', workspaceId);
  return apiFetch(`/api/v1/platform/ai-costs?${params.toString()}`);
}

export async function savePlatformWorkspaceAiCostPolicy(
  workspaceId: string,
  payload: WorkspaceAiCostPolicyUpdate,
): Promise<PlatformAiCostSummary> {
  return apiFetch(`/api/v1/platform/ai-costs/${encodeURIComponent(workspaceId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformPendingBillingReconciliations(
  workspaceId: string,
  limit = 50,
  offset = 0,
): Promise<PlatformAgentBillingReconciliationPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiFetch(
    `/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/agent-runs/billing-reconciliation/pending?${params.toString()}`,
  );
}

export async function reconcilePlatformAgentRunBilling(
  workspaceId: string,
  runId: string,
  payload: PlatformAgentBillingReconciliationDecision,
): Promise<PlatformAgentBillingReconciliationRun> {
  return apiFetch(
    `/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/agent-runs/${encodeURIComponent(runId)}/billing-reconciliation`,
    { method: 'POST', body: JSON.stringify(payload) },
  );
}

export async function fetchPlatformAiModels(): Promise<PlatformAiModelsOverview> {
  return apiFetch('/api/v1/platform/ai-models');
}

export async function fetchPlatformAiTokenPackages(): Promise<AiTokenPackage[]> {
  return apiFetch('/api/v1/platform/ai-token-packages');
}

export async function savePlatformAiTokenPackage(
  key: string,
  payload: AiTokenPackageUpsert,
): Promise<AiTokenPackage> {
  return apiFetch(`/api/v1/platform/ai-token-packages/${encodeURIComponent(key)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformWorkspaceAiTokenPackageRequests(
  workspaceId: string,
  options: { limit?: number; offset?: number; status?: string } = {},
): Promise<{
  items: WorkspaceAiTokenPackageRequest[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 10),
    offset: String(options.offset ?? 0),
  });
  if (options.status) params.set('status', options.status);
  return apiFetch(`/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/ai-token-package-requests/page?${params}`);
}

export async function decidePlatformWorkspaceAiTokenPackageRequest(
  workspaceId: string,
  requestId: string,
  decision: 'approved' | 'rejected',
  decisionNote: string,
): Promise<WorkspaceAiTokenPackageRequest> {
  return apiFetch(`/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/ai-token-package-requests/${encodeURIComponent(requestId)}/decision`, {
    method: 'POST',
    body: JSON.stringify({ decision, decisionNote }),
  });
}

export async function revokePlatformWorkspaceAiTokenPackageRequest(
  workspaceId: string,
  requestId: string,
  decisionNote: string,
): Promise<WorkspaceAiTokenPackageRequest> {
  return apiFetch(`/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/ai-token-package-requests/${encodeURIComponent(requestId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ decisionNote }),
  });
}

export interface PlatformWorkspacePage {
  items: PlatformWorkspaceSummary[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: 'asc' | 'desc' }>;
}

export async function fetchPlatformWorkspacePage(options: {
  limit?: number;
  offset?: number;
  search?: string;
  status?: string;
} = {}): Promise<PlatformWorkspacePage> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
  });
  if (options.search?.trim()) params.set('search', options.search.trim());
  if (options.status && options.status !== 'all') params.set('status', options.status);
  return apiFetch(`/api/v1/platform/workspaces/page?${params.toString()}`);
}

export async function fetchPlatformWorkspaceFinanceExecutive(workspaceId: string): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/executive`);
}
export async function fetchPlatformWorkspaceFinanceDrilldown(workspaceId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/drilldown`, { method: 'POST', body: JSON.stringify(payload) });
}
export async function fetchPlatformWorkspaceFinanceDre(workspaceId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/dre`, { method: 'POST', body: JSON.stringify(payload) });
}
export async function generatePlatformWorkspaceFinanceForecast(workspaceId: string, payload: Record<string, unknown>): Promise<Array<Record<string, unknown>>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/forecasts/generate`, { method: 'POST', body: JSON.stringify(payload) });
}
export async function calculatePlatformWorkspaceFinanceValuation(workspaceId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/valuations/calculate`, { method: 'POST', body: JSON.stringify(payload) });
}
export async function getPlatformWorkspaceFinanceTaxProfile(workspaceId: string): Promise<Array<Record<string, unknown>>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/tax-profile`);
}
export async function updatePlatformWorkspaceFinanceTaxProfile(workspaceId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/tax-profile`, { method: 'PUT', body: JSON.stringify(payload) });
}
export async function simulatePlatformWorkspaceFinanceTaxReform(workspaceId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/tax-reform/simulate`, { method: 'POST', body: JSON.stringify(payload) });
}
export async function listPlatformWorkspaceFinanceBudgetLines(workspaceId: string): Promise<Array<Record<string, unknown>>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/budget-lines`);
}
export async function createPlatformWorkspaceFinanceBudgetLine(workspaceId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/budget-lines`, { method: 'POST', body: JSON.stringify(payload) });
}
export async function fetchPlatformWorkspaceFinanceInsights(workspaceId: string): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/insights`);
}
export async function createPlatformWorkspaceFinanceExport(workspaceId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/exports`, { method: 'POST', body: JSON.stringify(payload) });
}
export async function listPlatformWorkspaceFinanceExports(workspaceId: string, options: { limit?: number; offset?: number } = {}): Promise<{ items: Array<Record<string, unknown>>; total: number; limit: number; offset: number; hasMore: boolean }> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50), offset: String(options.offset ?? 0) });
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/exports?${params.toString()}`);
}
export async function getPlatformWorkspaceFinanceExport(workspaceId: string, jobId: string): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/exports/${encodeURIComponent(jobId)}`);
}
export async function cancelPlatformWorkspaceFinanceExport(workspaceId: string, jobId: string): Promise<Record<string, unknown>> { return apiFetch(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/exports/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }); }
export async function downloadPlatformWorkspaceFinanceExport(workspaceId: string, jobId: string, format: string): Promise<void> {
  const result = await apiBlob(`/api/v1/platform/finance/workspaces/${encodeURIComponent(workspaceId)}/exports/${encodeURIComponent(jobId)}/download`);
  const url = URL.createObjectURL(result.blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = result.filename ?? `finance-${jobId}.${format}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

// ── B1: Centro de Comando — Financeiro / Assinaturas / Pessoas / 360 ─────

export async function fetchPlatformBillingOverview(periodDays = 30): Promise<Record<string, any>> {
  return apiFetch(`/api/v1/platform/billing/overview?periodDays=${periodDays}`);
}

// ── Bateria de KPIs do Super Admin (~150 indicadores por categoria) ──────

export interface PlatformKpi {
  key: string;
  label: string;
  category: string;
  value: number | null;
  unit: 'brl' | 'pct' | 'count' | 'number';
  delta_pct: number | null;
  series: Array<{ period: string; value: number | null }> | null;
  sem_dado: boolean;
  nota: string | null;
  definition: string;
  formula: string;
  source: string;
  period: string;
  filters: string[];
  updated_at: string;
  quality: 'atualizado' | 'desatualizado' | 'parcial' | 'estimado' | 'indisponivel';
  coverage: string;
  owner: string;
  drill_down: string | null;
}

export interface PlatformKpiCategory {
  key: string;
  label: string;
  count: number;
  kpis: PlatformKpi[];
}

export interface PlatformKpiResponse {
  generated_at: string;
  period_days: number;
  total_kpis: number;
  categories: PlatformKpiCategory[];
}

export async function fetchPlatformMetrics(periodDays = 30): Promise<PlatformKpiResponse> {
  return apiFetch(`/api/v1/platform/metrics?periodDays=${periodDays}`);
}

export interface PlatformSubscriptionFilters {
  plano?: string;
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export async function fetchPlatformSubscriptions(
  filters: PlatformSubscriptionFilters = {},
): Promise<{
  items: Record<string, any>[];
  count: number;
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}> {
  const params = new URLSearchParams();
  if (filters.plano) params.set('plano', filters.plano);
  if (filters.status) params.set('status', filters.status);
  if (filters.search) params.set('search', filters.search);
  params.set('limit', String(filters.limit ?? 50));
  params.set('offset', String(filters.offset ?? 0));
  return apiFetch(`/api/v1/platform/subscriptions?${params.toString()}`);
}

export async function changeSubscriptionPlan(
  workspaceId: string,
  payload: { novoPlano: string; motivo: string; valorMensalCents?: number | null },
): Promise<Record<string, any>> {
  return apiFetch(`/api/v1/platform/subscriptions/${encodeURIComponent(workspaceId)}/plan`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function applySubscriptionDiscount(
  workspaceId: string,
  payload: { descontoPct: number; cortesia: boolean; motivo: string },
): Promise<Record<string, any>> {
  return apiFetch(`/api/v1/platform/subscriptions/${encodeURIComponent(workspaceId)}/discount`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function extendSubscriptionTrial(
  workspaceId: string,
  payload: { dias: number; motivo: string },
): Promise<Record<string, any>> {
  return apiFetch(`/api/v1/platform/subscriptions/${encodeURIComponent(workspaceId)}/extend-trial`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function suspendSubscription(
  workspaceId: string,
  payload: { motivo: string },
): Promise<Record<string, any>> {
  return apiFetch(`/api/v1/platform/subscriptions/${encodeURIComponent(workspaceId)}/suspend`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function reactivateSubscription(
  workspaceId: string,
  payload: { motivo: string },
): Promise<Record<string, any>> {
  return apiFetch(`/api/v1/platform/subscriptions/${encodeURIComponent(workspaceId)}/reactivate`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export interface PlatformSubscriptionEvent {
  id: string;
  eventType: string;
  actorLabel: string | null;
  motivo: string | null;
  occurredAt: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface PlatformSubscriptionEventPage {
  items: PlatformSubscriptionEvent[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export async function fetchPlatformSubscriptionEventsPage(
  workspaceId: string,
  limit = 10,
  offset = 0,
): Promise<PlatformSubscriptionEventPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiFetch(
    `/api/v1/platform/subscriptions/${encodeURIComponent(workspaceId)}/events/page?${params.toString()}`,
  );
}

export async function fetchWorkspace360(workspaceId: string): Promise<Record<string, any>> {
  return apiFetch(`/api/v1/platform/workspace-360/${encodeURIComponent(workspaceId)}`);
}

// ── Conta 360 (Blackbox account drawer) — agregado da conta + overrides ──

export async function fetchWorkspaceOverview(workspaceId: string): Promise<WorkspaceOverviewResponse> {
  return apiFetch(`/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/overview`);
}

export async function fetchPlatformWorkspaceUtilityTemplateCatalog(
  workspaceId: string,
): Promise<UtilityTemplateCatalog> {
  return apiFetch(
    `/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/whatsapp/utility-template-library`,
  );
}

export async function retryPlatformWorkspaceUtilityTemplate(
  workspaceId: string,
  catalogKey: string,
): Promise<UtilityTemplateCatalog> {
  return apiFetch(
    `/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/whatsapp/utility-template-library/${encodeURIComponent(catalogKey)}/retry`,
    { method: 'POST' },
  );
}

export async function putWorkspaceEntitlementOverrides(
  workspaceId: string,
  overrides: EntitlementOverrideWriteItem[],
): Promise<{ overrides: EntitlementOverrideWriteResult[] }> {
  return apiFetch(`/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/entitlement-overrides`, {
    method: 'PUT',
    body: JSON.stringify({ overrides }),
  });
}

/** Bônus de quota de harmonização financeira (F14) — concessão manual root. */
export async function grantWorkspaceFinanceHarmonizationBonus(
  workspaceId: string,
  payload: { competence: string; extra: number; reason: string },
): Promise<PlatformFinanceHarmonizationQuota> {
  return apiFetch(
    `/api/v1/platform/workspaces/${encodeURIComponent(workspaceId)}/finance-harmonization-quota/bonus`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );
}

export interface PlatformPeopleFilters {
  search?: string;
  role?: string;
  workspaceId?: string;
  limit?: number;
  offset?: number;
}

export interface PlatformPerson {
  user_id: string;
  name: string;
  email: string | null;
  is_active: boolean;
  workspace_id: string;
  workspace_name: string;
  role: string;
  access_package: string | null;
  membership_status: string;
  last_seen_at: string | null;
  active_session_count: number;
  invitation_id: string | null;
  invitation_status: string | null;
  invitation_expires_at: string | null;
}

export interface PlatformAccessPackage {
  key: string;
  label: string;
  description: string;
  workspaceRole: string;
  sensitiveDataPolicy: string;
}

export async function fetchPlatformPeople(
  filters: PlatformPeopleFilters = {},
): Promise<{
  items: PlatformPerson[];
  count: number;
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}> {
  const params = new URLSearchParams();
  if (filters.search) params.set('search', filters.search);
  if (filters.role) params.set('role', filters.role);
  if (filters.workspaceId) params.set('workspaceId', filters.workspaceId);
  params.set('limit', String(filters.limit ?? 50));
  params.set('offset', String(filters.offset ?? 0));
  return apiFetch(`/api/v1/platform/people?${params.toString()}`);
}

export async function fetchPlatformPeopleAccessPackages(): Promise<PlatformAccessPackage[]> {
  return apiFetch('/api/v1/platform/people/access-packages');
}

export interface PlatformPersonInvitation {
  id: string;
  workspace_id: string;
  membership_id: string;
  user_id: string;
  email: string;
  name: string;
  status: string;
  expires_at: string;
  send_count: number;
}

function platformPeopleMutationKey(operation: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `blackbox-people-${operation}-${random}`;
}

export async function invitePlatformPerson(payload: {
  workspaceId: string;
  name: string;
  email: string;
  accessPackage: string;
}): Promise<PlatformPersonInvitation> {
  return apiFetch('/api/v1/platform/people/invitations', {
    method: 'POST',
    headers: { 'Idempotency-Key': platformPeopleMutationKey('invite') },
    body: JSON.stringify(payload),
  });
}

export async function resendPlatformPersonInvitation(
  workspaceId: string,
  invitationId: string,
): Promise<PlatformPersonInvitation> {
  return apiFetch(
    `/api/v1/platform/people/invitations/${encodeURIComponent(workspaceId)}/${encodeURIComponent(invitationId)}/resend`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': platformPeopleMutationKey('resend') },
    },
  );
}

export async function revokePlatformPersonInvitation(
  workspaceId: string,
  invitationId: string,
): Promise<PlatformPersonInvitation> {
  return apiFetch(
    `/api/v1/platform/people/invitations/${encodeURIComponent(workspaceId)}/${encodeURIComponent(invitationId)}`,
    { method: 'DELETE' },
  );
}

export async function setPlatformPersonBlocked(
  workspaceId: string,
  userId: string,
  blocked: boolean,
  reason: string,
): Promise<Record<string, any>> {
  return apiFetch(
    `/api/v1/platform/people/${encodeURIComponent(workspaceId)}/${encodeURIComponent(userId)}/block`,
    { method: 'PATCH', body: JSON.stringify({ blocked, reason }) },
  );
}

export async function setPlatformPersonAccessPackage(
  workspaceId: string,
  userId: string,
  accessPackage: string,
  reason: string,
): Promise<Record<string, any>> {
  return apiFetch(
    `/api/v1/platform/people/${encodeURIComponent(workspaceId)}/${encodeURIComponent(userId)}/access-package`,
    { method: 'PATCH', body: JSON.stringify({ accessPackage, reason }) },
  );
}

export async function revokePlatformPersonSessions(
  workspaceId: string,
  userId: string,
  reason: string,
): Promise<{ user_id: string; workspace_id: string; revoked_count: number }> {
  return apiFetch(
    `/api/v1/platform/people/${encodeURIComponent(workspaceId)}/${encodeURIComponent(userId)}/sessions/revoke`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  );
}

export async function setPlatformPersonTemporaryPassword(
  workspaceId: string,
  userId: string,
  temporaryPassword: string,
  reason: string,
): Promise<PlatformTemporaryPasswordResult> {
  return apiFetch(
    `/api/v1/platform/people/${encodeURIComponent(workspaceId)}/${encodeURIComponent(userId)}/temporary-password`,
    { method: 'POST', body: JSON.stringify({ temporaryPassword, reason }) },
  );
}

export async function transferPlatformWorkspaceOwnership(
  workspaceId: string,
  userId: string,
  reason: string,
): Promise<{ workspace_id: string; owner_user_id: string; demoted_user_ids: string[]; revoked_sessions: number }> {
  return apiFetch(
    `/api/v1/platform/people/${encodeURIComponent(workspaceId)}/${encodeURIComponent(userId)}/transfer-ownership`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  );
}

export async function fetchPlatformPlans(): Promise<WorkspacePlanCatalogItem[]> {
  return apiFetch('/api/v1/platform/plans');
}

export async function savePlatformPlan(
  key: string,
  payload: Partial<WorkspacePlanCatalogItem>,
): Promise<WorkspacePlanCatalogItem> {
  return apiFetch(`/api/v1/platform/plans/${key}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function createPlatformWorkspace(
  payload: Record<string, unknown>,
): Promise<PlatformWorkspaceSummary> {
  return apiFetch('/api/v1/platform/workspaces', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updatePlatformWorkspace(
  workspaceId: string,
  payload: Record<string, unknown>,
): Promise<PlatformWorkspaceSummary> {
  return apiFetch(`/api/v1/platform/workspaces/${workspaceId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export type PlatformWorkspaceBulkAction =
  | 'suspend'
  | 'reactivate'
  | 'complete_onboarding'
  | 'reopen_onboarding'
  | 'set_plan';

export type PlatformBulkResourceType =
  | 'workspace'
  | 'customer_user'
  | 'staff'
  | 'lead'
  | 'ticket'
  | 'subscription'
  | 'workspace_invitation'
  | 'staff_invitation';

export interface PlatformSelectionSet {
  id: string;
  resourceType: PlatformBulkResourceType;
  mode: 'explicit' | 'filter';
  filterHash: string;
  targetCount: number;
  status: string;
  expiresAt: string;
}

export interface PlatformBulkPreview {
  id: string;
  selectionSetId: string;
  resourceType: PlatformBulkResourceType;
  action: string;
  status: 'building' | 'ready' | 'invalid' | 'expired';
  risk: 'low' | 'high' | 'critical';
  targetCount: number;
  eligibleCount: number;
  noOpCount: number;
  blockedCount: number;
  warnings: Array<{ code?: string; message?: string }>;
  blockers: Array<{ resourceId: string; label?: string; state?: string; reason: string | null }>;
  impactSummary: Record<string, unknown>;
  sample: Array<{ resourceId: string; label: string; status?: string; state?: string; reason: string | null }>;
  confirmationPhrase: string | null;
  expiresAt: string;
}

export interface PlatformBulkJobItem {
  id: string;
  resourceId: string;
  ordinal: number;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'conflict' | 'skipped' | 'cancelled' | 'compensated';
  errorCode: string | null;
  errorDetail: string | null;
}

export interface PlatformBulkJob {
  id: string;
  resourceType: PlatformBulkResourceType;
  action: string;
  reason: string;
  status: 'queued' | 'running' | 'cancelling' | 'succeeded' | 'partial' | 'failed' | 'cancelled';
  requestedCount: number;
  eligibleCount: number;
  processedCount: number;
  succeededCount: number;
  failedCount: number;
  conflictedCount: number;
  skippedCount: number;
  cancelledCount: number;
  progressPercent: number;
  correlationId: string;
  heartbeatAt: string | null;
  items: PlatformBulkJobItem[];
}

export async function createPlatformWorkspaceSelection(payload: {
  mode: 'explicit' | 'filter';
  ids?: string[];
  filters?: Record<string, unknown>;
  excludedIds?: string[];
}): Promise<PlatformSelectionSet> {
  return createPlatformSelection({ resourceType: 'workspace', ...payload });
}

export async function createPlatformSelection(payload: {
  resourceType: PlatformBulkResourceType;
  mode: 'explicit' | 'filter';
  ids?: string[];
  filters?: Record<string, unknown>;
  excludedIds?: string[];
}): Promise<PlatformSelectionSet> {
  return apiFetch('/api/v1/platform/operations/selections', {
    method: 'POST',
    body: JSON.stringify({
      resourceType: payload.resourceType,
      mode: payload.mode,
      ids: payload.ids ?? [],
      filters: payload.filters ?? {},
      sort: [{ field: 'createdAt', direction: 'desc' }],
      excludedIds: payload.excludedIds ?? [],
    }),
  });
}

export async function createPlatformBulkPreview(payload: {
  selectionSetId: string;
  action: string;
  parameters?: Record<string, unknown>;
}): Promise<PlatformBulkPreview> {
  return apiFetch('/api/v1/platform/operations/bulk/previews', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformBulkPreview(previewId: string): Promise<PlatformBulkPreview> {
  return apiFetch(`/api/v1/platform/operations/bulk/previews/${encodeURIComponent(previewId)}`);
}

export async function createPlatformBulkJob(payload: {
  previewId: string;
  reason: string;
  confirmationPhrase?: string | null;
  idempotencyKey: string;
}): Promise<PlatformBulkJob> {
  return apiFetch('/api/v1/platform/operations/bulk/jobs', {
    method: 'POST',
    headers: { 'Idempotency-Key': payload.idempotencyKey },
    body: JSON.stringify({
      previewId: payload.previewId,
      reason: payload.reason,
      confirmationPhrase: payload.confirmationPhrase,
    }),
  });
}

export async function fetchPlatformBulkJob(jobId: string): Promise<PlatformBulkJob> {
  return apiFetch(`/api/v1/platform/operations/bulk/jobs/${encodeURIComponent(jobId)}`);
}

export async function fetchPlatformBulkJobs(limit = 50, offset = 0): Promise<{
  items: PlatformBulkJob[];
  total: number;
  limit: number;
  offset: number;
}> {
  return apiFetch(`/api/v1/platform/operations/bulk/jobs?limit=${limit}&offset=${offset}`);
}

export async function cancelPlatformBulkJob(jobId: string): Promise<PlatformBulkJob> {
  return apiFetch(`/api/v1/platform/operations/bulk/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  });
}

export async function createPlatformBulkRetryPreview(jobId: string): Promise<PlatformBulkPreview> {
  return apiFetch(`/api/v1/platform/operations/bulk/jobs/${encodeURIComponent(jobId)}/retry-failures`, {
    method: 'POST',
  });
}

export async function createPlatformBulkRestorationPreview(jobId: string): Promise<PlatformBulkPreview> {
  return apiFetch(`/api/v1/platform/operations/bulk/jobs/${encodeURIComponent(jobId)}/restoration-preview`, {
    method: 'POST',
  });
}

export async function restorePlatformBulkJob(payload: {
  jobId: string;
  previewId: string;
  reason: string;
  confirmationPhrase: string;
  idempotencyKey: string;
}): Promise<PlatformBulkJob> {
  return apiFetch(`/api/v1/platform/operations/bulk/jobs/${encodeURIComponent(payload.jobId)}/restore`, {
    method: 'POST',
    headers: { 'Idempotency-Key': payload.idempotencyKey },
    body: JSON.stringify({
      previewId: payload.previewId,
      reason: payload.reason,
      confirmationPhrase: payload.confirmationPhrase,
    }),
  });
}

export async function fetchPlatformSiteSettings(): Promise<PlatformSiteDocumentEnvelope<PublicSiteSettings>> {
  return apiFetch('/api/v1/platform/site/settings');
}

export async function savePlatformSiteSettings(
  payload: PublicSiteSettings,
  reason: string,
): Promise<PlatformSiteDocumentEnvelope<PublicSiteSettings>> {
  return apiFetch('/api/v1/platform/site/settings', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformSiteHome(): Promise<PlatformSiteDocumentEnvelope<PublicHomeDocument>> {
  return apiFetch('/api/v1/platform/site/home');
}

export async function savePlatformSiteHome(
  payload: PublicHomeDocument,
  reason: string,
): Promise<PlatformSiteDocumentEnvelope<PublicHomeDocument>> {
  return apiFetch('/api/v1/platform/site/home', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformSiteFaq(): Promise<PlatformSiteDocumentEnvelope<PublicFaqDocument>> {
  return apiFetch('/api/v1/platform/site/faq');
}

export async function savePlatformSiteFaq(
  payload: PublicFaqDocument,
  reason: string,
): Promise<PlatformSiteDocumentEnvelope<PublicFaqDocument>> {
  return apiFetch('/api/v1/platform/site/faq', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformSiteMedia(): Promise<PlatformSiteDocumentEnvelope<PublicMediaLibrary>> {
  return apiFetch('/api/v1/platform/site/media');
}

export async function savePlatformSiteMedia(
  payload: PublicMediaLibrary,
  reason: string,
): Promise<PlatformSiteDocumentEnvelope<PublicMediaLibrary>> {
  return apiFetch('/api/v1/platform/site/media', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformSiteLegal(
  kind: 'terms' | 'privacy',
): Promise<PlatformSiteDocumentEnvelope<PublicLegalDocument>> {
  return apiFetch(`/api/v1/platform/site/legal/${kind}`);
}

export async function savePlatformSiteLegal(
  kind: 'terms' | 'privacy',
  payload: PublicLegalDocument,
  reason: string,
): Promise<PlatformSiteDocumentEnvelope<PublicLegalDocument>> {
  return apiFetch(`/api/v1/platform/site/legal/${kind}`, {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function publishPlatformSiteResource(
  resource: 'settings' | 'home' | 'faq' | 'media' | 'terms' | 'privacy',
  reason: string,
): Promise<PlatformSiteDocumentEnvelope> {
  return apiFetch(`/api/v1/platform/site/publish/${resource}`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export interface PlatformContentPage<T> { items: T[]; total: number; limit: number; offset: number; hasMore: boolean }

export async function fetchPlatformBlogPostsPage(limit = 25, offset = 0): Promise<PlatformContentPage<BlogPostSummary>> {
  return apiFetch(`/api/v1/platform/blog/posts/page?limit=${limit}&offset=${offset}`);
}

export interface PlatformBlogPostMetrics {
  total: number;
  indexable: number;
  includedInSitemap: number;
  includedInLlms: number;
  scored: number;
  averageSeoScore: number | null;
  dataState: 'available' | 'insufficient_data';
}

export async function fetchPlatformBlogPostMetrics(): Promise<PlatformBlogPostMetrics> {
  return apiFetch('/api/v1/platform/blog/posts/metrics');
}

export async function fetchPlatformBlogPost(postId: string): Promise<BlogPost> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}`);
}

export async function createPlatformBlogPost(payload: Record<string, unknown>, reason: string): Promise<BlogPost> {
  return apiFetch('/api/v1/platform/blog/posts', {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function savePlatformBlogPost(postId: string, payload: Record<string, unknown>, reason: string): Promise<BlogPost> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}`, {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function publishPlatformBlogPost(postId: string, reason: string): Promise<BlogPost> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}/publish`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function schedulePlatformBlogPost(postId: string, publishAt: string, reason: string): Promise<BlogPost> {
  const query = new URLSearchParams({ publishAt }).toString();
  return apiFetch(`/api/v1/platform/blog/posts/${postId}/schedule?${query}`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function archivePlatformBlogPost(postId: string, reason: string): Promise<BlogPost> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}/archive`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function duplicatePlatformBlogPost(postId: string, reason: string): Promise<BlogPost> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}/duplicate`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function autofillPlatformBlogSeo(postId: string, reason: string): Promise<SeoAutofillResponse> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}/seo-autofill`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function assistPlatformBlogPost(
  postId: string,
  payload: EditorialAssistRequestPayload,
  reason: string,
): Promise<EditorialAssistResponse> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}/assist`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function generatePlatformBlogCover(postId: string, reason: string): Promise<GeneratedImageResponse> {
  return apiFetch(`/api/v1/platform/blog/posts/${postId}/generate-cover`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function fetchPlatformBlogSettings(): Promise<PlatformDocumentEnvelope<BlogSettings>> {
  return apiFetch('/api/v1/platform/blog/settings');
}

export async function savePlatformBlogSettings(payload: BlogSettings, reason: string): Promise<PlatformDocumentEnvelope<BlogSettings>> {
  return apiFetch('/api/v1/platform/blog/settings', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformBlogTaxonomy(): Promise<PlatformDocumentEnvelope<BlogTaxonomy>> {
  return apiFetch('/api/v1/platform/blog/taxonomy');
}

export async function savePlatformBlogTaxonomy(payload: BlogTaxonomy, reason: string): Promise<PlatformDocumentEnvelope<BlogTaxonomy>> {
  return apiFetch('/api/v1/platform/blog/taxonomy', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformBlogIndexing(): Promise<PlatformDocumentEnvelope<BlogIndexingSettings>> {
  return apiFetch('/api/v1/platform/blog/indexing');
}

export async function savePlatformBlogIndexing(
  payload: BlogIndexingSettings,
  reason: string,
): Promise<PlatformDocumentEnvelope<BlogIndexingSettings>> {
  return apiFetch('/api/v1/platform/blog/indexing', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function publishPlatformBlogResource(
  resource: 'blog_settings' | 'blog_taxonomy' | 'blog_indexing',
  reason: string,
): Promise<PlatformDocumentEnvelope> {
  return apiFetch(`/api/v1/platform/blog/publish/${resource}`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function fetchPlatformBlogMediaAssetsPage(limit = 24, offset = 0): Promise<PlatformContentPage<SiteMediaAsset>> {
  return apiFetch(`/api/v1/platform/blog/media/assets/page?limit=${limit}&offset=${offset}`);
}

export async function createPlatformBlogMediaAsset(payload: Record<string, unknown>, reason: string): Promise<SiteMediaPresign> {
  return apiFetch('/api/v1/platform/blog/media/assets', {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function confirmPlatformBlogMediaAsset(assetId: string, reason: string): Promise<SiteMediaConfirm> {
  return apiFetch(`/api/v1/platform/blog/media/assets/${assetId}/confirm`, {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
  });
}

export async function fetchPlatformGoogleBusinessConnection(): Promise<GoogleBusinessProfileConnection> {
  return apiFetch('/api/v1/platform/blog/google-business');
}

export async function savePlatformGoogleBusinessConnection(
  payload: GoogleBusinessConnectionUpdate,
  reason: string,
): Promise<GoogleBusinessProfileConnection> {
  return apiFetch('/api/v1/platform/blog/google-business', {
    method: 'PUT',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformGoogleBusinessJobsPage(limit = 25, offset = 0): Promise<PlatformContentPage<GoogleBusinessPublishJob>> {
  return apiFetch(`/api/v1/platform/blog/google-business/jobs/page?limit=${limit}&offset=${offset}`);
}

export async function queuePlatformGoogleBusinessJob(
  payload: GoogleBusinessPublishRequest,
  reason: string,
): Promise<GoogleBusinessPublishJob> {
  return apiFetch('/api/v1/platform/blog/google-business/jobs', {
    method: 'POST',
    headers: { 'X-Operation-Reason': encodeURIComponent(reason) },
    body: JSON.stringify(payload),
  });
}

export async function fetchPlatformOfficialWhatsAppConnections(): Promise<PlatformOfficialWhatsAppConnection[]> {
  return apiFetch('/api/v1/platform/official-whatsapp');
}

export async function fetchPlatformWhatsAppMetaOperations(): Promise<import('./whatsapp').WhatsAppMetaOperations> {
  return apiFetch('/api/v1/platform/official-whatsapp/meta-operations');
}

export async function fetchPlatformWorkspaceWhatsAppMetaOperations(workspaceId: string): Promise<import('./whatsapp').WhatsAppMetaOperations> {
  return apiFetch(`/api/v1/platform/official-whatsapp/meta-operations?workspace_id=${encodeURIComponent(workspaceId)}`);
}

export async function previewPlatformWhatsAppMetaSubscriptions(workspaceId: string): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/official-whatsapp/subscriptions/preview?workspace_id=${encodeURIComponent(workspaceId)}`, { method: 'POST' });
}

export async function reconcilePlatformWhatsAppMetaSubscriptions(workspaceId: string): Promise<import('./whatsapp').WhatsAppMetaSubscriptionJob> {
  return apiFetch(`/api/v1/platform/official-whatsapp/subscriptions/reconcile?workspace_id=${encodeURIComponent(workspaceId)}`, { method: 'POST' });
}

export async function retryPlatformWhatsAppMetaReceipt(workspaceId: string, receiptId: string): Promise<import('./whatsapp').WhatsAppMetaWebhookReceipt> {
  return apiFetch(`/api/v1/platform/official-whatsapp/meta-operations/receipts/${encodeURIComponent(receiptId)}/retry?workspace_id=${encodeURIComponent(workspaceId)}`, { method: 'POST' });
}

export async function updatePlatformWhatsAppMetaAutomationPolicy(
  workspaceId: string,
  field: string,
  payload: {
    enabled: boolean;
    mode: 'observe_only' | 'suggest_action' | 'approval_each_time';
    minimumSeverity: 'info' | 'warning' | 'high' | 'critical';
    expectedVersion: number;
  },
): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/platform/official-whatsapp/automation-policies/${encodeURIComponent(field)}?workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function connectPlatformOfficialWhatsAppManual(
  role: PlatformOfficialWhatsAppRole,
  payload: PlatformOfficialWhatsAppConnectManual,
): Promise<PlatformOfficialWhatsAppConnection> {
  return apiFetch(`/api/v1/platform/official-whatsapp/${role}/manual`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function connectPlatformOfficialWhatsAppEmbedded(
  role: PlatformOfficialWhatsAppRole,
  payload: PlatformOfficialWhatsAppEmbeddedCallback,
): Promise<PlatformOfficialWhatsAppConnection> {
  return apiFetch(`/api/v1/platform/official-whatsapp/${role}/embedded-signup`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function setPlatformOfficialWhatsAppActive(
  role: PlatformOfficialWhatsAppRole,
  payload: PlatformOfficialWhatsAppActiveUpdate,
): Promise<PlatformOfficialWhatsAppConnection> {
  return apiFetch(`/api/v1/platform/official-whatsapp/${role}/active`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function disconnectPlatformOfficialWhatsApp(
  role: PlatformOfficialWhatsAppRole,
): Promise<PlatformOfficialWhatsAppConnection> {
  return apiFetch(`/api/v1/platform/official-whatsapp/${role}`, {
    method: 'DELETE',
  });
}

const pendingOfficialWhatsAppTestKeys = new Map<string, string>();
const OFFICIAL_WHATSAPP_TEST_STORAGE_KEY =
  'meuexecutivo:official-whatsapp-test-idempotency:v1';
let officialWhatsAppTestKeysHydrated = false;

function officialWhatsAppTestFingerprint(
  role: PlatformOfficialWhatsAppRole,
  payload: PlatformOfficialWhatsAppTestSend,
): string {
  const value = JSON.stringify([role, payload.to, payload.body]);
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function persistOfficialWhatsAppTestKeys(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(
      OFFICIAL_WHATSAPP_TEST_STORAGE_KEY,
      JSON.stringify(Array.from(pendingOfficialWhatsAppTestKeys.entries()).slice(-20)),
    );
  } catch {
    // The in-memory key still protects retry inside the current page session.
  }
}

function hydrateOfficialWhatsAppTestKeys(): void {
  if (officialWhatsAppTestKeysHydrated) return;
  officialWhatsAppTestKeysHydrated = true;
  if (typeof window === 'undefined') return;
  try {
    const raw = window.sessionStorage.getItem(OFFICIAL_WHATSAPP_TEST_STORAGE_KEY);
    const entries: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(entries)) return;
    for (const entry of entries.slice(-20)) {
      if (
        Array.isArray(entry)
        && entry.length === 2
        && typeof entry[0] === 'string'
        && typeof entry[1] === 'string'
        && entry[0].length <= 32
        && entry[1].length <= 200
      ) {
        pendingOfficialWhatsAppTestKeys.set(entry[0], entry[1]);
      }
    }
  } catch {
    // Invalid or inaccessible storage must not block the control plane.
  }
}

export async function testPlatformOfficialWhatsApp(
  role: PlatformOfficialWhatsAppRole,
  payload: PlatformOfficialWhatsAppTestSend,
): Promise<PlatformOfficialWhatsAppTestResult> {
  hydrateOfficialWhatsAppTestKeys();
  const fingerprint = officialWhatsAppTestFingerprint(role, payload);
  let idempotencyKey = pendingOfficialWhatsAppTestKeys.get(fingerprint);
  if (!idempotencyKey) {
    idempotencyKey = `official-wa-test:${globalThis.crypto.randomUUID()}`;
    pendingOfficialWhatsAppTestKeys.set(fingerprint, idempotencyKey);
    persistOfficialWhatsAppTestKeys();
  }
  const result = await apiFetch<PlatformOfficialWhatsAppTestResult>(
    `/api/v1/platform/official-whatsapp/${role}/test`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    },
  );
  pendingOfficialWhatsAppTestKeys.delete(fingerprint);
  persistOfficialWhatsAppTestKeys();
  return result;
}

export function platformOfficialWhatsAppUncertainOperationId(
  error: unknown,
): string | null {
  if (!error || typeof error !== 'object') return null;
  const candidate = error as { status?: unknown; detail?: unknown };
  if (candidate.status !== 409 || !candidate.detail || typeof candidate.detail !== 'object') {
    return null;
  }
  const detail = candidate.detail as { code?: unknown; operationId?: unknown };
  if (
    detail.code !== 'PLATFORM-WA-DELIVERY-UNCERTAIN'
    || typeof detail.operationId !== 'string'
  ) {
    return null;
  }
  return detail.operationId;
}

// ── Growth CRM da plataforma — funil de aquisição B2B dos escritórios ────
// "Casa de ferreiro, espeto de pau": o Super Admin ganha aqui o mesmo tipo
// de funil que o KLM Hub vende para os escritórios, aplicado à própria
// captação de escritórios como clientes. Staff-only.

export interface GrowthProspect {
  id: string;
  nome_escritorio: string;
  contato_nome: string | null;
  contato_email: string | null;
  contato_telefone: string | null;
  cidade: string | null;
  uf: string | null;
  tamanho_estimado: string | null;
  origem: string;
  estagio: string;
  valor_estimado_cents: number;
  valor_estimado_brl: number;
  motivo_perda: string | null;
  notas: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  workspace_id: string | null;
  stage_entered_at: string | null;
  won_at: string | null;
  lost_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  score: number | null;
  next_action: string | null;
  next_action_at: string | null;
  campaign: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  proposal_status: string | null;
  proposal_reference: string | null;
  proposal_value_cents: number | null;
  activities?: GrowthActivity[];
  stage_events?: GrowthStageEvent[];
}

export interface GrowthActivity {
  id: string;
  prospect_id: string;
  tipo: string;
  descricao: string;
  actor_user_id: string | null;
  actor_label: string;
  occurred_at: string | null;
}

export interface GrowthStageEvent {
  id: string;
  prospect_id: string;
  from_estagio: string | null;
  to_estagio: string;
  actor_user_id: string | null;
  actor_label: string;
  motivo: string;
  occurred_at: string | null;
}

export interface GrowthFunnelMetrics {
  total_prospects: number;
  won: number;
  lost: number;
  win_rate_pct: number;
  conversion_prospect_to_pagante_pct: number;
  pipeline_estimado_cents: number;
  pipeline_estimado_brl: number;
  by_stage: Record<string, { count: number; valor_estimado_cents: number }>;
  by_origem: Record<string, number>;
  avg_days_per_stage: Record<string, number>;
  period_days: number;
}

export const GROWTH_FUNNEL_STAGES = [
  'prospect',
  'contatado',
  'demo_agendada',
  'trial',
  'proposta',
  'pagante',
  'perdido',
] as const;

export type GrowthFunnelStage = (typeof GROWTH_FUNNEL_STAGES)[number];

export async function fetchGrowthFunnelMetrics(periodDays = 90): Promise<GrowthFunnelMetrics> {
  return apiFetch(`/api/v1/platform/growth/funnel-metrics?periodDays=${periodDays}`);
}

export interface GrowthProspectFilters {
  estagio?: string;
  origem?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export async function fetchGrowthProspects(
  filters: GrowthProspectFilters = {},
): Promise<{
  items: GrowthProspect[];
  count: number;
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}> {
  const params = new URLSearchParams();
  if (filters.estagio) params.set('estagio', filters.estagio);
  if (filters.origem) params.set('origem', filters.origem);
  if (filters.search) params.set('search', filters.search);
  params.set('limit', String(filters.limit ?? 100));
  params.set('offset', String(filters.offset ?? 0));
  return apiFetch(`/api/v1/platform/growth/prospects?${params.toString()}`);
}

export async function fetchGrowthProspect(prospectId: string): Promise<GrowthProspect> {
  return apiFetch(`/api/v1/platform/growth/prospects/${encodeURIComponent(prospectId)}`);
}

export interface GrowthProspectCreatePayload {
  nomeEscritorio: string;
  contatoNome?: string;
  contatoEmail?: string;
  contatoTelefone?: string;
  cidade?: string;
  uf?: string;
  tamanhoEstimado?: string;
  origem?: string;
  valorEstimadoCents?: number;
  ownerUserId?: string | null;
  notas?: string;
  score?: number | null;
  nextAction?: string | null;
  nextActionAt?: string | null;
  campaign?: string | null;
  utmSource?: string | null;
  utmMedium?: string | null;
  utmCampaign?: string | null;
  proposalStatus?: string | null;
  proposalReference?: string | null;
  proposalValueCents?: number | null;
}

export async function createGrowthProspect(
  payload: GrowthProspectCreatePayload,
): Promise<GrowthProspect> {
  return apiFetch('/api/v1/platform/growth/prospects', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateGrowthProspect(
  prospectId: string,
  payload: Partial<GrowthProspectCreatePayload> & { workspaceId?: string },
): Promise<GrowthProspect> {
  return apiFetch(`/api/v1/platform/growth/prospects/${encodeURIComponent(prospectId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function moveGrowthProspectStage(
  prospectId: string,
  payload: { toEstagio: string; motivo?: string },
): Promise<GrowthProspect> {
  return apiFetch(`/api/v1/platform/growth/prospects/${encodeURIComponent(prospectId)}/stage`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function createGrowthActivity(
  prospectId: string,
  payload: { tipo: string; descricao?: string },
): Promise<GrowthActivity> {
  return apiFetch(`/api/v1/platform/growth/prospects/${encodeURIComponent(prospectId)}/activities`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export interface GrowthProspectConversion {
  prospect_id: string;
  workspace_id: string;
  workspace_name?: string;
  workspace_status?: string;
  invitation_id?: string;
  invitation_status?: string;
  already_converted: boolean;
}

export async function convertGrowthProspectToWorkspace(
  prospectId: string,
  payload: {
    workspaceName: string;
    slug?: string;
    ownerName: string;
    ownerEmail: string;
    planKey: string;
    setupMode: string;
  },
): Promise<GrowthProspectConversion> {
  return apiFetch(`/api/v1/platform/growth/prospects/${encodeURIComponent(prospectId)}/convert`, {
    method: 'POST',
    headers: { 'Idempotency-Key': platformPeopleMutationKey(`growth-convert-${prospectId}`) },
    body: JSON.stringify(payload),
  });
}

// ── Chaves dos provedores de IA da plataforma (Blackbox) ─────────────────
// Write-only: a chave entra no PUT e nunca volta; só os 4 últimos caracteres.

export type PlatformAiCredentialSource = 'blackbox' | 'environment' | 'none';

export interface PlatformAiCredentialStatus {
  provider: string;
  configured: boolean;
  isActive: boolean;
  apiKeyMasked: string | null;
  keyLast4: string | null;
  updatedAt: string | null;
  updatedByUserId: string | null;
  keyManaged: boolean;
  envConfigured: boolean;
  adapterRegistered: boolean;
  effectiveSource: PlatformAiCredentialSource;
  reason: string | null;
}

export interface PlatformAiCredentialsGate {
  modelApisEnabled: boolean;
  onlineApproved: boolean;
  cipherConfigured: boolean;
}

export interface PlatformAiCredentialsRead {
  gate: PlatformAiCredentialsGate;
  providers: PlatformAiCredentialStatus[];
}

export async function fetchPlatformAiCredentials(): Promise<PlatformAiCredentialsRead> {
  return apiFetch('/api/v1/platform/ai-credentials');
}

export async function savePlatformAiCredential(
  provider: string,
  apiKey: string,
): Promise<PlatformAiCredentialStatus> {
  return apiFetch(`/api/v1/platform/ai-credentials/${encodeURIComponent(provider)}`, {
    method: 'PUT',
    body: JSON.stringify({ apiKey }),
  });
}

export async function deactivatePlatformAiCredential(
  provider: string,
): Promise<PlatformAiCredentialStatus> {
  return apiFetch(`/api/v1/platform/ai-credentials/${encodeURIComponent(provider)}`, {
    method: 'DELETE',
  });
}
