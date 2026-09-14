/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CLIENT_SESSION_EXPIRED_EVENT,
  clearClientSession,
  consumeSessionExpiredNotice,
  getAdministrativeSessionToken,
  getToken,
  saveAdministrativeSessionToken,
  saveToken,
  SESSION_EXPIRED_MESSAGE,
} from '@/services/auth';
import { API_BASE, ApiError, apiFetch, SupportModeRouteDeniedError } from '@/services/http';
import {
  activateStagedSupportMode,
  clearSupportModeCredential,
  completeSupportModeEnding,
  getActiveSupportMode,
  getSupportModePhase,
  stageSupportModeCredential,
  SupportModeRequestAbortedError,
  SUPPORT_MODE_TOMBSTONE_KEY,
} from '@/support-mode/session';

const CLIENT_LIST_PATH = '/api/v1/clients?limit=100';

function jsonResponse(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function activateSupportMode(
  scopes: Parameters<typeof stageSupportModeCredential>[0]['scopes'],
  expiresAt = new Date(Date.now() + 10 * 60_000).toISOString(),
): void {
  stageSupportModeCredential({
    token: 'ephemeral-support-token',
    grantId: 'grant-1',
    workspaceId: 'workspace-1',
    scopes,
    expiresAt,
  });
  activateStagedSupportMode({
    grantId: 'grant-1',
    workspaceId: 'workspace-1',
    workspaceName: 'KLM Advocacia',
  });
}

describe('local HTTP session recovery', () => {
  beforeEach(() => {
    clearSupportModeCredential();
    completeSupportModeEnding();
    localStorage.clear();
    sessionStorage.clear();
    clearClientSession();
  });

  afterEach(() => {
    clearSupportModeCredential();
    completeSupportModeEnding();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    clearClientSession();
  });

  it('refreshes by HttpOnly cookie and retries without persisting a refresh token', async () => {
    saveToken('expired-token');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Sessão expirada' }, 401))
      .mockResolvedValueOnce(jsonResponse({
        accessToken: 'recovered-token',
        refreshToken: null,
        user: { id: 'user-local', name: 'Kleist Monteiro', email: 'local@klm.test', role: 'workspace_owner', initials: 'KM' },
        session: { activeWorkspace: null, workspaces: [], permissions: [] },
      }, 200))
      .mockResolvedValueOnce(jsonResponse({ connected: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch<{ connected: boolean }>('/api/v1/klm/omnichannel/threads')).resolves.toEqual({ connected: true });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe(`${API_BASE}/api/v1/auth/refresh`);
    expect(fetchMock.mock.calls[1][1]?.credentials).toBe('include');
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('Authorization')).toBeNull();
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get('Authorization')).toBe('Bearer recovered-token');
    expect(localStorage.getItem('klm_token')).toBeNull();
    expect(localStorage.getItem('klm_refresh_token')).toBeNull();
    expect(sessionStorage.getItem('klm_token')).toBe('recovered-token');
  });

  it('retries a transient refresh failure before repeating the authenticated request', async () => {
    saveToken('expired-token');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Sessão expirada' }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Indisponível' }, 503))
      .mockResolvedValueOnce(jsonResponse({
        accessToken: 'recovered-after-retry',
        refreshToken: null,
        user: { id: 'user-local', name: 'Kleist Monteiro', email: 'local@klm.test', role: 'workspace_owner', initials: 'KM' },
        session: { activeWorkspace: null, workspaces: [], permissions: [] },
      }, 200))
      .mockResolvedValueOnce(jsonResponse({ saved: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch<{ saved: boolean }>('/api/v1/whatsapp/opt-ins', {
      method: 'POST',
      body: JSON.stringify({ phone: '+5511999990000' }),
    })).resolves.toEqual({ saved: true });

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[1][0]).toBe(`${API_BASE}/api/v1/auth/refresh`);
    expect(fetchMock.mock.calls[2][0]).toBe(`${API_BASE}/api/v1/auth/refresh`);
  });

  it('announces session expiration and preserves a visible login notice after refresh rejection', async () => {
    saveToken('expired-token');
    const expiredListener = vi.fn();
    window.addEventListener(CLIENT_SESSION_EXPIRED_EVENT, expiredListener);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Sessão expirada' }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Refresh expirado' }, 401));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/api/v1/whatsapp/opt-ins', {
      method: 'POST',
      body: JSON.stringify({ phone: '+5511999990000' }),
    })).rejects.toMatchObject({ status: 401 });

    expect(expiredListener).toHaveBeenCalledOnce();
    expect(getToken()).toBeNull();
    expect(consumeSessionExpiredNotice()).toBe(SESSION_EXPIRED_MESSAGE);
    window.removeEventListener(CLIENT_SESSION_EXPIRED_EVENT, expiredListener);
  });

  it('keeps the local bootstrap fallback when the refresh cookie is unavailable', async () => {
    saveToken('expired-local-token');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Sessão expirada' }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Refresh ausente' }, 401))
      .mockResolvedValueOnce(jsonResponse({
        accessToken: 'local-bootstrap-token',
        refreshToken: 'ignored-local-refresh',
        user: { id: 'user-local', name: 'Kleist Monteiro', email: 'local@klm.test', role: 'workspace_owner', initials: 'KM' },
        session: { activeWorkspace: null, workspaces: [], permissions: [] },
      }, 200))
      .mockResolvedValueOnce(jsonResponse({ connected: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch<{ connected: boolean }>('/api/v1/klm/omnichannel/threads')).resolves.toEqual({ connected: true });

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[1][0]).toBe(`${API_BASE}/api/v1/auth/refresh`);
    expect(fetchMock.mock.calls[2][0]).toBe(`${API_BASE}/api/v1/klm/local/session`);
    expect(fetchMock.mock.calls[2][1]?.credentials).toBe('include');
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get('Authorization')).toBe('Bearer local-bootstrap-token');
    expect(localStorage.getItem('klm_token')).toBeNull();
    expect(localStorage.getItem('klm_refresh_token')).toBeNull();
    expect(sessionStorage.getItem('klm_token')).toBe('local-bootstrap-token');
    expect(sessionStorage.getItem('klm_refresh_token')).toBeNull();
  });

  it.each([
    'AUTH-018',
    'SUPPORT-ROOT-STEP-UP-STALE',
  ])('clears only the administrative step-up on %s', async (code) => {
    saveToken('main-access-token');
    saveAdministrativeSessionToken('expired-admin-step-up');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      detail: { code, message: 'Sessão administrativa expirada.' },
    }, 401));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/api/v1/platform/support-access/requests/grant-1/activate', {
      method: 'POST',
    })).rejects.toBeInstanceOf(ApiError);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getAdministrativeSessionToken()).toBeNull();
    expect(getToken()).toBe('main-access-token');
  });

  it('uses the support bearer only on allowed tenant routes and never on platform endpoints', async () => {
    saveToken('main-access-token');
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      scopes: ['clients:read'],
      expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      workspaceName: 'KLM Advocacia',
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [] }, 200))
      .mockResolvedValueOnce(jsonResponse([], 200));
    vi.stubGlobal('fetch', fetchMock);

    await apiFetch(CLIENT_LIST_PATH);
    await apiFetch('/api/v1/platform/support-access/requests');

    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Authorization')).toBe('Bearer ephemeral-support-token');
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('Authorization')).toBe('Bearer main-access-token');
  });

  it('ends support mode on its 401 without refreshing or logging out the main session', async () => {
    saveToken('main-access-token');
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      scopes: ['clients:read'],
      expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      workspaceName: 'KLM Advocacia',
    });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'Acesso temporário inválido.' }, 401));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(SupportModeRequestAbortedError);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getActiveSupportMode()).toBeNull();
    expect(getSupportModePhase()).toBe('ending');
    await expect(apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(SupportModeRouteDeniedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/v1/auth/refresh'))).toBe(false);
    expect(getToken()).toBe('main-access-token');
  });

  it('blocks tenant HTTP immediately during a manual teardown without fetch, refresh or principal bearer', async () => {
    saveToken('main-access-token');
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      scopes: ['clients:read'],
      expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      workspaceName: 'KLM Advocacia',
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    clearSupportModeCredential('manual');
    expect(getSupportModePhase()).toBe('ending');
    await expect(apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(SupportModeRouteDeniedError);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(getToken()).toBe('main-access-token');
  });

  it('blocks tenant HTTP exactly when the support grant expires', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T12:00:00Z'));
    saveToken('main-access-token');
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      scopes: ['clients:read'],
      expiresAt: new Date(Date.now() + 1_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      workspaceName: 'KLM Advocacia',
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await vi.advanceTimersByTimeAsync(999);
    expect(getSupportModePhase()).toBe('active');
    await vi.advanceTimersByTimeAsync(1);
    expect(getSupportModePhase()).toBe('ending');
    await expect(apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(SupportModeRouteDeniedError);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(getToken()).toBe('main-access-token');
  });

  it('blocks tenant HTTP synchronously when pagehide starts teardown', async () => {
    saveToken('main-access-token');
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      scopes: ['clients:read'],
      expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      workspaceName: 'KLM Advocacia',
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    window.dispatchEvent(new Event('pagehide'));
    expect(getSupportModePhase()).toBe('bootstrap');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).toBe('1');
    await expect(apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(SupportModeRouteDeniedError);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(getToken()).toBe('main-access-token');
  });

  it('blocks non-allowlisted tenant routes before the network during support mode', async () => {
    saveToken('main-access-token');
    stageSupportModeCredential({
      token: 'ephemeral-support-token',
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      scopes: ['clients:read'],
      expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    });
    activateStagedSupportMode({
      grantId: 'grant-1',
      workspaceId: 'workspace-1',
      workspaceName: 'KLM Advocacia',
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/api/v1/auth/me')).rejects.toBeInstanceOf(SupportModeRouteDeniedError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('denies every undeclared method, child action and query variant before fetch', async () => {
    saveToken('main-access-token');
    activateSupportMode([
      'workspace:read',
      'workspace:write',
      'clients:read',
      'clients:write',
      'matters:read',
      'matters:write',
      'tasks:write',
      'configurations:read',
      'configurations:write',
    ]);
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const matterId = '11111111-1111-4111-8111-111111111111';
    const taskId = '22222222-2222-4222-8222-222222222222';
    const deniedRequests: Array<[string, RequestInit | undefined]> = [
      [`/api/v1/clients/${matterId}`, { method: 'DELETE' }],
      [`/api/v1/matters/${matterId}/move`, { method: 'PATCH' }],
      [`/api/v1/matters/${matterId}/notes`, { method: 'POST' }],
      [`/api/v1/tasks/${taskId}/reorder`, { method: 'PATCH' }],
      ['/api/v1/configurations/brand.identity/documents/workspace/rollback', { method: 'POST' }],
      ['/api/v1/clients', undefined],
      ['/api/v1/clients?limit=100&include_deleted=true', undefined],
      [CLIENT_LIST_PATH, { method: 'HEAD' }],
      [CLIENT_LIST_PATH, { method: 'OPTIONS' }],
      ['/api/v1/admin/workspaces', undefined],
    ];

    for (const [path, init] of deniedRequests) {
      await expect(apiFetch(path, init)).rejects.toBeInstanceOf(SupportModeRouteDeniedError);
    }
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getToken()).toBe('main-access-token');
  });

  it('aborts an in-flight support request on manual ending and suppresses its stale response', async () => {
    saveToken('main-access-token');
    activateSupportMode(['clients:read']);
    let finishRequest: ((response: Response) => void) | undefined;
    let requestSignal: AbortSignal | null = null;
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      requestSignal = init?.signal ?? null;
      return new Promise<Response>((resolve) => { finishRequest = resolve; });
    });
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch(CLIENT_LIST_PATH);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    clearSupportModeCredential('manual');
    expect(requestSignal?.aborted).toBe(true);
    finishRequest?.(jsonResponse([{ name: 'Resposta antiga' }], 200));

    await expect(request).rejects.toBeInstanceOf(SupportModeRequestAbortedError);
    await expect(apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(SupportModeRouteDeniedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getToken()).toBe('main-access-token');
  });

  it('aborts an in-flight support request on pagehide and preserves only the safe tombstone', async () => {
    saveToken('main-access-token');
    activateSupportMode(['clients:read']);
    let finishRequest: ((response: Response) => void) | undefined;
    let requestSignal: AbortSignal | null = null;
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      requestSignal = init?.signal ?? null;
      return new Promise<Response>((resolve) => { finishRequest = resolve; });
    });
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch(CLIENT_LIST_PATH);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new Event('pagehide'));
    expect(requestSignal?.aborted).toBe(true);
    expect(getSupportModePhase()).toBe('bootstrap');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).toBe('1');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).not.toContain('ephemeral-support-token');
    finishRequest?.(jsonResponse([{ name: 'Resposta antiga' }], 200));

    await expect(request).rejects.toBeInstanceOf(SupportModeRequestAbortedError);
    await expect(apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(SupportModeRouteDeniedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('aborts an in-flight request exactly at the support expiration deadline', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T12:00:00Z'));
    saveToken('main-access-token');
    activateSupportMode(['clients:read'], new Date(Date.now() + 1_000).toISOString());
    let finishRequest: ((response: Response) => void) | undefined;
    let requestSignal: AbortSignal | null = null;
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      requestSignal = init?.signal ?? null;
      return new Promise<Response>((resolve) => { finishRequest = resolve; });
    });
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch(CLIENT_LIST_PATH);
    await vi.advanceTimersByTimeAsync(999);
    expect(requestSignal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(requestSignal?.aborted).toBe(true);
    finishRequest?.(jsonResponse([], 200));

    await expect(request).rejects.toBeInstanceOf(SupportModeRequestAbortedError);
    expect(getSupportModePhase()).toBe('ending');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('boots a reloaded document fail-closed from the tombstone before any tenant fetch', async () => {
    saveToken('main-access-token');
    sessionStorage.setItem(SUPPORT_MODE_TOMBSTONE_KEY, '1');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.resetModules();

    const freshSession = await import('@/support-mode/session');
    const freshHttp = await import('@/services/http');

    expect(freshSession.getSupportModePhase()).toBe('bootstrap');
    await expect(freshHttp.apiFetch(CLIENT_LIST_PATH)).rejects.toBeInstanceOf(
      freshHttp.SupportModeRouteDeniedError,
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(freshSession.completeSupportModeEnding()).toBe(true);
    expect(freshSession.getSupportModePhase()).toBe('inactive');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).toBeNull();
  });
});
