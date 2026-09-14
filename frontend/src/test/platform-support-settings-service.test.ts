// @vitest-environment node

import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  type PlatformSupportEscalationDraft,
  updatePlatformSupportAvailabilitySettings,
  updatePlatformSupportEscalationSettings,
} from '@/services/platform-support-settings';

afterEach(() => vi.clearAllMocks());

describe('platform support settings administrative transport', () => {
  it('transports an encoded audit reason for both configuration commands', async () => {
    http.apiFetch.mockResolvedValue({});
    const reason = 'Mudança operacional aprovada & registrada.';
    const availability = {
      enabled: true,
      timezone: 'America/Sao_Paulo',
      days: [],
      offlineMessage: 'Retornaremos no próximo período.',
    };
    const escalation: PlatformSupportEscalationDraft = {
      enabled: true,
      schedulingUrl: 'https://cal.example.test/suporte',
      whatsappNumber: null,
      businessDays: ['monday'],
      businessHoursStart: '09:00',
      businessHoursEnd: '18:00',
    };

    await updatePlatformSupportAvailabilitySettings(availability, 'etag-1', reason);
    await updatePlatformSupportEscalationSettings(escalation, 'etag-2', reason);

    const headers = {
      'X-Operation-Reason':
        'Mudan%C3%A7a%20operacional%20aprovada%20%26%20registrada.',
    };
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/support/availability-settings',
      {
        method: 'PUT',
        headers,
        body: JSON.stringify({ ...availability, expectedEtag: 'etag-1' }),
      },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/support/escalation-settings',
      {
        method: 'PUT',
        headers,
        body: JSON.stringify({ ...escalation, expectedEtag: 'etag-2' }),
      },
    );
  });
});
