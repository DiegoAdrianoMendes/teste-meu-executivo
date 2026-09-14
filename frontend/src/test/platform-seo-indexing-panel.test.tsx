/** @vitest-environment jsdom */
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PlatformSeoIndexingPanel } from '@/components/platform/PlatformSeoIndexingPanel';

const api = vi.hoisted(() => ({
  fetchIndexing: vi.fn(),
  fetchMetrics: vi.fn(),
  publish: vi.fn(),
  saveIndexing: vi.fn(),
}));

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>(
    '@/services/platform',
  );
  return {
    ...actual,
    fetchPlatformBlogIndexing: api.fetchIndexing,
    fetchPlatformBlogPostMetrics: api.fetchMetrics,
    publishPlatformBlogResource: api.publish,
    savePlatformBlogIndexing: api.saveIndexing,
  };
});

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <PlatformSeoIndexingPanel />
    </QueryClientProvider>,
  );
}

describe('PlatformSeoIndexingPanel', () => {
  beforeEach(() => {
    api.fetchIndexing.mockReset().mockResolvedValue({ draft: null });
    api.fetchMetrics.mockReset();
  });

  afterEach(cleanup);

  it('renders the server-side aggregate without requesting every post', async () => {
    api.fetchMetrics.mockResolvedValue({
      total: 3,
      indexable: 2,
      includedInSitemap: 2,
      includedInLlms: 1,
      scored: 3,
      averageSeoScore: 79,
      dataState: 'available',
    });

    renderPanel();

    expect(await screen.findByText('79/100')).toBeInTheDocument();
    expect(screen.getAllByText('2')).toHaveLength(2);
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(api.fetchMetrics).toHaveBeenCalledTimes(1);
  });

  it('does not turn absence of posts into fictional zero metrics', async () => {
    api.fetchMetrics.mockResolvedValue({
      total: 0,
      indexable: 0,
      includedInSitemap: 0,
      includedInLlms: 0,
      scored: 0,
      averageSeoScore: null,
      dataState: 'insufficient_data',
    });

    renderPanel();

    expect(await screen.findAllByText('Dados insuficientes')).toHaveLength(4);
    expect(screen.queryByText('0/100')).not.toBeInTheDocument();
  });
});
