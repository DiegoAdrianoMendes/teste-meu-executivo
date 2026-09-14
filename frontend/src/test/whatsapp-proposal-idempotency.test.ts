import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import { sendConversationProposal } from '@/services/whatsapp';

afterEach(() => vi.clearAllMocks());

describe('WhatsApp proposal idempotency contract', () => {
  it('forwards the stable attempt key in the exact Idempotency-Key header', async () => {
    http.apiFetch.mockResolvedValue({
      contact: { id: 'contact-1', name: 'Cliente', phone: '+5511999999999' },
      messages: [],
    });
    const payload = {
      connectionId: 'connection-1',
      templateId: 'template-1',
      format: 'pdf',
      customMessage: 'Segue a proposta.',
    };

    await sendConversationProposal('contact-1', payload, 'conversation-proposal:attempt-1');

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/whatsapp/conversations/contact-1/send-proposal',
      {
        method: 'POST',
        headers: { 'Idempotency-Key': 'conversation-proposal:attempt-1' },
        body: JSON.stringify(payload),
      },
    );
  });
});
