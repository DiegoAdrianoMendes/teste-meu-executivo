import { apiDownload, apiFetch, API_BASE } from '@/services/http';

export type LocalStorageState = 'pending_upload' | 'quarantined' | 'available' | 'rejected' | 'deleted';
export type LocalStorageAvStatus = 'pending' | 'clean' | 'infected' | 'error';

export interface LocalStorageObject {
  id: string;
  workspaceId: string;
  objectId: string;
  version: number;
  originalFilename: string;
  declaredContentType: string;
  detectedContentType?: string | null;
  expectedByteSize?: number | null;
  byteSize?: number | null;
  expectedSha256?: string | null;
  checksumSha256?: string | null;
  state: LocalStorageState;
  avStatus: LocalStorageAvStatus;
  quarantineReason?: string | null;
  retentionUntil?: string | null;
  uploadedAt?: string | null;
  scannedAt?: string | null;
  deletedAt?: string | null;
  restoredAt?: string | null;
  createdByUserId?: string | null;
  deletedByUserId?: string | null;
  restoredByUserId?: string | null;
  createdAt: string;
  updatedAt: string;
}

interface LocalStorageUploadTicket {
  object: LocalStorageObject;
  uploadUrl: string;
  expiresAt: string;
}

interface LocalStorageDownloadTicket {
  objectId: string;
  version: number;
  downloadUrl: string;
  expiresAt: string;
}

function ticketUrl(value: string): string {
  const baseOrigin = new URL(API_BASE || window.location.origin, window.location.origin).origin;
  const resolved = new URL(value, `${baseOrigin}/`);
  if (
    resolved.origin !== baseOrigin
    || !resolved.pathname.startsWith('/api/v1/local-storage/')
  ) {
    throw new Error('O ticket local apontou para uma origem não autorizada.');
  }
  return `${resolved.pathname}${resolved.search}`;
}

async function sha256(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, '0')).join('');
}

async function uploadWithTicket(uploadUrl: string, file: File): Promise<LocalStorageObject> {
  const response = await fetch(`${API_BASE}${ticketUrl(uploadUrl)}`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.message || error?.detail || 'O upload local não foi concluído.');
  }
  return response.json() as Promise<LocalStorageObject>;
}

export function fetchLocalStorageObjects(includeDeleted = false): Promise<LocalStorageObject[]> {
  return apiFetch<LocalStorageObject[]>(
    `/api/v1/local-storage/objects?include_deleted=${includeDeleted}`,
  );
}

export function fetchLocalStorageVersions(
  objectId: string,
  includeDeleted = true,
): Promise<LocalStorageObject[]> {
  return apiFetch<LocalStorageObject[]>(
    `/api/v1/local-storage/objects/${objectId}/versions?include_deleted=${includeDeleted}`,
  );
}

export async function uploadLocalStorageObject(
  file: File,
  options: { objectId?: string; retentionUntil?: string | null } = {},
): Promise<LocalStorageObject> {
  const checksumSha256 = await sha256(file);
  const path = options.objectId
    ? `/api/v1/local-storage/objects/${options.objectId}/versions`
    : '/api/v1/local-storage/objects';
  const authorization = await apiFetch<LocalStorageUploadTicket>(path, {
    method: 'POST',
    body: JSON.stringify({
      filename: file.name,
      contentType: file.type || 'application/octet-stream',
      byteSize: file.size,
      checksumSha256,
      retentionUntil: options.retentionUntil || null,
      expiresIn: 300,
    }),
  });
  return uploadWithTicket(authorization.uploadUrl, file);
}

export async function downloadLocalStorageObject(
  objectId: string,
  version?: number,
): Promise<Blob> {
  const query = new URLSearchParams({ expires_in: '300' });
  if (version) query.set('version', String(version));
  const authorization = await apiFetch<LocalStorageDownloadTicket>(
    `/api/v1/local-storage/objects/${objectId}/download-ticket?${query.toString()}`,
    { method: 'POST' },
  );
  return apiDownload(ticketUrl(authorization.downloadUrl));
}

export function exportLocalStorageObject(objectId: string): Promise<Blob> {
  return apiDownload(`/api/v1/local-storage/objects/${objectId}/export`);
}

export function extendLocalStorageRetention(
  objectId: string,
  retentionUntil: string,
): Promise<LocalStorageObject> {
  return apiFetch<LocalStorageObject>(`/api/v1/local-storage/objects/${objectId}/retention`, {
    method: 'PUT',
    body: JSON.stringify({ retentionUntil }),
  });
}

export function deleteLocalStorageObject(objectId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/local-storage/objects/${objectId}`, { method: 'DELETE' });
}

export function restoreLocalStorageObject(objectId: string): Promise<LocalStorageObject> {
  return apiFetch<LocalStorageObject>(`/api/v1/local-storage/objects/${objectId}/restore`, {
    method: 'POST',
  });
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
