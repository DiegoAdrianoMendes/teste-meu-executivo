import { apiFetch } from '@/services/http';

export interface PlatformTeam {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  managerUserId: string | null;
  active: boolean;
  version: number;
}

export interface PlatformTeamPage {
  items: PlatformTeam[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

export interface PlatformStaffInvitation {
  id: string;
  email: string;
  name: string;
  rolePackage: string;
  status: string;
  expiresAt: string;
  sendCount: number;
  version: number;
  createdAt: string;
}

export interface PlatformStaffInvitationPage {
  items: PlatformStaffInvitation[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

export interface PlatformWorkspaceAssignment {
  id: string;
  workspaceId: string;
  assignmentType: 'onboarding' | 'customer_success' | 'billing' | 'support';
  assignedUserId: string | null;
  assignedTeamId: string | null;
  startsAt: string;
  endsAt: string | null;
  version: number;
}

export interface PlatformWorkspaceAssignmentPage {
  items: PlatformWorkspaceAssignment[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

export interface PlatformOnboardingMilestone {
  id: string;
  workspaceId: string;
  key: string;
  label: string;
  status: 'pending' | 'in_progress' | 'blocked' | 'completed' | 'cancelled';
  ownerUserId: string | null;
  dueAt: string | null;
  completedAt: string | null;
  version: number;
}

export interface PlatformOnboardingMilestonePage {
  items: PlatformOnboardingMilestone[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

export interface PlatformOperationalAssignment {
  id: string;
  sourceType: 'billing' | 'lead' | 'ticket' | 'publication' | 'configuration';
  sourceId: string;
  ownerUserId: string | null;
  teamId: string | null;
  nextAction: string | null;
  nextActionAt: string | null;
  dueAt: string | null;
  state: 'open' | 'in_progress' | 'waiting_external' | 'waiting_customer' | 'blocked' | 'done' | 'cancelled';
  version: number;
}

export interface PlatformOperationalAssignmentPage {
  items: PlatformOperationalAssignment[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

export interface PlatformReviewRequest {
  id: string;
  sourceType: 'publication' | 'configuration';
  sourceId: string;
  state: 'draft' | 'in_review' | 'changes_requested' | 'approved' | 'published' | 'publish_failed' | 'cancelled';
  authorUserId: string | null;
  reviewerUserId: string | null;
  reviewDueAt: string | null;
  reviewedAt: string | null;
  reason: string | null;
  version: number;
}

export interface PlatformReviewRequestPage {
  items: PlatformReviewRequest[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

export function fetchPlatformTeamsPage(input: {
  search?: string;
  active?: boolean;
  limit?: number;
  offset?: number;
} = {}): Promise<PlatformTeamPage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  if (input.search?.trim()) query.set('search', input.search.trim());
  if (input.active !== undefined) query.set('active', String(input.active));
  return apiFetch(`/api/v1/platform/organization/teams/page?${query.toString()}`);
}

export function createPlatformTeam(input: {
  name: string;
  slug: string;
  description: string | null;
  managerUserId: string | null;
  reason: string;
}): Promise<PlatformTeam> {
  return apiFetch('/api/v1/platform/organization/teams', {
    method: 'POST',
    body: JSON.stringify({ ...input, active: true }),
  });
}

export function addPlatformTeamMember(input: {
  teamId: string;
  userId: string;
  isPrimary: boolean;
  reason: string;
}): Promise<{ id: string }> {
  return apiFetch(`/api/v1/platform/organization/teams/${input.teamId}/members`, {
    method: 'POST',
    body: JSON.stringify({
      userId: input.userId,
      isPrimary: input.isPrimary,
      reason: input.reason,
    }),
  });
}

export function fetchPlatformStaffInvitationsPage(input: {
  search?: string;
  status?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<PlatformStaffInvitationPage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  if (input.search?.trim()) query.set('search', input.search.trim());
  if (input.status) query.set('status', input.status);
  return apiFetch(`/api/v1/platform/organization/staff-invitations/page?${query.toString()}`);
}

export function createPlatformStaffInvitation(input: {
  email: string;
  name: string;
  rolePackage: string;
  expiresInDays: number;
  reason: string;
}): Promise<PlatformStaffInvitation> {
  return apiFetch('/api/v1/platform/organization/staff-invitations', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function revokePlatformStaffInvitation(
  invitationId: string,
  reason: string,
): Promise<PlatformStaffInvitation> {
  return apiFetch(`/api/v1/platform/organization/staff-invitations/${invitationId}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export function fetchWorkspaceAssignmentsPage(
  workspaceId: string,
  input: { limit?: number; offset?: number } = {},
): Promise<PlatformWorkspaceAssignmentPage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 10),
    offset: String(input.offset ?? 0),
  });
  return apiFetch(`/api/v1/platform/organization/workspaces/${workspaceId}/assignments/page?${query}`);
}

export function saveWorkspaceAssignment(workspaceId: string, input: {
  assignmentType: PlatformWorkspaceAssignment['assignmentType'];
  assignedUserId: string | null;
  assignedTeamId: string | null;
  expectedVersion: number | null;
  reason: string;
}): Promise<PlatformWorkspaceAssignment> {
  return apiFetch(`/api/v1/platform/organization/workspaces/${workspaceId}/assignments`, {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function fetchWorkspaceMilestonesPage(
  workspaceId: string,
  input: { limit?: number; offset?: number } = {},
): Promise<PlatformOnboardingMilestonePage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 10),
    offset: String(input.offset ?? 0),
  });
  return apiFetch(`/api/v1/platform/organization/workspaces/${workspaceId}/milestones/page?${query}`);
}

export function saveWorkspaceMilestone(workspaceId: string, input: {
  key: string;
  label: string;
  status: PlatformOnboardingMilestone['status'];
  ownerUserId: string | null;
  dueAt: string | null;
  expectedVersion: number | null;
  reason: string;
}): Promise<PlatformOnboardingMilestone> {
  return apiFetch(`/api/v1/platform/organization/workspaces/${workspaceId}/milestones`, {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function fetchOperationalAssignmentsPage(input: {
  sourceType?: PlatformOperationalAssignment['sourceType'];
  state?: PlatformOperationalAssignment['state'];
  limit?: number;
  offset?: number;
} = {}): Promise<PlatformOperationalAssignmentPage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  if (input.sourceType) query.set('sourceType', input.sourceType);
  if (input.state) query.set('state', input.state);
  return apiFetch(`/api/v1/platform/organization/operational-assignments/page?${query.toString()}`);
}

export function saveOperationalAssignment(input: Omit<PlatformOperationalAssignment, 'id' | 'version'> & {
  expectedVersion: number | null;
  reason: string;
}): Promise<PlatformOperationalAssignment> {
  return apiFetch('/api/v1/platform/organization/operational-assignments', {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function fetchPlatformReviewsPage(input: {
  sourceType?: PlatformReviewRequest['sourceType'];
  state?: PlatformReviewRequest['state'];
  limit?: number;
  offset?: number;
} = {}): Promise<PlatformReviewRequestPage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  if (input.sourceType) query.set('sourceType', input.sourceType);
  if (input.state) query.set('state', input.state);
  return apiFetch(`/api/v1/platform/organization/reviews/page?${query.toString()}`);
}

export function savePlatformReview(input: {
  sourceType: PlatformReviewRequest['sourceType'];
  sourceId: string;
  state: PlatformReviewRequest['state'];
  reviewerUserId: string | null;
  reviewDueAt: string | null;
  expectedVersion: number | null;
  reason: string;
}): Promise<PlatformReviewRequest> {
  return apiFetch('/api/v1/platform/organization/reviews', {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}
