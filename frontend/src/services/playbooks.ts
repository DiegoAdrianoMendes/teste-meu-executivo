import { apiFetch } from '@/services/http';

export type PlaybookLifecycleState =
  | 'draft'
  | 'in_review'
  | 'approved'
  | 'published'
  | 'superseded';

export type PlaybookAutonomyMode =
  | 'human_only'
  | 'ai_triage_human_meeting'
  | 'ai_close_guarded'
  | 'ai_autonomous_supervised';

export type PlaybookSimulationStatus = 'passed' | 'failed' | 'needs_review';

export interface PlaybookScope {
  areas: string[];
  channels: string[];
  stages: string[];
  agents: string[];
  offers: string[];
}

export interface PlaybookVoicePolicy {
  positioning: string;
  tone: string;
  vocabulary: string[];
  forbiddenVocabulary: string[];
  formality: 'conversational' | 'balanced' | 'formal';
  maxMessageCharacters: number;
  greeting: string;
  closing: string;
  allowedClaims: string[];
  forbiddenClaims: string[];
  examples: Array<{ label: string; text: string }>;
}

export interface PlaybookAudiencePolicy {
  personas: string[];
  pains: string[];
  urgencySignals: string[];
  intentSignals: string[];
  eligibilitySignals: string[];
  exclusionSignals: string[];
}

export interface PlaybookDiscoveryQuestion {
  id: string;
  prompt: string;
  required: boolean;
  condition?: string | null;
  doNotRepeatWhen: string;
  confirmation: string;
}

export interface PlaybookTriagePolicy {
  subthemes: string[];
  requiredDocuments: string[];
  sufficiencyRule: string;
  legalConsultTriggers: string[];
  urgencyRules: string[];
  denialEvidence: string[];
  medicationFields: string[];
}

export interface PlaybookQualificationPolicy {
  scoreThreshold: number;
  scoreRules: Array<{ label: string; points: number; evidence: string }>;
  requiredFields: string[];
  nextActionRules: string[];
  disqualificationReasons: string[];
}

export interface PlaybookCommercialPolicy {
  minimumInitialFeeBrl: number;
  maximumInitialFeeBrl: number | null;
  successOnlyAllowed: boolean;
  successOnlyRule: string;
  intermediateFeeAllowed: boolean;
  obligationToDoFeeRule: string;
  successOnlyMoralDamagesPercentage: number;
  oncologyObligationFeeMinimumBrl: number;
  oncologyObligationFeeMaximumBrl: number;
  requireTreatmentCycleForMedicationValue: boolean;
  requireHumanReview: boolean;
  policyNote: string;
  discountLimitPercent: number;
  proposalValidityDays: number;
  humanApprovalAboveBrl: number;
  paymentOptions: string[];
}

export interface PlaybookObjection {
  id: string;
  label: string;
  diagnosis: string;
  confirmationQuestion: string;
  allowedResponse: string;
  proof: string;
  nextBestAction: string;
  maxAttempts: number;
  escalationCriteria: string;
}

export interface PlaybookProposalPolicy {
  desktopTemplate: string;
  whatsappTemplate: string;
  requiredBlocks: string[];
  callToAction: string;
  approvalRequired: boolean;
  analyticsEvents: string[];
}

export interface PlaybookBrandKit {
  brandName: string;
  logoObjectId: string | null;
  logoObjectVersion: number | null;
  logoFilename: string | null;
  primaryColor: string;
  secondaryColor: string;
  accentColor: string;
  textColor: string;
  backgroundColor: string;
  headingFont: string;
  bodyFont: string;
  letterheadEnabled: boolean;
  letterheadHeader: string;
  letterheadFooter: string;
  contactLines: string[];
  signatureName: string;
  signatureTitle: string;
  disclaimer: string;
  pageSize: 'A4' | 'letter';
}

export interface PlaybookDocumentVariable {
  key: string;
  label: string;
  source: 'brand' | 'client' | 'case' | 'conversation' | 'document' | 'financial' | 'meeting' | 'manual' | 'system';
  sourcePath: string;
  required: boolean;
  sensitive: boolean;
  allowAgentWrite: boolean;
  fallbackPolicy: 'hard_gate' | 'human_input' | 'omit';
  fallbackText?: string | null;
}

export interface PlaybookDocumentSection {
  id: string;
  title: string;
  body: string;
  required: boolean;
}

export interface PlaybookDocumentTemplate {
  id: string;
  name: string;
  documentType: 'proposal' | 'contract' | 'power_of_attorney' | 'petition' | 'client_update' | 'meeting_brief' | 'email' | 'whatsapp_pdf' | 'invoice' | 'other';
  channel: 'document' | 'whatsapp' | 'email' | 'internal';
  outputFormat: 'pdf' | 'docx' | 'google_doc' | 'html' | 'message';
  layout: 'slides_16_9' | 'whatsapp_vertical' | 'letterhead_a4' | 'document_a4' | 'message';
  templateVersion: number;
  status: 'draft' | 'approved' | 'archived';
  sections: PlaybookDocumentSection[];
  requiredVariables: string[];
  applicableStages: string[];
  applicableSubthemes: string[];
  agentKeys: string[];
  sourceObjectId: string | null;
  sourceObjectVersion: number | null;
  sourceFilename: string | null;
  approvalRequired: boolean;
  active: boolean;
}

export type PlaybookOfficeTemplateKey =
  | 'contrato_honorarios'
  | 'peticao_inicial'
  | 'procuracao'
  | 'proposta'
  | 'outro';

/** Modelo timbrado do próprio escritório: base exclusiva usada sempre que o
 * tipo de documento é gerado. Sem `documentObjectId`, a geração daquele tipo
 * fica bloqueada (nunca cai num modelo genérico). */
export interface PlaybookOfficeTemplate {
  key: PlaybookOfficeTemplateKey;
  label: string;
  documentObjectId: string | null;
  documentObjectVersion: number | null;
  documentFilename: string | null;
  formato: 'docx';
  obrigatorio: boolean;
}

export interface PlaybookFaqEntry {
  id: string;
  category: string;
  question: string;
  approvedAnswer: string;
  keywords: string[];
  sourceRefs: string[];
  allowedChannels: string[];
  applicableStages: string[];
  agentKeys: string[];
  confidenceThreshold: number;
  requiresHumanReview: boolean;
  active: boolean;
}

export interface PlaybookResponseTemplate {
  id: string;
  name: string;
  intent: string;
  channel: 'whatsapp' | 'instagram' | 'messenger' | 'email' | 'internal';
  stage: string;
  body: string;
  variables: string[];
  agentKeys: string[];
  approvalRequired: boolean;
  active: boolean;
}

export interface PlaybookCadenceStep {
  id: string;
  delayMinutes: number;
  channel: string;
  messageIntent: string;
  typingDelaySeconds: number;
  stopWhen: string[];
}

export interface PlaybookFollowUpPolicy {
  leadNeverUnanswered: boolean;
  businessHours: string;
  maxMessagesPerDay: number;
  antiSpamWindowMinutes: number;
  cadences: Record<string, PlaybookCadenceStep[]>;
}

export interface PlaybookHandoffPolicy {
  normalDispatcher: 'orquestrador';
  fallbackQueue: string;
  requiredSummaryFields: string[];
  destinations: Array<{ key: string; label: string; slaMinutes: number }>;
}

export interface PlaybookAgentContract {
  key: string;
  label: string;
  objective: string;
  parentAgentKey: string | null;
  independentControl: boolean;
  inputs: string[];
  tools: string[];
  permissions: string[];
  slaMinutes: number;
  successCriteria: string[];
  prohibitions: string[];
  handoffs: string[];
  fallback: string;
}

export interface PlaybookPolicy {
  autonomyMode: PlaybookAutonomyMode;
  stageModes: Record<string, PlaybookAutonomyMode>;
  hardGates: string[];
  killSwitchEnabled: boolean;
  minimumConfidence: number;
}

export interface PlaybookContent {
  identityVoice: PlaybookVoicePolicy;
  audienceIntent: PlaybookAudiencePolicy;
  discovery: PlaybookDiscoveryQuestion[];
  legalTriage: PlaybookTriagePolicy;
  qualification: PlaybookQualificationPolicy;
  commercial: PlaybookCommercialPolicy;
  objections: PlaybookObjection[];
  proposals: PlaybookProposalPolicy;
  brandKit: PlaybookBrandKit;
  documentTemplates: PlaybookDocumentTemplate[];
  officeTemplates: PlaybookOfficeTemplate[];
  faqs: PlaybookFaqEntry[];
  responseTemplates: PlaybookResponseTemplate[];
  documentVariables: PlaybookDocumentVariable[];
  followUp: PlaybookFollowUpPolicy;
  handoff: PlaybookHandoffPolicy;
  postSale: string[];
  experiments: Array<{
    id: string;
    name: string;
    variant: string;
    control: string;
    metric: string;
    stopCriteria: string;
  }>;
  agents: PlaybookAgentContract[];
  policy: PlaybookPolicy;
}

export interface PlaybookVersion {
  id: string;
  version: number;
  state: PlaybookLifecycleState;
  payloadHash: string;
  reason?: string | null;
  createdByUserId?: string | null;
  reviewedByUserId?: string | null;
  approvedByUserId?: string | null;
  publishedByUserId?: string | null;
  createdAt: string;
  reviewedAt?: string | null;
  approvedAt?: string | null;
  publishedAt?: string | null;
}

export interface PlaybookSummary {
  id: string;
  name: string;
  slug: string;
  description: string;
  state: PlaybookLifecycleState;
  currentVersion: number;
  publishedVersion?: number | null;
  etag: string;
  scope: PlaybookScope;
  autonomyMode: PlaybookAutonomyMode;
  structuralCoverage: number;
  simulationPassRate: number;
  simulations: number;
  updatedAt: string;
}

export interface PlaybookDetail extends PlaybookSummary {
  content: PlaybookContent;
  versions: PlaybookVersion[];
}

export interface PlaybookMetrics {
  simulations: number;
  passRate: number;
  structuralCoverage: number;
  handoffRate: number;
  autonomousResolutionRate: number;
  objectionsCovered: number;
  agentsCovered: number;
  lastEvaluatedAt?: string | null;
}

export interface PlaybookSimulation {
  id: string;
  playbookId: string;
  version: number;
  status: PlaybookSimulationStatus;
  scenario: string;
  customerMessage: string;
  response: string;
  nextAction: string;
  policyTrace: Array<{ rule: string; outcome: string; reason: string }>;
  sources: string[];
  autonomyMode: PlaybookAutonomyMode;
  humanApprovalRequired: boolean;
  externalActionsExecuted: number;
  createdAt: string;
}

export interface PlaybookDiffEntry {
  path: string;
  before: unknown;
  after: unknown;
  change: 'added' | 'removed' | 'changed';
}

export interface PlaybookDiff {
  fromVersion: number;
  toVersion: number;
  entries: PlaybookDiffEntry[];
}

export interface PlaybookCreateInput {
  name: string;
  description: string;
  scope: PlaybookScope;
  autonomyMode: PlaybookAutonomyMode;
  cloneFromId?: string | null;
}

export interface PlaybookDraftInput {
  name: string;
  description: string;
  scope: PlaybookScope;
  content: PlaybookContent;
  reason: string;
  expectedEtag: string;
}

export interface PlaybookSimulationInput {
  scenario: string;
  customerMessage: string;
  context: Record<string, unknown>;
  version?: number | null;
}

export function listPlaybooks(): Promise<PlaybookSummary[]> {
  return apiFetch('/api/v1/playbooks');
}

export function getPlaybook(playbookId: string): Promise<PlaybookDetail> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}`);
}

export function createPlaybook(input: PlaybookCreateInput): Promise<PlaybookDetail> {
  return apiFetch('/api/v1/playbooks', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function clonePlaybook(playbookId: string, name: string): Promise<PlaybookDetail> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/clone`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export function savePlaybookDraft(
  playbookId: string,
  input: PlaybookDraftInput,
): Promise<PlaybookDetail> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/draft`, {
    method: 'PUT',
    headers: { 'If-Match': `"${input.expectedEtag}"` },
    body: JSON.stringify(input),
  });
}

function lifecycleAction(
  playbookId: string,
  action: 'submit' | 'approve' | 'publish',
  reason: string,
): Promise<PlaybookDetail> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/${action}`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export const submitPlaybook = (playbookId: string, reason: string) =>
  lifecycleAction(playbookId, 'submit', reason);

export const approvePlaybook = (playbookId: string, reason: string) =>
  lifecycleAction(playbookId, 'approve', reason);

export const publishPlaybook = (playbookId: string, reason: string) =>
  lifecycleAction(playbookId, 'publish', reason);

export function rollbackPlaybook(
  playbookId: string,
  version: number,
  reason: string,
): Promise<PlaybookDetail> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/rollback/${version}`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export function getPlaybookDiff(
  playbookId: string,
  fromVersion: number,
  toVersion: number,
): Promise<PlaybookDiff> {
  const query = new URLSearchParams({
    from_version: String(fromVersion),
    to_version: String(toVersion),
  });
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/diff?${query}`);
}

export function getPlaybookMetrics(playbookId: string): Promise<PlaybookMetrics> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/metrics`);
}

export function listPlaybookSimulations(playbookId: string): Promise<PlaybookSimulation[]> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/simulations`);
}

export function simulatePlaybook(
  playbookId: string,
  input: PlaybookSimulationInput,
): Promise<PlaybookSimulation> {
  return apiFetch(`/api/v1/playbooks/${encodeURIComponent(playbookId)}/simulations`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getEffectivePlaybook(query: {
  area?: string;
  channel?: string;
  stage?: string;
  agent?: string;
  offer?: string;
}): Promise<PlaybookDetail> {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return apiFetch(`/api/v1/playbooks/effective?${params}`);
}
