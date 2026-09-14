/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  klmApi,
  type KlmSupportTicket,
  type KlmSupportTicketCreate,
} from '@/klm/api';
import { submitSupportTicket } from '@/klm/support-offline-queue';

const payload: KlmSupportTicketCreate = {
  ticketType: 'problem',
  subject: 'Falha ao salvar',
  description: 'O formulário não conclui o salvamento.',
  priority: 'normal',
  severity: 'medium',
  technicalContext: {
    page: '/app/conversas',
    action: 'support_center_submission',
    frontendVersion: '1.0.0',
    browser: 'Chrome 126',
    device: 'Computador',
    viewport: '1440x900 @2x',
    occurredAt: '2026-07-24T15:00:00.000Z',
    realtimeStatus: 'connected',
    failedRequestIds: [],
  },
};

const ticket = {
  id: 'ticket-1',
  publicNumber: 'SAC-2026-0001',
} as KlmSupportTicket;
const scope = { workspaceId: 'workspace-1', userId: 'user-1' };

afterEach(() => {
  vi.restoreAllMocks();
});

describe('support offline queue submission boundary', () => {
  it('keeps a backend-confirmed ticket confirmed when an attachment is rejected', async () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
    const create = vi.spyOn(klmApi, 'createSupportTicket').mockResolvedValue(ticket);
    const upload = vi.spyOn(klmApi, 'uploadSupportAttachment').mockRejectedValue(
      new Error('O anexo não foi armazenado (422).'),
    );
    const file = new File(['evidence'], 'evidencia.txt', {
      type: 'text/plain',
      lastModified: 1,
    });

    const result = await submitSupportTicket(scope, payload, [file]);

    expect(result).toMatchObject({
      state: 'confirmed',
      record: ticket,
      pendingAttachments: 0,
      failedAttachments: 1,
    });
    expect(create.mock.calls[0][1]).toMatch(/^support-ticket-create:/);
    expect(upload.mock.calls[0][3]).toMatch(/^support-attachment:/);
  });

  it('confirms the complete submission only after ticket and attachment responses', async () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
    vi.spyOn(klmApi, 'createSupportTicket').mockResolvedValue(ticket);
    vi.spyOn(klmApi, 'uploadSupportAttachment').mockResolvedValue({
      id: 'attachment-1',
      messageId: null,
      storageObjectVersionId: 'version-1',
      originalFilename: 'evidencia.txt',
      contentType: 'text/plain',
      byteSize: 8,
      state: 'quarantined',
      avStatus: 'pending',
      createdAt: '2026-07-24T15:05:00.000Z',
    });
    const file = new File(['evidence'], 'evidencia.txt', { type: 'text/plain' });

    await expect(submitSupportTicket(scope, payload, [file])).resolves.toMatchObject({
      state: 'confirmed',
      pendingAttachments: 0,
      failedAttachments: 0,
    });
  });
});
