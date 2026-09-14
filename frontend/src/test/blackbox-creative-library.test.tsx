/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BlackboxCreativeLibrary from '@/pages/BlackboxCreativeLibrary';

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

const studiosApi = vi.hoisted(() => ({
  page: vi.fn(),
  save: vi.fn(),
  archive: vi.fn(),
}));

vi.mock('@/services/blackbox-studios', async () => {
  const actual = await vi.importActual<typeof import('@/services/blackbox-studios')>(
    '@/services/blackbox-studios',
  );
  return {
    ...actual,
    fetchCreativeLibraryPage: studiosApi.page,
    saveCreativeLibraryItem: studiosApi.save,
    archiveCreativeLibraryItem: studiosApi.archive,
  };
});

function libraryPage(offset: number) {
  return {
    currentVersion: 7,
    etag: 'etag-7',
    updatedAt: '2026-07-29T12:00:00Z',
    total: 26,
    limit: 25,
    offset,
    hasMore: offset === 0,
    items: [{
      id: `creative-${offset}`,
      name: offset === 0 ? 'Primeiro criativo' : 'Último criativo',
      kind: 'creative' as const,
      channel: 'meta' as const,
      objective: 'Provar paginação do acervo.',
      status: 'approved' as const,
      tags: [],
      createdAt: '2026-07-29T12:00:00Z',
      updatedAt: '2026-07-29T12:00:00Z',
    }],
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BlackboxCreativeLibrary />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BlackboxCreativeLibrary', () => {
  it('uses the filtered server page and its canonical total', async () => {
    studiosApi.page.mockImplementation(({ offset }: { offset: number }) => (
      Promise.resolve(libraryPage(offset))
    ));

    renderPage();

    await screen.findByText('Primeiro criativo');
    expect(screen.getByText('26 item(ns) no filtro atual.')).toBeInTheDocument();
    expect(screen.getByText('1–1 de 26')).toBeInTheDocument();
    expect(studiosApi.page).toHaveBeenLastCalledWith({ status: 'active', limit: 25, offset: 0 });

    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));

    await waitFor(() => expect(studiosApi.page).toHaveBeenLastCalledWith({
      status: 'active', limit: 25, offset: 25,
    }));
    await screen.findByText('Último criativo');
    expect(screen.getByText('26–26 de 26')).toBeInTheDocument();
  });
});
