/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BlackboxGlobalSettings from '@/pages/BlackboxGlobalSettings';

const configurationApi = vi.hoisted(() => ({
  fetch: vi.fn(),
  fetchVersions: vi.fn(),
  rollback: vi.fn(),
  save: vi.fn(),
}));

vi.mock('@/blackbox/session-manifest', () => ({
  useBlackboxManifest: () => ({
    effectiveUser: {
      id: 'root',
      isPlatformRoot: true,
      platformPermissions: [],
    },
  }),
}));

vi.mock('@/services/platform', () => ({
  fetchPlatformConfiguration: configurationApi.fetch,
  fetchPlatformConfigurationVersionPage: configurationApi.fetchVersions,
  rollbackPlatformConfiguration: configurationApi.rollback,
  savePlatformConfiguration: configurationApi.save,
}));

function renderPage(route: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={queryClient}>
        <BlackboxGlobalSettings />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BlackboxGlobalSettings source context', () => {
  it('opens the exact configuration namespace carried by a work-item link', async () => {
    configurationApi.fetch.mockImplementation(async (namespace: string) => ({
      id: 'configuration-1',
      namespace,
      schemaVersion: '1.0',
      currentVersion: 2,
      etag: 'etag-2',
      payload: { source: namespace },
      updatedByUserId: 'root',
      createdAt: '2026-07-30T12:00:00Z',
      updatedAt: '2026-07-30T12:30:00Z',
    }));
    configurationApi.fetchVersions.mockResolvedValue({
      items: [],
      total: 0,
      limit: 10,
      offset: 0,
      hasMore: false,
    });

    renderPage(
      '/blackbox/settings?sourceType=configuration'
      + '&sourceId=configuration-1'
      + '&tab=configuration'
      + '&focus=context'
      + '&namespace=workspace.experience',
    );

    await waitFor(() => expect(configurationApi.fetch).toHaveBeenCalledWith(
      'workspace.experience',
    ));
    expect(screen.getByLabelText('Namespace')).toHaveValue('workspace.experience');
    expect(await screen.findByText('Versão 2')).toBeInTheDocument();
  });
});
