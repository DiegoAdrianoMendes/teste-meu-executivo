// @vitest-environment node

import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  createPlatformBlogPost,
  fetchPlatformBlogPostMetrics,
  publishPlatformSiteResource,
  savePlatformBlogSettings,
  schedulePlatformBlogPost,
  savePlatformSiteSettings,
} from '@/services/platform';
import type { BlogSettings, PublicSiteSettings } from '@/types';

afterEach(() => vi.clearAllMocks());

describe('platform site administrative transport', () => {
  it('sends the mandatory operation reason when saving and publishing', async () => {
    http.apiFetch.mockResolvedValue({});
    const settings = { brandName: 'Meu Executivo' } as PublicSiteSettings;

    await savePlatformSiteSettings(settings, 'Revisão de marca aprovada & registrada.');
    await publishPlatformSiteResource('settings', 'Publicação aprovada em reunião.');

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/site/settings',
      {
        method: 'PUT',
        headers: {
          'X-Operation-Reason':
            'Revis%C3%A3o%20de%20marca%20aprovada%20%26%20registrada.',
        },
        body: JSON.stringify(settings),
      },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/site/publish/settings',
      {
        method: 'POST',
        headers: {
          'X-Operation-Reason':
            'Publica%C3%A7%C3%A3o%20aprovada%20em%20reuni%C3%A3o.',
        },
      },
    );
  });
});

describe('platform blog administrative transport', () => {
  it('loads aggregated SEO metrics instead of the unbounded post collection', async () => {
    http.apiFetch.mockResolvedValue({
      total: 2,
      indexable: 1,
      includedInSitemap: 1,
      includedInLlms: 2,
      scored: 2,
      averageSeoScore: 84,
      dataState: 'available',
    });

    await expect(fetchPlatformBlogPostMetrics()).resolves.toMatchObject({
      total: 2,
      averageSeoScore: 84,
    });
    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/blog/posts/metrics',
    );
  });

  it('carries the audited reason through create, schedule, and document updates', async () => {
    http.apiFetch.mockResolvedValue({});
    const reason = 'Revisão editorial aprovada & registrada.';
    const settings = { pageTitle: 'Blog Meu Executivo' } as BlogSettings;

    await createPlatformBlogPost({ title: 'Novo artigo' }, reason);
    await schedulePlatformBlogPost(
      'post-1',
      '2026-08-01T12:00:00.000Z',
      reason,
    );
    await savePlatformBlogSettings(settings, reason);

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/blog/posts',
      {
        method: 'POST',
        headers: {
          'X-Operation-Reason':
            'Revis%C3%A3o%20editorial%20aprovada%20%26%20registrada.',
        },
        body: JSON.stringify({ title: 'Novo artigo' }),
      },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/blog/posts/post-1/schedule?publishAt=2026-08-01T12%3A00%3A00.000Z',
      {
        method: 'POST',
        headers: {
          'X-Operation-Reason':
            'Revis%C3%A3o%20editorial%20aprovada%20%26%20registrada.',
        },
      },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      3,
      '/api/v1/platform/blog/settings',
      {
        method: 'PUT',
        headers: {
          'X-Operation-Reason':
            'Revis%C3%A3o%20editorial%20aprovada%20%26%20registrada.',
        },
        body: JSON.stringify(settings),
      },
    );
  });
});
