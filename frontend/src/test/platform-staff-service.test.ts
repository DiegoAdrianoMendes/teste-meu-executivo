import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import { fetchPlatformStaffPage } from '@/services/platform';

beforeEach(() => {
  http.apiFetch.mockReset();
});

describe('platform staff pagination service', () => {
  it('sends search, limit and offset to the server-side page contract', () => {
    fetchPlatformStaffPage({ search: '  suporte interno  ', limit: 25, offset: 50 });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/staff/page?limit=25&offset=50&search=suporte+interno',
    );
  });

  it('uses the operational page defaults without a fake client-side total', () => {
    fetchPlatformStaffPage();

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/staff/page?limit=50&offset=0',
    );
  });
});
