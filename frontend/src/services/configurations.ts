import { apiFetch } from '@/services/http';

export type ConfigurationScope = 'workspace' | 'role' | 'user';

export interface ConfigurationLayer {
  scope: string;
  scopeKey: string;
  version: number;
  etag: string;
}

export interface ResolvedConfiguration {
  namespace: string;
  effective: Record<string, unknown>;
  etag: string;
  layers: ConfigurationLayer[];
}

export interface WorkspaceExperience {
  supportLabel: string;
  welcomeMessage: string;
}

export interface ConfigurationDocument {
  id: string;
  namespace: string;
  scope: ConfigurationScope;
  scopeKey: string;
  roleKey?: string | null;
  userId?: string | null;
  schemaVersion: string;
  currentVersion: number;
  etag: string;
  payload: Record<string, unknown>;
  updatedByUserId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ConfigurationVersion {
  id: string;
  version: number;
  schemaVersion: string;
  payload: Record<string, unknown>;
  payloadHash: string;
  reason?: string | null;
  createdByUserId?: string | null;
  createdAt: string;
}

interface ConfigurationTarget {
  namespace: string;
  scope: ConfigurationScope;
  roleKey?: string | null;
  userId?: string | null;
}

function targetQuery(target: ConfigurationTarget): string {
  const query = new URLSearchParams();
  if (target.roleKey) query.set('role_key', target.roleKey);
  if (target.userId) query.set('user_id', target.userId);
  const value = query.toString();
  return value ? `?${value}` : '';
}

function targetPath(target: ConfigurationTarget): string {
  return `${targetBasePath(target)}${targetQuery(target)}`;
}

function targetBasePath(target: ConfigurationTarget): string {
  return `/api/v1/configurations/${encodeURIComponent(target.namespace)}/documents/${target.scope}`;
}

export function fetchEffectiveConfiguration(namespace: string): Promise<ResolvedConfiguration> {
  return apiFetch<ResolvedConfiguration>(
    `/api/v1/configurations/${encodeURIComponent(namespace)}/effective`,
  );
}

export async function fetchWorkspaceExperience(): Promise<WorkspaceExperience> {
  const resolved = await fetchEffectiveConfiguration('workspace.experience');
  const supportLabel = typeof resolved.effective.supportLabel === 'string'
    ? resolved.effective.supportLabel.trim()
    : '';
  const welcomeMessage = typeof resolved.effective.welcomeMessage === 'string'
    ? resolved.effective.welcomeMessage.trim()
    : '';
  return {
    supportLabel: supportLabel || 'Ajuda e suporte',
    welcomeMessage,
  };
}

export function fetchMyConfiguration(
  namespace: string,
): Promise<ConfigurationDocument | null> {
  return apiFetch<ConfigurationDocument | null>(
    `/api/v1/configurations/${encodeURIComponent(namespace)}/me`,
  );
}

export function saveMyConfiguration(
  namespace: string,
  payload: Record<string, unknown>,
  options: {
    etag?: string | null;
    reason?: string | null;
    schemaVersion?: string;
  } = {},
): Promise<ConfigurationDocument> {
  return apiFetch<ConfigurationDocument>(
    `/api/v1/configurations/${encodeURIComponent(namespace)}/me`,
    {
      method: 'PUT',
      headers: options.etag ? { 'If-Match': `"${options.etag}"` } : undefined,
      body: JSON.stringify({
        payload,
        schemaVersion: options.schemaVersion ?? '1.0',
        reason: options.reason || null,
      }),
    },
  );
}

export function fetchConfigurationDocument(
  target: ConfigurationTarget,
): Promise<ConfigurationDocument> {
  return apiFetch<ConfigurationDocument>(targetPath(target));
}

export function fetchConfigurationVersions(
  target: ConfigurationTarget,
): Promise<ConfigurationVersion[]> {
  return apiFetch<ConfigurationVersion[]>(
    `${targetBasePath(target)}/versions${targetQuery(target)}`,
  );
}

export function saveConfigurationDocument(
  target: ConfigurationTarget,
  payload: Record<string, unknown>,
  options: {
    etag?: string | null;
    reason?: string | null;
    schemaVersion?: string;
  } = {},
): Promise<ConfigurationDocument> {
  return apiFetch<ConfigurationDocument>(targetPath(target), {
    method: 'PUT',
    headers: options.etag ? { 'If-Match': `"${options.etag}"` } : undefined,
    body: JSON.stringify({
      payload,
      schemaVersion: options.schemaVersion ?? '1.0',
      reason: options.reason || null,
      roleKey: target.roleKey || null,
      userId: target.userId || null,
    }),
  });
}

export function rollbackConfigurationDocument(
  target: ConfigurationTarget,
  version: number,
  etag: string,
): Promise<ConfigurationDocument> {
  return apiFetch<ConfigurationDocument>(
    `${targetBasePath(target)}/rollback/${version}${targetQuery(target)}`,
    {
      method: 'POST',
      headers: { 'If-Match': `"${etag}"` },
    },
  );
}
