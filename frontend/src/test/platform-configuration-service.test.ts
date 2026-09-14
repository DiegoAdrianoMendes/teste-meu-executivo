/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearClientSession, saveAdministrativeSessionToken } from '@/services/auth';
import {
  fetchPlatformConfiguration,
  fetchPlatformConfigurationVersionPage,
  fetchPlatformAuditEvents,
  fetchPlatformOperationalLogs,
  fetchPlatformFeatureFlags,
  fetchPlatformGovernanceVersionPage,
  rollbackPlatformConfiguration,
  savePlatformConfiguration,
  updatePlatformFeatureFlag,
} from '@/services/platform';

const document = {
  id: '11111111-1111-4111-8111-111111111111',
  namespace: 'brand.identity',
  schemaVersion: '1.0',
  currentVersion: 2,
  etag: 'etag-2',
  payload: { name: 'Meu Executivo' },
  updatedByUserId: null,
  createdAt: '2026-07-27T10:00:00Z',
  updatedAt: '2026-07-27T11:00:00Z',
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('platform global configuration service', () => {
  beforeEach(() => {
    saveAdministrativeSessionToken('platform-token');
  });

  afterEach(() => {
    clearClientSession();
    vi.unstubAllGlobals();
  });

  it('encodes the namespace in document and history reads', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(document))
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, limit: 25, offset: 50, hasMore: false }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchPlatformConfiguration('brand/identity');
    await fetchPlatformConfigurationVersionPage('brand/identity', { limit: 25, offset: 50 });

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/configurations/brand%2Fidentity');
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain('/configurations/brand%2Fidentity/versions/page?limit=25&offset=50');
  });

  it('pages governance history instead of loading every immutable version', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      items: [], total: 0, limit: 10, offset: 20, hasMore: false,
    }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchPlatformGovernanceVersionPage('security', { limit: 10, offset: 20 });

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      '/platform/governance/security/versions/page?limit=10&offset=20',
    );
  });

  it('publishes with the current ETag and an auditable reason', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(document));
    vi.stubGlobal('fetch', fetchMock);

    await savePlatformConfiguration('brand.identity', { name: 'Meu Executivo' }, {
      schemaVersion: '1.0',
      reason: 'Atualização da marca',
      etag: 'etag-1',
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe('PUT');
    expect(new Headers(init.headers).get('If-Match')).toBe('"etag-1"');
    expect(JSON.parse(String(init.body))).toEqual({
      payload: { name: 'Meu Executivo' },
      schemaVersion: '1.0',
      reason: 'Atualização da marca',
    });
  });

  it('restores a version using optimistic concurrency', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(document));
    vi.stubGlobal('fetch', fetchMock);

    await rollbackPlatformConfiguration('brand.identity', 1, 'etag-2');

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/configurations/brand.identity/rollback/1');
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(new Headers(init.headers).get('If-Match')).toBe('"etag-2"');
  });

  it('queries the real platform audit trail with encoded filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [], count: 0, limit: 100, offset: 0 }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchPlatformAuditEvents(new URLSearchParams({ search: 'workspace 360', outcome: 'success' }));

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain('/platform/audit-events?');
    expect(url).toContain('search=workspace+360');
    expect(url).toContain('result=success');
  });

  it('queries persisted operational logs with encoded filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [], count: 0, limit: 50, nextCursor: null, hasMore: false }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchPlatformOperationalLogs(new URLSearchParams({ search: 'worker timeout', severity: 'critical', cursor: 'opaque-page' }));

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain('/platform/governance/operational-logs?');
    expect(url).toContain('search=worker+timeout');
    expect(url).toContain('severity=critical');
    expect(url).toContain('cursor=opaque-page');
  });

  it('reads and updates an individual feature flag with concurrency metadata', async () => {
    const catalog = { flags: { 'inbox.v2': false }, etag: 'flags-1', currentVersion: 1, updatedAt: null };
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(catalog)));
    vi.stubGlobal('fetch', fetchMock);

    await fetchPlatformFeatureFlags();
    await updatePlatformFeatureFlag('inbox.v2', true, 'Liberação controlada', 'flags-1');

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/platform/governance/feature-flags/catalog');
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain('/platform/governance/feature-flags/inbox.v2');
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({
      enabled: true,
      reason: 'Liberação controlada',
      expectedEtag: 'flags-1',
    });
  });
});
