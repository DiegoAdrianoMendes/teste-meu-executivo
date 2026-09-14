/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearClientSession, saveAdministrativeSessionToken } from '@/services/auth';
import { fetchPlatformWorkItems } from '@/services/platform-work-items';

describe('platform work-item query contracts', () => {
  beforeEach(() => saveAdministrativeSessionToken('platform-token'));

  afterEach(() => {
    clearClientSession();
    vi.unstubAllGlobals();
  });

  it('sends safe server-side search, responsibility and cursor filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [], nextCursor: null, hasMore: false, generatedAt: new Date().toISOString(),
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchPlatformWorkItems({
      scope: 'platform',
      search: '  ticket SUP-42  ',
      assignedToUserId: 'user-1',
      unassigned: true,
      priority: 'P1',
      sourceType: 'support_ticket',
      overdue: true,
      cursor: 'cursor-seguro',
    });

    const url = new URL(String(fetchMock.mock.calls[0]?.[0]), 'http://local.test');
    expect(url.searchParams.get('search')).toBe('ticket SUP-42');
    expect(url.searchParams.get('assignedToUserId')).toBe('user-1');
    expect(url.searchParams.get('unassigned')).toBe('true');
    expect(url.searchParams.get('priorities')).toBe('P1');
    expect(url.searchParams.get('sourceTypes')).toBe('support_ticket');
    expect(url.searchParams.get('overdue')).toBe('true');
    expect(url.searchParams.get('cursor')).toBe('cursor-seguro');
  });
});
