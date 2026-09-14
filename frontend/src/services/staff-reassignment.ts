import { apiFetch } from '@/services/http';

export type StaffReassignmentSurface = 'workspace' | 'platform';
export type StaffReassignmentMode = 'temporary' | 'permanent';
export type StaffReassignmentStatus =
  | 'applied'
  | 'restored'
  | 'partially_restored'
  | 'cancelled';

export type WorkspaceStaffReassignmentScope =
  | 'matters'
  | 'tasks'
  | 'meetings'
  | 'booking_links'
  | 'omnichannel_threads'
  | 'whatsapp_contacts'
  | 'social_conversations';

export type PlatformStaffReassignmentScope =
  | 'support_tickets'
  | 'growth_prospects';

export type StaffReassignmentScope =
  | WorkspaceStaffReassignmentScope
  | PlatformStaffReassignmentScope;

export interface StaffReassignmentPreviewInput {
  sourceUserId: string;
  targetUserId: string;
  scopes: StaffReassignmentScope[];
}

export interface StaffReassignmentCreateInput extends StaffReassignmentPreviewInput {
  mode: StaffReassignmentMode;
  reason: string;
  startsAt: string;
  endsAt?: string | null;
}

export interface StaffReassignmentRestoreInput {
  expectedVersion: number;
  reason: string;
}

export interface StaffReassignmentPreview {
  sourceUserId: string;
  sourceName: string;
  targetUserId: string;
  targetName: string;
  counts: Record<string, number>;
  total: number;
}

export interface StaffReassignmentPlan {
  id: string;
  surface: StaffReassignmentSurface;
  workspaceId?: string | null;
  sourceUserId: string;
  sourceName: string;
  targetUserId: string;
  targetName: string;
  mode: StaffReassignmentMode;
  status: StaffReassignmentStatus;
  reason: string;
  tagLabel: string;
  scopes: StaffReassignmentScope[];
  assignmentSummary: Record<string, number>;
  conflictCount: number;
  startsAt: string;
  endsAt?: string | null;
  appliedAt: string;
  restoredAt?: string | null;
  restoreReason?: string | null;
  requestedByUserId: string;
  approvedByUserId: string;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface StaffReassignmentPlanPage {
  items: StaffReassignmentPlan[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

/**
 * Contrato único consumido pelas superfícies isoladas:
 * - workspace: /api/v1/admin/staff-reassignments
 * - plataforma: /api/v1/platform/staff-reassignments
 *
 * Os sufixos são deliberadamente iguais para impedir que a UI mantenha duas
 * regras de continuidade diferentes.
 */
export const STAFF_REASSIGNMENT_ROUTES = {
  workspace: '/api/v1/admin/staff-reassignments',
  platform: '/api/v1/platform/staff-reassignments',
} as const satisfies Record<StaffReassignmentSurface, string>;

function rootFor(surface: StaffReassignmentSurface): string {
  return STAFF_REASSIGNMENT_ROUTES[surface];
}

export function createStaffReassignmentIdempotencyKey(): string {
  const randomPart = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `staff-reassignment-${randomPart}`;
}

export function previewStaffReassignment(
  surface: StaffReassignmentSurface,
  input: StaffReassignmentPreviewInput,
  signal?: AbortSignal,
): Promise<StaffReassignmentPreview> {
  return apiFetch(`${rootFor(surface)}/preview`, {
    method: 'POST',
    body: JSON.stringify(input),
    signal,
  });
}

export function createStaffReassignment(
  surface: StaffReassignmentSurface,
  input: StaffReassignmentCreateInput,
  idempotencyKey = createStaffReassignmentIdempotencyKey(),
): Promise<StaffReassignmentPlan> {
  return apiFetch(rootFor(surface), {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(input),
  });
}

export function listStaffReassignments(
  surface: StaffReassignmentSurface,
  signal?: AbortSignal,
): Promise<StaffReassignmentPlan[]> {
  return apiFetch(rootFor(surface), { signal });
}

export function listStaffReassignmentsPage(
  surface: StaffReassignmentSurface,
  limit = 10,
  offset = 0,
  signal?: AbortSignal,
): Promise<StaffReassignmentPlanPage> {
  return apiFetch(`${rootFor(surface)}/page?limit=${limit}&offset=${offset}`, { signal });
}

export function getStaffReassignment(
  surface: StaffReassignmentSurface,
  planId: string,
  signal?: AbortSignal,
): Promise<StaffReassignmentPlan> {
  return apiFetch(`${rootFor(surface)}/${encodeURIComponent(planId)}`, { signal });
}

export function restoreStaffReassignment(
  surface: StaffReassignmentSurface,
  planId: string,
  input: StaffReassignmentRestoreInput,
  idempotencyKey = createStaffReassignmentIdempotencyKey(),
): Promise<StaffReassignmentPlan> {
  return apiFetch(`${rootFor(surface)}/${encodeURIComponent(planId)}/restore`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(input),
  });
}
