export type FrontendContractIsoDateTime = string;
export type BrandCssVariable = `--${string}`;

export interface BrandAssets {
  logoLightUrl: string;
  logoDarkUrl: string;
  markUrl: string;
  faviconUrl: string;
  appleTouchIconUrl?: string | null;
  letterheadUrl?: string | null;
  signatureUrl?: string | null;
}

export interface BrandColors {
  primary: string;
  accent: string;
  foreground: string;
  background: string;
  sidebar: string;
  success: string;
  warning: string;
  danger: string;
}

export interface BrandMetadata {
  defaultTitle: string;
  titleTemplate: string;
  description: string;
  applicationName: string;
  author: string;
  themeColor: string;
}

export interface BrandContact {
  email: string;
  supportEmail: string;
  phone?: string | null;
  websiteUrl: string;
}

export interface BrandLegalIdentity {
  registeredName: string;
  registrationNumber?: string | null;
  barRegistration?: string | null;
  address?: string | null;
}

export interface BrandSocialProof {
  id: string;
  kind: 'testimonial' | 'metric' | 'credential' | 'case_result';
  label: string;
  value: string;
  approvedForExternalUse: boolean;
}

/** Workspace-scoped visual and institutional identity consumed by the SPA shell. */
export interface Brand {
  id: string;
  workspaceId: string;
  version: number;
  name: string;
  shortName: string;
  tagline: string;
  legalName: string;
  domain: string;
  defaultLocale: string;
  assets: BrandAssets;
  colors: BrandColors;
  cssVariables: Record<BrandCssVariable, string>;
  typography: {
    sansFamily: string;
    legalFamily: string;
  };
  metadata: BrandMetadata;
  contact: BrandContact;
  legal: BrandLegalIdentity;
  socialProof: BrandSocialProof[];
  updatedAt: FrontendContractIsoDateTime;
  updatedBy: string;
}

export type UserThemePreference = 'light' | 'dark' | 'system';
export type UserDensityPreference = 'compact' | 'comfortable' | 'spacious';

/** Preferences are isolated by both user and workspace, including local persistence. */
export interface UserPreferences {
  id: string;
  workspaceId: string;
  userId: string;
  version: number;
  locale: string;
  timezone: string;
  currency: string;
  dateFormat: string;
  timeFormat: '12h' | '24h';
  theme: UserThemePreference;
  density: UserDensityPreference;
  homePath: string;
  defaultFilters: Record<string, unknown>;
  notifications: {
    inApp: boolean;
    email: boolean;
    whatsapp: boolean;
    approvalRequests: boolean;
    slaWarnings: boolean;
  };
  dailyDigest: {
    enabled: boolean;
    hour: number;
  };
  accessibility: {
    reducedMotion: boolean;
    highContrast: boolean;
    fontScale: number;
  };
  updatedAt: FrontendContractIsoDateTime;
}

export type ProposalVersionStatus =
  | 'draft'
  | 'in_review'
  | 'approved'
  | 'published'
  | 'superseded'
  | 'archived';

export interface ProposalSection {
  id: string;
  kind: 'cover' | 'case' | 'pain' | 'timeline' | 'scope' | 'pricing' | 'proof' | 'cta' | 'custom';
  title: string;
  body: string;
  mediaUrl?: string | null;
}

export interface ProposalVersion {
  id: string;
  workspaceId: string;
  proposalId: string;
  matterId: string;
  version: number;
  status: ProposalVersionStatus;
  title: string;
  brandId: string;
  brandVersion: number;
  playbookVersionId: string;
  formats: Array<'pdf' | 'whatsapp_vertical'>;
  sections: ProposalSection[];
  pricing: {
    currency: string;
    initialFee: number;
    successFeePercentage?: number | null;
    intermediateFee?: number | null;
    paymentOptions: string[];
  };
  callToAction: {
    label: string;
    url?: string | null;
  };
  validUntil: FrontendContractIsoDateTime;
  approval: {
    required: boolean;
    status: 'not_requested' | 'pending' | 'approved' | 'rejected';
    decidedBy?: string | null;
    decidedAt?: FrontendContractIsoDateTime | null;
  };
  analytics: {
    views: number;
    lastViewedAt?: FrontendContractIsoDateTime | null;
    acceptedAt?: FrontendContractIsoDateTime | null;
  };
  checksum: string;
  createdAt: FrontendContractIsoDateTime;
  createdBy: string;
  publishedAt?: FrontendContractIsoDateTime | null;
}

export type AgentAutonomyMode =
  | 'human_only'
  | 'ai_triage_human_meeting'
  | 'ai_close_guarded'
  | 'ai_autonomous_supervised';

export type AgentPolicyDecision = 'allow' | 'require_human' | 'deny';

export interface AgentEffectivePolicyRule {
  id: string;
  label: string;
  decision: AgentPolicyDecision;
  reason: string;
  when: Record<string, unknown>;
  sourceRefs: string[];
}

/** Fully resolved policy used for one agent/workspace execution context. */
export interface AgentEffectivePolicy {
  id: string;
  workspaceId: string;
  agentKey: string;
  areaDefinitionId: string;
  playbookVersionId: string;
  channel: string;
  stage: string;
  autonomyMode: AgentAutonomyMode;
  allowedActions: string[];
  prohibitedActions: string[];
  tools: string[];
  hardGates: string[];
  financialLimits: {
    currency: string;
    minimumInitialFee?: number | null;
    maximumDiscountPercentage?: number | null;
  };
  rules: AgentEffectivePolicyRule[];
  fallback: {
    mode: 'human_queue' | 'human_takeover' | 'stop';
    queueKey?: string | null;
    reason: string;
  };
  sourceVersionIds: string[];
  computedAt: FrontendContractIsoDateTime;
}

export interface AreaFieldDefinition {
  key: string;
  label: string;
  type: 'text' | 'number' | 'currency' | 'date' | 'boolean' | 'select' | 'multiselect';
  required: boolean;
  options?: string[];
}

export interface AreaDocumentRequirement {
  key: string;
  label: string;
  required: boolean;
  acceptedMimeTypes: string[];
}

/** Plug-and-play legal area package. It references versioned assets instead of copying them. */
export interface AreaDefinition {
  id: string;
  workspaceId: string;
  key: string;
  version: number;
  status: 'draft' | 'active' | 'inactive' | 'archived';
  label: string;
  description: string;
  taxonomy: {
    themes: string[];
    subthemes: string[];
    urgencySignals: string[];
  };
  fields: AreaFieldDefinition[];
  documentRequirements: AreaDocumentRequirement[];
  qualificationCriteria: string[];
  agentKeys: string[];
  pipelineStageKeys: string[];
  defaultPlaybookId: string;
  objectionLibraryIds: string[];
  templateIds: string[];
  proposalTemplateIds: string[];
  pricingPolicyId: string;
  flowIds: string[];
  ragCollectionIds: string[];
  approvalRuleIds: string[];
  kpiKeys: string[];
  testScenarioIds: string[];
  createdAt: FrontendContractIsoDateTime;
  updatedAt: FrontendContractIsoDateTime;
}

export type PlaybookVersionStatus =
  | 'draft'
  | 'in_review'
  | 'approved'
  | 'published'
  | 'superseded'
  | 'archived';

export type PlaybookSectionKey =
  | 'identity_voice'
  | 'audience_intent'
  | 'discovery'
  | 'legal_triage'
  | 'qualification'
  | 'offer_fees'
  | 'objections'
  | 'objection_handling'
  | 'proposals'
  | 'follow_up'
  | 'handoff'
  | 'post_sale'
  | 'experimentation';

export interface PlaybookSection {
  key: PlaybookSectionKey;
  title: string;
  enabled: boolean;
  content: Record<string, unknown>;
}

export interface PlaybookVersion {
  id: string;
  workspaceId: string;
  playbookId: string;
  version: number;
  status: PlaybookVersionStatus;
  name: string;
  description: string;
  parentVersionId?: string | null;
  scope: {
    areaDefinitionIds: string[];
    channels: string[];
    stages: string[];
    agentKeys: string[];
    offerKeys: string[];
  };
  sections: PlaybookSection[];
  autonomyByStage: Array<{
    stage: string;
    mode: AgentAutonomyMode;
    hardGates: string[];
  }>;
  testScenarioIds: string[];
  changeSummary: string;
  checksum: string;
  createdAt: FrontendContractIsoDateTime;
  createdBy: string;
  approvedAt?: FrontendContractIsoDateTime | null;
  approvedBy?: string | null;
  publishedAt?: FrontendContractIsoDateTime | null;
}
