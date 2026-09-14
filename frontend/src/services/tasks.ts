import { apiFetch, ApiError } from '@/services/http';

export type TaskStatus = 'pending' | 'in_progress' | 'blocked' | 'done';
export type TaskPriority = 'alta' | 'media' | 'baixa';
export type TaskReviewState = 'not_required' | 'draft' | 'pending' | 'approved' | 'changes_requested';
export type TaskParticipantRole = 'owner' | 'assignee' | 'reviewer' | 'observer';
export type TaskParticipantState = 'assigned' | 'in_progress' | 'completed' | 'removed';
export type TaskView = 'mine' | 'assigned_by_me' | 'reviews' | 'team' | 'overdue' | 'blocked' | 'completed' | 'workspace';
export type TaskCommand =
  | 'start'
  | 'block'
  | 'unblock'
  | 'submit-for-review'
  | 'approve'
  | 'request-changes'
  | 'complete'
  | 'reopen'
  | 'cancel';

export interface CollaborativeTaskParticipant {
  id: string;
  userId: string;
  labelSnapshot: string;
  role: TaskParticipantRole;
  dueAt: string | null;
  state: TaskParticipantState;
  requiredForCompletion: boolean;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface TaskReviewRound {
  id: string;
  roundNumber: number;
  reviewerUserId: string;
  state: TaskReviewState;
  dueAt: string | null;
  submittedAt: string;
  submittedByUserId: string | null;
  resolvedAt: string | null;
  resolvedByUserId: string | null;
  decisionComment: string | null;
}

export interface CollaborativeTask {
  id: string;
  workspaceId: string;
  contextType: 'workspace' | 'client' | 'contract' | 'matter';
  origin: 'legacy' | 'manual' | 'administrative' | 'commercial' | 'publication' | 'process' | 'hearing' | 'deadline' | 'automation';
  matterId: string | null;
  clientId: string | null;
  contractId: string | null;
  originResourceId: string | null;
  originLabel: string | null;
  parentTaskId: string | null;
  title: string;
  description: string | null;
  status: TaskStatus;
  priority: TaskPriority | null;
  assignedTo: string | null;
  dueDate: string | null;
  dueAt: string | null;
  legacyDueText: string | null;
  reviewDueAt: string | null;
  reviewRequired: boolean;
  reviewState: TaskReviewState;
  reviewRound: number;
  version: number;
  createdByUserId: string | null;
  completedAt: string | null;
  cancelledAt: string | null;
  cancelledByUserId: string | null;
  cancellationReason: string | null;
  archivedAt?: string | null;
  archivedByUserId?: string | null;
  archiveReason?: string | null;
  participants: CollaborativeTaskParticipant[];
  reviewRounds: TaskReviewRound[];
  isAiGenerated: boolean;
  createdAt: string;
  sourceExceptionId: string | null;
  matterLabel?: string | null;
  clientLabel?: string | null;
  myRoles?: TaskParticipantRole[];
}

export type TaskEventType =
  | 'created'
  | 'updated'
  | 'participants_changed'
  | 'participant_state_changed'
  | 'started'
  | 'blocked'
  | 'unblocked'
  | 'review_submitted'
  | 'review_approved'
  | 'review_changes_requested'
  | 'completed'
  | 'reopened'
  | 'cancelled'
  | 'comment_added'
  | 'checklist_changed'
  | 'labels_changed'
  | 'dependency_changed'
  | 'attachment_changed'
  | 'organization_changed'
  | 'archived'
  | 'unarchived';

export interface TaskChecklistItem {
  id: string;
  label: string;
  position: number;
  completedAt: string | null;
  completedByUserId: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface TaskChecklist {
  id: string;
  taskId: string;
  title: string;
  position: number;
  version: number;
  items: TaskChecklistItem[];
  createdAt: string;
  updatedAt: string;
}

export interface TaskLabel {
  id: string;
  name: string;
  color: string;
}

export interface TaskLabelOption extends TaskLabel {
  handle: string;
}

export interface TaskDependency {
  id: string;
  predecessorTaskId: string;
  successorTaskId: string;
  createdAt: string;
}

export interface TaskDependencyCandidate {
  handle: string;
  label: string;
  status: TaskStatus;
  dueAt: string | null;
}

export interface TaskAttachment {
  id: string;
  taskEventId: string;
  documentId: string;
  nameSnapshot: string;
  mimeTypeSnapshot: string | null;
  createdAt: string;
}

export interface TaskMention {
  id: string;
  taskEventId: string;
  mentionedUserId: string;
  labelSnapshot: string;
  createdAt: string;
}

export interface TaskLegalResources {
  labels: TaskLabel[];
  subtasks: CollaborativeTask[];
  dependencies: TaskDependency[];
  attachments: TaskAttachment[];
  mentions: TaskMention[];
}

export interface TaskOrganizationList {
  id: string;
  name: string;
  description: string | null;
  position: number;
  version: number;
  archivedAt: string | null;
}

export interface TaskOrganizationColumn {
  id: string;
  boardId: string;
  name: string;
  position: number;
  version: number;
  archivedAt: string | null;
}

export interface TaskOrganizationBoard extends TaskOrganizationList {
  columns: TaskOrganizationColumn[];
}

export interface TaskListMembership {
  id: string;
  listId: string;
  taskId: string;
  position: number;
}

export interface TaskBoardPlacement {
  id: string;
  boardId: string;
  columnId: string;
  taskId: string;
  position: number;
  version: number;
}

export interface TaskOrganization {
  lists: TaskOrganizationList[];
  boards: TaskOrganizationBoard[];
  listMemberships: TaskListMembership[];
  placements: TaskBoardPlacement[];
}

export interface CollaborativeTaskEvent {
  id: string;
  taskId: string;
  actorUserId: string | null;
  eventType: TaskEventType;
  beforeData: Record<string, unknown> | null;
  afterData: Record<string, unknown> | null;
  changedPaths: string[];
  comment: string | null;
  correlationId: string;
  sessionType: string;
  administrativeSessionId: string | null;
  supportGrantId: string | null;
  resourceVersionBefore: number | null;
  resourceVersionAfter: number | null;
  occurredAt: string;
}

export interface TaskPage {
  items: CollaborativeTask[];
  total: number | null;
  nextCursor: string | null;
  hasMore: boolean;
}

export interface TaskListFilters {
  view: TaskView;
  status?: TaskStatus | null;
  search?: string;
  cursor?: string | null;
  limit?: number;
  matterRef?: string | null;
  clientRef?: string | null;
  ownerRef?: string | null;
  assigneeRef?: string | null;
  reviewerRef?: string | null;
  labelRef?: string | null;
  /** @deprecated Compatibilidade interna; URLs operacionais usam referências opacas. */
  matterId?: string | null;
  /** @deprecated Compatibilidade interna; URLs operacionais usam referências opacas. */
  clientId?: string | null;
  /** @deprecated Compatibilidade interna; URLs operacionais usam referências opacas. */
  ownerId?: string | null;
  /** @deprecated Compatibilidade interna; URLs operacionais usam referências opacas. */
  assigneeId?: string | null;
  /** @deprecated Compatibilidade interna; URLs operacionais usam referências opacas. */
  reviewerId?: string | null;
  priority?: TaskPriority | null;
  dueFrom?: string | null;
  dueTo?: string | null;
}

export interface TaskParticipantInput {
  userId: string;
  role: TaskParticipantRole;
  dueAt?: string | null;
  requiredForCompletion?: boolean;
}

export interface TaskMemberOption {
  id: string;
  handle: string;
  label: string;
  role: string;
}

export interface TaskMemberOptionPage {
  items: TaskMemberOption[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface TaskMatterOption {
  id: string;
  handle: string;
  label: string;
  clientLabel: string;
}

export interface TaskClientOption {
  id: string;
  handle: string;
  label: string;
  role: 'client';
}

export interface ClientContractOption {
  id: string;
  workspaceId: string;
  clientId: string;
  matterId: string | null;
  title: string;
  status: 'draft' | 'active' | 'ended' | 'cancelled';
  effectiveFrom: string | null;
  effectiveUntil: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface TaskCreateInput {
  matterId: string;
  title: string;
  description?: string | null;
  dueAt?: string | null;
  reviewDueAt?: string | null;
  reviewRequired?: boolean;
  priority?: TaskPriority | null;
  participants?: TaskParticipantInput[];
}

export interface TaskUpdateInput {
  expectedVersion: number;
  title?: string;
  description?: string | null;
  dueAt?: string | null;
  reviewDueAt?: string | null;
  reviewRequired?: boolean;
  priority?: TaskPriority | null;
}

type TaskPageResponse = TaskPage | CollaborativeTask[] | {
  items: CollaborativeTask[];
  total?: number | null;
  nextCursor?: string | null;
  hasMore?: boolean;
};

function normalizeTaskPage(payload: TaskPageResponse): TaskPage {
  if (Array.isArray(payload)) {
    return { items: payload, total: payload.length, nextCursor: null, hasMore: false };
  }
  return {
    items: payload.items,
    total: payload.total ?? null,
    nextCursor: payload.nextCursor ?? null,
    hasMore: payload.hasMore ?? Boolean(payload.nextCursor),
  };
}

export async function listCollaborativeTasks(filters: TaskListFilters): Promise<TaskPage> {
  const params = new URLSearchParams();
  params.set('scope', filters.view);
  params.set('limit', String(filters.limit ?? 50));
  if ((filters.search?.trim().length ?? 0) >= 2) params.set('search', filters.search!.trim());
  if (filters.matterRef) params.set('matterRef', filters.matterRef);
  if (filters.clientRef) params.set('clientRef', filters.clientRef);
  if (filters.ownerRef) params.set('ownerRef', filters.ownerRef);
  if (filters.assigneeRef) params.set('assigneeRef', filters.assigneeRef);
  if (filters.reviewerRef) params.set('reviewerRef', filters.reviewerRef);
  if (filters.labelRef) params.set('labelRef', filters.labelRef);
  if (filters.matterId) params.set('matter', filters.matterId);
  if (filters.clientId) params.set('client', filters.clientId);
  if (filters.ownerId) params.set('owner', filters.ownerId);
  if (filters.assigneeId) params.set('assignee', filters.assigneeId);
  if (filters.reviewerId) params.set('reviewer', filters.reviewerId);
  if (filters.priority) params.set('priority', filters.priority);
  if (filters.status) params.set('status', filters.status);
  if (filters.dueFrom) params.set('dueFrom', filters.dueFrom);
  if (filters.dueTo) params.set('dueTo', filters.dueTo);
  if (filters.cursor) params.set('cursor', filters.cursor);
  const payload = await apiFetch<TaskPageResponse>(`/api/v1/tasks?${params.toString()}`);
  return normalizeTaskPage(payload);
}

export function getCollaborativeTask(taskId: string): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}`);
}

export function listTaskLabelCatalog(): Promise<TaskLabelOption[]> {
  return apiFetch<TaskLabelOption[]>('/api/v1/tasks/label-catalog');
}

export function getTaskOrganization(): Promise<TaskOrganization> {
  return apiFetch<TaskOrganization>('/api/v1/tasks/organization');
}

export function createTaskList(input: { name: string; description?: string | null }): Promise<TaskOrganizationList> {
  return apiFetch<TaskOrganizationList>('/api/v1/tasks/lists', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function createTaskBoard(input: { name: string; description?: string | null }): Promise<TaskOrganizationBoard> {
  return apiFetch<TaskOrganizationBoard>('/api/v1/tasks/boards', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function createTaskBoardColumn(boardId: string, input: { name: string; expectedBoardVersion: number }): Promise<TaskOrganizationColumn> {
  return apiFetch<TaskOrganizationColumn>(`/api/v1/tasks/boards/${boardId}/columns`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function assignTaskToList(input: { taskId: string; listId: string; expectedTaskVersion: number }): Promise<TaskListMembership> {
  return apiFetch<TaskListMembership>('/api/v1/tasks/list-memberships', {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function placeTaskOnBoard(input: {
  taskId: string;
  boardId: string;
  columnId: string;
  expectedTaskVersion: number;
  expectedPlacementVersion?: number | null;
}): Promise<TaskBoardPlacement> {
  return apiFetch<TaskBoardPlacement>('/api/v1/tasks/board-placements', {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function archiveTask(taskId: string, input: { expectedVersion: number; reason: string }): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}/archive`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function unarchiveTask(taskId: string, input: { expectedVersion: number; reason: string }): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}/unarchive`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function listTaskEvents(
  taskId: string,
  offset = 0,
  limit = 50,
): Promise<{ items: CollaborativeTaskEvent[]; total: number }> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return apiFetch<{ items: CollaborativeTaskEvent[]; total: number }>(
    `/api/v1/tasks/${taskId}/events?${params.toString()}`,
  );
}

export function createCollaborativeTask(input: TaskCreateInput): Promise<CollaborativeTask> {
  const { matterId, ...payload } = input;
  const idempotencyKey = `task-ui:${crypto.randomUUID()}`;
  return apiFetch<CollaborativeTask>(`/api/v1/matters/${matterId}/tasks`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ ...payload, idempotencyKey }),
  });
}

export function createContextualTask(input: Omit<TaskCreateInput, 'matterId'> & {
  context: { type: 'workspace' | 'client' | 'contract' | 'matter'; clientId?: string; contractId?: string; matterId?: string };
  origin?: 'manual' | 'administrative' | 'commercial';
}): Promise<CollaborativeTask> {
  const idempotencyKey = `task-ui:${crypto.randomUUID()}`;
  return apiFetch<CollaborativeTask>('/api/v1/tasks', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ ...input, idempotencyKey }),
  });
}

export async function listClientContracts(clientId: string, query = ''): Promise<ClientContractOption[]> {
  const params = new URLSearchParams({ clientId });
  if (query.trim().length >= 2) params.set('search', query.trim());
  return apiFetch<ClientContractOption[]>(`/api/v1/legal/client-contracts?${params.toString()}`);
}

export function createClientContract(input: {
  clientId: string;
  title: string;
  matterId?: string | null;
}): Promise<ClientContractOption> {
  return apiFetch<ClientContractOption>('/api/v1/legal/client-contracts', {
    method: 'POST',
    body: JSON.stringify({ ...input, status: 'active' }),
  });
}

export async function listTaskMemberOptions(query = ''): Promise<TaskMemberOption[]> {
  const params = new URLSearchParams({ limit: '50' });
  if (query.trim().length >= 2) params.set('search', query.trim());
  return apiFetch<TaskMemberOption[]>(`/api/v1/tasks/member-options?${params.toString()}`);
}

export async function listTaskMemberOptionsPage(
  query = '',
  offset = 0,
  limit = 100,
  selectedRefs: string[] = [],
  scope: TaskView = 'workspace',
  legacySelectedIds: string[] = [],
): Promise<TaskMemberOptionPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    scope,
  });
  if (query.trim().length >= 2) params.set('search', query.trim());
  selectedRefs.forEach((reference) => params.append('selectedRef', reference));
  legacySelectedIds.forEach((id) => params.append('selectedId', id));
  return apiFetch<TaskMemberOptionPage>(`/api/v1/tasks/member-options/page?${params.toString()}`);
}

export async function listTaskMatterOptions(query = ''): Promise<TaskMatterOption[]> {
  const params = new URLSearchParams({ limit: '50' });
  if (query.trim().length >= 2) params.set('search', query.trim());
  return apiFetch<TaskMatterOption[]>(`/api/v1/tasks/matter-options?${params.toString()}`);
}

export async function listTaskFilterMatterOptions(
  view: TaskView,
  query = '',
  selectedRefs: string[] = [],
  legacySelectedIds: string[] = [],
): Promise<TaskMatterOption[]> {
  const params = new URLSearchParams({ scope: view, limit: '50' });
  if (query.trim().length >= 2) params.set('search', query.trim());
  selectedRefs.forEach((reference) => params.append('selectedRef', reference));
  legacySelectedIds.forEach((id) => params.append('selectedId', id));
  return apiFetch<TaskMatterOption[]>(
    `/api/v1/tasks/filter-options/matters?${params.toString()}`,
  );
}

export async function listTaskFilterClientOptions(
  view: TaskView,
  query = '',
  selectedRefs: string[] = [],
  legacySelectedIds: string[] = [],
): Promise<TaskClientOption[]> {
  const params = new URLSearchParams({ scope: view, limit: '50' });
  if (query.trim().length >= 2) params.set('search', query.trim());
  selectedRefs.forEach((reference) => params.append('selectedRef', reference));
  legacySelectedIds.forEach((id) => params.append('selectedId', id));
  return apiFetch<TaskClientOption[]>(
    `/api/v1/tasks/filter-options/clients?${params.toString()}`,
  );
}

export function updateCollaborativeTask(taskId: string, input: TaskUpdateInput): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
}

export function replaceTaskParticipants(
  taskId: string,
  expectedVersion: number,
  participants: TaskParticipantInput[],
  reason: string,
): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}/participants`, {
    method: 'PUT',
    body: JSON.stringify({ expectedVersion, participants, reason }),
  });
}

export function updateTaskParticipantState(
  taskId: string,
  participantUserId: string,
  expectedVersion: number,
  state: Exclude<TaskParticipantState, 'removed'>,
  reason: string,
): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(
    `/api/v1/tasks/${taskId}/participants/${participantUserId}/state`,
    {
      method: 'PUT',
      body: JSON.stringify({ expectedVersion, state, reason }),
    },
  );
}

export function executeTaskCommand(
  taskId: string,
  command: TaskCommand,
  expectedVersion: number,
  payload: { reason?: string; comment?: string } = {},
): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}/commands/${command}`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `task-command:${taskId}:${expectedVersion}:${command}` },
    body: JSON.stringify({ expectedVersion, ...payload }),
  });
}

export function addTaskComment(
  taskId: string,
  expectedVersion: number,
  comment: string,
  collaboration: { documentIds?: string[]; mentionedUserIds?: string[] } = {},
): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}/comments`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `task-comment:${crypto.randomUUID()}` },
    body: JSON.stringify({
      expectedVersion,
      comment,
      documentIds: collaboration.documentIds ?? [],
      mentionedUserIds: collaboration.mentionedUserIds ?? [],
    }),
  });
}

export interface TaskMatterDocumentOption {
  id: string;
  name: string;
  mimeType: string | null;
  status: string;
}

export function listTaskMatterDocuments(matterId: string): Promise<TaskMatterDocumentOption[]> {
  return apiFetch<TaskMatterDocumentOption[]>(`/api/v1/matters/${encodeURIComponent(matterId)}/documents`);
}

export function listTaskChecklists(taskId: string): Promise<TaskChecklist[]> {
  return apiFetch<TaskChecklist[]>(`/api/v1/tasks/${taskId}/checklists`);
}

export function createTaskChecklist(taskId: string, expectedTaskVersion: number, title: string): Promise<TaskChecklist> {
  return apiFetch<TaskChecklist>(`/api/v1/tasks/${taskId}/checklists`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `task-checklist:${crypto.randomUUID()}` },
    body: JSON.stringify({ expectedTaskVersion, title }),
  });
}

export function addTaskChecklistItem(taskId: string, checklistId: string, expectedTaskVersion: number, label: string): Promise<TaskChecklist> {
  return apiFetch<TaskChecklist>(`/api/v1/tasks/${taskId}/checklists/${checklistId}/items`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `task-checklist-item:${crypto.randomUUID()}` },
    body: JSON.stringify({ expectedTaskVersion, label }),
  });
}

export function updateTaskChecklistItem(
  taskId: string,
  checklistId: string,
  itemId: string,
  expectedTaskVersion: number,
  expectedItemVersion: number,
  completed: boolean,
): Promise<TaskChecklist> {
  return apiFetch<TaskChecklist>(`/api/v1/tasks/${taskId}/checklists/${checklistId}/items/${itemId}`, {
    method: 'PUT',
    body: JSON.stringify({ expectedTaskVersion, expectedItemVersion, completed }),
  });
}

export function archiveTaskChecklist(
  taskId: string,
  checklistId: string,
  expectedTaskVersion: number,
  expectedChecklistVersion: number,
  reason: string,
): Promise<void> {
  return apiFetch<void>(`/api/v1/tasks/${taskId}/checklists/${checklistId}`, {
    method: 'DELETE',
    body: JSON.stringify({ expectedTaskVersion, expectedChecklistVersion, reason }),
  });
}

export function getTaskLegalResources(taskId: string): Promise<TaskLegalResources> {
  return apiFetch<TaskLegalResources>(`/api/v1/tasks/${taskId}/legal-resources`);
}

export function listTaskDependencyCandidates(
  taskId: string,
  search = '',
): Promise<TaskDependencyCandidate[]> {
  const params = new URLSearchParams({ limit: '50' });
  if (search.trim().length >= 2) params.set('search', search.trim());
  return apiFetch<TaskDependencyCandidate[]>(
    `/api/v1/tasks/${taskId}/dependency-candidates?${params.toString()}`,
  );
}

export function replaceTaskLabels(
  taskId: string,
  expectedVersion: number,
  labels: Array<{ name: string; color: string }>,
  reason: string,
): Promise<TaskLabel[]> {
  return apiFetch<TaskLabel[]>(`/api/v1/tasks/${taskId}/labels`, {
    method: 'PUT',
    body: JSON.stringify({ expectedVersion, labels, reason }),
  });
}

export function createTaskSubtask(
  taskId: string,
  expectedParentVersion: number,
  title: string,
): Promise<CollaborativeTask> {
  return apiFetch<CollaborativeTask>(`/api/v1/tasks/${taskId}/subtasks`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `task-subtask:${crypto.randomUUID()}` },
    body: JSON.stringify({ expectedParentVersion, title }),
  });
}

export function addTaskDependency(
  taskId: string,
  expectedVersion: number,
  predecessorRef: string,
  reason: string,
): Promise<TaskDependency> {
  return apiFetch<TaskDependency>(`/api/v1/tasks/${taskId}/dependencies`, {
    method: 'POST',
    body: JSON.stringify({ expectedVersion, predecessorRef, reason }),
  });
}

export function removeTaskDependency(
  taskId: string,
  dependencyId: string,
  expectedVersion: number,
  reason: string,
): Promise<void> {
  return apiFetch<void>(`/api/v1/tasks/${taskId}/dependencies/${dependencyId}`, {
    method: 'DELETE',
    body: JSON.stringify({ expectedVersion, reason }),
  });
}

export type TaskUiErrorKind = 'forbidden' | 'conflict' | 'quota' | 'unavailable' | 'not_found' | 'error';

export function classifyTaskError(error: unknown): TaskUiErrorKind {
  if (error instanceof ApiError) {
    if (error.status === 403) return 'forbidden';
    if (error.status === 404) return 'not_found';
    const code = error.detail && typeof error.detail === 'object' && 'code' in error.detail
      ? String(error.detail.code)
      : '';
    if (error.status === 409 && /(quota|limit|entitlement)/i.test(code)) return 'quota';
    if (error.status === 409) return 'conflict';
    if (error.status >= 500 || error.status === 408 || error.status === 429) return 'unavailable';
  }
  return 'error';
}

export function taskErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
