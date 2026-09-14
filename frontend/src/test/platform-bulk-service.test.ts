/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearClientSession, saveAdministrativeSessionToken } from '@/services/auth';
import {
  createPlatformBulkRetryPreview,
  createPlatformBulkRestorationPreview,
  restorePlatformBulkJob,
} from '@/services/platform';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('platform bulk retry and restoration contracts', () => {
  beforeEach(() => {
    saveAdministrativeSessionToken('platform-token');
  });

  afterEach(() => {
    clearClientSession();
    vi.unstubAllGlobals();
  });

  it('requests a fresh preview for failed items instead of replaying the job', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 'retry-preview' }));
    vi.stubGlobal('fetch', fetchMock);

    await createPlatformBulkRetryPreview('job/unsafe');

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      '/operations/bulk/jobs/job%2Funsafe/retry-failures',
    );
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST');
  });

  it('requests restoration preview separately from execution', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 'restore-preview' }));
    vi.stubGlobal('fetch', fetchMock);

    await createPlatformBulkRestorationPreview('job-1');

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      '/operations/bulk/jobs/job-1/restoration-preview',
    );
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST');
  });

  it('executes restoration with reason, confirmation and idempotency', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 'restore-job' }));
    vi.stubGlobal('fetch', fetchMock);

    await restorePlatformBulkJob({
      jobId: 'job-1',
      previewId: 'preview-1',
      reason: 'Restaurar estado validado pela operação',
      confirmationPhrase: 'RESTAURAR 1',
      idempotencyKey: 'restore-job-1',
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/operations/bulk/jobs/job-1/restore');
    expect(init.method).toBe('POST');
    expect(new Headers(init.headers).get('Idempotency-Key')).toBe('restore-job-1');
    expect(JSON.parse(String(init.body))).toEqual({
      previewId: 'preview-1',
      reason: 'Restaurar estado validado pela operação',
      confirmationPhrase: 'RESTAURAR 1',
    });
  });
});
