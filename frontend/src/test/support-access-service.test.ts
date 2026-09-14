/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clearClientSession,
  saveAdministrativeSessionToken,
  saveToken,
} from '@/services/auth';
import {
  activatePlatformRootDirectAccess,
  activatePlatformSupportAccess,
  AdministrativeSessionRequiredError,
  approveWorkspaceSupportAccess,
  listPlatformSupportAccess,
  listWorkspaceSupportAccess,
  pagePlatformSupportAccessEvents,
  pageWorkspaceSupportAccess,
  pageWorkspaceSupportAccessEvents,
  isSupportScopeSelectionValid,
  rejectWorkspaceSupportAccess,
  requestPlatformSupportAccess,
  revokeWorkspaceSupportAccess,
  type SupportAccessGrant,
} from '@/services/support-access';
import {
  clearSupportModeCredential,
  completeSupportModeEnding,
  SUPPORT_MODE_TOMBSTONE_KEY,
} from '@/support-mode/session';

const GRANT_ID = '11111111-1111-4111-8111-111111111111';
const OTHER_GRANT_ID = '22222222-2222-4222-8222-222222222222';
const WORKSPACE_ID = '33333333-3333-4333-8333-333333333333';
const REQUESTER_ID = '44444444-4444-4444-8444-444444444444';
const APPROVER_ID = '55555555-5555-4555-8555-555555555555';

const grant: SupportAccessGrant = {
  id: GRANT_ID,
  workspaceId: WORKSPACE_ID,
  requesterUserId: REQUESTER_ID,
  reason: 'Diagnóstico local necessário para restaurar o acesso.',
  ticketReference: 'SUP-1234',
  requestedScopes: ['workspace:read'],
  requestedDurationMinutes: 15,
  state: 'requested',
  requestedAt: '2026-07-19T15:00:00Z',
  approverUserId: null,
  approvedScopes: null,
  approvedDurationMinutes: null,
  approvedAt: null,
  activatedAt: null,
  expiresAt: null,
  expiredAt: null,
  rejectedAt: null,
  rejectionReason: null,
  revokedAt: null,
  revocationReason: null,
  version: 1,
  createdAt: '2026-07-19T15:00:00Z',
  updatedAt: '2026-07-19T15:00:00Z',
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function storedValues(storage: Storage): string[] {
  return Array.from({ length: storage.length }, (_, index) => storage.getItem(storage.key(index) ?? '') ?? '');
}

function activeGrant(overrides: Record<string, unknown> = {}): SupportAccessGrant {
  const now = Date.now();
  return {
    ...grant,
    state: 'active',
    requestedAt: new Date(now - 120_000).toISOString(),
    approverUserId: APPROVER_ID,
    approvedScopes: ['workspace:read'],
    approvedDurationMinutes: 10,
    approvedAt: new Date(now - 60_000).toISOString(),
    activatedAt: new Date(now).toISOString(),
    expiresAt: new Date(now + 10 * 60_000).toISOString(),
    expiredAt: null,
    rejectedAt: null,
    rejectionReason: null,
    revokedAt: null,
    revocationReason: null,
    version: 3,
    createdAt: new Date(now - 120_000).toISOString(),
    updatedAt: new Date(now).toISOString(),
    ...overrides,
  } as SupportAccessGrant;
}

const activationRuntimeCases: Array<[
  string,
  { response?: Record<string, unknown>; grant?: Record<string, unknown> },
]> = [
  ['missing token', { response: { supportAccessToken: '' } }],
  ['wrong token type', { response: { tokenType: 'bearer' } }],
  ['non-integer expiresIn', { response: { expiresIn: 10.5 } }],
  ['inconsistent expiresIn', { response: { expiresIn: 30 } }],
  ['wrong grant id', { grant: { id: OTHER_GRANT_ID } }],
  ['missing workspace id', { grant: { workspaceId: '' } }],
  ['invalid requester', { grant: { requesterUserId: 'requester' } }],
  ['missing approver', { grant: { approverUserId: null } }],
  ['requester as approver', { grant: { approverUserId: REQUESTER_ID } }],
  ['non-active state', { grant: { state: 'approved' } }],
  ['missing requested scopes', { grant: { requestedScopes: null } }],
  ['missing approved scopes', { grant: { approvedScopes: null } }],
  ['unknown approved scope', { grant: { approvedScopes: ['unknown:read'] } }],
  ['duplicate approved scope', { grant: { approvedScopes: ['workspace:read', 'workspace:read'] } }],
  ['scope outside the request', { grant: { approvedScopes: ['clients:read'] } }],
  ['invalid requested duration', { grant: { requestedDurationMinutes: 4 } }],
  ['approved duration above request', { grant: { approvedDurationMinutes: 16 } }],
  ['missing requested timestamp', { grant: { requestedAt: null } }],
  ['missing approved timestamp', { grant: { approvedAt: null } }],
  ['missing activation timestamp', { grant: { activatedAt: null } }],
  ['missing creation timestamp', { grant: { createdAt: null } }],
  ['missing update timestamp', { grant: { updatedAt: null } }],
  ['future activation', { grant: { activatedAt: new Date(Date.now() + 120_000).toISOString() } }],
  ['terminal revocation', { grant: { revokedAt: new Date().toISOString() } }],
  ['missing reason', { grant: { reason: '' } }],
  ['missing ticket', { grant: { ticketReference: '' } }],
  ['invalid version', { grant: { version: 0 } }],
  ['expired deadline', { grant: { expiresAt: '2020-01-01T00:00:00.000Z' } }],
];

describe('support access service', () => {
  beforeEach(() => {
    clearSupportModeCredential();
    completeSupportModeEnding();
    localStorage.clear();
    sessionStorage.clear();
    clearClientSession();
    saveToken('main-access-token');
  });

  afterEach(() => {
    clearSupportModeCredential();
    completeSupportModeEnding();
    vi.unstubAllGlobals();
    clearClientSession();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('uses the real list filters for platform and workspace requests', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, limit: 25, offset: 50, hasMore: false }));
    vi.stubGlobal('fetch', fetchMock);

    await listPlatformSupportAccess({ workspaceId: 'workspace-1', state: 'approved', limit: 25 });
    await listWorkspaceSupportAccess({ state: 'active', limit: 10 });
    await pageWorkspaceSupportAccess({ state: 'requested', limit: 25, offset: 50 });

    const platformUrl = new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost');
    expect(platformUrl.pathname).toBe('/api/v1/platform/support-access/requests');
    expect(platformUrl.searchParams.get('workspace_id')).toBe('workspace-1');
    expect(platformUrl.searchParams.get('state')).toBe('approved');
    expect(platformUrl.searchParams.get('limit')).toBe('25');

    const workspaceUrl = new URL(fetchMock.mock.calls[1][0] as string, 'http://localhost');
    expect(workspaceUrl.pathname).toBe('/api/v1/workspaces/support-access/requests');
    expect(workspaceUrl.searchParams.get('state')).toBe('active');
    expect(workspaceUrl.searchParams.get('limit')).toBe('10');

    const workspacePageUrl = new URL(fetchMock.mock.calls[2][0] as string, 'http://localhost');
    expect(workspacePageUrl.pathname).toBe('/api/v1/workspaces/support-access/requests/page');
    expect(workspacePageUrl.searchParams.get('state')).toBe('requested');
    expect(workspacePageUrl.searchParams.get('limit')).toBe('25');
    expect(workspacePageUrl.searchParams.get('offset')).toBe('50');
  });

  it('sends the exact request, approval, rejection and revocation decisions', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(grant)));
    vi.stubGlobal('fetch', fetchMock);

    await requestPlatformSupportAccess({
      workspaceId: 'workspace-1',
      reason: 'Diagnóstico local necessário para restaurar o acesso.',
      ticketReference: 'SUP-1234',
      requestedScopes: ['workspace:read', 'clients:read'],
      durationMinutes: 20,
    });
    await approveWorkspaceSupportAccess('grant-1', {
      approvedScopes: ['workspace:read'],
      durationMinutes: 10,
    });
    await rejectWorkspaceSupportAccess('grant-2', { reason: 'Pedido sem necessidade atual.' });
    await revokeWorkspaceSupportAccess('grant-3', { reason: 'Diagnóstico concluído.' });

    expect(new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost').pathname).toBe('/api/v1/platform/support-access/requests');
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      workspaceId: 'workspace-1',
      reason: 'Diagnóstico local necessário para restaurar o acesso.',
      ticketReference: 'SUP-1234',
      requestedScopes: ['workspace:read', 'clients:read'],
      durationMinutes: 20,
    });
    expect(new URL(fetchMock.mock.calls[1][0] as string, 'http://localhost').pathname).toBe('/api/v1/workspaces/support-access/requests/grant-1/approve');
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
      approvedScopes: ['workspace:read'],
      durationMinutes: 10,
    });
    expect(new URL(fetchMock.mock.calls[2][0] as string, 'http://localhost').pathname).toBe('/api/v1/workspaces/support-access/requests/grant-2/reject');
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({ reason: 'Pedido sem necessidade atual.' });
    expect(new URL(fetchMock.mock.calls[3][0] as string, 'http://localhost').pathname).toBe('/api/v1/workspaces/support-access/requests/grant-3/revoke');
    expect(JSON.parse(fetchMock.mock.calls[3][1]?.body as string)).toEqual({ reason: 'Diagnóstico concluído.' });
  });

  it('loads support audit trails through paginated contracts', async () => {
    const page = { items: [], total: 0, limit: 25, offset: 0, hasMore: false };
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(page)));
    vi.stubGlobal('fetch', fetchMock);

    await pagePlatformSupportAccessEvents('grant/1', { limit: 25, offset: 50 });
    await pageWorkspaceSupportAccessEvents('grant/2', { limit: 10, offset: 20 });

    const platformUrl = new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost');
    expect(platformUrl.pathname).toBe('/api/v1/platform/support-access/requests/grant%2F1/events/page');
    expect(platformUrl.searchParams.get('limit')).toBe('25');
    expect(platformUrl.searchParams.get('offset')).toBe('50');
    const workspaceUrl = new URL(fetchMock.mock.calls[1][0] as string, 'http://localhost');
    expect(workspaceUrl.pathname).toBe('/api/v1/workspaces/support-access/requests/grant%2F2/events/page');
    expect(workspaceUrl.searchParams.get('limit')).toBe('10');
    expect(workspaceUrl.searchParams.get('offset')).toBe('20');
  });

  it('activates only with the MFA administrative header and never persists the support token', async () => {
    saveAdministrativeSessionToken('verified-admin-session');
    const activatedGrant = activeGrant();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      supportAccessToken: 'one-time-support-token',
      tokenType: 'support_access',
      expiresIn: 600,
      grant: activatedGrant,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const activation = activatePlatformSupportAccess(GRANT_ID);
    await expect(activation).resolves.toEqual({
      expiresIn: 600,
      grant: activatedGrant,
    });
    expect(JSON.stringify(await activation)).not.toContain('one-time-support-token');

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost').pathname).toBe(`/api/v1/platform/support-access/requests/${GRANT_ID}/activate`);
    expect(headers.get('Authorization')).toBe('Bearer main-access-token');
    expect(headers.get('X-Administrative-Session')).toBe('verified-admin-session');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
    expect(storedValues(localStorage)).not.toContain('one-time-support-token');
    expect(storedValues(sessionStorage)).not.toContain('one-time-support-token');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).toBe('1');
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).not.toContain(GRANT_ID);
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).not.toContain(WORKSPACE_ID);
    expect(sessionStorage.getItem(SUPPORT_MODE_TOMBSTONE_KEY)).not.toContain('workspace:read');
  });

  it('fails before the network when no MFA administrative session exists', async () => {
    saveAdministrativeSessionToken(null);
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(activatePlatformSupportAccess(GRANT_ID)).rejects.toBeInstanceOf(AdministrativeSessionRequiredError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('requires the stored MFA step-up and sends it for root-direct access', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      activatePlatformRootDirectAccess(WORKSPACE_ID, 'Diagnóstico root auditado.'),
    ).rejects.toBeInstanceOf(AdministrativeSessionRequiredError);
    expect(fetchMock).not.toHaveBeenCalled();

    saveAdministrativeSessionToken('fresh-root-step-up');
    const rootGrant = activeGrant({
      approverUserId: null,
      ticketReference: 'ROOT-audit',
      requestedScopes: ['workspace:read'],
      approvedScopes: ['workspace:read'],
    });
    fetchMock.mockResolvedValue(jsonResponse({
      supportAccessToken: 'one-time-root-support-token',
      tokenType: 'support_access',
      expiresIn: 600,
      grant: rootGrant,
    }));

    await expect(
      activatePlatformRootDirectAccess(WORKSPACE_ID, 'Diagnóstico root auditado.'),
    ).resolves.toEqual({ expiresIn: 600, grant: rootGrant });

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('X-Administrative-Session')).toBe('fresh-root-step-up');
    expect(headers.get('Authorization')).toBe('Bearer main-access-token');
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      reason: 'Diagnóstico root auditado.',
    });
  });

  it('fails closed when activation returns an incomplete support credential', async () => {
    saveAdministrativeSessionToken('verified-admin-session');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      supportAccessToken: 'one-time-support-token',
      tokenType: 'support_access',
      expiresIn: 600,
      grant: {
        ...activeGrant(),
        id: '',
      },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(activatePlatformSupportAccess(GRANT_ID)).rejects.toThrow(
      'A ativação de suporte não retornou uma credencial temporária válida.',
    );
    expect(storedValues(localStorage)).not.toContain('one-time-support-token');
    expect(storedValues(sessionStorage)).not.toContain('one-time-support-token');
  });

  it.each(activationRuntimeCases)('rejects activation runtime data with %s', async (_label, override) => {
    saveAdministrativeSessionToken('verified-admin-session');
    const runtimeGrant = activeGrant(override.grant);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      supportAccessToken: 'one-time-support-token',
      tokenType: 'support_access',
      expiresIn: 600,
      grant: runtimeGrant,
      ...(override.response ?? {}),
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(activatePlatformSupportAccess(GRANT_ID)).rejects.toThrow(
      'A ativação de suporte não retornou uma credencial temporária válida.',
    );
    expect(storedValues(localStorage)).not.toContain('one-time-support-token');
    expect(storedValues(sessionStorage)).not.toContain('one-time-support-token');
  });

  it('rejects an empty expected grant id before the network', async () => {
    saveAdministrativeSessionToken('verified-admin-session');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(activatePlatformSupportAccess('   ')).rejects.toThrow('A ativação de suporte não recebeu o grant esperado.');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('requires a primary scope whenever lead data is requested', () => {
    expect(isSupportScopeSelectionValid(['lead_data:read'])).toBe(false);
    expect(isSupportScopeSelectionValid(['clients:read', 'lead_data:read'])).toBe(true);
  });
});
