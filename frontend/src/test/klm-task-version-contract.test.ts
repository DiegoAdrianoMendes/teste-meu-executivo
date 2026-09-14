import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({
  API_BASE: '',
  apiFetch: http.apiFetch,
}));

import { klmApi } from '@/klm/api';

afterEach(() => vi.clearAllMocks());

describe('legacy task compatibility transport', () => {
  it('sends the optimistic version required by the toggle endpoint', async () => {
    http.apiFetch.mockResolvedValue({ id: 'task-1', version: 8, status: 'done' });

    await klmApi.toggleMatterTask('task-1', 7);

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/tasks/task-1/toggle?expectedVersion=7',
      { method: 'PATCH' },
    );
  });
});
