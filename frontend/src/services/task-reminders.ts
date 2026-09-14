import { apiFetch } from '@/services/http';

export type TaskReminderRelativeTo = 'general_due' | 'personal_due' | 'review_due';
export type TaskReminderChannel = 'in_app' | 'email' | 'whatsapp';
export type TaskReminderRecipient =
  | 'owner'
  | 'assignees'
  | 'required_assignees'
  | 'reviewer'
  | 'specific_user';
export type TaskReminderRuleState =
  | 'scheduled'
  | 'pending_due'
  | 'pending_recipient'
  | 'unavailable'
  | 'disabled';
export type TaskReminderDeliveryStatus =
  | 'pending'
  | 'processing'
  | 'delivered'
  | 'failed'
  | 'cancelled'
  | 'unavailable';

export interface TaskReminderDelivery {
  id: string;
  recipientUserId: string;
  deadlineAt: string;
  scheduledFor: string;
  channel: TaskReminderChannel;
  status: TaskReminderDeliveryStatus;
  attempts: number;
  deliveredAt: string | null;
  cancelledAt: string | null;
  lastErrorCode: string | null;
  notificationId: string | null;
}

export interface TaskReminderRule {
  id: string;
  workspaceId: string;
  matterId: string;
  taskId: string;
  relativeTo: TaskReminderRelativeTo;
  offsetMinutesBefore: number;
  channel: TaskReminderChannel;
  recipient: TaskReminderRecipient;
  recipientUserId: string | null;
  state: TaskReminderRuleState;
  active: boolean;
  version: number;
  deliveryAvailable: boolean;
  unavailabilityReason: string | null;
  nextScheduledAt: string | null;
  deliveries: TaskReminderDelivery[];
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
}

export interface TaskReminderRuleCreateInput {
  expectedVersion: number;
  relativeTo: TaskReminderRelativeTo;
  offsetMinutesBefore: number;
  channel: TaskReminderChannel;
  recipient: TaskReminderRecipient;
  recipientUserId?: string | null;
}

export interface TaskReminderRuleUpdateInput {
  expectedVersion: number;
  relativeTo?: TaskReminderRelativeTo;
  offsetMinutesBefore?: number;
  channel?: TaskReminderChannel;
  recipient?: TaskReminderRecipient;
  recipientUserId?: string | null;
}

function reminderRulesPath(taskId: string): string {
  return `/api/v1/tasks/${encodeURIComponent(taskId)}/reminder-rules`;
}

export function newTaskReminderIdempotencyKey(operation: 'create' | 'update' | 'disable'): string {
  return `task-reminder:${operation}:${crypto.randomUUID()}`;
}

export function listTaskReminderRules(taskId: string): Promise<TaskReminderRule[]> {
  return apiFetch<TaskReminderRule[]>(reminderRulesPath(taskId));
}

export function createTaskReminderRule(
  taskId: string,
  input: TaskReminderRuleCreateInput,
  idempotencyKey: string,
): Promise<TaskReminderRule> {
  return apiFetch<TaskReminderRule>(reminderRulesPath(taskId), {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(input),
  });
}

export function updateTaskReminderRule(
  taskId: string,
  ruleId: string,
  input: TaskReminderRuleUpdateInput,
  idempotencyKey: string,
): Promise<TaskReminderRule> {
  return apiFetch<TaskReminderRule>(
    `${reminderRulesPath(taskId)}/${encodeURIComponent(ruleId)}`,
    {
      method: 'PATCH',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(input),
    },
  );
}

export function disableTaskReminderRule(
  taskId: string,
  ruleId: string,
  expectedVersion: number,
  idempotencyKey: string,
): Promise<{ id: string; taskId: string; deleted: true; version: number }> {
  const params = new URLSearchParams({ expectedVersion: String(expectedVersion) });
  return apiFetch(
    `${reminderRulesPath(taskId)}/${encodeURIComponent(ruleId)}?${params.toString()}`,
    {
      method: 'DELETE',
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  );
}
