import { describe, expect, it } from 'vitest';

import {
  collectSupportTechnicalContext,
  sanitizeSupportFailureIds,
} from '@/klm/support-context';

describe('support technical context', () => {
  it('keeps only allowlisted diagnostic fields and removes route parameters', () => {
    const context = collectSupportTechnicalContext({
      route: '/app/conversas?client=Maria&token=secret#message',
      realtimeStatus: 'connected',
      failureIds: ['req_123', ' error:ABC-9 ', 'token=secret', 'conversa pessoal'],
      now: new Date('2026-07-24T15:30:00.000Z'),
      navigatorLike: {
        onLine: true,
        userAgent: 'Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36',
      },
      viewport: { width: 1440, height: 900, devicePixelRatio: 2 },
    });

    expect(context).toMatchObject({
      page: '/app/conversas',
      action: 'support_center_submission',
      browser: 'Chrome 126',
      device: 'Computador',
      viewport: '1440x900 @2x',
      occurredAt: '2026-07-24T15:30:00.000Z',
      realtimeStatus: 'connected',
      failedRequestIds: ['req_123', 'error:ABC-9'],
    });
    const serialized = JSON.stringify(context);
    expect(serialized).not.toContain('Maria');
    expect(serialized).not.toContain('token=secret');
    expect(serialized).not.toContain('conversa pessoal');
  });

  it('deduplicates and limits technical failure identifiers', () => {
    const values = Array.from({ length: 30 }, (_, index) => `request-${index}`);
    expect(sanitizeSupportFailureIds(['request-1', 'request-1', ...values])).toHaveLength(20);
  });

  it('redacts resource identifiers from dynamic route segments', () => {
    const context = collectSupportTechnicalContext({
      route: '/app/chamados/9d2cfde8-e113-4f1f-a822-4250999db1e2',
      now: new Date('2026-07-24T15:30:00.000Z'),
      navigatorLike: { onLine: false, userAgent: 'Mozilla/5.0 Firefox/127.0' },
      viewport: { width: 390, height: 844, devicePixelRatio: 3 },
    });

    expect(context.page).toBe('/app/chamados/:id');
    expect(context.realtimeStatus).toBe('offline');
    expect(context.device).toBe('Celular');
  });
});
