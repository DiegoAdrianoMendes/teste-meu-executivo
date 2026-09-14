/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiDownload: vi.fn(), apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({
  API_BASE: '',
  apiDownload: http.apiDownload,
  apiFetch: http.apiFetch,
}));

import {
  downloadLocalStorageObject,
  fetchLocalStorageObjects,
} from '@/services/local-storage';

afterEach(() => vi.clearAllMocks());

describe('local storage frontend transport', () => {
  it('uses the tenant object API and accepts only local-storage ticket paths', async () => {
    http.apiFetch
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({
        objectId: 'object-1',
        version: 2,
        downloadUrl: '/api/v1/local-storage/object-download?ticket=local-ticket',
        expiresAt: '2026-07-19T12:05:00Z',
      });
    http.apiDownload.mockResolvedValue(new Blob(['file']));

    await fetchLocalStorageObjects(true);
    await downloadLocalStorageObject('object-1', 2);

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/local-storage/objects?include_deleted=true',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/local-storage/objects/object-1/download-ticket?expires_in=300&version=2',
      { method: 'POST' },
    );
    expect(http.apiDownload).toHaveBeenCalledWith(
      '/api/v1/local-storage/object-download?ticket=local-ticket',
    );
  });

  it('rejects a ticket URL that attempts to leave the configured API origin', async () => {
    http.apiFetch.mockResolvedValue({
      objectId: 'object-1',
      version: 1,
      downloadUrl: 'https://attacker.invalid/api/v1/local-storage/object-download?ticket=stolen',
      expiresAt: '2026-07-19T12:05:00Z',
    });

    await expect(downloadLocalStorageObject('object-1')).rejects.toThrow('origem não autorizada');
    expect(http.apiDownload).not.toHaveBeenCalled();
  });
});
