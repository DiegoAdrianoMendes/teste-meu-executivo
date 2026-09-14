import type {
  AgentRun,
  AuditLog,
  CampaignAttribution,
  CardFieldChange,
  CardScore,
  ConversationItem,
  ConversationInsight,
  DocumentItem,
  Filters,
  MatterCard,
  RevenueProjection,
  HonorariumEstimate,
  Task,
  TimelineEvent,
  HumanOverride,
  KnowledgeChunk,
} from '@/types';
import { apiFetch } from '@/services/http';

type ApiMatter = Record<string, any>;

function mapTimeline(items: any[] = []): TimelineEvent[] {
  return items.map((item) => ({
    id: item.id,
    type: item.type ?? item.eventType,
    title: item.title,
    description: item.description,
    timestamp: item.timestamp,
    agentId: item.agentId ?? item.agent_id ?? '',
    metadata: item.metadata,
  }));
}

function mapTasks(items: any[] = []): Task[] {
  return items.map((item) => ({
    id: item.id,
    title: item.title,
    description: item.description,
    status: item.status,
    version: typeof item.version === 'number' ? item.version : undefined,
    assignedTo: item.assignedTo ?? item.assigned_to ?? '',
    dueDate: item.dueDate ?? item.due_date,
    createdAt: item.createdAt ?? item.created_at,
    isAiGenerated: item.isAiGenerated ?? item.is_ai_generated ?? false,
    priority: item.priority,
    sourceExceptionId: item.sourceExceptionId ?? item.source_exception_id ?? null,
  }));
}

function mapDocuments(items: any[] = []): DocumentItem[] {
  return items.map((item) => ({
    id: item.id,
    name: item.name,
    type: item.type,
    status: item.status,
    url: item.url,
    createdAt: item.createdAt ?? item.created_at,
    createdBy: item.createdBy ?? item.created_by ?? '',
    size: item.size,
  }));
}

function mapConversations(items: any[] = []): ConversationItem[] {
  return items.map((item) => ({
    id: item.id,
    agentId: item.agentId ?? item.agent_id ?? '',
    message: item.message,
    timestamp: item.timestamp,
    isInternal: item.isInternal ?? item.is_internal ?? false,
  }));
}

function mapHumanOverrides(items: any[] = []): HumanOverride[] {
  return items.map((item) => ({
    id: item.id,
    matterId: item.matterId ?? item.matter_id,
    agentRunId: item.agentRunId ?? item.agent_run_id,
    decidedByUserId: item.decidedByUserId ?? item.decided_by_user_id,
    decision: item.decision,
    notes: item.notes,
    beforePayload: item.beforePayload ?? item.before_payload,
    afterPayload: item.afterPayload ?? item.after_payload,
    createdAt: item.createdAt ?? item.created_at,
    updatedAt: item.updatedAt ?? item.updated_at,
  }));
}

function mapAgentRuns(items: any[] = []): AgentRun[] {
  return items.map((item) => ({
    id: item.id,
    workspaceId: item.workspaceId ?? item.workspace_id,
    matterId: item.matterId ?? item.matter_id,
    triggeredByUserId: item.triggeredByUserId ?? item.triggered_by_user_id,
    agentName: item.agentName ?? item.agent_name,
    triggerSource: item.triggerSource ?? item.trigger_source,
    threadId: item.threadId ?? item.thread_id,
    dedupeKey: item.dedupeKey ?? item.dedupe_key,
    celeryTaskId: item.celeryTaskId ?? item.celery_task_id,
    status: item.status,
    inputPayload: item.inputPayload ?? item.input_payload,
    outputPayload: item.outputPayload ?? item.output_payload,
    errorMessage: item.errorMessage ?? item.error_message,
    attemptCount: item.attemptCount ?? item.attempt_count ?? 0,
    startedAt: item.startedAt ?? item.started_at,
    completedAt: item.completedAt ?? item.completed_at,
    createdAt: item.createdAt ?? item.created_at,
    updatedAt: item.updatedAt ?? item.updated_at,
    overrides: mapHumanOverrides(item.overrides),
  }));
}

function mapKnowledgeChunks(items: any[] = []): KnowledgeChunk[] {
  return items.map((item) => ({
    id: item.id,
    workspaceId: item.workspaceId ?? item.workspace_id,
    matterId: item.matterId ?? item.matter_id,
    taskId: item.taskId ?? item.task_id,
    createdByUserId: item.createdByUserId ?? item.created_by_user_id,
    sourceType: item.sourceType ?? item.source_type,
    sourceRef: item.sourceRef ?? item.source_ref,
    title: item.title,
    contentText: item.contentText ?? item.content_text,
    summary: item.summary,
    contentHash: item.contentHash ?? item.content_hash,
    rankHint: item.rankHint ?? item.rank_hint ?? 0,
    tags: item.tags ?? [],
    chunkMetadata: item.chunkMetadata ?? item.chunk_metadata ?? {},
    createdAt: item.createdAt ?? item.created_at,
    updatedAt: item.updatedAt ?? item.updated_at,
  }));
}

function mapConversationInsight(item?: any): ConversationInsight | undefined {
  if (!item) return undefined;
  return {
    id: item.id,
    sourceChannel: item.sourceChannel ?? item.source_channel ?? 'whatsapp',
    summary: item.summary,
    funnelSignal: item.funnelSignal ?? item.funnel_signal,
    closeIntent: item.closeIntent ?? item.close_intent,
    documentReadiness: item.documentReadiness ?? item.document_readiness,
    urgencyLevel: item.urgencyLevel ?? item.urgency_level,
    sentiment: item.sentiment,
    trustLevel: item.trustLevel ?? item.trust_level,
    noShowRisk: item.noShowRisk ?? item.no_show_risk,
    objections: item.objections ?? [],
    requestedDocuments: item.requestedDocuments ?? item.requested_documents ?? [],
    missingInformation: item.missingInformation ?? item.missing_information ?? [],
    recommendedAction: item.recommendedAction ?? item.recommended_action,
    nextBestMessage: item.nextBestMessage ?? item.next_best_message,
    estimatedConversionValue: item.estimatedConversionValue ?? item.estimated_conversion_value,
    closedSignal: item.closedSignal ?? item.closed_signal ?? false,
    conversionPlatforms: item.conversionPlatforms ?? item.conversion_platforms ?? [],
    rawPayload: item.rawPayload ?? item.raw_payload ?? {},
    lastMessageAt: item.lastMessageAt ?? item.last_message_at,
    createdAt: item.createdAt ?? item.created_at,
    updatedAt: item.updatedAt ?? item.updated_at,
  };
}

function mapAuditLogs(items: any[] = []): AuditLog[] {
  return items.map((item) => ({
    id: item.id,
    action: item.action,
    entityType: item.entityType ?? item.entity_type ?? 'matter',
    entityId: item.entityId ?? item.entity_id,
    agentId: item.agentId ?? item.agent_id ?? '',
    timestamp: item.timestamp,
    description: item.description,
    before: item.before,
    after: item.after,
  }));
}

function mapCardScore(item?: any): CardScore | undefined {
  if (!item) return undefined;
  return {
    id: item.id,
    viabilityScore: item.viabilityScore,
    urgencyScore: item.urgencyScore,
    evidenceScore: item.evidenceScore,
    conversionScore: item.conversionScore,
    relationshipScore: item.relationshipScore,
    overallScore: item.overallScore,
    explanation: item.explanation,
  };
}

function mapCampaignAttribution(item?: any): CampaignAttribution | undefined {
  if (!item) return undefined;
  return {
    id: item.id,
    source: item.source,
    medium: item.medium,
    campaign: item.campaign,
    content: item.content,
    term: item.term,
    channel: item.channel,
    firstTouchAt: item.firstTouchAt,
    lastTouchAt: item.lastTouchAt,
    notes: item.notes,
  };
}

function mapRevenueProjection(item?: any): RevenueProjection | undefined {
  if (!item) return undefined;
  return {
    id: item.id,
    estimatedClaimValue: item.estimatedClaimValue,
    projectedRevenue: item.projectedRevenue,
    successProbability: item.successProbability,
    confidenceLevel: item.confidenceLevel,
    paybackDays: item.paybackDays,
    currency: item.currency ?? 'BRL',
    notes: item.notes,
  };
}

function mapHonorariumEstimate(item?: any): HonorariumEstimate | undefined {
  if (!item) return undefined;
  return {
    id: item.id,
    contractType: item.contractType,
    fixedFee: item.fixedFee,
    quotaLitisPercentage: item.quotaLitisPercentage,
    expectedExpenses: item.expectedExpenses,
    totalEstimatedValue: item.totalEstimatedValue,
    notes: item.notes,
  };
}

function mapFieldChanges(items: any[] = []): CardFieldChange[] {
  return items.map((item) => ({
    id: item.id,
    fieldName: item.fieldName,
    oldValue: item.oldValue,
    newValue: item.newValue,
    summary: item.summary,
    changeSource: item.changeSource,
    actorType: item.actorType,
    changedByUserId: item.changedByUserId,
    createdAt: item.createdAt,
  }));
}

export function mapMatter(matter: ApiMatter): MatterCard {
  return {
    id: matter.id,
    client: matter.client,
    practiceArea: matter.practiceArea,
    caseNumber: matter.caseNumber,
    columnId: matter.columnId,
    microStatus: matter.microStatus,
    priority: matter.priority,
    urgency: matter.urgency,
    classification: matter.classification,
    demandType: matter.demandType,
    leadSource: matter.leadSource,
    executiveSummary: matter.executiveSummary,
    starsRating: matter.starsRating ?? 0,
    assignedAgents: matter.assignedAgentIds ?? [],
    nextAction: matter.nextAction ?? 'Sem próxima ação definida',
    currentAgent: matter.currentAgentId ?? '',
    responsibleHuman: matter.responsibleHumanId ?? '',
    lastInteraction: matter.lastInteraction ?? matter.updatedAt,
    unreadMessages: matter.unreadMessages ?? 0,
    openTasks: matter.openTasks ?? 0,
    isBlocked: matter.isBlocked ?? false,
    needsApproval: matter.needsApproval ?? false,
    docScore: matter.docScore ?? 0,
    productionScore: matter.productionScore ?? 0,
    strategyNotes: matter.strategyNotes ?? '',
    risks: matter.risks ?? [],
    blocks: matter.blocks ?? [],
    documents: mapDocuments(matter.documents),
    tasks: mapTasks(matter.tasks),
    timeline: mapTimeline(matter.timeline),
    conversations: mapConversations(matter.conversations),
    auditLogs: mapAuditLogs(matter.auditLogs),
    campaignAttribution: mapCampaignAttribution(matter.campaignAttribution),
    revenueProjection: mapRevenueProjection(matter.revenueProjection),
    honorariumEstimate: mapHonorariumEstimate(matter.honorariumEstimate),
    cardScore: mapCardScore(matter.cardScore),
    fieldChanges: mapFieldChanges(matter.fieldChanges),
    agentRuns: mapAgentRuns(matter.agentRuns),
    humanOverrides: mapHumanOverrides(matter.humanOverrides),
    knowledgeChunks: mapKnowledgeChunks(matter.knowledgeChunks),
    conversationInsight: mapConversationInsight(matter.conversationInsight ?? matter.conversation_insight),
    nextDeadline: matter.nextDeadline,
    nextHearing: matter.nextHearing,
    claimValue: matter.claimValue,
    createdAt: matter.createdAt,
    updatedAt: matter.updatedAt,
  };
}

function appendFilter(params: URLSearchParams, key: string, value: string | number | boolean | null | undefined) {
  if (value === null || value === undefined || value === '') {
    return;
  }
  params.set(key, String(value));
}

export async function fetchMatters(filters?: Filters): Promise<MatterCard[]> {
  const params = new URLSearchParams();
  if (filters) {
    appendFilter(params, 'search', filters.search.trim() || undefined);
    appendFilter(params, 'micro_status', filters.status[0]);
    appendFilter(params, 'priority', filters.priority[0]);
    appendFilter(params, 'urgency', filters.urgency[0]);
    appendFilter(params, 'agent_id', filters.agentId[0]);
    appendFilter(params, 'column_id', filters.columnId[0]);
    appendFilter(params, 'classification', filters.classification[0]);
    appendFilter(params, 'lead_source', filters.leadSource[0]);
    appendFilter(params, 'close_intent', filters.closeIntent[0]);
    appendFilter(params, 'document_readiness', filters.documentReadiness[0]);
    appendFilter(params, 'is_blocked', filters.isBlocked);
    appendFilter(params, 'needs_approval', filters.needsApproval);
    appendFilter(params, 'has_unread_messages', filters.hasUnreadMessages);
    appendFilter(params, 'has_financial_pending', filters.hasFinancialPending);
    appendFilter(params, 'doc_score_below', filters.docScoreBelow);
    appendFilter(params, 'updated_within_days', filters.updatedWithinDays);
    appendFilter(params, 'min_claim_value', filters.minClaimValue);
    appendFilter(params, 'max_claim_value', filters.maxClaimValue);
  }

  const suffix = params.toString() ? `?${params.toString()}` : '';
  const matters = await apiFetch<ApiMatter[]>(`/api/v1/matters${suffix}`);
  return matters.map(mapMatter);
}

export async function fetchMatterDetail(matterId: string): Promise<MatterCard> {
  const matter = await apiFetch<ApiMatter>(`/api/v1/matters/${matterId}`);
  return mapMatter(matter);
}

export async function fetchDocumentDownloadUrl(documentId: string): Promise<string> {
  const response = await apiFetch<{ downloadUrl: string }>(
    `/api/v1/documents/${documentId}/download-url`,
  );
  return response.downloadUrl;
}

export async function updateMatterDetail(matterId: string, payload: Record<string, unknown>): Promise<MatterCard> {
  const matter = await apiFetch<ApiMatter>(`/api/v1/matters/${matterId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
    headers: { 'X-KLM-Change-Source': 'manual_spa' },
  });
  return mapMatter(matter);
}

export type MatterTaskToggleResponse = {
  id: string;
  status: Task['status'];
  version?: number;
  completedAt?: string | null;
};

/** `PATCH /tasks/{id}/toggle` exige a versão esperada; o backend devolve a tarefa com a versão nova. */
export async function toggleMatterTask(taskId: string, expectedVersion: number): Promise<MatterTaskToggleResponse> {
  const task = await apiFetch<Record<string, any>>(
    `/api/v1/tasks/${taskId}/toggle?expectedVersion=${encodeURIComponent(String(expectedVersion))}`,
    { method: 'PATCH' },
  );
  return {
    id: String(task.id),
    status: task.status,
    version: typeof task.version === 'number' ? task.version : undefined,
    completedAt: task.completedAt ?? task.completed_at ?? null,
  };
}

export async function retryAgentRun(matterId: string, runId: string): Promise<void> {
  await apiFetch(`/api/v1/agent-runs/matters/${matterId}/${runId}/retry`, {
    method: 'POST',
  });
}

export async function overrideAgentRun(
  matterId: string,
  runId: string,
  payload: { decision: 'accepted' | 'corrected' | 'rejected'; notes?: string; correctedPayload?: Record<string, unknown> }
): Promise<void> {
  await apiFetch(`/api/v1/agent-runs/matters/${matterId}/${runId}/override`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export type MatterNoteResponse = { message: string; event_id: string };

/** O backend responde `event_id` (snake_case; `apiFetch` não cameliza). */
export async function addMatterNote(matterId: string, note: string): Promise<MatterNoteResponse> {
  return apiFetch<MatterNoteResponse>(`/api/v1/matters/${matterId}/notes`, {
    method: 'POST',
    body: JSON.stringify({ note }),
  });
}
