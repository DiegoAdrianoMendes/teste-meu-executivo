import { apiFetch } from '@/services/http';

export type PlatformReleaseEnvironment = 'local' | 'staging' | 'production';
export type PlatformReleaseStatus = 'planned' | 'deploying' | 'active' | 'failed' | 'rolled_back';

export interface PlatformReleaseRecord {
  id: string;
  version: string;
  environment: PlatformReleaseEnvironment;
  status: PlatformReleaseStatus;
  commitSha: string | null;
  source: string;
  deployedAt: string | null;
  previousReleaseId: string | null;
  rollbackTargetId: string | null;
  revision: number;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformReleasePage {
  items: PlatformReleaseRecord[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: 'asc' | 'desc' }>;
}

export function fetchPlatformReleaseRecords(input: {
  environment?: PlatformReleaseEnvironment;
  limit?: number;
  offset?: number;
} = {}): Promise<PlatformReleasePage> {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  if (input.environment) query.set('environment', input.environment);
  return apiFetch(`/api/v1/platform/governance/releases/records/page?${query.toString()}`);
}

export function createPlatformReleaseRecord(input: {
  version: string;
  environment: PlatformReleaseEnvironment;
  status: PlatformReleaseStatus;
  commitSha: string | null;
  source: string;
  deployedAt: string | null;
  previousReleaseId: string | null;
  rollbackTargetId: string | null;
  reason: string;
}): Promise<PlatformReleaseRecord> {
  return apiFetch('/api/v1/platform/governance/releases/records', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function updatePlatformReleaseRecord(
  release: PlatformReleaseRecord,
  input: { status: PlatformReleaseStatus; deployedAt: string | null; rollbackTargetId: string | null; reason: string },
): Promise<PlatformReleaseRecord> {
  return apiFetch(`/api/v1/platform/governance/releases/records/${release.id}`, {
    method: 'PATCH',
    headers: { 'If-Match': `"${release.revision}"` },
    body: JSON.stringify(input),
  });
}
