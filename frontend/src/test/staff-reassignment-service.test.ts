import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  STAFF_REASSIGNMENT_ROUTES,
  createStaffReassignment,
  getStaffReassignment,
  listStaffReassignmentsPage,
  previewStaffReassignment,
  restoreStaffReassignment,
} from '@/services/staff-reassignment';

const previewInput = {
  sourceUserId: '00000000-0000-0000-0000-000000000001',
  targetUserId: '00000000-0000-0000-0000-000000000002',
  scopes: ['support_tickets'] as const,
};

beforeEach(() => {
  http.apiFetch.mockReset();
});

describe('staff-reassignment service', () => {
  it('keeps workspace and platform on physically distinct API roots', () => {
    expect(STAFF_REASSIGNMENT_ROUTES).toEqual({
      workspace: '/api/v1/admin/staff-reassignments',
      platform: '/api/v1/platform/staff-reassignments',
    });

    listStaffReassignmentsPage('workspace', 10, 20);
    listStaffReassignmentsPage('platform', 25, 0);

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/admin/staff-reassignments/page?limit=10&offset=20',
      { signal: undefined },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/staff-reassignments/page?limit=25&offset=0',
      { signal: undefined },
    );
  });

  it('uses preview without an idempotency header because it has no side effect', () => {
    previewStaffReassignment('platform', {
      ...previewInput,
      scopes: [...previewInput.scopes],
    });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/staff-reassignments/preview',
      {
        method: 'POST',
        body: JSON.stringify(previewInput),
        signal: undefined,
      },
    );
  });

  it('protects application and restoration with caller-stable idempotency keys', () => {
    createStaffReassignment(
      'platform',
      {
        ...previewInput,
        scopes: [...previewInput.scopes],
        mode: 'temporary',
        reason: 'Férias: período aprovado.',
        startsAt: '2026-07-24T12:00:00.000Z',
        endsAt: '2026-07-31T12:00:00.000Z',
      },
      'apply-key-1',
    );

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/staff-reassignments',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Idempotency-Key': 'apply-key-1' },
      }),
    );

    restoreStaffReassignment(
      'platform',
      'plan/with unsafe chars',
      { expectedVersion: 2, reason: 'Retorno confirmado.' },
      'restore-key-1',
    );

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/staff-reassignments/plan%2Fwith%20unsafe%20chars/restore',
      {
        method: 'POST',
        headers: { 'Idempotency-Key': 'restore-key-1' },
        body: JSON.stringify({ expectedVersion: 2, reason: 'Retorno confirmado.' }),
      },
    );
  });

  it('encodes plan identifiers when reading history details', () => {
    const controller = new AbortController();
    getStaffReassignment('workspace', 'plan/1', controller.signal);

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/admin/staff-reassignments/plan%2F1',
      { signal: controller.signal },
    );
  });
});
