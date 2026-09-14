/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlatformIncidentPanel } from '@/components/platform/PlatformIncidentPanel';

const api = vi.hoisted(() => ({ create: vi.fn(), fetch: vi.fn(), update: vi.fn() }));
vi.mock('@/services/platform', () => ({
  createPlatformIncident: api.create,
  fetchPlatformIncidents: api.fetch,
  updatePlatformIncident: api.update,
}));
vi.mock('@/blackbox/session-manifest', () => ({
  useBlackboxManifest: () => ({ effectiveUser: { id: 'root', isPlatformRoot: true } }),
}));

function renderPanel(route = '/blackbox/governance/security') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<MemoryRouter initialEntries={[route]}><QueryClientProvider client={queryClient}><PlatformIncidentPanel /></QueryClientProvider></MemoryRouter>);
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('PlatformIncidentPanel', () => {
  it('opens and resolves a managed incident as platform root', async () => {
    api.fetch.mockResolvedValueOnce({ items: [], openCount: 0, nextCursor: null, hasMore: false }).mockResolvedValue({
      openCount: 1,
      nextCursor: null,
      hasMore: false,
      items: [{ id: 'incident-1', title: 'Fila acumulando', summary: 'Worker sem vazão.', severity: 'sev2', status: 'investigating', components: [], ownerUserId: null, createdByUserId: 'root', updates: [{ status: 'investigating', note: 'Worker sem vazão.', actorId: 'root', occurredAt: '2026-07-27T20:00:00Z' }], startedAt: '2026-07-27T20:00:00Z', resolvedAt: null, createdAt: '2026-07-27T20:00:00Z', updatedAt: '2026-07-27T20:00:00Z' }],
    });
    api.create.mockResolvedValue({ id: 'incident-1' });
    api.update.mockResolvedValue({ id: 'incident-1', status: 'resolved' });
    renderPanel();

    fireEvent.change(await screen.findByLabelText('Título'), { target: { value: 'Fila acumulando' } });
    fireEvent.change(screen.getByLabelText('Resumo inicial'), { target: { value: 'Worker sem vazão.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Abrir incidente' }));
    await waitFor(() => expect(api.create).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Fila acumulando', severity: 'sev3' }),
      expect.anything(),
    ));
    expect(await screen.findByText('Fila acumulando')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Status de Fila acumulando'));
    fireEvent.click(await screen.findByText('Resolvido'));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(
      'incident-1',
      expect.objectContaining({ status: 'resolved' }),
    ));
  });

  it('highlights an incident opened from a dashboard drill-down', async () => {
    api.fetch.mockResolvedValue({
      openCount: 1,
      nextCursor: null,
      hasMore: false,
      items: [{ id: 'incident-focus', title: 'Incidente em foco', summary: 'Resumo seguro.', severity: 'sev2', status: 'monitoring', components: [], ownerUserId: null, createdByUserId: 'root', updates: [], startedAt: '2026-07-27T20:00:00Z', resolvedAt: null, createdAt: '2026-07-27T20:00:00Z', updatedAt: '2026-07-27T20:00:00Z' }],
    });

    renderPanel('/blackbox/governance/security?incident=incident-focus');

    const heading = await screen.findByText('Incidente em foco');
    expect(heading.closest('article')).toHaveAttribute('aria-current', 'true');
  });

  it('navigates incident cursor pages without conflating page and platform totals', async () => {
    api.fetch.mockImplementation(({ cursor }: { cursor?: string }) => Promise.resolve({
      openCount: 30,
      nextCursor: cursor ? null : 'cursor-2',
      hasMore: !cursor,
      items: [{
        id: cursor ? 'incident-26' : 'incident-1',
        title: cursor ? 'Incidente 26' : 'Incidente 1',
        summary: 'Resumo seguro.', severity: 'sev3', status: 'investigating', components: [],
        ownerUserId: null, createdByUserId: 'root', updates: [],
        startedAt: '2026-07-27T20:00:00Z', resolvedAt: null,
        createdAt: '2026-07-27T20:00:00Z', updatedAt: '2026-07-27T20:00:00Z',
      }],
    }));

    renderPanel();
    await screen.findByText('Incidente 1');
    expect(screen.getByText(/1 incidente\(s\) nesta página · 30 aberto/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));
    await waitFor(() => expect(api.fetch).toHaveBeenCalledWith({ cursor: 'cursor-2', limit: 25 }));
    await screen.findByText('Incidente 26');
    expect(screen.getByRole('button', { name: 'Anterior' })).not.toBeDisabled();
  });
});
