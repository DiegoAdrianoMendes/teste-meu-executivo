import { Button } from '@/components/ui/button';
import { useAppStore } from '@/store/app-context';
import { Bookmark, AlertTriangle, BellRing, CircleDollarSign, Clock, FileX, Lock, ShieldCheck } from 'lucide-react';
import type { Filters } from '@/types';

interface Preset {
  label: string;
  shortLabel?: string;
  icon: React.ReactNode;
  filters: Partial<Filters>;
}

const presets: Preset[] = [
  { label: 'Urgentes', icon: <AlertTriangle className="h-3 w-3" />, filters: { urgency: ['urgente'] } },
  { label: 'Aguardando cliente', shortLabel: 'Aguardando', icon: <Clock className="h-3 w-3" />, filters: { status: ['sem_resposta', 'aguardando_docs'] } },
  { label: 'Docs incompletos', shortLabel: 'Docs', icon: <FileX className="h-3 w-3" />, filters: { docScoreBelow: 100 } },
  { label: 'Em aprovação', shortLabel: 'Aprovação', icon: <ShieldCheck className="h-3 w-3" />, filters: { needsApproval: true } },
  { label: 'Fechamento quente', shortLabel: 'Fechar', icon: <AlertTriangle className="h-3 w-3" />, filters: { closeIntent: ['high'] } },
  { label: 'Pagamento pendente', shortLabel: 'Financeiro', icon: <CircleDollarSign className="h-3 w-3" />, filters: { hasFinancialPending: true } },
  { label: 'Mensagens não lidas', shortLabel: 'Inbox', icon: <BellRing className="h-3 w-3" />, filters: { hasUnreadMessages: true } },
  { label: 'Sem resposta', icon: <Clock className="h-3 w-3" />, filters: { status: ['sem_resposta'] } },
  { label: 'Com bloqueio', shortLabel: 'Bloqueio', icon: <Lock className="h-3 w-3" />, filters: { isBlocked: true } },
];

export function SavedFilterPresets() {
  const { state, dispatch, updateFilters } = useAppStore();

  const isPresetActive = (preset: Preset) => {
    const f = state.filters;
    for (const [key, value] of Object.entries(preset.filters)) {
      const currentVal = f[key as keyof Filters];
      if (Array.isArray(value)) {
        if (!Array.isArray(currentVal) || JSON.stringify(currentVal.sort()) !== JSON.stringify([...value].sort())) return false;
      } else {
        if (currentVal !== value) return false;
      }
    }
    return true;
  };

  const applyPreset = (preset: Preset) => {
    if (isPresetActive(preset)) {
      dispatch({ type: 'RESET_FILTERS' });
    } else {
      dispatch({ type: 'RESET_FILTERS' });
      updateFilters(preset.filters);
    }
  };

  return (
    <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-thin pb-1 -mx-1 px-1">
      <Bookmark className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
      {presets.map(p => (
        <Button
          key={p.label}
          variant={isPresetActive(p) ? 'default' : 'outline'}
          size="sm"
          className="h-7 text-xs gap-1 px-2.5 shrink-0 whitespace-nowrap"
          onClick={() => applyPreset(p)}
        >
          {p.icon}
          <span className="sm:hidden">{p.shortLabel ?? p.label}</span>
          <span className="hidden sm:inline">{p.label}</span>
        </Button>
      ))}
    </div>
  );
}
