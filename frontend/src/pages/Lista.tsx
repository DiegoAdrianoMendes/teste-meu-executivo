import { useState, useMemo } from 'react';
import { SectionHeader } from '@/components/common/SectionHeader';
import { AdvancedFilterBar } from '@/components/common/AdvancedFilterBar';
import { SavedFilterPresets } from '@/components/common/SavedFilterPresets';
import { EmptyState } from '@/components/common/EmptyState';
import { StatusBadge } from '@/components/common/StatusBadge';
import { PriorityBadge } from '@/components/common/PriorityBadge';
import { ClassificationChip } from '@/components/common/ClassificationChip';
import { StarsRating } from '@/components/common/StarsRating';
import { DocScoreBar } from '@/components/common/DocScoreBar';
import { useAppStore } from '@/store/app-context';
import { getAgentById } from '@/lib/agent-display';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { ArrowUpDown, ArrowUp, ArrowDown, Lock, ShieldCheck } from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';
import type { MatterCard } from '@/types';
import { formatBRL } from '@/lib/formatters';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import { EMPTY_PERSONALIZED_FILTER, matchesPersonalizedFilter, type PersonalizedFilterDefinition } from '@/lib/personalized-filters';
import { matterCardPersonalizedFields } from '@/lib/matter-card-filters';

type SortField = 'client' | 'starsRating' | 'classification' | 'demandType' | 'columnId' | 'microStatus' | 'priority' | 'docScore' | 'productionScore' | 'nextDeadline' | 'lastInteraction' | 'claimValue';
type SortDir = 'asc' | 'desc';

const columnLabels: { field: SortField; label: string; width?: string; hideMobile?: boolean }[] = [
  { field: 'client', label: 'Cliente', width: 'min-w-[180px]' },
  { field: 'starsRating', label: '★' },
  { field: 'classification', label: 'Class.', hideMobile: true },
  { field: 'demandType', label: 'Demanda', width: 'min-w-[140px]', hideMobile: true },
  { field: 'columnId', label: 'Fase', hideMobile: true },
  { field: 'microStatus', label: 'Status' },
  { field: 'priority', label: 'Prioridade' },
  { field: 'docScore', label: 'Doc', hideMobile: true },
  { field: 'productionScore', label: 'Prod', hideMobile: true },
  { field: 'nextDeadline', label: 'Prazo', hideMobile: true },
  { field: 'lastInteraction', label: 'Últ. Interação', hideMobile: true },
  { field: 'claimValue', label: 'Valor', hideMobile: true },
];

function getSortValue(card: MatterCard, field: SortField): string | number {
  switch (field) {
    case 'client': return card.client.name.toLowerCase();
    case 'starsRating': return card.starsRating;
    case 'classification': return card.classification;
    case 'demandType': return card.demandType.toLowerCase();
    case 'columnId': return card.columnId;
    case 'microStatus': return card.microStatus;
    case 'priority': return card.priority === 'alta' ? 0 : card.priority === 'media' ? 1 : 2;
    case 'docScore': return card.docScore;
    case 'productionScore': return card.productionScore;
    case 'nextDeadline': return card.nextDeadline ?? 'z';
    case 'lastInteraction': return card.lastInteraction;
    case 'claimValue': return card.claimValue ?? 0;
    default: return '';
  }
}

const columnTitleMap: Record<string, string> = {
  entrada: 'Entrada',
  qualificacao: 'Qualificação',
  proposta_fechamento: 'Proposta',
  coleta_dados: 'Coleta de Infos e Docs',
  triagem_juridica: 'Legado Jurídico',
  pesquisa_validacao: 'Legado Jurídico',
  redacao_peca: 'Legado Jurídico',
  aprovacao_pendente: 'Legado Jurídico',
  pronto_protocolo: 'Legado Jurídico',
  migrar_sistema: 'Legado',
};

export default function Lista() {
  const { getFilteredCards, selectCard, state } = useAppStore();
  const isMobile = useIsMobile();
  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);

  const standardFilteredCards = getFilteredCards();
  const personalizedFields = useMemo(() => matterCardPersonalizedFields(state.cards), [state.cards]);
  const cards = useMemo(
    () => standardFilteredCards.filter((card) => matchesPersonalizedFilter(card, personalizedFilter, personalizedFields)),
    [personalizedFields, personalizedFilter, standardFilteredCards],
  );
  const financialPendingCount = cards.filter((card) => card.microStatus === 'pagamento_pendente').length;
  const highCloseIntentCount = cards.filter((card) => card.conversationInsight?.closeIntent === 'high').length;
  const docsAtRiskCount = cards.filter((card) => (card.conversationInsight?.documentReadiness === 'low') || card.docScore < 70).length;

  const sorted = useMemo(() => {
    if (!sortField) return cards;
    return [...cards].sort((a, b) => {
      const va = getSortValue(a, sortField);
      const vb = getSortValue(b, sortField);
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [cards, sortField, sortDir]);

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('asc');
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ArrowUpDown className="h-3 w-3 opacity-30" />;
    return sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />;
  };

  const visibleCols = isMobile ? columnLabels.filter(c => !c.hideMobile) : columnLabels;

  return (
    <div className="space-y-4 max-w-full">
      <SectionHeader
        title="Lista de Casos"
        description={`${sorted.length} caso(s) encontrado(s)`}
      />

      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1"><SavedFilterPresets /></div>
        <PersonalizedFilterBuilder scope="matter-list" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} />
      </div>
      <AdvancedFilterBar />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'Resultados', value: sorted.length },
          { label: 'Fechamento quente', value: highCloseIntentCount },
          { label: 'Financeiro pendente', value: financialPendingCount },
          { label: 'Docs em risco', value: docsAtRiskCount },
        ].map((item) => (
          <Card key={item.label}>
            <CardContent className="py-4">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
              <p className="mt-1 text-2xl font-bold tabular-nums">{item.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {sorted.length === 0 ? (
        <EmptyState description="Ajuste os filtros para encontrar casos." />
      ) : isMobile ? (
        /* Mobile: Card-based list */
        <div className="space-y-2">
          {sorted.map(card => (
            <Card
              key={card.id}
              className={`cursor-pointer hover:bg-muted/50 transition-colors ${state.selectedCardId === card.id ? 'ring-1 ring-primary/20 bg-primary/5' : ''}`}
              style={{ boxShadow: 'var(--shadow-card)' }}
              onClick={() => selectCard(card.id)}
            >
              <CardContent className="p-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold truncate">{card.client.name}</p>
                    <p className="text-xs text-muted-foreground truncate">{card.client.employer}</p>
                  </div>
                  <div className="shrink-0"><StarsRating rating={card.starsRating} size="sm" /></div>
                </div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <StatusBadge status={card.microStatus} />
                  <PriorityBadge priority={card.priority} />
                  <ClassificationChip classification={card.classification} />
                  {card.isBlocked && <Lock className="h-3 w-3 text-destructive" />}
                  {card.needsApproval && <ShieldCheck className="h-3 w-3 text-amber-500" />}
                </div>
                <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span className="min-w-0 truncate">{card.demandType}</span>
                  <span className="shrink-0 tabular-nums">{card.claimValue ? formatBRL(card.claimValue) : '—'}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="border rounded-lg overflow-auto" style={{ boxShadow: 'var(--shadow-card)' }}>
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/50">
                {visibleCols.map(col => (
                  <TableHead
                    key={col.field}
                    className={`text-xs uppercase tracking-wider font-semibold cursor-pointer select-none hover:bg-muted/80 ${col.width ?? ''}`}
                    onClick={() => toggleSort(col.field)}
                  >
                    <div className="flex items-center gap-1">
                      {col.label}
                      <SortIcon field={col.field} />
                    </div>
                  </TableHead>
                ))}
                <TableHead className="text-xs uppercase tracking-wider font-semibold">Flags</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map(card => {
                const isSelected = state.selectedCardId === card.id;
                return (
                  <TableRow
                    key={card.id}
                    className={`cursor-pointer hover:bg-muted/50 transition-colors ${isSelected ? 'bg-primary/5 ring-1 ring-primary/20' : ''}`}
                    onClick={() => selectCard(card.id)}
                  >
                    <TableCell>
                      <div className="min-w-0">
                        <p className="text-sm font-semibold truncate max-w-[180px]">{card.client.name}</p>
                        <p className="text-xs text-muted-foreground truncate max-w-[160px]">{card.client.employer}</p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <StarsRating rating={card.starsRating} size="sm" />
                    </TableCell>
                    <TableCell>
                      <ClassificationChip classification={card.classification} />
                    </TableCell>
                    <TableCell>
                      <span className="text-xs text-muted-foreground truncate block max-w-[130px]">{card.demandType}</span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs whitespace-nowrap">{columnTitleMap[card.columnId] ?? card.columnId}</Badge>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={card.microStatus} />
                    </TableCell>
                    <TableCell>
                      <PriorityBadge priority={card.priority} />
                    </TableCell>
                    <TableCell>
                      <DocScoreBar score={card.docScore} compact />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <div className="w-10 h-1.5 rounded-full bg-muted overflow-hidden">
                          <div className="h-full rounded-full bg-primary/70" style={{ width: `${card.productionScore}%` }} />
                        </div>
                        <span className="text-xs text-muted-foreground tabular-nums">{card.productionScore}%</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs whitespace-nowrap tabular-nums">{card.nextDeadline ? new Date(card.nextDeadline).toLocaleDateString('pt-BR') : '—'}</span>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                        {new Date(card.lastInteraction).toLocaleDateString('pt-BR')}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="text-sm font-medium whitespace-nowrap tabular-nums">{card.claimValue ? formatBRL(card.claimValue) : '—'}</span>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        {card.needsApproval && (
                          <ShieldCheck className="h-3.5 w-3.5 text-amber-500" />
                        )}
                        {card.isBlocked && (
                          <Lock className="h-3.5 w-3.5 text-destructive" />
                        )}
                        {!card.needsApproval && !card.isBlocked && (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
