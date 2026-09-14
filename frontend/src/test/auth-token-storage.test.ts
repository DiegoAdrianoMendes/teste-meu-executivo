/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clearClientSession,
  getAccessTokenExpiresAt,
  getRefreshToken,
  getToken,
  loginWithCredentials,
  refreshAccessToken,
  removeToken,
  saveRefreshToken,
  saveToken,
} from '@/services/auth';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('auth token storage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    clearClientSession();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    clearClientSession();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('keeps the access token in memory/sessionStorage and removes legacy localStorage JWTs', () => {
    localStorage.setItem('klm_token', 'legacy-access');
    localStorage.setItem('klm_refresh_token', 'legacy-refresh');

    saveToken('session-access');

    expect(getToken()).toBe('session-access');
    expect(sessionStorage.getItem('klm_token')).toBe('session-access');
    expect(localStorage.getItem('klm_token')).toBeNull();
    expect(localStorage.getItem('klm_refresh_token')).toBeNull();
  });

  it('reads the access-token expiry used by proactive session renewal', () => {
    const payload = btoa(JSON.stringify({ exp: 1_786_350_000 }))
      .replace(/=/g, '')
      .replace(/\+/g, '-')
      .replace(/\//g, '_');

    saveToken(`header.${payload}.signature`);

    expect(getAccessTokenExpiresAt()).toBe(1_786_350_000_000);
  });

  it('never persists refresh tokens exposed by a transitional response', () => {
    saveRefreshToken('transitional-refresh');

    expect(getRefreshToken()).toBeNull();
    expect(localStorage.getItem('klm_refresh_token')).toBeNull();
    expect(sessionStorage.getItem('klm_refresh_token')).toBeNull();
  });

  it('uses credentials include for login so the browser accepts the HttpOnly cookie', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      accessToken: 'access',
      refreshToken: null,
      user: { id: 'user', name: 'User', email: 'user@example.com', role: 'operator', initials: 'US' },
      session: { activeWorkspace: null, availableWorkspaces: [], permissions: [], isInternalStaff: false, activeRole: null },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await loginWithCredentials('user@example.com', 'password');

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][1]?.credentials).toBe('include');
  });

  it('awaits backend logout before clearing client tokens', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    saveToken('access-to-revoke');

    await removeToken();

    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem('klm_token')).toBeNull();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/auth/logout');
    expect(fetchMock.mock.calls[0][1]?.credentials).toBe('include');
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Authorization')).toBe('Bearer access-to-revoke');
  });

  it('does not restore a token when an in-flight refresh finishes after logout', async () => {
    let resolveRefresh: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => {
      resolveRefresh = resolve;
    }));
    vi.stubGlobal('fetch', fetchMock);

    const pendingRefresh = refreshAccessToken();
    clearClientSession();
    resolveRefresh?.(jsonResponse({
      accessToken: 'late-access-token',
      refreshToken: null,
      user: { id: 'user', name: 'User', email: 'user@example.com', role: 'operator', initials: 'US' },
      session: { activeWorkspace: null, availableWorkspaces: [], permissions: [], isInternalStaff: false, activeRole: null },
    }));

    await expect(pendingRefresh).resolves.toBeNull();
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem('klm_token')).toBeNull();
  });
});
