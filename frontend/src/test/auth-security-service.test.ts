/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clearClientSession,
  getAdministrativeSessionToken,
  getToken,
  saveAdministrativeSessionToken,
  saveToken,
} from '@/services/auth';
import {
  fetchCurrentAdministrativeSession,
  verifyAdministrativeSession,
} from '@/services/auth-security';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('auth security service', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    clearClientSession();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearClientSession();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('drops an invalid administrative token without destroying the user session', async () => {
    saveToken('main-access-token');
    saveAdministrativeSessionToken('expired-admin-token');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'expired' }, 401));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchCurrentAdministrativeSession()).resolves.toBeNull();

    expect(getAdministrativeSessionToken()).toBeNull();
    expect(getToken()).toBe('main-access-token');
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Authorization')).toBe('Bearer expired-admin-token');
  });

  it('stores the purpose-bound administrative token only after verification succeeds', async () => {
    saveToken('main-access-token');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      administrativeSessionToken: 'new-admin-token',
      tokenType: 'admin_session',
      expiresIn: 900,
      session: {
        id: 'session-1',
        mfaMethod: 'challenge',
        expiresAt: '2026-07-19T15:15:00Z',
        lastVerifiedAt: '2026-07-19T15:00:00Z',
        revokedAt: null,
        revocationReason: null,
        createdAt: '2026-07-19T15:00:00Z',
      },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await verifyAdministrativeSession('challenge-1', { code: '123456' });

    expect(getAdministrativeSessionToken()).toBe('new-admin-token');
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Authorization')).toBe('Bearer main-access-token');
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({ code: '123456' });
  });
});
