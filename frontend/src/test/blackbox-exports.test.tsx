/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BlackboxExports from '@/pages/BlackboxExports';

const exportApi = vi.hoisted(() => ({
  fetch: vi.fn(),
  create: vi.fn(),
  cancel: vi.fn(),
  download: vi.fn(),
}));

vi.mock('@/blackbox/session-manifest', () => ({
  useBlackboxManifest: () => ({
    effectiveUser: {
      id: 'finance-user',
      name: 'Financeiro',
      isPlatformRoot: false,
      platformAccessActive: true,
      platformPermissions: [
        { module: 'dashboard', action: 'view' },
        { module: 'finance', action: 'export' },
      ],
    },
  }),
}));

vi.mock('@/services/platform-exports', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-exports')>('@/services/platform-exports');
  return {
    ...actual,
    fetchPlatformExports: exportApi.fetch,
    createPlatformExport: exportApi.create,
    cancelPlatformExport: exportApi.cancel,
    downloadPlatformExport: exportApi.download,
  };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><BlackboxExports /></QueryClientProvider>);
}

beforeEach(() => {
  exportApi.fetch.mockReset().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  exportApi.create.mockReset().mockResolvedValue({ id: 'job-1' });
  exportApi.cancel.mockReset();
  exportApi.download.mockReset();
});

afterEach(cleanup);

describe('BlackboxExports', () => {
  it('shows only datasets authorized by the session manifest', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Central de Downloads' })).toBeInTheDocument();
    expect(screen.getByText('Cobrança')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: 'Dataset' }));
    expect(screen.queryByRole('option', { name: 'Auditoria' })).not.toBeInTheDocument();
  });

  it('creates an asynchronous job with an idempotency key', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Gerar arquivo/i }));
    await waitFor(() => expect(exportApi.create).toHaveBeenCalledTimes(1));
    expect(exportApi.create.mock.calls[0][0]).toMatchObject({ dataset: 'billing', format: 'csv' });
    expect(exportApi.create.mock.calls[0][0].idempotencyKey).toMatch(/^export:billing:csv:/);
  });
});
