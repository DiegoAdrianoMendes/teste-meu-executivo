import { apiFetch } from '@/services/http';

const PLATFORM_NOTICES_PATH = '/api/v1/platform/notices';
const NOTICE_INBOX_PATH = '/api/v1/notices/inbox';

export type PlatformNoticeStatus = 'draft' | 'scheduled' | 'published' | 'cancelled';
export type PlatformNoticeChannel = 'in_app' | 'email';
export type PlatformNoticeAudienceKind = 'global' | 'workspaces' | 'users' | 'rule';
export type PlatformNoticeRuleField = 'user_role' | 'workspace_role';
export type PlatformNoticeReceiptState =
  | 'in_app_available'
  | 'email_queued_local'
  | 'email_blocked_missing_address'
  | 'cancelled';
export type PlatformNoticeAuditAction =
  | 'created'
  | 'updated'
  | 'recipient_previewed'
  | 'scheduled'
  | 'published'
  | 'cancelled'
  | 'read'
  | 'acknowledged';

export type PlatformNoticeUserRole =
  | 'senior_lawyer'
  | 'lawyer'
  | 'intern'
  | 'ai_analyst'
  | 'ai_drafter'
  | 'admin';

export type PlatformNoticeWorkspaceRole =
  | 'workspace_owner'
  | 'workspace_admin'
  | 'legal_manager'
  | 'finance_manager'
  | 'operator'
  | 'viewer';

export interface PlatformNoticeInboxItem {
  receiptId: string;
  noticeId: string;
  title: string;
  body: string;
  requiresAcknowledgement: boolean;
  publishedAt: string;
  readAt: string | null;
  acknowledgedAt: string | null;
}

export interface PlatformNoticeInbox {
  items: PlatformNoticeInboxItem[];
  total: number;
  unreadCount: number;
  pendingAcknowledgementCount: number;
}

export type PlatformNoticeRuleValue = PlatformNoticeUserRole | PlatformNoticeWorkspaceRole;

export type PlatformNoticeRule =
  | { field: 'user_role'; operator: 'in'; values: PlatformNoticeUserRole[] }
  | { field: 'workspace_role'; operator: 'in'; values: PlatformNoticeWorkspaceRole[] };

export interface PlatformNoticeAudience {
  kind: PlatformNoticeAudienceKind;
  workspaceIds: string[];
  userIds: string[];
  rule: PlatformNoticeRule | null;
}

export interface PlatformNotice {
  id: string;
  title: string;
  body: string;
  status: PlatformNoticeStatus;
  audience: PlatformNoticeAudience;
  channels: PlatformNoticeChannel[];
  requiresAcknowledgement: boolean;
  emailDeliveryMode: 'local_queue_only' | null;
  scheduledAt: string | null;
  publishedAt: string | null;
  cancelledAt: string | null;
  createdByUserId: string | null;
  updatedByUserId: string | null;
  publishedByUserId: string | null;
  cancelledByUserId: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformNoticeList {
  items: PlatformNotice[];
  total: number;
}

export interface PlatformNoticeCreateInput {
  title: string;
  body: string;
  audience: PlatformNoticeAudience;
  channels: PlatformNoticeChannel[];
  requiresAcknowledgement: boolean;
}

export interface PlatformNoticeUpdateInput {
  expectedVersion: number;
  title?: string;
  body?: string;
  audience?: PlatformNoticeAudience;
  channels?: PlatformNoticeChannel[];
  requiresAcknowledgement?: boolean;
}

export interface PlatformNoticeRecipient {
  userId: string;
  name: string;
  emailHint: string | null;
  matchedBy: string;
  workspaceIds: string[];
}

export interface PlatformNoticeRecipientPreview {
  noticeId: string;
  totalRecipients: number;
  emailEligible: number;
  emailBlockedMissingAddress: number;
  sample: PlatformNoticeRecipient[];
  evaluatedAt: string;
}

export interface PlatformNoticeReceipt {
  id: string;
  noticeId: string;
  recipientUserId: string;
  recipientName: string;
  channel: PlatformNoticeChannel;
  state: PlatformNoticeReceiptState;
  deliveryMode: 'in_app_local' | 'local_queue_only';
  externalDeliveryAttempted: false;
  recipientAddressHint: string | null;
  matchedBy: string;
  scopeAllWorkspaces: boolean;
  workspaceIds: string[];
  availableAt: string | null;
  queuedAt: string | null;
  readAt: string | null;
  acknowledgedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformNoticeReceiptList {
  items: PlatformNoticeReceipt[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface PlatformNoticePublication {
  notice: PlatformNotice;
  recipientCount: number;
  receiptCount: number;
  receiptsByState: Record<string, number>;
  emailDeliveryMode: 'local_queue_only' | null;
}

export interface PlatformNoticeDispatch {
  processedCount: number;
  publications: PlatformNoticePublication[];
}

export interface PlatformNoticeAuditEvent {
  id: string;
  noticeId: string;
  actorUserId: string | null;
  actorUserName: string | null;
  action: PlatformNoticeAuditAction;
  details: Record<string, unknown>;
  occurredAt: string;
}

export interface PlatformNoticeAuditEventPage {
  items: PlatformNoticeAuditEvent[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export function fetchPlatformNoticeInbox(limit = 50): Promise<PlatformNoticeInbox> {
  const safeLimit = boundedInteger(limit, 50, 1, 200);
  return apiFetch<PlatformNoticeInbox>(`${NOTICE_INBOX_PATH}?offset=0&limit=${safeLimit}`);
}

export function markPlatformNoticeRead(receiptId: string): Promise<PlatformNoticeInboxItem> {
  return apiFetch<PlatformNoticeInboxItem>(`${NOTICE_INBOX_PATH}/${encodeURIComponent(receiptId)}/read`, {
    method: 'PUT',
  });
}

export function acknowledgePlatformNotice(receiptId: string): Promise<PlatformNoticeInboxItem> {
  return apiFetch<PlatformNoticeInboxItem>(`${NOTICE_INBOX_PATH}/${encodeURIComponent(receiptId)}/acknowledgement`, {
    method: 'PUT',
  });
}

export interface PlatformNoticeListFilters {
  offset?: number;
  limit?: number;
  status?: PlatformNoticeStatus;
}

function boundedInteger(value: number | undefined, fallback: number, minimum: number, maximum: number): number {
  if (value === undefined || !Number.isFinite(value)) return fallback;
  return Math.max(minimum, Math.min(maximum, Math.trunc(value)));
}

function operationKey(operation: string): string {
  const randomId = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `notice-${operation}-${randomId}`;
}

function mutationHeaders(operation: string, idempotencyKey?: string): HeadersInit {
  return { 'Idempotency-Key': idempotencyKey ?? operationKey(operation) };
}

function noticePath(noticeId: string): string {
  return `${PLATFORM_NOTICES_PATH}/${encodeURIComponent(noticeId)}`;
}

export function listPlatformNotices(
  filters: PlatformNoticeListFilters = {},
): Promise<PlatformNoticeList> {
  const params = new URLSearchParams({
    offset: String(boundedInteger(filters.offset, 0, 0, Number.MAX_SAFE_INTEGER)),
    limit: String(boundedInteger(filters.limit, 50, 1, 200)),
  });
  if (filters.status) params.set('status', filters.status);
  return apiFetch<PlatformNoticeList>(`${PLATFORM_NOTICES_PATH}?${params.toString()}`);
}

export function getPlatformNotice(noticeId: string): Promise<PlatformNotice> {
  return apiFetch<PlatformNotice>(noticePath(noticeId));
}

export function createPlatformNotice(
  payload: PlatformNoticeCreateInput,
  idempotencyKey?: string,
): Promise<PlatformNotice> {
  return apiFetch<PlatformNotice>(PLATFORM_NOTICES_PATH, {
    method: 'POST',
    headers: mutationHeaders('create', idempotencyKey),
    body: JSON.stringify(payload),
  });
}

export function updatePlatformNotice(
  noticeId: string,
  payload: PlatformNoticeUpdateInput,
  idempotencyKey?: string,
): Promise<PlatformNotice> {
  return apiFetch<PlatformNotice>(noticePath(noticeId), {
    method: 'PATCH',
    headers: mutationHeaders('update', idempotencyKey),
    body: JSON.stringify(payload),
  });
}

export function previewPlatformNoticeRecipients(
  noticeId: string,
  sampleLimit = 50,
): Promise<PlatformNoticeRecipientPreview> {
  const safeLimit = boundedInteger(sampleLimit, 50, 1, 200);
  return apiFetch<PlatformNoticeRecipientPreview>(
    `${noticePath(noticeId)}/recipients/preview?sampleLimit=${safeLimit}`,
  );
}

export function schedulePlatformNotice(
  noticeId: string,
  scheduledAt: string,
  idempotencyKey?: string,
): Promise<PlatformNotice> {
  return apiFetch<PlatformNotice>(`${noticePath(noticeId)}/schedule`, {
    method: 'POST',
    headers: mutationHeaders('schedule', idempotencyKey),
    body: JSON.stringify({ scheduledAt }),
  });
}

export function publishPlatformNotice(
  noticeId: string,
  idempotencyKey?: string,
): Promise<PlatformNoticePublication> {
  return apiFetch<PlatformNoticePublication>(`${noticePath(noticeId)}/publish`, {
    method: 'POST',
    headers: mutationHeaders('publish', idempotencyKey),
  });
}

export function cancelPlatformNotice(
  noticeId: string,
  idempotencyKey?: string,
): Promise<PlatformNotice> {
  return apiFetch<PlatformNotice>(`${noticePath(noticeId)}/cancel`, {
    method: 'POST',
    headers: mutationHeaders('cancel', idempotencyKey),
  });
}

export function dispatchDuePlatformNotices(
  limit = 100,
  idempotencyKey?: string,
): Promise<PlatformNoticeDispatch> {
  const safeLimit = boundedInteger(limit, 100, 1, 500);
  return apiFetch<PlatformNoticeDispatch>(`${PLATFORM_NOTICES_PATH}/dispatch-due?limit=${safeLimit}`, {
    method: 'POST',
    headers: mutationHeaders('dispatch-due', idempotencyKey),
  });
}

export function listPlatformNoticeReceipts(
  noticeId: string,
  offset = 0,
  limit = 100,
): Promise<PlatformNoticeReceiptList> {
  const params = new URLSearchParams({
    offset: String(boundedInteger(offset, 0, 0, Number.MAX_SAFE_INTEGER)),
    limit: String(boundedInteger(limit, 100, 1, 500)),
  });
  return apiFetch<PlatformNoticeReceiptList>(`${noticePath(noticeId)}/receipts?${params.toString()}`);
}

export function listPlatformNoticeAudit(
  noticeId: string,
  offset = 0,
  limit = 50,
): Promise<PlatformNoticeAuditEventPage> {
  const params = new URLSearchParams({
    offset: String(boundedInteger(offset, 0, 0, Number.MAX_SAFE_INTEGER)),
    limit: String(boundedInteger(limit, 50, 1, 200)),
  });
  return apiFetch<PlatformNoticeAuditEventPage>(`${noticePath(noticeId)}/audit/page?${params.toString()}`);
}
