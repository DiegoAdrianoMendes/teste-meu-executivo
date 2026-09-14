import type {
  KlmLegalDemandNature,
  KlmLegalDemandPhase,
  KlmLegalDemandRisk,
} from '@/klm/api';

/**
 * Rótulos e tons compartilhados pela Cadeia Jurídica — Demandas
 * (`DemandasPage`, `DemandaDetailPage`, `NewLegalDemandDialog`). Vocabulário
 * fechado espelhando `app/models/legal_demand.py` (Onda 2).
 */

export const LEGAL_DEMAND_NATURE_LABELS: Record<KlmLegalDemandNature, string> = {
  judicial: 'Judicial',
  administrativo: 'Administrativo',
  consultivo: 'Consultivo',
  contrato: 'Contrato',
  acordo: 'Acordo',
  servico_recorrente: 'Serviço recorrente',
  massificado: 'Massificado',
};

export const LEGAL_DEMAND_NATURES = Object.keys(LEGAL_DEMAND_NATURE_LABELS) as KlmLegalDemandNature[];

export const LEGAL_DEMAND_PHASE_LABELS: Record<KlmLegalDemandPhase, string> = {
  triagem: 'Triagem',
  em_andamento: 'Em andamento',
  suspenso: 'Suspenso',
  concluido: 'Concluído',
  encerrado: 'Encerrado',
};

export const LEGAL_DEMAND_PHASES = Object.keys(LEGAL_DEMAND_PHASE_LABELS) as KlmLegalDemandPhase[];

export const LEGAL_DEMAND_PHASE_TONE: Record<KlmLegalDemandPhase, 'gold' | 'blue' | 'green' | 'muted' | 'red'> = {
  triagem: 'gold',
  em_andamento: 'blue',
  suspenso: 'red',
  concluido: 'green',
  encerrado: 'muted',
};

export const LEGAL_DEMAND_RISK_LABELS: Record<KlmLegalDemandRisk, string> = {
  baixo: 'Baixo',
  medio: 'Médio',
  alto: 'Alto',
  critico: 'Crítico',
};

export const LEGAL_DEMAND_RISK_TONE: Record<KlmLegalDemandRisk, 'green' | 'gold' | 'red' | 'muted'> = {
  baixo: 'green',
  medio: 'gold',
  alto: 'red',
  critico: 'red',
};
