// ── Domain Types for KLM Hub ──

export type PracticeArea =
  | 'trabalhista'
  | 'civel'
  | 'tributario'
  | 'empresarial'
  | 'previdenciario'
  | 'bancario'
  | 'plano_saude'
  | 'seguros'
  | 'saude_suplementar'
  | 'universal';
export type HealthSubtheme =
  | 'medicamento_alto_custo'
  | 'tratamento'
  | 'home_care'
  | 'cirurgia'
  | 'exame'
  | 'opme'
  | 'terapia'
  | 'outro';
export type Priority = 'alta' | 'media' | 'baixa';
export type Urgency = 'urgente' | 'normal' | 'baixa';
export type ClientClassification = 'Premium' | 'Forte' | 'Bom' | 'Frio';

export type MicroStatus =
  | 'lead_novo' | 'sem_resposta' | 'triagem_incompleta' | 'qualificado'
  | 'proposta_enviada' | 'assinatura_pendente' | 'pagamento_pendente'
  | 'aguardando_docs' | 'docs_parciais' | 'docs_invalidos' | 'docs_completos'
  | 'tese_preliminar' | 'risco_probatorio' | 'pesquisa_em_andamento'
  | 'validacao_aprovada' | 'validacao_reprovada'
  | 'minuta_em_elaboracao' | 'revisao_interna' | 'ajustes_solicitados'
  | 'pdf_gerado' | 'checklist_final' | 'migracao_pendente';

export type AgentType = 'human' | 'ai';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'changes_requested';
export type TaskStatus = 'pending' | 'in_progress' | 'done' | 'blocked';
export type AgentRunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'waiting_human' | 'cancelled';
export type HumanOverrideDecision = 'accepted' | 'corrected' | 'rejected';
export type DocumentType = 'peticao_inicial' | 'procuracao' | 'contrato' | 'comprovante' | 'sentenca' | 'recurso' | 'outro';
export type DocumentStatus = 'aguardando' | 'parcial' | 'invalido' | 'completo';
export type TimelineEventType = 'status_change' | 'document_added' | 'comment' | 'task_created' | 'task_completed' | 'approval_requested' | 'approval_resolved' | 'agent_action' | 'deadline_alert';

export type KanbanColumnId =
  | 'entrada' | 'qualificacao' | 'proposta_fechamento' | 'coleta_dados'
  | 'triagem_juridica' | 'pesquisa_validacao' | 'redacao_peca'
  | 'aprovacao_pendente' | 'pronto_protocolo' | 'migrar_sistema';

// ── Entities ──

export interface Agent {
  id: string;
  name: string;
  type: AgentType;
  avatar?: string;
  initials: string;
  role: string;
  color: string;
  email?: string;
  phone?: string | null;
  isActive?: boolean;
  isInternalStaff?: boolean;
  isPlatformAdmin?: boolean;
  platformRolePackage?: string | null;
  platformAccessActive?: boolean;
  platformPermissions?: PlatformPermission[];
}

export interface Client {
  id: string;
  name: string;
  cpf: string;
  email: string;
  phone: string;
  employer: string;
  position: string;
  sensitiveDataHidden?: boolean;
  maskedLabel?: string | null;
}

export interface DocumentItem {
  id: string;
  name: string;
  type: DocumentType;
  status: DocumentStatus;
  url?: string;
  createdAt: string;
  createdBy: string;
  size?: string;
  mimeType?: string | null;
}

export interface Task {
  id: string;
  title: string;
  description?: string;
  status: TaskStatus;
  assignedTo: string;
  dueDate?: string;
  createdAt: string;
  isAiGenerated: boolean;
  priority?: Priority;
  /** Versão otimista da tarefa (`PATCH /tasks/{id}/toggle?expectedVersion=`). Só vem no detalhe do caso. */
  version?: number;
  // Referência à Central de Exceções — só preenchida quando a task nasceu
  // de uma falha materializada por `ExceptionCenterService` (ver
  // app/schemas/task.py::TaskRead::source_exception_id); nunca escrita
  // pelo cliente.
  sourceExceptionId?: string | null;
}

export interface TimelineEvent {
  id: string;
  type: TimelineEventType;
  title: string;
  description?: string;
  timestamp: string;
  agentId: string;
  metadata?: Record<string, unknown>;
}

export interface AuditLog {
  id: string;
  action: string;
  entityType: string;
  entityId: string;
  agentId: string;
  timestamp: string;
  description?: string;
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
}

export interface CampaignAttribution {
  id: string;
  source?: string;
  medium?: string;
  campaign?: string;
  content?: string;
  term?: string;
  channel?: string;
  firstTouchAt?: string;
  lastTouchAt?: string;
  notes?: string;
}

export interface RevenueProjection {
  id: string;
  estimatedClaimValue?: number;
  projectedRevenue?: number;
  successProbability?: number;
  confidenceLevel?: number;
  paybackDays?: number;
  currency: string;
  notes?: string;
}

export interface HonorariumEstimate {
  id: string;
  contractType?: string;
  fixedFee?: number;
  quotaLitisPercentage?: number;
  expectedExpenses?: number;
  totalEstimatedValue?: number;
  notes?: string;
}

export type FinanceEntryType = 'receivable' | 'expense' | 'refund';
export type FinanceEntryStatus = 'pending' | 'scheduled' | 'paid' | 'overdue' | 'cancelled';
export type FinanceReminderStatus = 'pending' | 'sent' | 'dismissed';
export type IntegrationProvider =
  | 'asaas'
  | 'conta_azul'
  | 'zapsign'
  | 'docusign'
  | 'custom'
  | 'pluga'
  | 'zapier'
  | 'google_ads'
  | 'google_tag_manager'
  | 'ga4'
  | 'meta_ads'
  | 'meta_messenger'
  | 'meta_instagram'
  | 'whatsapp_cloud';
export type IntegrationStatus = 'disconnected' | 'pending' | 'connected' | 'error';

export interface FinanceReminder {
  id: string;
  workspaceId: string;
  financeEntryId: string;
  status: FinanceReminderStatus;
  remindAt: string;
  channel: string;
  message?: string;
  resolvedAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface FinanceEntry {
  id: string;
  workspaceId: string;
  matterId?: string;
  title: string;
  description?: string;
  entryType: FinanceEntryType;
  status: FinanceEntryStatus;
  provider?: string;
  externalRef?: string;
  amount: number;
  currency: string;
  dueDate?: string;
  paidAt?: string;
  reminderDaysBefore: number;
  notifyClient: boolean;
  tags: string[];
  entryMetadata: Record<string, unknown>;
  reminders: FinanceReminder[];
  createdAt: string;
  updatedAt: string;
}

export interface IntegrationConnection {
  id: string;
  workspaceId: string;
  provider: IntegrationProvider;
  status: IntegrationStatus;
  label: string;
  baseUrl?: string;
  externalAccountId?: string;
  webhookUrl?: string;
  apiVersion?: string;
  capabilities: string[];
  settings: Record<string, unknown>;
  lastSyncAt?: string;
  notes?: string;
  createdAt: string;
  updatedAt: string;
}

export interface FinanceSettingsProfile {
  remindersEnabled: boolean;
  defaultReminderDaysBefore: number;
  defaultReminderChannel: string;
  allowClientNotification: boolean;
  autoCreateReceivableOnClose: boolean;
  openApiEnabled: boolean;
  externalSyncMode: string;
}

export interface FinanceOverview {
  totalOpenReceivables: number;
  overdueAmount: number;
  scheduledAmount: number;
  paidAmount: number;
  pendingReminders: number;
  entriesDueSoon: number;
  upcomingEntries: FinanceEntry[];
  reminders: FinanceReminder[];
  integrations: IntegrationConnection[];
  highlights: string[];
}

export interface IntegrationCatalogProvider {
  key: string;
  label: string;
  category: string;
  description: string;
  capabilities: string[];
  native: boolean;
  defaultWebhookPath: string;
  docsUrl?: string;
  setupHint: {
    authType: string;
    webhookSecurity: string;
    requiredSettings: string[];
    recommendedBaseUrl?: string | null;
    recommendedApiVersion?: string | null;
    setupSteps: string[];
  };
}

export interface OpenApiResource {
  key: string;
  label: string;
  path: string;
  method: string;
  description: string;
}

export interface OpenApiSurface {
  workspaceId: string;
  workspaceSlug: string;
  baseUrl: string;
  docsUrl?: string | null;
  openapiUrl?: string | null;
  webhookBaseUrl: string;
  resources: OpenApiResource[];
}

export type PublicApiScope =
  | 'contacts:read'
  | 'conversations:read'
  | 'leads:write'
  | 'matters:write'
  | 'notes:write'
  | 'templates:send'
  | 'campaigns:write';

export interface WorkspacePublicApiToken {
  id: string;
  label: string;
  tokenPrefix: string;
  scopes: string[];
  rateLimitPerMinute: number;
  isActive: boolean;
  lastUsedAt?: string | null;
  expiresAt?: string | null;
  revokedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface WorkspacePublicApiTokenCreateResponse {
  token: WorkspacePublicApiToken;
  plainTextToken: string;
}

export interface PublicApiRequestLog {
  id: string;
  tokenId: string;
  path: string;
  method: string;
  scope: string;
  statusCode: number;
  ipAddress?: string | null;
  userAgent?: string | null;
  requestedAt: string;
}

export interface CardScore {
  id: string;
  viabilityScore?: number;
  urgencyScore?: number;
  evidenceScore?: number;
  conversionScore?: number;
  relationshipScore?: number;
  overallScore?: number;
  explanation?: string;
}

export interface CardFieldChange {
  id: string;
  fieldName: string;
  oldValue?: Record<string, unknown>;
  newValue?: Record<string, unknown>;
  summary?: string;
  changeSource?: string;
  actorType: 'human' | 'ai' | 'system';
  changedByUserId?: string;
  createdAt: string;
}

export interface ConversationItem {
  id: string;
  agentId: string;
  message: string;
  timestamp: string;
  isInternal: boolean;
}

export interface HumanOverride {
  id: string;
  matterId: string;
  agentRunId: string;
  decidedByUserId?: string;
  decision: HumanOverrideDecision;
  notes?: string;
  beforePayload?: Record<string, unknown>;
  afterPayload?: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeChunk {
  id: string;
  workspaceId: string;
  matterId: string;
  taskId?: string;
  createdByUserId?: string;
  sourceType: string;
  sourceRef?: string;
  title?: string;
  contentText: string;
  summary?: string;
  contentHash: string;
  rankHint: number;
  tags: string[];
  chunkMetadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ConversationInsight {
  id: string;
  sourceChannel: string;
  summary?: string;
  funnelSignal?: string;
  closeIntent?: string;
  documentReadiness?: string;
  urgencyLevel?: string;
  sentiment?: string;
  trustLevel?: string;
  noShowRisk?: string;
  objections: string[];
  requestedDocuments: string[];
  missingInformation: string[];
  recommendedAction?: string;
  nextBestMessage?: string;
  estimatedConversionValue?: number;
  closedSignal: boolean;
  conversionPlatforms: string[];
  rawPayload: Record<string, unknown>;
  lastMessageAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface AgentRun {
  id: string;
  workspaceId: string;
  matterId: string;
  triggeredByUserId?: string;
  agentName: string;
  triggerSource: string;
  threadId?: string;
  dedupeKey?: string;
  celeryTaskId?: string;
  status: AgentRunStatus;
  inputPayload?: Record<string, unknown>;
  outputPayload?: Record<string, unknown>;
  errorMessage?: string;
  attemptCount: number;
  startedAt?: string;
  completedAt?: string;
  createdAt: string;
  updatedAt: string;
  overrides: HumanOverride[];
}

export interface ApprovalItem {
  id: string;
  cardId: string;
  clientName: string;
  type: string;
  title: string;
  description: string;
  requestedBy: string;
  requestedAt: string;
  status: ApprovalStatus;
  resolvedBy?: string;
  resolvedAt?: string;
  priority: Priority;
  resolutionReason?: string;
  documentVersionUrl?: string;
  sensitiveDataHidden?: boolean;
}

export interface MatterCard {
  id: string;
  client: Client;
  practiceArea: PracticeArea;
  caseNumber?: string;
  columnId: KanbanColumnId;
  microStatus: MicroStatus;
  priority: Priority;
  urgency: Urgency;
  classification: ClientClassification;
  demandType: string;
  leadSource?: string;
  executiveSummary?: string;
  healthSubtheme?: HealthSubtheme | null;
  healthObjectName?: string | null;
  healthInsurer?: string | null;
  healthDenialReason?: string | null;
  medicalUrgencyHours?: number | null;
  economicValueAnnual?: number | null;
  leadScore?: number;
  starsRating: number;
  assignedAgents: string[];
  nextAction: string;
  currentAgent: string;
  responsibleHuman: string;
  lastInteraction: string;
  unreadMessages: number;
  openTasks: number;
  isBlocked: boolean;
  needsApproval: boolean;
  docScore: number;
  productionScore: number;
  strategyNotes: string;
  risks: string[];
  blocks: string[];
  documents: DocumentItem[];
  tasks: Task[];
  timeline: TimelineEvent[];
  conversations: ConversationItem[];
  auditLogs: AuditLog[];
  campaignAttribution?: CampaignAttribution;
  revenueProjection?: RevenueProjection;
  honorariumEstimate?: HonorariumEstimate;
  cardScore?: CardScore;
  fieldChanges?: CardFieldChange[];
  agentRuns?: AgentRun[];
  humanOverrides?: HumanOverride[];
  knowledgeChunks?: KnowledgeChunk[];
  conversationInsight?: ConversationInsight;
  nextDeadline?: string;
  nextHearing?: string;
  claimValue?: number;
  createdAt: string;
  updatedAt: string;
}

export interface KanbanColumn {
  id: KanbanColumnId;
  title: string;
  color: string;
  order: number;
}

export interface ReportMetric {
  id: string;
  label: string;
  value: number;
  previousValue?: number;
  unit?: string;
  trend?: 'up' | 'down' | 'stable';
}

export interface ReportChartPoint {
  name: string;
  count: number;
  color?: string;
}

export interface ReportValuePoint {
  name: string;
  value: number;
  color?: string;
}

export interface ReportInsight {
  id: string;
  title: string;
  summary: string;
  priority: 'low' | 'medium' | 'high' | string;
  recommendedAction?: string;
  source: string;
  metricRefs: string[];
}

export interface AdPlatformConversion {
  platform: string;
  source: string;
  conversions: number;
  conversionValue: number;
  currency: string;
  eventName: string;
}

export interface ReportsSummaryData {
  kpis: ReportMetric[];
  casesByPhase: ReportChartPoint[];
  casesByClassification: ReportChartPoint[];
  casesByResponsible: ReportChartPoint[];
  casesByDemand: ReportChartPoint[];
  casesBySource: ReportChartPoint[];
  conversationSignals: ReportChartPoint[];
  documentsByStatus: ReportChartPoint[];
  funnelOverview: ReportChartPoint[];
  revenueBySource: ReportValuePoint[];
  tasksHumanVsAi: ReportChartPoint[];
  adPlatformConversions: AdPlatformConversion[];
  insights: ReportInsight[];
}

export interface Notification {
  id: string;
  title: string;
  message: string;
  timestamp: string;
  read: boolean;
  type: 'info' | 'warning' | 'urgent' | 'success';
  cardId?: string;
}

// ── WhatsApp Messaging Types ──

export type MessageStatus = 'pending' | 'delivery_uncertain' | 'sent' | 'delivered' | 'read' | 'failed';
export type AiMode = 'active' | 'paused' | 'off';
export type ConversationQueueStatus = 'ai' | 'human' | 'follow_up' | 'closed';
export type HumanControlState = 'copilot' | 'manual_pause' | 'manual_off';
export type MetaChannelType = 'INSTAGRAM' | 'MESSENGER';
export type MetaConnectionStatus = 'connected' | 'pending' | 'disconnected' | 'error';
export type SocialEventType =
  | 'direct_message'
  | 'story_reply'
  | 'story_mention'
  | 'post_comment'
  | 'keyword_comment'
  | 'page_message'
  | 'page_comment'
  | 'entry_point';
export type FollowRelationshipStatus = 'following' | 'not_following' | 'unknown';
export type ApprovedMessageStatus = 'draft' | 'in_review' | 'approved' | 'archived';
export type MetaCampaignStatus = 'draft' | 'scheduled' | 'running' | 'paused' | 'completed' | 'stopped' | 'failed';

export interface WhatsAppMessage {
  id: string;
  conversationId: string;
  senderId: string;
  text: string;
  timestamp: string;
  status: MessageStatus;
  isFromClient: boolean;
  sensitiveDataHidden?: boolean;
  messageType?: 'text' | 'audio' | 'image' | 'video' | 'document';
  transcript?: string;
  imageTranscript?: string;
  mediaUrl?: string;
}

export interface WhatsAppConversation {
  id: string;
  clientId: string;
  clientName: string;
  clientPhone: string;
  clientAvatar?: string;
  cardId?: string;
  whatsappConnectionId?: string;
  messages: WhatsAppMessage[];
  lastMessage: string;
  lastMessageTime: string;
  unreadCount: number;
  aiMode: AiMode;
  isOnline: boolean;
  assignedUserId?: string | null;
  assignedUserName?: string | null;
  queueStatus: ConversationQueueStatus;
  humanControlState: HumanControlState;
  activeFlowTemplateId?: string | null;
  activeFlowLabel?: string | null;
  nextFollowUpAt?: string | null;
  sensitiveDataHidden?: boolean;
}

export interface ConversationInternalNote {
  id: string;
  contactId: string;
  matterId?: string | null;
  authorUserId?: string | null;
  authorName?: string | null;
  body: string;
  mentions: string[];
  noteMetadata: Record<string, unknown>;
  createdAt: string;
}

export interface LeadDrawerData {
  contact: WhatsAppConversation;
  matter?: MatterCard | null;
  notes: ConversationInternalNote[];
  recentMeetings: Meeting[];
  canEditMatter: boolean;
  canManageConversation: boolean;
}

export interface MetaChannelConnection {
  id: string;
  channelType: MetaChannelType;
  pageId?: string | null;
  instagramAccountId?: string | null;
  externalAccountId?: string | null;
  accountName?: string | null;
  username?: string | null;
  scopes: string[];
  capabilityMatrix: Record<string, unknown>;
  status: MetaConnectionStatus;
  webhookRegistered: boolean;
  lastError?: string | null;
  connectedAt?: string | null;
  disconnectedAt?: string | null;
  lastSyncedAt?: string | null;
  updatedAt: string;
}

export interface MetaSocialMessage {
  id: string;
  direction: 'inbound' | 'outbound';
  status: MessageStatus;
  body: string;
  authorName?: string | null;
  isPublicReply: boolean;
  messageMetadata: Record<string, unknown>;
  timestamp: string;
  deliveredAt?: string | null;
  readAt?: string | null;
}

export interface MetaSocialConversation {
  id: string;
  channelType: MetaChannelType;
  connectionId: string;
  matterId?: string | null;
  assignedUserId?: string | null;
  assignedUserName?: string | null;
  externalThreadId: string;
  externalParticipantId?: string | null;
  participantName: string;
  participantHandle?: string | null;
  participantAvatarUrl?: string | null;
  eventType: SocialEventType;
  queueStatus: ConversationQueueStatus;
  humanControlState: HumanControlState;
  followStatus: FollowRelationshipStatus;
  sourceTitle?: string | null;
  sourceUrl?: string | null;
  sourceMetadata: Record<string, unknown>;
  activeFlowLabel?: string | null;
  nextFollowUpAt?: string | null;
  lastMessagePreview?: string | null;
  lastMessageAt?: string | null;
  unreadCount: number;
  policyState?: string | null;
  isPublicThread: boolean;
  messages: MetaSocialMessage[];
  sensitiveDataHidden?: boolean;
}

export interface MetaCommentThread {
  id: string;
  conversationId: string;
  participantName: string;
  participantHandle?: string | null;
  eventType: SocialEventType;
  sourceTitle?: string | null;
  sourceUrl?: string | null;
  followStatus: FollowRelationshipStatus;
  lastMessagePreview?: string | null;
  lastMessageAt?: string | null;
  policyState?: string | null;
  sensitiveDataHidden?: boolean;
}

export interface MetaApprovedMessage {
  id: string;
  channelType: MetaChannelType;
  messageKind: string;
  title: string;
  body: string;
  ctaLabel?: string | null;
  ctaUrl?: string | null;
  status: ApprovedMessageStatus;
  requiresFollowing: boolean;
  restrictions: Record<string, unknown>;
  createdById?: string | null;
  approvedById?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MetaAutomationRule {
  id: string;
  channelType: MetaChannelType;
  title: string;
  eventType: SocialEventType;
  enabled: boolean;
  keyword?: string | null;
  approvedMessageId?: string | null;
  actionConfig: Record<string, unknown>;
  updatedAt: string;
}

export interface MetaChannelCampaign {
  id: string;
  channelType: MetaChannelType;
  connectionId: string;
  approvedMessageId?: string | null;
  title: string;
  description?: string | null;
  status: MetaCampaignStatus;
  audienceFilter: Record<string, unknown>;
  policySummary: Record<string, unknown>;
  scheduledAt?: string | null;
  launchedAt?: string | null;
  totalRecipients: number;
  eligibleRecipients: number;
  blockedRecipients: number;
  sentCount: number;
  deliveredCount: number;
  readCount: number;
  failedCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface MetaConversationNote {
  id: string;
  conversationId: string;
  matterId?: string | null;
  authorUserId?: string | null;
  authorName?: string | null;
  body: string;
  mentions: string[];
  noteMetadata: Record<string, unknown>;
  createdAt: string;
}

export interface MetaLeadDrawerData {
  conversation: MetaSocialConversation;
  matter?: MatterCard | null;
  notes: MetaConversationNote[];
  recentMeetings: Meeting[];
  canEditMatter: boolean;
  canManageConversation: boolean;
}

export interface MetaCopilotResult {
  summary: string;
  recommendedAction: string;
  replySuggestions: string[];
  suggestedPublicReply: boolean;
  automationEnabled: boolean;
  automationRuleId?: string | null;
  automationTitle?: string | null;
  modelUsed?: string | null;
  provider?: string | null;
}

export interface MessageOptimizationResult {
  suggestions: string[];
}

export type WhatsAppConnectionType = 'QR_CODE' | 'OFFICIAL_MANUAL' | 'TECH_PROVIDER';
export type WhatsAppConnectionRuntimeStatus = 'connected' | 'pending' | 'disconnected' | 'error';
export type MetaTokenLifecycleState = 'not_applicable' | 'unknown' | 'valid' | 'expiring' | 'expired';
export type MetaTemplateStatus = 'approved' | 'pending' | 'rejected' | 'paused' | 'disabled';
export type CampaignStatus = 'draft' | 'scheduled' | 'running' | 'paused' | 'completed' | 'stopped' | 'failed';
export type CampaignRecipientStatus = 'queued' | 'scheduled' | 'processing' | 'sent' | 'delivered' | 'read' | 'failed' | 'skipped';
export type OptInStatus = 'opted_in' | 'opted_out';

export interface WhatsAppConnectionSummary {
  id: string;
  connectionType: WhatsAppConnectionType;
  transportMode?: 'official_api' | 'coexistence' | 'qr_gateway';
  isDefault: boolean;
  phoneNumberId?: string | null;
  phoneNumber?: string | null;
  displayPhoneNumber?: string | null;
  displayName?: string | null;
  businessAccountId?: string | null;
  wabaId?: string | null;
  instanceId?: string | null;
  qrProvider?: string | null;
  qrStatus?: string | null;
  status: WhatsAppConnectionRuntimeStatus;
  webhookRegistered: boolean;
  connectedAt?: string | null;
  disconnectedAt?: string | null;
  updatedAt: string;
  lastError?: string | null;
  /** BYOA multi-tenant: true quando esta conexão tem o próprio App Secret cifrado salvo. */
  appSecretConfigured?: boolean;
  coexistenceSyncStatus?: 'not_applicable' | 'requested' | 'contacts_received' | 'completed' | 'partial' | 'failed';
  coexistenceContactsSyncRequestId?: string | null;
  coexistenceHistorySyncRequestId?: string | null;
  coexistenceSyncRequestedAt?: string | null;
  tokenExpiresAt?: string | null;
  tokenLifecycleState?: MetaTokenLifecycleState;
  tokenExpiresInDays?: number | null;
}

export interface MetaEmbeddedSignupConfig {
  available: boolean;
  manualCloudApiAvailable: boolean;
  coexistenceAvailable: boolean;
  techProviderReady: boolean;
  appId?: string | null;
  configId?: string | null;
  solutionId?: string | null;
  graphApiVersion: string;
  featureType: 'whatsapp_business_app_onboarding';
  missingConfiguration: string[];
  missingTechProviderConfiguration: string[];
}

export interface WhatsAppWebhookInfo {
  webhookUrl: string;
  verifyToken?: string | null;
  verifyTokenConfigured: boolean;
  appSecretConfigured: boolean;
  instructions: string[];
  recommendedFields?: string[];
}

export interface WhatsAppManualConnectionPayload {
  accessToken: string;
  phoneNumberId: string;
  businessAccountId: string;
  registrationPin?: string | null;
  isDefault?: boolean;
  /** App Secret do app Meta do escritório (BYOA). Undefined = não altera o valor salvo. */
  appSecret?: string | null;
}

export type WhatsAppConnectionTier = 'tier_250' | 'tier_1k' | 'tier_10k' | 'tier_100k' | 'unlimited';

export type WhatsAppQualityRating = 'verde' | 'amarelo' | 'vermelho';

/** Painel de saúde do número (W5.6): tier, quota diária, quality e rampa. */
export interface WhatsAppConnectionHealth {
  connectionId: string;
  tier: WhatsAppConnectionTier;
  dailyLimit: number | null;
  dailyUsed: number;
  dailyRemaining: number | null;
  windowStartedAt: string;
  qualityRating: WhatsAppQualityRating | null;
  rampUpStage: number;
  lastSyncedAt?: string | null;
  syncedFromMeta: boolean;
  syncMessage: string;
  honest: boolean;
}

export interface MetaTemplate {
  id: string;
  whatsappConnectionId: string;
  externalTemplateId?: string | null;
  name: string;
  category?: string | null;
  language: string;
  status: MetaTemplateStatus;
  qualityRating?: string | null;
  components: Array<Record<string, unknown>>;
  rawPayload: Record<string, unknown>;
  lastSyncedAt: string;
  updatedAt: string;
}

/** Card consumido pela biblioteca de modelos na conversa. A API pode evoluir
 * campos de telemetria sem quebrar o fluxo: os campos opcionais permanecem
 * explicitamente não verificados quando ausentes. */
export type TemplatePickerIntent = 'follow_up' | 'reativacao' | 'documentos' | 'audiencia' | 'cobranca' | 'proposta' | null;
export type TemplatePickerBinding =
  | { kind: 'field'; fieldKey: string }
  | { kind: 'prompt' }
  | { kind: 'literal'; value: string };
export interface TemplatePickerVariable {
  position: number;
  scope: 'body' | 'header' | 'button';
  binding?: TemplatePickerBinding | null;
  example?: string | null;
}
export interface TemplatePickerCard {
  id: string;
  metaTemplateId?: string | null;
  name: string;
  language: string;
  intent: TemplatePickerIntent;
  sector?: 'comercial' | 'juridico' | 'financeiro' | null;
  metaCategory?: string | null;
  status: string;
  header?: { type?: string; text?: string | null } | null;
  bodyText: string;
  footerText?: string | null;
  buttons: Array<{ type?: string; label?: string; text?: string; url?: string | null; hasDynamicSuffix?: boolean }>;
  variables: TemplatePickerVariable[];
  isSequence?: boolean;
  sequenceStepCount?: number | null;
  usageCount30d?: number | null;
  lastUsedAt?: string | null;
  lastSentToContactAt?: string | null;
}
export interface TemplatePickerSuggestion {
  templateId: string;
  reasonCode: 'conversation_cold' | 'matter_awaiting_documents' | 'receivable_open' | 'hearing_upcoming' | string;
  reasonText: string;
  rank: number;
}
export interface TemplatePickerResponse {
  cards: TemplatePickerCard[];
  /** Optional approved+pending source used only by sequence authoring. */
  sequenceTemplates?: TemplatePickerCard[];
  suggestions?: TemplatePickerSuggestion[];
  sectorMode?: 'condensed' | 'by_sector';
  userSector?: string | null;
  refreshedAt?: string | null;
}

/** Payload used by the conversation sequence authoring surface.  Sequence
 * stages are intentionally explicit here: the UI must not infer a journey
 * from a template's raw provider payload. */
export interface WhatsAppSequenceStage {
  id: string;
  templateId?: string | null;
  templateName?: string | null;
  templateLanguage?: string | null;
  body?: string | null;
  status?: string | null;
  delayValue?: number | null;
  delayUnit?: 'hours' | 'days' | null;
  scheduleMode?: 'now' | 'delay' | null;
  scheduledAt?: string | null;
  bindings?: Array<{ fieldKey: string; value?: string | null }>;
}

export interface WhatsAppSequenceStartPayload {
  threadId: string;
  matterId?: string | null;
  contactId?: string | null;
  templateId?: string | null;
  stages: WhatsAppSequenceStage[];
  interruptOnReply: boolean;
  idempotencyKey: string;
}

export interface WhatsAppSequenceStartResult {
  campaignId?: string;
  flowId?: string | null;
  firstRecipientId: string;
  dispatchState: 'accepted' | 'failed' | 'uncertain';
  code?: string | null;
  status?: string;
  message?: string;
}
export interface TemplateVariableValue {
  fieldKey: string;
  label: string;
  resolvedValue: string | null;
}
export interface TemplateVariableGroup {
  key: string;
  label: string;
  values: TemplateVariableValue[];
}
export interface TemplateVariablesResponse {
  groups: TemplateVariableGroup[];
}
export interface TemplateQuickSendResult {
  id?: string;
  messageId?: string | null;
  status?: string;
  body?: string | null;
  message?: string | null;
  templateId?: string;
}

export interface MetaTemplateAnalysis {
  predictedCategory: 'utility' | 'marketing' | 'authentication';
  approvalLikelihood: 'high' | 'medium' | 'low';
  confidence: number;
  costBand: string;
  recommendation: string;
  summary: string;
  signals: string[];
  provider: string;
  modelUsed: string;
  metaCategorySuggested?: 'utility' | 'marketing' | 'authentication' | null;
  marketingSignals: string[];
  oabRisk: {
    level: 'low' | 'medium' | 'high';
    signals: string[];
    requiresAcknowledgement: boolean;
  };
}

export type TemplateCategory = 'marketing' | 'utility' | 'authentication';
export type TemplateLocalStatus =
  | 'draft'
  | 'submission_prepared'
  | 'pending_internal_approval'
  | 'catalog_queued'
  | 'catalog_pending_gate'
  | 'catalog_failed'
  | 'catalog_name_reserved';

export interface TemplateButton {
  type: 'QUICK_REPLY' | 'URL' | 'PHONE_NUMBER' | 'COPY_CODE';
  text: string;
  url?: string | null;
  phoneNumber?: string | null;
  example?: string[] | null;
}

export interface TemplateComponentsInput {
  headerType?: 'NONE' | 'TEXT' | 'IMAGE' | 'VIDEO' | 'DOCUMENT';
  headerText?: string | null;
  headerExample?: string | null;
  bodyText: string;
  bodyExamples?: string[];
  footerText?: string | null;
  buttons: TemplateButton[];
}

export interface UnifiedTemplate {
  id: string;
  whatsappConnectionId: string;
  externalTemplateId?: string | null;
  name: string;
  category?: string | null;
  language: string;
  status: MetaTemplateStatus;
  qualityRating?: string | null;
  components: Array<Record<string, unknown>>;
  isDraft: boolean;
  localStatus?: TemplateLocalStatus | null;
  validationErrors: string[];
  metaSubmissionAvailable: boolean;
  rejectedReason?: string | null;
  previousCategory?: string | null;
  createdAt: string;
  updatedAt: string;
  lastSyncedAt?: string | null;
  removedAtSourceAt?: string | null;
}

export interface TemplateSubmitResult {
  template: UnifiedTemplate;
  submittedToMeta: boolean;
  message: string;
}

export interface UtilityTemplateCatalogItem {
  key: string;
  catalogVersion: string;
  itemVersion: number;
  metaName: string;
  category: 'utility';
  language: string;
  body: string;
  variables: Array<{ position: number; key: string; example: string }>;
  practiceAreas: string[];
  templateId?: string | null;
  externalTemplateId?: string | null;
  status?: MetaTemplateStatus | null;
  provisioningStatus: string;
  rejectionReason?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  attempt: number;
  submittedAt?: string | null;
  updatedAt?: string | null;
  canRetry: boolean;
}

export interface UtilityTemplateCatalog {
  catalogVersion: string;
  connectionId?: string | null;
  gateOpen: boolean;
  items: UtilityTemplateCatalogItem[];
}

export interface CampaignAudienceFilter {
  audienceListIds: string[];
  includeCrmContacts: boolean;
  columnIds: string[];
  practiceAreas: string[];
  leadSources: string[];
  assignedUserIds: string[];
  classifications: string[];
  queueStatuses: string[];
  createdFrom?: string | null;
  createdTo?: string | null;
  hasOpenTasks?: boolean | null;
  includeUnlinkedContacts: boolean;
  includeBlocked: boolean;
  search?: string | null;
}

export interface AudienceListMember {
  id: string;
  contactId?: string | null;
  matterId?: string | null;
  phone: string;
  name?: string | null;
  email?: string | null;
  status: 'valid' | 'invalid' | 'suppressed' | string;
  sourceRow?: number | null;
  memberMetadata: Record<string, unknown>;
  createdAt: string;
}

export interface AudienceList {
  id: string;
  createdById?: string | null;
  name: string;
  description?: string | null;
  source: 'manual' | 'csv' | 'crm' | 'flow' | string;
  status: 'active' | 'archived' | string;
  memberCount: number;
  validCount: number;
  invalidCount: number;
  duplicateCount: number;
  importMetadata: Record<string, unknown>;
  members: AudienceListMember[];
  sensitiveDataHidden: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface AudienceListImportResult {
  audienceList: AudienceList;
  imported: number;
  updated: number;
  invalid: number;
  duplicates: number;
  totalRows: number;
  optInsRecorded: number;
  normalized: number;
}

export interface CampaignAudienceSummary {
  id: string;
  filters: Record<string, unknown>;
  estimatedRecipients: number;
  sampleRecipients: CampaignAudienceRecipient[];
}

export interface CampaignAudienceRecipient {
  name?: string | null;
  phone?: string | null;
  [key: string]: unknown;
}

export interface CampaignTemplateSnapshot {
  id: string;
  sourceTemplateId?: string | null;
  templateName: string;
  category?: string | null;
  language: string;
  status: MetaTemplateStatus;
  headerParameters: string[];
  bodyParameters: string[];
  components: Array<Record<string, unknown>>;
  rawPayload: Record<string, unknown>;
}

export interface CampaignRecipient {
  id: string;
  contactId?: string | null;
  matterId?: string | null;
  phone: string;
  name?: string | null;
  wamid?: string | null;
  status: CampaignRecipientStatus;
  lastError?: string | null;
  sendContext: Record<string, unknown>;
  sentAt?: string | null;
  deliveredAt?: string | null;
  readAt?: string | null;
}

export interface WhatsAppCampaign {
  id: string;
  whatsappConnectionId: string;
  createdById?: string | null;
  followUpFlowId?: string | null;
  flowStartMode: 'manual' | 'on_delivery' | 'on_reply';
  title: string;
  description?: string | null;
  status: CampaignStatus;
  scheduledAt?: string | null;
  launchedAt?: string | null;
  completedAt?: string | null;
  pausedAt?: string | null;
  totalRecipients: number;
  eligibleRecipients: number;
  blockedRecipients: number;
  sentCount: number;
  deliveredCount: number;
  readCount: number;
  failedCount: number;
  audience?: CampaignAudienceSummary | null;
  templateSnapshot?: CampaignTemplateSnapshot | null;
  sampleRecipients: CampaignRecipient[];
  createdAt: string;
  updatedAt: string;
}

export interface MetaCommercialRateCard {
  id: string;
  rateCardKey: string;
  category: string;
  countryCode: string;
  currency: string;
  commercialRate: string;
  effectiveFrom: string;
  effectiveTo?: string | null;
  sourceUrl: string;
  checkedAt: string;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface MetaCommercialRateCardUpsert {
  category: string;
  countryCode: string;
  currency: string;
  commercialRate: string;
  effectiveFrom: string;
  effectiveTo?: string | null;
  sourceUrl: string;
  checkedAt: string;
  isActive: boolean;
}

export interface MetaCommercialChargeCurrencyTotal {
  currency: string;
  estimatedRecipientCount: number;
  confirmedRecipientCount: number;
  estimatedCommercialCharge: string;
  confirmedCommercialCharge: string;
}

export interface MetaCommercialChargeLine {
  rateCardKey: string;
  templateCategory: string;
  recipientCountryCode: string;
  countryResolution: string;
  rateCountryCode: string;
  rateMatch: 'exact' | 'global_fallback';
  currency: string;
  commercialRate: string;
  rateEffectiveFrom: string;
  rateEffectiveTo?: string | null;
  rateSourceUrl: string;
  rateCheckedAt: string;
  estimatedRecipientCount: number;
  confirmedRecipientCount: number;
  estimatedCommercialCharge: string;
  confirmedCommercialCharge: string;
}

export interface MetaCommercialMissingRate {
  campaignId: string;
  templateCategory?: string | null;
  recipientCountryCode: string;
  countryResolution: string;
  chargeStage: 'estimated' | 'confirmed';
  recipientCount: number;
  rateReferenceFrom: string;
  rateReferenceTo: string;
  reason: 'missing_template_category' | 'no_effective_rate' | 'ambiguous_currency' | 'overlapping_effective_rates';
  candidateRateCardKeys: string[];
}

export interface CampaignMetaCommercialCharge {
  campaignId: string;
  title: string;
  templateCategory?: string | null;
  eligibleRecipients: number;
  confirmedRecipients: number;
  commercialChargeLines: MetaCommercialChargeLine[];
  totalsByCurrency: MetaCommercialChargeCurrencyTotal[];
  missingRates: MetaCommercialMissingRate[];
}

export interface WhatsAppCommercialChargeSummary {
  workspaceId: string;
  requestedCampaignId?: string | null;
  generatedAt: string;
  chargeBasis: 'delivered_messages';
  internalProviderCostExposed: false;
  campaigns: CampaignMetaCommercialCharge[];
  totalsByCurrency: MetaCommercialChargeCurrencyTotal[];
  missingRates: MetaCommercialMissingRate[];
}

export interface OptInRecord {
  id: string;
  contactId?: string | null;
  phone: string;
  channel: string;
  status: OptInStatus;
  source?: string | null;
  details: Record<string, unknown>;
  grantedAt: string;
  revokedAt?: string | null;
  updatedAt: string;
}

export interface SuppressionEntry {
  id: string;
  contactId?: string | null;
  phone: string;
  reason: string;
  details: Record<string, unknown>;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

// ── Auth Types ──

export interface AuthUser {
  id: string;
  name: string;
  role: string;
  initials: string;
  email: string;
  phone?: string | null;
  isInternalStaff?: boolean;
  isPlatformAdmin?: boolean;
  isPlatformRoot?: boolean;
  platformRolePackage?: string | null;
  platformAccessActive?: boolean;
  platformPermissions?: PlatformPermission[];
  /** Backend may require a password rotation on the first authenticated session. */
  requiresPasswordSetup?: boolean;
  requiresPasswordChange?: boolean;
  mustChangePassword?: boolean;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  status: string;
  isActive: boolean;
  onboardingCompleted?: boolean;
  onboardingProgress?: number;
  nextOnboardingStep?: string | null;
  planSummary?: WorkspacePlanSummary | null;
}

export interface WorkspacePlanSummary {
  tier?: string | null;
  name?: string | null;
  subscriptionStatus?: string | null;
  maxUsers?: number | null;
  maxWorkspaces?: number | null;
  maxConnectedWhatsapps?: number | null;
  maxAiAgents?: number | null;
  maxAiConversationsPerMonth?: number | null;
  maxAudioMinutesPerMonth?: number | null;
  maxStorageBytes?: number | null;
  maxFiles?: number | null;
  maxFilesPerConversation?: number | null;
  automationLimit?: number | null;
  maxOperationalRecipients?: number | null;
  maxCampaignRecipientsPerMonth?: number | null;
  seatsUsed: number;
  seatsRemaining?: number | null;
  connectedWhatsappsUsed: number;
  connectedWhatsappsRemaining?: number | null;
  aiAgentsUsed: number;
  aiAgentsRemaining?: number | null;
  aiConversationsUsedThisMonth: number;
  aiConversationsRemainingThisMonth?: number | null;
  audioMinutesUsedThisMonth: number;
  audioMinutesRemainingThisMonth?: number | null;
  storageBytesUsed: number;
  storageBytesRemaining?: number | null;
  filesUsed: number;
  filesRemaining?: number | null;
  automationsUsed: number;
  automationsRemaining?: number | null;
  operationalRecipientsUsed: number;
  operationalRecipientsRemaining?: number | null;
  campaignRecipientsUsedThisMonth: number;
  campaignRecipientsRemainingThisMonth?: number | null;
  maxMonitoredProcesses?: number | null;
  monitoredProcessesUsed: number;
  monitoredProcessesRemaining?: number | null;
  supportTier?: string | null;
  hasDigitalSignature: boolean;
  hasCalendar: boolean;
  hasEmail: boolean;
  hasWebhooks: boolean;
  hasAdvancedAudit: boolean;
  hasSuccessManager: boolean;
  isAtLimit: boolean;
}

export interface AiTokenModelBreakdown {
  model: string;
  provider: string;
  runs: number;
  inputTokens: number;
  outputTokens: number;
}

export interface AiCostModelBreakdown extends AiTokenModelBreakdown {
  costUsd: number;
}

export interface AiTokenTaskBreakdown {
  taskClass: string;
  runs: number;
  inputTokens: number;
  outputTokens: number;
}

export interface AiCostTaskBreakdown extends AiTokenTaskBreakdown {
  costUsd: number;
}

export interface AiCostRuntimeGates {
  modelApisEnabled: boolean;
  onlineApproved: boolean;
  externalActionsEnabled: boolean;
}

export type AiCostAlertLevel = 'none' | 'ok' | 'warning' | 'critical' | 'blocked';
export type AiQuotaState = 'configured' | 'local_legacy' | 'missing' | 'not_applicable';

export interface WorkspaceAiTokenSummary {
  workspaceId?: string | null;
  periodStart: string;
  periodEnd: string;
  totalRuns: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  monthlyLimit?: number | null;
  policyMonthlyLimit?: number | null;
  alertAtPct: number;
  hardLimit: boolean;
  usedPercent?: number | null;
  remainingTokens?: number | null;
  alertLevel: AiCostAlertLevel;
  quotaState: AiQuotaState;
  byModel: AiTokenModelBreakdown[];
  byTask: AiTokenTaskBreakdown[];
  gates: AiCostRuntimeGates;
}

export interface PlatformAiCostSummary extends Omit<WorkspaceAiTokenSummary, 'byModel' | 'byTask'> {
  costUsd: number;
  byModel: AiCostModelBreakdown[];
  byTask: AiCostTaskBreakdown[];
}

export interface WorkspaceAiCostPolicyUpdate {
  monthlyLimit: number | null;
  alertAtPct: number;
  hardLimit: boolean;
}

// ── Conciliação de cobrança incerta de agent run por provider de IA ───────

export interface PlatformAgentBillingReconciliationRun {
  id: string;
  workspaceId: string;
  matterId: string;
  agentName: string;
  triggerSource: string;
  status: AgentRunStatus;
  errorMessage: string | null;
  aiProvider: string | null;
  modelUsed: string | null;
  inputTokens: number;
  outputTokens: number;
  outputPayload: Record<string, unknown> | null;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformAgentBillingReconciliationPage {
  total: number;
  limit: number;
  offset: number;
  items: PlatformAgentBillingReconciliationRun[];
}

export interface PlatformAgentBillingReconciliationDecision {
  decision: 'charged' | 'not_charged';
  actualCostUsd?: string | null;
  evidenceReference: string;
  reason: string;
  providerRequestId?: string | null;
}

// ── Roteamento de modelos por classe de tarefa + BYOK (S4) ────────────────

export type AiRouteSource = 'workspace' | 'platform' | 'unconfigured';

export interface AiModelPrice {
  inputPerMillionUsd: number;
  outputPerMillionUsd: number;
  cachedInputPerMillionUsd?: number | null;
}

export interface AiModelOption {
  provider: string;
  model: string;
  price?: AiModelPrice | null;
}

export interface AiTaskClassRoute {
  taskClass: string;
  provider: string | null;
  model: string | null;
  source: AiRouteSource;
  platformProvider?: string | null;
  platformModel?: string | null;
  price?: AiModelPrice | null;
}

export interface WorkspaceAiModelRouting {
  catalogVersion: string;
  classes: AiTaskClassRoute[];
  options: AiModelOption[];
}

export interface WorkspaceAiModelRoutingUpdate {
  routes: Record<string, { provider: string; model: string } | null>;
}

export interface WorkspaceAiCredentialStatus {
  provider: string;
  configured: boolean;
  isActive: boolean;
  apiKeyMasked?: string | null;
  updatedAt?: string | null;
}

export interface PlatformAiByokProvider {
  provider: string;
  isActive: boolean;
  apiKeyMasked: string;
  updatedAt?: string | null;
}

export interface PlatformAiByokWorkspace {
  workspaceId: string;
  workspaceName: string;
  providers: PlatformAiByokProvider[];
  customRouteClasses: string[];
}

export interface PlatformAiModelsOverview {
  catalogVersion: string;
  defaults: AiTaskClassRoute[];
  byokWorkspaces: PlatformAiByokWorkspace[];
}

export type AiTokenPackageRequestStatus = 'requested' | 'approved' | 'rejected' | 'revoked';

export interface AiTokenPackage {
  id: string;
  key: string;
  name: string;
  description?: string | null;
  tokenQuantity: number;
  commercialPriceCents?: number | null;
  currency: string;
  isActive: boolean;
  sortOrder: number;
  createdAt: string;
  updatedAt: string;
}

export interface WorkspaceAiTokenPackageRequest {
  id: string;
  workspaceId: string;
  packageId: string;
  requestedByUserId: string;
  decidedByUserId?: string | null;
  status: AiTokenPackageRequestStatus;
  packageKeySnapshot: string;
  packageNameSnapshot: string;
  tokenQuantitySnapshot: number;
  commercialPriceCentsSnapshot?: number | null;
  currencySnapshot: string;
  periodStart: string;
  periodEnd: string;
  requestNote?: string | null;
  decisionNote?: string | null;
  decidedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AiTokenPackageCatalog {
  packages: AiTokenPackage[];
  requests: WorkspaceAiTokenPackageRequest[];
  supportReleaseRequired: true;
  selfActivationEnabled: false;
}

export interface AiTokenPackageUpsert {
  name: string;
  description?: string | null;
  tokenQuantity: number;
  commercialPriceCents?: number | null;
  currency: string;
  isActive: boolean;
  sortOrder: number;
}

export interface WorkspaceSettingsProfile extends WorkspaceSummary {
  legalName?: string | null;
  description?: string | null;
  setupMode?: string;
  settings?: Record<string, unknown>;
  planSummary?: WorkspacePlanSummary | null;
}

export interface WorkspaceMembershipSummary {
  id: string;
  workspaceId: string;
  userId: string;
  role: string;
  status: string;
  isDefault: boolean;
  accessPackage?: string | null;
  workspace: WorkspaceSummary;
  rolePermissions?: Permission[];
  permissionGrants?: Permission[];
  permissionDenials?: Permission[];
  permissions: Permission[];
  canViewSensitiveLeadData?: boolean;
  canEditSensitiveLeadData?: boolean;
}

export interface SessionContext {
  surface: 'workspace' | 'platform' | 'support';
  activeWorkspace: WorkspaceSummary | null;
  availableWorkspaces: WorkspaceMembershipSummary[];
  permissions: Permission[];
  isInternalStaff: boolean;
  isPlatformRoot?: boolean;
  platformRolePackage?: string | null;
  platformPermissions?: PlatformPermission[];
  activeRole: string | null;
  /** Optional first-access credential rotation contract. */
  requiresPasswordSetup?: boolean;
  requiresPasswordChange?: boolean;
  mustChangePassword?: boolean;
}

export interface BlackboxSessionManifest {
  userId: string;
  isPlatformRoot: boolean;
  rolePackage: string | null;
  permissions: PlatformPermission[];
  availableModules: string[];
  maskedFields: string[];
  supportSession: Record<string, unknown> | null;
  featureFlags: Record<string, boolean>;
  frontendVersion: string;
  backendVersion: string;
}

export type OnboardingStepId =
  | 'welcome'
  | 'whatsapp'
  | 'areas'
  | 'template'
  | 'follow_up'
  | 'playbook'
  | 'integrations'
  | 'review';

export type IntegrationSetupStatus = 'not_started' | 'connected' | 'skipped';

export interface WorkspaceIntegrationSetupStatus {
  key: string;
  label: string;
  description: string;
  required: boolean;
  status: IntegrationSetupStatus;
}

export interface OnboardingStepStatus {
  id: OnboardingStepId;
  title: string;
  description: string;
  required: boolean;
  completed: boolean;
  skipped: boolean;
  blocked: boolean;
  status: string;
}

export interface WorkspaceOnboardingState {
  currentStep: OnboardingStepId;
  onboardingCompleted: boolean;
  onboardingProgress: number;
  nextOnboardingStep: OnboardingStepId | null;
  pendingRequiredSteps: OnboardingStepId[];
  completedSteps: OnboardingStepId[];
  skippedSteps: OnboardingStepId[];
  reviewCompleted: boolean;
  playbookId: string | null;
  integrations: WorkspaceIntegrationSetupStatus[];
  steps: OnboardingStepStatus[];
  areaActivations: WorkspaceAreaActivation[];
  notificationRecipients: WorkspaceNotificationRecipient[];
}

export interface WorkspaceOnboardingUpdate {
  currentStep?: OnboardingStepId;
  markStepCompleted?: OnboardingStepId;
  skipStep?: OnboardingStepId;
  integrations?: Array<Pick<WorkspaceIntegrationSetupStatus, 'key' | 'status'>>;
  reviewCompleted?: boolean;
  playbookId?: string;
  areaActivations?: WorkspaceAreaActivation[];
  notificationRecipients?: WorkspaceNotificationRecipient[];
}

// ── Filters ──

export interface Filters {
  search: string;
  status: MicroStatus[];
  priority: Priority[];
  urgency: Urgency[];
  agentId: string[];
  columnId: KanbanColumnId[];
  classification: ClientClassification[];
  leadSource: string[];
  closeIntent: string[];
  documentReadiness: string[];
  isBlocked: boolean | null;
  needsApproval: boolean | null;
  hasUnreadMessages: boolean | null;
  hasFinancialPending: boolean | null;
  docScoreBelow: number | null;
  updatedWithinDays: number | null;
  minClaimValue: number | null;
  maxClaimValue: number | null;
}

export type ModuleId =
  | 'dashboard'
  | 'kanban'
  | 'lista'
  | 'relatorios'
  | 'aprovacoes'
  | 'configuracoes'
  | 'mensagens'
  | 'campanhas'
  | 'admin'
  | 'automacoes'
  | 'platform'
  | 'whatsapp-inbox'
  | 'whatsapp-campaigns'
  | 'whatsapp-templates'
  | 'whatsapp-meta-templates'
  | 'whatsapp-automations'
  | 'whatsapp-connections'
  | 'instagram-inbox'
  | 'instagram-comments'
  | 'instagram-automations'
  | 'instagram-approved'
  | 'instagram-connections'
  | 'instagram-entrypoints'
  | 'messenger-inbox'
  | 'messenger-comments'
  | 'messenger-automations'
  | 'messenger-approved'
  | 'messenger-connections'
  | 'messenger-entrypoints';

// ── Admin Types ──

export type PermissionModule =
  | 'dashboard' | 'kanban' | 'lista' | 'mensagens' | 'relatorios'
  | 'aprovacoes' | 'configuracoes' | 'admin' | 'workspace'
  | 'users' | 'memberships' | 'roles' | 'permissions'
  | 'finance' | 'integrations' | 'automations' | 'agents'
  | 'executives' | 'notifications' | 'branding' | 'security' | 'audit' | 'lead_data' | 'processos'
  | 'tasks'
  // Cadeia jurídica — Demandas (Onda 2/5). Mesmo módulo já usado pelo
  // backend em `require_permission("legal", ...)` (ver legal_demands.py,
  // legal_case_strategy.py, protocol_folder.py) e já concedido por padrão a
  // owner/admin/advogado em app/core/rbac.py — só faltava aqui.
  | 'legal';
export type PermissionAction =
  | 'view' | 'create' | 'edit' | 'delete' | 'approve'
  | 'export' | 'manage_ai' | 'send_message' | 'manage'
  | 'assign' | 'review' | 'complete' | 'comment';
export type PermissionScope = 'all' | 'own' | 'team' | 'workspace';

export interface Permission {
  module: PermissionModule;
  action: PermissionAction;
  scope: PermissionScope;
}

export interface AdminRole {
  id: string;
  name: string;
  description: string;
  permissions: Permission[];
  color: string;
}

export interface WorkspaceAccessPackage {
  key: string;
  label: string;
  description: string;
  workspaceRole: string;
  permissionGrants: Permission[];
  permissionDenials: Permission[];
  permissions: Permission[];
  sensitiveDataPolicy: 'hidden' | 'view' | 'edit' | string;
}

export interface AdminUser {
  id: string;
  membershipId?: string;
  name: string;
  initials: string;
  email: string;
  phone?: string | null;
  type: AgentType;
  role: string;
  color: string;
  avatar?: string;
  isActive: boolean;
  lastLogin: string;
  roles: string[]; // role IDs
  accessPackage?: string | null;
  directPermissions: Permission[];
  deniedPermissions?: Permission[];
  rolePermissions?: Permission[];
  effectivePermissions?: Permission[];
  canViewSensitiveLeadData?: boolean;
  canEditSensitiveLeadData?: boolean;
  membershipStatus?: 'active' | 'invited' | 'suspended';
  invitationId?: string | null;
  invitationStatus?: 'pending' | 'accepted' | 'revoked' | 'expired' | null;
  invitationExpiresAt?: string | null;
  invitationDeliveryId?: string | null;
  invitationDeliveryStatus?: 'pending' | 'sending' | 'sent' | 'failed' | 'blocked' | null;
  invitationDeliveryErrorCode?: string | null;
  // Bot-specific
  aiModel?: string;
  maxActionsPerDay?: number;
  requiresHumanApproval?: boolean;
  description?: string;
}

export interface PlatformPermission {
  module: string;
  action: string;
}

export interface PlatformRolePackage {
  key: string;
  label: string;
  description: string;
  permissions: PlatformPermission[];
  assignable: boolean;
  requiresMfa: boolean;
}

export interface PlatformStaffUser {
  id: string;
  name: string;
  email: string;
  initials: string;
  rolePackage: string;
  roleLabel: string;
  accessActive: boolean;
  isPlatformRoot: boolean;
  permissionGrants: PlatformPermission[];
  permissionDenials: PlatformPermission[];
  permissions: PlatformPermission[];
  requiresPasswordSetup: boolean;
  requiresPasswordChange: boolean;
  passwordSetupDeliveryId?: string | null;
  activeSessionCount: number;
  lastLoginAt?: string | null;
  createdAt: string;
}

export interface PlatformStaffPage {
  items: PlatformStaffUser[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  sort: Array<{ field: string; direction: string }>;
}

export type AutomationStageMode = 'human_only' | 'ai_only' | 'human_in_the_loop' | 'ai_with_human_approval';
export type CostProfile = 'economic' | 'balanced' | 'quality';
export type ContextMode = 'essential' | 'balanced' | 'full';
export type AutomationPreset = 'economic' | 'balanced' | 'supervised' | 'custom';

export interface AutomationPresetOption {
  key: AutomationPreset;
  label: string;
  description: string;
}

export interface WorkspaceStagePolicy {
  stageKey: string;
  label: string;
  description: string;
  mode: AutomationStageMode;
  agentKey: string;
  model?: string | null;
  costProfile: CostProfile;
  contextMode: ContextMode;
  useRag: boolean;
  multimodalEnabled: boolean;
  humanReviewRequired: boolean;
}

export interface WorkspaceAutomationSettings {
  onboardingMode: string;
  preset: AutomationPreset;
  availablePresets: AutomationPresetOption[];
  stagePolicies: WorkspaceStagePolicy[];
}

export interface ConversationAutomationRule {
  id: string;
  label: string;
  enabled: boolean;
  triggerType: string;
  keyword?: string | null;
  replyText?: string | null;
  flowTemplateId?: string | null;
  targetQueueStatus?: ConversationQueueStatus | null;
  targetColumnId?: KanbanColumnId | null;
  escalateToHuman: boolean;
  onlyFirstMessage: boolean;
  silenceWindowHours: number;
}

export interface WhatsAppAutomationSettings {
  enabled: boolean;
  onboardingCompleted: boolean;
  timezone: string;
  businessHoursStart: number;
  businessHoursEnd: number;
  rules: ConversationAutomationRule[];
}

export type ModelTier = 'simple' | 'medium' | 'complex' | 'premium';
export type FollowUpProfile = 'gentle' | 'balanced' | 'assertive';

export interface FlowTemplateReference {
  id: string;
  name: string;
  description?: string | null;
  practiceArea: string;
  trigger: string;
  isActive: boolean;
  nodeCount: number;
  promptCount: number;
}

export interface PracticeAreaStagePrompt {
  stageKey: string;
  title: string;
  prompt: string;
}

export interface PracticeAreaTemplate {
  key: string;
  label: string;
  description: string;
  flowPracticeArea: string;
  triggerSummary: string;
  basePrompt: string;
  defaultFollowUpProfile: FollowUpProfile;
  humanInterventionMode: AutomationStageMode;
  defaultAgentKey: string;
  modelTier: ModelTier;
  checklist: string[];
  allowedFlowTemplateIds: string[];
  stagePrompts: PracticeAreaStagePrompt[];
  availableFlows: FlowTemplateReference[];
}

export interface ModelCatalogItem {
  key: string;
  label: string;
  provider: string;
  tier: ModelTier;
  description: string;
  experimental: boolean;
}

export interface AgentPromptPolicy {
  agentKey: string;
  label: string;
  prompt: string;
}

export interface AiGovernanceSettings {
  gemmaEnabled: boolean;
  simpleModel: string;
  mediumModel: string;
  complexModel: string;
  premiumModel: string;
  availableModels: ModelCatalogItem[];
  agentPrompts: AgentPromptPolicy[];
}

export interface NotificationSettingsProfile {
  whatsappEnabled: boolean;
  emailEnabled: boolean;
  dailyDigestHour: number;
  recipients: WorkspaceNotificationRecipient[];
  rules: WorkspaceNotificationRule[];
}

export interface WorkspaceNotificationRecipient {
  id: string;
  name: string;
  email?: string | null;
  phone?: string | null;
  role: string;
  userId?: string | null;
  locked?: boolean;
}

export interface WorkspaceNotificationRule {
  key: 'contractSent' | 'contractSigned' | 'meetingScheduled' | 'meetingReminder' | 'dailyDigest' | string;
  label: string;
  description: string;
  enabled: boolean;
  emailEnabled: boolean;
  whatsappEnabled: boolean;
  reminderMinutesBefore?: number | null;
}

export interface ConversationAssignee {
  id: string;
  name: string;
  email: string;
  phone?: string | null;
  role: string;
}

export interface MessageTemplate {
  id: string;
  source: 'system' | 'workspace' | string;
  kind: 'message' | 'proposal' | 'contract' | 'contract_message' | string;
  category: string;
  label: string;
  body: string;
  format: 'text' | 'pdf' | string;
  favorite: boolean;
  title?: string | null;
  footer?: string | null;
}

export interface MessageTemplateSettings {
  systemTemplates: MessageTemplate[];
  workspaceTemplates: MessageTemplate[];
  proposalTemplates: MessageTemplate[];
  contractTemplates: MessageTemplate[];
}

export type MeetingStatus = 'scheduled' | 'completed' | 'cancelled';

export interface Meeting {
  id: string;
  workspaceId: string;
  matterId?: string | null;
  responsibleUserId?: string | null;
  title: string;
  startsAt: string;
  endsAt?: string | null;
  status: MeetingStatus;
  location?: string | null;
  meetingUrl?: string | null;
  notes?: string | null;
  recipientOverrides: WorkspaceNotificationRecipient[];
  googleCalendarEventId?: string | null;
  reminderSentAt?: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface BrandingSettingsProfile {
  brandName: string;
  shortName: string;
  tagline: string;
  legalName: string;
  domain: string;
  defaultLocale: string;
  logoUrl?: string | null;
  logoDarkUrl?: string | null;
  iconUrl?: string | null;
  faviconUrl?: string | null;
  appleTouchIconUrl?: string | null;
  letterheadUrl?: string | null;
  signatureUrl?: string | null;
  primaryColor: string;
  accentColor: string;
  foregroundColor: string;
  backgroundColor: string;
  sidebarColor: string;
  successColor: string;
  warningColor: string;
  dangerColor: string;
  sansFamily: string;
  legalFamily: string;
  defaultTitle: string;
  titleTemplate: string;
  description: string;
  applicationName: string;
  author: string;
  themeColor: string;
  email: string;
  supportEmail: string;
  phone?: string | null;
  websiteUrl: string;
  registeredName: string;
  registrationNumber?: string | null;
  barRegistration?: string | null;
  address?: string | null;
  socialProof: Array<{
    id: string;
    kind: 'testimonial' | 'metric' | 'credential' | 'case_result';
    label: string;
    value: string;
    approvedForExternalUse: boolean;
  }>;
  layoutDensity: 'compact' | 'comfortable' | 'spacious';
}

export interface SecurityAuditSettingsProfile {
  sessionTimeoutMinutes: number;
  requireHumanReviewForHighRisk: boolean;
  requireStrongerReviewForNewPrompts: boolean;
  auditRetentionDays: number;
  openApiEnabled: boolean;
}

export interface AdminControlPlane {
  practiceAreas: PracticeAreaTemplate[];
  aiGovernance: AiGovernanceSettings;
  notifications: NotificationSettingsProfile;
  branding: BrandingSettingsProfile;
  securityAudit: SecurityAuditSettingsProfile;
}

export interface WorkspaceAreaActivation {
  key: string;
  label: string;
  description: string;
  enabled: boolean;
  selectedFlowTemplateId?: string | null;
  followUpProfile: FollowUpProfile;
  humanInterventionMode: AutomationStageMode;
  checklist: string[];
  flowOptions: FlowTemplateReference[];
}

export interface WorkspaceOperationsSettings {
  areaActivations: WorkspaceAreaActivation[];
}

export interface PlanLimitSet {
  maxTokensPerMonth?: number | null;
  maxAiExecutionsPerMonth?: number | null;
  maxUsers?: number | null;
  maxWorkspaces?: number | null;
  maxConnectedWhatsapps?: number | null;
  maxAiAgents?: number | null;
  maxAiConversationsPerMonth?: number | null;
  maxAudioMinutesPerMonth?: number | null;
  maxStorageBytes?: number | null;
  maxFiles?: number | null;
  maxFilesPerConversation?: number | null;
  automationLimit?: number | null;
  maxOperationalRecipients?: number | null;
  maxCampaignRecipientsPerMonth?: number | null;
}

export interface PlanFeatureSet {
  digitalSignature: boolean;
  calendar: boolean;
  email: boolean;
  webhooks: boolean;
  advancedAudit: boolean;
  successManager: boolean;
  supportTier: string;
  integrations: string[];
  highlights: string[];
}

export interface WorkspacePlanCatalogItem {
  key: string;
  name: string;
  description: string;
  recommended: boolean;
  limits: PlanLimitSet;
  features: PlanFeatureSet;
  isActive: boolean;
}

export interface PlatformWorkspaceOwner {
  name?: string | null;
  email?: string | null;
}

export interface PlatformWorkspacePlanSummary {
  tier?: string | null;
  name?: string | null;
  subscriptionStatus?: string | null;
}

export interface PlatformWorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  legalName?: string | null;
  description?: string | null;
  status: string;
  setupMode: string;
  isActive: boolean;
  onboardingCompleted: boolean;
  onboardingProgress: number;
  nextOnboardingStep?: string | null;
  owner: PlatformWorkspaceOwner;
  planSummary?: PlatformWorkspacePlanSummary | null;
  memberCount: number;
  matterCount30d: number;
  aiRuns30d: number;
  healthScore: number;
  healthStatus: string;
  launchNotes?: string | null;
}

// ── Conta 360 (Blackbox account drawer) ──

export interface WorkspaceOverviewIdentification {
  id: string;
  name: string;
  slug: string;
  status: string;
  created_at?: string | null;
}

export interface WorkspaceOverviewSubscriptionPlan {
  id: string;
  name: string;
  tier: string;
}

export interface WorkspaceOverviewSubscription {
  status: string;
  interval: string;
  amount_cents: number;
  currency: string;
  current_period_end?: string | null;
  trial_end?: string | null;
  cancel_at_period_end: boolean;
  plan?: WorkspaceOverviewSubscriptionPlan | null;
}

export interface WorkspaceOverviewEntitlementUsage {
  consumed_quantity: number;
  reserved_quantity: number;
  remaining_quantity: number | null;
}

export interface WorkspaceOverviewEntitlement {
  entitlement: string;
  unit: string;
  quantitative: boolean;
  metered: boolean;
  enabled?: boolean;
  limit_quantity?: number | null;
  hard_limit?: boolean | null;
  origin: 'plan' | 'override' | 'not_configured';
  source?: string;
  error?: string;
  usage_this_month?: WorkspaceOverviewEntitlementUsage;
}

export interface WorkspaceOverviewEntitlementOverride {
  entitlement: string;
  mode: 'grant' | 'revoke' | 'limit';
  quantity: number | null;
  reason: string;
  created_by_user_id?: string | null;
  updated_at?: string | null;
}

export interface WorkspaceOverviewIntegration {
  provider: string;
  label?: string | null;
  status: string;
  last_sync_at?: string | null;
}

export interface WorkspaceOverviewWhatsAppNumber {
  display_phone_number?: string | null;
  display_name?: string | null;
  status: string;
  is_default: boolean;
}

export interface WorkspaceOverviewAutonomyByPlaybook {
  name: string;
  autonomy_mode: string;
}

export interface WorkspaceOverviewAgents {
  active_ai_agents: number;
  published_playbooks: number;
  autonomy_by_playbook: WorkspaceOverviewAutonomyByPlaybook[];
}

export interface WorkspaceOverviewFinanceKpis {
  period_start: string;
  period_end: string;
  recebido: string;
  a_receber: string;
  despesas: string;
  resultado: string;
  mrr: string;
  inadimplencia_pct: string;
}

export interface WorkspaceOverviewAuditEvent {
  event_type: string;
  entity_type: string;
  actor_label?: string | null;
  summary?: string | null;
  occurred_at?: string | null;
}

export interface WorkspaceOverviewTaskSummary {
  total: number;
  open: number;
  overdue: number;
  blocked: number;
  pending_review: number;
  completed: number;
  cancelled: number;
  last_updated_at: string | null;
  /** Sempre false: a Conta 360 não transporta conteúdo privado de tarefas. */
  content_included: false;
}

export interface WorkspaceOverviewResponse {
  workspace: WorkspaceOverviewIdentification;
  subscription: WorkspaceOverviewSubscription | null;
  entitlements: WorkspaceOverviewEntitlement[];
  entitlement_overrides: WorkspaceOverviewEntitlementOverride[];
  integrations: WorkspaceOverviewIntegration[];
  whatsapp_numbers: WorkspaceOverviewWhatsAppNumber[];
  agents: WorkspaceOverviewAgents;
  members: Array<{
    user_id: string;
    name: string;
    email: string | null;
    role: string;
    status: string;
    access_package: string | null;
    active_session_count: number;
  }>;
  support: { open_tickets: number };
  tasks: WorkspaceOverviewTaskSummary;
  finance_kpis: WorkspaceOverviewFinanceKpis | null;
  recent_audit_events: WorkspaceOverviewAuditEvent[];
  private_data_included: boolean;
}

/** Bônus de quota de harmonização financeira (F14) concedido pelo root — Conta 360. */
export interface PlatformFinanceHarmonizationQuota {
  competence: string;
  used: number;
  limit: number;
  remaining: number;
  renewsAt: string;
}

export interface EntitlementOverrideWriteItem {
  entitlementKey: string;
  action: 'upsert' | 'delete';
  mode?: 'grant' | 'revoke' | 'limit';
  quantity?: number | null;
  reason: string;
}

export interface EntitlementOverrideWriteResult {
  entitlement: string;
  mode: string;
  quantity: number | null;
  reason: string;
  createdByUserId?: string | null;
  updatedAt?: string | null;
}

export interface SeoMetadata {
  title: string;
  description: string;
  canonicalUrl?: string | null;
  ogImageUrl?: string | null;
}

export interface PublicSectionMedia {
  posterUrl?: string | null;
  mp4Url?: string | null;
  webmUrl?: string | null;
  gifUrl?: string | null;
  alt?: string | null;
  caption?: string | null;
  layoutVariant: string;
}

export interface PublicSectionCta {
  primaryLabel?: string | null;
  secondaryLabel?: string | null;
  secondaryTarget?: string | null;
}

export interface PublicHomeSectionItem {
  title: string;
  description?: string | null;
  badge?: string | null;
}

export interface PublicHomeSection {
  key: string;
  navLabel: string;
  title: string;
  subtitle?: string | null;
  body?: string | null;
  bullets: string[];
  items: PublicHomeSectionItem[];
  cta?: PublicSectionCta | null;
  isVisible: boolean;
  order: number;
  media?: PublicSectionMedia | null;
}

export interface PublicFaqItem {
  question: string;
  answer: string;
}

export interface PublicSiteSettings {
  brandName: string;
  tagline: string;
  supportEmail: string;
  contactEmail: string;
  whatsappUrl: string;
  whatsappMessage: string;
  primaryCtaLabel: string;
  secondaryCtaLabel: string;
  secondaryCtaSectionId: string;
  floatingWhatsappLabel: string;
  seo: SeoMetadata;
}

export interface PublicHomeDocument {
  sections: PublicHomeSection[];
  seo: SeoMetadata;
}

export interface PublicFaqDocument {
  items: PublicFaqItem[];
}

export interface PublicLegalSection {
  heading: string;
  body: string;
}

export interface PublicLegalDocument {
  title: string;
  updatedLabel: string;
  seo: SeoMetadata;
  sections: PublicLegalSection[];
}

export interface PublicMediaAsset {
  key: string;
  label: string;
  posterUrl?: string | null;
  mp4Url?: string | null;
  webmUrl?: string | null;
  gifUrl?: string | null;
  alt?: string | null;
  caption?: string | null;
  layoutVariant: string;
}

export interface PublicMediaLibrary {
  items: PublicMediaAsset[];
}

export interface PublicHomePayload {
  settings: PublicSiteSettings;
  home: PublicHomeDocument;
  faq: PublicFaqDocument;
}

export interface PlatformDocumentEnvelope<T = Record<string, unknown>> {
  key: string;
  version: number;
  updatedAt: string;
  publishedAt?: string | null;
  draft: T;
  published: T;
}

export type PlatformSiteDocumentEnvelope<T = Record<string, unknown>> = PlatformDocumentEnvelope<T>;

export type BlogPostStatus = 'draft' | 'scheduled' | 'published' | 'archived';
export type SiteMediaAssetStatus = 'draft' | 'ready' | 'processing' | 'failed';
export type GoogleBusinessConnectionStatus = 'disconnected' | 'pending' | 'connected' | 'error';
export type GoogleBusinessPublishStatus = 'draft' | 'queued' | 'scheduled' | 'published' | 'failed' | 'requires_review';
export type PlatformOfficialWhatsAppRole = 'notifications' | 'outbound' | 'support';
export type PlatformOfficialWhatsAppStatus = 'disconnected' | 'pending' | 'connected' | 'error';
export type PlatformOfficialWhatsAppConnectionType = 'OFFICIAL_MANUAL' | 'TECH_PROVIDER';

export interface BlogBlock {
  type: string;
  content?: string | null;
  level?: number | null;
  items: string[];
  url?: string | null;
  title?: string | null;
  metadata: Record<string, unknown>;
}

export interface BlogMediaRef {
  assetId?: string | null;
  url?: string | null;
  alt?: string | null;
  caption?: string | null;
  mimeType?: string | null;
  layoutVariant?: string | null;
}

export interface BlogSeo {
  title: string;
  description: string;
  canonicalUrl?: string | null;
  ogImageUrl?: string | null;
  keywords: string[];
  socialCopy?: string | null;
}

export interface IndexingPolicy {
  indexable: boolean;
  includeInSitemap: boolean;
  includeInRss: boolean;
  includeInLlms: boolean;
}

export interface SeoAudit {
  score: number;
  readingTimeMinutes: number;
  suggestions: string[];
  detectedKeywords: string[];
}

export interface BlogPostSummary {
  id: string;
  slug: string;
  title: string;
  subtitle?: string | null;
  excerpt?: string | null;
  authorName: string;
  authorRole?: string | null;
  status: BlogPostStatus;
  featured: boolean;
  categories: string[];
  tags: string[];
  coverMedia: BlogMediaRef;
  seo: BlogSeo;
  indexing: IndexingPolicy;
  seoAudit: SeoAudit;
  readingTimeMinutes: number;
  revision: number;
  publishedAt?: string | null;
  scheduledFor?: string | null;
  updatedAt: string;
}

export interface BlogPost extends BlogPostSummary {
  contentBlocks: BlogBlock[];
  ogMedia: BlogMediaRef;
  aiConfig: Record<string, unknown>;
}

export interface BlogIndexPayload {
  pageTitle: string;
  intro: string;
  seo: BlogSeo;
  featuredPost?: BlogPostSummary | null;
  posts: BlogPostSummary[];
  categories: string[];
}

export interface BlogCategoryPayload {
  category: string;
  posts: BlogPostSummary[];
  seo: BlogSeo;
}

export interface BlogTaxonomy {
  categories: string[];
  tags: string[];
}

export interface BlogSettings {
  pageTitle: string;
  intro: string;
  ctaLabel: string;
  ctaUrl: string;
  seo: BlogSeo;
  aiProviderText: string;
  aiProviderImage: string;
}

export interface BlogIndexingSettings {
  siteUrl: string;
  sitemapEnabled: boolean;
  rssEnabled: boolean;
  llmsEnabled: boolean;
  robotsPolicy: string;
  llmsSummary: string;
}

export interface SiteMediaAsset {
  id: string;
  key: string;
  label: string;
  mimeType: string;
  fileKind: string;
  extension?: string | null;
  status: SiteMediaAssetStatus;
  source: string;
  publicUrl?: string | null;
  altText?: string | null;
  caption?: string | null;
  width?: number | null;
  height?: number | null;
  sizeBytes?: number | null;
  usageMetadata: Record<string, unknown>;
  generationPrompt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface SiteMediaPresign {
  asset: SiteMediaAsset;
  uploadUrl: string;
}

export interface SiteMediaConfirm {
  asset: SiteMediaAsset;
}

export interface EditorialAssistRequestPayload {
  action: string;
  title?: string | null;
  category?: string | null;
  selection?: string | null;
  prompt?: string | null;
}

export interface EditorialAssistResponse {
  title: string;
  contentBlocks: BlogBlock[];
  excerpt?: string | null;
  suggestedKeywords: string[];
  suggestedFaq: BlogBlock[];
}

export interface SeoAutofillResponse {
  seo: BlogSeo;
  excerpt: string;
  slug: string;
  seoAudit: SeoAudit;
}

export interface GeneratedImageResponse {
  asset: SiteMediaAsset;
  providerUsed: string;
}

export interface PlatformOfficialWhatsAppConnection {
  id?: string | null;
  role: PlatformOfficialWhatsAppRole;
  connectionType?: PlatformOfficialWhatsAppConnectionType | null;
  isActive: boolean;
  status: PlatformOfficialWhatsAppStatus;
  phoneNumberId?: string | null;
  phoneNumber?: string | null;
  displayPhoneNumber?: string | null;
  displayName?: string | null;
  businessAccountId?: string | null;
  wabaId?: string | null;
  webhookRegistered: boolean;
  connectedAt?: string | null;
  disconnectedAt?: string | null;
  updatedAt?: string | null;
  lastError?: string | null;
  transportMode?: 'official_api' | 'coexistence';
  coexistenceSyncStatus?: 'not_applicable' | 'requested' | 'contacts_received' | 'completed' | 'partial' | 'failed';
  coexistenceContactsSyncRequestId?: string | null;
  coexistenceHistorySyncRequestId?: string | null;
  coexistenceSyncRequestedAt?: string | null;
  tokenExpiresAt?: string | null;
  tokenLifecycleState?: MetaTokenLifecycleState;
  tokenExpiresInDays?: number | null;
}

export interface PlatformOfficialWhatsAppConnectManual {
  accessToken: string;
  phoneNumberId: string;
  businessAccountId: string;
  registrationPin?: string | null;
  displayName?: string | null;
  isActive: boolean;
}

export interface PlatformOfficialWhatsAppEmbeddedCallback {
  code: string;
  wabaId?: string | null;
  phoneNumberId?: string | null;
  registrationPin?: string | null;
  isActive: boolean;
  transportMode?: 'official_api' | 'coexistence';
}

export interface PlatformOfficialWhatsAppActiveUpdate {
  isActive: boolean;
}

export interface PlatformOfficialWhatsAppTestSend {
  to: string;
  body: string;
}

export interface PlatformOfficialWhatsAppTestResult {
  success: boolean;
  message: string;
}

export interface GoogleBusinessProfileConnection {
  id?: string | null;
  businessName: string;
  accountName?: string | null;
  locationId?: string | null;
  locationName?: string | null;
  status: GoogleBusinessConnectionStatus;
  profileUrl?: string | null;
  settings: Record<string, unknown>;
  lastSyncAt?: string | null;
}

export interface GoogleBusinessConnectionUpdate {
  businessName: string;
  accountName?: string | null;
  locationId?: string | null;
  locationName?: string | null;
  profileUrl?: string | null;
  clientId?: string | null;
  clientSecret?: string | null;
  refreshToken?: string | null;
  accessToken?: string | null;
  settings: Record<string, unknown>;
}

export interface GoogleBusinessPublishJob {
  id: string;
  connectionId: string;
  targetType: string;
  targetId?: string | null;
  title: string;
  status: GoogleBusinessPublishStatus;
  scheduledFor?: string | null;
  publishedAt?: string | null;
  payload: Record<string, unknown>;
  providerResponse: Record<string, unknown>;
  errorMessage?: string | null;
  createdAt: string;
}

export interface GoogleBusinessPublishRequest {
  targetType: string;
  targetId?: string | null;
  title: string;
  payload: Record<string, unknown>;
  scheduledFor?: string | null;
}

export interface PlatformConfigurationDocument {
  id: string;
  namespace: string;
  schemaVersion: string;
  currentVersion: number;
  etag: string;
  payload: Record<string, unknown>;
  updatedByUserId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformConfigurationVersion {
  id: string;
  version: number;
  schemaVersion: string;
  payload: Record<string, unknown>;
  payloadHash: string;
  reason?: string | null;
  createdByUserId?: string | null;
  createdAt: string;
}

export interface PlatformConfigurationVersionPage {
  items: PlatformConfigurationVersion[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

// ── App State ──

export interface AppState {
  cards: MatterCard[];
  columns: KanbanColumn[];
  filters: Filters;
  selectedCardId: string | null;
  drawerOpen: boolean;
  approvalItems: ApprovalItem[];
  metrics: ReportMetric[];
  notifications: Notification[];
  loading: boolean;
  practiceArea: PracticeArea;
  activeModule: ModuleId;
  isAuthenticated: boolean;
  currentUser: AuthUser | null;
  session: SessionContext | null;
  activeWorkspace: WorkspaceSummary | null;
  conversations: WhatsAppConversation[];
}

export type * from './frontend-contracts';
