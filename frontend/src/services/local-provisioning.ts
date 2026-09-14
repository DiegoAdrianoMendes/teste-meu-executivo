import { apiFetch } from '@/services/http';

export interface LocalProvisioningResource {
  key: string;
  provider: string;
  resourceType: string;
  logicalKey: string;
  action: 'create' | 'update' | 'noop';
  status: 'pending' | 'drift' | 'ready';
  desiredHash: string;
  observedHash?: string | null;
  adapter: 'fake_local';
  externalEffects: number;
}

export interface LocalProvisioningPlan {
  workspaceId: string;
  digest: string;
  schemaVersion: string;
  generatedAt: string;
  resources: LocalProvisioningResource[];
  creates: number;
  updates: number;
  noops: number;
  ready: boolean;
  externalEffects: number;
}

export interface LocalProvisioningState {
  workspaceId: string;
  configured: boolean;
  documentVersion?: number | null;
  etag?: string | null;
  desiredDigest: string;
  lastCommand?: string | null;
  lastRunAt?: string | null;
  lastActorUserId?: string | null;
  resources: LocalProvisioningResource[];
  ready: boolean;
  externalEffects: number;
}

export interface LocalProvisioningCommandResult {
  action: 'apply' | 'reconcile';
  replayed: boolean;
  intentId: string;
  plan: LocalProvisioningPlan;
  state: LocalProvisioningState;
  externalEffects: number;
}

function commandKey(action: string): string {
  const nonce = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `spa:provisioning:${action}:${nonce}`;
}

export function fetchLocalProvisioningPlan(): Promise<LocalProvisioningPlan> {
  return apiFetch<LocalProvisioningPlan>('/api/v1/local-provisioning/plan');
}

export function fetchLocalProvisioningState(): Promise<LocalProvisioningState> {
  return apiFetch<LocalProvisioningState>('/api/v1/local-provisioning/status');
}

export function executeLocalProvisioning(
  action: 'apply' | 'reconcile',
): Promise<LocalProvisioningCommandResult> {
  return apiFetch<LocalProvisioningCommandResult>(`/api/v1/local-provisioning/${action}`, {
    method: 'POST',
    body: JSON.stringify({
      idempotencyKey: commandKey(action),
      reason: action === 'apply'
        ? 'Aplicação do plano local pela SPA'
        : 'Reconciliação local pela SPA',
    }),
  });
}
