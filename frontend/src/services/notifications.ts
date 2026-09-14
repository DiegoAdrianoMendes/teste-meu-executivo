import { apiFetch } from '@/services/http';
import type { Notification } from '@/types';

export async function fetchNotifications(limit = 50): Promise<Notification[]> {
  const safeLimit = Math.max(1, Math.min(100, Math.trunc(limit)));
  return apiFetch<Notification[]>(`/api/v1/notifications?limit=${safeLimit}`);
}

export async function markNotificationRead(notificationId: string): Promise<void> {
  await apiFetch(`/api/v1/notifications/${encodeURIComponent(notificationId)}/read`, {
    method: 'PATCH',
  });
}

export async function markAllNotificationsRead(): Promise<{ updatedCount: number }> {
  return apiFetch<{ updatedCount: number }>('/api/v1/notifications/mark-all-read', {
    method: 'POST',
  });
}

export interface ModuleAccessRequestPayload {
  module: string;
  moduleLabel?: string;
  note?: string;
}

export async function requestModuleAccess(
  payload: ModuleAccessRequestPayload,
): Promise<{ message: string; notifiedAdmins: number }> {
  return apiFetch<{ message: string; notifiedAdmins: number }>('/api/v1/notifications/request-access', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
