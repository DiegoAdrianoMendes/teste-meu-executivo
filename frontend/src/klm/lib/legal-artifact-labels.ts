import type {
  KlmCitationVerificationStatus,
  KlmLegalArtifactKind,
  KlmLegalArtifactProtocolabilityState,
  KlmLegalReviewSeverity,
} from '@/klm/api';

/**
 * Rótulos e tons compartilhados pelo editor jurídico da demanda
 * (`LegalArtifactsTab`, Sprint A UI, aba "Peças"). Vocabulário fechado
 * espelhando `app/schemas/klm_operations.py` e
 * `app/models/legal_artifact_verification.py`.
 */

export const LEGAL_ARTIFACT_KIND_LABELS: Record<KlmLegalArtifactKind, string> = {
  petition: 'Petição',
  contract: 'Contrato de honorários',
  power_of_attorney: 'Procuração',
  proposal: 'Proposta',
  client_update: 'Comunicado ao cliente',
};

export const LEGAL_ARTIFACT_PROTOCOLABILITY_LABELS: Record<KlmLegalArtifactProtocolabilityState, string> = {
  bloqueado: 'Bloqueado',
  pendente: 'Pendente',
  protocolavel: 'Protocolável',
};

export const LEGAL_ARTIFACT_PROTOCOLABILITY_TONE: Record<
  KlmLegalArtifactProtocolabilityState,
  'green' | 'gold' | 'red'
> = {
  bloqueado: 'red',
  pendente: 'gold',
  protocolavel: 'green',
};

export const CITATION_STATUS_LABELS: Record<KlmCitationVerificationStatus, string> = {
  verificada: 'Verificada',
  nao_verificada: 'Não verificada',
  nao_localizada: 'Não localizada',
};

export const CITATION_STATUS_TONE: Record<KlmCitationVerificationStatus, 'green' | 'gold' | 'red'> = {
  verificada: 'green',
  nao_verificada: 'gold',
  nao_localizada: 'red',
};

export const LEGAL_REVIEW_SEVERITY_LABELS: Record<KlmLegalReviewSeverity, string> = {
  critica: 'Crítica',
  alta: 'Alta',
  media: 'Média',
  baixa: 'Baixa',
};

export const LEGAL_REVIEW_SEVERITY_TONE: Record<KlmLegalReviewSeverity, 'red' | 'gold' | 'blue' | 'muted'> = {
  critica: 'red',
  alta: 'gold',
  media: 'blue',
  baixa: 'muted',
};

export const LEGAL_ARTIFACT_STATUS_LABELS: Record<string, string> = {
  draft: 'Rascunho',
  review_pending: 'Em revisão',
  approved: 'Aprovado',
  rejected: 'Rejeitado',
  changes_requested: 'Ajustes solicitados',
  archived: 'Arquivado',
};
