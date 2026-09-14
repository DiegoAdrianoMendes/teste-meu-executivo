import { beforeEach, describe, expect, it, vi } from 'vitest';

const httpMocks = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>();
  return { ...actual, apiFetch: httpMocks.apiFetch };
});

import { toggleMatterTask, updateMatterDetail } from '@/services/matters';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('services/matters: contrato de transporte (V9-b)', () => {
  it('o PATCH do caso carimba a origem manual_spa e envia só o campo pedido', async () => {
    httpMocks.apiFetch.mockResolvedValue({ id: 'matter-1', client: {}, tasks: [], timeline: [], documents: [] });
    await updateMatterDetail('matter-1', { starsRating: 3 }).catch(() => undefined);

    expect(httpMocks.apiFetch).toHaveBeenCalledWith('/api/v1/matters/matter-1', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ starsRating: 3 }),
      headers: { 'X-KLM-Change-Source': 'manual_spa' },
    }));
  });

  it('o toggle da tarefa manda a versão esperada no query param e devolve a versão nova', async () => {
    httpMocks.apiFetch.mockResolvedValue({ id: 'task-1', status: 'done', version: 4, completedAt: '2026-09-04T12:00:00Z' });
    const result = await toggleMatterTask('task-1', 3);

    expect(httpMocks.apiFetch).toHaveBeenCalledWith('/api/v1/tasks/task-1/toggle?expectedVersion=3', { method: 'PATCH' });
    expect(result).toEqual({ id: 'task-1', status: 'done', version: 4, completedAt: '2026-09-04T12:00:00Z' });
  });
});
