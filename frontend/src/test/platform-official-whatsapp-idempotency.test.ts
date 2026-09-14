import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  platformOfficialWhatsAppUncertainOperationId,
  testPlatformOfficialWhatsApp,
} from '@/services/platform';

const storageKey = 'meuexecutivo:official-whatsapp-test-idempotency:v1';

afterEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.removeItem(storageKey);
});

function idempotencyKey(callIndex: number): string | null {
  return new Headers(http.apiFetch.mock.calls[callIndex][1]?.headers).get('Idempotency-Key');
}

describe('Blackbox official WhatsApp idempotency', () => {
  it('retains one key after an ambiguous failure and rotates only after a resolved result', async () => {
    http.apiFetch
      .mockRejectedValueOnce(new TypeError('network interrupted'))
      .mockResolvedValueOnce({ success: true, message: 'sent' })
      .mockResolvedValueOnce({ success: true, message: 'sent again' });
    const payload = { to: '+5511999990000', body: 'Mensagem de teste' };

    await expect(
      testPlatformOfficialWhatsApp('notifications', payload),
    ).rejects.toThrow('network interrupted');
    await testPlatformOfficialWhatsApp('notifications', payload);
    await testPlatformOfficialWhatsApp('notifications', payload);

    expect(idempotencyKey(0)).toMatch(/^official-wa-test:/);
    expect(idempotencyKey(1)).toBe(idempotencyKey(0));
    expect(idempotencyKey(2)).not.toBe(idempotencyKey(1));
  });

  it('recovers the pending key after the page module is reloaded', async () => {
    http.apiFetch
      .mockRejectedValueOnce(new TypeError('network interrupted'))
      .mockResolvedValueOnce({ success: true, message: 'sent' });
    const payload = { to: '+5511999990001', body: 'Persistida na sessão' };

    await expect(
      testPlatformOfficialWhatsApp('outbound', payload),
    ).rejects.toThrow('network interrupted');
    const firstKey = idempotencyKey(0);

    vi.resetModules();
    const reloaded = await import('@/services/platform');
    await reloaded.testPlatformOfficialWhatsApp('outbound', payload);

    expect(idempotencyKey(1)).toBe(firstKey);
  });

  it('identifies only the explicit uncertain-delivery contract for the UI', () => {
    expect(platformOfficialWhatsAppUncertainOperationId({
      status: 409,
      detail: {
        code: 'PLATFORM-WA-DELIVERY-UNCERTAIN',
        operationId: 'operation-1',
      },
    })).toBe('operation-1');
    expect(platformOfficialWhatsAppUncertainOperationId({
      status: 409,
      detail: { code: 'PLATFORM-WA-IDEMPOTENCY-CONFLICT' },
    })).toBeNull();
  });
});
