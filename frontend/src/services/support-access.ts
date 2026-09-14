import { getAdministrativeSessionToken } from '@/services/auth';
import { ApiError, apiFetch } from '@/services/http';
import { stageSupportModeCredential } from '@/support-mode/session';
import {
  hasPrimarySupportScope,
  isSupportScopeSelectionValid,
  parseExactSupportAccessScopes,
  SUPPORT_ACCESS_MAX_SCOPES,
  SUPPORT_ACCESS_SCOPE_OPTIONS,
  type SupportAccessScope,
} from '@/support-mode/scopes';

export {
  hasPrimarySupportScope,
  isSupportScopeSelectionValid,
  SUPPORT_ACCESS_MAX_SCOPES,
  SUPPORT_ACCESS_SCOPE_OPTIONS,
};
export type { SupportAccessScope };

export const SUPPORT_ACCESS_REQUEST_MIN_DURATION_MINUTES = 5;
export const SUPPORT_ACCESS_REQUEST_MAX_DURATION_MINUTES = 30;
export type SupportAccessState =
  | 'requested'
  | 'approved'
  | 'active'
  | 'expired'
  | 'revoked'
  | 'rejected';

export interface SupportAccessGrant {
  id: string;
  workspaceId: string;
  requesterUserId: string;
  reason: string;
  ticketReference: string;
  requestedScopes: SupportAccessScope[];
  requestedDurationMinutes: number;
  state: SupportAccessState;
  requestedAt: string;
  approverUserId?: string | null;
  approvedScopes?: SupportAccessScope[] | null;
  approvedDurationMinutes?: number | null;
  approvedAt?: string | null;
  activatedAt?: string | null;
  expiresAt?: string | null;
  expiredAt?: string | null;
  rejectedAt?: string | null;
  rejectionReason?: string | null;
  revokedAt?: string | null;
  revocationReason?: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface SupportAccessEvent {
  id: string;
  grantId: string;
  workspaceId: string;
  actorUserId?: string | null;
  eventType: string;
  fromState?: string | null;
  toState?: string | null;
  details: Record<string, unknown>;
  occurredAt: string;
}

export interface SupportAccessEventPage {
  items: SupportAccessEvent[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface SupportAccessRequestInput {
  workspaceId: string;
  reason: string;
  ticketReference: string;
  requestedScopes: SupportAccessScope[];
  durationMinutes: number;
}

export interface SupportAccessApprovalInput {
  approvedScopes: SupportAccessScope[];
  durationMinutes: number;
}

export interface SupportAccessDecisionInput {
  reason: string;
}

export interface SupportAccessTokenResponse {
  supportAccessToken: string;
  tokenType: 'support_access';
  expiresIn: number;
  grant: SupportAccessGrant;
}

export interface SupportAccessActivation {
  expiresIn: number;
  grant: SupportAccessGrant;
}

export interface SupportAccessListFilters {
  workspaceId?: string;
  state?: SupportAccessState;
  limit?: number;
}

export class AdministrativeSessionRequiredError extends Error {
  readonly code = 'SUPPORT-ROOT-STEP-UP-REQUIRED';

  constructor() {
    super('Confirme o MFA e mantenha uma sessão administrativa ativa antes de ativar o acesso.');
    this.name = 'AdministrativeSessionRequiredError';
  }
}

export function isAdministrativeStepUpError(error: unknown): boolean {
  if (error instanceof AdministrativeSessionRequiredError) return true;
  if (!(error instanceof ApiError) || !isRecord(error.detail)) return false;
  return [
    'AUTH-018',
    'SUPPORT-ROOT-STEP-UP-REQUIRED',
    'SUPPORT-ROOT-STEP-UP-STALE',
  ].includes(String(error.detail.code ?? ''));
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ACTIVATION_CLOCK_TOLERANCE_MS = 30_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value);
}

function requiredTimestamp(value: unknown): number | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

function invalidActivation(): Error {
  return new Error('A ativação de suporte não retornou uma credencial temporária válida.');
}

function parseActiveActivation(
  value: unknown,
  expectedGrantId?: string,
): { token: string; expiresIn: number; grant: SupportAccessGrant; approvedScopes: SupportAccessScope[] } {
  if (!isRecord(value)) throw invalidActivation();
  const response = value;
  const grantValue = response.grant;
  if (!isRecord(grantValue)) throw invalidActivation();
  const grant: Record<string, unknown> = grantValue;
  const token = response.supportAccessToken;
  const expiresIn = response.expiresIn;
  const requestedScopes = parseExactSupportAccessScopes(grant.requestedScopes);
  const approvedScopes = parseExactSupportAccessScopes(grant.approvedScopes);
  const requestedDuration = grant.requestedDurationMinutes;
  const approvedDuration = grant.approvedDurationMinutes;
  const requestedAt = requiredTimestamp(grant.requestedAt);
  const approvedAt = requiredTimestamp(grant.approvedAt);
  const activatedAt = requiredTimestamp(grant.activatedAt);
  const expiresAt = requiredTimestamp(grant.expiresAt);
  const createdAt = requiredTimestamp(grant.createdAt);
  const updatedAt = requiredTimestamp(grant.updatedAt);
  const now = Date.now();
  const approvedWithinRequest = Boolean(
    requestedScopes
    && approvedScopes
    && approvedScopes.every((scope) => requestedScopes.includes(scope)),
  );
  const chronologyValid = Boolean(
    requestedAt !== null
    && approvedAt !== null
    && activatedAt !== null
    && expiresAt !== null
    && createdAt !== null
    && updatedAt !== null
    && requestedAt <= approvedAt + ACTIVATION_CLOCK_TOLERANCE_MS
    && approvedAt <= activatedAt + ACTIVATION_CLOCK_TOLERANCE_MS
    && createdAt <= updatedAt + ACTIVATION_CLOCK_TOLERANCE_MS
    && activatedAt <= now + ACTIVATION_CLOCK_TOLERANCE_MS
    && updatedAt <= now + ACTIVATION_CLOCK_TOLERANCE_MS
    && expiresAt > now
    && expiresAt > activatedAt
  );
  const durationValid = Boolean(
    isPositiveInteger(requestedDuration)
    && requestedDuration >= SUPPORT_ACCESS_REQUEST_MIN_DURATION_MINUTES
    && requestedDuration <= SUPPORT_ACCESS_REQUEST_MAX_DURATION_MINUTES
    && isPositiveInteger(approvedDuration)
    && approvedDuration <= requestedDuration
    && approvedDuration <= SUPPORT_ACCESS_REQUEST_MAX_DURATION_MINUTES
    && activatedAt !== null
    && expiresAt !== null
    && expiresAt - activatedAt <= (approvedDuration * 60_000) + ACTIVATION_CLOCK_TOLERANCE_MS
  );
  const expiryContractValid = Boolean(
    isPositiveInteger(expiresIn)
    && expiresAt !== null
    && Math.abs((expiresAt - now) - (expiresIn * 1_000)) <= ACTIVATION_CLOCK_TOLERANCE_MS
    && isPositiveInteger(approvedDuration)
    && expiresIn * 1_000 <= (approvedDuration * 60_000) + ACTIVATION_CLOCK_TOLERANCE_MS
  );
  const rootDirect = grant.approverUserId == null
    && typeof grant.ticketReference === 'string'
    && grant.ticketReference.startsWith('ROOT-');

  if (
    response.tokenType !== 'support_access'
    || typeof token !== 'string'
    || token.length === 0
    || token !== token.trim()
    || (expectedGrantId !== undefined && (!isUuid(expectedGrantId) || grant.id !== expectedGrantId))
    || !isUuid(grant.id)
    || !isUuid(grant.workspaceId)
    || !isUuid(grant.requesterUserId)
    || (!rootDirect && !isUuid(grant.approverUserId))
    || (!rootDirect && grant.requesterUserId === grant.approverUserId)
    || typeof grant.reason !== 'string'
    || !grant.reason.trim()
    || typeof grant.ticketReference !== 'string'
    || !grant.ticketReference.trim()
    || grant.state !== 'active'
    || !requestedScopes
    || !approvedScopes
    || !approvedWithinRequest
    || !durationValid
    || !chronologyValid
    || !expiryContractValid
    || grant.expiredAt !== null
    || grant.rejectedAt !== null
    || grant.rejectionReason !== null
    || grant.revokedAt !== null
    || grant.revocationReason !== null
    || !isPositiveInteger(grant.version)
  ) {
    throw invalidActivation();
  }

  return {
    token,
    expiresIn: expiresIn as number,
    grant: grant as unknown as SupportAccessGrant,
    approvedScopes,
  };
}

function queryString(filters: SupportAccessListFilters): string {
  const params = new URLSearchParams();
  if (filters.workspaceId) params.set('workspace_id', filters.workspaceId);
  if (filters.state) params.set('state', filters.state);
  params.set('limit', String(filters.limit ?? 100));
  return params.toString();
}

export function listWorkspaceSupportAccess(
  filters: Pick<SupportAccessListFilters, 'state' | 'limit'> = {},
): Promise<SupportAccessGrant[]> {
  return apiFetch(`/api/v1/workspaces/support-access/requests?${queryString(filters)}`);
}

export function pageWorkspaceSupportAccess(
  filters: SupportAccessListFilters & { offset?: number } = {},
): Promise<SupportAccessGrantPage> {
  const params = new URLSearchParams();
  if (filters.state) params.set('state', filters.state);
  params.set('limit', String(filters.limit ?? 25));
  params.set('offset', String(filters.offset ?? 0));
  return apiFetch(`/api/v1/workspaces/support-access/requests/page?${params.toString()}`);
}

export function pageWorkspaceSupportAccessEvents(
  grantId: string,
  input: { limit?: number; offset?: number } = {},
): Promise<SupportAccessEventPage> {
  const params = new URLSearchParams({
    limit: String(input.limit ?? 25),
    offset: String(input.offset ?? 0),
  });
  return apiFetch(`/api/v1/workspaces/support-access/requests/${encodeURIComponent(grantId)}/events/page?${params.toString()}`);
}

export function approveWorkspaceSupportAccess(
  grantId: string,
  payload: SupportAccessApprovalInput,
): Promise<SupportAccessGrant> {
  return apiFetch(`/api/v1/workspaces/support-access/requests/${grantId}/approve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function rejectWorkspaceSupportAccess(
  grantId: string,
  payload: SupportAccessDecisionInput,
): Promise<SupportAccessGrant> {
  return apiFetch(`/api/v1/workspaces/support-access/requests/${grantId}/reject`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function revokeWorkspaceSupportAccess(
  grantId: string,
  payload: SupportAccessDecisionInput,
): Promise<SupportAccessGrant> {
  return apiFetch(`/api/v1/workspaces/support-access/requests/${grantId}/revoke`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function requestPlatformSupportAccess(
  payload: SupportAccessRequestInput,
): Promise<SupportAccessGrant> {
  return apiFetch('/api/v1/platform/support-access/requests', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function listPlatformSupportAccess(
  filters: SupportAccessListFilters = {},
): Promise<SupportAccessGrant[]> {
  return apiFetch(`/api/v1/platform/support-access/requests?${queryString(filters)}`);
}

export interface SupportAccessGrantPage {
  items: SupportAccessGrant[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: 'asc' | 'desc' }>;
}

export function pagePlatformSupportAccess(
  filters: SupportAccessListFilters & { offset?: number } = {},
): Promise<SupportAccessGrantPage> {
  const params = new URLSearchParams();
  if (filters.workspaceId) params.set('workspace_id', filters.workspaceId);
  if (filters.state) params.set('state', filters.state);
  params.set('limit', String(filters.limit ?? 50));
  params.set('offset', String(filters.offset ?? 0));
  return apiFetch(`/api/v1/platform/support-access/requests/page?${params.toString()}`);
}

export function pagePlatformSupportAccessEvents(
  grantId: string,
  input: { limit?: number; offset?: number } = {},
): Promise<SupportAccessEventPage> {
  const params = new URLSearchParams({
    limit: String(input.limit ?? 25),
    offset: String(input.offset ?? 0),
  });
  return apiFetch(`/api/v1/platform/support-access/requests/${encodeURIComponent(grantId)}/events/page?${params.toString()}`);
}

export async function activatePlatformSupportAccess(grantId: string): Promise<SupportAccessActivation> {
  const administrativeSessionToken = getAdministrativeSessionToken()?.trim();
  if (!administrativeSessionToken) throw new AdministrativeSessionRequiredError();
  if (!isUuid(grantId.trim())) {
    throw new Error('A ativação de suporte não recebeu o grant esperado.');
  }

  return activatePlatformSupportAccessWithSession(grantId.trim(), administrativeSessionToken);
}

export async function activatePlatformRootDirectAccess(
  workspaceId: string,
  reason?: string,
): Promise<SupportAccessActivation> {
  const administrativeSessionToken = getAdministrativeSessionToken()?.trim();
  if (!administrativeSessionToken) throw new AdministrativeSessionRequiredError();
  if (!isUuid(workspaceId.trim())) throw new Error('A conta selecionada é inválida.');
  const result = await apiFetch<unknown>(
    `/api/v1/platform/support-access/root-direct/${encodeURIComponent(workspaceId.trim())}`,
    {
      method: 'POST',
      headers: { 'X-Administrative-Session': administrativeSessionToken },
      body: reason ? JSON.stringify({ reason: reason.trim() }) : undefined,
    },
  );
  const activation = parseActiveActivation(result);
  stageSupportModeCredential({
    token: activation.token,
    grantId: activation.grant.id,
    workspaceId: activation.grant.workspaceId,
    scopes: activation.approvedScopes,
    expiresAt: activation.grant.expiresAt as string,
  });
  return { expiresIn: activation.expiresIn, grant: activation.grant };
}

async function activatePlatformSupportAccessWithSession(
  grantId: string,
  administrativeSessionToken: string,
): Promise<SupportAccessActivation> {
  const result = await apiFetch<unknown>(`/api/v1/platform/support-access/requests/${encodeURIComponent(grantId)}/activate`, {
    method: 'POST',
    headers: { 'X-Administrative-Session': administrativeSessionToken },
  });

  const activation = parseActiveActivation(result, grantId);

  stageSupportModeCredential({
    token: activation.token,
    grantId: activation.grant.id,
    workspaceId: activation.grant.workspaceId,
    scopes: activation.approvedScopes,
    expiresAt: activation.grant.expiresAt as string,
  });
  return { expiresIn: activation.expiresIn, grant: activation.grant };
}
