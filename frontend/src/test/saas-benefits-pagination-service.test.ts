import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import { saasBenefitsApi } from '@/services/saas-benefits';

beforeEach(() => {
  http.apiFetch.mockReset();
});

describe('benefits and coupons server pagination', () => {
  it('uses typed page contracts for benefits and the immutable ledger', () => {
    saasBenefitsApi.grantsPage({ workspaceId: 'workspace-1', status: 'pending', limit: 20, offset: 40 });
    saasBenefitsApi.ledgerPage({ workspaceId: 'workspace-1', eventType: 'applied', limit: 20, offset: 20 });

    expect(http.apiFetch.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/platform/saas-benefits/grants/page?limit=20&offset=40&workspaceId=workspace-1&status=pending',
      '/api/v1/platform/saas-benefits/ledger/page?limit=20&offset=20&workspaceId=workspace-1&eventType=applied',
    ]);
  });

  it('uses typed page contracts for coupons and redemptions', () => {
    saasBenefitsApi.couponsPage({ search: '  lançamento  ', status: 'active', limit: 25, offset: 0 });
    saasBenefitsApi.couponRedemptionsPage({ search: '  conta  ', limit: 25, offset: 25 });

    expect(http.apiFetch.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/platform/saas-benefits/coupons/page?limit=25&offset=0&search=lan%C3%A7amento&status=active',
      '/api/v1/platform/saas-benefits/coupons/redemptions/page?limit=25&offset=25&search=conta',
    ]);
  });

  it('paginates policy history, referrals, Partners and coupon audit', () => {
    saasBenefitsApi.policyHistoryPage({ limit: 10, offset: 10 });
    saasBenefitsApi.referralsPage({ status: 'registered', limit: 10, offset: 20 });
    saasBenefitsApi.partnersPage({ status: 'candidate', limit: 10, offset: 30 });
    saasBenefitsApi.couponAuditPage('coupon-1', { limit: 10, offset: 40 });

    expect(http.apiFetch.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/platform/saas-benefits/policy/history/page?limit=10&offset=10',
      '/api/v1/platform/saas-benefits/referrals/page?limit=10&offset=20&status=registered',
      '/api/v1/platform/saas-benefits/partners/page?limit=10&offset=30&status=candidate',
      '/api/v1/platform/saas-benefits/coupons/coupon-1/audit/page?limit=10&offset=40',
    ]);
  });
});
