import { API_BASE } from '@/services/api-origin';
import { apiFetch } from '@/services/http';
import type { AuthUser, SessionContext } from '@/types';

export interface IdentityErrorDetail {
  code?: string;
  message?: string;
}

export class IdentityApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'IdentityApiError';
  }
}

export interface IdentityRequestAccepted {
  requestId: string;
  status: 'accepted';
  message: string;
  deliveryMode: 'transactional_email' | 'privileged_local_simulation';
  deliveryId: string;
  expiresAt: string;
}

export interface PublicIdentityRequestAccepted {
  requestId: string;
  status: 'accepted';
  message: string;
  deliveryMode: 'transactional_email' | 'privileged_local_simulation';
  expiresAt: string;
}

export type LocalOutboxPurpose = 'signup_verification' | 'workspace_invitation' | 'password_reset';

export interface LocalOutboxMessage {
  id: string;
  purpose: LocalOutboxPurpose;
  recipientHint: string;
  deliveryMode: 'local_simulation';
  code?: string | null;
  link: string;
  expiresAt: string;
}

export interface IdentitySessionResponse {
  access_token?: string;
  accessToken?: string;
  refresh_token?: string | null;
  refreshToken?: string | null;
  user: AuthUser;
  session: SessionContext;
  verificationStatus: 'verified';
}

export interface InvitationPreview {
  invitationId: string;
  workspaceName: string;
  recipientHint: string;
  status: 'pending' | 'accepted' | 'revoked' | 'expired';
  expiresAt: string;
  requiresPassword: boolean;
}

export interface InvitationAccepted {
  invitationId: string;
  membershipId: string;
  status: 'accepted';
  requiresLogin: true;
  message: string;
}

export interface UserSession {
  id: string;
  deviceLabel: string;
  deviceType: string;
  browser: string;
  operatingSystem: string;
  createdAt: string;
  lastSeenAt: string;
  expiresAt: string;
  revokedAt?: string | null;
  revocationReason?: string | null;
  current: boolean;
}

interface ErrorEnvelope {
  detail?: IdentityErrorDetail | string;
}

function errorDetails(envelope: ErrorEnvelope, fallback: string): { code: string; message: string } {
  if (envelope.detail && typeof envelope.detail === 'object') {
    return {
      code: envelope.detail.code ?? 'IDENTITY-REQUEST-FAILED',
      message: envelope.detail.message ?? fallback,
    };
  }
  return {
    code: 'IDENTITY-REQUEST-FAILED',
    message: typeof envelope.detail === 'string' ? envelope.detail : fallback,
  };
}

async function publicIdentityFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers,
  });
  if (!response.ok) {
    const envelope = await response.json().catch(() => ({})) as ErrorEnvelope;
    const detail = errorDetails(envelope, 'Não foi possível concluir a solicitação.');
    throw new IdentityApiError(response.status, detail.code, detail.message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `identity-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function passwordByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

export function passwordPolicyError(value: string): string | null {
  const bytes = passwordByteLength(value);
  if (bytes < 10) return 'A senha deve ter ao menos 10 bytes em UTF-8.';
  if (bytes > 72) return 'A senha deve ter no máximo 72 bytes em UTF-8.';
  return null;
}

export function requestSignup(
  payload: { name: string; email: string; workspaceName: string; password: string; plan: string },
  idempotencyKey: string,
): Promise<IdentityRequestAccepted> {
  return publicIdentityFetch<IdentityRequestAccepted>('/api/v1/auth/register', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function resendSignupVerification(
  email: string,
  idempotencyKey: string,
): Promise<PublicIdentityRequestAccepted> {
  return publicIdentityFetch<PublicIdentityRequestAccepted>('/api/v1/auth/register/resend', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ email }),
  });
}

export function verifySignup(payload: {
  token?: string;
  deliveryId?: string;
  code?: string;
}): Promise<IdentitySessionResponse> {
  return publicIdentityFetch<IdentitySessionResponse>('/api/v1/auth/register/verify', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function readLocalOutbox(deliveryId: string): Promise<LocalOutboxMessage> {
  return publicIdentityFetch<LocalOutboxMessage>(`/api/v1/auth/local-outbox/${deliveryId}`);
}

export function requestPasswordReset(email: string): Promise<PublicIdentityRequestAccepted> {
  return publicIdentityFetch<PublicIdentityRequestAccepted>('/api/v1/auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(payload: {
  newPassword: string;
  token?: string;
  deliveryId?: string;
  code?: string;
}): Promise<{ message: string }> {
  return publicIdentityFetch<{ message: string }>('/api/v1/auth/reset-password', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function previewInvitation(invitationId: string, token: string): Promise<InvitationPreview> {
  const query = new URLSearchParams({ token });
  return publicIdentityFetch<InvitationPreview>(`/api/v1/auth/invitations/${invitationId}?${query}`);
}

export function acceptInvitation(
  invitationId: string,
  payload: { token?: string; deliveryId?: string; code?: string; name?: string; password?: string },
  idempotencyKey: string,
): Promise<InvitationAccepted> {
  return publicIdentityFetch<InvitationAccepted>(`/api/v1/auth/invitations/${invitationId}/accept`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function listUserSessions(): Promise<UserSession[]> {
  return apiFetch<UserSession[]>('/api/v1/auth/sessions');
}

export function revokeUserSession(sessionId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/auth/sessions/${sessionId}`, { method: 'DELETE' });
}

export function revokeOtherUserSessions(): Promise<{ revokedCount: number }> {
  return apiFetch<{ revokedCount: number }>('/api/v1/auth/sessions/others', { method: 'DELETE' });
}
