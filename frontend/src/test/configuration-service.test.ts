import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  fetchConfigurationDocument,
  fetchConfigurationVersions,
  rollbackConfigurationDocument,
  saveConfigurationDocument,
} from '@/services/configurations';

afterEach(() => vi.clearAllMocks());

describe('configuration API contract', () => {
  it('keeps target selectors after the versions and rollback route segments', async () => {
    http.apiFetch.mockResolvedValue({});
    const target = {
      namespace: 'sales.playbook',
      scope: 'role' as const,
      roleKey: 'closer',
    };

    await fetchConfigurationDocument(target);
    await fetchConfigurationVersions(target);
    await rollbackConfigurationDocument(target, 3, 'etag-4');

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/configurations/sales.playbook/documents/role?role_key=closer',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/configurations/sales.playbook/documents/role/versions?role_key=closer',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      3,
      '/api/v1/configurations/sales.playbook/documents/role/rollback/3?role_key=closer',
      { method: 'POST', headers: { 'If-Match': '"etag-4"' } },
    );
  });

  it('writes the selected user layer with optimistic concurrency', async () => {
    http.apiFetch.mockResolvedValue({});
    await saveConfigurationDocument(
      { namespace: 'agenda.policy', scope: 'user', userId: 'user-1' },
      { availability: 'restricted' },
      { etag: 'etag-2', reason: 'Disponibilidade individual' },
    );

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/configurations/agenda.policy/documents/user?user_id=user-1',
      expect.objectContaining({
        method: 'PUT',
        headers: { 'If-Match': '"etag-2"' },
      }),
    );
    expect(JSON.parse(http.apiFetch.mock.calls[0][1].body)).toEqual({
      payload: { availability: 'restricted' },
      schemaVersion: '1.0',
      reason: 'Disponibilidade individual',
      roleKey: null,
      userId: 'user-1',
    });
  });
});
