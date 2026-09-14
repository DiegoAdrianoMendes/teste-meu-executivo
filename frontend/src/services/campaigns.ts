import { apiDownload, apiFetch } from '@/services/http';
import { requireWhatsAppPhone } from '@/lib/whatsapp-phone';
import type {
  AudienceList,
  AudienceListImportResult,
  MetaTemplate,
  MetaTemplateAnalysis,
  OptInRecord,
  SuppressionEntry,
  TemplateCategory,
  TemplateComponentsInput,
  TemplateSubmitResult,
  UnifiedTemplate,
  UtilityTemplateCatalog,
  TemplatePickerResponse,
  TemplateVariablesResponse,
  TemplateQuickSendResult,
  WhatsAppSequenceStartPayload,
  WhatsAppSequenceStartResult,
  WhatsAppCommercialChargeSummary,
  WhatsAppCampaign,
} from '@/types';

export async function fetchAudienceLists(includeArchived = false): Promise<AudienceList[]> {
  const suffix = includeArchived ? '?includeArchived=true' : '';
  return apiFetch(`/api/v1/whatsapp/audience-lists${suffix}`);
}

export async function fetchAudienceList(audienceListId: string): Promise<AudienceList> {
  return apiFetch(`/api/v1/whatsapp/audience-lists/${audienceListId}`);
}

export async function createAudienceList(payload: {
  name: string;
  description?: string | null;
  source?: 'manual' | 'csv' | 'crm' | 'flow';
}): Promise<AudienceList> {
  return apiFetch('/api/v1/whatsapp/audience-lists', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAudienceList(
  audienceListId: string,
  payload: { name?: string; description?: string | null; status?: 'active' | 'archived' },
): Promise<AudienceList> {
  return apiFetch(`/api/v1/whatsapp/audience-lists/${audienceListId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function addAudienceListMember(
  audienceListId: string,
  payload: { phone: string; name?: string | null; email?: string | null },
): Promise<AudienceList> {
  return apiFetch(`/api/v1/whatsapp/audience-lists/${audienceListId}/members`, {
    method: 'POST',
    body: JSON.stringify({ ...payload, phone: requireWhatsAppPhone(payload.phone) }),
  });
}

export async function importAudienceListCsv(
  audienceListId: string,
  file: File,
): Promise<AudienceListImportResult> {
  return apiFetch(`/api/v1/whatsapp/audience-lists/${audienceListId}/import-csv`, {
    method: 'POST',
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'X-File-Name': file.name,
    },
    body: await file.text(),
  });
}

export async function downloadAudienceListCsv(audienceList: AudienceList): Promise<void> {
  const blob = await apiDownload(`/api/v1/whatsapp/audience-lists/${audienceList.id}/export.csv`);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${audienceList.name.replace(/[^a-zA-Z0-9_-]+/g, '_') || 'lista'}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function fetchCampaigns(): Promise<WhatsAppCampaign[]> {
  return apiFetch('/api/v1/whatsapp/campaigns');
}

export async function fetchMetaCommercialCharges(
  campaignId?: string | null,
): Promise<WhatsAppCommercialChargeSummary> {
  const suffix = campaignId ? `?campaignId=${encodeURIComponent(campaignId)}` : '';
  return apiFetch(`/api/v1/whatsapp/commercial-charges${suffix}`);
}

export async function fetchCampaign(campaignId: string): Promise<WhatsAppCampaign> {
  return apiFetch(`/api/v1/whatsapp/campaigns/${campaignId}`);
}

export async function createCampaign(payload: Record<string, unknown>): Promise<WhatsAppCampaign> {
  return apiFetch('/api/v1/whatsapp/campaigns', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateCampaign(campaignId: string, payload: Record<string, unknown>): Promise<WhatsAppCampaign> {
  return apiFetch(`/api/v1/whatsapp/campaigns/${campaignId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function launchCampaign(
  campaignId: string,
  payload: { dryRun?: boolean; sampleSize?: number },
): Promise<WhatsAppCampaign> {
  return apiFetch(`/api/v1/whatsapp/campaigns/${campaignId}/launch`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function pauseCampaign(campaignId: string): Promise<WhatsAppCampaign> {
  return apiFetch(`/api/v1/whatsapp/campaigns/${campaignId}/pause`, {
    method: 'POST',
  });
}

export async function resumeCampaign(campaignId: string): Promise<WhatsAppCampaign> {
  return apiFetch(`/api/v1/whatsapp/campaigns/${campaignId}/resume`, {
    method: 'POST',
  });
}

export async function stopCampaign(campaignId: string): Promise<WhatsAppCampaign> {
  return apiFetch(`/api/v1/whatsapp/campaigns/${campaignId}/stop`, {
    method: 'POST',
  });
}

export async function fetchMetaTemplates(connectionId?: string | null): Promise<MetaTemplate[]> {
  const suffix = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : '';
  return apiFetch(`/api/v1/whatsapp/meta-templates${suffix}`);
}

export async function fetchTemplatePicker(params: {
  threadId?: string | null;
  matterId?: string | null;
} = {}): Promise<TemplatePickerResponse> {
  const query = new URLSearchParams();
  if (params.threadId) query.set('threadId', params.threadId);
  if (params.matterId) query.set('matterId', params.matterId);
  const suffix = query.size ? `?${query.toString()}` : '';
  return apiFetch(`/api/v1/whatsapp/templates/picker${suffix}`);
}

export async function fetchTemplateVariables(params: {
  contactId?: string | null;
  matterId?: string | null;
} = {}): Promise<TemplateVariablesResponse> {
  const query = new URLSearchParams();
  if (params.contactId) query.set('contactId', params.contactId);
  if (params.matterId) query.set('matterId', params.matterId);
  const suffix = query.size ? `?${query.toString()}` : '';
  return apiFetch(`/api/v1/whatsapp/variables${suffix}`);
}

export async function quickSendWhatsAppTemplate(payload: {
  threadId: string;
  matterId?: string | null;
  templateId: string;
  idempotencyKey: string;
  headerParameters?: string[];
  bodyParameters?: string[];
  buttonParameters?: Array<string | { index: number; value: string }>;
  bindings?: Record<string, string>;
  connectionId?: string | null;
}): Promise<TemplateQuickSendResult> {
  return apiFetch('/api/v1/whatsapp/quick-send', {
    method: 'POST',
    headers: { 'Idempotency-Key': payload.idempotencyKey },
    body: JSON.stringify(payload),
  });
}

/** Start a reviewed, one-contact WhatsApp sequence.  This uses the same
 * campaign/flow path as the campaign assistant; it is not a second delivery
 * implementation. */
export async function startWhatsAppSequence(
  payload: WhatsAppSequenceStartPayload,
): Promise<WhatsAppSequenceStartResult> {
  return apiFetch('/api/v1/whatsapp/sequences/start', {
    method: 'POST',
    headers: { 'Idempotency-Key': payload.idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export async function analyzeMetaTemplateDraft(payload: {
  templateId?: string | null;
  name: string;
  categoryIntent: 'utility' | 'marketing' | 'authentication';
  body: string;
  sampleVariables: string;
  practiceArea?: string | null;
}): Promise<MetaTemplateAnalysis> {
  return apiFetch('/api/v1/whatsapp/meta-templates/analyze', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function syncMetaTemplates(connectionId?: string | null): Promise<MetaTemplate[]> {
  return apiFetch('/api/v1/whatsapp/meta-templates/sync', {
    method: 'POST',
    body: JSON.stringify({ connectionId: connectionId ?? null }),
  });
}

export async function fetchUnifiedTemplates(
  connectionId?: string | null,
  includeRemovedSource = false,
): Promise<UnifiedTemplate[]> {
  const params = new URLSearchParams();
  if (connectionId) params.set('connection_id', connectionId);
  if (includeRemovedSource) params.set('include_removed_source', 'true');
  const suffix = params.size ? `?${params.toString()}` : '';
  return apiFetch(`/api/v1/whatsapp/meta-templates/unified${suffix}`);
}

export async function createTemplateDraft(payload: {
  whatsappConnectionId: string;
  name: string;
  language: string;
  category: TemplateCategory;
  components: TemplateComponentsInput;
}): Promise<UnifiedTemplate> {
  return apiFetch('/api/v1/whatsapp/meta-template-drafts', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateTemplateDraft(
  templateId: string,
  payload: { category?: TemplateCategory; language?: string; components?: TemplateComponentsInput },
): Promise<UnifiedTemplate> {
  return apiFetch(`/api/v1/whatsapp/meta-template-drafts/${templateId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteTemplateDraft(templateId: string): Promise<void> {
  await apiFetch(`/api/v1/whatsapp/meta-template-drafts/${templateId}`, {
    method: 'DELETE',
  });
}

export async function removeLocalTemplateRecord(templateId: string): Promise<void> {
  await apiFetch(
    `/api/v1/whatsapp/meta-templates/${templateId}/local-record?confirm_history_loss=true`,
    { method: 'DELETE' },
  );
}

export async function submitTemplateDraft(templateId: string, oabRiskAcknowledged = false): Promise<TemplateSubmitResult> {
  return apiFetch(`/api/v1/whatsapp/meta-template-drafts/${templateId}/submit`, {
    method: 'POST',
    body: JSON.stringify({ oabRiskAcknowledged }),
  });
}

export async function fetchPendingTemplateInternalApprovals(): Promise<UnifiedTemplate[]> {
  return apiFetch('/api/v1/whatsapp/meta-template-drafts/pending-internal-approval');
}

export async function approveTemplateInternalSubmission(templateId: string): Promise<TemplateSubmitResult> {
  return apiFetch(`/api/v1/whatsapp/meta-template-drafts/${templateId}/approve-internal`, {
    method: 'POST',
  });
}

export async function cloneRejectedTemplateAsDraft(templateId: string): Promise<UnifiedTemplate> {
  return apiFetch(`/api/v1/whatsapp/meta-template-drafts/${templateId}/clone-rejected`, {
    method: 'POST',
  });
}

export async function fetchUtilityTemplateCatalog(connectionId?: string | null): Promise<UtilityTemplateCatalog> {
  const suffix = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : '';
  return apiFetch(`/api/v1/whatsapp/utility-template-library${suffix}`);
}

export async function retryUtilityTemplateCatalogItem(
  catalogKey: string,
  connectionId?: string | null,
): Promise<UtilityTemplateCatalog> {
  const suffix = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : '';
  return apiFetch(`/api/v1/whatsapp/utility-template-library/${encodeURIComponent(catalogKey)}/retry${suffix}`, {
    method: 'POST',
  });
}

export async function fetchOptIns(): Promise<OptInRecord[]> {
  return apiFetch('/api/v1/whatsapp/opt-ins');
}

export async function saveOptIn(payload: Record<string, unknown>): Promise<OptInRecord> {
  return apiFetch('/api/v1/whatsapp/opt-ins', {
    method: 'POST',
    body: JSON.stringify({ ...payload, phone: requireWhatsAppPhone(payload.phone) }),
  });
}

export async function fetchSuppressions(): Promise<SuppressionEntry[]> {
  return apiFetch('/api/v1/whatsapp/suppressions');
}

export async function saveSuppression(payload: Record<string, unknown>): Promise<SuppressionEntry> {
  return apiFetch('/api/v1/whatsapp/suppressions', {
    method: 'POST',
    body: JSON.stringify({ ...payload, phone: requireWhatsAppPhone(payload.phone) }),
  });
}
