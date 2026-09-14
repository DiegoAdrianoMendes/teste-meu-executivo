import type {
  MetaApprovedMessage,
  MetaAutomationRule,
  MetaChannelCampaign,
  MetaChannelConnection,
  MetaCommentThread,
  MetaConversationNote,
  MetaLeadDrawerData,
  MetaCopilotResult,
  MetaSocialConversation,
  FollowRelationshipStatus,
} from '@/types';
import { apiFetch } from '@/services/http';
import { mapMatter } from '@/services/matters';

type ChannelKey = 'instagram' | 'messenger';

function mapConversation(item: Record<string, any>): MetaSocialConversation {
  return {
    id: item.id,
    channelType: item.channelType ?? item.channel_type,
    connectionId: item.connectionId ?? item.connection_id,
    matterId: item.matterId ?? item.matter_id ?? null,
    assignedUserId: item.assignedUserId ?? item.assigned_user_id ?? null,
    assignedUserName: item.assignedUserName ?? item.assigned_user_name ?? null,
    externalThreadId: item.externalThreadId ?? item.external_thread_id,
    externalParticipantId: item.externalParticipantId ?? item.external_participant_id ?? null,
    participantName: item.participantName ?? item.participant_name,
    participantHandle: item.participantHandle ?? item.participant_handle ?? null,
    participantAvatarUrl: item.participantAvatarUrl ?? item.participant_avatar_url ?? null,
    eventType: item.eventType ?? item.event_type,
    queueStatus: item.queueStatus ?? item.queue_status,
    humanControlState: item.humanControlState ?? item.human_control_state,
    followStatus: item.followStatus ?? item.follow_status,
    sourceTitle: item.sourceTitle ?? item.source_title ?? null,
    sourceUrl: item.sourceUrl ?? item.source_url ?? null,
    sourceMetadata: item.sourceMetadata ?? item.source_metadata ?? {},
    activeFlowLabel: item.activeFlowLabel ?? item.active_flow_label ?? null,
    nextFollowUpAt: item.nextFollowUpAt ?? item.next_follow_up_at ?? null,
    lastMessagePreview: item.lastMessagePreview ?? item.last_message_preview ?? null,
    lastMessageAt: item.lastMessageAt ?? item.last_message_at ?? null,
    unreadCount: item.unreadCount ?? item.unread_count ?? 0,
    policyState: item.policyState ?? item.policy_state ?? null,
    isPublicThread: Boolean(item.isPublicThread ?? item.is_public_thread),
    messages: (item.messages ?? []).map((message: Record<string, any>) => ({
      id: message.id,
      direction: message.direction,
      status: message.status,
      body: message.body,
      authorName: message.authorName ?? message.author_name ?? null,
      isPublicReply: Boolean(message.isPublicReply ?? message.is_public_reply),
      messageMetadata: message.messageMetadata ?? message.message_metadata ?? {},
      timestamp: message.timestamp,
      deliveredAt: message.deliveredAt ?? message.delivered_at ?? null,
      readAt: message.readAt ?? message.read_at ?? null,
    })),
    sensitiveDataHidden: Boolean(item.sensitiveDataHidden ?? item.sensitive_data_hidden),
  };
}

function mapNote(item: Record<string, any>): MetaConversationNote {
  return {
    id: item.id,
    conversationId: item.conversationId ?? item.conversation_id,
    matterId: item.matterId ?? item.matter_id ?? null,
    authorUserId: item.authorUserId ?? item.author_user_id ?? null,
    authorName: item.authorName ?? item.author_name ?? null,
    body: item.body ?? '',
    mentions: item.mentions ?? [],
    noteMetadata: item.noteMetadata ?? item.note_metadata ?? {},
    createdAt: item.createdAt ?? item.created_at,
  };
}

export async function fetchMetaConnections(channel: ChannelKey): Promise<MetaChannelConnection[]> {
  return apiFetch(`/api/v1/${channel}/connections`);
}

export async function createMetaConnection(channel: ChannelKey, payload: Record<string, unknown>): Promise<MetaChannelConnection> {
  return apiFetch(`/api/v1/${channel}/connections`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchMetaConversations(channel: ChannelKey): Promise<MetaSocialConversation[]> {
  const data = await apiFetch<Record<string, any>[]>(`/api/v1/${channel}/conversations`);
  return data.map(mapConversation);
}

export async function createMetaConversation(channel: ChannelKey, payload: Record<string, unknown>): Promise<MetaSocialConversation> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return mapConversation(data);
}

export async function fetchMetaConversation(channel: ChannelKey, conversationId: string): Promise<MetaSocialConversation> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations/${conversationId}`);
  return mapConversation(data);
}

export async function sendMetaMessage(channel: ChannelKey, conversationId: string, body: string, isPublicReply = false): Promise<MetaSocialConversation> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations/${conversationId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ body, isPublicReply }),
  });
  return mapConversation(data);
}

export async function generateMetaCopilot(
  channel: ChannelKey,
  conversationId: string,
  payload: { tone?: string; goal?: string | null }
): Promise<MetaCopilotResult> {
  return apiFetch(`/api/v1/${channel}/conversations/${conversationId}/copilot`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchMetaComments(channel: ChannelKey): Promise<MetaCommentThread[]> {
  return apiFetch(`/api/v1/${channel}/comments`);
}

export async function refreshMetaFollowStatus(channel: ChannelKey, conversationId: string, status: FollowRelationshipStatus): Promise<MetaSocialConversation> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations/${conversationId}/follow-status`, {
    method: 'POST',
    body: JSON.stringify({ status }),
  });
  return mapConversation(data);
}

export async function fetchApprovedMessages(channel: ChannelKey): Promise<MetaApprovedMessage[]> {
  return apiFetch(`/api/v1/${channel}/approved-messages`);
}

export async function createApprovedMessage(channel: ChannelKey, payload: Record<string, unknown>): Promise<MetaApprovedMessage> {
  return apiFetch(`/api/v1/${channel}/approved-messages`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchAutomationRules(channel: ChannelKey): Promise<MetaAutomationRule[]> {
  return apiFetch(`/api/v1/${channel}/automation-rules`);
}

export async function createAutomationRule(channel: ChannelKey, payload: Record<string, unknown>): Promise<MetaAutomationRule> {
  return apiFetch(`/api/v1/${channel}/automation-rules`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchMetaCampaigns(channel: ChannelKey): Promise<MetaChannelCampaign[]> {
  return apiFetch(`/api/v1/${channel}/campaigns`);
}

export async function createMetaCampaign(channel: ChannelKey, payload: Record<string, unknown>): Promise<MetaChannelCampaign> {
  return apiFetch(`/api/v1/${channel}/campaigns`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateMetaCampaign(channel: ChannelKey, campaignId: string, payload: Record<string, unknown>): Promise<MetaChannelCampaign> {
  return apiFetch(`/api/v1/${channel}/campaigns/${campaignId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchMetaDrawer(channel: ChannelKey, conversationId: string): Promise<MetaLeadDrawerData> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations/${conversationId}/drawer`);
  return {
    conversation: mapConversation(data.conversation ?? {}),
    matter: data.matter ? mapMatter(data.matter) : null,
    notes: (data.notes ?? []).map(mapNote),
    recentMeetings: data.recentMeetings ?? data.recent_meetings ?? [],
    canEditMatter: Boolean(data.canEditMatter ?? data.can_edit_matter),
    canManageConversation: Boolean(data.canManageConversation ?? data.can_manage_conversation),
  };
}

export async function fetchMetaNotes(channel: ChannelKey, conversationId: string): Promise<MetaConversationNote[]> {
  const data = await apiFetch<Record<string, any>[]>(`/api/v1/${channel}/conversations/${conversationId}/notes`);
  return data.map(mapNote);
}

export async function createMetaNote(channel: ChannelKey, conversationId: string, body: string): Promise<MetaConversationNote> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations/${conversationId}/notes`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  });
  return mapNote(data);
}

export async function moveMetaConversationStage(channel: ChannelKey, conversationId: string, payload: { columnId: string; microStatus?: string | null }): Promise<MetaLeadDrawerData> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations/${conversationId}/move-stage`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return {
    conversation: mapConversation(data.conversation ?? {}),
    matter: data.matter ? mapMatter(data.matter) : null,
    notes: (data.notes ?? []).map(mapNote),
    recentMeetings: data.recentMeetings ?? data.recent_meetings ?? [],
    canEditMatter: Boolean(data.canEditMatter ?? data.can_edit_matter),
    canManageConversation: Boolean(data.canManageConversation ?? data.can_manage_conversation),
  };
}

export async function updateMetaMatter(channel: ChannelKey, conversationId: string, payload: Record<string, unknown>): Promise<MetaLeadDrawerData> {
  const data = await apiFetch<Record<string, any>>(`/api/v1/${channel}/conversations/${conversationId}/matter`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
  return {
    conversation: mapConversation(data.conversation ?? {}),
    matter: data.matter ? mapMatter(data.matter) : null,
    notes: (data.notes ?? []).map(mapNote),
    recentMeetings: data.recentMeetings ?? data.recent_meetings ?? [],
    canEditMatter: Boolean(data.canEditMatter ?? data.can_edit_matter),
    canManageConversation: Boolean(data.canManageConversation ?? data.can_manage_conversation),
  };
}
