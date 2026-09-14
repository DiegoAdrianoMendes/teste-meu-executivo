import { useMemo, useState } from 'react';
import { Search, SlidersHorizontal, X } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useAppStore } from '@/store/app-context';
import { getAgentById } from '@/lib/agent-display';
import type { ClientClassification, KanbanColumnId, MicroStatus, Priority } from '@/types';
import { formatBRL } from '@/lib/formatters';

const priorities: { value: Priority; label: string }[] = [
  { value: 'alta', label: 'Alta' },
  { value: 'media', label: 'Média' },
  { value: 'baixa', label: 'Baixa' },
];

const columns: { value: KanbanColumnId; label: string }[] = [
  { value: 'entrada', label: 'Entrada' },
  { value: 'qualificacao', label: 'Qualificação' },
  { value: 'proposta_fechamento', label: 'Proposta e Fechamento' },
  { value: 'coleta_dados', label: 'Coleta de Infos e Docs' },
];

const classifications: { value: ClientClassification; label: string }[] = [
  { value: 'Premium', label: 'Premium' },
  { value: 'Forte', label: 'Forte' },
  { value: 'Bom', label: 'Bom' },
  { value: 'Frio', label: 'Frio' },
];

const microStatuses: { value: MicroStatus; label: string }[] = [
  { value: 'lead_novo', label: 'Lead novo' },
  { value: 'sem_resposta', label: 'Sem resposta' },
  { value: 'triagem_incompleta', label: 'Triagem incompleta' },
  { value: 'qualificado', label: 'Qualificado' },
  { value: 'proposta_enviada', label: 'Proposta enviada' },
  { value: 'assinatura_pendente', label: 'Assinatura pendente' },
  { value: 'pagamento_pendente', label: 'Pagamento pendente' },
  { value: 'aguardando_docs', label: 'Aguardando docs' },
  { value: 'docs_parciais', label: 'Docs parciais' },
  { value: 'docs_completos', label: 'Docs completos' },
  { value: 'checklist_final', label: 'Checklist final' },
];

function uniqueValues(values: Array<string | undefined>) {
  return Array.from(new Set(values.filter(Boolean))) as string[];
}

export function AdvancedFilterBar() {
  const { state, updateFilters, dispatch } = useAppStore();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const f = state.filters;

  const leadSources = useMemo(
    () => uniqueValues(state.cards.map((card) => card.leadSource)),
    [state.cards],
  );

  const closeIntentOptions = useMemo(
    () => uniqueValues(state.cards.map((card) => card.conversationInsight?.closeIntent)),
    [state.cards],
  );

  const documentReadinessOptions = useMemo(
    () => uniqueValues(state.cards.map((card) => card.conversationInsight?.documentReadiness)),
    [state.cards],
  );
  const agentOptions = useMemo(
    () => uniqueValues(
      state.cards.flatMap((card) => [
        card.responsibleHuman,
        card.currentAgent,
        ...card.assignedAgents,
      ]),
    ).map((id) => {
      const agent = getAgentById(id);
      return {
        id,
        label: agent?.name ?? id,
        type: agent?.type ?? 'human',
      };
    }),
    [state.cards],
  );

  const hasFilters = Boolean(
    f.search
    || f.priority.length
    || f.columnId.length
    || f.classification.length
    || f.status.length
    || f.agentId.length
    || f.leadSource.length
    || f.closeIntent.length
    || f.documentReadiness.length
    || f.isBlocked !== null
    || f.needsApproval !== null
    || f.hasUnreadMessages !== null
    || f.hasFinancialPending !== null
    || f.docScoreBelow !== null
    || f.updatedWithinDays !== null
    || f.minClaimValue !== null
    || f.maxClaimValue !== null
  );

  const activeChips: { label: string; onRemove: () => void }[] = [];
  if (f.priority.length) f.priority.forEach((value) => activeChips.push({ label: `Prioridade: ${value}`, onRemove: () => updateFilters({ priority: f.priority.filter((item) => item !== value) }) }));
  if (f.columnId.length) f.columnId.forEach((value) => activeChips.push({ label: `Fase: ${columns.find((item) => item.value === value)?.label ?? value}`, onRemove: () => updateFilters({ columnId: f.columnId.filter((item) => item !== value) }) }));
  if (f.classification.length) f.classification.forEach((value) => activeChips.push({ label: `Classe: ${value}`, onRemove: () => updateFilters({ classification: f.classification.filter((item) => item !== value) }) }));
  if (f.status.length) f.status.forEach((value) => activeChips.push({ label: `Status: ${microStatuses.find((item) => item.value === value)?.label ?? value}`, onRemove: () => updateFilters({ status: f.status.filter((item) => item !== value) }) }));
  if (f.agentId.length) f.agentId.forEach((value) => activeChips.push({ label: `Responsável: ${getAgentById(value)?.name ?? value}`, onRemove: () => updateFilters({ agentId: f.agentId.filter((item) => item !== value) }) }));
  if (f.leadSource.length) f.leadSource.forEach((value) => activeChips.push({ label: `Origem: ${value}`, onRemove: () => updateFilters({ leadSource: f.leadSource.filter((item) => item !== value) }) }));
  if (f.closeIntent.length) f.closeIntent.forEach((value) => activeChips.push({ label: `Fechamento: ${value}`, onRemove: () => updateFilters({ closeIntent: f.closeIntent.filter((item) => item !== value) }) }));
  if (f.documentReadiness.length) f.documentReadiness.forEach((value) => activeChips.push({ label: `Docs: ${value}`, onRemove: () => updateFilters({ documentReadiness: f.documentReadiness.filter((item) => item !== value) }) }));
  if (f.isBlocked === true) activeChips.push({ label: 'Bloqueado', onRemove: () => updateFilters({ isBlocked: null }) });
  if (f.needsApproval === true) activeChips.push({ label: 'Em aprovação', onRemove: () => updateFilters({ needsApproval: null }) });
  if (f.hasUnreadMessages === true) activeChips.push({ label: 'Com mensagens não lidas', onRemove: () => updateFilters({ hasUnreadMessages: null }) });
  if (f.hasFinancialPending === true) activeChips.push({ label: 'Financeiro pendente', onRemove: () => updateFilters({ hasFinancialPending: null }) });
  if (f.docScoreBelow !== null) activeChips.push({ label: `Docs < ${f.docScoreBelow}%`, onRemove: () => updateFilters({ docScoreBelow: null }) });
  if (f.updatedWithinDays !== null) activeChips.push({ label: `Atualizado em ${f.updatedWithinDays} dia(s)`, onRemove: () => updateFilters({ updatedWithinDays: null }) });
  if (f.minClaimValue !== null) activeChips.push({ label: `Valor min. ${formatBRL(f.minClaimValue)}`, onRemove: () => updateFilters({ minClaimValue: null }) });
  if (f.maxClaimValue !== null) activeChips.push({ label: `Valor max. ${formatBRL(f.maxClaimValue)}`, onRemove: () => updateFilters({ maxClaimValue: null }) });

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
        <div className="relative w-full lg:max-w-md">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Buscar cliente, empresa, origem, resumo ou conversa..."
            value={f.search}
            onChange={(event) => updateFilters({ search: event.target.value })}
            className="h-9 pl-8 text-sm"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select value={f.priority[0] ?? ''} onValueChange={(value) => updateFilters({ priority: value ? [value as Priority] : [] })}>
            <SelectTrigger className="h-9 w-[140px] text-xs"><SelectValue placeholder="Prioridade" /></SelectTrigger>
            <SelectContent>{priorities.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
          </Select>

          <Select value={f.columnId[0] ?? ''} onValueChange={(value) => updateFilters({ columnId: value ? [value as KanbanColumnId] : [] })}>
            <SelectTrigger className="h-9 w-[170px] text-xs"><SelectValue placeholder="Fase" /></SelectTrigger>
            <SelectContent>{columns.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
          </Select>

          <Button variant="outline" size="sm" className="h-9 gap-1 text-xs" onClick={() => setShowAdvanced((current) => !current)}>
            <SlidersHorizontal className="h-3.5 w-3.5" />
            Avançado
          </Button>

          {hasFilters && (
            <Button variant="ghost" size="sm" className="h-9 text-xs text-muted-foreground" onClick={() => dispatch({ type: 'RESET_FILTERS' })}>
              <X className="mr-1 h-3 w-3" />
              Limpar tudo
            </Button>
          )}
        </div>
      </div>

      {showAdvanced && (
        <div className="grid gap-4 rounded-lg border bg-muted/20 p-4 xl:grid-cols-4">
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Classificação</Label>
              <Select value={f.classification[0] ?? ''} onValueChange={(value) => updateFilters({ classification: value ? [value as ClientClassification] : [] })}>
                <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="Todas" /></SelectTrigger>
                <SelectContent>{classifications.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Microstatus</Label>
              <Select value={f.status[0] ?? ''} onValueChange={(value) => updateFilters({ status: value ? [value as MicroStatus] : [] })}>
                <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="Todos" /></SelectTrigger>
                <SelectContent>{microStatuses.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Responsável</Label>
              <Select value={f.agentId[0] ?? ''} onValueChange={(value) => updateFilters({ agentId: value ? [value] : [] })}>
                <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="Todos" /></SelectTrigger>
                <SelectContent>{agentOptions.filter((item) => item.type === 'human').map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Origem do lead</Label>
              <Select value={f.leadSource[0] ?? ''} onValueChange={(value) => updateFilters({ leadSource: value ? [value] : [] })}>
                <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="Todas" /></SelectTrigger>
                <SelectContent>
                  {leadSources.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Intenção de fechamento</Label>
              <Select value={f.closeIntent[0] ?? ''} onValueChange={(value) => updateFilters({ closeIntent: value ? [value] : [] })}>
                <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="Qualquer" /></SelectTrigger>
                <SelectContent>
                  {closeIntentOptions.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Prontidão documental</Label>
              <Select value={f.documentReadiness[0] ?? ''} onValueChange={(value) => updateFilters({ documentReadiness: value ? [value] : [] })}>
                <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="Qualquer" /></SelectTrigger>
                <SelectContent>
                  {documentReadinessOptions.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Atualizado nos últimos dias</Label>
              <Input
                type="number"
                min={1}
                value={f.updatedWithinDays ?? ''}
                onChange={(event) => updateFilters({ updatedWithinDays: event.target.value ? Number(event.target.value) : null })}
                className="h-9 text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Valor mínimo</Label>
              <Input
                type="number"
                min={0}
                value={f.minClaimValue ?? ''}
                onChange={(event) => updateFilters({ minClaimValue: event.target.value ? Number(event.target.value) : null })}
                className="h-9 text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Valor máximo</Label>
              <Input
                type="number"
                min={0}
                value={f.maxClaimValue ?? ''}
                onChange={(event) => updateFilters({ maxClaimValue: event.target.value ? Number(event.target.value) : null })}
                className="h-9 text-xs"
              />
            </div>
          </div>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Button variant={f.isBlocked === true ? 'default' : 'outline'} size="sm" className="h-9 text-xs" onClick={() => updateFilters({ isBlocked: f.isBlocked === true ? null : true })}>
                Bloqueados
              </Button>
              <Button variant={f.needsApproval === true ? 'default' : 'outline'} size="sm" className="h-9 text-xs" onClick={() => updateFilters({ needsApproval: f.needsApproval === true ? null : true })}>
                Aprovação
              </Button>
              <Button variant={f.hasUnreadMessages === true ? 'default' : 'outline'} size="sm" className="h-9 text-xs" onClick={() => updateFilters({ hasUnreadMessages: f.hasUnreadMessages === true ? null : true })}>
                Não lidas
              </Button>
              <Button variant={f.hasFinancialPending === true ? 'default' : 'outline'} size="sm" className="h-9 text-xs" onClick={() => updateFilters({ hasFinancialPending: f.hasFinancialPending === true ? null : true })}>
                Financeiro
              </Button>
            </div>
            <div className="space-y-1.5">
              <Label>Score documental abaixo de</Label>
              <Input
                type="number"
                min={0}
                max={100}
                value={f.docScoreBelow ?? ''}
                onChange={(event) => updateFilters({ docScoreBelow: event.target.value ? Number(event.target.value) : null })}
                className="h-9 text-xs"
              />
            </div>
          </div>
        </div>
      )}

      {activeChips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {activeChips.map((chip) => (
            <Badge key={chip.label} variant="secondary" className="cursor-pointer gap-1 pr-1 text-xs hover:bg-destructive/10" onClick={chip.onRemove}>
              {chip.label}
              <X className="h-2.5 w-2.5" />
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
