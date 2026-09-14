// @vitest-environment node

import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  fetchPlatformConfigurationDraftPage,
  fetchPlatformConfigurationDraftVersionPage,
  fetchPlatformConfigurationWorkspace,
  fetchPlatformConfigurationWorkspacePage,
} from '@/services/platform-configuration-studio';

afterEach(() => vi.clearAllMocks());

describe('platform configuration studio transport', () => {
  it('resolves the selected workspace by exact id', async () => {
    http.apiFetch.mockResolvedValue({ id: 'workspace-1', canRead: true });

    await fetchPlatformConfigurationWorkspace('workspace/1');

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/configuration-studio/workspaces/workspace%2F1/access',
    );
  });

  it('keeps discovery paginated', async () => {
    http.apiFetch.mockResolvedValue({ items: [], total: 0 });

    await fetchPlatformConfigurationWorkspacePage({
      search: 'acme',
      limit: 20,
      offset: 40,
    });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/configuration-studio/workspaces/page?limit=20&offset=40&search=acme',
    );
  });

  it('pages drafts and immutable versions instead of loading full collections', async () => {
    http.apiFetch.mockResolvedValue({ items: [], total: 0, hasMore: false });

    await fetchPlatformConfigurationDraftPage('workspace/1', {
      kind: 'flow',
      limit: 10,
      offset: 20,
    });
    await fetchPlatformConfigurationDraftVersionPage('workspace/1', 'draft/1', {
      limit: 10,
      offset: 30,
    });

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/configuration-studio/workspaces/workspace%2F1/drafts/page?limit=10&offset=20&kind=flow',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/configuration-studio/workspaces/workspace%2F1/drafts/draft%2F1/versions/page?limit=10&offset=30',
    );
  });
});
