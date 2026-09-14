// ── Serviço de Autenticação Real ──
// Conecta ao backend local por padrão; produção exige configuração explícita.

import type { PlatformPermission, SessionContext } from '@/types';
import { API_BASE } from '@/services/api-origin';
import { APP_SURFACE } from '@/runtime-surface';
export type { Permission, SessionContext, WorkspaceMembershipSummary, WorkspaceSummary } from '@/types';

const TOKEN_KEY = 'klm_token';
const REFRESH_TOKEN_KEY = 'klm_refresh_token';
const USER_KEY = 'klm_user';
const SESSION_KEY = 'klm_session';
const ADMIN_SESSION_KEY = 'klm_admin_session_token';
const SESSION_EXPIRED_NOTICE_KEY = 'meu_executivo_session_expired_notice';
export const CLIENT_SESSION_EXPIRED_EVENT = 'meu-executivo:auth:session-expired';
export const SESSION_EXPIRED_MESSAGE = 'Sua sessão expirou. Entre novamente para continuar com segurança.';
const isLocalSimulation = (import.meta.env.VITE_KLM_RUNTIME_MODE || 'local_simulation') === 'local_simulation';

let accessToken: string | null = null;
const localBootstrapPromises = new Map<AuthAudience, Promise<LoginResponse>>();
let refreshPromise: Promise<string | null> | null = null;
let sessionGeneration = 0;

export type AuthAudience = 'workspace' | 'platform';

function currentAudience(): AuthAudience {
  return APP_SURFACE;
}

export interface LoginResponse {
  access_token?: string;
  accessToken?: string;
  refresh_token?: string | null;
  refreshToken?: string | null;
  token_type?: string;
  tokenType?: string;
  user: {
    id: string;
    name: string;
    email: string;
    role: string;
    initials: string;
    isInternalStaff?: boolean;
    isPlatformAdmin?: boolean;
    isPlatformRoot?: boolean;
    platformRolePackage?: string | null;
    platformAccessActive?: boolean;
    platformPermissions?: PlatformPermission[];
    requiresPasswordSetup?: boolean;
    requiresPasswordChange?: boolean;
    mustChangePassword?: boolean;
  };
  session: SessionContext;
}

/**
 * Compatibility reader for the first-access password contract. Backend
 * releases may name this flag differently during expand/contract rollout;
 * a missing flag is deliberately treated as false.
 */
export function requiresPasswordChange(
  user?: Pick<LoginResponse['user'], 'requiresPasswordSetup' | 'requiresPasswordChange' | 'mustChangePassword'> | null,
  session?: Pick<SessionContext, 'requiresPasswordSetup' | 'requiresPasswordChange' | 'mustChangePassword'> | null,
): boolean {
  return Boolean(
    user?.requiresPasswordSetup
    || user?.requiresPasswordChange
    || user?.mustChangePassword
    || session?.requiresPasswordSetup
    || session?.requiresPasswordChange
    || session?.mustChangePassword,
  );
}

export interface MfaLoginChallengeResponse {
  requiresMfa: true;
  challengeId: string;
  purpose: 'login';
  expiresIn: number;
  attemptsRemaining: number;
  recoveryAvailable: boolean;
  simulatedCode?: string | null;
}

export type LoginResult = LoginResponse | MfaLoginChallengeResponse;

export function isMfaLoginChallenge(value: LoginResult): value is MfaLoginChallengeResponse {
  return 'requiresMfa' in value && value.requiresMfa === true;
}

export function extractToken(data: LoginResponse): string {
  return data.access_token || data.accessToken || '';
}

export function extractRefreshToken(data: LoginResponse): string {
  return data.refresh_token || data.refreshToken || '';
}

function readStorage(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(storage: Storage, key: string, value: string | null): void {
  try {
    if (value) storage.setItem(key, value);
    else storage.removeItem(key);
  } catch {
    // Memory remains the fallback when browser storage is unavailable.
  }
}

function clearLegacyTokenStorage(): void {
  writeStorage(localStorage, TOKEN_KEY, null);
  writeStorage(localStorage, REFRESH_TOKEN_KEY, null);
  writeStorage(sessionStorage, REFRESH_TOKEN_KEY, null);
}

export function bootstrapLocalSession(audience: AuthAudience = currentAudience()): Promise<LoginResponse> {
  const pending = localBootstrapPromises.get(audience);
  if (pending) return pending;
  const endpoint = audience === 'platform'
    ? '/api/v1/klm/local/platform-session'
    : '/api/v1/klm/local/session';
  const bootstrapPromise = (() => {
    const generation = sessionGeneration;
    return fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    })
      .then(async (response) => {
        if (!response.ok) throw new Error('Backend local do KLM Hub indisponível.');
        const data = (await response.json()) as LoginResponse;
        const accessToken = extractToken(data);
        if (!accessToken) throw new Error('A sessão local não retornou um token de acesso.');
        if (generation !== sessionGeneration) throw new Error('Sessão local cancelada.');
        saveToken(accessToken);
        saveUser(data.user);
        saveSession(data.session);
        return data;
      })
      .finally(() => {
        localBootstrapPromises.delete(audience);
      });
  })();
  localBootstrapPromises.set(audience, bootstrapPromise);
  return bootstrapPromise;
}

export const LOCAL_PLATFORM_ROLE_PACKAGES = [
  'platform_admin',
  'sales_sdr',
  'commercial_manager',
  'onboarding',
  'customer_success',
  'support',
  'technical_support',
  'finance_billing',
  'marketing_content',
  'community_manager',
  'compliance_security',
  'product_operations',
  'engineering',
] as const;

export type LocalPlatformRolePackage =
  (typeof LOCAL_PLATFORM_ROLE_PACKAGES)[number];

export async function bootstrapLocalPlatformRole(
  rolePackage: LocalPlatformRolePackage,
): Promise<LoginResponse> {
  if (!isLocalSimulation || APP_SURFACE !== 'platform') {
    throw new Error('A troca de função local só existe na Central Blackbox local.');
  }
  const response = await fetch(
    `${API_BASE}/api/v1/klm/local/platform-session/${encodeURIComponent(rolePackage)}`,
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    },
  );
  if (!response.ok) {
    throw new Error('A identidade interna local não pôde ser ativada.');
  }
  const data = (await response.json()) as LoginResponse;
  const token = extractToken(data);
  if (!token) throw new Error('A sessão interna local não retornou credencial.');
  saveToken(token);
  saveUser(data.user);
  saveSession(data.session);
  return data;
}

async function requestRefreshedAccessToken(generation: number): Promise<string | null> {
  const maxAttempts = 2;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-Auth-Audience': APP_SURFACE,
        },
      });
      if (!response.ok) {
        const transient = response.status >= 500 || response.status === 408 || response.status === 429;
        if (transient && attempt + 1 < maxAttempts) continue;
        return null;
      }
      const data = (await response.json()) as LoginResponse;
      const token = extractToken(data);
      if (!token || generation !== sessionGeneration) return null;
      saveToken(token);
      if (data.user) saveUser(data.user);
      if (data.session) saveSession(data.session);
      return token;
    } catch {
      if (attempt + 1 >= maxAttempts) return null;
    }
  }
  return null;
}

function withCrossTabRefreshLock(operation: () => Promise<string | null>): Promise<string | null> {
  if (typeof navigator === 'undefined' || !navigator.locks) return operation();
  const lockManager = navigator.locks as unknown as {
    request: (name: string, callback: () => Promise<string | null>) => Promise<string | null>;
  };
  return lockManager.request('meu-executivo-auth-refresh', operation);
}

export function refreshAccessToken(): Promise<string | null> {
  if (!refreshPromise) {
    const generation = sessionGeneration;
    refreshPromise = withCrossTabRefreshLock(() => requestRefreshedAccessToken(generation))
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

export async function recoverAccessToken(
  options: { forceRefresh?: boolean } = {},
): Promise<string | null> {
  const existingToken = getToken();
  if (existingToken && !options.forceRefresh) return existingToken;
  const refreshedToken = await refreshAccessToken();
  if (refreshedToken || !isLocalSimulation) return refreshedToken;
  try {
    return extractToken(await bootstrapLocalSession(currentAudience())) || null;
  } catch {
    return null;
  }
}

export async function loginWithCredentials(
  email: string,
  password: string,
  audience: AuthAudience = 'workspace',
): Promise<LoginResult> {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, audience }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Erro de conexão' }));
    throw new Error(err.detail?.message || err.detail || 'Credenciais inválidas');
  }

  return res.json();
}

export async function verifyMfaLogin(
  challengeId: string,
  verifier: { code?: string; recoveryCode?: string },
): Promise<LoginResponse> {
  const response = await fetch(`${API_BASE}/api/v1/auth/mfa/login/${challengeId}/verify`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(verifier),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Falha na verificação' }));
    throw new Error(error.detail?.message || error.detail || 'Código de verificação inválido.');
  }
  return response.json();
}

export async function selectWorkspace(
  token: string,
  workspaceId: string
): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/api/v1/auth/select-workspace`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ workspaceId }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Erro de conexão' }));
    throw new Error(err.detail?.message || err.detail || 'Erro ao selecionar workspace');
  }

  return res.json();
}

export class SessionAccessError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'SessionAccessError';
  }
}

export async function fetchCurrentSession(): Promise<SessionContext> {
  let token = getToken();
  if (!token) token = await recoverAccessToken();
  if (!token) {
    throw new SessionAccessError(401, 'Sessão indisponível.');
  }
  const fetchSession = (currentToken: string) => fetch(`${API_BASE}/api/v1/auth/me`, {
    credentials: 'include',
    headers: { Authorization: `Bearer ${currentToken}` },
  });
  let response = await fetchSession(token);
  if (response.status === 401) {
    token = await recoverAccessToken({ forceRefresh: true });
    if (token) response = await fetchSession(token);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new SessionAccessError(
      response.status,
      body?.detail?.message || body?.detail || 'Não foi possível atualizar a sessão.',
    );
  }
  return response.json();
}

export function saveToken(token: string): void {
  clearLegacyTokenStorage();
  accessToken = token || null;
  writeStorage(sessionStorage, TOKEN_KEY, accessToken);
}

export function saveRefreshToken(_token: string): void {
  clearLegacyTokenStorage();
}

export function getToken(): string | null {
  clearLegacyTokenStorage();
  if (!accessToken) accessToken = readStorage(sessionStorage, TOKEN_KEY);
  return accessToken;
}

export function getRefreshToken(): string | null {
  clearLegacyTokenStorage();
  return null;
}

export function clearClientSession(): void {
  sessionGeneration += 1;
  accessToken = null;
  clearLegacyTokenStorage();
  writeStorage(sessionStorage, TOKEN_KEY, null);
  writeStorage(sessionStorage, USER_KEY, null);
  writeStorage(sessionStorage, SESSION_KEY, null);
  writeStorage(localStorage, USER_KEY, null);
  writeStorage(localStorage, SESSION_KEY, null);
  writeStorage(sessionStorage, ADMIN_SESSION_KEY, null);
}

export function expireClientSession(message = SESSION_EXPIRED_MESSAGE): void {
  writeStorage(sessionStorage, SESSION_EXPIRED_NOTICE_KEY, message);
  clearClientSession();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(CLIENT_SESSION_EXPIRED_EVENT, { detail: { message } }));
  }
}

export function consumeSessionExpiredNotice(): string | null {
  const message = readStorage(sessionStorage, SESSION_EXPIRED_NOTICE_KEY);
  writeStorage(sessionStorage, SESSION_EXPIRED_NOTICE_KEY, null);
  return message;
}

export function getAccessTokenExpiresAt(token = getToken()): number | null {
  if (!token) return null;
  try {
    const encodedPayload = token.split('.')[1];
    if (!encodedPayload) return null;
    const normalized = encodedPayload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded)) as { exp?: unknown };
    return typeof payload.exp === 'number' && Number.isFinite(payload.exp)
      ? payload.exp * 1_000
      : null;
  } catch {
    return null;
  }
}

export async function removeToken(): Promise<void> {
  const token = getToken();
  try {
    await fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: 'POST',
      credentials: 'include',
      keepalive: true,
      headers: {
        'X-Auth-Audience': APP_SURFACE,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  } catch {
    // Local logout still clears client credentials when the backend is unavailable.
  } finally {
    clearClientSession();
  }
}

export function saveUser(user: LoginResponse['user']): void {
  writeStorage(sessionStorage, USER_KEY, JSON.stringify(user));
  writeStorage(localStorage, USER_KEY, null);
}

export function saveSession(session: SessionContext): void {
  writeStorage(sessionStorage, SESSION_KEY, JSON.stringify(session));
  writeStorage(localStorage, SESSION_KEY, null);
}

export function getSavedUser(): LoginResponse['user'] | null {
  try {
    const u = readStorage(sessionStorage, USER_KEY);
    return u ? JSON.parse(u) : null;
  } catch {
    return null;
  }
}

export function getSavedSession(): SessionContext | null {
  try {
    const session = readStorage(sessionStorage, SESSION_KEY);
    return session ? JSON.parse(session) : null;
  } catch {
    return null;
  }
}

export function saveAdministrativeSessionToken(token: string | null): void {
  writeStorage(sessionStorage, ADMIN_SESSION_KEY, token);
}

export function getAdministrativeSessionToken(): string | null {
  return readStorage(sessionStorage, ADMIN_SESSION_KEY);
}
