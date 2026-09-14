import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { X } from 'lucide-react';
import { useAppStore } from '@/store/app-context';
import type { Priority, KanbanColumnId } from '@/types';

const priorities: { value: Priority; label: string }[] = [
  { value: 'alta', label: 'Alta' },
  { value: 'media', label: 'Média' },
  { value: 'baixa', label: 'Baixa' },
];

const columns: { value: KanbanColumnId; label: string }[] = [
  { value: 'entrada', label: 'Entrada' },
  { value: 'qualificacao', label: 'Qualificação' },
  { value: 'proposta_fechamento', label: 'Proposta' },
  { value: 'coleta_dados', label: 'Coleta de Infos e Docs' },
];

export function FilterBar() {
  const { state, updateFilters, dispatch } = useAppStore();
  const hasFilters = state.filters.priority.length > 0 || state.filters.columnId.length > 0;

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Select
        value={state.filters.priority[0] ?? ''}
        onValueChange={v => updateFilters({ priority: v ? [v as Priority] : [] })}
      >
        <SelectTrigger className="h-8 text-xs w-[130px]">
          <SelectValue placeholder="Prioridade" />
        </SelectTrigger>
        <SelectContent>
          {priorities.map(p => (
            <SelectItem key={p.value} value={p.value} className="text-xs">{p.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={state.filters.columnId[0] ?? ''}
        onValueChange={v => updateFilters({ columnId: v ? [v as KanbanColumnId] : [] })}
      >
        <SelectTrigger className="h-8 text-xs w-[140px]">
          <SelectValue placeholder="Fase" />
        </SelectTrigger>
        <SelectContent>
          {columns.map(c => (
            <SelectItem key={c.value} value={c.value} className="text-xs">{c.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {hasFilters && (
        <Button variant="ghost" size="sm" className="h-8 text-xs text-muted-foreground" onClick={() => dispatch({ type: 'RESET_FILTERS' })}>
          <X className="h-3 w-3 mr-1" /> Limpar
        </Button>
      )}
    </div>
  );
}
