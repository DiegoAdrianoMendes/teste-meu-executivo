import { matchSupportModeRoute } from '@/support-mode/routes';
import {
  parseExactSupportAccessScopes,
  type SupportAccessScope,
} from '@/support-mode/scopes';

export const SUPPORT_MODE_TOMBSTONE_KEY = 'klm_support_mode_teardown_pending';
const SUPPORT_MODE_TOMBSTONE_VALUE = '1';

export interface SupportModeSession {
  grantId: string;
  workspaceId: string;
  workspaceName: string;
  workspaceSlug?: string | null;
  scopes: SupportAccessScope[];
  expiresAt: string;
  sessionType?: 'root_direct' | 'support_approved';
}

export type SupportModeEndReason = 'expired' | 'manual' | 'pagehide' | 'replaced' | 'unauthorized';

interface SupportModeCredential {
  token: string;
  session: Omit<SupportModeSession, 'workspaceName' | 'workspaceSlug'>;
}

type SupportModeVault =
  | { phase: 'inactive' }
  | { phase: 'bootstrap' }
  | { phase: 'staged'; credential: SupportModeCredential }
  | { phase: 'active'; credential: SupportModeCredential; session: SupportModeSession }
  | { phase: 'ending'; session: SupportModeSession; reason: SupportModeEndReason };

export type SupportModePhase = SupportModeVault['phase'];

export interface SupportModeRequestIdentity {
  generation: number;
  grantId: string;
  workspaceId: string;
}

export type SupportRequestDecision =
  | { kind: 'normal' }
  | { kind: 'blocked'; requiredScope: string | null }
  | {
      kind: 'support';
      token: string;
      requiredScope: SupportAccessScope;
      identity: SupportModeRequestIdentity;
    };

export interface SupportModeRequestLease {
  signal: AbortSignal;
  assertCurrent: () => void;
  release: () => void;
}

export class SupportModeRequestAbortedError extends Error {
  constructor() {
    super('A requisição temporária foi cancelada porque o modo suporte mudou.');
    this.name = 'SupportModeRequestAbortedError';
  }
}

type SupportModeListener = (session: SupportModeSession | null, reason?: SupportModeEndReason) => void;

interface RegisteredSupportRequest {
  controller: AbortController;
  detachCallerSignal: () => void;
}

let vault: SupportModeVault = { phase: 'inactive' };
let generation = 0;
let expirationTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<SupportModeListener>();
const supportRequests = new Map<symbol, RegisteredSupportRequest>();

function routePath(path: string): string {
  try {
    return new URL(path, 'http://support-mode.local').pathname;
  } catch {
    return path.split(/[?#]/, 1)[0] || '/';
  }
}

function hasPersistedTombstone(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY) !== null;
  } catch {
    return true;
  }
}

function persistTombstone(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    window.sessionStorage.setItem(SUPPORT_MODE_TOMBSTONE_KEY, SUPPORT_MODE_TOMBSTONE_VALUE);
    return window.sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY) === SUPPORT_MODE_TOMBSTONE_VALUE;
  } catch {
    return false;
  }
}

function removeTombstone(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    window.sessionStorage.removeItem(SUPPORT_MODE_TOMBSTONE_KEY);
    return window.sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY) === null;
  } catch {
    return false;
  }
}

function emit(session: SupportModeSession | null, reason?: SupportModeEndReason): void {
  listeners.forEach((listener) => listener(session, reason));
}

function clearExpirationTimer(): void {
  if (expirationTimer === null) return;
  clearTimeout(expirationTimer);
  expirationTimer = null;
}

export function abortAllSupportModeRequests(): void {
  for (const request of supportRequests.values()) {
    request.detachCallerSignal();
    request.controller.abort();
  }
  supportRequests.clear();
}

function invalidateSupportGeneration(): void {
  abortAllSupportModeRequests();
  generation += 1;
}

function scheduleExpiration(expiresAt: string): void {
  clearExpirationTimer();
  const remaining = new Date(expiresAt).getTime() - Date.now();
  if (!Number.isFinite(remaining) || remaining <= 0) {
    clearSupportModeCredential('expired');
    return;
  }
  expirationTimer = setTimeout(() => clearSupportModeCredential('expired'), remaining);
}

export function initializeSupportModeFromStorage(): boolean {
  if (!hasPersistedTombstone()) return false;
  clearExpirationTimer();
  invalidateSupportGeneration();
  vault = { phase: 'bootstrap' };
  return true;
}

export function stageSupportModeCredential(input: {
  token: string;
  grantId: string;
  workspaceId: string;
  scopes: SupportAccessScope[];
  expiresAt: string;
}): void {
  const token = typeof input.token === 'string' ? input.token.trim() : '';
  const grantId = typeof input.grantId === 'string' ? input.grantId.trim() : '';
  const workspaceId = typeof input.workspaceId === 'string' ? input.workspaceId.trim() : '';
  const scopes = parseExactSupportAccessScopes(input.scopes);
  const expiry = new Date(input.expiresAt).getTime();
  if (
    !token
    || !grantId
    || !workspaceId
    || !scopes
    || !Number.isFinite(expiry)
    || expiry <= Date.now()
  ) {
    throw new Error('A ativação de suporte não retornou uma credencial temporária válida.');
  }
  if (vault.phase === 'active' || vault.phase === 'ending' || vault.phase === 'bootstrap') {
    throw new Error('Finalize o acesso temporário atual antes de ativar outro grant.');
  }
  if (!persistTombstone()) {
    invalidateSupportGeneration();
    vault = { phase: 'bootstrap' };
    throw new Error('Não foi possível preparar o encerramento seguro do modo suporte.');
  }

  clearExpirationTimer();
  invalidateSupportGeneration();
  vault = {
    phase: 'staged',
    credential: {
      token,
      session: {
        grantId,
        workspaceId,
        scopes,
        expiresAt: input.expiresAt,
      },
    },
  };
  scheduleExpiration(input.expiresAt);
}

export function activateStagedSupportMode(input: {
  grantId: string;
  workspaceId: string;
  workspaceName: string;
  workspaceSlug?: string | null;
  sessionType?: SupportModeSession['sessionType'];
}): SupportModeSession {
  if (
    vault.phase !== 'staged'
    || vault.credential.session.grantId !== input.grantId
    || vault.credential.session.workspaceId !== input.workspaceId
    || new Date(vault.credential.session.expiresAt).getTime() <= Date.now()
  ) {
    clearSupportModeCredential('expired');
    throw new Error('A ativação temporária expirou. Ative o acesso novamente.');
  }
  const activeSession: SupportModeSession = {
    ...vault.credential.session,
    workspaceName: input.workspaceName,
    workspaceSlug: input.workspaceSlug,
    sessionType: input.sessionType ?? 'support_approved',
  };
  const credential = vault.credential;
  invalidateSupportGeneration();
  vault = { phase: 'active', credential, session: activeSession };
  emit(activeSession);
  return activeSession;
}

export function discardStagedSupportMode(): void {
  if (vault.phase !== 'staged') return;
  clearExpirationTimer();
  invalidateSupportGeneration();
  vault = removeTombstone() ? { phase: 'inactive' } : { phase: 'bootstrap' };
}

export function clearSupportModeCredential(reason: SupportModeEndReason = 'manual'): void {
  if (vault.phase === 'inactive') return;
  if (vault.phase === 'bootstrap' || vault.phase === 'ending') {
    abortAllSupportModeRequests();
    return;
  }
  clearExpirationTimer();
  if (vault.phase === 'staged') {
    discardStagedSupportMode();
    return;
  }
  const session = vault.session;
  persistTombstone();
  invalidateSupportGeneration();
  vault = { phase: 'ending', session, reason };
  emit(null, reason);
}

export function completeSupportModeEnding(onBeforeRelease?: () => void): boolean {
  if (vault.phase !== 'ending' && vault.phase !== 'bootstrap') return true;
  if (!removeTombstone()) return false;
  try {
    onBeforeRelease?.();
  } catch (error) {
    persistTombstone();
    throw error;
  }
  invalidateSupportGeneration();
  vault = { phase: 'inactive' };
  return true;
}

export function getSupportModePhase(): SupportModePhase {
  return vault.phase;
}

export function getActiveSupportMode(): SupportModeSession | null {
  if (vault.phase !== 'active') return null;
  if (new Date(vault.session.expiresAt).getTime() <= Date.now()) {
    clearSupportModeCredential('expired');
    return null;
  }
  return vault.session;
}

export function captureSupportModeRequestIdentity(
  expectedSession?: Pick<SupportModeSession, 'grantId' | 'workspaceId'>,
): SupportModeRequestIdentity | null {
  const session = getActiveSupportMode();
  if (!session || vault.phase !== 'active') return null;
  if (
    expectedSession
    && (session.grantId !== expectedSession.grantId || session.workspaceId !== expectedSession.workspaceId)
  ) {
    return null;
  }
  return { generation, grantId: session.grantId, workspaceId: session.workspaceId };
}

export function isSupportModeRequestIdentityCurrent(identity: SupportModeRequestIdentity): boolean {
  const session = getActiveSupportMode();
  return Boolean(
    session
    && vault.phase === 'active'
    && generation === identity.generation
    && session.grantId === identity.grantId
    && session.workspaceId === identity.workspaceId,
  );
}

export function registerSupportModeRequest(
  identity: SupportModeRequestIdentity,
  callerSignal?: AbortSignal | null,
): SupportModeRequestLease {
  if (!isSupportModeRequestIdentityCurrent(identity)) {
    throw new SupportModeRequestAbortedError();
  }

  const requestId = Symbol('support-request');
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  if (callerSignal?.aborted) controller.abort();
  else callerSignal?.addEventListener('abort', abortFromCaller, { once: true });
  const detachCallerSignal = () => callerSignal?.removeEventListener('abort', abortFromCaller);
  supportRequests.set(requestId, { controller, detachCallerSignal });

  return {
    signal: controller.signal,
    assertCurrent: () => {
      if (controller.signal.aborted || !isSupportModeRequestIdentityCurrent(identity)) {
        throw new SupportModeRequestAbortedError();
      }
    },
    release: () => {
      detachCallerSignal();
      supportRequests.delete(requestId);
    },
  };
}

export function resolveSupportRequest(path: string, method = 'GET'): SupportRequestDecision {
  const pathname = routePath(path);
  if (pathname === '/api/v1/platform' || pathname.startsWith('/api/v1/platform/')) {
    return { kind: 'normal' };
  }
  if (!pathname.startsWith('/api/v1/')) return { kind: 'normal' };
  if (vault.phase === 'bootstrap' || vault.phase === 'staged' || vault.phase === 'ending') {
    return { kind: 'blocked', requiredScope: null };
  }

  const session = getActiveSupportMode();
  if (vault.phase !== 'active') {
    return vault.phase === 'inactive'
      ? { kind: 'normal' }
      : { kind: 'blocked', requiredScope: null };
  }
  if (!session) return { kind: 'blocked', requiredScope: null };

  const route = matchSupportModeRoute(path, method);
  if (!route || !session.scopes.includes(route.scope)) {
    return { kind: 'blocked', requiredScope: route?.scope ?? null };
  }
  return {
    kind: 'support',
    token: vault.credential.token,
    requiredScope: route.scope,
    identity: { generation, grantId: session.grantId, workspaceId: session.workspaceId },
  };
}

export function subscribeToSupportMode(listener: SupportModeListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function beginPageHideTeardown(): void {
  if (vault.phase === 'inactive') return;
  clearExpirationTimer();
  persistTombstone();
  invalidateSupportGeneration();
  vault = { phase: 'bootstrap' };
  emit(null, 'pagehide');
}

initializeSupportModeFromStorage();

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', beginPageHideTeardown);
}
