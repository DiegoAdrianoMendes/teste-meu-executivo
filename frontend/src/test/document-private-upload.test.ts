/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({
  API_BASE: '',
  apiFetch: http.apiFetch,
}));

import { klmApi, sha256File } from '@/klm/api';

function fileWithBytes(contents: string, type = 'application/pdf') {
  const bytes = new TextEncoder().encode(contents);
  const file = new File([bytes], 'prova.pdf', { type });
  Object.defineProperty(file, 'arrayBuffer', {
    value: async () => bytes.buffer,
  });
  return file;
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('private document upload contract', () => {
  it('calculates the SHA-256 sent when requesting an upload authorization', async () => {
    const file = fileWithBytes('abc');

    const checksumSha256 = await sha256File(file);
    await klmApi.createDocumentUpload('matter-1', {
      name: file.name,
      mimeType: file.type,
      fileSize: String(file.size),
      checksumSha256,
    }, 'matter-document:test-retry');

    expect(checksumSha256).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/matters/matter-1/documents', {
      method: 'POST',
      headers: { 'Idempotency-Key': 'matter-document:test-retry' },
      body: JSON.stringify({
        name: 'prova.pdf',
        mimeType: 'application/pdf',
        fileSize: '3',
        checksumSha256,
      }),
    });
  });

  it('reapplies exactly the signed upload headers without adding or replacing one', async () => {
    const upload = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', upload);
    const file = fileWithBytes('conteudo');

    await klmApi.uploadDocumentBytes('https://storage.example/signed', file, {
      'Content-Type': 'application/signed-pdf',
      'x-amz-server-side-encryption': 'aws:kms',
      'x-amz-meta-sha256': 'digest',
    });

    expect(String(upload.mock.calls[0][0])).toBe('https://storage.example/signed');
    expect(upload.mock.calls[0][1]).toEqual({
      method: 'PUT',
      credentials: 'omit',
      headers: {
        'Content-Type': 'application/signed-pdf',
        'x-amz-server-side-encryption': 'aws:kms',
        'x-amz-meta-sha256': 'digest',
      },
      body: file,
    });
  });

  it('uses the file content type only for the local compatibility path without signed headers', async () => {
    const upload = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', upload);
    const file = fileWithBytes('conteudo');

    await klmApi.uploadDocumentBytes('/api/v1/local-storage/upload?ticket=local', file, {});

    expect(String(upload.mock.calls[0][0])).toBe('http://localhost:3000/api/v1/local-storage/upload?ticket=local');
    expect(upload.mock.calls[0][1]).toEqual({
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/pdf' },
      body: file,
    });
  });

  it('does not continue after a rejected private upload', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }));

    await expect(
      klmApi.uploadDocumentBytes('https://storage.example/expired', fileWithBytes('abc')),
    ).rejects.toThrow('Falha no armazenamento privado (403).');
  });

  it('does not repeat PUT or confirmation when document authorization is an idempotent replay', async () => {
    const upload = vi.fn();
    vi.stubGlobal('fetch', upload);
    const existing = {
      id: 'document-1',
      name: 'prova.pdf',
      type: 'documento',
      status: 'completo',
      createdAt: '2026-07-30T14:59:00Z',
    };
    http.apiFetch.mockResolvedValueOnce([existing]);

    await expect(klmApi.completeDocumentUpload('matter-1', {
      documentId: 'document-1',
      s3Key: 'private/document-1',
      uploadUrl: null,
      uploadRequired: false,
      uploadHeaders: {},
      expiresIn: 300,
    }, fileWithBytes('abc'))).resolves.toEqual(existing);

    expect(upload).not.toHaveBeenCalled();
    expect(http.apiFetch).toHaveBeenCalledTimes(1);
    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/matters/matter-1/documents');
    expect(http.apiFetch).not.toHaveBeenCalledWith(
      '/api/v1/documents/document-1/confirm',
      expect.anything(),
    );
  });

  it('uses the exact signed headers for support attachments too', async () => {
    const upload = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', upload);
    http.apiFetch
      .mockResolvedValueOnce({
        attachmentId: 'attachment-1',
        storageObjectVersionId: 'version-1',
        uploadUrl: 'https://storage.example/signed-support',
        uploadRequired: true,
        uploadHeaders: {
          'Content-Type': 'application/pdf',
          'x-amz-meta-storage-state': 'quarantine',
          'x-amz-meta-sha256': 'digest',
          'x-amz-server-side-encryption': 'aws:kms',
          'x-amz-server-side-encryption-aws-kms-key-id': 'kms-key',
        },
        expiresAt: '2026-07-30T15:00:00Z',
      })
      .mockResolvedValueOnce({
        id: 'attachment-1',
        messageId: null,
        storageObjectVersionId: 'version-1',
        originalFilename: 'prova.pdf',
        contentType: 'application/pdf',
        byteSize: 3,
        state: 'quarantined',
        avStatus: 'pending',
        createdAt: '2026-07-30T14:59:00Z',
      });
    const file = fileWithBytes('abc');

    await klmApi.uploadSupportAttachment('ticket-1', file);

    expect(String(upload.mock.calls[0][0])).toBe('https://storage.example/signed-support');
    expect(upload.mock.calls[0][1]).toEqual({
      method: 'PUT',
      credentials: 'omit',
      headers: {
        'Content-Type': 'application/pdf',
        'x-amz-meta-storage-state': 'quarantine',
        'x-amz-meta-sha256': 'digest',
        'x-amz-server-side-encryption': 'aws:kms',
        'x-amz-server-side-encryption-aws-kms-key-id': 'kms-key',
      },
      body: file,
    });
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/support/tickets/ticket-1/attachments/attachment-1/confirm',
      { method: 'POST' },
    );
  });

  it('does not repeat PUT or confirmation when support authorization is an idempotent replay', async () => {
    const upload = vi.fn();
    vi.stubGlobal('fetch', upload);
    const existing = {
      id: 'attachment-1',
      messageId: null,
      storageObjectVersionId: 'version-1',
      originalFilename: 'prova.pdf',
      contentType: 'application/pdf',
      byteSize: 3,
      state: 'quarantined',
      avStatus: 'pending',
      createdAt: '2026-07-30T14:59:00Z',
    };
    http.apiFetch
      .mockResolvedValueOnce({
        attachmentId: 'attachment-1',
        storageObjectVersionId: 'version-1',
        uploadUrl: null,
        uploadRequired: false,
        uploadHeaders: {},
        expiresAt: '2026-07-30T15:00:00Z',
      })
      .mockResolvedValueOnce({ attachments: [existing] });

    await expect(
      klmApi.uploadSupportAttachment(
        'ticket-1',
        fileWithBytes('abc'),
        undefined,
        'support-attachment:test-retry',
      ),
    ).resolves.toEqual(existing);

    expect(upload).not.toHaveBeenCalled();
    expect(http.apiFetch).toHaveBeenCalledTimes(2);
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/support/tickets/ticket-1/attachments',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Idempotency-Key': 'support-attachment:test-retry' },
      }),
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(2, '/api/v1/support/tickets/ticket-1');
  });
});
