import { apiFetch } from '@/services/http';

export type BenefitUnit = 'days' | 'months' | 'tokens' | 'percentage' | 'cents';
export type BenefitStatus = 'pending' | 'available' | 'applied' | 'reversed' | 'expired';
export type CouponStatus = 'draft' | 'active' | 'paused' | 'expired' | 'archived';

export interface ReferralPolicyRules {
  rewardMonthsByPlan: Record<string, number>;
  welcomeBenefit: 'days' | 'tokens';
  welcomeAmount: number;
  holdDaysAfterFirstPayment: number;
  partnerMinActiveReferrals: number;
  partnerMinReferredMrrCents: number;
  partnerReviewMonths: number;
}

export interface ReferralProfile {
  id: string;
  workspaceId: string;
  code: string;
  active: boolean;
  link: string;
  createdAt: string;
}

export interface BenefitGrant {
  id: string;
  workspaceId: string;
  benefitType: string;
  unit: BenefitUnit;
  amount: string;
  status: BenefitStatus;
  sourceType: string;
  sourceRef: string | null;
  reason: string;
  ticketId: string | null;
  approvedAt: string | null;
  appliedAt: string | null;
  expiresAt: string | null;
  reversedAt: string | null;
  createdAt: string;
}

export interface BenefitLedgerEntry {
  id: string;
  grantId: string;
  workspaceId?: string;
  eventType: string;
  unit: BenefitUnit;
  delta: string;
  reason: string;
  ticketId: string | null;
  actorUserId?: string | null;
  createdAt: string;
}

export interface WorkspaceBenefitsOverview {
  profile: ReferralProfile | null;
  referrals: Array<{
    id: string;
    referredWorkspaceId: string;
    referredWorkspaceName: string;
    status: string;
    planCode: string | null;
    firstPaymentAt: string | null;
    holdUntil: string | null;
    rewardedAt: string | null;
    createdAt: string;
  }>;
  benefits: BenefitGrant[];
  ledger: BenefitLedgerEntry[];
  partner: {
    status: string;
    tier: string;
    activeReferrals: number;
    referredMrrCents: number;
    nextReviewAt: string | null;
    exemptionEndsAt: string | null;
  } | null;
  couponRedemptions: Array<{
    id: string;
    code: string;
    name: string;
    status: string;
    redeemedAt: string;
  }>;
  policy: ReferralPolicyRules;
}

export interface PlatformBenefitsOverview {
  referrals: Record<string, number>;
  benefits: Record<string, number>;
  activePartners: number;
  activeCoupons: number;
  couponRedemptions: number;
}

export interface PlatformPolicySnapshot {
  id?: string;
  version: number;
  status: string;
  rules: ReferralPolicyRules;
  reason: string | null;
  effectiveAt: string | null;
  createdByUserId?: string | null;
  createdAt?: string;
}

export interface PlatformReferral {
  id: string;
  referrerWorkspaceId: string;
  referredWorkspaceId: string;
  referredWorkspaceName: string;
  status: string;
  planCode: string | null;
  firstPaymentAt: string | null;
  holdUntil: string | null;
  rewardedAt: string | null;
}

export interface PartnerMembership {
  workspaceId: string;
  workspaceName: string;
  status: string;
  activeReferrals: number;
  referredMrrCents: number;
  nextReviewAt: string | null;
  exemptionEndsAt: string | null;
}

export interface Coupon {
  id: string;
  code: string;
  name: string;
  description: string;
  status: CouponStatus;
  benefitType: string;
  unit: BenefitUnit;
  amount: string;
  allowedPlanCodes: string[];
  totalLimit: number | null;
  perWorkspaceLimit: number;
  redeemedCount: number;
  autoApply: boolean;
  startsAt: string | null;
  endsAt: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface CouponWritePayload {
  code: string;
  name: string;
  description: string;
  benefitType: string;
  unit: BenefitUnit;
  amount: number;
  allowedPlanCodes: string[];
  totalLimit: number | null;
  perWorkspaceLimit: number;
  autoApply: boolean;
  startsAt: string | null;
  endsAt: string | null;
  reason: string;
  ticketId: string | null;
}

export interface CouponRedemption {
  id: string;
  couponId: string;
  couponCode: string;
  workspaceId: string;
  workspaceName: string;
  status: string;
  redeemedAt: string;
}

export interface CouponAuditEntry {
  id: string;
  action: string;
  actorUserId: string | null;
  ticketId: string | null;
  reason: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  createdAt: string;
}

export interface SaasBenefitsPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

function pageQuery(input: {
  workspaceId?: string;
  search?: string;
  status?: string;
  eventType?: string;
  limit?: number;
  offset?: number;
} = {}): string {
  const query = new URLSearchParams({
    limit: String(input.limit ?? 50),
    offset: String(input.offset ?? 0),
  });
  if (input.workspaceId) query.set('workspaceId', input.workspaceId);
  if (input.search?.trim()) query.set('search', input.search.trim());
  if (input.status) query.set('status', input.status);
  if (input.eventType) query.set('eventType', input.eventType);
  return query.toString();
}

function jsonRequest(method: string, body: unknown, idempotencyKey?: string): RequestInit {
  return {
    method,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    body: JSON.stringify(body),
  };
}

export const saasBenefitsApi = {
  workspaceOverview: () => apiFetch<WorkspaceBenefitsOverview>('/api/v1/saas-benefits/me'),
  createProfile: () => apiFetch<ReferralProfile>('/api/v1/saas-benefits/profile', { method: 'POST' }),
  claimReferral: (code: string, idempotencyKey: string) => apiFetch<{ id: string; status: string }>(
    '/api/v1/saas-benefits/referrals/claim',
    jsonRequest('POST', { code, idempotencyKey }, idempotencyKey),
  ),
  redeemCoupon: (code: string, idempotencyKey: string) => apiFetch<{ id: string; status: string; benefitGrantId: string }>(
    '/api/v1/saas-benefits/coupons/redeem',
    jsonRequest('POST', { code, idempotencyKey }, idempotencyKey),
  ),
  platformOverview: () => apiFetch<PlatformBenefitsOverview>('/api/v1/platform/saas-benefits/overview'),
  policy: () => apiFetch<PlatformPolicySnapshot>('/api/v1/platform/saas-benefits/policy'),
  policyHistoryPage: (input: { limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<PlatformPolicySnapshot>>(
    `/api/v1/platform/saas-benefits/policy/history/page?${pageQuery(input)}`,
  ),
  publishPolicy: (rules: ReferralPolicyRules, reason: string) => apiFetch<{ id: string; version: number; status: string }>(
    '/api/v1/platform/saas-benefits/policy',
    jsonRequest('POST', { rules, reason }),
  ),
  referralsPage: (input: { status?: string; limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<PlatformReferral>>(
    `/api/v1/platform/saas-benefits/referrals/page?${pageQuery(input)}`,
  ),
  processEligible: () => apiFetch<{ processed: number; ids: string[] }>(
    '/api/v1/platform/saas-benefits/referrals/process-eligible',
    { method: 'POST' },
  ),
  recordFirstPayment: (
    attributionId: string,
    payload: { paidAt: string; planCode: string; paymentReference: string },
  ) => apiFetch<{ id: string; status: string; holdUntil: string }>(
    `/api/v1/platform/saas-benefits/referrals/${encodeURIComponent(attributionId)}/first-payment`,
    jsonRequest('POST', payload),
  ),
  grantsPage: (input: { workspaceId?: string; status?: string; limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<BenefitGrant>>(
    `/api/v1/platform/saas-benefits/grants/page?${pageQuery(input)}`,
  ),
  ledgerPage: (input: { workspaceId?: string; eventType?: string; limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<BenefitLedgerEntry>>(
    `/api/v1/platform/saas-benefits/ledger/page?${pageQuery(input)}`,
  ),
  createGrant: (payload: {
    workspaceId: string;
    benefitType: string;
    unit: BenefitUnit;
    amount: number;
    reason: string;
    idempotencyKey: string;
    ticketId: string | null;
    sourceType: string;
    sourceRef?: string | null;
    expiresAt?: string | null;
  }) => apiFetch<BenefitGrant>(
    '/api/v1/platform/saas-benefits/grants',
    jsonRequest('POST', payload, payload.idempotencyKey),
  ),
  createAndApplyGrant: (payload: {
    workspaceId: string;
    benefitType: string;
    unit: BenefitUnit;
    amount: number;
    reason: string;
    idempotencyKey: string;
    ticketId: string | null;
    sourceType: string;
    sourceRef?: string | null;
    expiresAt?: string | null;
  }) => apiFetch<BenefitGrant>(
    '/api/v1/platform/saas-benefits/grants/apply-now',
    jsonRequest('POST', payload, payload.idempotencyKey),
  ),
  decideGrant: (grantId: string, action: 'approve' | 'apply' | 'reverse', reason: string, ticketId: string | null) => apiFetch<BenefitGrant>(
    `/api/v1/platform/saas-benefits/grants/${encodeURIComponent(grantId)}/${action}`,
    jsonRequest('POST', { reason, ticketId }),
  ),
  partnersPage: (input: { status?: string; limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<PartnerMembership>>(
    `/api/v1/platform/saas-benefits/partners/page?${pageQuery(input)}`,
  ),
  evaluatePartner: (workspaceId: string) => apiFetch<PartnerMembership>(
    `/api/v1/platform/saas-benefits/partners/${encodeURIComponent(workspaceId)}/evaluate`,
    { method: 'POST' },
  ),
  couponsPage: (input: { search?: string; status?: string; limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<Coupon>>(
    `/api/v1/platform/saas-benefits/coupons/page?${pageQuery(input)}`,
  ),
  createCoupon: (payload: CouponWritePayload) => apiFetch<Coupon>(
    '/api/v1/platform/saas-benefits/coupons',
    jsonRequest('POST', payload),
  ),
  updateCoupon: (couponId: string, payload: CouponWritePayload, expectedVersion: number) => apiFetch<Coupon>(
    `/api/v1/platform/saas-benefits/coupons/${encodeURIComponent(couponId)}`,
    jsonRequest('PUT', { ...payload, expectedVersion }),
  ),
  changeCouponStatus: (coupon: Coupon, status: CouponStatus, reason: string, ticketId: string | null) => apiFetch<Coupon>(
    `/api/v1/platform/saas-benefits/coupons/${encodeURIComponent(coupon.id)}/status`,
    jsonRequest('POST', { status, expectedVersion: coupon.version, reason, ticketId }),
  ),
  couponRedemptionsPage: (input: { search?: string; status?: string; limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<CouponRedemption>>(
    `/api/v1/platform/saas-benefits/coupons/redemptions/page?${pageQuery(input)}`,
  ),
  couponAuditPage: (couponId: string, input: { limit?: number; offset?: number } = {}) => apiFetch<SaasBenefitsPage<CouponAuditEntry>>(
    `/api/v1/platform/saas-benefits/coupons/${encodeURIComponent(couponId)}/audit/page?${pageQuery(input)}`,
  ),
};
