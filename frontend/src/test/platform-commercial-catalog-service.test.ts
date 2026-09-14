import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  addCommercialAdjustment,
  addEntitlementBonus,
  bootstrapCommercialOffers,
  commercialIdempotencyKey,
  getWorkspaceCapabilityManifest,
  getWorkspaceCommercialContract,
  listCommercialContracts,
  listCommercialOffers,
  previewWorkspaceCommercialContract,
  previewWorkspaceCommercialContractRevision,
  publishCommercialOffer,
  reviseWorkspaceCommercialContract,
  revokeCommercialAdjustment,
  revokeEntitlementBonus,
  updateCommercialAdjustment,
  updateEntitlementBonus,
  type CommercialOfferPublishRequest,
} from '@/services/platform-commercial-catalog';

beforeEach(() => http.apiFetch.mockReset());

describe('platform commercial catalog service', () => {
  it('uses paginated real reads and forwards AbortSignal', () => {
    const controller = new AbortController();
    listCommercialOffers({ limit: 20, offset: 40, publicOnly: true, signal: controller.signal });
    listCommercialContracts({ limit: 10, offset: 20, signal: controller.signal });

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/commercial/offers?limit=20&offset=40&publicOnly=true',
      { signal: controller.signal },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/commercial/contracts?limit=10&offset=20',
      { signal: controller.signal },
    );
  });

  it('protects bootstrap and immutable publication with idempotency keys', () => {
    bootstrapCommercialOffers('catalog-bootstrap-1');
    expect(http.apiFetch).toHaveBeenLastCalledWith('/api/v1/platform/commercial/offers/bootstrap', {
      method: 'POST',
      headers: { 'Idempotency-Key': 'catalog-bootstrap-1' },
    });

    const payload = {
      sku: 'crm_start',
      family: 'crm',
      kind: 'base_plan',
      legacyTier: 'starter',
      reason: 'Nova revisão aprovada.',
      snapshot: { schemaVersion: '2.0', entitlements: {} },
    } as unknown as CommercialOfferPublishRequest;
    publishCommercialOffer(payload, 'synthetic-idempotency-key-for-test');
    const [path, init] = http.apiFetch.mock.calls[1];
    expect(path).toBe('/api/v1/platform/commercial/offers/publish');
    expect(init.headers).toEqual({
      'Idempotency-Key': 'synthetic-idempotency-key-for-test',
    });
    expect(JSON.parse(init.body)).toEqual(payload);
  });

  it('keeps account preview separate from contract creation and reads effective state', () => {
    const payload = {
      workspaceId: 'workspace-1',
      lines: [{ sku: 'crm_advanced', quantity: 1, billingInterval: 'monthly' as const, entitlementOverrides: {} }],
    };
    previewWorkspaceCommercialContract(payload);
    getWorkspaceCommercialContract('workspace/1');
    getWorkspaceCapabilityManifest('workspace/1');

    expect(http.apiFetch).toHaveBeenNthCalledWith(1, '/api/v1/platform/commercial/contracts/preview', {
      method: 'POST', body: JSON.stringify(payload),
    });
    expect(http.apiFetch).toHaveBeenNthCalledWith(2, '/api/v1/platform/commercial/workspaces/workspace%2F1/contract', { signal: undefined });
    expect(http.apiFetch).toHaveBeenNthCalledWith(3, '/api/v1/platform/commercial/workspaces/workspace%2F1/manifest', { signal: undefined });
  });

  it('sends adjustment and bonus commands to typed account resources', () => {
    const adjustment = {
      type: 'percentage_discount' as const,
      value: 1_000,
      startsAt: '2026-07-30T12:00:00Z',
      stackGroup: 'negociacao',
      stackable: false,
      portability: 'preserve' as const,
      reason: 'Condição comercial aprovada.',
      source: 'manual' as const,
    };
    addCommercialAdjustment('contract/1', adjustment, 'adjustment-1');
    const bonus = {
      entitlement: 'task_participants' as const,
      mode: 'quantity_delta' as const,
      quantity: 5,
      startsAt: '2026-07-30T12:00:00Z',
      renewsEachPeriod: false,
      portability: 'preserve' as const,
      reason: 'Capacidade adicional aprovada.',
      source: 'manual' as const,
    };
    addEntitlementBonus('workspace/1', bonus, 'bonus-1');

    expect(http.apiFetch.mock.calls[0][0]).toBe('/api/v1/platform/commercial/contracts/contract%2F1/adjustments');
    expect(http.apiFetch.mock.calls[0][1].headers).toEqual({ 'Idempotency-Key': 'adjustment-1' });
    expect(http.apiFetch.mock.calls[1][0]).toBe('/api/v1/platform/commercial/workspaces/workspace%2F1/bonuses');
    expect(http.apiFetch.mock.calls[1][1].headers).toEqual({ 'Idempotency-Key': 'bonus-1' });
  });

  it('creates printable scoped idempotency keys inside backend bounds', () => {
    const key = commercialIdempotencyKey('offer-publish');
    expect(key.startsWith('offer-publish-')).toBe(true);
    expect(key.length).toBeLessThanOrEqual(200);
  });

  it('uses version headers and idempotency for revisions, edits and revocations', () => {
    const lines = [{ sku: 'crm_evolution', quantity: 1, billingInterval: 'monthly' as const, entitlementOverrides: {} }];
    previewWorkspaceCommercialContractRevision('contract/1', { expectedVersion: 3, lines });
    reviseWorkspaceCommercialContract('contract/1', 3, { lines, reason: 'Upgrade aprovado.' }, 'revision-1');
    const adjustment = {
      type: 'fixed_discount' as const, value: 1_000, startsAt: '2026-07-30T12:00:00Z',
      stackGroup: 'manual', stackable: false, portability: 'preserve' as const,
      reason: 'Ajuste revisado.', source: 'manual' as const,
    };
    updateCommercialAdjustment('adjustment/1', 2, adjustment, 'adjustment-update-1');
    revokeCommercialAdjustment('adjustment/1', 3, 'Encerrado.', 'adjustment-revoke-1');
    const bonus = {
      entitlement: 'tokens' as const, mode: 'quantity_delta' as const, quantity: 1_000,
      startsAt: '2026-07-30T12:00:00Z', renewsEachPeriod: false,
      portability: 'preserve' as const, reason: 'Bônus revisado.', source: 'manual' as const,
    };
    updateEntitlementBonus('bonus/1', 4, bonus, 'bonus-update-1');
    revokeEntitlementBonus('bonus/1', 5, 'Encerrado.', 'bonus-revoke-1');

    expect(http.apiFetch.mock.calls[0][0]).toContain('/revision-preview');
    expect(http.apiFetch.mock.calls[1][1].headers).toEqual({
      'Idempotency-Key': 'revision-1', 'If-Match': '"3"',
    });
    expect(http.apiFetch.mock.calls[2][1].headers['If-Match']).toBe('"2"');
    expect(http.apiFetch.mock.calls[3][1].headers['If-Match']).toBe('"3"');
    expect(http.apiFetch.mock.calls[4][1].headers['If-Match']).toBe('"4"');
    expect(http.apiFetch.mock.calls[5][1].headers['If-Match']).toBe('"5"');
  });
});
