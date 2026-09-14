import { Bot, Pause, Play, UserCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { AiMode } from '@/types';

interface AIControlBarProps {
  aiMode: AiMode;
  onToggle: (mode: AiMode) => void;
}

const modeConfig: Record<AiMode, { label: string; color: string; icon: React.ReactNode }> = {
  active: { label: 'IA Ativa', color: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))] border-[hsl(var(--success))]/30', icon: <Bot className="h-3 w-3" /> },
  paused: { label: 'IA Pausada', color: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30', icon: <Pause className="h-3 w-3" /> },
  off: { label: 'Manual', color: 'bg-muted text-muted-foreground border-border', icon: <UserCheck className="h-3 w-3" /> },
};

export function AIControlBar({ aiMode, onToggle }: AIControlBarProps) {
  const config = modeConfig[aiMode];

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 border-t bg-muted/30">
      <div className={`flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border ${config.color}`}>
        {config.icon}
        {config.label}
      </div>
      <div className="flex-1" />
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-2 text-xs gap-1"
        onClick={() => onToggle('active')}
        disabled={aiMode === 'active'}
      >
        <Play className="h-2.5 w-2.5" /> Ativar IA
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-2 text-xs gap-1"
        onClick={() => onToggle('paused')}
        disabled={aiMode === 'paused'}
      >
        <Pause className="h-2.5 w-2.5" /> Pausar
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-2 text-xs gap-1"
        onClick={() => onToggle('off')}
        disabled={aiMode === 'off'}
      >
        <UserCheck className="h-2.5 w-2.5" /> Manual
      </Button>
    </div>
  );
}
