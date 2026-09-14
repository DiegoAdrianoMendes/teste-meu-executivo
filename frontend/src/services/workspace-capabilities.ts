import { ApiError, apiFetch } from '@/services/http';
import type {
  CommercialEntitlementKey,
  EffectiveCapability,
  EffectiveCapabilityManifest,
} from '@/services/platform-commercial-catalog';

export type WorkspaceCapabilityManifestStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'not_configured'
  | 'forbidden'
  | 'unavailable';

export function fetchWorkspaceCapabilityManifest(
  signal?: AbortSignal,
): Promise<EffectiveCapabilityManifest> {
  return apiFetch('/api/v1/workspaces/capability-manifest', { signal });
}

function apiDetailCode(error: ApiError): string | null {
  if (!error.detail || typeof error.detail !== 'object' || !('code' in error.detail)) return null;
  return typeof error.detail.code === 'string' ? error.detail.code : null;
}

export function classifyWorkspaceCapabilityManifestError(
  error: unknown,
): Exclude<WorkspaceCapabilityManifestStatus, 'idle' | 'loading' | 'ready'> {
  if (error instanceof ApiError) {
    const code = apiDetailCode(error);
    if (code === 'COMMERCIAL-MANIFEST-NOT-CONFIGURED' || code === 'COMMERCIAL-MANIFEST-INVALID') {
      return 'not_configured';
    }
    if (error.status === 401 || error.status === 403) return 'forbidden';
  }
  return 'unavailable';
}

export function getWorkspaceCapability(
  manifest: EffectiveCapabilityManifest | null,
  key: CommercialEntitlementKey,
): EffectiveCapability | null {
  return manifest?.capabilities[key] ?? null;
}

/** A capacidade pode continuar visível quando a quota acabou: o operador
 * ainda precisa consultar dados existentes e entender por que uma nova
 * mutação está bloqueada. */
export function isWorkspaceCapabilityVisible(capability: EffectiveCapability | null): boolean {
  return Boolean(capability?.contracted && capability.enabled);
}

export function isWorkspaceCapabilityUsable(capability: EffectiveCapability | null): boolean {
  return Boolean(capability?.contracted && capability.enabled && capability.permitted);
}
