import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}));

vi.mock('@/services/http', () => ({
  API_BASE: '',
  apiBlob: vi.fn(),
  apiDownload: vi.fn(),
  apiFetch: http.apiFetch,
  apiUpload: vi.fn(),
}));

import { klmApi } from '@/klm/api';

afterEach(() => vi.clearAllMocks());

describe('meeting participant transport', () => {
  it('uses the versioned participant replacement endpoint and exact camelCase contract', async () => {
    http.apiFetch.mockResolvedValue({ id: 'meeting-1', version: 4, participants: [] });

    await klmApi.replaceMeetingParticipants('meeting-1', {
      expectedVersion: 3,
      participants: [
        {
          userId: 'user-organizer',
          role: 'organizer',
          rsvp: 'accepted',
          reminderMinutesBefore: 30,
        },
        {
          userId: 'user-observer',
          role: 'observer',
          rsvp: 'tentative',
          reminderMinutesBefore: null,
        },
      ],
      confirmParticipantConflicts: true,
      conflictReason: 'Sobreposição confirmada pelo cliente.',
    });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/meetings/meeting-1/participants',
      {
        method: 'PUT',
        body: JSON.stringify({
          expectedVersion: 3,
          participants: [
            {
              userId: 'user-organizer',
              role: 'organizer',
              rsvp: 'accepted',
              reminderMinutesBefore: 30,
            },
            {
              userId: 'user-observer',
              role: 'observer',
              rsvp: 'tentative',
              reminderMinutesBefore: null,
            },
          ],
          confirmParticipantConflicts: true,
          conflictReason: 'Sobreposição confirmada pelo cliente.',
        }),
      },
    );
  });
});
