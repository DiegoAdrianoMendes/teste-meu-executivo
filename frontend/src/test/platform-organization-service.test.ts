import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  fetchPlatformStaffInvitationsPage,
  fetchWorkspaceAssignmentsPage,
  fetchWorkspaceMilestonesPage,
} from '@/services/platform-organization';

beforeEach(() => {
  http.apiFetch.mockReset();
});

describe('platform organization pagination service', () => {
  it('requests a filtered invitation page with a server-side total', () => {
    fetchPlatformStaffInvitationsPage({
      search: '  convite pendente  ',
      status: 'pending',
      limit: 5,
      offset: 10,
    });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/organization/staff-invitations/page?limit=5&offset=10&search=convite+pendente&status=pending',
    );
  });

  it('requests account responsibilities and milestones as real pages', () => {
    fetchWorkspaceAssignmentsPage('workspace-1', { limit: 5, offset: 10 });
    fetchWorkspaceMilestonesPage('workspace-1', { limit: 5, offset: 15 });

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/organization/workspaces/workspace-1/assignments/page?limit=5&offset=10',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/organization/workspaces/workspace-1/milestones/page?limit=5&offset=15',
    );
  });
});
