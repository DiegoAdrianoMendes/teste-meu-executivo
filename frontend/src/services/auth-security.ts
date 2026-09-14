import { getAdministrativeSessionToken, saveAdministrativeSessionToken } from '@/services/auth';
import { API_BASE, apiFetch } from '@/services/http';

export type MfaChallengePurpose = 'enrollment' | 'login' | 'admin_session';
export type MfaVerificationMethod = 'challenge' | 'recovery';

export interface MfaStatus {
  enabled: boolean;
  simulated: boolean;
  recoveryCodesRemaining: number;
}

export interface MfaChallenge {
  challengeId: string;
  purpose: MfaChallengePurpose;
  expiresIn: number;
  attemptsRemaining: number;
  recoveryAvailable: boolean;
  simulatedCode?: string | null;
  provisioningUri?: string | null;
  manualEntryKey?: string | null;
}

export interface MfaEnrollmentComplete extends MfaStatus {
  recoveryCodes: string[];
}

export interface AdministrativeSession {
  id: string;
  mfaMethod: MfaVerificationMethod;
  expiresAt: string;
  lastVerifiedAt?: string | null;
  revokedAt?: string | null;
  revocationReason?: string | null;
  createdAt: string;
}

export interface AdministrativeSessionToken {
  administrativeSessionToken: string;
  tokenType: 'admin_session';
  expiresIn: number;
  session: AdministrativeSession;
}

export interface AdministrativeSessionStatus {
  active: boolean;
  session: AdministrativeSession;
}

export interface AuthSecurityEvent {
  id: string;
  eventType: string;
  outcome: string;
  challengeId?: string | null;
  administrativeSessionId?: string | null;
  details: Record<string, unknown>;
  occurredAt: string;
}

export type MfaVerifier = { code: string; recoveryCode?: never } | { recoveryCode: string; code?: never };

export function fetchMfaStatus(): Promise<MfaStatus> {
  return apiFetch<MfaStatus>('/api/v1/auth/mfa');
}

export function beginMfaEnrollment(): Promise<MfaChallenge> {
  return apiFetch<MfaChallenge>('/api/v1/auth/mfa/enroll', { method: 'POST' });
}

export function verifyMfaEnrollment(challengeId: string, verifier: MfaVerifier): Promise<MfaEnrollmentComplete> {
  return apiFetch<MfaEnrollmentComplete>(`/api/v1/auth/mfa/enroll/${challengeId}/verify`, {
    method: 'POST',
    body: JSON.stringify(verifier),
  });
}

export function beginAdministrativeSession(): Promise<MfaChallenge> {
  return apiFetch<MfaChallenge>('/api/v1/auth/admin-sessions/challenge', { method: 'POST' });
}

export async function verifyAdministrativeSession(
  challengeId: string,
  verifier: MfaVerifier,
): Promise<AdministrativeSessionToken> {
  const result = await apiFetch<AdministrativeSessionToken>(
    `/api/v1/auth/admin-sessions/challenge/${challengeId}/verify`,
    { method: 'POST', body: JSON.stringify(verifier) },
  );
  saveAdministrativeSessionToken(result.administrativeSessionToken);
  return result;
}

export function listAdministrativeSessions(): Promise<AdministrativeSession[]> {
  return apiFetch<AdministrativeSession[]>('/api/v1/auth/admin-sessions');
}

export function revokeAdministrativeSession(sessionId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/auth/admin-sessions/${sessionId}`, { method: 'DELETE' });
}

export async function revokeAllAdministrativeSessions(): Promise<void> {
  await apiFetch<void>('/api/v1/auth/admin-sessions', { method: 'DELETE' });
  saveAdministrativeSessionToken(null);
}

export function listAuthSecurityEvents(limit = 50): Promise<AuthSecurityEvent[]> {
  return apiFetch<AuthSecurityEvent[]>(`/api/v1/auth/security-events?limit=${limit}`);
}

export async function fetchCurrentAdministrativeSession(): Promise<AdministrativeSessionStatus | null> {
  const token = getAdministrativeSessionToken();
  if (!token) return null;
  const response = await fetch(`${API_BASE}/api/v1/auth/admin-sessions/current`, {
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401 || response.status === 403) {
    saveAdministrativeSessionToken(null);
    return null;
  }
  if (!response.ok) throw new Error('Não foi possível validar a sessão administrativa.');
  return response.json() as Promise<AdministrativeSessionStatus>;
}
