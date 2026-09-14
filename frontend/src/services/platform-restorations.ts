import { apiFetch } from '@/services/http';

export interface PlatformRestorationPreview {
  id: string;
  sourceAuditEventId: string | null;
  resourceType: string;
  resourceId: string;
  currentVersion: number;
  currentHash: string;
  targetVersion: number | null;
  targetHash: string;
  diff: Array<{
    path: string;
    label: string;
    changeType: 'added' | 'removed' | 'changed';
    before: unknown;
    after: unknown;
    sensitive: boolean;
  }>;
  impact: Record<string, unknown>;
  warnings: string[];
  eligible: boolean;
  ineligibleReasons: string[];
  expiresAt: string;
  consumedAt: string | null;
}

export interface PlatformRestorationJob {
  id: string;
  previewId: string;
  actorUserId: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'compensated';
  reason: string;
  correlationId: string;
  errorCode: string | null;
  errorDetail: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export function createRestorationPreview(sourceAuditEventId: string): Promise<PlatformRestorationPreview> {
  return apiFetch('/api/v1/platform/restorations/previews', {
    method: 'POST',
    body: JSON.stringify({ sourceAuditEventId }),
  });
}

export function createPlatformRestoration(input: {
  preview: PlatformRestorationPreview;
  reason: string;
  confirmation: string;
  idempotencyKey: string;
}): Promise<PlatformRestorationJob> {
  return apiFetch('/api/v1/platform/restorations', {
    method: 'POST',
    headers: { 'Idempotency-Key': input.idempotencyKey },
    body: JSON.stringify({
      previewId: input.preview.id,
      expectedResourceVersion: input.preview.currentVersion,
      reason: input.reason,
      confirmation: input.confirmation,
    }),
  });
}

export function fetchPlatformRestoration(jobId: string): Promise<PlatformRestorationJob> {
  return apiFetch(`/api/v1/platform/restorations/${jobId}`);
}
