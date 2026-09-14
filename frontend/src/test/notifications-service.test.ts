import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/services/notifications';

afterEach(() => vi.clearAllMocks());

describe('notifications transport', () => {
  it('loads the current user inbox with a bounded limit', async () => {
    http.apiFetch.mockResolvedValue([]);

    await fetchNotifications(500);

    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/notifications?limit=100');
  });

  it('persists individual and bulk read state', async () => {
    http.apiFetch.mockResolvedValue(undefined);

    await markNotificationRead('notice/one');
    await markAllNotificationsRead();

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/notifications/notice%2Fone/read',
      { method: 'PATCH' },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/notifications/mark-all-read',
      { method: 'POST' },
    );
  });
});
