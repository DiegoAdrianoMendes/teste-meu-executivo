import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  createPlanPolicyIdempotencyKey,
  listPlanPolicies,
  listPlanPoliciesPage,
  listPlanPolicyVersionsPage,
  previewPlanPolicyRollout,
  publishPlanPolicy,
  savePlanPolicyDraft,
  type PlanPolicySnapshot,
} from '@/services/plan-policies';

const snapshot = {
  schemaVersion: '1.0',
  name: 'Profissional',
  description: null,
  isActive: true,
  isPublic: true,
  recommended: false,
  sortOrder: 2,
  currency: 'BRL',
  priceMonthlyCents: 69700,
  priceAnnuallyCents: 669120,
  supportTier: 'standard',
  integrations: [],
  highlights: [],
  parameters: {
    maxMatters: null,
    maxWorkspaces: 1,
    maxFilesPerConversation: 20,
    maxOperationalRecipients: 10,
    maxCampaignRecipientsPerMonth: 1_000,
  },
  entitlements: {},
} as PlanPolicySnapshot;

beforeEach(() => {
  http.apiFetch.mockReset();
});

describe('plan-policies service', () => {
  it('uses the platform contract and forwards AbortSignal on reads', () => {
    const controller = new AbortController();
    listPlanPolicies(controller.signal);

    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/platform/plan-policies', {
      signal: controller.signal,
    });
  });

  it('uses paginated plan and version contracts', () => {
    const controller = new AbortController();
    listPlanPoliciesPage(10, 20, controller.signal);
    listPlanPolicyVersionsPage('plan-1', 10, 30, controller.signal);

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/plan-policies/page?limit=10&offset=20',
      { signal: controller.signal },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/plan-policies/plan-1/versions/page?limit=10&offset=30',
      { signal: controller.signal },
    );
  });

  it('sends If-Match only when updating an existing draft', () => {
    savePlanPolicyDraft('plan-1', snapshot, 'draft-etag-1');

    const [path, init] = http.apiFetch.mock.calls[0];
    expect(path).toBe('/api/v1/platform/plan-policies/plan-1/draft');
    expect(init.method).toBe('PUT');
    expect((init.headers as Headers).get('If-Match')).toBe('draft-etag-1');
    expect(JSON.parse(init.body)).toEqual({ snapshot });

    http.apiFetch.mockClear();
    savePlanPolicyDraft('plan-1', snapshot);
    expect((http.apiFetch.mock.calls[0][1].headers as Headers).has('If-Match')).toBe(false);
  });

  it('keeps preview and publication payloads separate and protects publish with a key', () => {
    const rollout = { rolloutMode: 'existing_and_new' as const, sampleLimit: 10 };
    previewPlanPolicyRollout('plan-1', rollout);
    expect(http.apiFetch).toHaveBeenLastCalledWith(
      '/api/v1/platform/plan-policies/plan-1/preview',
      { method: 'POST', body: JSON.stringify(rollout) },
    );

    const publication = {
      rolloutMode: 'existing_and_new' as const,
      draftEtag: 'draft-etag-1',
      reason: 'Ajuste de franquias do plano.',
    };
    publishPlanPolicy('plan-1', publication, 'plan-policy-request-1');

    const [path, init] = http.apiFetch.mock.calls[1];
    expect(path).toBe('/api/v1/platform/plan-policies/plan-1/publish');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Idempotency-Key': 'plan-policy-request-1' });
    expect(JSON.parse(init.body)).toEqual(publication);
  });

  it('creates printable idempotency keys accepted by the backend bounds', () => {
    const key = createPlanPolicyIdempotencyKey();
    expect(key.startsWith('plan-policy-')).toBe(true);
    expect(key.length).toBeGreaterThanOrEqual(8);
    expect(key.length).toBeLessThanOrEqual(200);
  });
});
