import { apiFetch } from '@/services/http';
import { resolveApiHttpUrl } from '@/services/api-origin';

export interface TaskExternalSharePreview {
  title: string;
  status: string;
  dueAt: string | null;
  safeContext: string | null;
  expiresAt: string;
  previewFingerprint: string;
  excludedContent: string[];
}

export interface TaskExternalShareRecord {
  id: string;
  expiresAt: string;
  revokedAt: string | null;
  createdAt: string;
}

export interface TaskExternalShareIssued extends TaskExternalShareRecord {
  url: string;
}

export interface PublicTaskExternalShare {
  title: string;
  status: string;
  dueAt: string | null;
  safeContext: string | null;
  expiresAt: string;
}

export async function redeemPublicTaskExternalShare(token: string): Promise<PublicTaskExternalShare> {
  const response = await fetch(
    resolveApiHttpUrl('/api/v1/public/task-shares/redeem', window.location.origin),
    {
      method: 'POST',
      headers: { Accept: 'application/json', 'X-Task-Share-Token': token },
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    },
  );
  if (!response.ok) throw new Error('Link indisponível');
  return response.json() as Promise<PublicTaskExternalShare>;
}

export function previewTaskExternalShare(taskId: string, expiresAt: string, safeContext: string | null) {
  return apiFetch<TaskExternalSharePreview>(`/api/v1/tasks/${taskId}/external-shares/preview`, {
    method: 'POST',
    body: JSON.stringify({ expiresAt, safeContext }),
  });
}

export function createTaskExternalShare(
  taskId: string,
  expiresAt: string,
  safeContext: string | null,
  previewFingerprint: string,
) {
  return apiFetch<TaskExternalShareIssued>(`/api/v1/tasks/${taskId}/external-shares`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `task-external-share:${crypto.randomUUID()}` },
    body: JSON.stringify({ expiresAt, safeContext, previewFingerprint }),
  });
}

export function listTaskExternalShares(taskId: string) {
  return apiFetch<TaskExternalShareRecord[]>(`/api/v1/tasks/${taskId}/external-shares`);
}

export function revokeTaskExternalShare(taskId: string, shareId: string) {
  return apiFetch<TaskExternalShareRecord>(`/api/v1/tasks/${taskId}/external-shares/${shareId}`, {
    method: 'DELETE',
  });
}
