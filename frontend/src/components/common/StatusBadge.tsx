import { Badge } from '@/components/ui/badge';
import type { MicroStatus } from '@/types';

const statusConfig: Record<MicroStatus, { label: string; className: string }> = {
  lead_novo: { label: 'Lead Novo', className: 'bg-[hsl(var(--info))]/10 text-[hsl(var(--info))] border-[hsl(var(--info))]/30' },
  sem_resposta: { label: 'Sem Resposta', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30' },
  triagem_incompleta: { label: 'Triagem Incompleta', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30' },
  qualificado: { label: 'Qualificado', className: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))] border-[hsl(var(--success))]/30' },
  proposta_enviada: { label: 'Proposta Enviada', className: 'bg-primary/10 text-primary border-primary/30' },
  assinatura_pendente: { label: 'Assinatura Pendente', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30' },
  pagamento_pendente: { label: 'Pagamento Pendente', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30' },
  aguardando_docs: { label: 'Aguardando Docs', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30' },
  docs_parciais: { label: 'Docs Parciais', className: 'bg-[hsl(var(--info))]/10 text-[hsl(var(--info))] border-[hsl(var(--info))]/30' },
  docs_invalidos: { label: 'Docs Inválidos', className: 'bg-destructive/10 text-destructive border-destructive/30' },
  docs_completos: { label: 'Docs Completos', className: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))] border-[hsl(var(--success))]/30' },
  tese_preliminar: { label: 'Tese Preliminar', className: 'bg-primary/10 text-primary border-primary/30' },
  risco_probatorio: { label: 'Risco Probatório', className: 'bg-destructive/10 text-destructive border-destructive/30' },
  pesquisa_em_andamento: { label: 'Pesquisa em Andamento', className: 'bg-[hsl(var(--info))]/10 text-[hsl(var(--info))] border-[hsl(var(--info))]/30' },
  validacao_aprovada: { label: 'Validação Aprovada', className: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))] border-[hsl(var(--success))]/30' },
  validacao_reprovada: { label: 'Validação Reprovada', className: 'bg-destructive/10 text-destructive border-destructive/30' },
  minuta_em_elaboracao: { label: 'Minuta em Elaboração', className: 'bg-primary/10 text-primary border-primary/30' },
  revisao_interna: { label: 'Revisão Interna', className: 'bg-[hsl(var(--blocked))]/10 text-[hsl(var(--blocked))] border-[hsl(var(--blocked))]/30' },
  ajustes_solicitados: { label: 'Ajustes Solicitados', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30' },
  pdf_gerado: { label: 'PDF Gerado', className: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))] border-[hsl(var(--success))]/30' },
  checklist_final: { label: 'Checklist Final', className: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))] border-[hsl(var(--success))]/30' },
  migracao_pendente: { label: 'Migração Pendente', className: 'bg-[hsl(var(--info))]/10 text-[hsl(var(--info))] border-[hsl(var(--info))]/30' },
};

export function StatusBadge({ status }: { status: MicroStatus }) {
  const config = statusConfig[status] ?? { label: status, className: '' };
  return (
    <Badge variant="outline" className={`text-xs font-medium px-1.5 py-0 h-5 ${config.className}`}>
      {config.label}
    </Badge>
  );
}
