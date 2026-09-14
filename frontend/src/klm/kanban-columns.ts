import type { KanbanColumn } from '@/types';

export const KLM_KANBAN_COLUMNS: KanbanColumn[] = [
  { id: 'entrada', title: 'Entrada', color: 'hsl(220 9% 46%)', order: 0 },
  { id: 'qualificacao', title: 'Qualificação', color: 'hsl(199 89% 48%)', order: 1 },
  {
    id: 'proposta_fechamento',
    title: 'Proposta / Fechamento',
    color: 'hsl(262 50% 45%)',
    order: 2,
  },
  {
    id: 'coleta_dados',
    title: 'Coleta de Infos e Docs',
    color: 'hsl(38 92% 50%)',
    order: 3,
  },
  {
    id: 'triagem_juridica',
    title: 'Triagem Jurídica (Legado)',
    color: 'hsl(142 25% 55%)',
    order: 4,
  },
  {
    id: 'pesquisa_validacao',
    title: 'Pesquisa e Validação (Legado)',
    color: 'hsl(280 25% 60%)',
    order: 5,
  },
  {
    id: 'redacao_peca',
    title: 'Redação da Peça (Legado)',
    color: 'hsl(217 20% 45%)',
    order: 6,
  },
  {
    id: 'aprovacao_pendente',
    title: 'Aprovação Pendente (Legado)',
    color: 'hsl(0 35% 60%)',
    order: 7,
  },
  {
    id: 'pronto_protocolo',
    title: 'Pronto para Protocolo (Legado)',
    color: 'hsl(160 20% 50%)',
    order: 8,
  },
  {
    id: 'migrar_sistema',
    title: 'Migrar para Sistema (Legado)',
    color: 'hsl(220 14% 70%)',
    order: 9,
  },
];
