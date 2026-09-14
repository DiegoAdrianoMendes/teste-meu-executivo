import type {
  AdminControlPlane,
  AdminRole,
  AdminUser,
  AiTokenPackageCatalog,
  AiGovernanceSettings,
  BrandingSettingsProfile,
  FinanceEntry,
  FinanceOverview,
  FinanceReminder,
  FinanceSettingsProfile,
  IntegrationCatalogProvider,
  IntegrationConnection,
  NotificationSettingsProfile,
  OpenApiSurface,
  PublicApiRequestLog,
  Meeting,
  MessageTemplateSettings,
  PracticeAreaTemplate,
  Permission,
  PublicApiScope,
  SecurityAuditSettingsProfile,
  WorkspacePublicApiToken,
  WorkspacePublicApiTokenCreateResponse,
  WorkspaceAutomationSettings,
  WorkspaceAccessPackage,
  WhatsAppAutomationSettings,
  WorkspaceOperationsSettings,
  WorkspacePlanSummary,
  WorkspaceAiCostPolicyUpdate,
  WorkspaceAiCredentialStatus,
  WorkspaceAiModelRouting,
  WorkspaceAiModelRoutingUpdate,
  WorkspaceAiTokenSummary,
  WorkspaceAiTokenPackageRequest,
  WorkspaceMembershipSummary,
  WorkspaceOnboardingState,
  WorkspaceOnboardingUpdate,
  WorkspaceSettingsProfile,
  WorkspaceSummary,
} from '@/types';
import { apiFetch } from '@/services/http';
import { createIdempotencyKey } from '@/services/identity';
import { withMeetingCreateIdempotency } from '@/services/meeting-idempotency';

interface RoleDefinitionResponse {
  id: string;
  label: string;
  description: string;
  permissions: Permission[];
}

interface UserResponse {
  id: string;
  name: string;
  email?: string;
  phone?: string | null;
  type?: 'human' | 'ai';
  role: string;
  initials: string;
  color: string;
  avatar?: string;
  isActive: boolean;
  isInternalStaff?: boolean;
}

interface TeamMemberResponse {
  membershipId: string;
  userId: string;
  name: string;
  email: string;
  phone?: string | null;
  initials: string;
  color: string;
  avatar?: string | null;
  workspaceRole: string;
  accessPackage?: string | null;
  status: 'active' | 'invited' | 'suspended';
  isActive: boolean;
  rolePermissions: Permission[];
  permissionGrants: Permission[];
  permissionDenials: Permission[];
  permissions: Permission[];
  canViewSensitiveLeadData: boolean;
  canEditSensitiveLeadData: boolean;
  createdAt: string;
  invitationId?: string | null;
  invitationStatus?: 'pending' | 'accepted' | 'revoked' | 'expired' | null;
  invitationExpiresAt?: string | null;
  invitationDeliveryId?: string | null;
  invitationDeliveryStatus?: 'pending' | 'sending' | 'sent' | 'failed' | 'blocked' | null;
  invitationDeliveryErrorCode?: string | null;
}

interface LocalInvitationDeliveryResponse {
  link: string;
}

interface WorkspaceSettingsResponse extends WorkspaceSummary {
  legalName?: string | null;
  description?: string | null;
  setupMode?: string;
  settings?: Record<string, unknown>;
}

interface WorkspaceAutomationSettingsResponse {
  onboardingMode: string;
  preset: WorkspaceAutomationSettings['preset'];
  availablePresets: WorkspaceAutomationSettings['availablePresets'];
  stagePolicies: WorkspaceAutomationSettings['stagePolicies'];
}

const roleColors: Record<string, string> = {
  workspace_owner: 'hsl(0 84% 60%)',
  workspace_admin: 'hsl(217 50% 22%)',
  legal_manager: 'hsl(262 50% 45%)',
  finance_manager: 'hsl(155 55% 32%)',
  operator: 'hsl(142 50% 35%)',
  viewer: 'hsl(38 60% 45%)',
};

export async function fetchAdminUsers(): Promise<UserResponse[]> {
  return apiFetch<UserResponse[]>('/api/v1/admin/users');
}

export async function fetchWorkspaceMemberships(): Promise<WorkspaceMembershipSummary[]> {
  return apiFetch<WorkspaceMembershipSummary[]>('/api/v1/admin/memberships');
}

export async function fetchTeamMembers(): Promise<AdminUser[]> {
  const members = await apiFetch<TeamMemberResponse[]>('/api/v1/admin/team-members');
  return members.map((member) => ({
    id: member.userId,
    membershipId: member.membershipId,
    name: member.name,
    initials: member.initials,
    email: member.email,
    phone: member.phone ?? null,
    type: 'human',
    role: member.workspaceRole,
    color: member.color,
    avatar: member.avatar ?? undefined,
    isActive: member.isActive,
    lastLogin: '-',
    roles: [member.workspaceRole],
    accessPackage: member.accessPackage ?? null,
    directPermissions: member.permissionGrants,
    deniedPermissions: member.permissionDenials,
    rolePermissions: member.rolePermissions,
    effectivePermissions: member.permissions,
    canViewSensitiveLeadData: member.canViewSensitiveLeadData,
    canEditSensitiveLeadData: member.canEditSensitiveLeadData,
    membershipStatus: member.status,
    invitationId: member.invitationId,
    invitationStatus: member.invitationStatus,
    invitationExpiresAt: member.invitationExpiresAt,
    invitationDeliveryId: member.invitationDeliveryId,
    invitationDeliveryStatus: member.invitationDeliveryStatus,
    invitationDeliveryErrorCode: member.invitationDeliveryErrorCode,
  }));
}

export async function fetchWorkspaceAccessPackages(): Promise<WorkspaceAccessPackage[]> {
  return apiFetch<WorkspaceAccessPackage[]>('/api/v1/admin/access-packages');
}

export async function fetchRoleCatalog(): Promise<AdminRole[]> {
  const roles = await apiFetch<RoleDefinitionResponse[]>('/api/v1/admin/roles');
  return roles.map((role) => ({
    id: role.id,
    name: role.label,
    description: role.description,
    permissions: role.permissions,
    color: roleColors[role.id] ?? 'hsl(220 14% 50%)',
  }));
}

export async function fetchWorkspaceSettings(): Promise<WorkspaceSettingsProfile> {
  return apiFetch<WorkspaceSettingsResponse>('/api/v1/admin/settings');
}

export async function fetchWorkspacePlanSummary(): Promise<WorkspacePlanSummary | null> {
  return apiFetch<WorkspacePlanSummary | null>('/api/v1/workspaces/plan-summary');
}

export async function fetchWorkspaceAiCosts(): Promise<WorkspaceAiTokenSummary> {
  return apiFetch<WorkspaceAiTokenSummary>('/api/v1/workspaces/ai-costs');
}

/** Pacote de acesso do próprio usuário logado — self-service, sem exigir permissão de admin. */
export async function fetchMyAccessPackage(): Promise<WorkspaceAccessPackage | null> {
  return apiFetch<WorkspaceAccessPackage | null>('/api/v1/workspaces/me/access-package');
}

export async function saveWorkspaceAiCostPolicy(
  payload: WorkspaceAiCostPolicyUpdate,
): Promise<WorkspaceAiTokenSummary> {
  return apiFetch<WorkspaceAiTokenSummary>('/api/v1/workspaces/ai-costs', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchWorkspaceAiModelRouting(): Promise<WorkspaceAiModelRouting> {
  return apiFetch<WorkspaceAiModelRouting>('/api/v1/workspaces/ai-models');
}

export async function saveWorkspaceAiModelRouting(
  payload: WorkspaceAiModelRoutingUpdate,
): Promise<WorkspaceAiModelRouting> {
  return apiFetch<WorkspaceAiModelRouting>('/api/v1/workspaces/ai-models', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchWorkspaceAiCredentials(): Promise<WorkspaceAiCredentialStatus[]> {
  return apiFetch<WorkspaceAiCredentialStatus[]>('/api/v1/workspaces/ai-credentials');
}

/** Write-only: a chave nunca volta em resposta — somente a máscara. */
export async function saveWorkspaceAiCredential(
  provider: string,
  apiKey: string,
): Promise<WorkspaceAiCredentialStatus> {
  return apiFetch<WorkspaceAiCredentialStatus>(
    `/api/v1/workspaces/ai-credentials/${encodeURIComponent(provider)}`,
    { method: 'PUT', body: JSON.stringify({ apiKey }) },
  );
}

export async function deleteWorkspaceAiCredential(
  provider: string,
): Promise<WorkspaceAiCredentialStatus> {
  return apiFetch<WorkspaceAiCredentialStatus>(
    `/api/v1/workspaces/ai-credentials/${encodeURIComponent(provider)}`,
    { method: 'DELETE' },
  );
}

export async function fetchWorkspaceAiTokenPackages(): Promise<AiTokenPackageCatalog> {
  return apiFetch<AiTokenPackageCatalog>('/api/v1/workspaces/ai-token-packages');
}

export async function requestWorkspaceAiTokenPackage(
  packageId: string,
): Promise<WorkspaceAiTokenPackageRequest> {
  return apiFetch<WorkspaceAiTokenPackageRequest>('/api/v1/workspaces/ai-token-packages/requests', {
    method: 'POST',
    body: JSON.stringify({
      packageId,
      idempotencyKey: createIdempotencyKey(),
    }),
  });
}

export async function saveWorkspaceSettings(payload: Partial<WorkspaceSettingsProfile>): Promise<WorkspaceSettingsProfile> {
  return apiFetch<WorkspaceSettingsResponse>('/api/v1/admin/settings', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchWorkspaceAutomationSettings(): Promise<WorkspaceAutomationSettings> {
  return apiFetch<WorkspaceAutomationSettingsResponse>('/api/v1/admin/settings/automation');
}

export async function saveWorkspaceAutomationSettings(payload: WorkspaceAutomationSettings): Promise<WorkspaceAutomationSettings> {
  return apiFetch<WorkspaceAutomationSettingsResponse>('/api/v1/admin/settings/automation', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminControlPlane(): Promise<AdminControlPlane> {
  return apiFetch<AdminControlPlane>('/api/v1/admin/settings/control-plane');
}

export async function saveAdminControlPlane(payload: {
  practiceAreas?: PracticeAreaTemplate[];
  aiGovernance?: AiGovernanceSettings;
  notifications?: NotificationSettingsProfile;
  branding?: BrandingSettingsProfile;
  securityAudit?: SecurityAuditSettingsProfile;
}): Promise<AdminControlPlane> {
  return apiFetch<AdminControlPlane>('/api/v1/admin/settings/control-plane', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchWorkspaceOperations(): Promise<WorkspaceOperationsSettings> {
  return apiFetch<WorkspaceOperationsSettings>('/api/v1/workspaces/settings/operations');
}

export async function saveWorkspaceOperations(payload: WorkspaceOperationsSettings): Promise<WorkspaceOperationsSettings> {
  return apiFetch<WorkspaceOperationsSettings>('/api/v1/workspaces/settings/operations', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchWorkspaceOnboarding(): Promise<WorkspaceOnboardingState> {
  return apiFetch<WorkspaceOnboardingState>('/api/v1/workspaces/onboarding');
}

export async function saveWorkspaceOnboarding(payload: WorkspaceOnboardingUpdate): Promise<WorkspaceOnboardingState> {
  return apiFetch<WorkspaceOnboardingState>('/api/v1/workspaces/onboarding', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchMeetings(): Promise<Meeting[]> {
  return apiFetch<Meeting[]>('/api/v1/meetings');
}

export async function fetchMessageTemplates(): Promise<MessageTemplateSettings> {
  return apiFetch<MessageTemplateSettings>('/api/v1/admin/message-templates');
}

export async function saveMessageTemplates(payload: MessageTemplateSettings): Promise<MessageTemplateSettings> {
  return apiFetch<MessageTemplateSettings>('/api/v1/admin/message-templates', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function fetchWhatsAppAutomationRules(): Promise<WhatsAppAutomationSettings> {
  return apiFetch<WhatsAppAutomationSettings>('/api/v1/admin/automation-rules');
}

export async function saveWhatsAppAutomationRules(payload: WhatsAppAutomationSettings): Promise<WhatsAppAutomationSettings> {
  return apiFetch<WhatsAppAutomationSettings>('/api/v1/admin/automation-rules', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function saveMeeting(payload: Partial<Meeting> & { id?: string }): Promise<Meeting> {
  if (payload.id) {
    if (!payload.version) throw new Error('Atualize a agenda antes de editar este compromisso.');
    const { id, version, ...changes } = payload;
    return apiFetch<Meeting>(`/api/v1/meetings/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ ...changes, expectedVersion: version }),
    });
  }

  return withMeetingCreateIdempotency(payload, (idempotencyKey) =>
    apiFetch<Meeting>('/api/v1/meetings', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deleteMeeting(meetingId: string, expectedVersion: number): Promise<void> {
  await apiFetch(`/api/v1/meetings/${meetingId}?expectedVersion=${encodeURIComponent(expectedVersion)}`, {
    method: 'DELETE',
  });
}

export async function fetchFinanceOverview(): Promise<FinanceOverview> {
  return apiFetch<FinanceOverview>('/api/v1/finance/overview');
}

export async function fetchFinanceSettings(): Promise<FinanceSettingsProfile> {
  return apiFetch<FinanceSettingsProfile>('/api/v1/finance/settings');
}

export async function saveFinanceSettings(payload: Partial<FinanceSettingsProfile>): Promise<FinanceSettingsProfile> {
  return apiFetch<FinanceSettingsProfile>('/api/v1/finance/settings', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchFinanceEntries(): Promise<FinanceEntry[]> {
  return apiFetch<FinanceEntry[]>('/api/v1/finance/entries');
}

export async function saveFinanceEntry(payload: Partial<FinanceEntry> & { id?: string }): Promise<FinanceEntry> {
  if (payload.id) {
    return apiFetch<FinanceEntry>(`/api/v1/finance/entries/${payload.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  return apiFetch<FinanceEntry>('/api/v1/finance/entries', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchFinanceReminders(): Promise<FinanceReminder[]> {
  return apiFetch<FinanceReminder[]>('/api/v1/finance/reminders');
}

export async function saveFinanceReminder(
  reminderId: string,
  payload: Partial<FinanceReminder>,
): Promise<FinanceReminder> {
  return apiFetch<FinanceReminder>(`/api/v1/finance/reminders/${reminderId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchFinanceIntegrations(): Promise<IntegrationConnection[]> {
  return apiFetch<IntegrationConnection[]>('/api/v1/finance/integrations');
}

export async function saveFinanceIntegration(
  payload: Partial<IntegrationConnection> & { id?: string },
): Promise<IntegrationConnection> {
  if (payload.id) {
    return apiFetch<IntegrationConnection>(`/api/v1/finance/integrations/${payload.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  return apiFetch<IntegrationConnection>('/api/v1/finance/integrations', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchIntegrationCatalog(): Promise<IntegrationCatalogProvider[]> {
  return apiFetch<IntegrationCatalogProvider[]>('/api/v1/integrations/catalog');
}

export async function fetchOpenApiSurface(): Promise<OpenApiSurface> {
  return apiFetch<OpenApiSurface>('/api/v1/integrations/open-api');
}

export async function fetchPublicApiTokens(): Promise<WorkspacePublicApiToken[]> {
  return apiFetch<WorkspacePublicApiToken[]>('/api/v1/integrations/public-api-tokens');
}

export async function fetchPublicApiLogs(): Promise<PublicApiRequestLog[]> {
  return apiFetch<PublicApiRequestLog[]>('/api/v1/integrations/public-api-tokens/logs');
}

export async function createPublicApiToken(payload: {
  label: string;
  scopes: PublicApiScope[];
  rateLimitPerMinute: number;
  expiresAt?: string | null;
}): Promise<WorkspacePublicApiTokenCreateResponse> {
  return apiFetch<WorkspacePublicApiTokenCreateResponse>('/api/v1/integrations/public-api-tokens', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function revokePublicApiToken(tokenId: string): Promise<void> {
  await apiFetch(`/api/v1/integrations/public-api-tokens/${tokenId}`, {
    method: 'DELETE',
  });
}

export function mapAdminUsers(
  users: UserResponse[],
  memberships: WorkspaceMembershipSummary[],
): AdminUser[] {
  const membershipsByUser = new Map<string, WorkspaceMembershipSummary>();
  memberships.forEach((membership) => membershipsByUser.set(membership.userId, membership));

  return users.map((user) => {
    const membership = membershipsByUser.get(user.id);
    return {
      id: user.id,
      membershipId: membership?.id,
      name: user.name,
      initials: user.initials,
      email: user.email ?? '',
      phone: user.phone ?? null,
      type: user.type ?? 'human',
      role: membership?.role ?? user.role,
      color: user.color,
      avatar: user.avatar,
      isActive: user.isActive,
      lastLogin: '-',
      roles: membership ? [membership.role] : [],
      accessPackage: membership?.accessPackage ?? null,
      directPermissions: membership?.permissionGrants ?? [],
      deniedPermissions: membership?.permissionDenials ?? [],
      rolePermissions: membership?.rolePermissions ?? [],
      effectivePermissions: membership?.permissions ?? [],
      canViewSensitiveLeadData: membership?.canViewSensitiveLeadData ?? false,
      canEditSensitiveLeadData: membership?.canEditSensitiveLeadData ?? false,
      membershipStatus: (membership?.status as AdminUser['membershipStatus']) ?? 'active',
      description: user.isInternalStaff ? 'Staff interno da plataforma' : undefined,
    };
  });
}

export async function saveAdminUser(
  user: AdminUser,
  _memberships?: WorkspaceMembershipSummary[],
): Promise<void> {
  const selectedWorkspaceRole = user.roles[0] ?? 'operator';
  const membershipPayload = {
    workspaceRole: selectedWorkspaceRole,
    accessPackage: user.accessPackage ?? null,
    permissionGrants: user.directPermissions,
    permissionDenials: user.deniedPermissions ?? [],
  };

  if (user.membershipId) {
    await apiFetch(`/api/v1/admin/team-members/${user.membershipId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        workspaceRole: membershipPayload.workspaceRole,
        accessPackage: membershipPayload.accessPackage,
        ...(user.membershipStatus === 'invited'
          ? {}
          : { status: user.isActive ? 'active' : 'suspended' }),
        permissionGrants: membershipPayload.permissionGrants,
        permissionDenials: membershipPayload.permissionDenials,
      }),
    });
    return;
  }

  await apiFetch<TeamMemberResponse>('/api/v1/admin/team-members', {
    method: 'POST',
    headers: { 'Idempotency-Key': createIdempotencyKey() },
    body: JSON.stringify({
      name: user.name,
      email: user.email,
      workspaceRole: membershipPayload.workspaceRole,
      accessPackage: membershipPayload.accessPackage,
      permissionGrants: membershipPayload.permissionGrants,
      permissionDenials: membershipPayload.permissionDenials,
    }),
  });
}

export async function resendTeamInvitation(invitationId: string): Promise<void> {
  await apiFetch<TeamMemberResponse>(`/api/v1/admin/team-members/invitations/${invitationId}/resend`, {
    method: 'POST',
    headers: { 'Idempotency-Key': createIdempotencyKey() },
  });
}

export async function revokeTeamInvitation(invitationId: string): Promise<void> {
  await apiFetch<TeamMemberResponse>(`/api/v1/admin/team-members/invitations/${invitationId}`, {
    method: 'DELETE',
  });
}

export async function fetchLocalInvitationLink(invitationId: string): Promise<string> {
  const delivery = await apiFetch<LocalInvitationDeliveryResponse>(
    `/api/v1/klm/local/invitations/${invitationId}/delivery`,
  );
  return delivery.link;
}

export async function fetchInvitationLink(invitationId: string): Promise<string> {
  const delivery = await apiFetch<LocalInvitationDeliveryResponse>(
    `/api/v1/admin/team-members/invitations/${invitationId}/link`,
  );
  return delivery.link;
}
