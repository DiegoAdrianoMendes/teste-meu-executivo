import { apiFetch } from '@/services/http';

export type StudioKind = 'agent' | 'agent_package' | 'flow' | 'experience';

export interface StudioProposal {
  kind: StudioKind;
  title: string;
  key: string;
  payload: Record<string, unknown>;
  summary: string;
  suggestions: string[];
  warnings: string[];
  approvalRequired: boolean;
  assistantMode: string;
}

export interface StudioDraft {
  id: string;
  workspaceId: string;
  kind: StudioKind;
  key: string;
  title: string;
  status: string;
  payload: Record<string, unknown>;
  currentVersion: number;
  etag: string;
  updatedAt: string;
  publication?: {
    targetType: string;
    targetId: string;
    targetVersion: number;
    publishedAt: string;
  } | null;
}

export interface StudioPublishResult {
  draft: StudioDraft;
  targetType: string;
  targetId: string;
  targetVersion: number;
  alreadyPublished: boolean;
}

export interface StudioVersion {
  version: number;
  payload: Record<string, unknown>;
  reason: string | null;
  createdByUserId: string | null;
  createdAt: string;
}

export interface StudioDraftPage {
  items: StudioDraft[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface StudioVersionPage {
  items: StudioVersion[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface StudioWorkspaceOption {
  id: string;
  name: string;
  slug: string;
  canRead: boolean;
  canWrite: boolean;
  expiresAt?: string | null;
  readExpiresAt?: string | null;
  writeExpiresAt?: string | null;
}

export interface StudioWorkspacePage {
  items: StudioWorkspaceOption[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export function fetchPlatformConfigurationWorkspacePage(input: {
  search?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<StudioWorkspacePage> {
  const params = new URLSearchParams({
    limit: String(input.limit ?? 20),
    offset: String(input.offset ?? 0),
  });
  if (input.search?.trim()) params.set('search', input.search.trim());
  return apiFetch(`/api/v1/platform/configuration-studio/workspaces/page?${params.toString()}`);
}

export function fetchPlatformConfigurationWorkspace(
  workspaceId: string,
): Promise<StudioWorkspaceOption> {
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/access`,
  );
}

export function assistPlatformConfiguration(
  workspaceId: string,
  input: {
    kind: StudioKind;
    prompt: string;
    title?: string;
    currentPayload?: Record<string, unknown>;
    attachmentNames?: string[];
  },
): Promise<StudioProposal> {
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/assist`,
    {
      method: 'POST',
      body: JSON.stringify(input),
    },
  );
}

export function fetchPlatformConfigurationDraftPage(
  workspaceId: string,
  input: { kind?: StudioKind; limit?: number; offset?: number } = {},
): Promise<StudioDraftPage> {
  const params = new URLSearchParams({
    limit: String(input.limit ?? 10),
    offset: String(input.offset ?? 0),
  });
  if (input.kind) params.set('kind', input.kind);
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/drafts/page?${params.toString()}`,
  );
}

export function createPlatformConfigurationDraft(
  workspaceId: string,
  input: {
    kind: StudioKind;
    title: string;
    key: string;
    payload: Record<string, unknown>;
    reason?: string;
  },
): Promise<StudioDraft> {
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/drafts`,
    {
      method: 'POST',
      body: JSON.stringify(input),
    },
  );
}

export function updatePlatformConfigurationDraft(
  workspaceId: string,
  draftId: string,
  input: {
    title?: string;
    payload: Record<string, unknown>;
    reason?: string;
    expectedEtag?: string;
  },
): Promise<StudioDraft> {
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/drafts/${encodeURIComponent(draftId)}`,
    {
      method: 'PUT',
      body: JSON.stringify(input),
    },
  );
}

export function publishPlatformConfigurationDraft(
  workspaceId: string,
  draftId: string,
  input: { expectedEtag?: string; reason?: string },
): Promise<StudioPublishResult> {
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/drafts/${encodeURIComponent(draftId)}/publish`,
    {
      method: 'POST',
      body: JSON.stringify(input),
    },
  );
}

export function fetchPlatformConfigurationDraftVersionPage(
  workspaceId: string,
  draftId: string,
  input: { limit?: number; offset?: number } = {},
): Promise<StudioVersionPage> {
  const params = new URLSearchParams({
    limit: String(input.limit ?? 10),
    offset: String(input.offset ?? 0),
  });
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/drafts/${encodeURIComponent(draftId)}/versions/page?${params.toString()}`,
  );
}

export function restorePlatformConfigurationDraft(
  workspaceId: string,
  draftId: string,
  input: { targetVersion: number; expectedEtag: string; reason: string },
): Promise<StudioDraft> {
  return apiFetch(
    `/api/v1/platform/configuration-studio/workspaces/${encodeURIComponent(workspaceId)}/drafts/${encodeURIComponent(draftId)}/restore`,
    { method: 'POST', body: JSON.stringify(input) },
  );
}
