import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  sendConversationContract,
  sendConversationMedia,
  sendWhatsAppMessage,
} from '@/services/whatsapp';

afterEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.removeItem('meuexecutivo:whatsapp-outbound-idempotency:v1');
});

function idempotencyKey(callIndex: number): string | null {
  return new Headers(http.apiFetch.mock.calls[callIndex][1]?.headers).get('Idempotency-Key');
}

describe('WhatsApp outbound idempotency', () => {
  it('retains one key after an ambiguous failure and rotates it after success', async () => {
    http.apiFetch
      .mockRejectedValueOnce(new TypeError('network interrupted'))
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(undefined);

    await expect(
      sendWhatsAppMessage('contact-retry', 'Mensagem única', 'connection-1'),
    ).rejects.toThrow('network interrupted');
    await sendWhatsAppMessage('contact-retry', 'Mensagem única', 'connection-1');
    await sendWhatsAppMessage('contact-retry', 'Mensagem única', 'connection-1');

    expect(idempotencyKey(0)).toMatch(/^whatsapp-message:/);
    expect(idempotencyKey(1)).toBe(idempotencyKey(0));
    expect(idempotencyKey(2)).not.toBe(idempotencyKey(1));
  });

  it('sends required stable headers for private media and contract intents', async () => {
    http.apiFetch.mockResolvedValue({
      contact: { id: 'contact-output', name: 'Cliente', phone: '+5511999999999' },
      messages: [],
    });

    await sendConversationMedia('contact-output', {
      connectionId: 'connection-1',
      documentId: 'document-1',
      filename: 'contrato.pdf',
    });
    await sendConversationContract('contact-output', {
      connectionId: 'connection-1',
      templateId: 'template-1',
    });

    expect(idempotencyKey(0)).toMatch(/^whatsapp-media:/);
    expect(idempotencyKey(1)).toMatch(/^whatsapp-contract:/);
  });

  it('keeps the same key after an HTTP success with uncertain delivery', async () => {
    http.apiFetch
      .mockResolvedValueOnce({ status: 'delivery_uncertain' })
      .mockResolvedValueOnce({ status: 'sent' })
      .mockResolvedValueOnce({ status: 'sent' });

    await sendWhatsAppMessage('contact-uncertain', 'Mensagem única', 'connection-1');
    await sendWhatsAppMessage('contact-uncertain', 'Mensagem única', 'connection-1');
    await sendWhatsAppMessage('contact-uncertain', 'Mensagem única', 'connection-1');

    expect(idempotencyKey(1)).toBe(idempotencyKey(0));
    expect(idempotencyKey(2)).not.toBe(idempotencyKey(1));
  });

  it('reuses the pending key after the page module is reloaded', async () => {
    http.apiFetch
      .mockRejectedValueOnce(new TypeError('network interrupted'))
      .mockResolvedValueOnce(undefined);

    await expect(
      sendWhatsAppMessage('contact-reload', 'Mensagem persistida', 'connection-1'),
    ).rejects.toThrow('network interrupted');
    const firstKey = idempotencyKey(0);

    vi.resetModules();
    const reloaded = await import('@/services/whatsapp');
    await reloaded.sendWhatsAppMessage(
      'contact-reload',
      'Mensagem persistida',
      'connection-1',
    );

    expect(idempotencyKey(1)).toBe(firstKey);
  });
});
