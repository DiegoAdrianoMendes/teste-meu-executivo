import { beforeEach, describe, expect, it, vi } from 'vitest';

const identity = vi.hoisted(() => ({ createIdempotencyKey: vi.fn() }));

vi.mock('@/services/identity', () => identity);

import { withMeetingCreateIdempotency } from '@/services/meeting-idempotency';

describe('meeting creation idempotency', () => {
  beforeEach(() => {
    identity.createIdempotencyKey.mockReset();
  });

  it('reuses one key after an uncertain response and consumes it after success', async () => {
    identity.createIdempotencyKey
      .mockReturnValueOnce('meeting-attempt-key-1')
      .mockReturnValueOnce('meeting-attempt-key-2');
    const request = vi.fn()
      .mockRejectedValueOnce(new Error('connection reset'))
      .mockResolvedValueOnce({ id: 'meeting-1' })
      .mockResolvedValueOnce({ id: 'meeting-2' });
    const payload = { title: 'Reunião', startsAt: '2026-08-10T13:00:00Z' };

    await expect(withMeetingCreateIdempotency(payload, request)).rejects.toThrow(
      'connection reset',
    );
    await expect(withMeetingCreateIdempotency(payload, request)).resolves.toEqual({
      id: 'meeting-1',
    });
    await expect(withMeetingCreateIdempotency(payload, request)).resolves.toEqual({
      id: 'meeting-2',
    });

    expect(request.mock.calls.map(([key]) => key)).toEqual([
      'meeting-attempt-key-1',
      'meeting-attempt-key-1',
      'meeting-attempt-key-2',
    ]);
  });

  it('canonicalizes object keys so an equivalent retry keeps its key', async () => {
    identity.createIdempotencyKey.mockReturnValue('meeting-canonical-key');
    const request = vi.fn().mockRejectedValue(new Error('offline'));

    await expect(
      withMeetingCreateIdempotency({ title: 'A', startsAt: 'B' }, request),
    ).rejects.toThrow('offline');
    await expect(
      withMeetingCreateIdempotency({ startsAt: 'B', title: 'A' }, request),
    ).rejects.toThrow('offline');

    expect(request).toHaveBeenNthCalledWith(1, 'meeting-canonical-key');
    expect(request).toHaveBeenNthCalledWith(2, 'meeting-canonical-key');
    expect(identity.createIdempotencyKey).toHaveBeenCalledTimes(1);
  });
});
