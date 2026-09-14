/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearClientSession, saveToken } from '@/services/auth';
import { ApiError, ApiRequestTimeoutError, apiFetch } from '@/services/http';
import {
  clearSupportModeCredential,
  completeSupportModeEnding,
} from '@/support-mode/session';

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('HTTP performance safeguards', () => {
  beforeEach(() => {
    clearSupportModeCredential();
    completeSupportModeEnding();
    localStorage.clear();
    sessionStorage.clear();
    clearClientSession();
    saveToken('access-token');
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    clearSupportModeCredential();
    completeSupportModeEnding();
    localStorage.clear();
    sessionStorage.clear();
    clearClientSession();
  });

  it('retries transient GET responses with exponential backoff', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'temporariamente indisponível' }, 503))
      .mockResolvedValueOnce(jsonResponse({ detail: 'gateway indisponível' }, 502))
      .mockResolvedValueOnce(jsonResponse({ ready: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch<{ ready: boolean }>('/api/v1/status');
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(249);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(499);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);

    await expect(request).resolves.toEqual({ ready: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('retries transient transport failures only for an idempotent request', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse({ ready: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch<{ ready: boolean }>('/api/v1/status');
    await vi.advanceTimersByTimeAsync(250);

    await expect(request).resolves.toEqual({ ready: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['a non-transient client error', 'GET', 400],
    ['a transient response from a non-idempotent request', 'POST', 503],
  ])('does not retry %s', async (_label, method, status) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'falha final' }, status));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/api/v1/actions', { method })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('aborts a non-idempotent request when its attempt reaches the timeout', async () => {
    vi.useFakeTimers();
    let forwardedSignal: AbortSignal | null = null;
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      forwardedSignal = init?.signal ?? null;
      return new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch('/api/v1/actions', { method: 'POST' });
    const rejection = expect(request).rejects.toBeInstanceOf(ApiRequestTimeoutError);
    await vi.advanceTimersByTimeAsync(14_999);
    expect(forwardedSignal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);

    await rejection;
    expect(forwardedSignal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('preserves caller cancellation during backoff and never starts another attempt', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const cancellation = new DOMException('cancelada pelo chamador', 'AbortError');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'indisponível' }, 503));
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch('/api/v1/status', { signal: controller.signal });
    const rejection = expect(request).rejects.toBe(cancellation);
    await vi.advanceTimersByTimeAsync(0);

    controller.abort(cancellation);
    await rejection;
    await vi.advanceTimersByTimeAsync(1_000);

    expect(controller.signal.aborted).toBe(true);
    expect(controller.signal.reason).toBe(cancellation);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('propagates caller cancellation to an in-flight fetch attempt', async () => {
    const controller = new AbortController();
    const cancellation = new DOMException('cancelada pelo chamador', 'AbortError');
    let forwardedSignal: AbortSignal | null = null;
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      forwardedSignal = init?.signal ?? null;
      return new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const request = apiFetch('/api/v1/status', { signal: controller.signal });
    const rejection = expect(request).rejects.toBe(cancellation);
    await Promise.resolve();
    controller.abort(cancellation);

    await rejection;
    expect(forwardedSignal?.aborted).toBe(true);
    expect(forwardedSignal?.reason).toBe(cancellation);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
