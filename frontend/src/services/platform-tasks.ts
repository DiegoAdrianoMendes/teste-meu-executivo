import { ApiError, apiFetch } from '@/services/http';

export type PlatformTaskStatus = 'pending' | 'in_progress' | 'blocked' | 'done' | 'cancelled';
export type PlatformTaskPriority = 'P0' | 'P1' | 'P2' | 'P3';
export type PlatformTaskReviewState =
  | 'not_required'
  | 'draft'
  | 'pending'
  | 'approved'
  | 'changes_requested'
  | 'rejected';
export type PlatformTaskParticipantRole = 'assignee' | 'observer';
export type PlatformTaskParticipantState = 'assigned' | 'in_progress' | 'completed' | 'removed';
export type PlatformTaskScope = 'mine' | 'reviews' | 'team' | 'platform';
export type PlatformTaskCommand =
  | 'start'
  | 'block'
  | 'submit-for-review'
  | 'approve'
  | 'request-changes'
  | 'reject'
  | 'complete'
  | 'reopen'
  | 'cancel';

export interface PlatformTaskParticipant {
  id: string;
  userId: string;
  role: PlatformTaskParticipantRole;
  personalDueAt: string | null;
  state: PlatformTaskParticipantState;
  requiredForCompletion: boolean;
  labelSnapshot: string;
  active: boolean;
  removedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformTaskReviewRound {
  id: string;
  roundNumber: number;
  reviewerUserId: string;
  state: 'pending' | 'approved' | 'changes_requested' | 'rejected' | 'cancelled';
  reviewDueAt: string | null;
  submittedByUserId: string;
  submittedAt: string;
  taskVersionReviewed: number;
  resolvedByUserId: string | null;
  resolvedAt: string | null;
  decisionReason: string | null;
}

export interface PlatformTask {
  id: string;
  title: string;
  description: string | null;
  status: PlatformTaskStatus;
  priority: PlatformTaskPriority;
  dueAt: string | null;
  ownerDueAt: string | null;
  reviewDueAt: string | null;
  reviewRequired: boolean;
  reviewState: PlatformTaskReviewState;
  reviewRoundNumber: number;
  version: number;
  ownerUserId: string;
  reviewerUserId: string | null;
  createdByUserId: string;
  startedAt: string | null;
  completedAt: string | null;
  cancelledAt: string | null;
  cancellationReason: string | null;
  blockedReason: string | null;
  workspaceId: string | null;
  linkedTicketId: string | null;
  linkedIncidentId: string | null;
  linkedLeadId: string | null;
  linkedReleaseId: string | null;
  participants: PlatformTaskParticipant[];
  reviewRounds: PlatformTaskReviewRound[];
  allowedCommands?: PlatformTaskCommand[];
  createdAt: string;
  updatedAt: string;
}

export interface PlatformTaskPage {
  items: PlatformTask[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface PlatformTaskParticipantInput {
  userId: string;
  role: PlatformTaskParticipantRole;
  personalDueAt?: string | null;
  requiredForCompletion?: boolean;
}

export interface PlatformTaskCreateInput {
  title: string;
  description?: string | null;
  priority: PlatformTaskPriority;
  dueAt?: string | null;
  ownerUserId: string;
  ownerDueAt?: string | null;
  reviewerUserId?: string | null;
  reviewDueAt?: string | null;
  reviewRequired?: boolean;
  participants: PlatformTaskParticipantInput[];
  workspaceId?: string | null;
  linkedTicketId?: string | null;
  linkedIncidentId?: string | null;
  linkedLeadId?: string | null;
  linkedReleaseId?: string | null;
  reason: string;
}

export interface PlatformTaskUpdateInput {
  expectedVersion: number;
  title?: string;
  description?: string | null;
  priority?: PlatformTaskPriority;
  dueAt?: string | null;
  workspaceId?: string | null;
  linkedTicketId?: string | null;
  linkedIncidentId?: string | null;
  linkedLeadId?: string | null;
  linkedReleaseId?: string | null;
  reason: string;
}

export interface PlatformTaskAssignmentsInput {
  expectedVersion: number;
  ownerUserId: string;
  ownerDueAt?: string | null;
  reviewerUserId?: string | null;
  reviewDueAt?: string | null;
  participants: PlatformTaskParticipantInput[];
  reason: string;
}

export type PlatformTaskEventType =
  | 'created'
  | 'updated'
  | 'participants_changed'
  | 'started'
  | 'blocked'
  | 'review_submitted'
  | 'review_approved'
  | 'review_changes_requested'
  | 'review_rejected'
  | 'completed'
  | 'reopened'
  | 'cancelled'
  | 'comment_added';

export interface PlatformTaskEvent {
  id: string;
  taskId: string;
  actorUserId: string | null;
  actorLabelSnapshot: string;
  actorRoleSnapshot: string;
  isPlatformRoot: boolean;
  eventType: PlatformTaskEventType;
  beforeData: Record<string, unknown> | null;
  afterData: Record<string, unknown> | null;
  changedPaths: string[];
  comment: string | null;
  reason: string;
  correlationId: string;
  administrativeSessionId: string | null;
  sessionType: string;
  resourceVersionBefore: number | null;
  resourceVersionAfter: number | null;
  occurredAt: string;
}

export interface PlatformTaskEventPage {
  items: PlatformTaskEvent[];
  total: number;
}

function encodeId(value: string): string {
  return encodeURIComponent(value);
}

export function listPlatformTasks(input: {
  scope: PlatformTaskScope;
  statuses?: PlatformTaskStatus[];
  priorities?: PlatformTaskPriority[];
  workspaceId?: string | null;
  search?: string;
  overdue?: boolean;
  unassigned?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PlatformTaskPage> {
  const params = new URLSearchParams({
    scope: input.scope,
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  input.statuses?.forEach((value) => params.append('statuses', value));
  input.priorities?.forEach((value) => params.append('priorities', value));
  if (input.workspaceId) params.set('workspaceId', input.workspaceId);
  if (input.search?.trim()) params.set('search', input.search.trim());
  if (input.overdue != null) params.set('overdue', String(input.overdue));
  if (input.unassigned != null) params.set('unassigned', String(input.unassigned));
  return apiFetch<PlatformTaskPage>(`/api/v1/platform/tasks?${params.toString()}`);
}

export function getPlatformTask(taskId: string): Promise<PlatformTask> {
  return apiFetch<PlatformTask>(`/api/v1/platform/tasks/${encodeId(taskId)}`);
}

export function createPlatformTask(input: PlatformTaskCreateInput): Promise<PlatformTask> {
  return apiFetch<PlatformTask>('/api/v1/platform/tasks', {
    method: 'POST',
    headers: { 'Idempotency-Key': `platform-task:create:${crypto.randomUUID()}` },
    body: JSON.stringify(input),
  });
}

export function updatePlatformTask(taskId: string, input: PlatformTaskUpdateInput): Promise<PlatformTask> {
  return apiFetch<PlatformTask>(`/api/v1/platform/tasks/${encodeId(taskId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
}

export function replacePlatformTaskAssignments(
  taskId: string,
  input: PlatformTaskAssignmentsInput,
): Promise<PlatformTask> {
  return apiFetch<PlatformTask>(`/api/v1/platform/tasks/${encodeId(taskId)}/assignments`, {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function updatePlatformTaskParticipantState(
  taskId: string,
  userId: string,
  expectedVersion: number,
  state: Exclude<PlatformTaskParticipantState, 'removed'>,
  reason: string,
): Promise<PlatformTask> {
  return apiFetch<PlatformTask>(
    `/api/v1/platform/tasks/${encodeId(taskId)}/participants/${encodeId(userId)}/state`,
    {
      method: 'PUT',
      body: JSON.stringify({ expectedVersion, state, reason }),
    },
  );
}

export function executePlatformTaskCommand(
  taskId: string,
  command: PlatformTaskCommand,
  expectedVersion: number,
  input: { reason?: string; comment?: string } = {},
): Promise<PlatformTask> {
  return apiFetch<PlatformTask>(
    `/api/v1/platform/tasks/${encodeId(taskId)}/commands/${encodeURIComponent(command)}`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': `platform-task:${taskId}:${expectedVersion}:${command}` },
      body: JSON.stringify({ expectedVersion, ...input }),
    },
  );
}

export function addPlatformTaskComment(
  taskId: string,
  expectedVersion: number,
  comment: string,
): Promise<PlatformTask> {
  return apiFetch<PlatformTask>(`/api/v1/platform/tasks/${encodeId(taskId)}/comments`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `platform-task:comment:${crypto.randomUUID()}` },
    body: JSON.stringify({ expectedVersion, comment }),
  });
}

export function listPlatformTaskEvents(
  taskId: string,
  offset = 0,
  limit = 100,
): Promise<PlatformTaskEventPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return apiFetch<PlatformTaskEventPage>(
    `/api/v1/platform/tasks/${encodeId(taskId)}/events?${params.toString()}`,
  );
}

export type PlatformTaskUiErrorKind = 'forbidden' | 'conflict' | 'not_found' | 'unavailable' | 'error';

export function classifyPlatformTaskError(error: unknown): PlatformTaskUiErrorKind {
  if (!(error instanceof ApiError)) return 'error';
  if (error.status === 403) return 'forbidden';
  if (error.status === 404) return 'not_found';
  if (error.status === 409) return 'conflict';
  if (error.status === 408 || error.status === 429 || error.status >= 500) return 'unavailable';
  return 'error';
}

export function platformTaskErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
