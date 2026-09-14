import { apiBlob, apiFetch } from '@/services/http';

export type PlatformExportDataset =
  | 'accounts'
  | 'users'
  | 'kpis'
  | 'billing'
  | 'tickets'
  | 'leads'
  | 'audit'
  | 'bulk_result';

export type PlatformExportFormat = 'csv' | 'xlsx' | 'ndjson';

export interface PlatformExportJob {
  id: string;
  actorUserId: string;
  dataset: PlatformExportDataset;
  format: PlatformExportFormat;
  filters: Record<string, unknown>;
  columns: string[];
  selectionSetId: string | null;
  status: 'queued' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled' | 'expired';
  processedRows: number;
  rowCount: number | null;
  byteSize: number | null;
  checksumSha256: string | null;
  errorCode: string | null;
  errorDetail: string | null;
  correlationId: string;
  cancelRequestedAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  expiresAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformExportPage {
  items: PlatformExportJob[];
  total: number;
  limit: number;
  offset: number;
}

export function fetchPlatformExports(limit = 50, offset = 0): Promise<PlatformExportPage> {
  return apiFetch(`/api/v1/platform/exports?limit=${limit}&offset=${offset}`);
}

export function createPlatformExport(input: {
  dataset: PlatformExportDataset;
  format: PlatformExportFormat;
  filters?: Record<string, unknown>;
  columns?: string[];
  selectionSetId?: string | null;
  idempotencyKey: string;
}): Promise<PlatformExportJob> {
  return apiFetch('/api/v1/platform/exports', {
    method: 'POST',
    headers: { 'Idempotency-Key': input.idempotencyKey },
    body: JSON.stringify({
      dataset: input.dataset,
      format: input.format,
      filters: input.filters ?? {},
      columns: input.columns ?? [],
      selectionSetId: input.selectionSetId ?? null,
    }),
  });
}

export function cancelPlatformExport(jobId: string): Promise<PlatformExportJob> {
  return apiFetch(`/api/v1/platform/exports/${jobId}/cancel`, { method: 'POST' });
}

export async function downloadPlatformExport(job: PlatformExportJob): Promise<void> {
  const result = await apiBlob(`/api/v1/platform/exports/${job.id}/download`);
  const url = URL.createObjectURL(result.blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = result.filename ?? `blackbox-${job.dataset}-${job.id}.${job.format}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
