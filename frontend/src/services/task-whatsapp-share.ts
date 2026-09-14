import { apiFetch } from '@/services/http';

export interface TaskWhatsAppContact {
  contactRef: string;
  label: string;
  maskedAddress: string | null;
  consentState: string;
  serviceWindowState: 'open' | 'closed';
  serviceWindowExpiresAt: string | null;
  sendEligible: boolean;
  gateReasons: string[];
}

interface DraftInput {
  contactRef: string;
  shareId: string;
  shareUrl: string;
  messageBody: string;
}

export interface TaskWhatsAppDraftPreview {
  contact: TaskWhatsAppContact;
  messageBody: string;
  previewFingerprint: string;
  deliveryMode: 'conversations_local_draft';
  externalActionsExecuted: 0;
  humanConfirmationRequired: true;
}

export interface TaskWhatsAppDraftPrepared {
  contact: TaskWhatsAppContact;
  messageBody: string;
  deliveryStatus: 'local_draft';
  deliveryMode: 'conversations_local_draft';
  externalActionsExecuted: 0;
  conversationPath: '/app/conversas';
  replayed: boolean;
}

export function listTaskWhatsAppContacts(taskId: string) {
  return apiFetch<TaskWhatsAppContact[]>(`/api/v1/tasks/${taskId}/external-shares/whatsapp/contacts`);
}

export function previewTaskWhatsAppDraft(taskId: string, input: DraftInput) {
  return apiFetch<TaskWhatsAppDraftPreview>(`/api/v1/tasks/${taskId}/external-shares/whatsapp/preview`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function prepareTaskWhatsAppDraft(taskId: string, input: DraftInput & { previewFingerprint: string; confirmed: true }) {
  return apiFetch<TaskWhatsAppDraftPrepared>(`/api/v1/tasks/${taskId}/external-shares/whatsapp/draft`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `task-whatsapp-draft:${crypto.randomUUID()}` },
    body: JSON.stringify(input),
  });
}
