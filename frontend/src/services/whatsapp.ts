import type {
  AiMode,
  ConversationAssignee,
  ConversationInternalNote,
  LeadDrawerData,
  MessageStatus,
  MessageOptimizationResult,
  MessageTemplate,
  WhatsAppConnectionHealth,
  WhatsAppConnectionSummary,
  WhatsAppConversation,
  WhatsAppManualConnectionPayload,
  WhatsAppMessage,
  WhatsAppWebhookInfo,
  MetaEmbeddedSignupConfig,
} from '@/types';
import { ApiError, apiFetch } from '@/services/http';
import { mapMatter } from '@/services/matters';
import { requireWhatsAppPhone } from '@/lib/whatsapp-phone';

type ApiContact = Record<string, any>;
type ApiConversation = Record<string, any>;
type ApiMessage = Record<string, any>;

export interface WhatsAppMetaWebhookField {
  field: string;
  category: string;
  severity: 'info' | 'warning' | 'critical' | string;
  implementation: 'native' | 'semantic_projection' | 'domain_projection';
  recommended: boolean;
  sprint: number;
  destination: string;
  description: string;
  operatorAction: string;
  observationState: 'observed' | 'not_observed';
  eventCount: number;
  lastObservedAt: string | null;
  lastSummary: Record<string, unknown> | string | null;
  resourceType?: string | null;
  appHref?: string | null;
  blackboxHref?: string | null;
  stateCode?: string | null;
  receiptCounts?: Record<string, number>;
  desiredSubscription?: boolean;
  subscriptionCount?: number;
  applicability?: 'app_level' | 'waba_level' | 'asset_dependent' | 'manual_only' | string;
  providerStates?: string[];
  automation?: {
    enabled: boolean;
    mode: 'observe_only' | 'suggest_action' | 'auto_reversible' | 'approval_each_time';
    actionKey: string | null;
    version: number;
  };
}

export interface WhatsAppMetaWebhookReceipt {
  id: string;
  workspaceId?: string;
  connectionId: string;
  field: string;
  status: string;
  attemptCount: number;
  projectionType: string | null;
  sanitizedErrorCode: string | null;
  observedAt: string;
  processedAt: string | null;
  retryAvailable: boolean;
  retryManagedBy: 'receipt' | 'provider_envelope';
}

export interface WhatsAppMetaOperationalEvent {
  id: string;
  workspaceId: string | null;
  connectionId: string | null;
  field: string;
  category: string;
  severity: string;
  implementation: string;
  summary: string;
  payload: Record<string, unknown>;
  occurredAt: string;
}

export interface WhatsAppMetaOperations {
  catalog: WhatsAppMetaWebhookField[];
  events: WhatsAppMetaOperationalEvent[];
  eventCount: number;
  latestEventAt: string | null;
  receipts?: WhatsAppMetaWebhookReceipt[];
  receiptCount?: number;
  latestReceiptAt?: string | null;
}

export interface WhatsAppMetaSubscriptionJob {
  id: string;
  status: 'queued' | 'running' | 'retrying' | 'completed' | 'completed_with_errors' | 'failed';
  attemptedCount: number;
  succeededCount: number;
  failedCount: number;
  resultSummary: Record<string, unknown>;
  sanitizedErrorCode: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
}

const pendingOutboundKeys = new Map<string, string>();
const MAX_PENDING_OUTBOUND_KEYS = 100;
const PENDING_OUTBOUND_STORAGE_KEY = 'meuexecutivo:whatsapp-outbound-idempotency:v1';
let pendingOutboundKeysHydrated = false;

function persistPendingOutboundKeys(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(
      PENDING_OUTBOUND_STORAGE_KEY,
      JSON.stringify(Array.from(pendingOutboundKeys.entries())),
    );
  } catch {
    // Memory remains the safe fallback when storage is unavailable or full.
  }
}

function hydratePendingOutboundKeys(): void {
  if (pendingOutboundKeysHydrated) return;
  pendingOutboundKeysHydrated = true;
  if (typeof window === 'undefined') return;
  try {
    const raw = window.sessionStorage.getItem(PENDING_OUTBOUND_STORAGE_KEY);
    const entries: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(entries)) return;
    for (const entry of entries.slice(-MAX_PENDING_OUTBOUND_KEYS)) {
      if (
        Array.isArray(entry)
        && entry.length === 2
        && typeof entry[0] === 'string'
        && typeof entry[1] === 'string'
        && entry[0].length <= 160
        && entry[1].length <= 200
      ) {
        pendingOutboundKeys.set(entry[0], entry[1]);
      }
    }
  } catch {
    // Invalid or inaccessible storage must not block the operator.
  }
}

function nonSensitiveFingerprint(value: unknown): string {
  const serialized = JSON.stringify(value);
  let hash = 2166136261;
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function newIdempotencyKey(prefix: string): string {
  const randomPart = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}:${randomPart}`;
}

function outboundResultIsConclusive(result: unknown): boolean {
  if (!result || typeof result !== 'object') return true;
  const record = result as Record<string, unknown>;
  const statuses: unknown[] = [record.status];
  if (Array.isArray(record.messages) && record.messages.length > 0) {
    const lastMessage = record.messages[record.messages.length - 1];
    if (lastMessage && typeof lastMessage === 'object') {
      statuses.push((lastMessage as Record<string, unknown>).status);
    }
  }
  return !statuses.some((status) => (
    status === 'pending' || status === 'delivery_uncertain'
  ));
}

async function withStableOutboundKey<T>(
  operation: string,
  intent: unknown,
  execute: (idempotencyKey: string) => Promise<T>,
): Promise<T> {
  hydratePendingOutboundKeys();
  const fingerprint = `${operation}:${nonSensitiveFingerprint(intent)}`;
  let idempotencyKey = pendingOutboundKeys.get(fingerprint);
  if (!idempotencyKey) {
    if (pendingOutboundKeys.size >= MAX_PENDING_OUTBOUND_KEYS) {
      const oldest = pendingOutboundKeys.keys().next().value as string | undefined;
      if (oldest) pendingOutboundKeys.delete(oldest);
    }
    idempotencyKey = newIdempotencyKey(operation);
    pendingOutboundKeys.set(fingerprint, idempotencyKey);
    persistPendingOutboundKeys();
  }
  // Rejections intentionally leave the key in the map. A repeated click after
  // timeout/5xx must reconcile the first provider attempt, not create another.
  const result = await execute(idempotencyKey);
  // A replay may answer HTTP 200 while the durable message remains pending or
  // delivery_uncertain. Keep the key in that case for the same reason.
  if (outboundResultIsConclusive(result)) {
    pendingOutboundKeys.delete(fingerprint);
    persistPendingOutboundKeys();
  }
  return result;
}

function mapStatus(status?: string): MessageStatus {
  if (status === 'pending' || status === 'delivery_uncertain' || status === 'sent' || status === 'delivered' || status === 'read' || status === 'failed') {
    return status;
  }
  return 'sent';
}

function mapMessage(message: ApiMessage, contactId: string): WhatsAppMessage {
  const direction = message.direction ?? 'outbound';
  return {
    id: message.id,
    conversationId: contactId,
    senderId: message.sentById ?? message.sent_by_id ?? contactId,
    text: message.body ?? '',
    timestamp: message.timestamp,
    status: mapStatus(message.status),
    isFromClient: direction === 'inbound',
    sensitiveDataHidden: Boolean(message.sensitiveDataHidden ?? message.sensitive_data_hidden),
    messageType: message.mediaType ?? message.media_type ?? 'text',
    transcript: message.transcript ?? undefined,
    imageTranscript: message.imageTranscript ?? message.image_transcript ?? undefined,
    mediaUrl: message.mediaUrl ?? message.media_url ?? undefined,
  };
}

function deriveAiMode(contact: ApiContact): AiMode {
  const queueStatus = contact.queueStatus ?? contact.queue_status ?? 'ai';
  const controlState = contact.humanControlState ?? contact.human_control_state ?? 'copilot';
  if (queueStatus === 'ai') {
    return 'active';
  }
  if (controlState === 'manual_off') {
    return 'off';
  }
  return 'paused';
}

function mapConversationSummary(contact: ApiContact): WhatsAppConversation {
  return {
    id: contact.id,
    clientId: contact.id,
    clientName: contact.name ?? contact.phone ?? 'Contato sem nome',
    clientPhone: contact.phone ?? '',
    clientAvatar: contact.avatarUrl ?? contact.avatar_url,
    cardId: contact.matterId ?? contact.matter_id,
    whatsappConnectionId: contact.whatsappConnectionId ?? contact.whatsapp_connection_id,
    messages: [],
    lastMessage: contact.lastMessage ?? '',
    lastMessageTime: contact.lastMessageTime ?? contact.last_seen ?? new Date().toISOString(),
    unreadCount: contact.unreadCount ?? 0,
    aiMode: deriveAiMode(contact),
    isOnline: false,
    assignedUserId: contact.assignedUserId ?? contact.assigned_user_id ?? null,
    assignedUserName: contact.assignedUserName ?? contact.assigned_user_name ?? null,
    queueStatus: contact.queueStatus ?? contact.queue_status ?? 'ai',
    humanControlState: contact.humanControlState ?? contact.human_control_state ?? 'copilot',
    activeFlowTemplateId: contact.activeFlowTemplateId ?? contact.active_flow_template_id ?? null,
    activeFlowLabel: contact.activeFlowLabel ?? contact.active_flow_label ?? null,
    nextFollowUpAt: contact.nextFollowUpAt ?? contact.next_follow_up_at ?? null,
    sensitiveDataHidden: Boolean(contact.sensitiveDataHidden ?? contact.sensitive_data_hidden),
  };
}

export async function fetchWhatsAppContacts(search?: string): Promise<WhatsAppConversation[]> {
  const params = new URLSearchParams();
  if (search?.trim()) {
    params.set('search', search.trim());
  }
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const contacts = await apiFetch<ApiContact[]>(`/api/v1/whatsapp/contacts${suffix}`);
  return contacts.map((contact) => mapConversationSummary(contact));
}

export async function fetchWhatsAppConversation(contactId: string): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/contacts/${contactId}`);
  const summary = mapConversationSummary(data.contact ?? {});
  return {
    ...summary,
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, summary.id)),
    lastMessage: summary.lastMessage || (data.messages?.length ? data.messages[data.messages.length - 1].body ?? '' : ''),
    lastMessageTime: summary.lastMessageTime || (data.messages?.length ? data.messages[data.messages.length - 1].timestamp : new Date().toISOString()),
  };
}

function mapNote(note: Record<string, any>): ConversationInternalNote {
  return {
    id: note.id,
    contactId: note.contactId ?? note.contact_id,
    matterId: note.matterId ?? note.matter_id ?? null,
    authorUserId: note.authorUserId ?? note.author_user_id ?? null,
    authorName: note.authorName ?? note.author_name ?? null,
    body: note.body ?? '',
    mentions: note.mentions ?? [],
    noteMetadata: note.noteMetadata ?? note.note_metadata ?? {},
    createdAt: note.createdAt ?? note.created_at,
  };
}

export async function fetchConversationDrawer(contactId: string): Promise<LeadDrawerData> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/whatsapp/conversations/${contactId}/drawer`);
  return {
    contact: mapConversationSummary(data.contact ?? {}),
    matter: data.matter ? mapMatter(data.matter) : null,
    notes: (data.notes ?? []).map((item: Record<string, any>) => mapNote(item)),
    recentMeetings: data.recentMeetings ?? data.recent_meetings ?? [],
    canEditMatter: Boolean(data.canEditMatter ?? data.can_edit_matter),
    canManageConversation: Boolean(data.canManageConversation ?? data.can_manage_conversation),
  };
}

export async function fetchConversationNotes(contactId: string): Promise<ConversationInternalNote[]> {
  const data = await apiFetch<Record<string, any>[]>(`/api/v1/whatsapp/conversations/${contactId}/notes`);
  return data.map(mapNote);
}

export async function createConversationNote(
  contactId: string,
  payload: { body: string; mentions?: string[] },
): Promise<ConversationInternalNote> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/whatsapp/conversations/${contactId}/notes`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return mapNote(data);
}

export async function moveConversationStage(
  contactId: string,
  payload: { columnId: string; microStatus?: string | null },
): Promise<LeadDrawerData> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/whatsapp/conversations/${contactId}/move-stage`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return {
    contact: mapConversationSummary(data.contact ?? {}),
    matter: data.matter ? mapMatter(data.matter) : null,
    notes: (data.notes ?? []).map((item: Record<string, any>) => mapNote(item)),
    recentMeetings: data.recentMeetings ?? data.recent_meetings ?? [],
    canEditMatter: Boolean(data.canEditMatter ?? data.can_edit_matter),
    canManageConversation: Boolean(data.canManageConversation ?? data.can_manage_conversation),
  };
}

export async function optimizeWhatsAppDraft(
  body: string,
  tone: string,
): Promise<MessageOptimizationResult> {
  return apiFetch('/api/v1/whatsapp/messages/optimize', {
    method: 'POST',
    body: JSON.stringify({ body, tone }),
  });
}

export async function fetchWhatsAppConnections(): Promise<WhatsAppConnectionSummary[]> {
  return apiFetch('/api/v1/whatsapp/connections');
}

export async function fetchWhatsAppMetaOperations(): Promise<WhatsAppMetaOperations> {
  return apiFetch('/api/v1/whatsapp/meta-operations');
}

export async function previewWhatsAppMetaSubscriptions(): Promise<Record<string, unknown>> {
  return apiFetch('/api/v1/whatsapp/meta-subscriptions/preview', { method: 'POST' });
}

export async function reconcileWhatsAppMetaSubscriptions(): Promise<WhatsAppMetaSubscriptionJob> {
  return apiFetch('/api/v1/whatsapp/meta-subscriptions/reconcile', { method: 'POST' });
}

export async function fetchWhatsAppMetaSubscriptionJob(jobId: string): Promise<WhatsAppMetaSubscriptionJob> {
  return apiFetch(`/api/v1/whatsapp/meta-subscriptions/jobs/${encodeURIComponent(jobId)}`);
}

export async function retryWhatsAppMetaReceipt(receiptId: string): Promise<WhatsAppMetaWebhookReceipt> {
  return apiFetch(`/api/v1/whatsapp/meta-operations/receipts/${encodeURIComponent(receiptId)}/retry`, {
    method: 'POST',
  });
}

export async function updateWhatsAppMetaAutomationPolicy(
  field: string,
  payload: {
    enabled: boolean;
    mode: 'observe_only' | 'suggest_action' | 'auto_reversible' | 'approval_each_time';
    minimumSeverity: 'info' | 'warning' | 'high' | 'critical';
    expectedVersion: number;
  },
): Promise<Record<string, unknown>> {
  return apiFetch(`/api/v1/whatsapp/meta-automation-policies/${encodeURIComponent(field)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchMetaEmbeddedSignupConfig(): Promise<MetaEmbeddedSignupConfig> {
  return apiFetch('/api/v1/tech-providers/config');
}

export async function completeMetaEmbeddedSignup(payload: {
  code: string;
  wabaId?: string;
  phoneNumberId?: string;
  transportMode: 'coexistence';
}): Promise<WhatsAppConnectionSummary> {
  return apiFetch('/api/v1/tech-providers/callback', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/**
 * Reads the environment-wide external-delivery gate without depending on a
 * connection or template record. The status endpoint returns a disconnected
 * status for a workspace with no connection when the gate is open, and the
 * canonical KLM-EXT-001 response only when the global gate is closed.
 */
export async function fetchWhatsAppExternalDeliveryGate(): Promise<boolean> {
  try {
    await apiFetch('/api/v1/whatsapp/status');
    return true;
  } catch (error) {
    const detail = error instanceof ApiError && error.detail && typeof error.detail === 'object'
      ? error.detail as { code?: unknown }
      : null;
    if (error instanceof ApiError && error.status === 503 && detail?.code === 'KLM-EXT-001') {
      return false;
    }
    throw error;
  }
}

export async function fetchWhatsAppWebhookInfo(): Promise<WhatsAppWebhookInfo> {
  return apiFetch('/api/v1/whatsapp/connections/webhook-info');
}

export async function fetchWhatsAppConnectionHealth(connectionId: string): Promise<WhatsAppConnectionHealth> {
  return apiFetch(`/api/v1/whatsapp/connections/${connectionId}/health`);
}

export async function createManualWhatsAppConnection(
  payload: WhatsAppManualConnectionPayload,
): Promise<WhatsAppConnectionSummary> {
  return apiFetch('/api/v1/whatsapp/connections/manual', {
    method: 'POST',
    body: JSON.stringify({
      accessToken: payload.accessToken,
      phoneNumberId: payload.phoneNumberId,
      businessAccountId: payload.businessAccountId,
      registrationPin: payload.registrationPin || undefined,
      isDefault: payload.isDefault ?? true,
      appSecret: payload.appSecret || undefined,
    }),
  });
}

export async function fetchConversationAssignees(): Promise<ConversationAssignee[]> {
  return apiFetch('/api/v1/whatsapp/conversations/assignees');
}

export async function sendWhatsAppMessage(contactId: string, body: string, connectionId?: string | null): Promise<void> {
  const payload = {
    contactId,
    connectionId: connectionId ?? undefined,
    body,
  };
  await withStableOutboundKey('whatsapp-message', payload, (idempotencyKey) => (
    apiFetch('/api/v1/whatsapp/messages', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    })
  ));
}

export interface StartWhatsAppConversationPayload {
  connectionId: string;
  metaTemplateId: string;
  phone: string;
  contactName?: string;
  headerParameters: string[];
  bodyParameters: string[];
}

export async function startWhatsAppConversation(
  payload: StartWhatsAppConversationPayload,
): Promise<ApiConversation> {
  const canonicalPayload = { ...payload, phone: requireWhatsAppPhone(payload.phone) };
  return withStableOutboundKey('whatsapp-conversation-start', canonicalPayload, (idempotencyKey) => (
    apiFetch('/api/v1/whatsapp/conversations/start', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(canonicalPayload),
    })
  ));
}

export async function assignConversation(contactId: string, userId?: string | null): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/conversations/${contactId}/assign`, {
    method: 'POST',
    body: JSON.stringify({ userId: userId ?? null }),
  });
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function claimConversation(contactId: string): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/conversations/${contactId}/claim`, {
    method: 'POST',
  });
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function handoffConversation(
  contactId: string,
  payload: { queueStatus: string; humanControlState?: string },
): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/conversations/${contactId}/handoff`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function applyConversationFlow(
  contactId: string,
  payload: { templateId: string; customName?: string | null; moveToFollowUp?: boolean; nextFollowUpAt?: string | null },
): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/conversations/${contactId}/apply-flow`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function scheduleConversationMessage(
  contactId: string,
  payload: { connectionId?: string | null; scheduledAt: string; body: string },
): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/conversations/${contactId}/schedule-message`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function scheduleConversationMeeting(
  contactId: string,
  payload: {
    title: string;
    startsAt: string;
    endsAt?: string | null;
    responsibleUserId?: string | null;
    location?: string | null;
    meetingUrl?: string | null;
    notes?: string | null;
  },
): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/conversations/${contactId}/schedule-meeting`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function sendConversationMedia(
  contactId: string,
  payload: {
    connectionId?: string | null;
    documentId?: string | null;
    url?: string | null;
    filename?: string | null;
    caption?: string | null;
  },
): Promise<WhatsAppConversation> {
  const data = await withStableOutboundKey(
    'whatsapp-media',
    { contactId, payload },
    (idempotencyKey) => apiFetch<ApiConversation>(
      `/api/v1/whatsapp/conversations/${contactId}/send-media`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(payload),
      },
    ),
  );
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function sendConversationProposal(
  contactId: string,
  payload: { connectionId?: string | null; templateId: string; format?: string | null; customMessage?: string | null },
  idempotencyKey: string,
): Promise<WhatsAppConversation> {
  const data = await apiFetch<ApiConversation>(`/api/v1/whatsapp/conversations/${contactId}/send-proposal`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function sendConversationContract(
  contactId: string,
  payload: { connectionId?: string | null; templateId: string; authMode?: string | null; customMessage?: string | null },
): Promise<WhatsAppConversation> {
  const data = await withStableOutboundKey(
    'whatsapp-contract',
    { contactId, payload },
    (idempotencyKey) => apiFetch<ApiConversation>(
      `/api/v1/whatsapp/conversations/${contactId}/send-contract`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(payload),
      },
    ),
  );
  return {
    ...mapConversationSummary(data.contact ?? {}),
    messages: (data.messages ?? []).map((message: ApiMessage) => mapMessage(message, contactId)),
  };
}

export async function fetchMessageTemplates(): Promise<MessageTemplate[]> {
  return apiFetch('/api/v1/whatsapp/templates');
}

export async function markWhatsAppConversationRead(contactId: string): Promise<void> {
  await apiFetch(`/api/v1/whatsapp/contacts/${contactId}/read`, {
    method: 'POST',
  });
}
