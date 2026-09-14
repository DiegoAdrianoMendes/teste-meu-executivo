import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  cancelPlatformNotice,
  createPlatformNotice,
  dispatchDuePlatformNotices,
  getPlatformNotice,
  listPlatformNoticeAudit,
  listPlatformNoticeReceipts,
  listPlatformNotices,
  previewPlatformNoticeRecipients,
  publishPlatformNotice,
  schedulePlatformNotice,
  updatePlatformNotice,
  type PlatformNoticeCreateInput,
} from '@/services/platform-notices';

afterEach(() => vi.clearAllMocks());

const payload: PlatformNoticeCreateInput = {
  title: 'Manutenção programada',
  body: 'A plataforma ficará indisponível por alguns minutos.',
  audience: {
    kind: 'workspaces',
    workspaceIds: ['11111111-1111-4111-8111-111111111111'],
    userIds: [],
    rule: null,
  },
  channels: ['in_app', 'email'],
  requiresAcknowledgement: true,
};

describe('platform notices transport', () => {
  it('uses the exact list, detail and real-preview contracts with bounded pagination', async () => {
    http.apiFetch.mockResolvedValue({ items: [], total: 0 });

    await listPlatformNotices({ offset: -20, limit: 900, status: 'scheduled' });
    await getPlatformNotice('notice/with slash');
    await previewPlatformNoticeRecipients('notice/with slash', 999);
    await listPlatformNoticeReceipts('notice/with slash', -1, 900);
    await listPlatformNoticeAudit('notice/with slash', -20, 2_000);

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/notices?offset=0&limit=200&status=scheduled',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/notices/notice%2Fwith%20slash',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      3,
      '/api/v1/platform/notices/notice%2Fwith%20slash/recipients/preview?sampleLimit=200',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      4,
      '/api/v1/platform/notices/notice%2Fwith%20slash/receipts?offset=0&limit=500',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      5,
      '/api/v1/platform/notices/notice%2Fwith%20slash/audit/page?offset=0&limit=200',
    );
  });

  it('sends camelCase draft data and optimistic version with explicit idempotency keys', async () => {
    http.apiFetch.mockResolvedValue({});

    await createPlatformNotice(payload, 'create-key-123');
    await updatePlatformNotice(
      '11111111-1111-4111-8111-111111111111',
      { ...payload, expectedVersion: 4 },
      'update-key-123',
    );

    expect(http.apiFetch).toHaveBeenNthCalledWith(1, '/api/v1/platform/notices', {
      method: 'POST',
      headers: { 'Idempotency-Key': 'create-key-123' },
      body: JSON.stringify(payload),
    });
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/notices/11111111-1111-4111-8111-111111111111',
      {
        method: 'PATCH',
        headers: { 'Idempotency-Key': 'update-key-123' },
        body: JSON.stringify({ ...payload, expectedVersion: 4 }),
      },
    );
  });

  it('maps schedule, publish, cancellation and due-dispatch actions without external-email payloads', async () => {
    http.apiFetch.mockResolvedValue({});
    const noticeId = '22222222-2222-4222-8222-222222222222';
    const scheduledAt = '2026-08-01T15:00:00.000Z';

    await schedulePlatformNotice(noticeId, scheduledAt, 'schedule-key-123');
    await publishPlatformNotice(noticeId, 'publish-key-123');
    await cancelPlatformNotice(noticeId, 'cancel-key-123');
    await dispatchDuePlatformNotices(900, 'dispatch-key-123');

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      `/api/v1/platform/notices/${noticeId}/schedule`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': 'schedule-key-123' },
        body: JSON.stringify({ scheduledAt }),
      },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      `/api/v1/platform/notices/${noticeId}/publish`,
      { method: 'POST', headers: { 'Idempotency-Key': 'publish-key-123' } },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      3,
      `/api/v1/platform/notices/${noticeId}/cancel`,
      { method: 'POST', headers: { 'Idempotency-Key': 'cancel-key-123' } },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      4,
      '/api/v1/platform/notices/dispatch-due?limit=500',
      { method: 'POST', headers: { 'Idempotency-Key': 'dispatch-key-123' } },
    );
  });

  it('generates retry-safe idempotency headers when the caller does not provide one', async () => {
    http.apiFetch.mockResolvedValue({});

    await createPlatformNotice(payload);

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/notices',
      expect.objectContaining({
        headers: { 'Idempotency-Key': expect.stringMatching(/^notice-create-.{8,}$/) },
      }),
    );
  });
});
