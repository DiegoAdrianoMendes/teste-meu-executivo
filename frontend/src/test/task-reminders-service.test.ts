import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  createTaskReminderRule,
  disableTaskReminderRule,
  listTaskReminderRules,
  newTaskReminderIdempotencyKey,
  updateTaskReminderRule,
} from '@/services/task-reminders';

afterEach(() => vi.clearAllMocks());

describe('task reminder transport', () => {
  it('lists rules through the encoded task-owned endpoint', () => {
    listTaskReminderRules('task/opaque');

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/tasks/task%2Fopaque/reminder-rules',
    );
  });

  it('creates a rule with the caller-owned stable idempotency key and task version', () => {
    const input = {
      expectedVersion: 3,
      relativeTo: 'general_due' as const,
      offsetMinutesBefore: 1_440,
      channel: 'in_app' as const,
      recipient: 'owner' as const,
      recipientUserId: null,
    };

    createTaskReminderRule('task-1', input, 'task-reminder:create:stable-key');

    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/tasks/task-1/reminder-rules', {
      method: 'POST',
      headers: { 'Idempotency-Key': 'task-reminder:create:stable-key' },
      body: JSON.stringify(input),
    });
  });

  it('updates and disables one encoded rule using its expected version', () => {
    const input = {
      expectedVersion: 4,
      offsetMinutesBefore: 60,
      channel: 'in_app' as const,
      recipient: 'specific_user' as const,
      recipientUserId: 'user-2',
    };

    updateTaskReminderRule('task/1', 'rule/2', input, 'task-reminder:update:stable-key');
    disableTaskReminderRule('task/1', 'rule/2', 5, 'task-reminder:disable:stable-key');

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/tasks/task%2F1/reminder-rules/rule%2F2',
      {
        method: 'PATCH',
        headers: { 'Idempotency-Key': 'task-reminder:update:stable-key' },
        body: JSON.stringify(input),
      },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/tasks/task%2F1/reminder-rules/rule%2F2?expectedVersion=5',
      {
        method: 'DELETE',
        headers: { 'Idempotency-Key': 'task-reminder:disable:stable-key' },
      },
    );
  });

  it('generates bounded operation-scoped keys for one UI intent', () => {
    const first = newTaskReminderIdempotencyKey('create');
    const second = newTaskReminderIdempotencyKey('create');

    expect(first).toMatch(/^task-reminder:create:/);
    expect(first).not.toBe(second);
    expect(first.length).toBeLessThanOrEqual(180);
  });
});
