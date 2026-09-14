export { bootstrapLocalSession as bootstrapKlmLocalSession } from '@/services/auth';
import { apiBlob, apiDownload, apiFetch, apiUpload, API_BASE } from '@/services/http';
import { addMatterNote } from '@/services/matters';
import { withMeetingCreateIdempotency } from '@/services/meeting-idempotency';
import {
  type KlmNativeDomain,
} from '@/klm/native-domains';

export type KlmRuntimeStatus = {
  mode: string;
  localOnly: boolean;
  localInfrastructure: boolean;
  externalActionsEnabled: boolean;
  awsEnabled: boolean;
  modelApisEnabled: boolean;
  onlineApproved: boolean;
  localBootstrapEnabled: boolean;
  providers: Record<string, string>;
};

export type ExecutiveKey = 'ceo' | 'coo' | 'cfo' | 'cro' | 'cpo' | 'cto' | 'cio' | 'chro';

export type ExecutiveProfile = {
  key: ExecutiveKey;
  displayName: string;
  mention: string;
  role: string;
  description: string;
  domainManifest: { domains: string[]; [key: string]: unknown };
  promptVersion: string;
  status: 'active' | 'disabled';
};

export type ExecutiveThread = {
  id: string;
  workspaceId: string;
  createdByUserId: string;
  title: string | null;
  visibility: 'private' | 'shared';
  status: 'active' | 'archived' | 'deleted';
  mode: 'direct' | 'roundtable';
  selectedExecutives: ExecutiveKey[];
  ceoAlwaysPresent: boolean;
  lastMessageAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ExecutiveSource = {
  sourceType: string;
  sourceRef: string;
  title: string;
  occurredAt: string | null;
  indexedAt: string | null;
  score?: number | null;
  excerpt?: string | null;
  stale: boolean;
};

export type ExecutiveSourceReceipt = {
  id: string;
  workspaceId: string;
  threadId: string | null;
  roundId: string | null;
  executiveKey: ExecutiveKey | null;
  pipelineVersion: string;
  queryHash: string;
  period: Record<string, unknown>;
  structuredQueries: Array<Record<string, unknown>>;
  selectedSources: ExecutiveSource[];
  rejectedSources: Array<Record<string, unknown>>;
  insufficiencyReasons: string[];
  generatedAt: string;
};

export type ExecutiveThreadShare = {
  threadId: string;
  userId: string;
  grantedBy: string;
  grantedAt: string;
  revokedAt: string | null;
  permission: 'view_and_reply';
};

export type ExecutiveMessage = {
  id: string;
  threadId: string;
  roundId: string | null;
  authorType: 'human' | 'executive' | 'system';
  authorUserId: string | null;
  executiveKey: ExecutiveKey | null;
  body: string;
  messageKind: 'question' | 'response' | 'synthesis' | 'notice' | 'error';
  sequence: number;
  status: string;
  createdAt: string;
};

export type ExecutiveRound = {
  id: string;
  threadId: string;
  status: 'received' | 'context_planning' | 'retrieving_context' | 'executive_selection' | 'executives_responding' | 'ceo_synthesizing' | 'completed' | 'partial_failure' | 'failed' | 'cancelled';
  requestedExecutives: ExecutiveKey[];
  selectedByCeo: ExecutiveKey[];
  selectionReasons: Record<string, string>;
  messages: ExecutiveMessage[];
  startedAt: string;
  completedAt: string | null;
};

export type ExecutiveReport = {
  id: string;
  workspaceId: string;
  periodStart: string;
  periodEnd: string;
  status: 'scheduled' | 'collecting_data' | 'generating' | 'published' | 'published_with_gaps' | 'partial_failure' | 'failed' | 'superseded';
  title: string;
  body: string;
  executiveParticipants: ExecutiveKey[];
  publishedAt: string | null;
  createdAt: string;
};

export type KlmLocalDiagnostics = {
  runId: string;
  status: 'healthy' | 'degraded';
  checkedAt: string;
  summary: {
    status: 'healthy' | 'degraded';
    total: number;
    healthy: number;
    degraded: number;
    unavailable: number;
  };
  checks: Array<{
    key: string;
    label: string;
    status: 'healthy' | 'degraded';
    state: string;
    detail: string;
    checkedAt: string;
    evidence: Record<string, unknown>;
  }>;
};

export type KlmNativeCapabilityDisposition =
  | 'discovered'
  | 'approved_native'
  | 'approved_embedded'
  | 'approved_full_app'
  | 'implemented'
  | 'not_applicable'
  | 'blocked_license'
  | 'blocked_dependency'
  | 'blocked_external_gate';

export type KlmNativeCapability = {
  id: string;
  domain: KlmNativeDomain;
  key: string;
  label: string;
  category: string;
  description: string;
  disposition: KlmNativeCapabilityDisposition;
  exposure: 'none' | 'bridge' | 'native' | 'microfrontend' | 'full_app';
  mode: 'local_canonical' | 'local_equivalent' | 'local_reconciled' | 'local_command' | 'controlled_configuration' | 'responsive_substitution';
  surface: string;
  relatedRoutes: string[];
  nativeRoute: string;
  verified: boolean;
  externalEffectVerified: boolean;
  reason: string;
};

export type KlmNativeCapabilityRegistry = {
  schemaVersion: string;
  scope: string;
  capabilities: KlmNativeCapability[];
  summary: {
    total: number;
    available: number;
    verified: number;
    externalEffectVerified: number;
    byDomain: Record<KlmNativeDomain, number>;
    byDisposition: Partial<Record<KlmNativeCapabilityDisposition, number>>;
  };
  verification: {
    state: 'verified' | 'partial' | 'unverified';
    reason: string;
    checks: number;
  };
};

export type KlmFunctionUnitState =
  | 'discovered'
  | 'mapped'
  | 'native_shadow'
  | 'native_primary'
  | 'native_verified'
  | 'blocked_proprietary'
  | 'blocked_license';

export type KlmFunctionUnit = {
  id: string;
  domain: KlmNativeDomain;
  name: string;
  category: string;
  capability: string;
  maturity: KlmFunctionUnitState;
  disposition: KlmFunctionUnitState;
  summary: string;
};

export type KlmFunctionUnitList = {
  schemaVersion: '1.0';
  scope: 'klm-capability-functions';
  summary: {
    total: number;
    available: number;
    blocked: number;
    verified: number;
    remaining: number;
    byDomain: Record<KlmNativeDomain, number>;
    byCategory: Record<string, number>;
    byDisposition: Partial<Record<KlmFunctionUnitState, number>>;
  };
  filters: {
    domains: KlmNativeDomain[];
    categories: string[];
    dispositions: KlmFunctionUnitState[];
    capabilities: string[];
  };
  page: number;
  pageSize: number;
  total: number;
  pages: number;
  items: KlmFunctionUnit[];
};

export type KlmFunctionUnitQuery = {
  page?: number;
  pageSize?: number;
  search?: string;
  domain?: KlmNativeDomain;
  category?: string;
  disposition?: KlmFunctionUnitState;
  capability?: string;
};

export type KlmFunctionUnitRuntime = {
  function: KlmFunctionUnit;
  state: {
    maturity: KlmFunctionUnitState;
    availableModes: Array<'dry-run' | 'execute' | 'configure'>;
    auditEventCount: number;
    lastActivityAt: string | null;
    externalEffectsEnabled: false;
  };
};

export type KlmFunctionUnitActionResult = {
  status: 'succeeded' | 'configured' | 'simulated' | 'blocked_external';
  replayed: boolean;
  externalEffectExecuted: false;
  summary: string;
};

export type KlmNativeInboxRoutingContext = {
  linked: boolean;
  threadId: string;
  conversationId: string | null;
  conversation: {
    id: string;
    status: string;
    priority: string | null;
    labels: string[];
    createdAt: string | null;
    updatedAt: string | null;
    assignee: { id: number; name: string } | null;
    team: { id: number; name: string } | null;
    inbox: { id: number; name: string } | null;
    externalNetworkExecuted: false;
  } | null;
  inboxes: Array<{ id: number; name: string }>;
  teams: Array<{ id: number; name: string }>;
  labels: Array<{ id: number; title: string }>;
  agents: Array<{ id: number; name: string; role: string; availability: string }>;
  cannedResponses: Array<{ id: number; shortCode: string; content: string }>;
  projection: KlmNativeInboxProjectionStats;
};

export type KlmNativeInboxProjectionStats = {
  totalMessages: number;
  projectedMessages: number;
  receivedMessages: number;
  pendingMessages: number;
  failedMessages: number;
  lastSyncedAt: string | null;
  externalChannelDeliveryEnabled: false;
};

export type KlmNativeInboxThreadSync = KlmNativeInboxProjectionStats & {
  status: 'synced';
  threadId: string;
  conversationId: string;
  projectedNow: number;
  contactCreated: boolean;
  rateLimited: boolean;
};

export type KlmNativeInboxRoutingUpdate = {
  status?: 'open' | 'resolved' | 'pending' | 'snoozed';
  priority?: 'low' | 'medium' | 'high' | 'urgent' | null;
  assigneeId?: number | null;
  teamId?: number | null;
  labels?: string[];
};

export type KlmNativeCollaborationThreadContext = {
  linked: boolean;
  threadId: string;
  roomId: string | null;
  roomType: string | null;
  room: { id: string; name: string; displayName: string } | null;
  members: Array<{ id: string; name: string; username: string; status: string; role: string }>;
  messages: Array<{
    id: string;
    body: string;
    author: { id: string; name: string; username: string };
    occurredAt: string | null;
    attachments: Array<{ name: string; mediaType: string }>;
    reactions: Array<{ emoji: string; count: number }>;
    replyCount: number;
  }>;
  discussions: Array<{
    id: string;
    name: string;
    messageCount: number;
    createdAt: string | null;
    updatedAt: string | null;
  }>;
  sensitiveDataHidden: boolean;
};

export type KlmNativeCollaborationThreadSync = {
  status: 'synced';
  threadId: string;
  roomId: string;
  messagesSeen: number;
  messagesProjected: number;
  externalNetworkExecuted: false;
};

export type KlmNativeAgentContext = {
  linked: boolean;
  agents: Array<{
    id: string;
    name: string;
    displayName: string;
    description: string;
    isActive: boolean;
    state: string;
    qualityScore: number;
    queueSize: number;
    transferToHuman: boolean;
  }>;
  workflows: Array<{
    id: string;
    name: string;
    description: string | null;
    status: string;
    revision: number;
    nodeCount: number;
    connectionCount: number;
    runCount: number;
  }>;
  analytics: { runs: number; byStatus: Record<string, number>; externalActionsExecuted: 0 };
  performance: Array<{
    id: string;
    name: string;
    qualityScore: number;
    queueSize: number;
    totalChats: number;
    completedRuns: number;
    failedRuns: number;
    waitingHumanRuns: number;
  }>;
  people: {
    items: Array<{ id: string; name: string; stage: string; activeMatters: number }>;
    total: number;
  };
  peopleStats: { totalPeople: number; activeMatters: number; newLeads7D: number };
  tools: Array<{ id: number; name: string; status: string; external: false }>;
  groups: Array<{ id: string; name: string; memberCount: number }>;
  notifications: Array<{ id: string; title: string; message: string; createdAt: string | null }>;
  knowledge: {
    items: Array<{
      id: string;
      name: string;
      documentType: string;
      title: string;
      summary: string;
      matterId: string | null;
      tags: string[];
    }>;
    total: number;
    queueSize: number;
  };
  timeRange: '24h' | '7d' | '30d' | '90d';
  sensitiveDataHidden: boolean;
  externalExecutionEnabled: false;
};

export type KlmNativeAgentAgentSync = {
  status: 'synced';
  created: number;
  updated: number;
  adopted: number;
  conflicts: number;
  seen: number;
};

export type KlmNativeAgentFlowContext = {
  linked: boolean;
  flowId: string;
  revision: number;
  workflowStatus: string | null;
  nodeCount: number;
  connectionCount: number;
  digestMatches: boolean;
  graphMatches: boolean;
  staleLink: boolean;
  externalExecutionEnabled: false;
};

export type KlmNativeAgentFlowSync = {
  status: 'synced';
  flowId: string;
  graphAction: 'replaced' | 'unchanged';
  nodeCount: number;
  connectionCount: number;
  revision: number;
  externalExecutionEnabled: false;
};

export type KlmNativeCRMProjection = {
  linked: boolean;
  totalRecords: number;
  availableRecords: number;
  failedRecords: number;
  staleRecords: number;
  byEntityType: {
    contacts: number;
    leads: number;
    opportunities: number;
    cases: number;
    tasks: number;
    financeEntries: number;
  };
  updatedAt: string | null;
  externalExecutionEnabled: false;
};

export type KlmNativeCRMItem = {
  id: string;
  entityType: string;
  name: string;
  stage: string | null;
  status: string | null;
  priority: string | null;
  amount: number | null;
  currency: string;
  probability: number | null;
  daysInStage: number | null;
  agingStatus: string | null;
  dueDate: string | null;
  nextFollowUp: string | null;
  overdue: boolean;
  slaBreached: boolean;
  escalationCount: number;
};

export type KlmNativeCRMColumn = {
  id: string;
  name: string;
  order: number;
  color: string | null;
  stageType: string | null;
  wipLimit: number | null;
  itemCount: number;
  items: KlmNativeCRMItem[];
};

export type KlmNativeCRMFunnelContext = {
  projection: KlmNativeCRMProjection;
  counts: { accounts: number; contacts: number; leads: number; opportunities: number };
  urgentCounts: { overdueTasks: number; followupsToday: number; hotLeads: number };
  pipeline: Array<{ id: string; label: string; count: number; value: number }>;
  revenue: { totalPipeline: number; weightedPipeline: number; conversionRate: number; currency: string };
  opportunityColumns: KlmNativeCRMColumn[];
  leadColumns: KlmNativeCRMColumn[];
  caseColumns: KlmNativeCRMColumn[];
  goals: Array<{ id: string; name: string; target: number; achieved: number; percent: number; active: boolean }>;
  agingConfig: Array<{ stage: string; expectedDays: number; warningDays: number | null }>;
  notificationsUnread: number;
  sensitiveDataHidden: boolean;
};

export type KlmNativeCRMRecord = KlmNativeCRMItem & {
  syncState: string;
  lastSyncedAt: string | null;
};

export type KlmNativeCRMMatterContext = {
  projection: KlmNativeCRMProjection;
  matterId: string;
  records: KlmNativeCRMRecord[];
  sensitiveDataHidden: boolean;
};

export type KlmNativeCRMWorkContext = {
  projection: KlmNativeCRMProjection;
  taskColumns: KlmNativeCRMColumn[];
  tasks: KlmNativeCRMItem[];
  boards: Array<{ id: string; name: string; description: string | null; columnCount: number }>;
  notificationsUnread: number;
  sensitiveDataHidden: boolean;
};

export type KlmNativeCRMFinanceItem = {
  id: string;
  entityType: string;
  name: string;
  status: string | null;
  amount: number | null;
  amountDue: number | null;
  currency: string;
  issueDate: string | null;
  dueDate: string | null;
  validUntil: string | null;
  category: string | null;
  active: boolean;
};

export type KlmNativeCRMFinanceContext = {
  projection: KlmNativeCRMProjection;
  summary: { totalInvoiced: number; totalPaid: number; totalDue: number; currency: string };
  statusCounts: Record<string, number>;
  overdue: { count: number; amount: number };
  recentActivity: { entries: number; paid: number; pending: number };
  estimateSummary: { count: number; active: number };
  invoices: KlmNativeCRMFinanceItem[];
  estimates: KlmNativeCRMFinanceItem[];
  products: KlmNativeCRMFinanceItem[];
  aging: Array<{ bucket: string; count: number; amount: number }>;
  revenueSeries: Array<{ period: string | null; revenue: number; count: number }>;
  notificationsUnread: number;
  sensitiveDataHidden: boolean;
};

export type KlmInternalMessageFeedEntry = {
  message: KlmMessage;
  thread: KlmThread;
};

export type KlmDashboard = {
  metrics: Record<string, number>;
  funnel: Array<{ stage: string; count: number }>;
  decisions: Array<{ type: string; count: number; priority: string }>;
  runtime: KlmRuntimeStatus;
};

export type KlmFunnelCard = {
  id: string;
  clientId: string;
  name: string;
  practiceArea?: string;
  subject: string;
  object: string;
  source: string;
  owner: string;
  probability: number;
  initialFee: number;
  projectedRevenue: number;
  slaDueAt: string | null;
  documents: number;
  documentScore: number;
  productionScore?: number;
  stage: string;
  microStatus: string;
  cardOrder?: number;
  threadId?: string | null;
  threadStatus?: string | null;
  channel?: KlmOmnichannelChannel | null;
  unreadMessages?: number;
  lastMessagePreview?: string | null;
  lastMessageAt?: string | null;
  assignedHumanId?: string | null;
  assignedTeamId?: string | null;
  nextAction?: string | null;
  nextDeadline?: string | null;
  priority?: string;
  urgency?: string;
  classification?: string;
  tags?: string[];
  openTasks?: number;
  isBlocked?: boolean;
  needsApproval?: boolean;
  upcomingMeetingAt?: string | null;
  meetingUrl?: string | null;
  activeFlow?: null | {
    runId: string;
    flowId: string;
    name: string;
    status: string;
    progress: number;
    completedSteps: number;
    totalSteps: number;
    currentStep: string | null;
    errorStatus: string | null;
  };
  updatedAt?: string;
  sensitiveDataHidden?: boolean;
};

export type KlmFunnel = {
  boardVersion: number;
  stages: Array<{ id: string; label: string; count: number }>;
  cards: KlmFunnelCard[];
  metrics: Record<string, number>;
};

export type KlmFunnelMoveCommand = {
  expectedBoardVersion: number;
  position?: number;
  sourceColumnId?: string;
  beforeId?: string;
  afterId?: string;
  operationId?: string;
};

export type KlmFunnelMoveResult = {
  matterId: string;
  columnId: string;
  microStatus: string;
  cardOrder: number;
  boardVersion: number;
  operationId: string;
  agentDispatched: boolean;
  deduplicated: boolean;
  automationStatus?: 'not_started' | 'queued' | 'processing' | 'completed' | 'waiting_human' | 'failed';
  agentsTriggered?: string[];
  agentRunIds?: string[];
  approvalId?: string | null;
  automationMessage?: string | null;
};

export type KlmKpis = {
  topMetrics: Array<{ key: string; label: string; value: number | null; unit: string }>;
  families: Array<{ key: string; label: string; coverage: number }>;
  channels: Array<Record<string, number | string>>;
  periodDays: number | null;
  subtheme: string | null;
  legalAreaKey?: string | null;
  legalProductKey?: string | null;
  generatedAt: string;
  source: string;
  trend?: {
    leads?: Array<{ date: string; value: number }>;
    revenue?: Array<{ date: string; value: number }>;
  };
  teamPerformance?: {
    audience: 'workspace_team';
    periodDays: number;
    generatedAt: string;
    scoreFormula: string;
    rows: Array<{
      id: string;
      name: string;
      scopeName: string;
      role?: string | null;
      primaryCount: number;
      completedCount: number;
      pendingCount: number;
      qualityValue?: number | null;
      score: number;
    }>;
  };
};

export type KlmAdvancedKpiMetric = {
  key: string;
  label: string;
  category: string;
  categoryLabel: string;
  value: number | null;
  previousValue: number | null;
  deltaPct: number | null;
  polarity: 'positive' | 'negative' | 'neutral';
  unit: string;
  hasData: boolean;
  description: string;
  source: string;
  dimensions: Record<string, string>;
  note: string | null;
};

export type KlmAdvancedKpiReport = {
  generatedAt: string;
  periodDays: number;
  previousPeriodDays: number;
  catalogTotal: number;
  totalRows: number;
  rowsWithData: number;
  coverageTotal: number;
  coverageWithData: number;
  truncated: boolean;
  appliedFilters: Record<string, unknown>;
  categories: Array<{
    key: string;
    label: string;
    metricCount: number;
    metricsWithData: number;
  }>;
  options: Record<string, Array<{
    value: string;
    label: string;
    parentValue?: string | null;
  }>>;
  rows: KlmAdvancedKpiMetric[];
};

export type KlmAdvancedKpiFilters = {
  periodDays?: number;
  legalAreaKey?: string | null;
  legalProductKey?: string | null;
  channel?: string | null;
  responsibleUserId?: string | null;
  stage?: string | null;
  category?: string | null;
  query?: string | null;
  onlyWithData?: boolean;
  sortBy?: 'label' | 'category' | 'value';
  sortDirection?: 'asc' | 'desc';
  limit?: number;
};

export type KlmQuality = {
  score: number;
  metrics: Record<string, number>;
  dimensions: Array<{ key: string; label: string; score: number }>;
  reviewQueue: Array<{ threadId: string; matterId: string | null; reason: string; score: number }>;
  generatedAt: string;
};

export type KlmOmnichannelAuditFinding = {
  id: string;
  workspaceId: string;
  threadId: string;
  matterId: string | null;
  messageId: string | null;
  findingType: string;
  severity: 'info' | 'warning' | 'critical';
  state: 'open' | 'retry_scheduled' | 'waiting_human' | 'resolved' | 'dismissed';
  title: string;
  summary: string;
  dedupeKey: string;
  evidence: Record<string, unknown>;
  remediation: Record<string, unknown>;
  detectedAt: string;
  lastSeenAt: string;
  resolvedAt: string | null;
  retryCount: number;
  maxRetries: number;
  nextRetryAt: string | null;
  internalNoteMessageId: string | null;
  retryScheduleId: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type KlmOmnichannelAuditDashboard = {
  findings: KlmOmnichannelAuditFinding[];
  total: number;
  openCount: number;
  criticalCount: number;
  retryScheduledCount: number;
  waitingHumanCount: number;
  resolvedCount: number;
  inspectedThreads: number;
  healthyThreads: number;
  coveragePercent: number;
  responseCoveragePercent: number;
  deliveryIntegrityPercent: number;
  generatedAt: string;
};

export type KlmOmnichannelChannel =
  | 'whatsapp'
  | 'email'
  | 'form'
  | 'instagram'
  | 'facebook'
  | 'messenger'
  | 'tiktok'
  | 'webchat'
  | 'audio'
  | 'internal';

export type KlmThread = {
  id: string;
  workspaceId: string;
  matterId: string | null;
  clientId: string | null;
  assignedHumanId: string | null;
  identityId?: string | null;
  assignedTeamId?: string | null;
  primaryChannel: KlmOmnichannelChannel;
  status: string;
  subject: string | null;
  objectName: string | null;
  ownerAgentKey: string | null;
  queue?: string;
  priority?: number;
  isFavorite?: boolean;
  preferredChannel?: string | null;
  version?: number;
  score: number;
  unreadCount: number;
  lastMessagePreview: string | null;
  lastMessageAt: string | null;
  lastInboundAt?: string | null;
  slaDueAt: string | null;
  firstResponseDueAt?: string | null;
  resolutionDueAt?: string | null;
  closedAt?: string | null;
  closedReason?: string | null;
  policyState?: Record<string, unknown>;
  threadMetadata: Record<string, unknown>;
  sensitiveDataHidden?: boolean;
};

export type KlmThreadFilterQuery = Partial<{
  channel: KlmOmnichannelChannel;
  status: string;
  search: string;
  assignedHumanId: string;
  assignedTeamId: string;
  ownerAgentKey: string;
  queue: string;
  scoreMin: number;
  scoreMax: number;
  threadPriorityMin: number;
  threadPriorityMax: number;
  matterPriority: string;
  urgency: string;
  classification: string;
  demandType: string;
  healthSubtheme: string;
  columnId: string;
  leadSource: string;
  tag: string;
  hasUnread: boolean;
  isFavorite: boolean;
  flowStatus: string;
  slaState: 'overdue' | 'on_time' | 'without_sla';
  lastMessageAfter: string;
  lastMessageBefore: string;
  sortBy: 'recent' | 'score' | 'priority' | 'unread' | 'sla';
  sortDirection: 'asc' | 'desc';
  limit: number;
}>;

export type KlmConversationResponseState =
  | 'cliente_aguardando_escritorio'
  | 'escritorio_aguardando_cliente'
  | 'respondido'
  | 'sem_mensagem';

export type KlmConversationResponseSlaStatus =
  | 'within_sla'
  | 'overdue'
  | 'without_sla'
  | 'not_applicable';

export type KlmConversationResponsePeriod =
  | 'all_time'
  | 'last_24_hours'
  | 'last_7_days'
  | 'last_30_days'
  | 'last_90_days'
  | 'custom';

export type KlmConversationResponseFilterPreset =
  | 'all'
  | 'pending'
  | 'overdue'
  | 'waiting_for_client'
  | 'responded'
  | 'without_messages';

export type KlmConversationResponseSort =
  | 'duration'
  | 'started_at'
  | 'last_activity'
  | 'sla_due_at'
  | 'first_response'
  | 'attendant'
  | 'team'
  | 'status';

export type KlmConversationResponseMetric = {
  threadId: string;
  threadStatus: string;
  primaryChannel: string;
  state: KlmConversationResponseState;
  stateStartedAt: string | null;
  durationSeconds: number | null;
  lastClientFacingActivityAt: string | null;
  assignedTeamId: string | null;
  assignedTeamName: string | null;
  assignedAttendantId: string | null;
  assignedAttendantName: string | null;
  slaDueAt: string | null;
  slaStatus: KlmConversationResponseSlaStatus;
  isOverdue: boolean;
  overdueSeconds: number;
  firstClientMessageAt: string | null;
  firstResponseAt: string | null;
  firstResponseSeconds: number | null;
  firstResponseWithinSla: boolean | null;
};

export type KlmConversationResponseKpis = {
  conversations: number;
  pending: number;
  averageWaitSeconds: number | null;
  medianWaitSeconds: number | null;
  p90WaitSeconds: number | null;
  longestWaitSeconds: number | null;
  averageFirstResponseSeconds: number | null;
  withinSlaRate: number | null;
  slaEvaluated: number;
  withinSla: number;
};

export type KlmConversationResponseAggregate = {
  groupId: string | null;
  groupName: string;
  metrics: KlmConversationResponseKpis;
};

export type KlmConversationResponseMetrics = {
  generatedAt: string;
  period: {
    preset: KlmConversationResponsePeriod;
    startedAt: string | null;
    endedAt: string;
    asOf: string;
  };
  filters: {
    preset: KlmConversationResponseFilterPreset;
    attendantIds: string[];
    teamIds: string[];
    statuses: KlmConversationResponseState[];
    slaStatuses: KlmConversationResponseSlaStatus[];
    sortBy: KlmConversationResponseSort;
    sortDirection: 'asc' | 'desc';
  };
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
  items: KlmConversationResponseMetric[];
  account: KlmConversationResponseAggregate;
  byTeam: KlmConversationResponseAggregate[];
  byAttendant: KlmConversationResponseAggregate[];
};

export type KlmConversationResponseMetricsQuery = Partial<{
  period: KlmConversationResponsePeriod;
  dateFrom: string;
  dateTo: string;
  preset: KlmConversationResponseFilterPreset;
  attendantIds: string[];
  teamIds: string[];
  statuses: KlmConversationResponseState[];
  slaStatuses: KlmConversationResponseSlaStatus[];
  sortBy: KlmConversationResponseSort;
  sortDirection: 'asc' | 'desc';
  offset: number;
  limit: number;
}>;

export type KlmMessage = {
  id: string;
  threadId: string;
  matterId: string | null;
  endpointId?: string | null;
  bindingId?: string | null;
  connectionId?: string | null;
  channel: KlmThread['primaryChannel'];
  direction: 'inbound' | 'outbound' | 'internal';
  authorType: 'client' | 'agent' | 'human' | 'system';
  authorLabel: string;
  body: string;
  deliveryStatus: string;
  provider?: string | null;
  transport?: string;
  contentType?: string;
  language?: string | null;
  occurredAt: string;
  messageMetadata: Record<string, unknown>;
  contentLifecycleState?: 'original' | 'edited' | 'deleted' | 'provider_unsupported';
  evidenceEventCount?: number;
  evidenceRevisionCount?: number;
  evidenceChainSha256?: string | null;
  contentEditedAt?: string | null;
  contentDeletedAt?: string | null;
  sensitiveDataHidden?: boolean;
};

export type KlmMessageEvidenceMedia = {
  storageObjectId: string | null;
  objectId: string | null;
  storageObjectVersion: number | null;
  sha256: string | null;
  byteSize: number | null;
  mimeType: string | null;
  filename: string | null;
  providerMediaId: string | null;
};

export type KlmMessageEvidenceEvent = {
  id: string;
  chainId: string;
  revision: number;
  contentRevision: number | null;
  messageId: string | null;
  threadId: string | null;
  eventType: 'captured' | 'edited' | 'deleted' | 'provider_receipt' | 'media_captured' | 'media_unavailable' | 'provider_unsupported';
  observationState: 'observed' | 'provider_signal' | 'inferred' | 'unavailable';
  provider: string;
  connectionKey: string;
  externalMessageId: string;
  contentType: string | null;
  bodySnapshot: string | null;
  structuredContent: Record<string, unknown>;
  eventPayload: Record<string, unknown>;
  eventPayloadSha256: string;
  sourceEnvelopeSha256: string | null;
  signatureSha256: string | null;
  previousEvidenceSha256: string | null;
  evidenceSha256: string;
  media: KlmMessageEvidenceMedia;
  providerOccurredAt: string | null;
  receivedAt: string;
};

export type KlmMessageEvidenceChain = {
  messageId: string;
  threadId: string;
  chainId: string | null;
  contentLifecycleState: 'original' | 'edited' | 'deleted' | 'provider_unsupported';
  eventCount: number;
  contentRevisionCount: number;
  integrityStatus: 'verified' | 'failed' | 'unavailable';
  chainHeadSha256: string | null;
  originalSnapshot: KlmMessageEvidenceEvent | null;
  currentSnapshot: KlmMessageEvidenceEvent | null;
  events: KlmMessageEvidenceEvent[];
  providerNotes: string[];
  sensitiveDataHidden: boolean;
  generatedAt: string;
};

export type KlmMessageEvidenceExport = {
  schemaVersion: string;
  exportedAt: string;
  chain: KlmMessageEvidenceChain;
  rawProviderEnvelopeIncluded: boolean;
  rawProviderEnvelopeNote: string;
  manifestSha256: string;
};

export type KlmChannelConnection = {
  id: string;
  workspaceId: string;
  channel: KlmOmnichannelChannel;
  provider: string;
  connectionKey: string;
  displayName: string;
  externalAccountId: string | null;
  transportMode: string;
  status: string;
  isDefault: boolean;
  capabilities: Record<string, { supported?: boolean; enabled?: boolean; state?: string; note?: string }>;
  limits: Record<string, unknown>;
  routingPolicy: Record<string, unknown>;
  health: Record<string, unknown>;
  configuration: Record<string, unknown>;
  credentialReference: string | null;
  webhookState: string;
  version: number;
  verifiedAt: string | null;
  connectedAt: string | null;
  lastSyncedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmMetaGroupWebhookField =
  | 'group_lifecycle_update'
  | 'group_participants_update'
  | 'group_settings_update'
  | 'group_status_update';

export type KlmMetaGroupProductMode =
  | 'official_api'
  | 'cloud_api'
  | 'business_app'
  | 'coexistence'
  | 'multi_solution'
  | 'unsupported';

export type KlmMetaGroupOfficialLimits = {
  inviteOnly: true;
  maxParticipants: 8;
  maxGroupsPerBusinessNumber: 10000;
};

export type KlmMetaGroupCapabilityGate = {
  workspaceId: string;
  connectionId: string;
  provider: string;
  productMode: KlmMetaGroupProductMode;
  developerAppState: 'not_ready' | 'ready';
  developerAppReady: boolean;
  developerAppMissingRequirements: string[];
  requiredWebhookFields: KlmMetaGroupWebhookField[];
  subscribedWebhookFields: KlmMetaGroupWebhookField[];
  obaEligibilityState: 'unknown' | 'eligible' | 'ineligible';
  obaEvidenceSource: 'unverified' | 'meta_api' | 'meta_business_manager';
  obaGateOpen: boolean;
  productModeSupported: boolean;
  coexistenceDenied: boolean;
  businessAppDenied: boolean;
  multiSolutionDenied: boolean;
  runtimeExternalActionsEnabled: boolean;
  externalCapabilityGateOpen: boolean;
  externalExecutionGateOpen: false;
  externalDispatchImplemented: false;
  localPreparationAvailable: true;
  capabilityDenialReasons: string[];
  executionDenialReasons: string[];
  limits: KlmMetaGroupOfficialLimits;
  observedAt: string;
};

export type KlmMetaGroupCapability = {
  id: string | null;
  workspaceId: string;
  connectionId: string;
  configured: boolean;
  phoneNumberId: string | null;
  wabaId: string | null;
  developerAppConfigured: boolean;
  webhookCallbackVerified: boolean;
  obaEvidenceReference: string | null;
  obaEvidenceRecordedAt: string | null;
  evidenceMetadata: Record<string, unknown>;
  version: number;
  createdAt: string | null;
  updatedAt: string | null;
  gate: KlmMetaGroupCapabilityGate;
  deduplicated: boolean;
};

export type KlmMetaGroupCapabilityConfigure = {
  expectedVersion: number;
  phoneNumberId: string;
  wabaId?: string | null;
  developerAppConfigured: boolean;
  webhookCallbackVerified: boolean;
  subscribedWebhookFields: KlmMetaGroupWebhookField[];
  obaEligibilityState: KlmMetaGroupCapabilityGate['obaEligibilityState'];
  obaEvidenceSource: KlmMetaGroupCapabilityGate['obaEvidenceSource'];
  obaEvidenceReference?: string | null;
  obaEvidenceRecordedAt?: string | null;
  evidenceMetadata?: Record<string, unknown>;
};

export type KlmMetaGroupOperation = {
  id: string;
  workspaceId: string;
  groupId: string | null;
  connectionId: string;
  requestedByUserId: string | null;
  operationType: 'create_group' | 'prepare_official_group_creation' | 'prepare_group_message';
  state: 'local_ready' | 'blocked_by_capability';
  idempotencyKey: string;
  requestPayloadSha256: string;
  requestPayload: Record<string, unknown>;
  capabilitySnapshot: Record<string, unknown>;
  externalDispatchExecuted: false;
  createdAt: string;
  updatedAt: string;
};

export type KlmMetaGroupPinnedMessage = {
  text: string;
  pinnedByUserId: string | null;
  pinnedAt: string;
};

export type KlmMetaGroup = {
  id: string;
  workspaceId: string;
  connectionId: string;
  capabilityId: string | null;
  createdByUserId: string | null;
  groupKind: 'local_internal' | 'meta_cloud_group';
  isLocalInternal: boolean;
  isOfficialMetaGroup: boolean;
  providerGroupId: string | null;
  providerRequestId: string | null;
  subject: string;
  description: string | null;
  joinApprovalMode: 'auto_approve' | 'approval_required';
  state: string;
  inviteLink: string | null;
  inviteLinkSimulated: boolean;
  participantCount: number;
  createIdempotencyKey: string;
  requestPayloadSha256: string;
  lastLifecycleAt: string | null;
  lastMessagePreview: string | null;
  lastMessageAt: string | null;
  pinnedMessage: KlmMetaGroupPinnedMessage | null;
  version: number;
  groupMetadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  gate: KlmMetaGroupCapabilityGate;
};

export type KlmMetaGroupCollection = {
  items: KlmMetaGroup[];
  total: number;
};

export type KlmMetaGroupParticipant = {
  id: string;
  workspaceId: string;
  groupId: string;
  providerUserId: string;
  waId: string | null;
  businessScopedUserId: string | null;
  username: string | null;
  joinRequestId: string | null;
  state: 'invited' | 'join_requested' | 'joined' | 'left' | 'removed' | 'rejected';
  source: 'invite_link' | 'join_request' | 'provider_webhook';
  role: 'member' | 'admin';
  joinedAt: string | null;
  leftAt: string | null;
  participantMetadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

export type KlmMetaGroupParticipantCollection = {
  items: KlmMetaGroupParticipant[];
  total: number;
};

export type KlmMetaGroupParticipantRemoval = {
  participant: KlmMetaGroupParticipant;
  group: KlmMetaGroup;
  executed: boolean;
  prepared: boolean;
  blockedReasons: string[];
  deduplicated: boolean;
};

export type KlmMetaGroupInviteLinkRotation = {
  group: KlmMetaGroup;
  rotated: boolean;
  simulated: boolean;
  blockedReasons: string[];
  deduplicated: boolean;
};

export type KlmMetaGroupMessage = {
  id: string;
  groupId: string;
  requestedByUserId: string | null;
  text: string | null;
  message: Record<string, unknown>;
  state: 'local_ready' | 'blocked_by_capability';
  externalDispatchExecuted: false;
  createdAt: string;
};

export type KlmMetaGroupMessageCollection = {
  items: KlmMetaGroupMessage[];
  total: number;
};

export type KlmMetaGroupCreateResult = {
  group: KlmMetaGroup;
  operation: KlmMetaGroupOperation;
  deduplicated: boolean;
};

export type KlmMetaGroupOfficialCreationPreparation = {
  operation: KlmMetaGroupOperation;
  gate: KlmMetaGroupCapabilityGate;
  groupProjectionCreated: false;
  projectionSource: 'verified_group_lifecycle_update';
  providerRequestPath: string | null;
  providerRequestPayloadReady: boolean;
  providerRequestPayload: Record<string, unknown> | null;
  externalDispatchExecuted: false;
  blockedReasons: string[];
  deduplicated: boolean;
};

export type KlmMetaGroupMessagePreparation = {
  group: KlmMetaGroup;
  operation: KlmMetaGroupOperation;
  recipientType: 'group';
  toGroupId: string | null;
  providerPayloadReady: boolean;
  providerPayload: Record<string, unknown> | null;
  externalDispatchExecuted: false;
  blockedReasons: string[];
  deduplicated: boolean;
};

export type KlmWorkspaceTeam = {
  id: string;
  workspaceId: string;
  name: string;
  slug: string;
  description: string | null;
  teamType: string;
  color: string;
  capacity: number;
  routingStrategy: string;
  schedule: Record<string, unknown>;
  isActive: boolean;
  memberCount: number;
  availableCount: number;
  openThreadCount: number;
  createdAt: string;
  updatedAt: string;
};

export type KlmWorkspaceUser = {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  type: 'human' | 'ai' | null;
  role: string;
  initials: string;
  color: string;
  avatar: string | null;
  isActive: boolean;
  isInternalStaff: boolean;
};

export type ExecutiveShareCandidate = {
  id: string;
  name: string;
  email: string | null;
  initials: string;
  workspaceRole: 'workspace_owner' | 'workspace_admin';
};

export type KlmWorkspaceTeamMember = {
  id: string;
  workspaceId: string;
  teamId: string;
  membershipId: string;
  userId: string;
  userName: string;
  memberRole: string;
  routingWeight: number;
  maxConcurrent: number;
  isAvailable: boolean;
  createdAt: string;
  updatedAt: string;
};

export type KlmStaffAvailabilityRedirect = {
  kind: 'user' | 'team';
  userId?: string | null;
  teamId?: string | null;
};

export type KlmStaffRecurringSlot = {
  id?: string;
  weekday: number;
  startsAt: string;
  endsAt: string;
  validFrom?: string | null;
  validUntil?: string | null;
  label?: string | null;
};

export type KlmStaffAvailabilityException = {
  id: string;
  workspaceId: string;
  scope: 'staff' | 'workspace';
  staffUserId: string | null;
  kind: 'unavailable' | 'available_override' | 'vacation' | 'holiday';
  title: string;
  startsAt: string;
  endsAt: string;
  allDay: boolean;
  absenceMessage: string | null;
  redirectKind: 'user' | 'team' | null;
  redirectUserId: string | null;
  redirectTeamId: string | null;
  autoReassign: boolean | null;
  version: number;
  sourceRequestId: string;
  deletedAt: string | null;
};

export type KlmStaffAvailabilityProfile = {
  id: string;
  workspaceId: string;
  staffUserId: string;
  timezone: string;
  scheduleConfigured: boolean;
  availabilityMode: 'available' | 'away';
  availabilityStartsAt: string | null;
  availabilityEndsAt: string | null;
  version: number;
  absenceMessage: string | null;
  absenceMessageStartsAt: string | null;
  absenceMessageEndsAt: string | null;
  redirectKind: 'user' | 'team' | null;
  redirectUserId: string | null;
  redirectTeamId: string | null;
  redirectStartsAt: string | null;
  redirectEndsAt: string | null;
  autoReassignEnabled: boolean;
  updatedByUserId: string | null;
};

export type KlmStaffAvailabilityState = {
  workspaceId: string;
  staffUserId: string;
  profile: KlmStaffAvailabilityProfile | null;
  recurringSlots: Array<KlmStaffRecurringSlot & {
    id: string;
    workspaceId: string;
    profileId: string;
    sourceRequestId: string;
    createdAt: string;
    updatedAt: string;
  }>;
  exceptions: KlmStaffAvailabilityException[];
};

export type KlmStaffAvailabilityChange =
  | {
      changeType: 'replace_configuration';
      mode: 'available' | 'away';
      timezone: string;
      weeklyAvailability: Array<{
        weekday: number;
        enabled: boolean;
        windows: Array<{ start: string; end: string }>;
      }>;
      exceptions: Array<{
        id: string;
        kind: 'vacation' | 'holiday' | 'exception';
        name: string;
        startsOn: string;
        endsOn: string;
        mode: 'unavailable' | 'custom_hours';
        windows: Array<{ start: string; end: string }>;
      }>;
      absenceMessage: { enabled: boolean; text: string };
      activation: {
        mode: 'now' | 'scheduled';
        startsAt: string | null;
        endsAt: string | null;
      };
      redirect: { kind: 'none' | 'user' | 'team'; targetId: string | null };
      automaticSwap: boolean;
    }
  | {
      changeType: 'replace_recurring_schedule';
      timezone: string;
      slots: KlmStaffRecurringSlot[];
    }
  | {
      changeType: 'create_exception';
      scope: 'staff' | 'workspace';
      kind: 'unavailable' | 'available_override' | 'vacation' | 'holiday';
      title: string;
      startsAt: string;
      endsAt: string;
      allDay: boolean;
      absenceMessage?: string | null;
      redirect?: KlmStaffAvailabilityRedirect | null;
      autoReassign?: boolean | null;
    }
  | {
      changeType: 'update_exception';
      exceptionId: string;
      expectedExceptionVersion: number;
      kind: 'unavailable' | 'available_override' | 'vacation' | 'holiday';
      title: string;
      startsAt: string;
      endsAt: string;
      allDay: boolean;
      absenceMessage?: string | null;
      redirect?: KlmStaffAvailabilityRedirect | null;
      autoReassign?: boolean | null;
    }
  | {
      changeType: 'delete_exception';
      exceptionId: string;
      expectedExceptionVersion: number;
    }
  | {
      changeType: 'set_absence_routing';
      absenceMessage?: string | null;
      absenceMessageStartsAt?: string | null;
      absenceMessageEndsAt?: string | null;
      redirect?: KlmStaffAvailabilityRedirect | null;
      redirectStartsAt?: string | null;
      redirectEndsAt?: string | null;
      autoReassignEnabled: boolean;
    };

export type KlmStaffAvailabilityChangeRequest = {
  id: string;
  workspaceId: string;
  targetUserId: string | null;
  requestedByUserId: string;
  resolvedByUserId: string | null;
  changeType: KlmStaffAvailabilityChange['changeType'];
  status: 'pending' | 'approved' | 'rejected' | 'cancelled';
  proposedChange: Record<string, unknown>;
  targetEntityId: string | null;
  baseVersion: number;
  appliedVersion: number | null;
  idempotencyKey: string;
  reason: string;
  decisionNote: string | null;
  decisionPolicy: Record<string, unknown> | null;
  resolvedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmStaffEffectiveAvailability = {
  workspaceId: string;
  staffUserId: string;
  evaluatedAt: string;
  timezone: string;
  configured: boolean;
  isAvailable: boolean;
  reason: string;
  activeExceptionId: string | null;
  activeExceptionKind: string | null;
  absenceMessage: string | null;
  redirectKind: 'user' | 'team' | null;
  redirectUserId: string | null;
  redirectTeamId: string | null;
  autoReassignEnabled: boolean;
};

export type KlmConversationMention = {
  id: string;
  workspaceId: string;
  threadId: string;
  referenceId: string | null;
  sourceMessageId: string;
  mentionType: 'user' | 'team';
  targetId: string;
  resolvedLabel: string;
  resolutionSource: string;
  resolvedAt: string;
  createdByUserId: string | null;
  createdAt: string;
};

export type KlmConversationReference = {
  id: string;
  workspaceId: string;
  threadId: string;
  sourceMessageId: string;
  targetMessageId: string;
  referenceType: 'reply' | 'quote' | 'context';
  idempotencyKey: string;
  createdByUserId: string | null;
  createdAt: string;
  mentions: KlmConversationMention[];
};

export type KlmConversationParticipant = {
  id: string;
  workspaceId: string;
  threadId: string;
  groupId: string;
  participantType: 'user' | 'team';
  participantId: string;
  role: 'owner' | 'admin' | 'member' | 'observer';
  resolvedLabel: string;
  resolutionSource: string;
  resolvedAt: string;
  idempotencyKey: string;
  createdByUserId: string | null;
  createdAt: string;
};

export type KlmConversationParticipantCollection = {
  group: null | {
    id: string;
    workspaceId: string;
    threadId: string;
    name: string;
    createdByUserId: string | null;
    createdAt: string;
  };
  participants: KlmConversationParticipant[];
};

export type KlmConversationCapabilities = {
  workspaceId: string;
  threadId: string;
  groupId: string | null;
  participantCount: number;
  localBridgeGateOpen: boolean;
  externalDeliveryGateOpen: boolean;
  capabilities: Array<{
    capability: string;
    scope: 'native' | 'provider';
    status: 'supported' | 'unavailable' | 'pending_enablement';
    provider: string;
    officialCapabilityId: string | null;
    providerLinked: boolean;
    gateOpen: boolean;
    externalEffectVerified: boolean;
    reason: string;
  }>;
  observedAt: string;
};

export type KlmOmnichannelContext = {
  thread: KlmThread;
  identity: null | {
    id: string;
    clientId: string | null;
    displayName: string;
    canonicalPhone: string | null;
    canonicalEmail: string | null;
    identityState: string;
    source: string;
    mergeVersion: number;
    identityMetadata: Record<string, unknown>;
  };
  channels: Array<{
    endpointId: string;
    bindingId: string | null;
    channel: KlmOmnichannelChannel;
    provider: string;
    connectionId: string | null;
    connectionKey: string;
    connectionName: string | null;
    connectionStatus: string | null;
    transportMode: string | null;
    externalAccountId: string | null;
    address: string | null;
    externalUserId: string;
    consentState: string;
    blockedAt: string | null;
    lastSeenAt: string | null;
    bindingCount: number;
    capabilities: Record<string, unknown>;
  }>;
  bindings: Array<Record<string, unknown>>;
  actionIntents: Array<{
    id: string;
    actionType: string;
    state: string;
    body: string | null;
    scheduledAt: string | null;
    createdAt: string;
    failureReason: string | null;
  }>;
  scheduledMessages: Array<{
    id: string;
    channel: KlmOmnichannelChannel;
    body: string | null;
    templateId: string | null;
    scheduledAt: string;
    timezone: string;
    state: string;
    cancellationReason: string | null;
  }>;
  calls: Array<{
    id: string;
    channel: KlmOmnichannelChannel;
    direction: string;
    state: string;
    permissionState: string;
    requestedAt: string;
    durationSeconds: number;
    summary: string | null;
  }>;
  automationRuns: Array<{
    id: string;
    ruleId: string;
    ruleName: string;
    state: string;
    currentAction: number;
    matched: boolean;
    preparedActions: number;
    coexistWithAi: boolean;
    startedAt: string;
    completedAt: string | null;
  }>;
  timeline: Array<{
    id: string;
    kind: string;
    occurredAt: string;
    channel: KlmOmnichannelChannel | null;
    eventType: string;
    direction: string | null;
    authorLabel: string | null;
    body: string | null;
    visibility: string;
    status: string | null;
    metadata: Record<string, unknown>;
  }>;
  externalGateOpen: boolean;
  generatedAt: string;
};

export type KlmOmnichannelMessageRoute = {
  endpointId?: string | null;
  bindingId?: string | null;
  connectionId?: string | null;
  connectionKey?: string | null;
  transport?: string | null;
};

export type KlmRichMessageContentType =
  | 'media'
  | 'template'
  | 'interactive_buttons'
  | 'interactive_list'
  | 'whatsapp_flow';

export type KlmRichMessageCommand = {
  body: string;
  contentType: KlmRichMessageContentType;
  language?: string | null;
  templateId?: string | null;
  messageMetadata: Record<string, unknown>;
};

export type KlmOmnichannelCopilot = {
  action: string;
  engine: string;
  content: string;
  suggestions: string[];
  unansweredQuestions: string[];
  tasks: Array<{ title: string; kind: string; source: string }>;
  evidenceMessageIds: string[];
  confidence: number;
  requiresModel: boolean;
  externalRequests: number;
  generatedAt: string;
};

export type KlmChannelConnectionValidation = {
  connection: KlmChannelConnection;
  readyForExternalSetup: boolean;
  missingConfiguration: string[];
  supportedCapabilities: string[];
  configurationRequired: string[];
  unsupportedCapabilities: string[];
  externalGateOpen: boolean;
};

export type KlmWebhookCredentialStatus = {
  connectionId: string;
  provider: string;
  connectionKey: string;
  state: string;
  algorithm: string;
  signingSecretConfigured: boolean;
  verifyTokenConfigured: boolean;
  timestampToleranceSeconds: number;
  version: number;
  webhookPath: string;
  externalGateOpen: boolean;
  lastVerifiedAt: string | null;
  rotatedAt: string | null;
  updatedAt: string;
};

export type KlmWebhookCenterReceipt = {
  id: string;
  connectionId: string;
  provider: string;
  connectionKey: string;
  externalEventId: string;
  externalLeadId: string | null;
  payloadSha256: string;
  signatureScheme: string;
  signatureTimestamp: string | null;
  receivedAt: string;
  processingState: string;
  leadCaptureReceiptId: string | null;
  leadCaptureState: string | null;
  hydrationJobId: string | null;
  hydrationState: string | null;
  threadId: string | null;
  matterId: string | null;
  identityId: string | null;
  errorCode: string | null;
};

export type KlmWebhookCenter = {
  connectionCount: number;
  configuredConnectionCount: number;
  activeCredentialCount: number;
  receiptWindowCount: number;
  receiptTotalCount: number;
  receiptMatchCount: number;
  processedCount: number;
  awaitingHydrationCount: number;
  failedCount: number;
  blockedCount: number;
  idempotencyEnforced: boolean;
  externalGateOpen: boolean;
  connections: Array<{
    connection: KlmChannelConnection;
    credential: KlmWebhookCredentialStatus | null;
    setupProgress: number;
    setupSteps: Array<{ key: string; label: string; completed: boolean; detail: string }>;
    receiptCount: number;
    processedCount: number;
    awaitingHydrationCount: number;
    failedCount: number;
    blockedCount: number;
    latestReceiptAt: string | null;
  }>;
  receipts: KlmWebhookCenterReceipt[];
  generatedAt: string;
};

export type KlmOmnichannelTransfer = {
  thread: KlmThread;
  event: {
    id: string;
    fromUserId: string | null;
    toUserId: string | null;
    fromTeamId: string | null;
    toTeamId: string | null;
    action: string;
    reason: string | null;
    source: string;
    expectedThreadVersion: number;
    resultThreadVersion: number;
    occurredAt: string;
  };
  deduplicated: boolean;
};

export type KlmOmnichannelActionIntent = {
  id: string;
  threadId: string;
  eventId: string | null;
  connectionId: string | null;
  actionType: string;
  targetExternalId: string | null;
  body: string | null;
  state: string;
  requiredCapability: string;
  capabilityState: string;
  scheduledAt: string | null;
  actionPayload: Record<string, unknown>;
  failureReason: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmOmnichannelChannelEvent = {
  event: {
    id: string;
    threadId: string;
    parentEventId: string | null;
    provider: string;
    connectionKey: string;
    eventType: string;
    externalEventId: string;
    actorExternalId: string | null;
    visibility: string;
    publicUrl: string | null;
    actionability: string;
    deadlineAt: string | null;
    occurredAt: string;
    receivedAt: string;
    correlation: Record<string, unknown>;
    eventPayload: Record<string, unknown>;
    eventMetadata: Record<string, unknown>;
  };
  thread: KlmThread;
  identity: KlmOmnichannelContext['identity'];
  message: KlmMessage | null;
  connection: null | {
    id: string;
    displayName: string;
    externalAccountId: string | null;
    transportMode: string;
    status: string;
    isDefault: boolean;
    capabilities: Record<string, { supported?: boolean; enabled?: boolean; state?: string; note?: string }>;
  };
  channel: KlmOmnichannelChannel;
};

export type KlmOmnichannelChannelEventList = {
  items: KlmOmnichannelChannelEvent[];
  hasMore: boolean;
  generatedAt: string;
};

export type KlmLeadCaptureReceipt = {
  id: string;
  workspaceId: string;
  provider: 'meta_ads' | 'tiktok_ads' | 'site' | 'lovable' | 'local_fixture' | string;
  connectionKey: string;
  externalLeadId: string;
  externalFormId: string | null;
  payloadSha256: string;
  state: string;
  mappingVersion: string;
  identityId: string | null;
  threadId: string | null;
  matterId: string | null;
  result: Record<string, unknown> & {
    scheduledMessageId?: string | null;
    proactiveRoute?: null | {
      channel?: string;
      connectionId?: string | null;
      connectionKey?: string;
      connectionName?: string | null;
      transportMode?: string | null;
      state?: string;
      externalActionsExecuted?: number;
    };
  };
  errorCode: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmLeadCaptureInbox = {
  items: Array<{
    receipt: KlmLeadCaptureReceipt;
    identity: KlmOmnichannelContext['identity'];
    thread: KlmThread | null;
    acquisition: {
      provider?: string;
      source?: string | null;
      campaign?: string | null;
      adSet?: string | null;
      ad?: string | null;
      formId?: string | null;
      providerReferences?: Record<string, string>;
      channel?: KlmOmnichannelChannel;
      receivedAt?: string;
      clickIdKeys?: string[];
    };
  }>;
  total: number;
  hasMore: boolean;
  countsByProvider: Record<string, number>;
  countsByState: Record<string, number>;
  generatedAt: string;
};

export type KlmLeadCaptureIngestResult = {
  receipt: KlmLeadCaptureReceipt;
  identity: NonNullable<KlmOmnichannelContext['identity']>;
  endpoint: Record<string, unknown> & { id: string };
  binding: Record<string, unknown> & { id: string };
  thread: KlmThread;
  message: KlmMessage | null;
  clientId: string;
  matterId: string;
  deduplicated: boolean;
};

export type KlmLeadHydrationJob = {
  id: string;
  workspaceId: string;
  connectionId: string;
  publicIngressReceiptId: string;
  leadCaptureReceiptId: string | null;
  provider: string;
  connectionKey: string;
  externalEventId: string;
  externalLeadId: string;
  externalFormId: string | null;
  locatorPayload: Record<string, unknown>;
  payloadSha256: string;
  state: string;
  attemptCount: number;
  maxAttempts: number;
  nextAttemptAt: string | null;
  leaseExpiresAt: string | null;
  fetchAttemptedAt: string | null;
  hydratedAt: string | null;
  completedAt: string | null;
  lastErrorCode: string | null;
  lastErrorMessage: string | null;
  providerReceipt: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

export type KlmLeadHydrationEvent = {
  id: string;
  hydrationJobId: string;
  provider: string;
  externalLeadId: string;
  eventType: string;
  previousState: string;
  nextState: string;
  errorCode: string | null;
  errorMessage: string | null;
  occurredAt: string;
};

export type KlmAdsManagerEntity = {
  id: string;
  workspaceId: string;
  connectionId: string;
  provider: string;
  entityType: 'account' | 'campaign' | 'ad_set' | 'ad' | 'creative' | 'form';
  externalId: string;
  parentExternalId: string | null;
  displayName: string;
  effectiveStatus: string;
  configuredStatus: string | null;
  objective: string | null;
  buyingType: string | null;
  currency: string | null;
  timezone: string | null;
  hierarchyPath: string[];
  entityMetadata: Record<string, unknown>;
  version: number;
  firstSeenAt: string;
  lastSeenAt: string;
  createdAt: string;
  updatedAt: string;
};

export type KlmAdsManagerImport = {
  id: string;
  connectionId: string;
  provider: string;
  externalSyncId: string;
  source: string;
  entityCount: number;
  observationCount: number;
  observedAt: string;
  receivedAt: string;
};

export type KlmAdsCampaignPerformance = {
  entityId: string;
  externalId: string;
  displayName: string;
  provider: string;
  effectiveStatus: string;
  objective: string | null;
  currency: string;
  spend: string | number;
  impressions: number;
  reach: number;
  clicks: number;
  conversations: number;
  comments: number;
  providerLeads: number;
  capturedLeads: number;
  qualifiedLeads: number;
  opportunities: number;
  contracts: number;
  revenue: string | number;
  ctr: string | number | null;
  cpl: string | number | null;
  cac: string | number | null;
  roas: string | number | null;
};

export type KlmAdsManagerSummary = Omit<KlmAdsCampaignPerformance,
  'entityId' | 'externalId' | 'displayName' | 'effectiveStatus' | 'objective' | 'provider'> & {
  dateFrom: string;
  dateTo: string;
  provider: string | null;
  capturedLeads: number;
  hydrationPending: number;
  hydrationFailed: number;
  campaigns: KlmAdsCampaignPerformance[];
  generatedAt: string;
};

export type KlmAdsSnapshotPayload = {
  externalSyncId: string;
  source: 'local_fixture' | 'manual' | 'provider_sync';
  observedAt: string;
  entities: Array<{
    entityType: KlmAdsManagerEntity['entityType'];
    externalId: string;
    parentExternalId?: string | null;
    displayName: string;
    effectiveStatus?: string;
    configuredStatus?: string | null;
    objective?: string | null;
    currency?: string | null;
    timezone?: string | null;
    hierarchyPath?: string[];
    metadata?: Record<string, unknown>;
    metrics?: Array<{
      metricDate: string;
      currency?: string;
      spend?: string | number;
      impressions?: number;
      reach?: number;
      clicks?: number;
      conversations?: number;
      comments?: number;
      leads?: number;
      qualifiedLeads?: number;
      opportunities?: number;
      contracts?: number;
      revenue?: string | number;
    }>;
  }>;
  metadata?: Record<string, unknown>;
};

export type KlmAdsSnapshotResult = {
  receipt: KlmAdsManagerImport;
  entities: KlmAdsManagerEntity[];
  observations: Array<Record<string, unknown>>;
  deduplicated: boolean;
};

export type KlmOmnichannelAutomationRule = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  channel: KlmOmnichannelChannel | null;
  triggerType: string;
  status: 'draft' | 'active' | 'paused';
  priority: number;
  coexistWithAi: boolean;
  stopOnMatch: boolean;
  triggerConfig: Record<string, unknown>;
  conditions: Array<Record<string, unknown>>;
  actions: Array<Record<string, unknown>>;
  quietHours: Record<string, unknown>;
  rateLimits: Record<string, unknown>;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type KlmOmnichannelScheduledMessage = KlmOmnichannelContext['scheduledMessages'][number] & {
  threadId: string;
  endpointId: string | null;
  connectionKey: string;
  attachments: Array<Record<string, unknown>>;
  attempts: number;
  maxAttempts: number;
  nextAttemptAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmOmnichannelCall = KlmOmnichannelContext['calls'][number] & {
  threadId: string;
  endpointId: string | null;
  connectionKey: string;
  providerCallId: string | null;
  failureReason: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmAreaProduct = {
  key: string;
  label: string;
  description: string;
  keywords: string[];
  requiredDocuments: string[];
  specialistAgentKey: string;
  closerAgentKey: string;
  contractTemplateKey: string;
  playbookScope: string;
  audience: string;
};

export type KlmAreaSubagent = {
  key: string;
  name: string;
  role: string;
  parentAgentKey: string;
  productKey?: string | null;
  description: string;
  taskClass: 'simple' | 'medium' | 'complex';
  tools: string[];
  gate: string;
};

export type KlmAreaBlueprint = Record<string, unknown> & {
  catalogVersion?: string;
  catalogManaged?: boolean;
  taxonomy?: { subthemes?: string[]; requiredFields?: string[] };
  products?: KlmAreaProduct[];
  documents?: string[];
  funnel?: string[];
  agentTeam?: string[];
  subagents?: KlmAreaSubagent[];
  routing?: {
    coordinationAgentKey?: string;
    ownerBridgeAgentKey?: string;
    ownerBridgeDelegatorKey?: string;
    sdrAgentKey?: string;
    specialistAgentKey?: string;
    closerAgentKey?: string;
    fallbackAgentKey?: string;
    manualOverrideWins?: boolean;
    productKeywordRouting?: boolean;
  };
  agentGovernance?: {
    policyVersion?: string;
    maxDelegationDepth?: number;
    enforceParentChain?: boolean;
    manualOverrideWins?: boolean;
    hermesCannotOrchestrate?: boolean;
    crossAreaAccessDisabled?: boolean;
    crossProductAccessDisabled?: boolean;
    crossWorkspaceAccessDenied?: boolean;
    crossClientAccessDenied?: boolean;
    leastPrivilegeTools?: boolean;
    independentReviewRequired?: boolean;
    externalActionsDisabled?: boolean;
  };
  contracts?: Array<{ key: string; label: string; productKey: string; playbookScope: string }>;
  playbook?: { onboardingRequired?: boolean; sections?: string[] };
  communicationProfile?: Record<string, unknown>;
  audiences?: string[];
  gates?: Record<string, unknown> & {
    humanRequestAgent?: string;
    humanHandoffCoordinator?: string;
    externalActionsDisabled?: boolean;
  };
};

export type KlmArea = {
  id: string;
  areaKey: string;
  name: string;
  description: string | null;
  status: 'active' | 'blueprint' | 'draft' | 'archived';
  version: string;
  readiness: number;
  isActive: boolean;
  blueprint: KlmAreaBlueprint;
  validatedAt: string | null;
  validatedByUserId?: string | null;
};

export type KlmTool = {
  key: string;
  name: string;
  category: string;
  state: string;
  external: boolean;
  humanGate: boolean;
  capabilities: string[];
};

export type KlmTechnicalRequest = {
  id: string;
  title: string;
  description: string;
  route: string | null;
  severity: 'low' | 'medium' | 'high' | 'critical';
  status: 'received' | 'analyzing' | 'patch_ready' | 'validating' | 'resolved' | 'rejected';
  resolution: string | null;
  evidence: Array<Record<string, unknown>>;
  createdAt: string;
  updatedAt: string;
};

export type KlmSupportTicketType = 'problem' | 'instability' | 'suggestion';
export type KlmSupportPriority = 'low' | 'normal' | 'high' | 'urgent';
export type KlmSupportSeverity = 'low' | 'medium' | 'high' | 'critical';
export type KlmSupportStatus =
  | 'new'
  | 'triage'
  | 'in_progress'
  | 'waiting_customer'
  | 'waiting_internal'
  | 'validating'
  | 'resolved'
  | 'closed'
  | 'cancelled';

export type KlmSupportTechnicalContext = {
  page: string | null;
  action: string | null;
  frontendVersion: string | null;
  browser: string | null;
  device: string | null;
  viewport: string | null;
  occurredAt: string | null;
  realtimeStatus: string | null;
  failedRequestIds: string[];
};

export type KlmSupportTicketCreate = {
  ticketType: KlmSupportTicketType;
  subject: string;
  description: string;
  priority: KlmSupportPriority;
  severity: KlmSupportSeverity;
  technicalContext: KlmSupportTechnicalContext;
};

export type KlmSupportUser = {
  id: string;
  name: string;
  avatarUrl: string | null;
};

export type KlmSupportTicket = {
  id: string;
  publicNumber: string;
  workspaceId: string;
  reporterUserId: string;
  assignedToUserId: string | null;
  ticketType: KlmSupportTicketType;
  priority: KlmSupportPriority;
  severity: KlmSupportSeverity;
  queue: 'support' | 'technical';
  status: KlmSupportStatus;
  subject: string;
  description: string;
  route: string | null;
  technicalContext: Record<string, unknown>;
  errorFingerprint: string | null;
  linkedErrorLogId: string | null;
  linkedIncidentId: string | null;
  linkedReleaseId: string | null;
  supportAccessRequestId: string | null;
  duplicateOfTicketId: string | null;
  version: number;
  firstResponseDueAt: string;
  resolutionDueAt: string;
  firstRespondedAt: string | null;
  resolvedAt: string | null;
  closedAt: string | null;
  reopenedAt: string | null;
  satisfactionScore: number | null;
  satisfactionComment: string | null;
  createdAt: string;
  updatedAt: string;
  reporter: KlmSupportUser | null;
  assignee: KlmSupportUser | null;
};

export type KlmSupportMessage = {
  id: string;
  authorUserId: string | null;
  authorLabel: string;
  visibility: 'public' | 'internal';
  body: string;
  createdAt: string;
};

export type KlmSupportAttachment = {
  id: string;
  messageId: string | null;
  storageObjectVersionId: string;
  originalFilename: string;
  contentType: string;
  byteSize: number | null;
  state: 'pending_upload' | 'quarantined' | 'available' | 'rejected' | 'deleted';
  avStatus: 'pending' | 'clean' | 'infected' | 'error';
  createdAt: string;
};

export type KlmSupportEvent = {
  id: string;
  actorUserId: string | null;
  eventType: string;
  summary: string;
  beforeData: Record<string, unknown>;
  afterData: Record<string, unknown>;
  createdAt: string;
};

export type KlmSupportTicketDetail = KlmSupportTicket & {
  messages: KlmSupportMessage[];
  attachments: KlmSupportAttachment[];
  events: KlmSupportEvent[];
  collaboratorIds: string[];
};

export type KlmSupportTicketPage = {
  items: KlmSupportTicket[];
  nextCursor: string | null;
};

type KlmSupportUploadAuthorization = {
  attachmentId: string;
  storageObjectVersionId: string;
  uploadUrl: string | null;
  uploadRequired: boolean;
  uploadHeaders?: Record<string, string>;
  expiresAt: string;
};

export type KlmSupportAttachmentDownload = {
  attachmentId: string;
  downloadUrl: string;
  expiresAt: string;
};

export type KlmExternalActionIntent = {
  id: string;
  workspaceId: string;
  requestedByUserId: string | null;
  matterId: string | null;
  actionType: string;
  provider: string | null;
  idempotencyKey: string;
  status: 'draft' | 'awaiting_approval' | 'approved_local' | 'blocked_external' | 'executed' | 'cancelled';
  payload: Record<string, unknown>;
  reason: string;
  createdAt: string;
  updatedAt: string;
};

export type KlmLegalArtifactKind = 'proposal' | 'contract' | 'power_of_attorney' | 'petition' | 'client_update';

export type KlmLegalArtifactVersion = {
  id: string;
  workspaceId: string;
  matterId: string;
  artifactKey: string;
  version: number;
  kind: KlmLegalArtifactKind;
  title: string;
  status: 'draft' | 'review_pending' | 'approved' | 'rejected' | 'changes_requested' | 'archived';
  content: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  createdByUserId: string;
  approvalItemId: string | null;
  createdAt: string;
  updatedAt: string;
  sensitiveDataHidden?: boolean;
};

export type KlmProcessMonitoringRecord = {
  id: string;
  workspaceId: string;
  matterId: string;
  createdByUserId: string;
  caseNumber: string;
  court: string | null;
  status: string;
  nextDeadlineAt: string | null;
  lastCheckedAt: string | null;
  lastEventSummary: string | null;
  sourceState: 'manual_local' | string;
  events: Array<{
    id: string;
    occurredAt: string;
    title: string;
    description?: string | null;
    source: string;
    recordedByUserId: string;
  }>;
  createdAt: string;
  updatedAt: string;
  sensitiveDataHidden?: boolean;
};

export type KlmTask = {
  id: string;
  version: number;
  matterId: string;
  title: string;
  description: string | null;
  status: 'pending' | 'in_progress' | 'done' | 'blocked';
  priority: 'alta' | 'media' | 'baixa' | null;
  assignedTo: string | null;
  dueDate: string | null;
  isAiGenerated: boolean;
  createdAt: string;
  // Referência cruzada com a Central de Exceções — só preenchida quando a
  // task nasceu de `ExceptionCenterService._materialize_task_for_exception`
  // (ver app/schemas/task.py::TaskRead); nunca um campo de escrita do
  // cliente. `null`/ausente = task criada normalmente, sem origem de falha.
  sourceExceptionId?: string | null;
};

export type KlmDocument = {
  id: string;
  matterId: string;
  name: string;
  type: string | null;
  status: string;
  url: null;
  size: string | null;
  mimeType: string | null;
  createdAt: string;
};

export type KlmDocumentUpload = {
  uploadUrl: string | null;
  uploadRequired: boolean;
  documentId: string;
  s3Key: string;
  expiresIn: number;
  uploadHeaders?: Record<string, string>;
};

export async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function secureUploadTarget(uploadUrl: string): { target: URL; apiOrigin: string } {
  const apiOrigin = new URL(API_BASE || window.location.origin, window.location.origin).origin;
  const target = new URL(uploadUrl, `${apiOrigin}/`);
  if (
    target.username
    || target.password
    || (target.protocol !== 'https:' && target.origin !== apiOrigin)
  ) {
    throw new Error('O destino seguro do arquivo não pôde ser validado.');
  }
  return { target, apiOrigin };
}

export type KlmTrackingAggregate = {
  events: number;
  leads: number;
  qualifiedLeads: number;
  meetings: number;
  clients: number;
  adSpend: number;
  attributableRevenue: number;
  ignoredValueEvents: number;
  conversionRate: number | null;
  cac: number | null;
  roas: number | null;
};

export type KlmTrackingSummary = Omit<KlmTrackingAggregate, 'events'> & {
  periodDays: number | null;
  periodStart: string | null;
  totalEvents: number;
  identifiedJourneys: number;
  byChannel: Array<KlmTrackingAggregate & { channel: string; value: number }>;
  eventCoverage: Array<{ event: string; count: number; covered: boolean }>;
  timeTrend: Array<KlmTrackingAggregate & { date: string }>;
  journeys: Array<KlmTrackingAggregate & {
    journeyId: string;
    identityType: string;
    eventPath: string[];
    channels: string[];
    startedAt: string;
    lastActivityAt: string;
    converted: boolean;
  }>;
  remarketing: Array<{ stage: string; channel: string; audienceSize: number; lastActivityAt: string }>;
  attributionCoverage: {
    conversations: number;
    trackedConversations: number;
    untrackedConversations: number;
    trackingRate: number | null;
    conversionEvents: number;
    attributedConversions: number;
    unattributedConversions: number;
    attributedConversionRate: number | null;
    feedbackEligibleConversions: number;
    feedbackBlockedNoEvidence: number;
    businessDuplicatesSuppressed?: number;
    fullHierarchyTouches: number;
    partialHierarchyTouches: number;
  };
  attributionRows: Array<KlmTrackingAggregate & {
    provider: string | null;
    providerLabel: string;
    channel: string;
    source: string | null;
    medium: string | null;
    accountId: string | null;
    accountName: string | null;
    campaignId: string | null;
    campaignName: string | null;
    adSetId: string | null;
    adSetName: string | null;
    adId: string | null;
    adName: string | null;
    creativeId: string | null;
    creativeName: string | null;
    formId: string | null;
    formName: string | null;
    placement: string | null;
    landingPageUrl: string | null;
    sourceUrl: string | null;
    clickIdKeys: string[];
    evidenceLevel: 'exact' | 'named' | 'unattributed';
    evidenceReason: string;
    hierarchyCompleteness: number;
    missingLevels: string[];
    providerConsoleUrl: string | null;
    attributionModel: 'last_paid_touch';
    firstTouchAt: string;
    lastTouchAt: string;
    touchCount: number;
    journeyCount: number;
    sampleJourneyId: string | null;
    sampleThreadId: string | null;
    sampleMatterId: string | null;
  }>;
  funnelSteps: Array<{
    stage: string;
    journeys: number;
    shareOfStart: number | null;
    conversionFromPrevious: number | null;
    dropOffFromPrevious: number;
    dropOffRate: number | null;
  }>;
  insights: Array<{
    key: string;
    severity: 'info' | 'warning' | 'critical' | 'opportunity';
    title: string;
    detail: string;
    recommendedAction: string;
    metric: Record<string, unknown>;
    evidence: Record<string, unknown>;
  }>;
  serviceQuality: {
    findings: number;
    activeFindings: number;
    criticalFindings: number;
    unansweredLeads: number;
    deliveryMismatches: number;
    duplicateOutbound: number;
    resolvedFindings: number;
    auditedThreads: number;
    qualityScore: number;
  };
  conversionFeedback: {
    prepared: number;
    blockedExternal: number;
    awaitingApproval: number;
    approvedLocal: number;
    rejected: number;
    duplicatePrevented: number;
    payloadReady: number;
    configurationBlocked: number;
    preparedProofs: number;
    providerReceipts: number;
    failedReceipts: number;
    providers: Record<string, number>;
  };
  feedbackEvents: Array<{
    intentId: string;
    provider: string;
    sourceEventId: string;
    sourceTouchEventId: string;
    conversionEvent: string;
    happenedAt: string;
    value: number | null;
    currency: string;
    status: string;
    providerEventId: string;
    matchKeyNames: string[];
    payloadSchemaVersion: string;
    matchingMode: string;
    readyForDispatch: boolean;
    missingConfiguration: string[];
    privacyGuard: {
      rawContactDataIncluded?: boolean;
      sensitiveMatterDataIncluded?: boolean;
      allowedDataClasses?: string[];
      excludedDataClasses?: string[];
    };
    payloadSha256: string;
    proofState: string;
    receiptState: string | null;
    receiptCount: number;
    dedupeKey: string;
    externalActionsExecuted: number;
  }>;
  dailyChannels: Array<{ date: string; channel: string; events: number; leads: number; clients: number; attributableRevenue: number }>;
  containsLocalFixtureData: boolean;
  generatedAt: string;
};

export type KlmConversionFeedbackPrepare = {
  scanned: number;
  prepared: number;
  deduplicated: number;
  skippedWithoutEvidence: number;
  suppressedBusinessDuplicates?: number;
  providers: Record<string, number>;
  externalActionsExecuted: number;
};

export type KlmAdsFeedbackDispatch = {
  preparedProcessed: number;
  alreadyDispatched: number;
  blockedSimulated: number;
  dispatchedReal: number;
  failed: number;
  externalActionsEnabled: boolean;
};

export type KlmAdsMeasurementConfig = {
  configured: boolean;
  datasetId: string | null;
  pixelId: string | null;
  accessTokenConfigured: boolean;
  accessTokenLast4: string | null;
  updatedAt: string | null;
};

export type KlmAdsMeasurementConfigUpdate = {
  datasetId?: string | null;
  pixelId?: string | null;
  accessToken?: string | null;
};

export type KlmCtwaBackfillReport = {
  scanned: number;
  withReferral: number;
  eventsCreated: number;
  skippedExisting: number;
  unresolved: number;
  dryRun: boolean;
  sampleDedupeKeys: string[];
};

export type KlmFlow = {
  id: string;
  name: string;
  description: string | null;
  practice_area: string;
  is_template: boolean;
  is_active: boolean;
  trigger: string;
  trigger_config: Record<string, unknown> | null;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  run_count: number;
  current_revision_id: string | null;
  current_revision: number;
  version: number;
};

export type KlmFlowNodeCatalog = {
  schema_version: number;
  node_count: number;
  external_node_types: string[];
  nodes: Array<{
    type: string;
    label: string;
    description: string;
    category: string;
    execution_mode: 'native' | 'control' | 'gated_external';
    side_effect: 'none' | 'local' | 'external';
    input_ports: Array<{ key: string; label: string }>;
    output_ports: Array<{ key: string; label: string }>;
    supports_dry_run: boolean;
    supports_error_route: boolean;
    config_version: number;
    /**
     * Contrato de autoria fornecido pelo runtime. A ausência é intencional em
     * nós legados: o editor deve mostrá-los como indisponíveis para criação em
     * vez de inventar campos que o backend não reconhece.
     */
    schema?: Record<string, unknown>;
    uischema?: Record<string, unknown>;
    outputSchema?: Record<string, unknown>;
    /** The runtime may expose a catalogued node without enabling it in this
     * environment. It remains visible as unavailable, never authorable. */
    availability?: 'available' | 'unavailable' | string;
    availabilityReason?: string | null;
    availability_reason?: string | null;
  }>;
};

export type KlmFlowNodeCatalogEntry = KlmFlowNodeCatalog['nodes'][number];

export type KlmHumanRoutingCatalog = {
  roles: Array<{ key: string; label: string; routableCount: number }>;
  skills: Array<{ key: string; label: string; routableCount: number }>;
  teams: Array<{ key: string; label: string; routableCount: number }>;
  directOwnerOnly: boolean;
  ownerFallback: { available: boolean; label: string; reason: string };
};

export type KlmHumanRoutingPolicyCatalog = {
  version: string;
  policies: Array<{
    key: string;
    version: string;
    label: string;
    description: string;
    requiredRoles: string[];
    requiredSkills: string[];
    maxHops: number;
    available: boolean;
    specialistAvailable: boolean;
    matchedRoles: string[];
    matchedSkills: string[];
    fallbackMode: 'finite_hierarchy' | 'owner' | string;
  }>;
};

export type KlmFlowGraphValidation = {
  valid: boolean;
  node_count: number;
  edge_count: number;
  reachable_node_count: number;
  estimated_cost_units: number;
  external_action_count: number;
  human_gate_count: number;
  issues: Array<{
    code: string;
    severity: 'error' | 'warning' | 'info';
    message: string;
    node_ids: string[];
  }>;
};

export type KlmAgentPlatformRoutingPathEntry = {
  userId?: string | null;
  label?: string | null;
  level?: number | null;
};

export type KlmAgentPlatformDecision = {
  id: string;
  decisionKey?: string;
  status: string;
  assignedUserId?: string | null;
  ownerUserId?: string | null;
  assignedUserLabel?: string | null;
  ownerUserLabel?: string | null;
  dueAt?: string | null;
  acknowledgedAt?: string | null;
  createdAt?: string | null;
  escalationLevel?: number;
  routingLevels?: number;
  requiredRoles?: string[];
  requiredSkills?: string[];
  purpose?: string | null;
  resolutionMode?: 'agent_outcome' | 'flow_checkpoint' | string;
  agentRunId?: string | null;
  allowedActions?: Array<'approve' | 'reject' | string>;
  inputFields?: string[];
  phoneDelivery?: Array<{ recipientKind: string; status: string; attempts: number; lastErrorCode?: string | null; providerReceiptId?: string | null }>;
  notificationDelivery?: Array<{ channel: string; recipientKind: string; status: string; attempts: number; lastErrorCode?: string | null; deliveredAt?: string | null; providerReceiptId?: string | null }>;
  routingPathLabels?: Array<KlmAgentPlatformRoutingPathEntry | string>;
  nextEscalation?: { userLabel?: string | null; roleLabel?: string | null; at?: string | null } | null;
};

export type KlmAgentPlatformDeployment = {
  id: string;
  agentKey?: string;
  agent_key?: string;
  version: number;
  status: string;
  rolePackage?: string;
  config?: {
    definitionVersion?: number;
    rolePackage?: string;
    model?: string;
    tools?: string[];
    knowledgeSources?: string[];
    autonomyPolicy?: string;
    humanRoutingPolicy?: string;
    budgetPolicy?: { monthlyTokenLimit?: number };
    testedAt?: string;
    testEvidence?: { kind?: string; externalModelCalled?: boolean };
  };
  gates?: Partial<Record<
    | 'definitionVerified'
    | 'roleVerified'
    | 'modelVerified'
    | 'knowledgeVerified'
    | 'autonomyVerified'
    | 'policyVerified'
    | 'entitlementVerified'
    | 'toolsVerified'
    | 'humanQueueVerified'
    | 'budgetVerified'
    | 'testPassed',
    boolean
  >> & {
    testKind?: string;
    externalModelCalled?: boolean;
    verifiedAt?: string;
    registryPolicyVersion?: string;
  };
  budgetUsage?: {
    periodStart?: string;
    usedTokens?: number;
    limitTokens?: number | null;
    remainingTokens?: number | null;
    exhausted?: boolean;
  };
};

export type KlmFlowCopilotAttachment = {
  name: string;
  media_type: string;
  size_bytes: number;
  text_excerpt?: string | null;
};

export type KlmFlowCopilotPlan = {
  proposal_id: string;
  status: 'awaiting_approval';
  engine: string;
  summary: string;
  rationale: string[];
  suggestions: string[];
  questions: string[];
  warnings: string[];
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  added_node_ids: string[];
  playbook_name: string | null;
  playbook_version: number | null;
  account_context_used: boolean;
  attachment_count: number;
  approval_required: true;
  external_actions_executed: 0;
  generated_at: string;
};

export type KlmOperationalCopilotDomain =
  | 'playbooks'
  | 'tracking'
  | 'connections'
  | 'social_comments'
  | 'agenda';

export type KlmOperationalCopilotAttachment = {
  name: string;
  media_type: string;
  size_bytes: number;
  text_excerpt?: string | null;
};

export type KlmOperationalCopilotAction = {
  action_id: string;
  label: string;
  description: string;
  kind: 'ui_update' | 'local_read' | 'local_draft' | 'local_write';
  risk: 'low' | 'medium';
  payload: Record<string, unknown>;
  required_permission: string;
  approval_required: true;
};

export type KlmOperationalCopilotPlan = {
  proposal_id: string;
  status: 'awaiting_approval';
  engine: string;
  domain: KlmOperationalCopilotDomain;
  summary: string;
  rationale: string[];
  suggestions: string[];
  questions: string[];
  warnings: string[];
  actions: KlmOperationalCopilotAction[];
  playbook_name: string | null;
  playbook_version: number | null;
  account_context_used: boolean;
  attachment_count: number;
  approval_required: true;
  external_actions_executed: 0;
  generated_at: string;
};

export type KlmFlowRun = {
  id: string;
  flow_id: string;
  matter_id: string;
  status: 'active' | 'paused' | 'completed' | 'failed' | 'waiting_human' | 'cancelled';
  current_node_id: string | null;
  context: Record<string, unknown> | null;
  pause_reason: string | null;
  started_at: string;
  completed_at: string | null;
  paused_at: string | null;
};

export type KlmFlowDryRun = {
  flow_id: string;
  matter_id: string;
  status: 'completed' | 'waiting_human' | 'max_steps_reached' | 'invalid_graph';
  trace: Array<{
    step: number;
    nodeId: string;
    type: string;
    label: string;
    status: 'simulated' | 'waiting_human';
    decision?: Record<string, unknown>;
  }>;
  max_steps: number;
  external_actions_executed: 0;
  context: Record<string, unknown>;
};

export type KlmMatterFlowJourneyStep = {
  node_id: string;
  node_type: string;
  label: string;
  state: 'completed' | 'current' | 'upcoming' | 'failed' | 'waiting_human';
  step: number | null;
  occurred_at: string | null;
  decision: Record<string, unknown> | null;
};

export type KlmMatterFlowJourney = {
  run_id: string;
  flow_id: string;
  flow_name: string;
  revision: number;
  status: KlmFlowRun['status'];
  progress_percent: number;
  completed_steps: number;
  total_steps: number;
  current_node_id: string | null;
  current_step_label: string | null;
  pause_reason: string | null;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
  paused_at: string | null;
  elapsed_seconds: number;
  timeline: KlmMatterFlowJourneyStep[];
};

export type KlmMatterFlowJourneyOverview = {
  matter_id: string;
  generated_at: string;
  kpis: {
    total_runs: number;
    active_runs: number;
    waiting_human_runs: number;
    completed_runs: number;
    failed_runs: number;
    completion_rate: number;
    overall_progress_percent: number;
    average_duration_seconds: number | null;
    completed_steps: number;
    total_steps: number;
  };
  journeys: KlmMatterFlowJourney[];
};

export type KlmAudioRequestResult = {
  message: KlmMessage;
  requestCount: number;
  gatePassed: boolean;
  externalIntent: null | { id: string; status: string; provider: string | null; reason: string };
  providerState: string;
};

// Natureza e tipo do evento — agenda unificada comercial + jurídico
// (Sprint B UI). Espelham `app.models.meeting.MeetingNature` e
// `MeetingEventType`; `prazo_fatal` é o único tipo inviolável quando a
// origem é `process_publication` no read-model `GET /agenda`.
export type KlmMeetingNature = 'comercial' | 'juridico';
export type KlmMeetingEventType =
  | 'reuniao'
  | 'audiencia'
  | 'sustentacao'
  | 'prazo_interno'
  | 'prazo_fatal'
  | 'revisao'
  | 'personalizado';

export type KlmMeetingParticipantRole = 'organizer' | 'attendee' | 'observer';
export type KlmMeetingParticipantRsvp = 'pending' | 'accepted' | 'declined' | 'tentative';

export type KlmMeetingParticipant = {
  id: string;
  membershipId: string;
  userId: string;
  role: KlmMeetingParticipantRole;
  rsvp: KlmMeetingParticipantRsvp;
  reminderMinutesBefore: number | null;
  labelSnapshot: string;
  active: boolean;
  removedAt: string | null;
};

export type KlmMeetingParticipantWrite = {
  userId: string;
  role: KlmMeetingParticipantRole;
  rsvp?: KlmMeetingParticipantRsvp;
  reminderMinutesBefore?: number | null;
};

export type KlmMeetingParticipantsUpdate = {
  expectedVersion: number;
  participants: KlmMeetingParticipantWrite[];
  confirmParticipantConflicts?: boolean;
  conflictReason?: string | null;
};

export type KlmMeeting = {
  id: string;
  workspaceId?: string;
  matterId: string | null;
  responsibleUserId?: string | null;
  linkedTaskId?: string | null;
  title: string;
  startsAt: string;
  endsAt: string | null;
  status: 'scheduled' | 'completed' | 'cancelled' | 'no_show';
  natureza?: KlmMeetingNature;
  eventType?: KlmMeetingEventType;
  location: string | null;
  meetingUrl: string | null;
  notes: string | null;
  recipientOverrides?: Array<Record<string, unknown>>;
  googleCalendarEventId?: string | null;
  reminderSentAt?: string | null;
  version: number;
  participants?: KlmMeetingParticipant[];
  createdAt?: string;
  updatedAt?: string;
};

export type KlmMeetingWrite = {
  matterId?: string | null;
  responsibleUserId?: string | null;
  linkedTaskId?: string | null;
  title: string;
  startsAt: string;
  endsAt?: string | null;
  status?: KlmMeeting['status'];
  natureza?: KlmMeetingNature;
  eventType?: KlmMeetingEventType;
  location?: string | null;
  meetingUrl?: string | null;
  notes?: string | null;
  recipientOverrides?: Array<Record<string, unknown>>;
  participants?: KlmMeetingParticipantWrite[];
  confirmParticipantConflicts?: boolean;
  conflictReason?: string | null;
};

/** Um item da agenda unificada (`GET /agenda`) — comercial e jurídico numa
 * única lista. `source` diz de onde veio: `meeting` (qualquer `Meeting`,
 * comercial ou jurídico), `process_publication` (prazo fatal já
 * CONFIRMADO da controladoria processual — sempre inviolável, com
 * `dias`/`memoriaCalculo` calculados por `prazo_calculator.py`, nunca
 * recalculados no front), ou um dos três relógios explícitos da tarefa.
 * Ver `app/schemas/agenda.py::AgendaUnifiedEventRead`. */
export type KlmAgendaUnifiedEventSource =
  | 'meeting'
  | 'process_publication'
  | 'task_due'
  | 'task_participant_due'
  | 'task_review_due';

export type KlmAgendaUnifiedEvent = {
  id: string;
  source: KlmAgendaUnifiedEventSource;
  sourceId: string;
  natureza: KlmMeetingNature;
  eventType: KlmMeetingEventType;
  title: string;
  startsAt: string;
  endsAt: string | null;
  matterId: string | null;
  matterLabel?: string | null;
  clientLabel?: string | null;
  taskId?: string | null;
  participantRole?: 'owner' | 'assignee' | 'reviewer' | 'observer' | null;
  responsibleLabel?: string | null;
  responsibleUserId: string | null;
  status: string | null;
  isInviolable: boolean;
  location: string | null;
  meetingUrl: string | null;
  dias: number | null;
  memoriaCalculo: Array<Record<string, unknown>> | null;
};

export type KlmCalendarProvider = 'manual' | 'google_meet' | 'microsoft_teams';

export type KlmAvailabilityWindow = { start: string; end: string };

export type KlmDayAvailability = {
  weekday: number;
  enabled: boolean;
  windows: KlmAvailabilityWindow[];
};

export type KlmCalendarSettings = {
  timezone: string;
  provider: KlmCalendarProvider;
  connectionStatus: 'offline' | 'manual_ready' | 'awaiting_api' | 'connected';
  accountEmail: string | null;
  calendarId: string | null;
  defaultMeetingUrl: string | null;
  defaultDurationMinutes: number;
  slotIntervalMinutes: number;
  minimumNoticeHours: number;
  bookingHorizonDays: number;
  bufferBeforeMinutes: number;
  bufferAfterMinutes: number;
  weeklyAvailability: KlmDayAvailability[];
  bookingPage: {
    enabled: boolean;
    title: string;
    description: string;
    confirmationMessage: string;
    showOfficePhone: boolean;
    officePhone: string | null;
  };
};

export type KlmBookingLink = {
  id: string;
  matterId: string | null;
  responsibleUserId: string | null;
  matterLabel: string | null;
  title: string;
  description: string | null;
  durationMinutes: number;
  meetingProvider: KlmCalendarProvider;
  expiresAt: string;
  maxUses: number | null;
  useCount: number;
  isActive: boolean;
  bookingPath: string;
  createdAt: string;
};

export type KlmAvailabilityPreview = {
  timezone: string;
  durationMinutes: number;
  meetingProvider: KlmCalendarProvider;
  connectionStatus: KlmCalendarSettings['connectionStatus'];
  meetingUrl: string | null;
  slots: Array<{ startsAt: string; endsAt: string }>;
};

export type KlmMessagingCadenceSettings = {
  enabled: boolean;
  showTypingIndicator: boolean;
  timezone: string;
  preDelayMinMs: number;
  preDelayMaxMs: number;
  charactersPerSecond: number;
  typingMinMs: number;
  typingMaxMs: number;
  jitterPercent: number;
  interMessageMinMs: number;
  maxMessagesPerMinute: number;
  maxMessagesPerHour: number;
  cooldownSeconds: number;
  quietHoursEnabled: boolean;
  quietHoursStart: number;
  quietHoursEnd: number;
  maxConsecutiveWithoutReply: number;
  stopOnBlockOrOptOut: boolean;
  officialApiOnly: boolean;
  requireValidConsent: boolean;
};

export type KlmMessagingCadencePlan = {
  enabled: boolean;
  showTypingIndicator: boolean;
  preDelayMs: number;
  typingDurationMs: number;
  totalDelayMs: number;
  plannedAt: string;
  typingStartsAt: string;
  notBefore: string;
  state: string;
  reason: string;
  minuteCount: number;
  hourCount: number;
  maxMessagesPerMinute: number;
  maxMessagesPerHour: number;
  maxConsecutiveWithoutReply: number;
  officialApiOnly: boolean;
  requireValidConsent: boolean;
  stopOnBlockOrOptOut: boolean;
};

export type KlmMeetingBriefing = {
  meetingId: string;
  matterId: string | null;
  linkedToMatter: boolean;
  clientName: string;
  contact: string | null;
  demandType: string | null;
  healthSubtheme: string | null;
  healthObjectName: string | null;
  healthInsurer: string | null;
  medicalUrgencyHours: number | null;
  leadScore: number | null;
  documentCount: number;
  readyDocumentCount: number;
  noShowRisk: string | null;
  summary: string;
  insights: string[];
  suggestedQuestions: string[];
  warnings: string[];
  nextAction: string | null;
  meetingUrl: string | null;
  roomProvider: string | null;
  generationMode: 'local_context' | string;
  generatedBy: string;
  generatedAt: string;
};

export type KlmDailyBriefing = {
  day: string;
  timezone: string;
  total: number;
  linkedCount: number;
  orphanCount: number;
  missingRoomCount: number;
  summary: string;
  priorities: string[];
  items: Array<{
    meetingId: string;
    startsAt: string;
    title: string;
    clientName: string;
    matterId: string | null;
    demandType: string | null;
    noShowRisk: string | null;
    readiness: string;
    meetingUrl: string | null;
    warnings: string[];
  }>;
  generationMode: 'local_context' | string;
  generatedBy: string;
  generatedAt: string;
};

export type KlmPublicBookingPage = {
  officeName: string;
  title: string;
  description: string;
  durationMinutes: number;
  timezone: string;
  meetingProvider: KlmCalendarProvider;
  showOfficePhone: boolean;
  officePhone: string | null;
  slots: Array<{ startsAt: string; endsAt: string }>;
};

export type KlmPublicBookingConfirmation = {
  confirmationId: string;
  startsAt: string;
  endsAt: string | null;
  timezone: string;
  location: string;
  meetingUrl: string | null;
  message: string;
};

export type KlmFinanceEntry = {
  id: string;
  matterId: string | null;
  title: string;
  description: string | null;
  status: 'pending' | 'scheduled' | 'paid' | 'overdue' | 'cancelled';
  amount: number;
  currency: string;
  dueDate: string | null;
  paidAt: string | null;
  provider: string | null;
};

export type KlmFinanceOverview = {
  totalOpenReceivables: number;
  overdueAmount: number;
  scheduledAmount: number;
  paidAmount: number;
  pendingReminders: number;
  entriesDueSoon: number;
  upcomingEntries: KlmFinanceEntry[];
  highlights: string[];
};

/** Cobrança em 1 clique (F2/F6) — ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §4.5. */
export type KlmFinanceChargeStatus =
  | 'rascunho'
  | 'criada'
  | 'visualizada'
  | 'confirmada'
  | 'recebida'
  | 'vencida'
  | 'estornada'
  | 'chargeback'
  | 'cancelada';

export type KlmFinanceCharge = {
  id: string;
  workspaceId: string;
  entryId: string;
  gateway: string;
  externalId: string | null;
  billingType: string | null;
  chargeKind: 'avulsa' | 'parcela' | 'recorrencia';
  status: KlmFinanceChargeStatus;
  value: number;
  netValue: number | null;
  feeValue: number | null;
  dueDate: string | null;
  paidAt: string | null;
  invoiceUrl: string | null;
  bankSlipUrl: string | null;
  pixPayload: string | null;
  externalReference: string | null;
  installmentRef: string | null;
  subscriptionRef: string | null;
  createdAt: string;
  updatedAt: string;
  draftNotice: string | null;
};

export type KlmFinanceAgingBuckets = {
  dias1A15: number;
  dias16A30: number;
  dias31A60: number;
  dias61Mais: number;
  total: number;
};

export type KlmFinanceKpis = {
  periodStart: string;
  periodEnd: string;
  asOf: string;
  recebido: number;
  aReceber: number;
  vencido: KlmFinanceAgingBuckets;
  ticketMedio: number;
  inadimplenciaPct: number;
  mrr: number;
  despesas: number;
  resultado: number;
};

function financeNumber(value: unknown, field: string): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) {
    throw new TypeError(`Resposta financeira inválida no campo ${field}.`);
  }
  return parsed;
}

function normalizeFinanceKpis(payload: KlmFinanceKpis): KlmFinanceKpis {
  return {
    ...payload,
    recebido: financeNumber(payload.recebido, 'recebido'),
    aReceber: financeNumber(payload.aReceber, 'aReceber'),
    vencido: {
      dias1A15: financeNumber(payload.vencido.dias1A15, 'vencido.dias1A15'),
      dias16A30: financeNumber(payload.vencido.dias16A30, 'vencido.dias16A30'),
      dias31A60: financeNumber(payload.vencido.dias31A60, 'vencido.dias31A60'),
      dias61Mais: financeNumber(payload.vencido.dias61Mais, 'vencido.dias61Mais'),
      total: financeNumber(payload.vencido.total, 'vencido.total'),
    },
    ticketMedio: financeNumber(payload.ticketMedio, 'ticketMedio'),
    inadimplenciaPct: financeNumber(payload.inadimplenciaPct, 'inadimplenciaPct'),
    mrr: financeNumber(payload.mrr, 'mrr'),
    despesas: financeNumber(payload.despesas, 'despesas'),
    resultado: financeNumber(payload.resultado, 'resultado'),
  };
}

export type KlmFinanceDreCategoria = {
  categoria: string;
  valor: number;
};

export type KlmFinanceDreMensal = {
  competencia: string;
  receitas: number;
  despesas: number;
  resultado: number;
};

export type KlmFinanceDre = {
  competencia: string;
  periodoInicio: string;
  periodoFim: string;
  receitasPorCategoria: KlmFinanceDreCategoria[];
  despesasPorCategoria: KlmFinanceDreCategoria[];
  receitaTotal: number;
  despesaTotal: number;
  resultado: number;
  mensal: KlmFinanceDreMensal[];
};

export type KlmFinanceCashFlowDay = {
  data: string;
  entradas: number;
  saidas: number;
  saldoDia: number;
  saldoAcumulado: number;
};

export type KlmFinanceCashFlowProjection = {
  asOf: string;
  horizonteDias: number;
  dias: KlmFinanceCashFlowDay[];
};

export type KlmFinanceExportFormat = 'pdf' | 'xlsx' | 'docx';
export type KlmFinanceExportJob = {
  id: string; workspaceId: string; actorUserId: string; format: KlmFinanceExportFormat;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'expired' | string;
  drillContext: Record<string, unknown>; expiresAt?: string | null; createdAt?: string | null;
  cancelRequestedAt?: string | null; errorDetail?: string | null; progress?: number;
  rowCount?: number | null; checksumSha256?: string | null; snapshotAt?: string | null;
};
export type KlmFinanceCostCenter = { id:string; workspaceId:string; code:string; name:string; description?:string|null; active:boolean; version:number; parentId?:string|null; type:string; ownerUserId?:string|null; status:string };
export type KlmFinanceCostCenterImpact = { costCenterId:string; version:number; childCount:number; allocationCount:number; allocatedAmount:number|string; requiresConfirmation:boolean };
export type KlmFinanceBudgetLine = { id:string; workspaceId:string; periodStart:string; periodEnd:string; costCenterId?:string|null; category?:string|null; entryType:'receivable'|'expense'|'refund'|string; amount:number|string; assumptions:Record<string,unknown>; status:string; version:number };
export type KlmFinanceAllocation = { id:string; workspaceId:string; financeEntryId:string; costCenterId:string; percentage:number; amount?:number|null; version:number; auditMetadata:Record<string,unknown>; clientId?:string|null; contractId?:string|null; matterId?:string|null; source:string };
export type KlmFinanceAllocationClosure = {
  financeEntryId:string;
  allocatedPercentage:number|string;
  unallocatedPercentage:number|string;
  allocatedAmount:number|string;
  unallocatedAmount:number|string;
  closed:boolean;
};
export type KlmFinanceLookupType = 'entry' | 'client' | 'matter' | 'contract' | 'team' | 'user';
export type KlmFinanceLookupItem = { id:string; label:string; clientId?:string|null; matterId?:string|null; inactive?:boolean; unavailable?:boolean };
export type KlmFinanceLookupPage = { items:KlmFinanceLookupItem[]; entityType:KlmFinanceLookupType; search:string; limit:number; nextCursor:string|null };
export type KlmExecutiveMetric = { value:number|string|null; quality:string; source:string; definition:string; unit:string };
export type KlmFinanceExecutiveAnalytics = { generatedAt:string; periodStart:string; periodEnd:string; arr:KlmExecutiveMetric; contractedRevenue:KlmExecutiveMetric; grossMargin:KlmExecutiveMetric; contributionMargin:KlmExecutiveMetric; ebitda:KlmExecutiveMetric; burn:KlmExecutiveMetric; runway:KlmExecutiveMetric; breakEven:KlmExecutiveMetric; concentration:KlmExecutiveMetric; dimensionalProfitability:KlmExecutiveMetric; cac:KlmExecutiveMetric; ltv:KlmExecutiveMetric; payback:KlmExecutiveMetric };
export type KlmFinanceTaxProfile = { id:string; workspaceId:string; regime:string; taxRate:number|string; effectiveFrom:string; municipality?:string|null; sources:Array<Record<string,unknown>>; reformCalendar:Record<string,unknown>; settings:Record<string,unknown>; version:number };
export type KlmFinanceTaxRules = { schemaVersion:number; verifiedAt:string; asOf:string; reviewRequired:boolean; timeline:Array<{year:number;phase:string;reviewDate?:string;summary:string;sourceUrl:string}>; rules:Array<{key:string;status:string;summary:string;sourceUrl:string}>; alerts:Array<{severity:string;code:string;message:string}>; reminderSchedule?:Array<{scheduleKey:string;milestoneKey:string;milestoneDate:string;triggerDate:string;offsetDays:number;label:string;state:'due'|'scheduled'|string;deliveryState:'called'|'blocked'|'not_called'|string;phoneDeliveryStatus:'pending'|'delivered'|'failed'|'not_configured'|'not_called'|string;profileConfigured:boolean;summary:string;sourceUrl:string;humanDecisionId?:string|null}>; mandatoryHumanGate:string; disclaimer:string };
export type KlmFinanceForecast = { id:string; workspaceId:string; periodStart:string; periodEnd:string; revenue:number|string; expense:number|string; assumptions:Record<string,unknown>; scenario:'conservative'|'base'|'optimistic'|string; horizon:string; version:number };
export type KlmFinanceValuation = { id:string; workspaceId:string; method:'dcf_simplified'|'revenue_multiple'|'ebitda_multiple'|string; valuationDate:string; enterpriseValue:number|string; assumptions:Record<string,unknown>; premiseQuality:'verified'|'partial'|'unverified'|string; version:number };
export type KlmFinanceAnalyticsGroup = { dimension:'cost_center'|'client'|'contract'|'matter'|'entry'; id:string|null; label:string; amount:number|string; evidenceAvailable?:boolean };
export type KlmFinanceAnalyticsItem = { allocationId:string; entryId:string; entryLabel:string; costCenterId:string; costCenterLabel:string; clientId?:string|null; clientLabel:string; contractId?:string|null; contractLabel:string; matterId?:string|null; matterLabel:string; percentage:number|string; amount:number|string; source:string; evidenceAvailable?:boolean; evidenceRef?:string|null };
export type KlmFinanceAnalyticsResponse = { workspaceId:string; filters:Record<string,string|null>; total:number|string; groupDimension:KlmFinanceAnalyticsGroup['dimension']; groups:KlmFinanceAnalyticsGroup[]; items:(KlmFinanceAnalyticsItem & { evidenceAvailable?:boolean })[]; totalCount:number; limit:number; offset:number; hasMore:boolean; coverage?:{allocatedAmount:number|string;unallocatedAmount:number|string;reconciled:boolean;consolidatesDescendants?:boolean;costCenterScopeCount?:number}; generatedAt:string };
export type KlmFinanceDimensionalDre = { mode:'cash'|'accrual'; periodStart:string; periodEnd:string; lines:Array<{key:string;label:string;amount:number|string}>; result:number|string; budget:{revenue:number|string;expenses:number|string;result:number|string;variance:number|string}|null; budgetQuality:'configured'|'not_configured'|'not_comparable'|string; source:string; generatedAt:string; granularity?:'monthly'|'annual'; accumulated?:boolean; series?:Array<{periodStart:string;periodEnd:string;result:number|string;accumulatedResult:number|string}>; comparison?:{periodStart:string;periodEnd:string;currentResult:number|string;previousResult:number|string;variance:number|string;variancePercent:number|string|null} };
export type KlmFinanceTaxReformSimulation = { regime:string; currentRate:number|string; currentEstimatedTax:number|string; transition:Array<{year:number;rate:number|string;estimatedTax:number|string;taxDelta:number|string;estimatedMargin:number|string;estimatedCashImpact:number|string}>; quality:string; humanReviewRequired:boolean; ownerAlerts:string[]; sources:Array<Record<string,unknown>>; generatedAt:string };
export type KlmFinanceTaxReviewRequest = { decisionId:string; status:string; assignedUserId:string; ownerUserId:string; dueAt:string; routingPath:string[]; ownerNotified:boolean; phoneDeliveryStatus:'pending'|'delivered'|'failed'|'not_configured' };

/** Régua de cobrança (dunning, F4) — ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4. */
export type KlmFinanceDunningStepKey = 'd1_lembrete' | 'd7_segunda_via' | 'd15_formal';

export type KlmFinanceDunningStepConfig = {
  enabled: boolean;
  offsetDays: number;
  requiresApproval: boolean;
  messageTemplate: string;
};

export type KlmFinanceDunningSettings = {
  steps: Record<KlmFinanceDunningStepKey, KlmFinanceDunningStepConfig>;
};

export type KlmFinanceDunningStepConfigUpdate = Partial<KlmFinanceDunningStepConfig>;

export type KlmFinanceDunningLogStatus = 'sent' | 'local_draft' | 'pending_approval' | 'blocked_gate_closed';

export type KlmFinanceDunningLog = {
  id: string;
  workspaceId: string;
  chargeId: string;
  step: KlmFinanceDunningStepKey;
  status: KlmFinanceDunningLogStatus;
  reasonCode: string | null;
  messagePreview: string | null;
  viewedBeforeDispatch: boolean;
  requiresApproval: boolean;
  approvedByUserId: string | null;
  approvedAt: string | null;
  sentAt: string | null;
  ownerNotifiedAt: string | null;
  createdAt: string;
  updatedAt: string;
  entryTitle: string | null;
  amount: number | null;
  dueDate: string | null;
};

/** Despesas completas (F8) — ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §C.3/F8. */
export type KlmFinanceExpenseApprovalStatus = 'not_required' | 'pending' | 'approved' | 'rejected';

export type KlmFinanceExpense = {
  id: string;
  workspaceId: string;
  matterId: string | null;
  title: string;
  description: string | null;
  entryType: 'receivable' | 'expense' | 'refund';
  status: 'pending' | 'scheduled' | 'paid' | 'overdue' | 'cancelled';
  provider: string | null;
  externalRef: string | null;
  amount: number;
  currency: string;
  dueDate: string | null;
  paidAt: string | null;
  reminderDaysBefore: number;
  notifyClient: boolean;
  tags: string[];
  entryMetadata: Record<string, unknown>;
  category: string | null;
  costCenter: string | null;
  reimbursable: boolean;
  reimbursementEntryId: string | null;
  receiptDocumentRef: string | null;
  recurrenceId: string | null;
  approvalStatus: KlmFinanceExpenseApprovalStatus;
  approvedBy: string | null;
  approvedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

/** Honorários jurídicos (F9) — ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 (F9). */
export type KlmFeeArrangementKind =
  | 'fixo'
  | 'entrada_exito'
  | 'mensalidade'
  | 'por_hora'
  | 'exito'
  | 'quota_litis'
  | 'hibrido';

export type KlmFeeArrangementStatus = 'ativo' | 'encerrado';

export type KlmFeeArrangement = {
  id: string;
  workspaceId: string;
  matterId: string;
  kind: KlmFeeArrangementKind;
  fixedValue: number | null;
  percent: number | null;
  baseCalculoExito: string | null;
  hourlyRate: number | null;
  status: KlmFeeArrangementStatus;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmFeeTimeEntry = {
  id: string;
  workspaceId: string;
  arrangementId: string;
  matterId: string;
  userId: string;
  date: string;
  minutes: number;
  description: string | null;
  hourlyRate: number;
  billed: boolean;
  invoiceEntryId: string | null;
  createdAt: string;
  updatedAt: string;
};

/** Resumo leve de FinanceEntry — mesmo shape usado pelos endpoints de honorários e contrato→cobrança. */
export type KlmFeeFinanceEntrySummary = {
  id: string;
  title: string;
  amount: number;
  dueDate: string | null;
  status: string;
  externalRef: string | null;
};

export type KlmFeeBillingType = 'BOLETO' | 'CREDIT_CARD' | 'PIX' | 'UNDEFINED';

export type KlmFeeRecurrence = {
  id: string;
  workspaceId: string;
  clientId: string;
  matterId: string | null;
  entryTitle: string;
  entryDescription: string | null;
  entryValue: number;
  entryType: string;
  kind: string;
  cycle: string;
  billingType: string | null;
  nextRun: string;
  endDate: string | null;
  maxCount: number | null;
  generatedCount: number;
  status: string;
  gatewaySubscriptionId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmGenerateFeeChargeResult =
  | { kind: 'fixo'; entry: KlmFeeFinanceEntrySummary; charge: KlmFinanceCharge }
  | { kind: 'entrada_exito'; installmentRef: string; entries: KlmFeeFinanceEntrySummary[] }
  | { kind: 'mensalidade'; recurrence: KlmFeeRecurrence };

export type KlmRenegotiateFeeResult = {
  installmentRef: string;
  entries: KlmFeeFinanceEntrySummary[];
};

export type KlmFeeSuccessEventResult = {
  approvalId: string;
  status: string;
};

export type KlmApproveFeeSuccessResult = {
  entry: KlmFeeFinanceEntrySummary;
  charge: KlmFinanceCharge;
};

/** Contrato → nós financeiros com aprovação (F11) — ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F11. */
export type KlmContractBillingItemKind = 'fixo' | 'entrada' | 'parcela' | 'mensalidade' | 'exito' | 'por_hora';

export type KlmContractBillingItem = {
  kind: KlmContractBillingItemKind;
  valor: number | null;
  percentual: number | null;
  vencimento: string | null;
  ciclo: string | null;
  descricao: string | null;
  confidence: number | null;
  needsReview: boolean;
};

export type KlmContractBillingProposalStatus = 'draft' | 'pending_approval' | 'approved' | 'rejected';

export type KlmContractBillingProposal = {
  id: string;
  workspaceId: string;
  matterId: string;
  sourceDocumentName: string | null;
  items: KlmContractBillingItem[];
  status: KlmContractBillingProposalStatus;
  extractionModel: string | null;
  approvalId: string | null;
  decidedAt: string | null;
  decidedBy: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmContractBillingSubmitResult = { approvalId: string; status: string };

export type KlmContractBillingApproveResult = {
  fixedEntries: Array<{ entry: KlmFeeFinanceEntrySummary; charge: KlmFinanceCharge }>;
  installments: { installmentRef: string; entries: KlmFeeFinanceEntrySummary[] } | null;
  recurrences: KlmFeeRecurrence[];
  arrangements: Array<{ id: string; kind: KlmFeeArrangementKind }>;
};

/** Webhook de leads de sites (F12 — Central de Conexões). */
export type KlmLeadWebhookToken = {
  token: string;
  webhookUrl: string;
};

export type KlmIntegrationStatusEntry = {
  connected: boolean;
  label: string;
  lastSyncAt: string | null;
};

export type KlmIntegrationsStatus = Record<string, KlmIntegrationStatusEntry>;

export type KlmLeadWebhookTestResult = {
  status: 'accepted' | 'rejected';
  reason: string;
};

/** Indicações com comissão (F13) — ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F13. */
export type KlmReferralPartnerType = 'contato' | 'cliente' | 'parceiro' | 'advogado';
export type KlmReferralCommissionType = 'percentual' | 'fixo';

export type KlmReferralPartner = {
  id: string;
  workspaceId: string;
  nome: string;
  tipo: KlmReferralPartnerType;
  contatoRef: string | null;
  comissaoTipo: KlmReferralCommissionType;
  comissaoValor: number;
  vigenciaInicio: string;
  vigenciaFim: string | null;
  ativo: boolean;
  notas: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmMatterReferral = {
  id: string;
  workspaceId: string;
  matterId: string;
  partnerId: string;
  registradoPor: string | null;
  data: string;
  createdAt: string;
};

export type KlmReferralCommission = {
  id: string;
  workspaceId: string;
  matterId: string;
  partnerId: string;
  chargeId: string;
  expenseEntryId: string;
  valorBase: number;
  comissaoTipo: KlmReferralCommissionType;
  comissaoValorCalculado: number;
  createdAt: string;
};

export type KlmBankTransactionSource = 'ofx' | 'csv' | 'asaas';
export type KlmReconciliationTargetKind = 'charge' | 'entry' | 'expense';
export type KlmReconciliationMethod = 'deterministico' | 'ia' | 'manual';
export type KlmReconciliationStatus = 'sugerido' | 'confirmado' | 'rejeitado';

export type KlmBankTransaction = {
  id: string;
  workspaceId: string;
  source: KlmBankTransactionSource;
  externalFingerprint: string;
  date: string;
  value: number;
  description: string | null;
  createdAt: string;
};

export type KlmReconciliationMatch = {
  id: string;
  workspaceId: string;
  bankTransactionId: string;
  targetKind: KlmReconciliationTargetKind | null;
  targetId: string | null;
  method: KlmReconciliationMethod;
  confidence: number | null;
  suggestedCategory: string | null;
  suggestedTags: string[];
  status: KlmReconciliationStatus;
  confirmedBy: string | null;
  confirmedAt: string | null;
  createdAt: string;
};

export type KlmPendingReconciliation = {
  transaction: KlmBankTransaction;
  match: KlmReconciliationMatch;
};

export type KlmReconciliationIngestResult = {
  imported: number;
  duplicates: number;
  matchedDeterministic: number;
  suggested: number;
};

// ── F14 — Harmonização de Documentos Financeiros × Lançamentos Manuais ──

export type KlmFinanceHarmonizationSourceType = 'pdf' | 'xml' | 'csv';
export type KlmFinanceHarmonizationStatus = 'draft' | 'aguardando_validacao' | 'validado' | 'rejeitado';
export type KlmFinanceHarmonizationMatchKind = 'exato' | 'aproximado' | 'so_no_documento' | 'so_no_sistema';
export type KlmFinanceHarmonizationMatchedSource = 'finance_entry' | 'bank_transaction';
export type KlmFinanceHarmonizationDecision = 'pendente' | 'confirmado' | 'rejeitado' | 'ajustar';

export type KlmFinanceHarmonizationItem = {
  id: string;
  runId: string;
  docDate: string | null;
  docValue: number | null;
  docDescription: string | null;
  matchedSource: KlmFinanceHarmonizationMatchedSource | null;
  matchedEntryId: string | null;
  matchKind: KlmFinanceHarmonizationMatchKind;
  deltaValue: number | null;
  deltaDays: number | null;
  decision: KlmFinanceHarmonizationDecision;
  decidedBy: string | null;
  decidedAt: string | null;
  createdAt: string;
};

export type KlmFinanceHarmonizationRun = {
  id: string;
  workspaceId: string;
  sourceDocumentName: string;
  sourceType: KlmFinanceHarmonizationSourceType;
  periodStart: string;
  periodEnd: string;
  competence: string;
  status: KlmFinanceHarmonizationStatus;
  totals: { batem?: number; naoBatem?: number; soNoDocumento?: number; soNoSistema?: number };
  parserWarning: string | null;
  createdBy: string | null;
  decidedBy: string | null;
  decidedAt: string | null;
  createdAt: string;
};

export type KlmFinanceHarmonizationRunDetail = KlmFinanceHarmonizationRun & {
  items: KlmFinanceHarmonizationItem[];
};

export type KlmFinanceHarmonizationQuota = {
  competence: string;
  used: number;
  limit: number;
  remaining: number;
  renewsAt: string;
};

// ── F3 — Recorrência nativa (gestão direta: listar/ver/pausar/retomar/encerrar) ──

export type KlmFinanceRecurrenceKind = 'assinatura_gateway' | 'geracao_programada';
export type KlmFinanceRecurrenceCycle =
  | 'semanal'
  | 'quinzenal'
  | 'mensal'
  | 'bimestral'
  | 'trimestral'
  | 'semestral'
  | 'anual';
export type KlmFinanceRecurrenceStatus = 'ativa' | 'pausada' | 'encerrada';

export type KlmFinanceRecurrence = {
  id: string;
  workspaceId: string;
  clientId: string;
  matterId: string | null;
  entryTitle: string;
  entryDescription: string | null;
  entryValue: number;
  entryType: string;
  kind: KlmFinanceRecurrenceKind;
  cycle: KlmFinanceRecurrenceCycle;
  billingType: string | null;
  nextRun: string;
  endDate: string | null;
  maxCount: number | null;
  generatedCount: number;
  status: KlmFinanceRecurrenceStatus;
  gatewaySubscriptionId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmFinanceInstallmentEntry = {
  id: string;
  installmentIndex: number;
  installmentCount: number;
  amount: number;
  dueDate: string | null;
  chargeId: string | null;
  chargeStatus: string | null;
};

export type KlmFinanceInstallmentSeries = {
  installmentRef: string;
  parentEntryId: string;
  entries: KlmFinanceInstallmentEntry[];
};

export type KlmApproval = {
  id: string;
  cardId: string;
  clientName: string;
  type: string;
  title: string;
  description: string | null;
  priority: 'alta' | 'media' | 'baixa';
  status: 'pending' | 'approved' | 'rejected' | 'changes_requested';
  requestedAt: string;
  resolutionReason: string | null;
};

export type KlmAuditEvent = {
  id: string;
  actorType: string;
  actorLabel: string;
  eventType: string;
  entityType: string;
  entityId: string | null;
  summary: string;
  occurredAt: string;
};

export type KlmFollowUp = {
  id: string;
  matterId: string | null;
  threadId: string | null;
  channel: KlmThread['primaryChannel'];
  status: 'scheduled' | 'due' | 'completed' | 'suppressed' | 'cancelled' | 'failed';
  dueAt: string;
  attempt: number;
  maxAttempts: number;
  reason: string | null;
};

export type KlmFollowUpUpdate = {
  dueAt?: string;
  reason?: string | null;
  status?: 'scheduled' | 'completed' | 'cancelled';
};

export type KlmKnowledgeArticleFeedbackSummary = {
  articleId: string;
  upCount: number;
  downCount: number;
  myFeedback: 'up' | 'down' | null;
};

export type KlmMedicationReference = {
  id: string;
  catalogKey: string;
  datasetVersion: string;
  productName: string;
  activeIngredient: string | null;
  laboratory: string | null;
  probableArea: string | null;
  oncology: boolean;
  injunctionType: string | null;
  therapeuticClassCmed: string | null;
  cmedPresentation: string;
  anvisaRegistration: string | null;
  ggremCode: string | null;
  pmvgWithoutTax: number;
  pmvg18: number | null;
  pfWithoutTax: number | null;
  capApplies: boolean;
  hospitalRestriction: boolean;
  commercialized2025: boolean;
  label: string | null;
  practicalLegalObservation: string | null;
  sourceUrl: string;
  sourceDatasetLabel: string;
  sourceRows: Array<Record<string, unknown>>;
  sourceRanks: Record<string, number>;
};

export type KlmMedicationCatalogStatus = {
  datasetVersion: string;
  sourceSha256: string;
  recordCount: number;
  oncologyCount: number;
  sourceFilename: string;
  cmedPublishedAt: string;
  sourceUrl: string;
  priceUnit: string;
  calculationFormula: string;
  limitations: string[];
};

export type KlmTreatmentEstimate = {
  id: string;
  workspaceId: string;
  matterId: string;
  medicationReferenceId: string;
  medication: KlmMedicationReference;
  presentationsPerCycle: number;
  cycleCount: number;
  cycleIntervalDays: number | null;
  treatmentDurationDays: number | null;
  costPerPresentation: number;
  totalTreatmentCost: number;
  calculationFormula: string;
  prescriptionReference: string | null;
  notes: string | null;
  reviewStatus: 'rascunho' | 'revisado' | 'aprovado' | 'bloqueado';
  appliedToMatterEconomicValue: boolean;
  sourceSnapshot: Record<string, unknown>;
  reviewedByUserId: string | null;
  reviewedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmLegalPricingPolicy = {
  minimumInitialFee: number;
  maximumInitialFee: number | null;
  allowSuccessOnly: boolean;
  successOnlyMoralDamagesPercentage: number;
  allowInjunctionMilestoneFee: boolean;
  oncologyObligationFeeMinimum: number;
  oncologyObligationFeeMaximum: number;
  requireTreatmentCycleForMedicationValue: boolean;
  requireHumanReview: boolean;
  currency: 'BRL';
  policyNote: string;
};

export type KlmMatterDetail = {
  id: string;
  columnId: string;
  microStatus: string;
  demandType: string;
  practiceArea?: string | null;
  legalAreaKey?: string | null;
  legalProductKey?: string | null;
  legalProductCustom?: string | null;
  legalObjectName?: string | null;
  leadSource: string | null;
  tags: string[];
  executiveSummary: string | null;
  healthSubtheme: string | null;
  healthSubthemeCustom: string | null;
  healthObjectName: string | null;
  healthInsurer: string | null;
  healthDenialReason: string | null;
  medicalUrgencyHours: number | null;
  economicValueAnnual: number | null;
  leadScore: number;
  manualContextRevision: number;
  manualContextUpdatedAt: string | null;
  nextAction: string | null;
  docScore: number;
  productionScore: number;
  claimValue: number | null;
  priority?: string;
  urgency?: string;
  classification?: string;
  strategyNotes?: string | null;
  risks?: string[];
  blocks?: string[];
  nextDeadline?: string | null;
  nextHearing?: string | null;
  isBlocked?: boolean;
  needsApproval?: boolean;
  openTasks?: number;
  unreadMessages?: number;
  createdAt?: string;
  updatedAt?: string;
  zapsignDocToken?: string | null;
  zapsignSignUrl?: string | null;
  zapsignStatus?: string | null;
  client: { id: string; name: string; email: string | null; phone: string | null; sensitiveDataHidden?: boolean };
  documents: Array<{ id: string; name: string; type: string | null; status: string; createdAt: string; size?: string | null; mimeType?: string | null }>;
  // `sourceExceptionId` vem do TaskRead do backend (app/schemas/task.py) tanto
  // aqui quanto no endpoint de tarefas do caso. Faltava só nesta declaração, e
  // como a tela usa `tasksQuery.data ?? matter.tasks`, o campo sumia da união e
  // o selo "gerado por falha" não compilava sob tsconfig.app.json.
  tasks?: Array<{ id: string; version: number; title: string; description?: string | null; status: string; priority?: string; dueDate?: string | null; sourceExceptionId?: string | null }>;
  timeline: Array<{ id: string; type: string; title: string; description: string | null; timestamp: string }>;
  conversations?: Array<{ id: string; message: string; sender?: string; timestamp?: string }>;
  knowledgeChunks: Array<{ id: string; title: string | null; summary: string | null; contentText: string; sourceType: string }>;
  fieldChanges?: Array<{ id: string; fieldName?: string; summary?: string | null; changedAt?: string }>;
  auditLogs?: Array<{ id: string; action?: string; description?: string | null; createdAt?: string }>;
  agentRuns?: Array<{ id: string; agentKey?: string; status: string; startedAt?: string; completedAt?: string | null }>;
  humanOverrides?: Array<{ id: string; action?: string; notes?: string | null; createdAt?: string }>;
  conversationInsight?: null | {
    summary?: string | null;
    noShowRisk?: string | null;
    objections?: string[];
    requestedDocuments?: string[];
    missingInformation?: string[];
    recommendedAction?: string | null;
    nextBestMessage?: string | null;
  };
  revenueProjection: null | {
    estimatedClaimValue: number | null;
    projectedRevenue: number | null;
    successProbability: number | null;
    confidenceLevel: number | null;
    notes: string | null;
    projectionPremises?: Partial<{
      fixedFee: number;
      successPercentage: number;
      economicValueAnnual: number;
      probability: number;
      economicObligation: boolean;
      paymentMode: 'initial_plus_success' | 'success_only';
      successBaseType: 'economic_obligation' | 'moral_damages';
      moralDamagesEstimate: number | null;
      injunctionMilestoneFee: number;
      obligationFee: number;
      reviewStatus: string;
    }>;
    projectionVersion?: number;
  };
  honorariumEstimate: null | { contractType: string | null; fixedFee: number | null; quotaLitisPercentage: number | null; expectedExpenses: number | null; totalEstimatedValue: number | null; notes: string | null };
  treatmentEstimates?: KlmTreatmentEstimate[];
  campaignAttribution: null | { source: string | null; medium: string | null; campaign: string | null; channel: string | null };
};

export type KlmFinancialProjection = {
  matterId: string;
  fixedFee: number;
  successPercentage: number;
  economicValueAnnual: number;
  probability: number;
  economicObligation: boolean;
  paymentMode: 'initial_plus_success' | 'success_only';
  successBaseType: 'economic_obligation' | 'moral_damages';
  moralDamagesEstimate: number | null;
  injunctionMilestoneFee: number;
  obligationFee: number;
  reviewStatus: string;
  conservative: number;
  likely: number;
  aggressive: number;
  withoutEconomicValue: number;
  projectionVersion: number;
  updatedAt: string;
};

export type KlmAgent = {
  key: string;
  version?: number;
  name: string;
  role: string;
  group: string;
  state: 'active' | 'paused' | 'review' | 'attention' | 'frozen';
  qualityScore: number;
  queueSize: number;
  description: string;
  tools: string[];
  allowedModels?: string[];
  defaultAutonomy?: 'A0' | 'A1' | 'A2' | 'A3' | 'A4';
  gate: string;
  permissions: {
    readMatter: boolean;
    writeCrm: boolean;
    createDraft: boolean;
    sendExternal: boolean;
    requestHuman: boolean;
  };
  modelPolicy: {
    strategy: string;
    provider: string;
    model: string;
    taskClass: string;
    modelApiEnabled: boolean;
    localModelEnabled: boolean;
    simulatorAvailable: boolean;
    channelDeliveryEnabled: boolean;
    externalEnabled: boolean;
  };
  areaKey?: string | null;
  productKey?: string | null;
  parentAgentKey?: string | null;
  areaEnabled?: boolean | null;
};

// hil_total: aprova cada resposta externa; hil_marcos: aprova só marcos
// (proposta, contrato, desconto); autonomo: conduz até o aceite do
// contrato sozinho, dentro da banda pré-aprovada abaixo.
export type KlmAgentAutonomyMode = 'hil_total' | 'hil_marcos' | 'autonomo';

export type KlmAgentAutonomySetting = {
  agentKey: string;
  autonomyMode: KlmAgentAutonomyMode;
  negotiationFloorPct: number | null;
  negotiationBandPct: number | null;
  etag?: string | null;
};

export type KlmAgentRegistry = {
  policyVersion: string;
  governance?: {
    version?: string;
    policyDigestAlgorithm?: 'sha256';
    policyDigest?: string;
    coordinationAgentKey?: string;
    ownerBridgeAgentKey?: string;
    ownerBridgeDelegatorKey?: string;
    maxDelegationDepth?: number;
    manualOverrideWins?: boolean;
    crossAreaAccessDisabled?: boolean;
    crossProductAccessDisabled?: boolean;
    crossWorkspaceAccessDenied?: boolean;
    crossClientAccessDenied?: boolean;
    leastPrivilegeTools?: boolean;
    externalActionsAllowed?: boolean;
    delegationRules?: {
      directParentOnly?: boolean;
      ownerBridgeOnlyFrom?: string;
      directOwnerBridgeInvocationDisabled?: boolean;
      independentControlSystemOnly?: boolean;
      depthValidated?: boolean;
    };
    hermesOnlyHumanBridge?: boolean;
    frozenAreaSafeAgents?: string[];
    hardHumanGates?: string[];
    independentControlAgents?: string[];
    parentByAgentKey?: Record<string, string | null>;
  };
  runtime: string;
  agents: KlmAgent[];
  specializedAgents?: KlmAgent[];
  metrics: {
    modeled: number;
    active: number;
    paused: number;
    qualityAverage: number;
    runs: number;
    waitingHuman: number;
    externalActionsExecuted: number;
    modelStrategy: string;
    modelApisEnabled: boolean;
    localModelsEnabled: boolean;
    executionMode: string;
    modelTokens: number;
    specializedModeled?: number;
    specializedEnabled?: number;
    coordinationAgent?: string;
    ownerBridgeAgent?: string;
  };
};

export type KlmAgentRun = {
  id: string;
  matterId: string;
  agentName: string;
  triggerSource: string;
  threadId: string | null;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'waiting_human' | 'cancelled';
  inputPayload: Record<string, unknown> | null;
  outputPayload: Record<string, unknown> | null;
  errorMessage: string | null;
  attemptCount: number;
  modelUsed: string | null;
  aiProvider: string | null;
  inputTokens: number;
  outputTokens: number;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmAgentExecution = {
  runs: KlmAgentRun[];
  messageIds: string[];
  approvalId: string | null;
  deduplicated: boolean;
  externalActionsExecuted: number;
  runtime: string;
  policyVersion: string;
};

// ── Aquisição Paga integrada (Sprint F5) ────────────────────────────────────
// Nenhum tipo aqui representa uma chamada real à Marketing API: toda criação
// de campanha é um rascunho (ExternalActionIntent) aguardando aprovação
// humana em /klm/external-intents/{id}/approve-local (fluxo já existente).
export type KlmPaidAcquisitionChannel = 'ctwa' | 'ctm' | 'ctd';
export type KlmPaidAcquisitionObjective =
  | 'lead_generation'
  | 'conversations'
  | 'traffic'
  | 'conversions';

export type KlmPaidAcquisitionSyncStatus = {
  gateOpen: boolean;
  connected: boolean;
  provider: string;
  lastSyncedAt: string | null;
  syncedCampaigns: number;
  syncedReceipts: number;
  lastReceiptSource: string | null;
  state: 'not_synced' | 'synced_demo' | 'synced_manual' | 'synced_official';
  message: string;
};

export type KlmCampaignDraft = {
  id: string;
  workspaceId: string;
  channel: KlmPaidAcquisitionChannel;
  channelLabel: string;
  status: 'draft' | 'awaiting_approval' | 'approved_local' | 'blocked_external' | 'cancelled';
  provider: string | null;
  marketingApiPayload: Record<string, unknown>;
  reason: string;
  requestedByUserId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmPrepareCampaignDraftInput = {
  idempotencyKey: string;
  channel: KlmPaidAcquisitionChannel;
  objective?: KlmPaidAcquisitionObjective;
  campaignName: string;
  dailyBudget: number | string;
  currency?: string;
  creative: {
    headline: string;
    primaryText: string;
    imageRef?: string | null;
    callToAction?: string;
  };
  welcomeMessage: string;
  iceBreakers?: string[];
  connectionId?: string | null;
};

export type KlmPaidAcquisitionFunnelRow = {
  campaignKey: string;
  campaignExternalId: string | null;
  campaignName: string;
  provider: string | null;
  channelHint: KlmPaidAcquisitionChannel | null;
  spend: number;
  spendCurrency: string;
  spendOrigin: 'oficial' | 'manual' | 'demonstracao' | 'sem_dado';
  conversations: number;
  qualifiedLeads: number;
  contracts: number;
  revenue: number;
  cpl: number | null;
  cac: number | null;
  roas: number | null;
};

export type KlmPaidAcquisitionFunnelSummary = {
  dateFrom: string;
  dateTo: string;
  rows: KlmPaidAcquisitionFunnelRow[];
  totals: KlmPaidAcquisitionFunnelRow;
};

// ── Tracking de conversão paga — Google Data Manager ──────────────────────
// Contrato real: app/api/v1/endpoints/integrations.py
// (/integrations/tracking/google-data-manager*). O Meta CAPI usa o par
// getAdsMeasurementConfig/saveAdsMeasurementConfig já existente mais abaixo
// (mesma IntegrationConnection que o despachante de conversões lê) — não
// duplicar aqui.
// Campos de segredo (developerToken/accessToken) só escrevem: a leitura
// nunca devolve o valor salvo, apenas hasDeveloperToken/hasAccessToken.
export type KlmGoogleDataManagerTrackingStatus = {
  configured: boolean;
  customerId: string | null;
  hasDeveloperToken: boolean;
  hasAccessToken: boolean;
};

export type KlmGoogleDataManagerTrackingUpsertInput = {
  customerId: string;
  developerToken?: string;
  accessToken?: string;
};

// ── Monitoramento processual (add-on) — /api/v1/processos ────────────────
// Contrato real: app/api/v1/endpoints/process_monitoring.py + schemas em
// app/schemas/process_monitoring.py. Módulo contratável à parte: quando não
// contratado, o backend recusa TODA a superfície (inclusive leitura) com
// 403 `{ code: 'PROC-MON-001' }` — nunca 200 vazio.
export type KlmMonitoredProcessStatus = 'ativo' | 'pausado' | 'erro';
export type KlmProcessPublicationSource = 'djen' | 'datajud';
export type KlmDeadlineCountingType = 'dias_uteis' | 'dias_corridos';
export type KlmDeadlineStatus = 'prazo_sugerido' | 'confirmado' | 'dispensado';

export type KlmMonitoredProcess = {
  id: string;
  workspaceId: string;
  matterId: string | null;
  numeroCnj: string;
  tribunal: string | null;
  status: KlmMonitoredProcessStatus;
  oabVinculada: string | null;
  responsibleUserId: string | null;
  createdByUserId: string;
  lastMovementAt: string | null;
  lastError: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmMonitoredProcessCreateInput = {
  numeroCnj: string;
  /** Aceito por compatibilidade; o backend deriva o tribunal do número CNJ e ignora texto livre. */
  tribunal?: string | null;
  matterId?: string | null;
  oabVinculada?: string | null;
  responsibleUserId?: string | null;
};

/** `PATCH /processos/{id}`: campo ausente não mexe, `null` explícito limpa.
 * Sem `tribunal`: o worker o deriva do número a cada tick e reverteria qualquer override. */
export type KlmMonitoredProcessUpdateInput = {
  oabVinculada?: string | null;
  matterId?: string | null;
  responsibleUserId?: string | null;
};

/** Um passo da memória de cálculo do prazo (`app/services/prazo_calculator.py`) — shape livre (dict), sempre exibido como veio do backend. */
export type KlmDeadlineCalculationStep = Record<string, unknown>;

export type KlmProcessPublication = {
  id: string;
  workspaceId: string;
  monitoredProcessId: string | null;
  fonte: KlmProcessPublicationSource;
  externalId: string;
  numeroCnj: string;
  conteudo: string;
  publishedAt: string;
  tipo: string | null;
  urgencia: string | null;
  dias: number | null;
  tipoContagem: KlmDeadlineCountingType | null;
  dataFatal: string | null;
  memoriaCalculo: KlmDeadlineCalculationStep[] | null;
  prazoStatus: KlmDeadlineStatus | null;
  confirmedByUserId: string | null;
  confirmedAt: string | null;
  createdAt: string;
};

// ── Controladoria jurídica — painel + distribuição (Onda 4, Sprint B UI) ──
// Contrato real: app/api/v1/endpoints/legal_controladoria.py + schemas em
// app/schemas/legal_controladoria.py (CamelCaseSchema converte
// snake_case → camelCase). Painel some as 11 categorias do dia; regras de
// distribuição alimentam o resumo "distribuição" da tela fundida
// Publicações + Controladoria (ver `PrazosPage`).
export type KlmControladoriaDeadlineWindowCounts = {
  d7: number;
  d3: number;
  d1: number;
  d0: number;
  atrasados: number;
};

export type KlmControladoriaPainel = {
  generatedAt: string;
  publicacoesNaoTratadas: number;
  prazosSugeridos: number;
  prazosFatais: number;
  prazosPorJanela: KlmControladoriaDeadlineWindowCounts;
  tarefasSemResponsavel: number;
  processosSemMovimentacao: number;
  audienciasProximas: number;
  documentosPendentes: number;
  minutasBloqueadas: number;
  excecoesDeAgentes: number;
  clientesSemAtualizacao: number;
};

export type KlmDistributionStrategy = 'round_robin' | 'least_load' | 'seniority' | 'fixed';

export type KlmDistributionRule = {
  id: string;
  workspaceId: string;
  name: string;
  description: string | null;
  active: boolean;
  priority: number;
  matchNatures: string[];
  matchAreas: string[];
  matchClientIds: string[];
  matchRisks: string[];
  matchUnits: string[];
  matchTeams: string[];
  strategy: KlmDistributionStrategy;
  candidateUserIds: string[];
  maxActivePerCandidate: number | null;
  respectAvailability: boolean;
  createdByUserId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmDistributionRuleListResult = { items: KlmDistributionRule[]; total: number };

// ── Operação Massificada / Lotes (Onda 8, Sprint B UI — "lotes por
// cliente") ───────────────────────────────────────────────────────────
// Contrato real: app/api/v1/endpoints/legal_batches.py + schemas em
// app/schemas/legal_batch.py (CamelCaseSchema converte snake_case →
// camelCase). REGRA DURA: `approveAllLegalBatchItems` NUNCA aprova
// tese/prazo_fatal/texto_final/protocolo — o serviço sempre devolve esses
// itens em `skippedProtectedItemIds`; só `approveLegalBatchItem` (item a
// item) move um item com uma dessas naturezas para `concluido`. O painel
// do lote (`KlmLegalBatchPanel`) é sempre a contagem real dos itens —
// nunca um contador incrementado à parte.
export type KlmLegalBatchCriterion =
  | 'cliente'
  | 'carteira'
  | 'tribunal'
  | 'materia'
  | 'tipo_publicacao'
  | 'fase'
  | 'tese'
  | 'responsavel'
  | 'prazo'
  | 'playbook';

export type KlmLegalBatchAction =
  | 'classificar'
  | 'agrupar'
  | 'vincular'
  | 'sugerir_tarefa'
  | 'preencher_variavel'
  | 'gerar_rascunho'
  | 'revisar'
  | 'pedir_documento'
  | 'organizar';

export type KlmLegalBatchDecisionKind =
  | 'classificacao'
  | 'agrupamento'
  | 'vinculo'
  | 'tarefa_sugerida'
  | 'variavel_preenchida'
  | 'rascunho'
  | 'revisao'
  | 'documento_solicitado'
  | 'organizacao'
  // ── decisões críticas — NUNCA aprováveis em lote ──
  | 'tese'
  | 'prazo_fatal'
  | 'texto_final'
  | 'protocolo';

/** As quatro naturezas de decisão que `approve-all` sempre pula — espelha
 * `app.models.legal_batch.PROTECTED_DECISION_KINDS` (mantido em código,
 * não só documentado, para o painel poder explicar o motivo do skip sem
 * adivinhar). */
export const KLM_LEGAL_BATCH_PROTECTED_DECISION_KINDS: readonly KlmLegalBatchDecisionKind[] = [
  'tese',
  'prazo_fatal',
  'texto_final',
  'protocolo',
];

export type KlmLegalBatch = {
  id: string;
  workspaceId: string;
  name: string;
  externalRef: string | null;
  criterionType: KlmLegalBatchCriterion;
  criterionValue: Record<string, unknown>;
  playbookRef: string | null;
  defaultAction: KlmLegalBatchAction | null;
  status: 'aberto' | 'processando' | 'com_pendencias' | 'concluido';
  createdByUserId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmLegalBatchItemState =
  | 'pendente'
  | 'classificado'
  | 'vinculado'
  | 'rascunho'
  | 'revisado'
  | 'bloqueado'
  | 'baixa_confianca'
  | 'divergente'
  | 'aguardando_humano'
  | 'concluido';

export type KlmLegalBatchItem = {
  id: string;
  workspaceId: string;
  batchId: string;
  itemRef: string | null;
  legalDemandId: string | null;
  action: KlmLegalBatchAction;
  decisionKind: KlmLegalBatchDecisionKind;
  state: KlmLegalBatchItemState;
  stateHistory: Array<Record<string, unknown>>;
  inputPayload: Record<string, unknown>;
  resultPayload: Record<string, unknown>;
  confidence: number | null;
  errorReason: string | null;
  autonomyLevelApplied: string | null;
  autonomyPolicyId: string | null;
  requiresHumanApproval: boolean;
  approvedByUserId: string | null;
  approvedAt: string | null;
  estimatedCost: number | null;
  estimatedTimeSavedMinutes: number | null;
  responsibleUserId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmLegalBatchPanel = {
  batchId: string;
  status: string;
  totalItems: number;
  pendente: number;
  classificado: number;
  vinculado: number;
  rascunho: number;
  revisado: number;
  bloqueado: number;
  baixaConfianca: number;
  divergente: number;
  aguardandoHumano: number;
  concluido: number;
  estimatedCostTotal: number;
  estimatedTimeSavedMinutesTotal: number;
};

export type KlmLegalBatchProcessResult = {
  batchId: string;
  processedItemIds: string[];
  succeededItemIds: string[];
  failedItemIds: string[];
  panel: KlmLegalBatchPanel;
};

export type KlmLegalBatchApproveAllResult = {
  batchId: string;
  approvedItemIds: string[];
  skippedProtectedItemIds: string[];
  skippedNotPendingItemIds: string[];
};

/** Recorte por contexto — cria o lote a partir de TODAS as demandas de um
 * cliente, ou de UMA demanda específica. Nunca os dois, nunca nenhum (ver
 * `LegalBatchCreateFromContext.validate_single_context` no backend, que
 * rejeita com 422 se violado — o botão "Processar em lote" da Demanda
 * sempre manda `legalDemandId`; o do Cliente sempre manda `clientId`). */
export type KlmLegalBatchCreateFromContextInput = {
  name: string;
  externalRef?: string | null;
  clientId?: string;
  legalDemandId?: string;
  playbookRef?: string | null;
  action?: KlmLegalBatchAction;
  decisionKind?: KlmLegalBatchDecisionKind;
};

// ── Demandas jurídicas — Cadeia Jurídica (Onda 2/5, Sprint A UI) ─────────
// Contrato real: app/api/v1/endpoints/legal_demands.py +
// app/api/v1/endpoints/legal_case_strategy.py. Schemas em
// app/schemas/legal_demand.py, app/schemas/case_party.py,
// app/schemas/case_strategy.py, app/schemas/case_360.py (CamelCaseSchema
// converte snake_case → camelCase no JSON automaticamente).
export type KlmLegalDemandNature =
  | 'judicial'
  | 'administrativo'
  | 'consultivo'
  | 'contrato'
  | 'acordo'
  | 'servico_recorrente'
  | 'massificado';

export type KlmLegalDemandRisk = 'baixo' | 'medio' | 'alto' | 'critico';

export type KlmLegalDemandPhase = 'triagem' | 'em_andamento' | 'suspenso' | 'concluido' | 'encerrado';

export type KlmLegalDemandParty = {
  name: string;
  role?: string | null;
  docNumber?: string | null;
};

export type KlmLegalDemand = {
  id: string;
  workspaceId: string;
  externalRef: string | null;
  nature: KlmLegalDemandNature;
  area: string | null;
  subarea: string | null;
  clientId: string;
  parties: KlmLegalDemandParty[];
  unit: string | null;
  team: string | null;
  responsibleUserId: string | null;
  strategy: string | null;
  risk: KlmLegalDemandRisk;
  value: number | null;
  phase: KlmLegalDemandPhase;
  sla: Record<string, unknown>;
  playbookRef: string | null;
  documentsRequired: string[];
  matterId: string | null;
  contractRef: string | null;
  financeRef: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmLegalDemandListResult = { items: KlmLegalDemand[]; total: number };

export type KlmLegalDemandCreateInput = {
  externalRef?: string | null;
  nature: KlmLegalDemandNature;
  area?: string | null;
  subarea?: string | null;
  clientId: string;
  parties?: KlmLegalDemandParty[];
  unit?: string | null;
  team?: string | null;
  responsibleUserId?: string | null;
  strategy?: string | null;
  risk?: KlmLegalDemandRisk;
  value?: number | null;
  sla?: Record<string, unknown>;
  playbookRef?: string | null;
  documentsRequired?: string[];
  matterId?: string | null;
  contractRef?: string | null;
  financeRef?: string | null;
};

export type KlmLegalDemandUpdateInput = Partial<Omit<KlmLegalDemandCreateInput, 'nature' | 'clientId'>>;

export type KlmLegalDemandPhaseTransitionInput = { phase: KlmLegalDemandPhase; reason?: string | null };

// ── Partes do caso além do cliente titular (Onda 5) ──
export type KlmCasePartyPole = 'ativo' | 'passivo' | 'terceiro_interessado';

export type KlmCaseParty = {
  id: string;
  workspaceId: string;
  legalDemandId: string;
  name: string;
  pole: KlmCasePartyPole;
  role: string | null;
  qualification: Record<string, unknown>;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmCasePartyListResult = { items: KlmCaseParty[] };

export type KlmCasePartyCreateInput = {
  name: string;
  pole: KlmCasePartyPole;
  role?: string | null;
  qualification?: Record<string, unknown>;
  notes?: string | null;
};

// ── Matriz estratégia fato–prova–pedido, com lacunas (Onda 5) ──
export type KlmProofForce = 'plena' | 'robusta' | 'mediana' | 'indiciaria' | 'fragil';
export type KlmProofStatus = 'tem' | 'falta' | 'solicitada';

export type KlmCaseFact = {
  id: string;
  workspaceId: string;
  legalDemandId: string;
  description: string;
  occurredAt: string | null;
  isControverted: boolean;
  origin: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmCaseFactCreateInput = {
  description: string;
  occurredAt?: string | null;
  isControverted?: boolean;
  origin?: string | null;
};

export type KlmCaseProofLink = {
  id: string;
  workspaceId: string;
  legalDemandId: string;
  factId: string;
  documentId: string | null;
  force: KlmProofForce;
  status: KlmProofStatus;
  notes: string | null;
  requestedAt: string | null;
  obtainedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmCaseProofLinkCreateInput = {
  documentId?: string | null;
  force: KlmProofForce;
  status?: KlmProofStatus;
  notes?: string | null;
};

export type KlmCasePleading = {
  id: string;
  workspaceId: string;
  legalDemandId: string;
  requestText: string;
  legalBasis: string | null;
  supportingFactIds: string[];
  createdAt: string;
  updatedAt: string;
};

export type KlmCasePleadingCreateInput = {
  requestText: string;
  legalBasis?: string | null;
  supportingFactIds?: string[];
};

export type KlmCaseStrategyGap = {
  kind: string;
  factId: string | null;
  pleadingId: string | null;
  description: string;
  exceptionId: string | null;
};

export type KlmCaseStrategyMatrix = {
  legalDemandId: string;
  facts: KlmCaseFact[];
  proofLinks: KlmCaseProofLink[];
  pleadings: KlmCasePleading[];
  gaps: KlmCaseStrategyGap[];
};

// ── Caso 360 — visão agregada da demanda (Onda 5, Plano Mestre §5) ──
export type KlmCase360Oportunidade = {
  matterId: string;
  caseNumber: string | null;
  columnId: string;
  microStatus: string;
  priority: string;
  urgency: string;
  classification: string;
  leadSource: string | null;
  claimValue: number | null;
  starsRating: number;
  cardOrder: number;
};

export type KlmCase360Cliente = {
  id: string;
  name: string;
  cpf: string | null;
  email: string | null;
  phone: string | null;
  employer: string | null;
  position: string | null;
  sensitiveDataHidden: boolean;
  maskedLabel: string | null;
};

export type KlmCase360Responsavel = { id: string; name: string; email: string | null };

export type KlmCase360Publicacao = {
  id: string;
  publishedAt: string;
  tipo: string | null;
  urgencia: string | null;
  prazoStatus: string | null;
  dataFatal: string | null;
  journeyState: string;
};

export type KlmCase360Processo = {
  id: string;
  numeroCnj: string;
  tribunal: string | null;
  status: string;
  lastMovementAt: string | null;
  publicacoes: KlmCase360Publicacao[];
};

export type KlmCase360Prazos = {
  legalDemandId: string;
  prazoLegalAt: string | null;
  prazoFatalAt: string | null;
  prazoInternoAt: string | null;
  slaClienteAt: string | null;
  prazoPlaybookAt: string | null;
  margemSegurancaDias: number | null;
  slaEffectiveAt: string | null;
  slaEffectiveLayer: string | null;
  slaEscalationLevel: number | null;
};

export type KlmCase360TrabalhoDistribuido = {
  responsibleUser: KlmCase360Responsavel | null;
  lastDecision: Record<string, unknown> | null;
  openEscalations: Array<Record<string, unknown>>;
};

export type KlmCase360FinanceTotals = {
  totalReceivable: number;
  totalPaid: number;
  totalPending: number;
  totalOverdue: number;
};

export type KlmCase360Financeiro = {
  entries: KlmFinanceEntry[];
  totals: KlmCase360FinanceTotals;
  feeArrangements: KlmFeeArrangement[];
};

export type KlmCase360Conversa = {
  id: string;
  matterId: string;
  agentId: string | null;
  message: string;
  isInternal: boolean;
  isRead: boolean;
  timestamp: string;
};

export type KlmCase360Comunicacoes = {
  totalCount: number;
  recent: KlmCase360Conversa[];
};

export type KlmCase360Excecao = {
  id: string;
  matterId: string | null;
  exceptionKind: string;
  origin: string;
  title: string;
  description: string | null;
  priority: string;
  status: string;
  createdAt: string;
};

export type KlmCase360TimelineEvent = {
  id: string;
  matterId: string;
  type: string;
  title: string;
  description: string | null;
  agentId: string | null;
  timestamp: string;
  metadata: Record<string, unknown> | null;
};

export type KlmCase360 = {
  generatedAt: string;
  demandId: string;
  workspaceId: string;
  demandaContratada: KlmLegalDemand;
  oportunidade: KlmCase360Oportunidade | null;
  cliente: KlmCase360Cliente;
  responsavel: KlmCase360Responsavel | null;
  partes: KlmCaseParty[];
  processos: KlmCase360Processo[];
  prazos: KlmCase360Prazos;
  trabalhoDistribuido: KlmCase360TrabalhoDistribuido;
  documentos: KlmDocument[];
  financeiro: KlmCase360Financeiro;
  comunicacoes: KlmCase360Comunicacoes;
  estrategia: string | null;
  provas: KlmCaseStrategyMatrix;
  excecoesAbertas: KlmCase360Excecao[];
  timeline: KlmCase360TimelineEvent[];
};

// ── Editor jurídico — versões/checklist/citações/revisão de uma demanda
// (Onda 6, app/api/v1/endpoints/legal_editor.py + legal_demands.py, Sprint
// A UI, aba "Peças"). Tipo próprio (não reaproveita `KlmLegalArtifactVersion`,
// que é o contrato mais antigo e mais enxuto de `/klm/legal-artifacts`) porque
// esta rota devolve campos extras específicos da demanda: `legalDemandId`,
// `authorType`, `officeTemplateKey` e `protocolabilityState`.
export type KlmLegalArtifactAuthorType = 'human' | 'agent';
export type KlmLegalArtifactProtocolabilityState = 'bloqueado' | 'pendente' | 'protocolavel';

export type KlmLegalArtifactEditorSection = { id: string; title: string; body: string };

export type KlmLegalArtifactEditorVersion = {
  id: string;
  workspaceId: string;
  matterId: string;
  legalDemandId: string | null;
  artifactKey: string;
  version: number;
  kind: KlmLegalArtifactKind;
  title: string;
  status: 'draft' | 'review_pending' | 'approved' | 'rejected' | 'changes_requested' | 'archived';
  content: { sections?: KlmLegalArtifactEditorSection[] } & Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  createdByUserId: string;
  authorType: KlmLegalArtifactAuthorType;
  authorAgentKey: string | null;
  officeTemplateKey: string | null;
  officeTemplateObjectId: string | null;
  officeTemplateObjectVersion: number | null;
  protocolabilityState: KlmLegalArtifactProtocolabilityState;
  approvalItemId: string | null;
  createdAt: string;
  updatedAt: string;
  sensitiveDataHidden?: boolean;
};

/** Só os 4 tipos com modelo timbrado gerável pelo editor — `client_update` é
 * mensagem, não documento (`LegalEditorUnsupportedKindError` no backend). */
export const LEGAL_ARTIFACT_GENERABLE_KINDS = [
  'petition',
  'contract',
  'power_of_attorney',
  'proposal',
] as const satisfies readonly KlmLegalArtifactKind[];

export type KlmLegalArtifactGenerateInput = {
  legalDemandId: string;
  kind: KlmLegalArtifactKind;
  title: string;
  officeTemplateKey?: string | null;
  artifactKey?: string | null;
  expectedVersion?: number | null;
  authorType?: KlmLegalArtifactAuthorType;
  authorAgentKey?: string | null;
};

export type KlmLegalArtifactSectionGenerateInput = { sectionId: string; expectedVersion: number };

export type KlmLegalArtifactSubmitResult = { artifact: KlmLegalArtifactEditorVersion; approvalId: string };

export type KlmLegalArtifactChecklistItem = { item: string; detail: string };

export type KlmLegalArtifactChecklist = {
  hasPlaceholder: boolean;
  missingVariables: string[];
  hasProcuracao: boolean;
  hasCustasPagas: boolean | null;
  pendingItems: KlmLegalArtifactChecklistItem[];
  protocolabilityState: KlmLegalArtifactProtocolabilityState;
};

export type KlmCitationVerificationStatus = 'verificada' | 'nao_verificada' | 'nao_localizada';
export type KlmCitationKind = 'sumula' | 'tema' | 'resp' | 'lei';

export type KlmCitationVerification = {
  id: string;
  workspaceId: string;
  legalDemandId: string;
  artifactVersionId: string;
  citationText: string;
  citationKind: KlmCitationKind | string;
  status: KlmCitationVerificationStatus;
  sourceReference: string | null;
  trail: Record<string, unknown>;
  checkedByUserId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmCitationVerificationRun = {
  artifactVersionId: string;
  allVerified: boolean;
  gateOpen: boolean;
  verifications: KlmCitationVerification[];
};

export type KlmLegalReviewSeverity = 'critica' | 'alta' | 'media' | 'baixa';

export type KlmLegalReviewFinding = {
  id: string;
  workspaceId: string;
  legalDemandId: string;
  artifactVersionId: string;
  severity: KlmLegalReviewSeverity;
  kind: string;
  description: string;
  reference: Record<string, unknown>;
  createdByUserId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmLegalReviewRun = {
  artifactVersionId: string;
  isProtocolable: boolean;
  journeyAdvanced: boolean;
  findings: KlmLegalReviewFinding[];
};

// ── Pasta de protocolo (Onda 7, app/api/v1/endpoints/protocol_folder.py) ──
// A plataforma PREPARA e VALIDA a pasta de protocolo; ela NUNCA protocola.
// Não existe (nem aqui, nem no backend) nenhum verbo "protocolar" — só
// `build` (monta/revalida) e `receipt` (registra recibo POSTERIOR e
// humano de um protocolo que já aconteceu fora da plataforma).
export type KlmProtocolFolderState = 'bloqueado' | 'pendente' | 'protocolavel';

export type KlmProtocolFolderPendingItem = { item: string; detail: string };

export type KlmProtocolFolderFile = {
  order: number;
  folder: string;
  filename: string;
  sourceType: 'artifact' | 'document';
  sourceId: string;
  sha256: string;
  sizeBytes: number;
  extension: string;
};

export type KlmProtocolFolderManifest = {
  processo: { numeroCnj: string | null; presente: boolean };
  tribunal: string;
  tipoAto: string;
  responsavel: { userId: string; nome: string } | null;
  versaoLiberada: { artifactVersionId: string; version: number; status: string };
  arquivos: KlmProtocolFolderFile[];
  tamanhoTotalBytes: number;
  assinaturaConfirmada: boolean;
  procuracao: { dispensada: boolean; presente: boolean };
  custas: { exigida: boolean; pagaNoFinanceiro: boolean | null };
  citacoes: { total: number; naoVerificadas: string[] };
  achadosCriticosAbertos: number;
  placeholders: { presente: boolean };
  pendencias: KlmProtocolFolderPendingItem[];
  geradoEm: string;
  estado: KlmProtocolFolderState;
};

export type KlmProtocolFolder = {
  id: string;
  legalDemandId: string;
  matterId: string;
  artifactVersionId: string;
  tribunal: string;
  actType: string;
  state: KlmProtocolFolderState;
  manifest: KlmProtocolFolderManifest;
  files: KlmProtocolFolderFile[];
  signatureConfirmed: boolean;
  builtByUserId: string | null;
  builtAt: string | null;
  receiptRegisteredAt: string | null;
  receiptRegisteredByUserId: string | null;
  receiptReference: string | null;
  receiptNote: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmProtocolFolderBuildInput = {
  artifactId: string;
  tribunal?: string | null;
  signatureConfirmed?: boolean;
};

export type KlmProtocolReceiptRegisterInput = {
  reference: string;
  note?: string | null;
  protocoledAt?: string | null;
};

// ── DMS jurídico — documentos do caso (Onda 7, Sprint A aba Documentos).
// Consome app/api/v1/endpoints/legal_documents.py — NUNCA confundir com
// `KlmDocument`/`KlmDocumentUpload` acima, que é o sistema mais simples já
// exibido no Caso 360. Este é o DMS rico: dedup por hash, classificação e
// OCR honestos (fail-closed), quarentena de antivírus e compartilhamento
// com expiração + trilha de download.
export type KlmLegalDocumentSource = 'upload_manual' | 'email' | 'whatsapp' | 'sync_mirror';

export type KlmLegalDocumentType =
  | 'peticao_inicial'
  | 'procuracao'
  | 'contrato'
  | 'comprovante'
  | 'sentenca'
  | 'recurso'
  | 'correspondencia'
  | 'outro'
  | 'nao_classificado';

export type KlmLegalDocumentClassificationMethod = 'ia' | 'manual' | 'indisponivel';

export type KlmLegalDocumentOcrStatus = 'nao_aplicavel' | 'ocr_pendente' | 'concluido' | 'indisponivel';

export type KlmLegalDocumentStorageState = 'pending' | 'quarantined' | 'available';

export type KlmLegalDocumentAvStatus =
  | 'pending'
  | 'simulated_unverified'
  | 'simulated_blocked'
  | 'clean'
  | 'infected'
  | 'error';

export type KlmLegalDocumentStorage = {
  state: KlmLegalDocumentStorageState;
  avStatus: KlmLegalDocumentAvStatus;
  quarantineReason: string | null;
  byteSize: number | null;
  version: number;
};

/** Campos comuns a `DocumentAssetRead` (resposta de upload) e ao detalhe. */
export type KlmLegalDocumentAssetSummary = {
  id: string;
  storageObjectId: string;
  contentSha256: string;
  originalFilename: string;
  matterId: string | null;
  clientId: string | null;
  documentType: KlmLegalDocumentType;
  classificationMethod: KlmLegalDocumentClassificationMethod;
  classificationConfidence: number | null;
  ocrStatus: KlmLegalDocumentOcrStatus;
  createdAt: string;
};

/** `DocumentAssetDetailRead` — o que a listagem e o detalhe retornam. */
export type KlmLegalDocumentAsset = KlmLegalDocumentAssetSummary & {
  storage: KlmLegalDocumentStorage | null;
};

export type KlmLegalDocumentReference = {
  id: string;
  documentAssetId: string;
  source: KlmLegalDocumentSource;
  originalFilename: string;
  externalRef: string | null;
  matterId: string | null;
  createdAt: string;
};

export type KlmLegalDocumentIngestResult = {
  asset: KlmLegalDocumentAssetSummary;
  reference: KlmLegalDocumentReference;
  createdNewAsset: boolean;
  deduplicated: boolean;
};

export type KlmLegalDocumentShareLinkCreateInput = {
  expiresAt: string;
  watermarkEnabled?: boolean;
  maxDownloads?: number | null;
};

/** Resposta de criação — única vez em que o `token` bruto é exposto. */
export type KlmLegalDocumentShareLinkIssued = {
  id: string;
  documentAssetId: string;
  token: string;
  expiresAt: string;
  watermarkEnabled: boolean;
  maxDownloads: number | null;
};

export type KlmLegalDocumentShareLink = {
  id: string;
  documentAssetId: string;
  expiresAt: string;
  watermarkEnabled: boolean;
  maxDownloads: number | null;
  downloadCount: number;
  revokedAt: string | null;
  createdAt: string;
};

export type KlmLegalDocumentShareDownload = {
  id: string;
  shareLinkId: string;
  downloadedAt: string;
  downloadedByUserId: string | null;
  downloaderIp: string | null;
};

// Importador Universal de Migração (Onda 3, Sprint C UI) —
// app/api/v1/endpoints/migration.py + app/schemas/migration.py.
export type KlmMigrationSourceSystem =
  | 'universal_csv'
  | 'universal_xlsx'
  | 'zip_docs'
  | 'json'
  | 'astrea'
  | 'advbox'
  | 'datajuri';

export type KlmMigrationJobStatus =
  | 'recebido'
  | 'staging'
  | 'validado'
  | 'previa'
  | 'importando'
  | 'reconciliando'
  | 'concluido'
  | 'revertido'
  | 'erro';

export type KlmMigrationDomain =
  | 'clientes'
  | 'contatos'
  | 'partes'
  | 'casos'
  | 'processos'
  | 'tarefas'
  | 'prazos'
  | 'documentos'
  | 'contratos'
  | 'honorarios'
  | 'despesas'
  | 'recebimentos'
  | 'tags';

export type KlmMigrationConflict = 'none' | 'duplicate' | 'ambiguous';

export type KlmMigrationRecordStatus = 'pendente' | 'valido' | 'invalido' | 'importado' | 'mesclado' | 'rejeitado';

export type KlmMigrationDivergenceSeverity = 'critica' | 'atencao';

export type KlmMigrationCutoverDecision = {
  decision: string;
  decidedBy?: string;
  notes?: string | null;
  decidedAt?: string;
};

export type KlmMigrationJob = {
  id: string;
  workspaceId: string;
  sourceSystem: KlmMigrationSourceSystem;
  status: KlmMigrationJobStatus;
  sourceManifest: Record<string, unknown>;
  fieldMapping: Record<string, unknown>;
  previewSummary: Record<string, unknown>;
  reconciliationSummary: Record<string, unknown>;
  semaphore: 'vermelho' | 'amarelo' | 'verde' | null;
  semaphoreComputedAt: string | null;
  cutoverDecision: KlmMigrationCutoverDecision | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
  rolledBackAt: string | null;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmMigrationJobList = { items: KlmMigrationJob[]; total: number };

export type KlmMigrationImportResult = {
  processed: number;
  remaining: number;
  status: KlmMigrationJobStatus;
};

export type KlmMigrationSemaphore = {
  semaphore: 'vermelho' | 'amarelo' | 'verde' | null;
  semaphoreComputedAt: string | null;
  reconciliationSummary: Record<string, unknown>;
  cutoverDecision: KlmMigrationCutoverDecision | null;
};

export type KlmMigrationStagingRecord = {
  id: string;
  jobId: string;
  domain: KlmMigrationDomain;
  raw: Record<string, unknown>;
  normalized: Record<string, unknown>;
  dedupeKey: string;
  weakIdentity: boolean;
  conflict: KlmMigrationConflict;
  recordStatus: KlmMigrationRecordStatus;
  validationError: string | null;
  targetId: string | null;
  targetTable: string | null;
  importedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type KlmMigrationDivergence = {
  id: string;
  jobId: string;
  domain: KlmMigrationDomain | null;
  tipo: string;
  severity: KlmMigrationDivergenceSeverity;
  detalhe: string;
  context: Record<string, unknown> | null;
  resolvido: boolean;
  resolvedBy: string | null;
  resolvedAt: string | null;
  resolutionNote: string | null;
  createdAt: string;
};

// ── SDR Jurídico Trabalhista — /api/v1/sdr/* ────────────────────────────────
// Contrato real: app/schemas/sdr.py (SDRStep, SDRHandoffTarget,
// SDRQualification, SDRStatusResponse, SDRReprocessResponse). Os schemas não
// declaram alias camelCase, portanto o JSON chega em snake_case.

export type KlmSdrStep =
  | 'inicio'
  | 'coletando_nome'
  | 'coletando_demanda'
  | 'coletando_empregador'
  | 'coletando_tempo_empresa'
  | 'coletando_situacao'
  | 'coletando_documentos'
  | 'qualificando'
  | 'qualificado'
  | 'handoff_closer'
  | 'handoff_assistente'
  | 'encerrado';

export type KlmSdrHandoffTarget = 'closer_juridico' | 'assistente_juridico' | 'nenhum';

export type KlmSdrQualification = {
  client_name: string | null;
  phone: string | null;
  demand_type: string | null;
  employer: string | null;
  time_at_company: string | null;
  employment_situation: string | null;
  has_ctps: boolean | null;
  has_documents: boolean | null;
  documents_requested: string[];
  documents_received: string[];
  qualification_score: number | null;
  classification: string | null;
  estimated_claim_value: number | null;
  viability_notes: string | null;
  handoff_target: KlmSdrHandoffTarget;
  handoff_notes: string | null;
  current_step: KlmSdrStep;
  completed: boolean;
  conversation_summary: string | null;
};

export type KlmSdrStatus = {
  matter_id: string;
  sdr_step: KlmSdrStep;
  sdr_completed: boolean;
  qualification: KlmSdrQualification | null;
};

export type KlmSdrReprocessResponse = {
  matter_id: string;
  status: string;
  message: string;
};

export const klmApi = {
  // ── Importador Universal de Migração — /api/v1/migration/* (Onda 3,
  // Sprint C UI). Contrato real: app/api/v1/endpoints/migration.py +
  // app/schemas/migration.py. Toda ação mutante exige capability
  // admin/manage; leitura exige admin/view.
  createMigrationJob: (params: {
    file: File;
    sourceSystem?: KlmMigrationSourceSystem;
    defaultDomain?: string;
    fieldMapping?: Record<string, unknown>;
  }) => {
    const formData = new FormData();
    formData.append('file', params.file);
    if (params.sourceSystem) formData.append('source_system', params.sourceSystem);
    if (params.defaultDomain) formData.append('default_domain', params.defaultDomain);
    if (params.fieldMapping) formData.append('field_mapping', JSON.stringify(params.fieldMapping));
    return apiUpload<KlmMigrationJob>('/api/v1/migration/jobs', formData);
  },
  listMigrationJobs: (params: { skip?: number; limit?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.skip != null) search.set('skip', String(params.skip));
    if (params.limit != null) search.set('limit', String(params.limit));
    const qs = search.toString();
    return apiFetch<KlmMigrationJobList>(`/api/v1/migration/jobs${qs ? `?${qs}` : ''}`);
  },
  getMigrationJob: (jobId: string) =>
    apiFetch<KlmMigrationJob>(`/api/v1/migration/jobs/${encodeURIComponent(jobId)}`),
  buildMigrationPreview: (jobId: string) =>
    apiFetch<KlmMigrationJob>(`/api/v1/migration/jobs/${encodeURIComponent(jobId)}/previa`, { method: 'POST' }),
  importMigrationBatch: (jobId: string, batchSize = 50) =>
    apiFetch<KlmMigrationImportResult>(`/api/v1/migration/jobs/${encodeURIComponent(jobId)}/importar`, {
      method: 'POST',
      body: JSON.stringify({ batchSize }),
    }),
  reconcileMigrationJob: (jobId: string) =>
    apiFetch<KlmMigrationJob>(`/api/v1/migration/jobs/${encodeURIComponent(jobId)}/reconciliacao`, { method: 'POST' }),
  getMigrationSemaphore: (jobId: string) =>
    apiFetch<KlmMigrationSemaphore>(`/api/v1/migration/jobs/${encodeURIComponent(jobId)}/semaforo`),
  decideMigrationCutover: (jobId: string, decision: 'prosseguir' | 'nao_prosseguir', notes?: string | null) =>
    apiFetch<KlmMigrationJob>(`/api/v1/migration/jobs/${encodeURIComponent(jobId)}/decisao-corte`, {
      method: 'POST',
      body: JSON.stringify({ decision, notes: notes ?? null }),
    }),
  rollbackMigrationJob: (jobId: string) =>
    apiFetch<KlmMigrationJob>(`/api/v1/migration/jobs/${encodeURIComponent(jobId)}/rollback`, { method: 'POST' }),
  listMigrationStagingRecords: (
    jobId: string,
    params: { domain?: KlmMigrationDomain; conflict?: KlmMigrationConflict; skip?: number; limit?: number } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.domain) search.set('domain', params.domain);
    if (params.conflict) search.set('conflict', params.conflict);
    if (params.skip != null) search.set('skip', String(params.skip));
    if (params.limit != null) search.set('limit', String(params.limit));
    const qs = search.toString();
    return apiFetch<KlmMigrationStagingRecord[]>(
      `/api/v1/migration/jobs/${encodeURIComponent(jobId)}/staging${qs ? `?${qs}` : ''}`,
    );
  },
  reprocessMigrationStagingRecord: (jobId: string, recordId: string, normalizedPatch: Record<string, unknown> = {}) =>
    apiFetch<KlmMigrationStagingRecord>(
      `/api/v1/migration/jobs/${encodeURIComponent(jobId)}/staging/${encodeURIComponent(recordId)}/reprocessar`,
      { method: 'PATCH', body: JSON.stringify({ normalizedPatch }) },
    ),
  listMigrationDivergences: (jobId: string, resolved?: boolean) => {
    const search = new URLSearchParams();
    if (resolved != null) search.set('resolved', String(resolved));
    const qs = search.toString();
    return apiFetch<KlmMigrationDivergence[]>(
      `/api/v1/migration/jobs/${encodeURIComponent(jobId)}/divergencias${qs ? `?${qs}` : ''}`,
    );
  },
  resolveMigrationDivergence: (jobId: string, divergenceId: string, resolutionNote?: string | null) =>
    apiFetch<KlmMigrationDivergence>(
      `/api/v1/migration/jobs/${encodeURIComponent(jobId)}/divergencias/${encodeURIComponent(divergenceId)}/resolver`,
      { method: 'POST', body: JSON.stringify({ resolutionNote: resolutionNote ?? null }) },
    ),
  executiveProfiles: () => apiFetch<ExecutiveProfile[]>('/api/v1/executives/profiles'),
  executiveThreads: (status: 'active' | 'archived' = 'active') =>
    apiFetch<ExecutiveThread[]>(`/api/v1/executives/threads?status=${status}`),
  executiveThread: (threadId: string) =>
    apiFetch<ExecutiveThread>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}`),
  createExecutiveThread: (payload: {
    title?: string;
    mode: 'direct' | 'roundtable';
    selectedExecutives: ExecutiveKey[];
  }) => apiFetch<ExecutiveThread>('/api/v1/executives/threads', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateExecutiveThread: (threadId: string, payload: { title?: string; visibility?: 'private' | 'shared' }) =>
    apiFetch<ExecutiveThread>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  archiveExecutiveThread: (threadId: string) =>
    apiFetch<ExecutiveThread>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}/archive`, { method: 'POST' }),
  executiveThreadShares: (threadId: string) =>
    apiFetch<ExecutiveThreadShare[]>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}/shares`),
  shareExecutiveThread: (threadId: string, userId: string) =>
    apiFetch<ExecutiveThreadShare>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}/shares`, {
      method: 'POST', body: JSON.stringify({ userId, permission: 'view_and_reply' }),
    }),
  revokeExecutiveThreadShare: (threadId: string, userId: string) =>
    apiFetch<void>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}/shares/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  executiveMessages: (threadId: string) =>
    apiFetch<ExecutiveMessage[]>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}/messages`),
  executiveSourceReceipts: (threadId: string) =>
    apiFetch<ExecutiveSourceReceipt[]>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}/sources`),
  sendExecutiveMessage: (threadId: string, payload: {
    body: string;
    requestedExecutives?: ExecutiveKey[];
    preventAdditionalInvites?: boolean;
    context?: Array<{ type: string; id: string }>;
  }) => apiFetch<ExecutiveRound>(`/api/v1/executives/threads/${encodeURIComponent(threadId)}/messages`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  cancelExecutiveRound: (roundId: string) =>
    apiFetch<ExecutiveRound>(`/api/v1/executives/rounds/${encodeURIComponent(roundId)}/cancel`, { method: 'POST' }),
  retryExecutiveRound: (roundId: string) =>
    apiFetch<ExecutiveRound>(`/api/v1/executives/rounds/${encodeURIComponent(roundId)}/retry`, { method: 'POST' }),
  executiveReports: () => apiFetch<ExecutiveReport[]>('/api/v1/executives/reports'),
  runtime: () => apiFetch<KlmRuntimeStatus>('/api/v1/klm/runtime'),
  runLocalDiagnostics: () => apiFetch<KlmLocalDiagnostics>('/api/v1/klm/diagnostics', { method: 'POST' }),
  capabilities: () => apiFetch<KlmNativeCapabilityRegistry>('/api/v1/capabilities'),
  functionUnits: (query: KlmFunctionUnitQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value === undefined || value === '') return;
      params.set(key, String(value));
    });
    const queryString = params.toString();
    const suffix = queryString ? `?${queryString}` : '';
    return apiFetch<KlmFunctionUnitList>(`/api/v1/capabilities/functions${suffix}`);
  },
  functionUnit: (unitId: string) =>
    apiFetch<KlmFunctionUnit>(`/api/v1/capabilities/functions/${encodeURIComponent(unitId)}`),
  functionUnitRuntime: (unitId: string) =>
    apiFetch<KlmFunctionUnitRuntime>(
      `/api/v1/capabilities/functions/${encodeURIComponent(unitId)}/runtime`,
    ),
  executeFunctionUnit: (
    unitId: string,
    payload: {
      mode: 'dry-run' | 'execute' | 'configure';
      idempotencyKey: string;
      resourceId?: string | null;
      payload?: Record<string, unknown>;
    },
  ) => apiFetch<KlmFunctionUnitActionResult>(
    `/api/v1/capabilities/functions/${encodeURIComponent(unitId)}/actions`,
    { method: 'POST', body: JSON.stringify(payload) },
  ),
  nativeInboxThreadContext: (threadId: string) =>
    apiFetch<KlmNativeInboxRoutingContext>(`/api/v1/communications/threads/${threadId}`),
  reconcileNativeInboxThread: (threadId: string) =>
    apiFetch<KlmNativeInboxThreadSync>(`/api/v1/communications/threads/${threadId}/reconcile`, {
      method: 'POST',
    }),
  updateNativeInboxRouting: (threadId: string, payload: KlmNativeInboxRoutingUpdate) =>
    apiFetch<KlmNativeInboxRoutingContext>(`/api/v1/communications/threads/${threadId}/routing`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  nativeCollaborationThreadContext: (threadId: string) =>
    apiFetch<KlmNativeCollaborationThreadContext>(`/api/v1/collaboration/threads/${threadId}`),
  reconcileNativeCollaborationThread: (threadId: string) =>
    apiFetch<KlmNativeCollaborationThreadSync>(`/api/v1/collaboration/threads/${threadId}/reconcile`, {
      method: 'POST',
    }),
  nativeAgentContext: (timeRange: KlmNativeAgentContext['timeRange'] = '7d') =>
    apiFetch<KlmNativeAgentContext>(`/api/v1/automation/agents?time_range=${timeRange}`),
  reconcileNativeAgents: () =>
    apiFetch<KlmNativeAgentAgentSync>('/api/v1/automation/agents/reconcile', {
      method: 'POST',
    }),
  nativeFlowContext: (flowId: string) =>
    apiFetch<KlmNativeAgentFlowContext>(`/api/v1/automation/flows/${flowId}`),
  reconcileNativeFlow: (flowId: string) =>
    apiFetch<KlmNativeAgentFlowSync>(`/api/v1/automation/flows/${flowId}/reconcile`, {
      method: 'POST',
    }),
  nativeCRMFunnelContext: () =>
    apiFetch<KlmNativeCRMFunnelContext>('/api/v1/crm/funnel'),
  nativeCRMMatterContext: (matterId: string) =>
    apiFetch<KlmNativeCRMMatterContext>(`/api/v1/crm/matters/${matterId}`),
  nativeCRMWorkContext: () =>
    apiFetch<KlmNativeCRMWorkContext>('/api/v1/crm/work'),
  nativeCRMFinanceContext: () =>
    apiFetch<KlmNativeCRMFinanceContext>('/api/v1/crm/finance'),
  dashboard: () => apiFetch<KlmDashboard>('/api/v1/klm/dashboard'),
  funnel: () => apiFetch<KlmFunnel>('/api/v1/klm/funnel'),
  moveFunnel: (matterId: string, columnId: string, command: KlmFunnelMoveCommand) =>
    apiFetch<KlmFunnelMoveResult>(
      `/api/v1/klm/funnel/${matterId}/stage`,
      {
        method: 'PATCH',
        body: JSON.stringify({
          columnId,
          position: command.position ?? 0,
          sourceColumnId: command.sourceColumnId,
          beforeId: command.beforeId,
          afterId: command.afterId,
          expectedBoardVersion: command.expectedBoardVersion,
          operationId: command.operationId ?? crypto.randomUUID(),
        }),
      },
    ),
  kpis: (
    periodDays?: number,
    healthSubtheme?: string | null,
    scope: { legalAreaKey?: string | null; legalProductKey?: string | null } = {},
  ) => {
    const params = new URLSearchParams();
    if (periodDays) params.set('period_days', String(periodDays));
    if (healthSubtheme) params.set('health_subtheme', healthSubtheme);
    if (scope.legalAreaKey) params.set('legal_area_key', scope.legalAreaKey);
    if (scope.legalProductKey) params.set('legal_product_key', scope.legalProductKey);
    const query = params.toString();
    return apiFetch<KlmKpis>(`/api/v1/klm/kpis${query ? `?${query}` : ''}`);
  },
  advancedKpis: (filters: KlmAdvancedKpiFilters = {}) => {
    const params = new URLSearchParams();
    if (filters.periodDays) params.set('period_days', String(filters.periodDays));
    if (filters.legalAreaKey) params.set('legal_area_key', filters.legalAreaKey);
    if (filters.legalProductKey) params.set('legal_product_key', filters.legalProductKey);
    if (filters.channel) params.set('channel', filters.channel);
    if (filters.responsibleUserId) params.set('responsible_user_id', filters.responsibleUserId);
    if (filters.stage) params.set('stage', filters.stage);
    if (filters.category) params.set('category', filters.category);
    if (filters.query) params.set('query', filters.query);
    if (filters.onlyWithData != null) params.set('only_with_data', String(filters.onlyWithData));
    if (filters.sortBy) params.set('sort_by', filters.sortBy);
    if (filters.sortDirection) params.set('sort_direction', filters.sortDirection);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmAdvancedKpiReport>(`/api/v1/klm/kpis/advanced${query ? `?${query}` : ''}`);
  },
  quality: () => apiFetch<KlmQuality>('/api/v1/klm/quality'),
  omnichannelAudit: (filters: {
    threadId?: string;
    state?: string;
    severity?: string;
    findingType?: string;
    query?: string;
    limit?: number;
  } = {}) => {
    const params = new URLSearchParams();
    if (filters.threadId) params.set('thread_id', filters.threadId);
    if (filters.state) params.set('state', filters.state);
    if (filters.severity) params.set('severity', filters.severity);
    if (filters.findingType) params.set('finding_type', filters.findingType);
    if (filters.query) params.set('query', filters.query);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmOmnichannelAuditDashboard>(
      `/api/v1/klm/omnichannel/audit/findings${query ? `?${query}` : ''}`,
    );
  },
  runOmnichannelAudit: (payload: { threadId?: string; limit?: number } = {}) =>
    apiFetch<KlmOmnichannelAuditDashboard>('/api/v1/klm/omnichannel/audit/run', {
      method: 'POST',
      body: JSON.stringify({ threadId: payload.threadId ?? null, limit: payload.limit ?? 250 }),
    }),
  actOnOmnichannelAuditFinding: (
    findingId: string,
    payload: { action: 'resolve' | 'dismiss' | 'retry'; expectedVersion: number; reason?: string },
  ) => apiFetch<KlmOmnichannelAuditFinding>(
    `/api/v1/klm/omnichannel/audit/findings/${findingId}/actions`,
    { method: 'POST', body: JSON.stringify(payload) },
  ),
  threads: () => apiFetch<KlmThread[]>('/api/v1/klm/omnichannel/threads'),
  filteredThreads: (filters: KlmThreadFilterQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return;
      params.set(key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`), String(value));
    });
    const suffix = params.size ? `?${params.toString()}` : '';
    return apiFetch<KlmThread[]>(`/api/v1/klm/omnichannel/threads${suffix}`);
  },
  conversationResponseMetrics: (filters: KlmConversationResponseMetricsQuery = {}) => {
    const params = new URLSearchParams();
    if (filters.period) params.set('period', filters.period);
    if (filters.dateFrom) params.set('date_from', filters.dateFrom);
    if (filters.dateTo) params.set('date_to', filters.dateTo);
    if (filters.preset) params.set('preset', filters.preset);
    filters.attendantIds?.forEach((value) => params.append('attendant_id', value));
    filters.teamIds?.forEach((value) => params.append('team_id', value));
    filters.statuses?.forEach((value) => params.append('status', value));
    filters.slaStatuses?.forEach((value) => params.append('sla', value));
    if (filters.sortBy) params.set('sort_by', filters.sortBy);
    if (filters.sortDirection) params.set('sort_direction', filters.sortDirection);
    if (filters.offset !== undefined) params.set('offset', String(filters.offset));
    if (filters.limit !== undefined) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmConversationResponseMetrics>(
      `/api/v1/klm/omnichannel/response-metrics${query ? `?${query}` : ''}`,
    );
  },
  internalMessageFeed: () =>
    apiFetch<KlmInternalMessageFeedEntry[]>('/api/v1/klm/omnichannel/internal-feed'),
  messages: (threadId: string) =>
    apiFetch<KlmMessage[]>(`/api/v1/klm/omnichannel/threads/${threadId}/messages`),
  messageEvidence: (threadId: string, messageId: string) =>
    apiFetch<KlmMessageEvidenceChain>(
      `/api/v1/klm/omnichannel/threads/${threadId}/messages/${messageId}/evidence`,
    ),
  exportMessageEvidence: (threadId: string, messageId: string) =>
    apiFetch<KlmMessageEvidenceExport>(
      `/api/v1/klm/omnichannel/threads/${threadId}/messages/${messageId}/evidence/export`,
    ),
  omnichannelContext: (threadId: string) =>
    apiFetch<KlmOmnichannelContext>(`/api/v1/klm/omnichannel/threads/${threadId}/context`),
  omnichannelCopilot: (
    threadId: string,
    payload: {
      action: 'summarize' | 'suggest_reply' | 'rewrite' | 'unanswered' | 'extract_tasks' | 'meeting_brief';
      draft?: string | null;
      tone?: 'acolhedor' | 'objetivo' | 'formal' | 'simples';
      goal?: string | null;
    },
  ) => apiFetch<KlmOmnichannelCopilot>(`/api/v1/klm/omnichannel/threads/${threadId}/copilot`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  omnichannelConnections: (channel?: KlmOmnichannelChannel) =>
    apiFetch<KlmChannelConnection[]>(
      `/api/v1/klm/omnichannel/connections${channel ? `?channel=${encodeURIComponent(channel)}` : ''}`,
    ),
  metaGroupCapability: (connectionId: string) =>
    apiFetch<KlmMetaGroupCapability>(
      `/api/v1/klm/omnichannel/meta-groups/connections/${encodeURIComponent(connectionId)}/capability`,
    ),
  configureMetaGroupCapability: (
    connectionId: string,
    payload: KlmMetaGroupCapabilityConfigure,
  ) => apiFetch<KlmMetaGroupCapability>(
    `/api/v1/klm/omnichannel/meta-groups/connections/${encodeURIComponent(connectionId)}/capability`,
    {
      method: 'PUT',
      headers: { 'Idempotency-Key': `spa-meta-group-capability:${crypto.randomUUID()}` },
      body: JSON.stringify(payload),
    },
  ),
  metaGroups: (connectionId: string) =>
    apiFetch<KlmMetaGroupCollection>(
      `/api/v1/klm/omnichannel/meta-groups/groups?connection_id=${encodeURIComponent(connectionId)}&limit=100`,
    ),
  createInternalMetaGroup: (
    connectionId: string,
    payload: {
      subject: string;
      description?: string | null;
      joinApprovalMode: 'auto_approve' | 'approval_required';
    },
  ) => apiFetch<KlmMetaGroupCreateResult>(
    `/api/v1/klm/omnichannel/meta-groups/connections/${encodeURIComponent(connectionId)}/groups`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': `spa-meta-group-create:${crypto.randomUUID()}` },
      body: JSON.stringify(payload),
    },
  ),
  prepareOfficialMetaGroupCreation: (
    connectionId: string,
    payload: {
      subject: string;
      description?: string | null;
      joinApprovalMode: 'auto_approve' | 'approval_required';
    },
  ) => apiFetch<KlmMetaGroupOfficialCreationPreparation>(
    `/api/v1/klm/omnichannel/meta-groups/connections/${encodeURIComponent(connectionId)}/official-groups/prepare`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': `spa-meta-group-official-create:${crypto.randomUUID()}` },
      body: JSON.stringify(payload),
    },
  ),
  prepareMetaGroupMessage: (groupId: string, body: string) =>
    apiFetch<KlmMetaGroupMessagePreparation>(
      `/api/v1/klm/omnichannel/meta-groups/groups/${encodeURIComponent(groupId)}/messages/prepare`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': `spa-meta-group-message:${crypto.randomUUID()}` },
        body: JSON.stringify({
          message: { type: 'text', text: { body, preview_url: false } },
          requireExternalCapability: true,
        }),
      },
    ),
  metaGroupMessages: (groupId: string) =>
    apiFetch<KlmMetaGroupMessageCollection>(
      `/api/v1/klm/omnichannel/meta-groups/groups/${encodeURIComponent(groupId)}/messages`,
    ),
  rotateMetaGroupInviteLink: (groupId: string) =>
    apiFetch<KlmMetaGroupInviteLinkRotation>(
      `/api/v1/klm/omnichannel/meta-groups/groups/${encodeURIComponent(groupId)}/invite-link/rotate`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': `spa-meta-group-invite-rotate:${crypto.randomUUID()}` },
      },
    ),
  metaGroupParticipants: (groupId: string) =>
    apiFetch<KlmMetaGroupParticipantCollection>(
      `/api/v1/klm/omnichannel/meta-groups/groups/${encodeURIComponent(groupId)}/participants`,
    ),
  removeMetaGroupParticipant: (groupId: string, participantId: string) =>
    apiFetch<KlmMetaGroupParticipantRemoval>(
      `/api/v1/klm/omnichannel/meta-groups/groups/${encodeURIComponent(groupId)}/participants/${encodeURIComponent(participantId)}`,
      {
        method: 'DELETE',
        headers: { 'Idempotency-Key': `spa-meta-group-participant-remove:${crypto.randomUUID()}` },
      },
    ),
  setMetaGroupPinnedMessage: (groupId: string, text: string) =>
    apiFetch<KlmMetaGroup>(
      `/api/v1/klm/omnichannel/meta-groups/groups/${encodeURIComponent(groupId)}/pinned-message`,
      {
        method: 'PUT',
        headers: { 'Idempotency-Key': `spa-meta-group-pin:${crypto.randomUUID()}` },
        body: JSON.stringify({ text }),
      },
    ),
  clearMetaGroupPinnedMessage: (groupId: string) =>
    apiFetch<KlmMetaGroup>(
      `/api/v1/klm/omnichannel/meta-groups/groups/${encodeURIComponent(groupId)}/pinned-message`,
      {
        method: 'DELETE',
        headers: { 'Idempotency-Key': `spa-meta-group-unpin:${crypto.randomUUID()}` },
      },
    ),
  createOmnichannelConnection: (payload: {
    channel: KlmOmnichannelChannel;
    provider: string;
    connectionKey: string;
    displayName: string;
    externalAccountId?: string | null;
    transportMode: string;
    isDefault?: boolean;
    capabilities?: Record<string, unknown>;
    limits?: Record<string, unknown>;
    routingPolicy?: Record<string, unknown>;
    configuration?: Record<string, unknown>;
    credentialReference?: string | null;
  }) => apiFetch<KlmChannelConnection>('/api/v1/klm/omnichannel/connections', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateOmnichannelConnection: (
    connectionId: string,
    payload: {
      expectedVersion: number;
      displayName?: string;
      externalAccountId?: string | null;
      isDefault?: boolean;
      capabilities?: Record<string, unknown>;
      limits?: Record<string, unknown>;
      routingPolicy?: Record<string, unknown>;
      configuration?: Record<string, unknown>;
      credentialReference?: string | null;
      action?: 'save' | 'pause' | 'resume' | 'disconnect_local';
    },
  ) => apiFetch<KlmChannelConnection>(`/api/v1/klm/omnichannel/connections/${connectionId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  validateOmnichannelConnection: (connectionId: string) =>
    apiFetch<KlmChannelConnectionValidation>(
      `/api/v1/klm/omnichannel/connections/${connectionId}/validate-local`,
      { method: 'POST' },
    ),
  webhookCenter: (filters: { provider?: string; state?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (filters.provider) params.set('provider', filters.provider);
    if (filters.state) params.set('state', filters.state);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmWebhookCenter>(
      `/api/v1/klm/omnichannel/webhook-center${query ? `?${query}` : ''}`,
    );
  },
  webhookCredential: (connectionId: string) =>
    apiFetch<KlmWebhookCredentialStatus>(
      `/api/v1/klm/omnichannel/connections/${connectionId}/webhook-credential`,
    ),
  configureWebhookCredential: (
    connectionId: string,
    payload: {
      expectedVersion: number;
      action: 'save' | 'rotate' | 'disable' | 'enable';
      signingSecret?: string;
      verifyToken?: string;
      timestampToleranceSeconds?: number;
    },
  ) => apiFetch<KlmWebhookCredentialStatus>(
    `/api/v1/klm/omnichannel/connections/${connectionId}/webhook-credential`,
    { method: 'PUT', body: JSON.stringify(payload) },
  ),
  omnichannelTeams: () =>
    apiFetch<KlmWorkspaceTeam[]>('/api/v1/klm/omnichannel/teams'),
  workspaceUsers: () =>
    apiFetch<KlmWorkspaceUser[]>('/api/v1/admin/users'),
  executiveShareCandidates: () =>
    apiFetch<ExecutiveShareCandidate[]>('/api/v1/executives/share-candidates'),
  omnichannelTeamMembers: (teamId: string) =>
    apiFetch<KlmWorkspaceTeamMember[]>(`/api/v1/klm/omnichannel/teams/${teamId}/members`),
  staffAvailabilityState: (staffUserId: string, includePast = false) =>
    apiFetch<KlmStaffAvailabilityState>(
      `/api/v1/staff-availability/staff/${staffUserId}?include_past=${includePast ? 'true' : 'false'}`,
    ),
  staffEffectiveAvailability: (staffUserId: string, at?: string) => {
    const params = new URLSearchParams();
    if (at) params.set('at', at);
    const query = params.toString();
    return apiFetch<KlmStaffEffectiveAvailability>(
      `/api/v1/staff-availability/staff/${staffUserId}/effective${query ? `?${query}` : ''}`,
    );
  },
  staffAvailabilityRequests: (filters: {
    status?: KlmStaffAvailabilityChangeRequest['status'];
    targetUserId?: string;
    limit?: number;
  } = {}) => {
    const params = new URLSearchParams();
    if (filters.status) params.set('status', filters.status);
    if (filters.targetUserId) params.set('target_user_id', filters.targetUserId);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmStaffAvailabilityChangeRequest[]>(
      `/api/v1/staff-availability/requests${query ? `?${query}` : ''}`,
    );
  },
  createStaffAvailabilityRequest: (payload: {
    targetUserId?: string | null;
    proposedChange: KlmStaffAvailabilityChange;
    expectedVersion?: number | null;
    reason: string;
  }) => apiFetch<KlmStaffAvailabilityChangeRequest>('/api/v1/staff-availability/requests', {
    method: 'POST',
    headers: { 'Idempotency-Key': `spa-staff-availability:${crypto.randomUUID()}` },
    body: JSON.stringify(payload),
  }),
  decideStaffAvailabilityRequest: (
    requestId: string,
    decision: 'approve' | 'reject' | 'cancel',
    note?: string,
  ) => apiFetch<KlmStaffAvailabilityChangeRequest>(
    `/api/v1/staff-availability/requests/${requestId}/${decision}`,
    { method: 'POST', body: JSON.stringify({ note: note?.trim() || null }) },
  ),
  conversationReferences: (threadId: string, limit = 200) =>
    apiFetch<KlmConversationReference[]>(
      `/api/v1/klm/omnichannel/collaboration/threads/${threadId}/references?limit=${limit}`,
    ),
  createConversationReference: (
    threadId: string,
    payload: {
      sourceMessageId: string;
      targetMessageId: string;
      referenceType?: 'reply' | 'quote' | 'context';
      mentions?: Array<{ mentionType: 'user' | 'team'; targetId: string }>;
      idempotencyKey?: string;
    },
  ) => apiFetch<KlmConversationReference>(
    `/api/v1/klm/omnichannel/collaboration/threads/${threadId}/references`,
    {
      method: 'POST',
      body: JSON.stringify({
        ...payload,
        referenceType: payload.referenceType ?? 'reply',
        idempotencyKey: payload.idempotencyKey ?? `spa-reference:${threadId}:${crypto.randomUUID()}`,
      }),
    },
  ),
  attachConversationMentions: (
    threadId: string,
    payload: {
      sourceMessageId: string;
      mentions: Array<{ mentionType: 'user' | 'team'; targetId: string }>;
      idempotencyKey?: string;
    },
  ) => apiFetch<KlmConversationMention[]>(
    `/api/v1/klm/omnichannel/collaboration/threads/${threadId}/mentions`,
    {
      method: 'POST',
      body: JSON.stringify({
        ...payload,
        idempotencyKey: payload.idempotencyKey ?? `spa-mentions:${threadId}:${crypto.randomUUID()}`,
      }),
    },
  ),
  conversationParticipants: (threadId: string) =>
    apiFetch<KlmConversationParticipantCollection>(
      `/api/v1/klm/omnichannel/collaboration/threads/${threadId}/participants`,
    ),
  addConversationParticipant: (
    threadId: string,
    payload: {
      participantType: 'user' | 'team';
      participantId: string;
      role?: 'owner' | 'admin' | 'member' | 'observer';
    },
  ) => apiFetch<KlmConversationParticipant>(
    `/api/v1/klm/omnichannel/collaboration/threads/${threadId}/participants`,
    {
      method: 'POST',
      body: JSON.stringify({
        ...payload,
        role: payload.role ?? 'member',
        idempotencyKey: `spa-participant:${threadId}:${payload.participantType}:${payload.participantId}`,
      }),
    },
  ),
  conversationCollaborationCapabilities: (threadId: string) =>
    apiFetch<KlmConversationCapabilities>(
      `/api/v1/klm/omnichannel/collaboration/threads/${threadId}/capabilities`,
    ),
  transferOmnichannelThread: (
    threadId: string,
    payload: {
      expectedVersion: number;
      toTeamId?: string | null;
      toUserId?: string | null;
      queue?: string | null;
      reason?: string | null;
      source?: 'human' | 'agent' | 'rule' | 'system';
    },
  ) => apiFetch<KlmOmnichannelTransfer>(`/api/v1/klm/omnichannel/threads/${threadId}/transfer`, {
    method: 'POST',
    body: JSON.stringify({
      ...payload,
      idempotencyKey: `spa-transfer:${threadId}:${crypto.randomUUID()}`,
    }),
  }),
  createOmnichannelAction: (
    threadId: string,
    payload: {
      eventId?: string | null;
      connectionId?: string | null;
      actionType: 'public_reply' | 'private_reply' | 'comment_like' | 'comment_hide' | 'comment_delete' | 'send_message' | 'mark_answered';
      targetExternalId?: string | null;
      body?: string | null;
      scheduledAt?: string | null;
      payload?: Record<string, unknown>;
    },
  ) => apiFetch<KlmOmnichannelActionIntent>(`/api/v1/klm/omnichannel/threads/${threadId}/actions`, {
    method: 'POST',
    body: JSON.stringify({
      ...payload,
      idempotencyKey: `spa-action:${threadId}:${crypto.randomUUID()}`,
    }),
  }),
  omnichannelAutomationRules: () =>
    apiFetch<KlmOmnichannelAutomationRule[]>('/api/v1/klm/omnichannel/automation-rules'),
  createOmnichannelAutomationRule: (payload: {
    name: string;
    slug: string;
    description?: string | null;
    channel?: KlmOmnichannelChannel | null;
    triggerType: string;
    status?: 'draft' | 'active' | 'paused';
    priority?: number;
    coexistWithAi?: boolean;
    stopOnMatch?: boolean;
    triggerConfig?: Record<string, unknown>;
    conditions?: Array<Record<string, unknown>>;
    actions: Array<Record<string, unknown>>;
    quietHours?: Record<string, unknown>;
    rateLimits?: Record<string, unknown>;
  }) => apiFetch<KlmOmnichannelAutomationRule>('/api/v1/klm/omnichannel/automation-rules', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  omnichannelEvents: (filters: {
    channel?: KlmOmnichannelChannel;
    eventType?: string;
    visibility?: string;
    actionability?: string;
    limit?: number;
  } = {}) => {
    const params = new URLSearchParams();
    if (filters.channel) params.set('channel', filters.channel);
    if (filters.eventType) params.set('event_type', filters.eventType);
    if (filters.visibility) params.set('visibility', filters.visibility);
    if (filters.actionability) params.set('actionability', filters.actionability);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmOmnichannelChannelEventList>(`/api/v1/klm/omnichannel/events${query ? `?${query}` : ''}`);
  },
  leadCaptures: (filters: { provider?: string; state?: string; limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    if (filters.provider) params.set('provider', filters.provider);
    if (filters.state) params.set('state', filters.state);
    if (filters.limit) params.set('limit', String(filters.limit));
    if (filters.offset) params.set('offset', String(filters.offset));
    const query = params.toString();
    return apiFetch<KlmLeadCaptureInbox>(`/api/v1/klm/omnichannel/lead-captures${query ? `?${query}` : ''}`);
  },
  ingestLeadCapture: (payload: {
    provider: 'meta_ads' | 'tiktok_ads' | 'site' | 'lovable' | 'local_fixture';
    connectionKey: string;
    externalLeadId: string;
    externalFormId?: string | null;
    channel: KlmOmnichannelChannel;
    name: string;
    phone?: string | null;
    email?: string | null;
    demandType?: string;
    healthSubtheme?: string | null;
    healthObjectName?: string | null;
    healthInsurer?: string | null;
    healthDenialReason?: string | null;
    medicalUrgencyHours?: number | null;
    source?: string | null;
    campaign?: string | null;
    adSet?: string | null;
    ad?: string | null;
    providerReferences?: Record<string, string>;
    clickIds?: Record<string, string>;
    fields?: Record<string, unknown>;
    proactiveMessageTemplate?: string | null;
  }) => apiFetch<KlmLeadCaptureIngestResult>('/api/v1/klm/omnichannel/lead-captures/ingest', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  ingestRawLeadCapture: (
    connectionId: string,
    payload: { rawPayload: Record<string, unknown>; proactiveMessageTemplate?: string | null },
  ) => apiFetch<KlmLeadCaptureIngestResult>(
    `/api/v1/klm/omnichannel/connections/${connectionId}/lead-captures/ingest-raw`,
    { method: 'POST', body: JSON.stringify(payload) },
  ),
  leadHydrations: (filters: { provider?: string; state?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (filters.provider) params.set('provider', filters.provider);
    if (filters.state) params.set('state', filters.state);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmLeadHydrationJob[]>(
      `/api/v1/klm/omnichannel/lead-hydrations${query ? `?${query}` : ''}`,
    );
  },
  updateLeadHydration: (jobId: string, action: 'retry' | 'cancel') =>
    apiFetch<KlmLeadHydrationJob>(`/api/v1/klm/omnichannel/lead-hydrations/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify({ action }),
    }),
  leadHydrationEvents: (jobId: string) =>
    apiFetch<KlmLeadHydrationEvent[]>(
      `/api/v1/klm/omnichannel/lead-hydrations/${jobId}/events`,
    ),
  adsManagerEntities: (filters: {
    connectionId?: string;
    provider?: string;
    entityType?: KlmAdsManagerEntity['entityType'];
    status?: string;
    limit?: number;
  } = {}) => {
    const params = new URLSearchParams();
    if (filters.connectionId) params.set('connection_id', filters.connectionId);
    if (filters.provider) params.set('provider', filters.provider);
    if (filters.entityType) params.set('entity_type', filters.entityType);
    if (filters.status) params.set('status', filters.status);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmAdsManagerEntity[]>(
      `/api/v1/klm/omnichannel/ads-manager/entities${query ? `?${query}` : ''}`,
    );
  },
  adsManagerImports: (filters: { provider?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (filters.provider) params.set('provider', filters.provider);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmAdsManagerImport[]>(
      `/api/v1/klm/omnichannel/ads-manager/imports${query ? `?${query}` : ''}`,
    );
  },
  adsManagerSummary: (filters: { dateFrom: string; dateTo: string; provider?: string }) => {
    const params = new URLSearchParams({ date_from: filters.dateFrom, date_to: filters.dateTo });
    if (filters.provider) params.set('provider', filters.provider);
    return apiFetch<KlmAdsManagerSummary>(
      `/api/v1/klm/omnichannel/ads-manager/summary?${params.toString()}`,
    );
  },
  ingestAdsManagerSnapshot: (connectionId: string, payload: KlmAdsSnapshotPayload) =>
    apiFetch<KlmAdsSnapshotResult>(
      `/api/v1/klm/omnichannel/connections/${connectionId}/ads-manager/snapshots`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  updateOmnichannelAutomationRule: (
    ruleId: string,
    payload: Partial<Omit<KlmOmnichannelAutomationRule, 'id' | 'createdAt' | 'updatedAt' | 'version'>> & { expectedVersion: number },
  ) => apiFetch<KlmOmnichannelAutomationRule>(`/api/v1/klm/omnichannel/automation-rules/${ruleId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  scheduleOmnichannelMessage: (
    threadId: string,
    payload: {
      endpointId?: string | null;
      connectionKey: string;
      channel: KlmOmnichannelChannel;
      body?: string | null;
      templateId?: string | null;
      attachments?: Array<Record<string, unknown>>;
      scheduledAt: string;
      timezone?: string;
    },
  ) => apiFetch<KlmOmnichannelScheduledMessage>(
    `/api/v1/klm/omnichannel/threads/${threadId}/scheduled-messages`,
    {
      method: 'POST',
      body: JSON.stringify({
        ...payload,
        idempotencyKey: `spa-scheduled:${threadId}:${crypto.randomUUID()}`,
      }),
    },
  ),
  updateOmnichannelScheduledMessage: (
    scheduledMessageId: string,
    payload: {
      action: 'reschedule' | 'pause' | 'resume' | 'cancel';
      scheduledAt?: string;
      timezone?: string;
      reason?: string;
    },
  ) => apiFetch<KlmOmnichannelScheduledMessage>(
    `/api/v1/klm/omnichannel/scheduled-messages/${scheduledMessageId}`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  ),
  createOmnichannelCall: (
    threadId: string,
    payload: {
      endpointId?: string | null;
      connectionKey: string;
      channel: KlmOmnichannelChannel;
      direction: 'inbound' | 'outbound';
      permissionState?: 'unknown' | 'requested' | 'granted' | 'denied';
      metadata?: Record<string, unknown>;
    },
  ) => apiFetch<KlmOmnichannelCall>(`/api/v1/klm/omnichannel/threads/${threadId}/calls`, {
    method: 'POST',
    body: JSON.stringify({
      ...payload,
      idempotencyKey: `spa-call:${threadId}:${crypto.randomUUID()}`,
    }),
  }),
  sendMessage: (
    threadId: string,
    body: string,
    channel: KlmThread['primaryChannel'],
    authorLabel: string,
    route: KlmOmnichannelMessageRoute = {},
    metadata: Record<string, unknown> = {},
  ) =>
    apiFetch<KlmMessage>(`/api/v1/klm/omnichannel/threads/${threadId}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        endpointId: route.endpointId ?? null,
        bindingId: route.bindingId ?? null,
        channel,
        direction: 'outbound',
        authorType: 'human',
        authorLabel,
        body,
        transport: route.transport ?? 'local',
        idempotencyKey: `spa:${threadId}:${crypto.randomUUID()}`,
        messageMetadata: {
          source: 'spa_human_composer',
          connectionId: route.connectionId ?? null,
          connectionKey: route.connectionKey ?? null,
          ...metadata,
        },
      }),
    }),
  sendRichMessage: (
    threadId: string,
    command: KlmRichMessageCommand,
    channel: KlmThread['primaryChannel'],
    authorLabel: string,
    route: KlmOmnichannelMessageRoute = {},
  ) =>
    apiFetch<KlmMessage>(`/api/v1/klm/omnichannel/threads/${threadId}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        endpointId: route.endpointId ?? null,
        bindingId: route.bindingId ?? null,
        channel,
        direction: 'outbound',
        authorType: 'human',
        authorLabel,
        body: command.body,
        contentType: command.contentType,
        language: command.language ?? null,
        templateId: command.templateId ?? null,
        transport: route.transport ?? 'local',
        idempotencyKey: `spa-rich:${threadId}:${crypto.randomUUID()}`,
        messageMetadata: {
          ...command.messageMetadata,
          source: 'spa_rich_composer',
          connectionId: route.connectionId ?? null,
          connectionKey: route.connectionKey ?? null,
        },
      }),
    }),
  sendInternalMessage: (
    threadId: string,
    body: string,
    authorLabel: string,
    metadata: Record<string, unknown> = {},
  ) =>
    apiFetch<KlmMessage>(`/api/v1/klm/omnichannel/threads/${threadId}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        channel: 'internal',
        direction: 'internal',
        authorType: 'human',
        authorLabel,
        body,
        idempotencyKey: `spa-internal:${threadId}:${crypto.randomUUID()}`,
        messageMetadata: {
          visibility: 'internal_only',
          customerVisible: false,
          externalActionsExecuted: 0,
          source: 'staff_note',
          ...metadata,
        },
      }),
    }),
  updateThread: (threadId: string, payload: Record<string, unknown>) =>
    apiFetch<KlmThread>(`/api/v1/klm/omnichannel/threads/${threadId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  areas: () => apiFetch<KlmArea[]>('/api/v1/klm/areas'),
  createArea: (name: string, source?: KlmArea | null) =>
    apiFetch<KlmArea>('/api/v1/klm/areas', {
      method: 'POST',
      body: JSON.stringify({
        areaKey: name.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, ''),
        name,
        description: source ? `Estrutura adaptada de ${source.name}.` : undefined,
        status: source ? 'blueprint' : 'draft',
        version: 'v0.1.0-local',
        readiness: source ? Math.min(source.readiness, 80) : 0,
        blueprint: source?.blueprint ?? {},
      }),
    }),
  updateArea: (areaId: string, payload: { name?: string; description?: string; blueprint?: KlmAreaBlueprint }) =>
    apiFetch<KlmArea>(`/api/v1/klm/areas/${areaId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  materializeArea: (areaId: string) =>
    apiFetch<KlmArea>(`/api/v1/klm/areas/${areaId}/materialize`, { method: 'POST' }),
  validateArea: (areaId: string) =>
    apiFetch<KlmArea>(`/api/v1/klm/areas/${areaId}/validate`, { method: 'POST' }),
  activateArea: (areaId: string) =>
    apiFetch<KlmArea>(`/api/v1/klm/areas/${areaId}/activate`, { method: 'POST' }),
  setAreaActivation: (areaId: string, enabled: boolean, forceOverPlan = false) =>
    apiFetch<KlmArea>(`/api/v1/klm/areas/${areaId}/activation`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled, forceOverPlan }),
    }),
  tools: () => apiFetch<KlmTool[]>('/api/v1/klm/tools'),
  agents: () => apiFetch<KlmAgentRegistry>('/api/v1/klm/agents'),
  setAgentState: (agentKey: string, state: 'active' | 'paused', reason: string) =>
    apiFetch<KlmAgent>(`/api/v1/klm/agents/${agentKey}/state`, {
      method: 'PATCH',
      body: JSON.stringify({ state, reason }),
    }),
  setAgentTools: (agentKey: string, tools: string[], reason: string) =>
    apiFetch<KlmAgent>(`/api/v1/klm/agents/${agentKey}/tools`, {
      method: 'PATCH',
      body: JSON.stringify({ tools, reason }),
    }),
  agentAutonomySettings: () => apiFetch<KlmAgentAutonomySetting[]>('/api/v1/klm/agents/autonomy'),
  setAgentAutonomy: (
    agentKey: string,
    payload: {
      autonomyMode: KlmAgentAutonomyMode;
      negotiationFloorPct?: number | null;
      negotiationBandPct?: number | null;
      reason?: string;
      expectedEtag?: string | null;
    },
  ) =>
    apiFetch<KlmAgentAutonomySetting>(`/api/v1/klm/agents/${agentKey}/autonomy`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  agentRuns: (matterId?: string) =>
    apiFetch<KlmAgentRun[]>(`/api/v1/klm/agents/runs${matterId ? `?matter_id=${matterId}` : ''}`),
  runAgent: (payload: { agentKey: string; matterId: string; threadId?: string; requestHuman: boolean; instructions?: string }) =>
    apiFetch<KlmAgentExecution>('/api/v1/klm/agents/runs', {
      method: 'POST',
      body: JSON.stringify({ ...payload, idempotencyKey: `spa-agent:${crypto.randomUUID()}` }),
    }),
  agentPlatformDeployments: (workspaceId: string, limit = 25, offset = 0) => apiFetch<{ items: KlmAgentPlatformDeployment[]; total: number; limit: number; offset: number; hasMore: boolean }>(`/api/v1/agent-platform/deployments?workspaceId=${encodeURIComponent(workspaceId)}&limit=${limit}&offset=${offset}`),
  agentPlatformCatalog: (workspaceId?: string) => apiFetch<{ policyVersion: string | null; runtime: string | null; agents: KlmAgent[]; rolePackages: Array<{ key: string; label: string; description: string }> }>(`/api/v1/agent-platform/catalog${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`),
  createAgentDeployment: (payload: Record<string, unknown>, workspaceId?: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/deployments${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`, { method: 'POST', body: JSON.stringify(payload) }),
  validateAgentDeployment: (id: string, workspaceId?: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/deployments/${encodeURIComponent(id)}/validate${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`, { method: 'POST' }),
  publishAgentDeployment: (id: string, workspaceId?: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/deployments/${encodeURIComponent(id)}/publish${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`, { method: 'POST' }),
  pauseAgentDeployment: (id: string, workspaceId?: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/deployments/${encodeURIComponent(id)}/pause${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`, { method: 'POST' }),
  agentPlatformNodes: () => apiFetch<KlmFlowNodeCatalogEntry[]>('/api/v1/agent-platform/nodes'),
  agentHumanRoutingCatalog: (workspaceId?: string) => apiFetch<KlmHumanRoutingCatalog>(`/api/v1/agent-platform/human-routing/catalog${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`),
  agentHumanRoutingPolicies: (workspaceId?: string) => apiFetch<KlmHumanRoutingPolicyCatalog>(`/api/v1/agent-platform/human-routing/policies${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`),
  agentPlatformDecisions: (workspaceId: string, limit = 25, offset = 0) => apiFetch<{ items: KlmAgentPlatformDecision[]; total: number; limit: number; offset: number; hasMore: boolean }>(`/api/v1/agent-platform/decisions?workspaceId=${encodeURIComponent(workspaceId)}&limit=${limit}&offset=${offset}`),
  agentHumanOverview: (workspaceId?: string) => apiFetch<{ open: number; assigned: number; acknowledged: number; overdue: number; escalated: number; phone: { pending: number; delivered: number; failed: number; notConfigured: number } }>(`/api/v1/agent-platform/decisions/overview${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`),
  agentPlatformRuns: (workspaceId: string, limit = 25, offset = 0) => apiFetch<{ items: Array<{ id: string; agentName: string; deploymentId: string | null; contextType: string; contextId: string | null; triggerSource: string; status: string; attemptCount: number; inputTokens: number; outputTokens: number; costUsd: string; startedAt: string | null; completedAt: string | null; createdAt: string; updatedAt: string }>; total: number; limit: number; offset: number; hasMore: boolean }>(`/api/v1/agent-platform/runs?workspaceId=${encodeURIComponent(workspaceId)}&limit=${limit}&offset=${offset}`),
  createAgentPlatformContextRun: (payload: { contextType: 'matter' | 'client' | 'contract' | 'conversation' | 'task' | 'finance' | 'workspace' | 'platform'; contextId?: string | null; agentName: string; triggerSource: string }, workspaceId: string) => apiFetch<{ id: string; status: string; contextType: string; contextId: string | null }>(`/api/v1/agent-platform/runs/context?workspaceId=${encodeURIComponent(workspaceId)}`, { method: 'POST', body: JSON.stringify({ context_type: payload.contextType, context_id: payload.contextId ?? null, agent_name: payload.agentName, trigger_source: payload.triggerSource, dedupe_key: `spa-agent-context:${crypto.randomUUID()}` }) }),
  agentPlatformContextOptions: (contextType: 'matter' | 'client' | 'contract' | 'conversation' | 'task' | 'finance', workspaceId: string, query = '') => apiFetch<{ items: Array<{ id: string; label: string }> }>(`/api/v1/agent-platform/runs/context-options?workspaceId=${encodeURIComponent(workspaceId)}&contextType=${encodeURIComponent(contextType)}&query=${encodeURIComponent(query)}&limit=100`),
  cancelAgentRun: (id: string, workspaceId: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/runs/${encodeURIComponent(id)}/cancel?workspaceId=${encodeURIComponent(workspaceId)}`, { method: 'POST' }),
  retryAgentRun: (id: string, workspaceId: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/runs/${encodeURIComponent(id)}/retry?workspaceId=${encodeURIComponent(workspaceId)}`, { method: 'POST' }),
  provideAgentRunHumanInput: (id: string, values: Record<string, unknown>, workspaceId: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/runs/${encodeURIComponent(id)}/human-input?workspaceId=${encodeURIComponent(workspaceId)}`, { method: 'POST', body: JSON.stringify({ values }) }),
  requestAgentHumanDecision: (payload: { reason: string; requiredRoles: string[]; requiredSkills: string[]; slaMinutes: number; maxHops: number }, workspaceId?: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/decisions${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`, { method: 'POST', body: JSON.stringify({ options: { reason: payload.reason }, required_roles: payload.requiredRoles, required_skills: payload.requiredSkills, sla_minutes: payload.slaMinutes, max_hops: payload.maxHops }) }),
  acknowledgeAgentDecision: (id: string, workspaceId?: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/decisions/${encodeURIComponent(id)}/acknowledge${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`, { method: 'POST' }),
  resolveAgentDecision: (id: string, payload: { decision: 'approved' | 'rejected'; reason?: string }, workspaceId?: string) => apiFetch<Record<string, unknown>>(`/api/v1/agent-platform/decisions/${encodeURIComponent(id)}/resolve${workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : ''}`, { method: 'POST', body: JSON.stringify({ choice: payload }) }),
  workspaceOwnerAlertChannel: () => apiFetch<{ ownerWhatsappPhone: string | null; configured: boolean }>('/api/v1/workspaces/settings/owner-channel'),
  agentWorkspaceOwnerAlertChannel: (workspaceId: string) => apiFetch<{ ownerWhatsappPhone: string | null; configured: boolean }>(`/api/v1/agent-platform/owner-alert-channel?workspaceId=${encodeURIComponent(workspaceId)}`),
  updateWorkspaceOwnerAlertChannel: (ownerWhatsappPhone: string | null) => apiFetch<{ ownerWhatsappPhone: string | null; configured: boolean }>('/api/v1/workspaces/settings/owner-channel', { method: 'PUT', body: JSON.stringify({ owner_whatsapp_phone: ownerWhatsappPhone }) }),
  simulateHealthIntake: (matterId: string, threadId?: string) =>
    apiFetch<KlmAgentExecution>('/api/v1/klm/agents/simulations/health-intake', {
      method: 'POST',
      body: JSON.stringify({ matterId, threadId, idempotencyKey: `spa-health-team:${crypto.randomUUID()}` }),
    }),
  simulateIntake: (matterId: string, threadId?: string) =>
    apiFetch<KlmAgentExecution>('/api/v1/klm/agents/simulations/intake', {
      method: 'POST',
      body: JSON.stringify({ matterId, threadId, idempotencyKey: `spa-area-team:${crypto.randomUUID()}` }),
    }),
  tracking: (periodDays?: number) => apiFetch<KlmTrackingSummary>(`/api/v1/klm/tracking/summary${periodDays ? `?period_days=${periodDays}` : ''}`),
  prepareTrackingConversionFeedback: (periodDays?: number) => apiFetch<KlmConversionFeedbackPrepare>(
    `/api/v1/klm/tracking/conversion-feedback/prepare${periodDays ? `?period_days=${periodDays}` : ''}`,
    { method: 'POST' },
  ),
  backfillCtwa: (payload: { dryRun: boolean }) => apiFetch<KlmCtwaBackfillReport>(
    '/api/v1/klm/tracking/backfill-ctwa',
    { method: 'POST', body: JSON.stringify(payload) },
  ),
  dispatchTrackingConversionFeedback: () => apiFetch<KlmAdsFeedbackDispatch>(
    '/api/v1/klm/tracking/conversion-feedback/dispatch',
    { method: 'POST' },
  ),
  getAdsMeasurementConfig: () => apiFetch<KlmAdsMeasurementConfig>('/api/v1/klm/tracking/ads-measurement-config'),
  saveAdsMeasurementConfig: (payload: KlmAdsMeasurementConfigUpdate) => apiFetch<KlmAdsMeasurementConfig>(
    '/api/v1/klm/tracking/ads-measurement-config',
    { method: 'PUT', body: JSON.stringify(payload) },
  ),
  technicalRequests: () => apiFetch<KlmTechnicalRequest[]>('/api/v1/klm/technical-requests'),
  createTechnicalRequest: (payload: { title: string; description: string; route: string; severity: string; evidence?: Array<Record<string, unknown>> }) =>
    apiFetch<KlmTechnicalRequest>('/api/v1/klm/technical-requests', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateTechnicalRequest: (
    requestId: string,
    payload: {
      status?: KlmTechnicalRequest['status'];
      severity?: KlmTechnicalRequest['severity'];
      resolution?: string | null;
      evidence?: Array<Record<string, unknown>>;
    },
  ) => apiFetch<KlmTechnicalRequest>(`/api/v1/klm/technical-requests/${requestId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  supportTickets: () => apiFetch<KlmSupportTicketPage>('/api/v1/support/tickets?limit=100'),
  supportTicket: (ticketId: string) =>
    apiFetch<KlmSupportTicketDetail>(`/api/v1/support/tickets/${encodeURIComponent(ticketId)}`),
  createSupportTicket: (payload: KlmSupportTicketCreate, idempotencyKey: string) =>
    apiFetch<KlmSupportTicket>('/api/v1/support/tickets', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    }),
  addSupportMessage: (ticketId: string, body: string, idempotencyKey: string) =>
    apiFetch<KlmSupportMessage>(`/api/v1/support/tickets/${encodeURIComponent(ticketId)}/messages`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ body, visibility: 'public' }),
    }),
  uploadSupportAttachment: async (
    ticketId: string,
    file: File,
    messageId?: string,
    idempotencyKey = `support-attachment:${crypto.randomUUID()}`,
  ) => {
    const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
    const checksumSha256 = Array.from(
      new Uint8Array(digest),
      (item) => item.toString(16).padStart(2, '0'),
    ).join('');
    const authorization = await apiFetch<KlmSupportUploadAuthorization>(
      `/api/v1/support/tickets/${encodeURIComponent(ticketId)}/attachments`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify({
          upload: {
            filename: file.name,
            contentType: file.type || 'application/octet-stream',
            byteSize: file.size,
            checksumSha256,
            expiresIn: 300,
          },
          messageId: messageId ?? null,
        }),
      },
    );
    if (authorization.uploadRequired !== false) {
      if (!authorization.uploadUrl) {
        throw new Error('A autorização de upload não informou um destino privado válido.');
      }
      const { target, apiOrigin } = secureUploadTarget(authorization.uploadUrl);
      const uploadHeaders = authorization.uploadHeaders ?? {};
      const headers = Object.keys(uploadHeaders).length
        ? { ...uploadHeaders }
        : { 'Content-Type': file.type || 'application/octet-stream' };
      const response = await fetch(target, {
        method: 'PUT',
        credentials: target.origin === apiOrigin ? 'include' : 'omit',
        headers,
        body: file,
      });
      if (!response.ok) throw new Error(`O anexo não foi armazenado (${response.status}).`);
      return apiFetch<KlmSupportAttachment>(
        `/api/v1/support/tickets/${encodeURIComponent(ticketId)}/attachments/${encodeURIComponent(authorization.attachmentId)}/confirm`,
        { method: 'POST' },
      );
    }
    const ticket = await apiFetch<KlmSupportTicketDetail>(
      `/api/v1/support/tickets/${encodeURIComponent(ticketId)}`,
    );
    const replayed = ticket.attachments.find((item) => item.id === authorization.attachmentId);
    if (!replayed) {
      throw new Error('O anexo idempotente não está mais disponível neste chamado.');
    }
    return replayed;
  },
  supportAttachmentDownloadTicket: (ticketId: string, attachmentId: string, expiresIn = 300) =>
    apiFetch<KlmSupportAttachmentDownload>(
      `/api/v1/support/tickets/${encodeURIComponent(ticketId)}/attachments/${encodeURIComponent(attachmentId)}/download-ticket?expiresIn=${encodeURIComponent(expiresIn)}`,
    ),
  closeSupportTicket: (ticketId: string) =>
    apiFetch<KlmSupportTicket>(`/api/v1/support/tickets/${encodeURIComponent(ticketId)}/close`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `support-close:${crypto.randomUUID()}` },
    }),
  reopenSupportTicket: (ticketId: string) =>
    apiFetch<KlmSupportTicket>(`/api/v1/support/tickets/${encodeURIComponent(ticketId)}/reopen`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `support-reopen:${crypto.randomUUID()}` },
    }),
  rateSupportTicket: (ticketId: string, score: number, comment?: string) =>
    apiFetch<KlmSupportTicket>(`/api/v1/support/tickets/${encodeURIComponent(ticketId)}/rating`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `support-rating:${crypto.randomUUID()}` },
      body: JSON.stringify({ score, comment: comment?.trim() || null }),
    }),
  externalIntents: () => apiFetch<KlmExternalActionIntent[]>('/api/v1/klm/external-intents'),
  createExternalIntent: (payload: {
    matterId?: string | null;
    actionType: string;
    provider?: string | null;
    idempotencyKey: string;
    payload?: Record<string, unknown>;
  }) => apiFetch<KlmExternalActionIntent>('/api/v1/klm/external-intents', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  createDomainExternalIntent: ({ domain, ...payload }: {
    domain: KlmNativeDomain;
    matterId?: string | null;
    actionType: string;
    idempotencyKey: string;
    payload?: Record<string, unknown>;
  }) => apiFetch<KlmExternalActionIntent>('/api/v1/klm/external-intents', {
    method: 'POST',
    body: JSON.stringify({ ...payload, provider: domain }),
  }),
  approveExternalIntentLocally: (intentId: string) =>
    apiFetch<KlmExternalActionIntent>(`/api/v1/klm/external-intents/${intentId}/approve-local`, {
      method: 'POST',
    }),
  rejectExternalIntentLocally: (intentId: string) =>
    apiFetch<KlmExternalActionIntent>(`/api/v1/klm/external-intents/${intentId}/reject-local`, {
      method: 'POST',
    }),
  legalArtifacts: (filters: { matterId?: string; kind?: KlmLegalArtifactKind } = {}) => {
    const params = new URLSearchParams();
    if (filters.matterId) params.set('matter_id', filters.matterId);
    if (filters.kind) params.set('kind', filters.kind);
    const query = params.toString();
    return apiFetch<KlmLegalArtifactVersion[]>(`/api/v1/klm/legal-artifacts${query ? `?${query}` : ''}`);
  },
  legalArtifact: (artifactId: string) =>
    apiFetch<KlmLegalArtifactVersion>(`/api/v1/klm/legal-artifacts/${artifactId}`),
  createLegalArtifact: (payload: {
    matterId: string;
    kind: KlmLegalArtifactKind;
    title: string;
    content: Record<string, unknown>;
    artifactKey?: string;
    expectedVersion?: number;
  }) => apiFetch<KlmLegalArtifactVersion>('/api/v1/klm/legal-artifacts', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  submitLegalArtifact: (artifactId: string) =>
    apiFetch<{ artifact: KlmLegalArtifactVersion; approvalId: string }>(`/api/v1/klm/legal-artifacts/${artifactId}/submit`, {
      method: 'POST',
    }),
  processMonitoring: (matterId?: string) =>
    apiFetch<KlmProcessMonitoringRecord[]>(`/api/v1/klm/process-monitoring${matterId ? `?matter_id=${matterId}` : ''}`),
  createProcessMonitoring: (payload: {
    matterId: string;
    caseNumber: string;
    court?: string | null;
    status?: string;
    nextDeadlineAt?: string | null;
    lastEventSummary?: string | null;
  }) => apiFetch<KlmProcessMonitoringRecord>('/api/v1/klm/process-monitoring', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateProcessMonitoring: (recordId: string, payload: {
    court?: string | null;
    status?: string;
    nextDeadlineAt?: string | null;
    lastEventSummary?: string | null;
  }) => apiFetch<KlmProcessMonitoringRecord>(`/api/v1/klm/process-monitoring/${recordId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  addProcessMonitoringEvent: (recordId: string, payload: {
    occurredAt?: string;
    title: string;
    description?: string | null;
    source?: 'manual_local' | 'court_connector_draft' | 'client_update_draft';
  }) => apiFetch<KlmProcessMonitoringRecord>(`/api/v1/klm/process-monitoring/${recordId}/events`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  matterTasks: (matterId: string) => apiFetch<KlmTask[]>(`/api/v1/matters/${matterId}/tasks`),
  createMatterTask: (matterId: string, payload: {
    title: string;
    description?: string | null;
    assignedTo?: string | null;
    dueDate?: string | null;
    priority?: KlmTask['priority'];
    isAiGenerated?: boolean;
    idempotencyKey?: string | null;
  }) => apiFetch<KlmTask>(`/api/v1/matters/${matterId}/tasks`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  toggleMatterTask: (taskId: string, expectedVersion: number) => apiFetch<KlmTask>(`/api/v1/tasks/${taskId}/toggle?expectedVersion=${encodeURIComponent(expectedVersion)}`, {
    method: 'PATCH',
  }),
  createDocumentUpload: (
    matterId: string,
    payload: { name: string; checksumSha256: string; docType?: string; status?: string; fileSize?: string; mimeType?: string },
    idempotencyKey = `matter-document:${crypto.randomUUID()}`,
  ) =>
    apiFetch<KlmDocumentUpload>(`/api/v1/matters/${matterId}/documents`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    }),
  uploadDocumentBytes: async (uploadUrl: string, file: File, uploadHeaders: Record<string, string> = {}) => {
    const { target, apiOrigin } = secureUploadTarget(uploadUrl);
    const headers = Object.keys(uploadHeaders).length
      ? { ...uploadHeaders }
      : { 'Content-Type': file.type || 'application/octet-stream' };
    const response = await fetch(target, {
      method: 'PUT',
      credentials: target.origin === apiOrigin ? 'include' : 'omit',
      headers,
      body: file,
    });
    if (!response.ok) throw new Error(`Falha no armazenamento privado (${response.status}).`);
  },
  confirmDocumentUpload: (documentId: string) =>
    apiFetch<KlmDocument>(`/api/v1/documents/${documentId}/confirm`, { method: 'POST' }),
  completeDocumentUpload: async (
    matterId: string,
    authorization: KlmDocumentUpload,
    file: File,
  ): Promise<KlmDocument> => {
    if (authorization.uploadRequired !== false) {
      if (!authorization.uploadUrl) {
        throw new Error('A autorização de upload não informou um destino privado válido.');
      }
      const { target, apiOrigin } = secureUploadTarget(authorization.uploadUrl);
      const uploadHeaders = authorization.uploadHeaders ?? {};
      const headers = Object.keys(uploadHeaders).length
        ? { ...uploadHeaders }
        : { 'Content-Type': file.type || 'application/octet-stream' };
      const response = await fetch(target, {
        method: 'PUT',
        credentials: target.origin === apiOrigin ? 'include' : 'omit',
        headers,
        body: file,
      });
      if (!response.ok) throw new Error(`Falha no armazenamento privado (${response.status}).`);
      return apiFetch<KlmDocument>(`/api/v1/documents/${authorization.documentId}/confirm`, {
        method: 'POST',
      });
    }
    const documents = await apiFetch<KlmDocument[]>(`/api/v1/matters/${matterId}/documents`);
    const replayed = documents.find((document) => document.id === authorization.documentId);
    if (!replayed) {
      throw new Error('O documento idempotente não está mais disponível neste caso.');
    }
    return replayed;
  },
  audioRequest: (payload: { threadId: string; transcript: string; durationSeconds: number; summary?: string; transcriptionEnabled?: boolean; summaryEnabled?: boolean; voiceReplyRequested?: boolean }) =>
    apiFetch<KlmAudioRequestResult>('/api/v1/klm/audio/requests', {
      method: 'POST',
      body: JSON.stringify({ ...payload, idempotencyKey: `spa-audio:${crypto.randomUUID()}` }),
    }),
  meetings: () => apiFetch<KlmMeeting[]>('/api/v1/meetings'),
  meetingsForMatter: (matterId: string) => apiFetch<KlmMeeting[]>(`/api/v1/meetings?matter_id=${encodeURIComponent(matterId)}`),
  createMeeting: (payload: KlmMeetingWrite) =>
    withMeetingCreateIdempotency(payload, (idempotencyKey) =>
      apiFetch<KlmMeeting>('/api/v1/meetings', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(payload),
      }),
    ),
  updateMeeting: (meetingId: string, payload: Partial<KlmMeetingWrite> & { expectedVersion: number }) =>
    apiFetch<KlmMeeting>(`/api/v1/meetings/${meetingId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  replaceMeetingParticipants: (meetingId: string, payload: KlmMeetingParticipantsUpdate) =>
    apiFetch<KlmMeeting>(`/api/v1/meetings/${meetingId}/participants`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteMeeting: (meetingId: string, expectedVersion: number) =>
    apiFetch<void>(`/api/v1/meetings/${meetingId}?expectedVersion=${encodeURIComponent(expectedVersion)}`, { method: 'DELETE' }),
  calendarSettings: () => apiFetch<KlmCalendarSettings>('/api/v1/agenda/settings'),
  updateCalendarSettings: (payload: Omit<KlmCalendarSettings, 'connectionStatus'>) =>
    apiFetch<KlmCalendarSettings>('/api/v1/agenda/settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  availability: (durationMinutes = 45, responsibleUserId?: string) => {
    const params = new URLSearchParams({ duration_minutes: String(durationMinutes) });
    if (responsibleUserId) params.set('responsible_user_id', responsibleUserId);
    return apiFetch<KlmAvailabilityPreview>(`/api/v1/agenda/availability?${params.toString()}`);
  },
  createInternalBooking: (payload: {
    matterId: string;
    responsibleUserId?: string | null;
    title: string;
    startsAt: string;
    durationMinutes: number;
    notes?: string | null;
  }) => apiFetch<KlmMeeting>('/api/v1/agenda/internal-bookings', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  bookingLinks: () => apiFetch<KlmBookingLink[]>('/api/v1/agenda/booking-links'),
  bookingLinksForMatter: (matterId: string) => apiFetch<KlmBookingLink[]>(`/api/v1/agenda/booking-links?matter_id=${encodeURIComponent(matterId)}`),
  createBookingLink: (payload: {
    matterId?: string | null;
    responsibleUserId?: string | null;
    title: string;
    description?: string | null;
    durationMinutes: number;
    meetingProvider?: KlmCalendarProvider | null;
    meetingUrl?: string | null;
    expiresAt?: string | null;
    maxUses?: number | null;
  }) => apiFetch<KlmBookingLink>('/api/v1/agenda/booking-links', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  revokeBookingLink: (linkId: string) =>
    apiFetch<KlmBookingLink>(`/api/v1/agenda/booking-links/${linkId}/revoke`, { method: 'POST' }),
  meetingBriefing: (meetingId: string) =>
    apiFetch<KlmMeetingBriefing>(`/api/v1/agenda/meetings/${meetingId}/briefing`),
  dailyBriefing: (day: string) =>
    apiFetch<KlmDailyBriefing>(`/api/v1/agenda/daily-briefing?day=${encodeURIComponent(day)}`),
  /** Agenda unificada — comercial + jurídico numa única lista (`GET
   * /agenda`). `natureza` filtra no backend; `start`/`end` recortam o
   * período (ISO 8601). Ver `AgendaPage` (toggle Tudo | Comercial |
   * Jurídico) e `KlmAgendaUnifiedEvent`. */
  unifiedAgenda: (params: { natureza?: 'todos' | KlmMeetingNature; start?: string; end?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.natureza) search.set('natureza', params.natureza);
    if (params.start) search.set('start', params.start);
    if (params.end) search.set('end', params.end);
    const qs = search.toString();
    return apiFetch<KlmAgendaUnifiedEvent[]>(`/api/v1/agenda${qs ? `?${qs}` : ''}`);
  },
  publicBookingPage: (token: string) =>
    apiFetch<KlmPublicBookingPage>(`/api/v1/agenda/public/${encodeURIComponent(token)}`),
  createPublicBooking: (token: string, payload: { startsAt: string; name: string; phone?: string; email?: string; notes?: string }) =>
    apiFetch<KlmPublicBookingConfirmation>(`/api/v1/agenda/public/${encodeURIComponent(token)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  messagingCadenceSettings: () => apiFetch<KlmMessagingCadenceSettings>('/api/v1/klm/messaging-cadence/settings'),
  updateMessagingCadenceSettings: (payload: KlmMessagingCadenceSettings) => apiFetch<KlmMessagingCadenceSettings>('/api/v1/klm/messaging-cadence/settings', {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  previewMessagingCadence: (text: string) => apiFetch<KlmMessagingCadencePlan>('/api/v1/klm/messaging-cadence/preview', {
    method: 'POST',
    body: JSON.stringify({ text, idempotencyKey: `spa-preview:${crypto.randomUUID()}` }),
  }),
  medicationCatalogStatus: () => apiFetch<KlmMedicationCatalogStatus>('/api/v1/medications/catalog/status'),
  medications: (query = '', oncology?: boolean) => {
    const params = new URLSearchParams({ limit: '50' });
    if (query.trim()) params.set('q', query.trim());
    if (oncology !== undefined) params.set('oncology', String(oncology));
    return apiFetch<KlmMedicationReference[]>(`/api/v1/medications/catalog?${params.toString()}`);
  },
  treatmentEstimates: (matterId: string) => apiFetch<KlmTreatmentEstimate[]>(`/api/v1/medications/matters/${matterId}/treatment-estimates`),
  upsertTreatmentEstimate: (matterId: string, payload: {
    medicationReferenceId: string;
    presentationsPerCycle: number;
    cycleCount: number;
    cycleIntervalDays?: number | null;
    treatmentDurationDays?: number | null;
    prescriptionReference?: string | null;
    notes?: string | null;
    reviewStatus: KlmTreatmentEstimate['reviewStatus'];
    applyToMatterEconomicValue: boolean;
  }) => apiFetch<KlmTreatmentEstimate>(`/api/v1/medications/matters/${matterId}/treatment-estimates`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  deleteTreatmentEstimate: (matterId: string, estimateId: string) => apiFetch<void>(`/api/v1/medications/matters/${matterId}/treatment-estimates/${estimateId}`, { method: 'DELETE' }),
  legalPricingPolicy: () => apiFetch<KlmLegalPricingPolicy>('/api/v1/medications/pricing-policy'),
  updateLegalPricingPolicy: (payload: KlmLegalPricingPolicy) => apiFetch<KlmLegalPricingPolicy>('/api/v1/medications/pricing-policy', {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  financeOverview: () => apiFetch<KlmFinanceOverview>('/api/v1/finance/overview'),
  financeEntries: (matterId?: string) => apiFetch<KlmFinanceEntry[]>(`/api/v1/finance/entries${matterId ? `?matter_id=${encodeURIComponent(matterId)}` : ''}`),
  createFinanceCharge: (
    entryId: string,
    payload: {
      billingType?: string;
      customerExternalId?: string | null;
      dueDate?: string | null;
      description?: string | null;
    } = {},
  ) => apiFetch<KlmFinanceCharge>(`/api/v1/finance/entries/${encodeURIComponent(entryId)}/charge`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  listFinanceCharges: (filters: { status?: KlmFinanceChargeStatus; dueBefore?: string; dueAfter?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (filters.status) params.set('status', filters.status);
    if (filters.dueBefore) params.set('due_before', filters.dueBefore);
    if (filters.dueAfter) params.set('due_after', filters.dueAfter);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmFinanceCharge[]>(`/api/v1/finance/charges${query ? `?${query}` : ''}`);
  },
  getFinanceCharge: (chargeId: string, withPix = false) =>
    apiFetch<KlmFinanceCharge>(`/api/v1/finance/charges/${encodeURIComponent(chargeId)}${withPix ? '?withPix=true' : ''}`),
  cancelFinanceCharge: (chargeId: string) =>
    apiFetch<KlmFinanceCharge>(`/api/v1/finance/charges/${encodeURIComponent(chargeId)}`, { method: 'DELETE' }),
  createFinanceInstallmentSeries: (
    entryId: string,
    payload: { installmentCount: number; totalValue?: number; firstDueDate?: string | null; billingType?: string; customerExternalId?: string | null },
  ) => apiFetch<KlmFinanceInstallmentSeries>(`/api/v1/finance/entries/${encodeURIComponent(entryId)}/installments`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  financeKpis: async (params: { startDate?: string; endDate?: string; asOf?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.startDate) query.set('startDate', params.startDate);
    if (params.endDate) query.set('endDate', params.endDate);
    if (params.asOf) query.set('asOf', params.asOf);
    const qs = query.toString();
    const payload = await apiFetch<KlmFinanceKpis>(`/api/v1/finance/reports/kpis${qs ? `?${qs}` : ''}`);
    return normalizeFinanceKpis(payload);
  },
  financeDre: (params: { competencia?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.competencia) query.set('competencia', params.competencia);
    const qs = query.toString();
    return apiFetch<KlmFinanceDre>(`/api/v1/finance/reports/dre${qs ? `?${qs}` : ''}`);
  },
  financeCashFlow: (params: { dias?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.dias) query.set('dias', String(params.dias));
    const qs = query.toString();
    return apiFetch<KlmFinanceCashFlowProjection>(`/api/v1/finance/reports/cash-flow${qs ? `?${qs}` : ''}`);
  },
  createFinanceExport: (payload: { format: KlmFinanceExportFormat; drillContext?: Record<string, unknown> }) => apiFetch<KlmFinanceExportJob>('/api/v1/finance/exports', { method: 'POST', body: JSON.stringify({ format: payload.format, drill_context: payload.drillContext ?? {} }) }),
  listFinanceExports: () => apiFetch<KlmFinanceExportJob[]>('/api/v1/finance/exports'),
  getFinanceExport: (jobId: string) => apiFetch<KlmFinanceExportJob>(`/api/v1/finance/exports/${encodeURIComponent(jobId)}`),
  cancelFinanceExport: (jobId: string) => apiFetch<KlmFinanceExportJob>(`/api/v1/finance/exports/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
  downloadFinanceExport: (jobId: string) => apiBlob(`/api/v1/finance/exports/${encodeURIComponent(jobId)}/download`),
  listFinanceCostCenters: () => apiFetch<KlmFinanceCostCenter[]>('/api/v1/finance/dimensional/cost-centers'),
  financeCostCenterImpact: (costCenterId:string) => apiFetch<KlmFinanceCostCenterImpact>(`/api/v1/finance/dimensional/cost-centers/${encodeURIComponent(costCenterId)}/impact`),
  previewFinanceCostCenterImpact: (costCenterId:string, payload:{ patch:{ name?:string; description?:string|null; parent_id?:string|null; owner_user_id?:string|null; status?:'active'|'inactive'|'archived' }; expectedVersion:number }) => apiFetch<KlmFinanceCostCenterImpact & { proposed:Record<string,unknown>; expectedVersion:number; eligible:boolean }>(`/api/v1/finance/dimensional/cost-centers/${encodeURIComponent(costCenterId)}/impact/preview`, { method:'POST', body:JSON.stringify(payload) }),
  updateFinanceCostCenter: (costCenterId:string, payload:{ name?:string; description?:string|null; parentId?:string|null; type?:string; ownerUserId?:string|null; status?:'active'|'inactive'|'archived'; expectedVersion:number }) => apiFetch<KlmFinanceCostCenter>(`/api/v1/finance/dimensional/cost-centers/${encodeURIComponent(costCenterId)}`, { method:'PATCH', body:JSON.stringify(payload) }),
  listFinanceBudgetLines: (filters:{periodStart?:string;periodEnd?:string;costCenterId?:string}={}) => { const query=new URLSearchParams(); if(filters.periodStart)query.set('period_start',filters.periodStart); if(filters.periodEnd)query.set('period_end',filters.periodEnd); if(filters.costCenterId)query.set('cost_center_id',filters.costCenterId); const suffix=query.toString(); return apiFetch<KlmFinanceBudgetLine[]>(`/api/v1/finance/dimensional/budget-lines${suffix?`?${suffix}`:''}`); },
  createFinanceBudgetLine: (payload:{ periodStart:string;periodEnd:string;costCenterId?:string|null;category?:string|null;entryType:'receivable'|'expense'|'refund';amount:number;assumptions:Record<string,unknown> }) => apiFetch<KlmFinanceBudgetLine>('/api/v1/finance/dimensional/budget-lines',{method:'POST',body:JSON.stringify(payload)}),
  financeLookup: (entityType: KlmFinanceLookupType, options: string | { search?:string; cursor?:string|null; limit?:number } = '') => {
    const normalized=typeof options === 'string' ? {search:options} : options;
    const query = new URLSearchParams({ entity_type:entityType, search:normalized.search ?? '', limit:String(normalized.limit ?? 50) });
    if(normalized.cursor) query.set('cursor',normalized.cursor);
    return apiFetch<KlmFinanceLookupPage>(`/api/v1/finance/dimensional/lookups?${query}`);
  },
  resolveFinanceLookups: (entityType:KlmFinanceLookupType, ids:string[]) => apiFetch<{items:KlmFinanceLookupItem[];entityType:KlmFinanceLookupType}>('/api/v1/finance/dimensional/lookups/resolve',{method:'POST',body:JSON.stringify({entityType,ids})}),
  createFinanceCostCenter: (payload: Record<string, unknown>) => apiFetch<KlmFinanceCostCenter>('/api/v1/finance/dimensional/cost-centers', { method:'POST', body: JSON.stringify(payload) }),
  createFinanceAllocation: (payload: Record<string, unknown>) => apiFetch<KlmFinanceAllocation>('/api/v1/finance/dimensional/allocations', { method:'POST', body: JSON.stringify(payload) }),
  financeAllocationClosure: (financeEntryId:string) => apiFetch<KlmFinanceAllocationClosure>(`/api/v1/finance/dimensional/allocations/${encodeURIComponent(financeEntryId)}/closure`),
  createFinanceForecast: (payload:{ periodStart:string; periodEnd:string; revenue:number; expense:number; assumptions:Record<string,unknown>; scenario:'conservative'|'base'|'optimistic'; horizon:'30d'|'90d'|'180d'|'365d'|'3y' }) => apiFetch<KlmFinanceForecast>('/api/v1/finance/dimensional/forecasts', { method:'POST', body: JSON.stringify(payload) }),
  generateFinanceForecasts: (payload:{ asOf:string; horizon:'30d'|'90d'|'180d'|'365d'|'3y'; scenarios:Array<'conservative'|'base'|'optimistic'> }) => apiFetch<KlmFinanceForecast[]>('/api/v1/finance/dimensional/forecasts/generate', { method:'POST', body:JSON.stringify(payload) }),
  listFinanceForecasts: (limit = 100) => apiFetch<KlmFinanceForecast[]>(`/api/v1/finance/dimensional/forecasts?limit=${encodeURIComponent(limit)}`),
  createFinanceValuation: (payload:{ method:'dcf_simplified'|'revenue_multiple'|'ebitda_multiple'; valuationDate:string; enterpriseValue:number; assumptions:Record<string,unknown>; premiseQuality:'verified'|'partial'|'unverified' }) => apiFetch<KlmFinanceValuation>('/api/v1/finance/dimensional/valuations', { method:'POST', body: JSON.stringify(payload) }),
  calculateFinanceValuation: (payload:{ method:'dcf_simplified'|'revenue_multiple'|'ebitda_multiple'; asOf:string; assumptions:Record<string,number> }) => apiFetch<KlmFinanceValuation>('/api/v1/finance/dimensional/valuations/calculate', { method:'POST', body:JSON.stringify(payload) }),
  listFinanceValuations: (limit = 100) => apiFetch<KlmFinanceValuation[]>(`/api/v1/finance/dimensional/valuations?limit=${encodeURIComponent(limit)}`),
  upsertFinanceTaxProfile: (payload: Record<string, unknown>) => apiFetch<Record<string, unknown>>('/api/v1/finance/dimensional/tax-profile', { method:'PUT', body: JSON.stringify(payload) }),
  financeTaxProfile: () => apiFetch<KlmFinanceTaxProfile | null>('/api/v1/finance/dimensional/tax-profile'),
  financeTaxRules: () => apiFetch<KlmFinanceTaxRules>('/api/v1/finance/dimensional/tax-rules'),
  financeAnalytics: (payload: Record<string, unknown>) => apiFetch<KlmFinanceAnalyticsResponse>('/api/v1/finance/dimensional/analytics/query', { method:'POST', body: JSON.stringify(payload) }),
  financeAnalyticsDrilldown: (payload: Record<string, unknown>) => apiFetch<KlmFinanceAnalyticsResponse>('/api/v1/finance/dimensional/analytics/drilldown', { method:'POST', body: JSON.stringify(payload) }),
  openFinanceEvidence: (reference: string) => apiFetch<{ downloadUrl: string; expiresAt: string }>(`/api/v1/finance/dimensional/evidence/${encodeURIComponent(reference)}`),
  financeDimensionalDre: (payload: { mode:'cash'|'accrual'; periodStart:string; periodEnd:string; costCenterId?:string|null; clientId?:string|null; contractId?:string|null; matterId?:string|null; granularity?:'monthly'|'annual'; accumulated?:boolean; comparePrevious?:boolean }) => apiFetch<KlmFinanceDimensionalDre>('/api/v1/finance/dimensional/dre', { method:'POST', body:JSON.stringify(payload) }),
  simulateFinanceTaxReform: (payload: { annualRevenue:number; annualExpense:number; currentEffectiveRate?:number|null; transitionRates?:Record<string,number>; pricePassThroughPercent?:number }) => apiFetch<KlmFinanceTaxReformSimulation>('/api/v1/finance/dimensional/tax-reform/simulate', { method:'POST', body:JSON.stringify(payload) }),
  requestFinanceTaxReview: (payload: { requestKey:string; reason:string; simulation:{ annualRevenue:number; annualExpense:number; currentEffectiveRate?:number|null; transitionRates?:Record<string,number>; pricePassThroughPercent?:number }; slaMinutes?:number }) => apiFetch<KlmFinanceTaxReviewRequest>('/api/v1/finance/dimensional/tax-reform/request-review', { method:'POST', body:JSON.stringify(payload) }),
  financeExecutiveAnalytics: () => apiFetch<KlmFinanceExecutiveAnalytics>('/api/v1/finance/v1/executive/analytics'),
  financeDunningConfig: () => apiFetch<KlmFinanceDunningSettings>('/api/v1/finance/dunning/config'),
  updateFinanceDunningConfig: (steps: Partial<Record<KlmFinanceDunningStepKey, KlmFinanceDunningStepConfigUpdate>>) =>
    apiFetch<KlmFinanceDunningSettings>('/api/v1/finance/dunning/config', {
      method: 'PATCH',
      body: JSON.stringify({ steps }),
    }),
  financeDunningLog: (params: { status?: KlmFinanceDunningLogStatus; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    return apiFetch<KlmFinanceDunningLog[]>(`/api/v1/finance/dunning/log${qs ? `?${qs}` : ''}`);
  },
  approveFinanceDunningLog: (logId: string) =>
    apiFetch<KlmFinanceDunningLog>(`/api/v1/finance/dunning/log/${encodeURIComponent(logId)}/approve`, {
      method: 'POST',
    }),
  listFinanceExpenses: (filters: { category?: string; costCenter?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (filters.category) params.set('category', filters.category);
    if (filters.costCenter) params.set('cost_center', filters.costCenter);
    if (filters.limit) params.set('limit', String(filters.limit));
    const query = params.toString();
    return apiFetch<KlmFinanceExpense[]>(`/api/v1/finance/expenses${query ? `?${query}` : ''}`);
  },
  approveFinanceExpense: (entryId: string, payload: { decision: 'approved' | 'rejected'; reason?: string | null }) =>
    apiFetch<KlmFinanceExpense>(`/api/v1/finance/expenses/${encodeURIComponent(entryId)}/approve`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  // Honorários jurídicos (F9).
  feeArrangements: (matterId?: string) => {
    const query = matterId ? `?matter_id=${encodeURIComponent(matterId)}` : '';
    return apiFetch<KlmFeeArrangement[]>(`/api/v1/finance/fee-arrangements${query}`);
  },
  createFeeArrangement: (payload: {
    matterId: string;
    kind: KlmFeeArrangementKind;
    fixedValue?: number | null;
    percent?: number | null;
    baseCalculoExito?: string | null;
    hourlyRate?: number | null;
    notes?: string | null;
  }) => apiFetch<KlmFeeArrangement>('/api/v1/finance/fee-arrangements', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  getFeeArrangement: (arrangementId: string) =>
    apiFetch<KlmFeeArrangement>(`/api/v1/finance/fee-arrangements/${encodeURIComponent(arrangementId)}`),
  closeFeeArrangement: (arrangementId: string) =>
    apiFetch<KlmFeeArrangement>(`/api/v1/finance/fee-arrangements/${encodeURIComponent(arrangementId)}/close`, {
      method: 'POST',
    }),
  generateFeeArrangementCharge: (
    arrangementId: string,
    payload: {
      installmentCount?: number | null;
      firstDueDate?: string | null;
      billingType?: KlmFeeBillingType;
      customerExternalId?: string | null;
    } = {},
  ) => apiFetch<KlmGenerateFeeChargeResult>(`/api/v1/finance/fee-arrangements/${encodeURIComponent(arrangementId)}/generate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  logFeeTimeEntry: (
    arrangementId: string,
    payload: { userId: string; date: string; minutes: number; description?: string | null },
  ) => apiFetch<KlmFeeTimeEntry>(`/api/v1/finance/fee-arrangements/${encodeURIComponent(arrangementId)}/time-entries`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  closeFeeMonthlyInvoice: (arrangementId: string, competence: string) =>
    apiFetch<KlmFeeFinanceEntrySummary>(`/api/v1/finance/fee-arrangements/${encodeURIComponent(arrangementId)}/invoices/close`, {
      method: 'POST',
      body: JSON.stringify({ competence }),
    }),
  registerFeeSuccessEvent: (arrangementId: string, payload: { baseValue: number; requestedBy?: string | null }) =>
    apiFetch<KlmFeeSuccessEventResult>(`/api/v1/finance/fee-arrangements/${encodeURIComponent(arrangementId)}/success-events`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  approveFeeSuccessEvent: (approvalId: string) =>
    // O autor da aprovação é derivado no backend do usuário autenticado — o
    // endpoint não aceita mais `resolvedBy` no corpo (ver contract_billing.py
    // approve_billing_proposal / fee_arrangements.py approve_success_fee).
    apiFetch<KlmApproveFeeSuccessResult>(`/api/v1/finance/fee-arrangements/success-events/${encodeURIComponent(approvalId)}/approve`, {
      method: 'POST',
    }),
  renegotiateFeeOverdueEntry: (
    entryId: string,
    payload: {
      installmentCount: number;
      agreedValue: number;
      firstDueDate?: string | null;
      billingType?: KlmFeeBillingType;
      requestedBy?: string | null;
    },
  ) => apiFetch<KlmRenegotiateFeeResult>(`/api/v1/finance/entries/${encodeURIComponent(entryId)}/renegotiate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  // Contrato → nós financeiros com aprovação (F11).
  extractContractBillingPlan: (payload: {
    matterId: string;
    contractText: string;
    sourceDocumentName?: string | null;
    requestedBy?: string | null;
  }) => apiFetch<KlmContractBillingProposal>('/api/v1/finance/contract-billing-proposals/extract', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  getContractBillingProposal: (proposalId: string) =>
    apiFetch<KlmContractBillingProposal>(`/api/v1/finance/contract-billing-proposals/${encodeURIComponent(proposalId)}`),
  updateContractBillingProposal: (
    proposalId: string,
    payload: Partial<{
      sourceDocumentName: string | null;
      items: KlmContractBillingItem[];
      notes: string | null;
      expectedUpdatedAt: string;
    }>,
    idempotencyKey?: string,
  ) => apiFetch<KlmContractBillingProposal>(`/api/v1/finance/contract-billing-proposals/${encodeURIComponent(proposalId)}`, {
    method: 'PATCH',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    body: JSON.stringify(payload),
  }),
  submitContractBillingProposal: (proposalId: string, payload: { requestedBy?: string | null } = {}) =>
    apiFetch<KlmContractBillingSubmitResult>(`/api/v1/finance/contract-billing-proposals/${encodeURIComponent(proposalId)}/submit`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  approveContractBillingProposal: (approvalId: string) =>
    // O autor da aprovação é derivado no backend do usuário autenticado — o
    // endpoint não aceita mais `resolvedBy` no corpo.
    apiFetch<KlmContractBillingApproveResult>(`/api/v1/finance/contract-billing-proposals/approvals/${encodeURIComponent(approvalId)}/approve`, {
      method: 'POST',
    }),
  rejectContractBillingProposal: (
    approvalId: string,
    payload: { resolutionReason?: string | null } = {},
  ) => apiFetch<KlmContractBillingProposal>(`/api/v1/finance/contract-billing-proposals/approvals/${encodeURIComponent(approvalId)}/reject`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  // Leads de sites (F12 — guia de conexões).
  integrationsStatus: () => apiFetch<KlmIntegrationsStatus>('/api/v1/integrations/status'),
  rotateSiteLeadsWebhookToken: () =>
    apiFetch<KlmLeadWebhookToken>('/api/v1/integrations/site-leads/token', { method: 'POST' }),
  sendTestSiteLead: (token: string, payload: Record<string, unknown>) =>
    apiFetch<KlmLeadWebhookTestResult>(`/api/v1/webhooks/leads/${encodeURIComponent(token)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  // Indicações com comissão (F13).
  referralPartners: (ativo?: boolean) => {
    const query = ativo === undefined ? '' : `?ativo=${ativo}`;
    return apiFetch<KlmReferralPartner[]>(`/api/v1/referrals/partners${query}`);
  },
  createReferralPartner: (payload: {
    nome: string;
    tipo: KlmReferralPartnerType;
    contatoRef?: string | null;
    comissaoTipo: KlmReferralCommissionType;
    comissaoValor: number;
    vigenciaInicio: string;
    vigenciaFim?: string | null;
    ativo?: boolean;
    notas?: string | null;
  }) => apiFetch<KlmReferralPartner>('/api/v1/referrals/partners', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateReferralPartner: (partnerId: string, payload: Partial<{
    nome: string;
    tipo: KlmReferralPartnerType;
    contatoRef: string | null;
    comissaoTipo: KlmReferralCommissionType;
    comissaoValor: number;
    vigenciaInicio: string;
    vigenciaFim: string | null;
    ativo: boolean;
    notas: string | null;
  }>) => apiFetch<KlmReferralPartner>(`/api/v1/referrals/partners/${encodeURIComponent(partnerId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  registerMatterReferral: (matterId: string, payload: { partnerId: string; data?: string | null }) =>
    apiFetch<KlmMatterReferral>(`/api/v1/referrals/matters/${encodeURIComponent(matterId)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  matterReferral: (matterId: string) =>
    apiFetch<KlmMatterReferral | null>(`/api/v1/referrals/matters/${encodeURIComponent(matterId)}`),
  referralCommissions: (partnerId?: string) => {
    const query = partnerId ? `?partner_id=${encodeURIComponent(partnerId)}` : '';
    return apiFetch<KlmReferralCommission[]>(`/api/v1/referrals/commissions${query}`);
  },
  reconciliationImportOfx: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload<KlmReconciliationIngestResult>('/api/v1/finance/reconciliation/import/ofx', formData);
  },
  reconciliationImportCsv: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload<KlmReconciliationIngestResult>('/api/v1/finance/reconciliation/import/csv', formData);
  },
  reconciliationPending: () => apiFetch<KlmPendingReconciliation[]>('/api/v1/finance/reconciliation/pending'),
  reconciliationConfirmMatch: (
    matchId: string,
    payload: { targetKind: KlmReconciliationTargetKind; targetId: string; category?: string | null; tags?: string[] },
  ) => apiFetch<KlmReconciliationMatch>(`/api/v1/finance/reconciliation/matches/${encodeURIComponent(matchId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  reconciliationRejectMatch: (matchId: string, payload: { reason?: string | null } = {}) =>
    apiFetch<KlmReconciliationMatch>(`/api/v1/finance/reconciliation/matches/${encodeURIComponent(matchId)}/reject`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  reconciliationImportAsaas: (payload: { start: string; finish: string }) =>
    apiFetch<KlmReconciliationIngestResult>('/api/v1/finance/reconciliation/import/asaas', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  reconciliationConfirmMatchesBatch: (
    items: {
      matchId: string;
      targetKind: KlmReconciliationTargetKind;
      targetId: string;
      category?: string | null;
      tags?: string[];
    }[],
  ) =>
    apiFetch<KlmReconciliationMatch[]>('/api/v1/finance/reconciliation/matches/confirm-batch', {
      method: 'POST',
      body: JSON.stringify({ items }),
    }),
  reconciliationRejectMatchesBatch: (matchIds: string[], reason?: string | null) =>
    apiFetch<KlmReconciliationMatch[]>('/api/v1/finance/reconciliation/matches/reject-batch', {
      method: 'POST',
      body: JSON.stringify({ matchIds, reason: reason ?? null }),
    }),
  financeRecurrences: (params: { status?: KlmFinanceRecurrenceStatus; clientId?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.clientId) query.set('client_id', params.clientId);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiFetch<KlmFinanceRecurrence[]>(`/api/v1/finance/recurrences${suffix}`);
  },
  financeRecurrence: (recurrenceId: string) =>
    apiFetch<KlmFinanceRecurrence>(`/api/v1/finance/recurrences/${encodeURIComponent(recurrenceId)}`),
  financeRecurrencePause: (recurrenceId: string) =>
    apiFetch<KlmFinanceRecurrence>(`/api/v1/finance/recurrences/${encodeURIComponent(recurrenceId)}/pause`, {
      method: 'POST',
    }),
  financeRecurrenceResume: (recurrenceId: string) =>
    apiFetch<KlmFinanceRecurrence>(`/api/v1/finance/recurrences/${encodeURIComponent(recurrenceId)}/resume`, {
      method: 'POST',
    }),
  financeRecurrenceEnd: (recurrenceId: string) =>
    apiFetch<KlmFinanceRecurrence>(`/api/v1/finance/recurrences/${encodeURIComponent(recurrenceId)}/end`, {
      method: 'POST',
    }),
  financeHarmonizationQuota: () => apiFetch<KlmFinanceHarmonizationQuota>('/api/v1/finance/harmonization/quota'),
  financeHarmonizationRuns: () => apiFetch<KlmFinanceHarmonizationRun[]>('/api/v1/finance/harmonization/runs'),
  financeHarmonizationCreateRun: (params: { file: File; periodStart: string; periodEnd: string }) => {
    const formData = new FormData();
    formData.append('file', params.file);
    formData.append('period_start', params.periodStart);
    formData.append('period_end', params.periodEnd);
    return apiUpload<KlmFinanceHarmonizationRunDetail>('/api/v1/finance/harmonization/runs', formData);
  },
  financeHarmonizationRun: (runId: string) =>
    apiFetch<KlmFinanceHarmonizationRunDetail>(`/api/v1/finance/harmonization/runs/${encodeURIComponent(runId)}`),
  financeHarmonizationDecideItem: (runId: string, itemId: string, decision: KlmFinanceHarmonizationDecision) =>
    apiFetch<KlmFinanceHarmonizationItem>(
      `/api/v1/finance/harmonization/runs/${encodeURIComponent(runId)}/items/${encodeURIComponent(itemId)}/decision`,
      { method: 'POST', body: JSON.stringify({ decision }) },
    ),
  financeHarmonizationValidateRun: (runId: string) =>
    apiFetch<KlmFinanceHarmonizationRun>(`/api/v1/finance/harmonization/runs/${encodeURIComponent(runId)}/validate`, {
      method: 'POST',
    }),
  financeHarmonizationRejectRun: (runId: string) =>
    apiFetch<KlmFinanceHarmonizationRun>(`/api/v1/finance/harmonization/runs/${encodeURIComponent(runId)}/reject`, {
      method: 'POST',
    }),
  documentDownloadUrl: (documentId: string) => apiFetch<{ downloadUrl: string; expiresIn: number }>(`/api/v1/documents/${documentId}/download-url`),
  approvals: () => apiFetch<KlmApproval[]>('/api/v1/approvals'),
  createApproval: (payload: {
    matterId: string;
    approvalType: string;
    title: string;
    description?: string | null;
    priority?: KlmApproval['priority'];
    documentVersionUrl?: string | null;
  }) => apiFetch<KlmApproval>('/api/v1/approvals', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  resolveApproval: (approvalId: string, status: 'approved' | 'rejected' | 'changes_requested', resolutionReason: string) =>
    apiFetch<KlmApproval>(`/api/v1/approvals/${approvalId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ status, resolutionReason }),
    }),
  activity: () => apiFetch<KlmAuditEvent[]>('/api/v1/klm/activity?limit=250'),
  followUps: () => apiFetch<KlmFollowUp[]>('/api/v1/klm/follow-ups'),
  createFollowUp: (payload: { matterId: string; threadId: string; channel: KlmThread['primaryChannel']; dueAt: string; reason: string }) =>
    apiFetch<KlmFollowUp>('/api/v1/klm/follow-ups', {
      method: 'POST',
      body: JSON.stringify({ ...payload, maxAttempts: 8, policyKey: 'lead_never_unanswered', payload: { source: 'klm_spa' } }),
    }),
  updateFollowUp: (followUpId: string, payload: KlmFollowUpUpdate) =>
    apiFetch<KlmFollowUp>(`/api/v1/klm/follow-ups/${encodeURIComponent(followUpId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  submitKnowledgeArticleFeedback: (articleId: string, value: 'up' | 'down') =>
    apiFetch<KlmKnowledgeArticleFeedbackSummary>(
      `/api/v1/knowledge/articles/${encodeURIComponent(articleId)}/feedback`,
      {
        method: 'POST',
        body: JSON.stringify({ value }),
      },
    ),
  matter: (matterId: string) => apiFetch<KlmMatterDetail>(`/api/v1/matters/${matterId}`),
  /**
   * Status da qualificação do SDR de um Matter.
   *
   * O backend (`app/api/v1/endpoints/sdr.py`) responde com os schemas Pydantic
   * `SDRStatusResponse`/`SDRQualification`, que não declaram alias camelCase —
   * por isso as chaves permanecem em snake_case aqui. Quando o usuário não tem
   * `lead_data.view`, o payload volta mascarado por `mask_sdr_status`.
   */
  sdrStatus: (matterId: string) =>
    apiFetch<KlmSdrStatus>(`/api/v1/sdr/matters/${encodeURIComponent(matterId)}`),
  /** Força a requalificação do SDR. Exige a permissão `kanban.edit` no backend. */
  reprocessSdr: (matterId: string, reason?: string | null) =>
    apiFetch<KlmSdrReprocessResponse>(
      `/api/v1/sdr/matters/${encodeURIComponent(matterId)}/reprocess`,
      {
        method: 'POST',
        body: JSON.stringify({ matter_id: matterId, reason: reason ?? null }),
      },
    ),
  createClient: (payload: { name: string; email?: string | null; phone?: string | null }) =>
    apiFetch<{ id: string; name: string; email: string | null; phone: string | null }>('/api/v1/clients', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  createMatter: (payload: {
    clientId: string;
    demandType: string;
    leadSource?: string | null;
    columnId: string;
    practiceArea?: string;
    tags?: string[];
  }) => apiFetch<KlmMatterDetail>('/api/v1/matters', {
    method: 'POST',
    body: JSON.stringify({ practiceArea: 'trabalhista', tags: [], ...payload }),
  }),
  addMatterNote: (matterId: string, note: string) => addMatterNote(matterId, note),
  updateMatter: (matterId: string, payload: Partial<{
    demandType: string;
    leadSource: string | null;
    tags: string[];
    priority: string;
    urgency: string;
    classification: string;
    nextAction: string | null;
    nextDeadline: string | null;
    executiveSummary: string | null;
    strategyNotes: string | null;
    healthSubtheme: string | null;
    healthSubthemeCustom: string | null;
    healthObjectName: string | null;
    healthInsurer: string | null;
    healthDenialReason: string | null;
    medicalUrgencyHours: number | null;
    leadScore: number;
  }>) => apiFetch<KlmMatterDetail>(`/api/v1/matters/${matterId}`, {
    method: 'PATCH',
    headers: { 'X-KLM-Change-Source': 'manual_case_360' },
    body: JSON.stringify(payload),
  }),
  updateClient: (clientId: string, payload: Partial<{
    name: string;
    email: string | null;
    phone: string | null;
    cpf: string | null;
    employer: string | null;
    position: string | null;
    expectedUpdatedAt: string;
  }>, idempotencyKey?: string) => apiFetch<{ id: string; name: string; email: string | null; phone: string | null }>(`/api/v1/clients/${clientId}`, {
    method: 'PATCH',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    body: JSON.stringify(payload),
  }),
  updateCase360: (matterId: string, payload: {
    client?: Partial<{
      name: string;
      email: string | null;
      phone: string | null;
      cpf: string | null;
      employer: string | null;
      position: string | null;
    }>;
    attribution?: Partial<{
      source: string | null;
      medium: string | null;
      campaign: string | null;
      content: string | null;
      term: string | null;
      channel: string | null;
      notes: string | null;
    }>;
    matter: Partial<{
      demandType: string;
      practiceArea: string | null;
      legalAreaKey: string | null;
      legalProductKey: string | null;
      legalProductCustom: string | null;
      legalObjectName: string | null;
      leadSource: string | null;
      tags: string[];
      priority: string;
      urgency: string;
      classification: string;
      nextAction: string | null;
      nextDeadline: string | null;
      executiveSummary: string | null;
      strategyNotes: string | null;
      healthSubtheme: string | null;
      healthSubthemeCustom: string | null;
      healthObjectName: string | null;
      healthInsurer: string | null;
      healthDenialReason: string | null;
      medicalUrgencyHours: number | null;
      leadScore: number;
    }>;
  }) => apiFetch<KlmMatterDetail>(`/api/v1/matters/${matterId}/case-360`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  moveMatter: (matterId: string, payload: { columnId: string; microStatus?: string | null }) =>
    apiFetch<KlmMatterDetail>(`/api/v1/matters/${matterId}/move`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  updateFinancialProjection: (matterId: string, payload: { fixedFee: number; successPercentage: number; economicValueAnnual: number; probability: number; economicObligation: boolean; paymentMode?: 'initial_plus_success' | 'success_only'; successBaseType?: 'economic_obligation' | 'moral_damages'; moralDamagesEstimate?: number | null; injunctionMilestoneFee?: number; obligationFee?: number; reviewStatus: string }) =>
    apiFetch<KlmFinancialProjection>(`/api/v1/klm/matters/${matterId}/financial-projection`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  flows: () => apiFetch<KlmFlow[]>('/api/v1/flows'),
  flowTemplates: () => apiFetch<KlmFlow[]>('/api/v1/flows/templates'),
  flowNodeCatalog: () => apiFetch<KlmFlowNodeCatalog>('/api/v1/flows/catalog'),
  validateFlowGraphDraft: (payload: { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> }) =>
    apiFetch<KlmFlowGraphValidation>('/api/v1/flows/validate', { method: 'POST', body: JSON.stringify(payload) }),
  simulateFlowGraphDraft: (payload: { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> }) =>
    apiFetch<KlmFlowGraphValidation>('/api/v1/flows/simulate', { method: 'POST', body: JSON.stringify(payload) }),
  flowCopilotPlan: (payload: {
    instruction: string;
    flow_id?: string;
    practice_area?: string;
    current_nodes?: Array<Record<string, unknown>>;
    current_edges?: Array<Record<string, unknown>>;
    attachments?: KlmFlowCopilotAttachment[];
  }) => apiFetch<KlmFlowCopilotPlan>('/api/v1/flows/copilot/plan', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  operationalCopilotPlan: (payload: {
    domain: KlmOperationalCopilotDomain;
    instruction: string;
    context_key?: string;
    context_snapshot?: Record<string, unknown>;
    attachments?: KlmOperationalCopilotAttachment[];
  }) => apiFetch<KlmOperationalCopilotPlan>('/api/v1/operational-copilot/plan', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  flowRuns: (matterId?: string, includeTerminal = false) => {
    const params = new URLSearchParams();
    if (matterId) params.set('matter_id', matterId);
    if (includeTerminal) params.set('include_terminal', 'true');
    const suffix = params.size ? `?${params.toString()}` : '';
    return apiFetch<KlmFlowRun[]>(`/api/v1/flows/runs${suffix}`);
  },
  matterFlowJourneys: (matterId: string, limit = 20) =>
    apiFetch<KlmMatterFlowJourneyOverview>(
      `/api/v1/flows/journeys/matters/${matterId}?${new URLSearchParams({ limit: String(limit) })}`,
    ),
  dryRunFlow: (flowId: string, matterId: string, context: Record<string, unknown> = {}) =>
    apiFetch<KlmFlowDryRun>('/api/v1/flows/dry-runs', {
      method: 'POST',
      body: JSON.stringify({ flow_id: flowId, matter_id: matterId, context, max_steps: 100 }),
    }),
  createFlow: (payload: {
    name: string;
    description?: string;
    practice_area?: string;
    trigger?: string;
    trigger_config?: Record<string, unknown>;
    nodes?: Array<Record<string, unknown>>;
    edges?: Array<Record<string, unknown>>;
    is_active?: boolean;
  }) => apiFetch<KlmFlow>('/api/v1/flows', { method: 'POST', body: JSON.stringify(payload) }),
  updateFlow: (flowId: string, payload: {
    expected_revision: number;
    name?: string;
    description?: string;
    practice_area?: string;
    trigger?: string;
    trigger_config?: Record<string, unknown>;
    nodes?: Array<Record<string, unknown>>;
    edges?: Array<Record<string, unknown>>;
    is_active?: boolean;
  }) =>
    apiFetch<KlmFlow>(`/api/v1/flows/${flowId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  runFlow: (
    flowId: string,
    matterId: string,
    options: { threadId?: string; context?: Record<string, unknown> } = {},
  ) =>
    apiFetch<KlmFlowRun>(`/api/v1/flows/${flowId}/run`, {
      method: 'POST',
      body: JSON.stringify({
        matter_id: matterId,
        thread_id: options.threadId,
        context: { source: 'klm_spa', ...(options.context ?? {}) },
      }),
    }),
  pauseFlowRun: (runId: string, reason = 'Pausado pelo atendimento humano') =>
    apiFetch<KlmFlowRun>(`/api/v1/flows/runs/${runId}/pause?${new URLSearchParams({ reason })}`, {
      method: 'POST',
    }),
  resumeFlowRun: (runId: string) =>
    apiFetch<KlmFlowRun>(`/api/v1/flows/runs/${runId}/resume`, { method: 'POST' }),
  cancelFlowRun: (runId: string) =>
    apiFetch<KlmFlowRun>(`/api/v1/flows/runs/${runId}/cancel`, { method: 'POST' }),
  paidAcquisitionSyncStatus: () =>
    apiFetch<KlmPaidAcquisitionSyncStatus>('/api/v1/paid-acquisition/sync-status'),
  paidAcquisitionCampaigns: (status?: string) => {
    const suffix = status ? `?${new URLSearchParams({ status })}` : '';
    return apiFetch<KlmCampaignDraft[]>(`/api/v1/paid-acquisition/campaigns${suffix}`);
  },
  paidAcquisitionCampaign: (draftId: string) =>
    apiFetch<KlmCampaignDraft>(`/api/v1/paid-acquisition/campaigns/${draftId}`),
  prepareCampaignDraft: (payload: KlmPrepareCampaignDraftInput) =>
    apiFetch<KlmCampaignDraft>('/api/v1/paid-acquisition/campaigns', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  paidAcquisitionFunnelSummary: (params: { dateFrom?: string; dateTo?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.dateFrom) search.set('date_from', params.dateFrom);
    if (params.dateTo) search.set('date_to', params.dateTo);
    const suffix = search.toString() ? `?${search.toString()}` : '';
    return apiFetch<KlmPaidAcquisitionFunnelSummary>(
      `/api/v1/paid-acquisition/funnel-summary${suffix}`,
    );
  },
  googleDataManagerTrackingStatus: () =>
    apiFetch<KlmGoogleDataManagerTrackingStatus>(
      '/api/v1/integrations/tracking/google-data-manager/status',
    ),
  saveGoogleDataManagerTracking: (payload: KlmGoogleDataManagerTrackingUpsertInput) =>
    apiFetch<KlmGoogleDataManagerTrackingStatus>(
      '/api/v1/integrations/tracking/google-data-manager',
      { method: 'PUT', body: JSON.stringify(payload) },
    ),
  // Monitoramento processual (add-on) — /api/v1/processos.
  listMonitoredProcesses: (params: { status?: KlmMonitoredProcessStatus } = {}) => {
    const search = new URLSearchParams();
    if (params.status) search.set('status', params.status);
    const qs = search.toString();
    return apiFetch<KlmMonitoredProcess[]>(`/api/v1/processos${qs ? `?${qs}` : ''}`);
  },
  createMonitoredProcess: (payload: KlmMonitoredProcessCreateInput) =>
    apiFetch<KlmMonitoredProcess>('/api/v1/processos', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateMonitoredProcess: (processId: string, payload: KlmMonitoredProcessUpdateInput) =>
    apiFetch<KlmMonitoredProcess>(`/api/v1/processos/${encodeURIComponent(processId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  pauseMonitoredProcess: (processId: string) =>
    apiFetch<KlmMonitoredProcess>(`/api/v1/processos/${encodeURIComponent(processId)}/pausar`, {
      method: 'POST',
    }),
  resumeMonitoredProcess: (processId: string) =>
    apiFetch<KlmMonitoredProcess>(`/api/v1/processos/${encodeURIComponent(processId)}/retomar`, {
      method: 'POST',
    }),
  listProcessPublications: (
    params: {
      urgencia?: string;
      prazoStatus?: KlmDeadlineStatus;
      monitoredProcessId?: string;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.urgencia) search.set('urgencia', params.urgencia);
    if (params.prazoStatus) search.set('prazo_status', params.prazoStatus);
    if (params.monitoredProcessId) search.set('monitored_process_id', params.monitoredProcessId);
    const qs = search.toString();
    return apiFetch<KlmProcessPublication[]>(`/api/v1/processos/publicacoes${qs ? `?${qs}` : ''}`);
  },
  /** Agenda de prazos do módulo (GET /processos/prazos) — só publicações com
   * prazo calculado, mais urgente primeiro; `status` filtra por `KlmDeadlineStatus`. */
  listProcessDeadlines: (params: { status?: KlmDeadlineStatus } = {}) => {
    const search = new URLSearchParams();
    if (params.status) search.set('status', params.status);
    const qs = search.toString();
    return apiFetch<KlmProcessPublication[]>(`/api/v1/processos/prazos${qs ? `?${qs}` : ''}`);
  },
  confirmProcessPublicationDeadline: (publicationId: string, note?: string | null) =>
    apiFetch<KlmProcessPublication>(
      `/api/v1/processos/publicacoes/${encodeURIComponent(publicationId)}/confirmar-prazo`,
      { method: 'POST', body: JSON.stringify({ note: note ?? null }) },
    ),
  dismissProcessPublicationDeadline: (publicationId: string, note?: string | null) =>
    apiFetch<KlmProcessPublication>(
      `/api/v1/processos/publicacoes/${encodeURIComponent(publicationId)}/dispensar-prazo`,
      { method: 'POST', body: JSON.stringify({ note: note ?? null }) },
    ),
  // Controladoria jurídica — /api/v1/legal/controladoria (Onda 4, Sprint B UI).
  /** Painel diário agregado (11 categorias) — tela fundida Publicações + Controladoria. */
  controladoriaPainel: () =>
    apiFetch<KlmControladoriaPainel>('/api/v1/legal/controladoria/painel'),
  /** Regras de distribuição do workspace — resumo "distribuição" da tela fundida. */
  listDistributionRules: (params: { activeOnly?: boolean } = {}) => {
    const search = new URLSearchParams();
    if (params.activeOnly) search.set('active_only', 'true');
    const qs = search.toString();
    return apiFetch<KlmDistributionRuleListResult>(`/api/v1/legal/controladoria/distribution-rules${qs ? `?${qs}` : ''}`);
  },
  // Operação Massificada / Lotes — /api/v1/legal/batches (Onda 8, Sprint B
  // UI "lotes por cliente"). Ver app/api/v1/endpoints/legal_batches.py.
  /** Cria (ou devolve, se `externalRef` já existir) o lote recortado por
   * cliente OU por demanda — nunca os dois. */
  createLegalBatchFromContext: (payload: KlmLegalBatchCreateFromContextInput) =>
    apiFetch<KlmLegalBatch>('/api/v1/legal/batches/from-context', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getLegalBatch: (batchId: string) =>
    apiFetch<KlmLegalBatch>(`/api/v1/legal/batches/${encodeURIComponent(batchId)}`),
  getLegalBatchPanel: (batchId: string) =>
    apiFetch<KlmLegalBatchPanel>(`/api/v1/legal/batches/${encodeURIComponent(batchId)}/panel`),
  listLegalBatchItems: (batchId: string, state?: KlmLegalBatchItemState) =>
    apiFetch<KlmLegalBatchItem[]>(
      `/api/v1/legal/batches/${encodeURIComponent(batchId)}/items${state ? `?state=${encodeURIComponent(state)}` : ''}`,
    ),
  /** Processa os itens pendentes do lote — falha parcial nunca escondida
   * (cada item processado devolve seu próprio resultado individual). */
  processLegalBatch: (batchId: string) =>
    apiFetch<KlmLegalBatchProcessResult>(`/api/v1/legal/batches/${encodeURIComponent(batchId)}/process`, {
      method: 'POST',
    }),
  /** Único caminho para aprovar tese/prazo fatal/texto final/protocolo —
   * item a item, sempre humano. */
  approveLegalBatchItem: (batchId: string, itemId: string) =>
    apiFetch<KlmLegalBatchItem>(
      `/api/v1/legal/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/approve`,
      { method: 'POST' },
    ),
  /** "Aprovar itens elegíveis" — NUNCA cobre tese/prazo fatal/texto final/
   * protocolo; o serviço devolve os pulados em `skippedProtectedItemIds`,
   * que o painel exibe honestamente em vez de fingir 100% de aprovação. */
  approveAllLegalBatchItems: (batchId: string) =>
    apiFetch<KlmLegalBatchApproveAllResult>(`/api/v1/legal/batches/${encodeURIComponent(batchId)}/approve-all`, {
      method: 'POST',
    }),
  rollbackLegalBatchItem: (batchId: string, itemId: string, reason?: string | null) =>
    apiFetch<KlmLegalBatchItem>(
      `/api/v1/legal/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/rollback`,
      { method: 'POST', body: JSON.stringify({ reason: reason ?? null }) },
    ),
  // Demandas jurídicas — /api/v1/legal/demands (Onda 2/5, Sprint A UI).
  listLegalDemands: (
    params: {
      nature?: KlmLegalDemandNature;
      phase?: KlmLegalDemandPhase;
      matterId?: string;
      clientId?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.nature) search.set('nature', params.nature);
    if (params.phase) search.set('phase', params.phase);
    if (params.matterId) search.set('matter_id', params.matterId);
    if (params.clientId) search.set('client_id', params.clientId);
    if (params.limit != null) search.set('limit', String(params.limit));
    if (params.offset != null) search.set('offset', String(params.offset));
    const qs = search.toString();
    return apiFetch<KlmLegalDemandListResult>(`/api/v1/legal/demands${qs ? `?${qs}` : ''}`);
  },
  getLegalDemand: (demandId: string) =>
    apiFetch<KlmLegalDemand>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}`),
  createLegalDemand: (payload: KlmLegalDemandCreateInput) =>
    apiFetch<KlmLegalDemand>('/api/v1/legal/demands', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateLegalDemand: (demandId: string, payload: KlmLegalDemandUpdateInput) =>
    apiFetch<KlmLegalDemand>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  transitionLegalDemandPhase: (demandId: string, payload: KlmLegalDemandPhaseTransitionInput) =>
    apiFetch<KlmLegalDemand>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}/transition`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getLegalDemandCase360: (demandId: string) =>
    apiFetch<KlmCase360>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}/case-360`),
  listCaseParties: (demandId: string) =>
    apiFetch<KlmCasePartyListResult>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}/parties`),
  addCaseParty: (demandId: string, payload: KlmCasePartyCreateInput) =>
    apiFetch<KlmCaseParty>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}/parties`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getCaseStrategyMatrix: (demandId: string) =>
    apiFetch<KlmCaseStrategyMatrix>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}/strategy`),
  addCaseFact: (demandId: string, payload: KlmCaseFactCreateInput) =>
    apiFetch<KlmCaseFact>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}/strategy/facts`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  addCaseProofLink: (demandId: string, factId: string, payload: KlmCaseProofLinkCreateInput) =>
    apiFetch<KlmCaseProofLink>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/strategy/facts/${encodeURIComponent(factId)}/proofs`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  addCasePleading: (demandId: string, payload: KlmCasePleadingCreateInput) =>
    apiFetch<KlmCasePleading>(`/api/v1/legal/demands/${encodeURIComponent(demandId)}/strategy/pleadings`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  // Versões jurídicas (peças) de uma demanda — /api/v1/legal/demands/{id}/artifacts
  // (Onda 6, app/api/v1/endpoints/legal_editor.py). Usado pela aba Protocolo
  // (Sprint A) para escolher a versão aprovada a partir da qual montar a pasta.
  listDemandLegalArtifacts: (demandId: string, kind?: KlmLegalArtifactKind) =>
    apiFetch<KlmLegalArtifactVersion[]>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts${kind ? `?kind=${encodeURIComponent(kind)}` : ''}`,
    ),
  // Pasta de protocolo — /api/v1/legal/demands/{id}/protocol-folder (Onda 7,
  // app/api/v1/endpoints/protocol_folder.py). Só existem `build` (monta/
  // revalida) e `receipt` (registra recibo posterior); não existe rota de
  // "protocolar" — ver docstring do router no backend.
  buildProtocolFolder: (demandId: string, payload: KlmProtocolFolderBuildInput) =>
    apiFetch<KlmProtocolFolder>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/protocol-folder/build`,
      {
        method: 'POST',
        body: JSON.stringify({
          artifactId: payload.artifactId,
          tribunal: payload.tribunal ?? null,
          signatureConfirmed: payload.signatureConfirmed ?? false,
        }),
      },
    ),
  getProtocolFolder: (demandId: string, protocolFolderId: string) =>
    apiFetch<KlmProtocolFolder>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/protocol-folder/${encodeURIComponent(protocolFolderId)}`,
    ),
  downloadProtocolFolderZip: (demandId: string, protocolFolderId: string) =>
    apiDownload(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/protocol-folder/${encodeURIComponent(protocolFolderId)}/export.zip`,
    ),
  registerProtocolReceipt: (
    demandId: string,
    protocolFolderId: string,
    payload: KlmProtocolReceiptRegisterInput,
  ) =>
    apiFetch<KlmProtocolFolder>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/protocol-folder/${encodeURIComponent(protocolFolderId)}/receipt`,
      {
        method: 'POST',
        body: JSON.stringify({
          reference: payload.reference,
          note: payload.note ?? null,
          protocoledAt: payload.protocoledAt ?? null,
        }),
      },
    ),
  // Editor jurídico — versões/checklist/citações/revisão de uma demanda
  // (Onda 6, app/api/v1/endpoints/legal_editor.py + legal_demands.py,
  // Sprint A UI, aba "Peças"). Ver KlmLegalArtifactEditorVersion acima.
  listLegalArtifactEditorVersions: (demandId: string, kind?: KlmLegalArtifactKind) =>
    apiFetch<KlmLegalArtifactEditorVersion[]>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts${kind ? `?kind=${encodeURIComponent(kind)}` : ''}`,
    ),
  getLegalArtifactEditorVersion: (demandId: string, artifactId: string) =>
    apiFetch<KlmLegalArtifactEditorVersion>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}`,
    ),
  generateLegalArtifact: (demandId: string, payload: KlmLegalArtifactGenerateInput) =>
    apiFetch<KlmLegalArtifactEditorVersion>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/generate`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  generateLegalArtifactSection: (
    demandId: string,
    artifactId: string,
    payload: KlmLegalArtifactSectionGenerateInput,
  ) =>
    apiFetch<KlmLegalArtifactEditorVersion>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}/sections/generate`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  submitLegalArtifactEditorVersion: (demandId: string, artifactId: string) =>
    apiFetch<KlmLegalArtifactSubmitResult>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}/submit`,
      { method: 'POST' },
    ),
  getLegalArtifactChecklist: (demandId: string, artifactId: string) =>
    apiFetch<KlmLegalArtifactChecklist>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}/checklist`,
    ),
  exportLegalArtifactDocx: (demandId: string, artifactId: string) =>
    apiBlob(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}/export.docx`,
    ),
  reimportLegalArtifactDocx: (
    demandId: string,
    artifactId: string,
    expectedVersion: number,
    file: File,
  ) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload<KlmLegalArtifactEditorVersion>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}/reimport?expected_version=${expectedVersion}`,
      formData,
    );
  },
  verifyLegalArtifactCitations: (demandId: string, artifactId: string) =>
    apiFetch<KlmCitationVerificationRun>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}/verify-citations`,
      { method: 'POST' },
    ),
  reviewLegalArtifact: (demandId: string, artifactId: string) =>
    apiFetch<KlmLegalReviewRun>(
      `/api/v1/legal/demands/${encodeURIComponent(demandId)}/artifacts/${encodeURIComponent(artifactId)}/review`,
      { method: 'POST' },
    ),
  // DMS jurídico — documentos do caso — /api/v1/legal/documents (Onda 7,
  // app/api/v1/endpoints/legal_documents.py, Sprint A aba Documentos).
  // Escopo por caso = `matter_id` (o Kanban/`Matter` vinculado à demanda,
  // `KlmLegalDemand.matterId`) — o backend não guarda `legal_demand_id`
  // no asset, então uma demanda sem `matterId` ainda não tem DMS.
  uploadLegalDocument: (params: { file: File; matterId?: string | null; clientId?: string | null }) => {
    const formData = new FormData();
    formData.append('file', params.file);
    if (params.matterId) formData.append('matter_id', params.matterId);
    if (params.clientId) formData.append('client_id', params.clientId);
    return apiUpload<KlmLegalDocumentIngestResult>('/api/v1/legal/documents/upload', formData);
  },
  listLegalDocuments: (
    params: {
      matterId?: string;
      documentType?: KlmLegalDocumentType;
      q?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.matterId) search.set('matter_id', params.matterId);
    if (params.documentType) search.set('document_type', params.documentType);
    if (params.q) search.set('q', params.q);
    if (params.limit != null) search.set('limit', String(params.limit));
    if (params.offset != null) search.set('offset', String(params.offset));
    const qs = search.toString();
    return apiFetch<KlmLegalDocumentAsset[]>(`/api/v1/legal/documents${qs ? `?${qs}` : ''}`);
  },
  // Sem filtro por caso no backend (rota workspace-wide) — a UI filtra por
  // `matterId` no cliente para mostrar só a quarentena deste caso.
  listQuarantinedLegalDocuments: () => apiFetch<KlmLegalDocumentAsset[]>('/api/v1/legal/documents/quarantine'),
  getLegalDocument: (assetId: string) =>
    apiFetch<KlmLegalDocumentAsset>(`/api/v1/legal/documents/${encodeURIComponent(assetId)}`),
  listLegalDocumentReferences: (assetId: string) =>
    apiFetch<KlmLegalDocumentReference[]>(`/api/v1/legal/documents/${encodeURIComponent(assetId)}/references`),
  createLegalDocumentShareLink: (assetId: string, payload: KlmLegalDocumentShareLinkCreateInput) =>
    apiFetch<KlmLegalDocumentShareLinkIssued>(`/api/v1/legal/documents/${encodeURIComponent(assetId)}/share`, {
      method: 'POST',
      body: JSON.stringify({
        expiresAt: payload.expiresAt,
        watermarkEnabled: payload.watermarkEnabled ?? false,
        maxDownloads: payload.maxDownloads ?? null,
      }),
    }),
  listLegalDocumentShareDownloads: (shareLinkId: string) =>
    apiFetch<KlmLegalDocumentShareDownload[]>(
      `/api/v1/legal/documents/share-links/${encodeURIComponent(shareLinkId)}/downloads`,
    ),
  revokeLegalDocumentShareLink: (shareLinkId: string) =>
    apiFetch<KlmLegalDocumentShareLink>(
      `/api/v1/legal/documents/share-links/${encodeURIComponent(shareLinkId)}/revoke`,
      { method: 'POST' },
    ),
  // ── BI jurídico + capacidade — /api/v1/legal/bi/* e /api/v1/legal/capacity
  // (Onda 8, Sprint C UI). Contrato real: app/api/v1/endpoints/legal_bi.py +
  // app/schemas/legal_bi.py (CamelCaseSchema converte snake_case →
  // camelCase). Três blocos de BI (operação/jurídico/IA) aceitam o mesmo
  // recorte `period_from`/`period_to` opcional; capacidade não tem recorte
  // (é sempre o instante atual). Cada bloco devolve `honesty` — nunca
  // esconder o metadado nem inventar valor onde o backend disse
  // `missingFields`.
  getLegalBiOperacao: (params: { periodFrom?: string; periodTo?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.periodFrom) search.set('period_from', params.periodFrom);
    if (params.periodTo) search.set('period_to', params.periodTo);
    const qs = search.toString();
    return apiFetch<KlmOperationBi>(`/api/v1/legal/bi/operacao${qs ? `?${qs}` : ''}`);
  },
  getLegalBiJuridico: (params: { periodFrom?: string; periodTo?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.periodFrom) search.set('period_from', params.periodFrom);
    if (params.periodTo) search.set('period_to', params.periodTo);
    const qs = search.toString();
    return apiFetch<KlmLegalBiRead>(`/api/v1/legal/bi/juridico${qs ? `?${qs}` : ''}`);
  },
  getLegalBiIa: (params: { periodFrom?: string; periodTo?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.periodFrom) search.set('period_from', params.periodFrom);
    if (params.periodTo) search.set('period_to', params.periodTo);
    const qs = search.toString();
    return apiFetch<KlmAiBi>(`/api/v1/legal/bi/ia${qs ? `?${qs}` : ''}`);
  },
  getLegalCapacity: () => apiFetch<KlmCapacityReport>('/api/v1/legal/capacity'),
};

// ── BI jurídico + capacidade — tipos (Onda 8, Sprint C UI) ─────────────────
// Espelham 1:1 app/schemas/legal_bi.py — nenhum campo reformatado nem
// metadado de honestidade omitido. Ver KpisPage (abas Operação jurídica /
// Jurídico / IA / Capacidade) e src/klm/api/useLegalBi.ts.
export type KlmBiHonesty = {
  generatedAt: string;
  periodFrom: string;
  periodTo: string;
  sampleSize: number;
  origin: string;
  missingFields: string[];
};

export type KlmJourneyTimingStat = {
  toState: string;
  sampleSize: number;
  avgHours: number | null;
  medianHours: number | null;
};

export type KlmCountRow = { key: string; count: number };

export type KlmAmountRow = { key: string; totalValue: number; count: number };

export type KlmClientSlaRow = {
  clientId: string;
  clientName: string;
  noPrazo: number;
  atrasado: number;
  semSlaDefinido: number;
};

export type KlmNatureDurationRow = { nature: string; sampleSize: number; avgDays: number };

export type KlmOperationBi = {
  honesty: KlmBiHonesty;
  tempoPorMarco: KlmJourneyTimingStat[];
  tarefasD0: number;
  tarefasD1: number;
  tarefasAtrasadas: number;
  tarefasSemDataClassificavel: number;
  tarefasSemResponsavel: number;
  retrabalhoCorrecoesPendentes: number;
  minutasComMultiplasVersoes: number;
  processosSemMovimentacao: number;
  backlogPorResponsavel: KlmCountRow[];
  slaPorCliente: KlmClientSlaRow[];
  excecoesPorOrigem: KlmCountRow[];
};

export type KlmLegalBiRead = {
  honesty: KlmBiHonesty;
  estoquePorFase: KlmCountRow[];
  entradasNoPeriodo: number;
  saidasNoPeriodo: number;
  duracaoMediaDiasPorNatureza: KlmNatureDurationRow[];
  valoresPorFase: KlmAmountRow[];
  contingenciaPorRisco: KlmAmountRow[];
  acordosNoPeriodo: number;
  demandasComEstrategiaRegistrada: number;
  resultadoPorColunaKanban: KlmCountRow[];
  resultadoSemMatterVinculado: number;
  motivoEncerramentoRegistrado: number;
};

export type KlmAiBi = {
  honesty: KlmBiHonesty;
  passosAutomatizadosPorAgente: KlmCountRow[];
  execucoesTotais: number;
  execucoesFalhas: number;
  execucoesAguardandoHumano: number;
  intervencoesHumanasTotal: number;
  taxaIntervencaoHumana: number | null;
  decisoesAceitas: number;
  decisoesCorrigidas: number;
  decisoesRejeitadas: number;
  custoUsdPorAgente: KlmAmountRow[];
  tokensInputTotais: number;
  tokensOutputTotais: number;
};

export type KlmCapacityHonesty = {
  generatedAt: string;
  sampleSize: number;
  origin: string;
  missingFields: string[];
};

export type KlmResponsibleCapacity = {
  responsibleUserId: string;
  responsibleName: string;
  cargaDemandasAtivas: number;
  cargaTarefasAbertas: number;
  disponibilidadeConfigurada: boolean;
  horasSemanaConfiguradas: number | null;
  disponivelAgora: boolean;
  motivoDisponibilidade: string;
};

export type KlmGroupCapacity = { key: string; cargaDemandasAtivas: number };

export type KlmCapacityReport = {
  honesty: KlmCapacityHonesty;
  porResponsavel: KlmResponsibleCapacity[];
  porUnidade: KlmGroupCapacity[];
  porEquipe: KlmGroupCapacity[];
};
