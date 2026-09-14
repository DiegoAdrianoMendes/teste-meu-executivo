interface DocScoreBarProps {
  score: number;
  compact?: boolean;
}

export function DocScoreBar({ score, compact = false }: DocScoreBarProps) {
  const color = score >= 80 ? 'hsl(var(--success))' : score >= 50 ? 'hsl(var(--warning))' : 'hsl(var(--destructive))';

  if (compact) {
    return (
      <div className="flex items-center gap-1">
        <div className="w-8 h-1.5 rounded-full bg-muted overflow-hidden">
          <div className="h-full rounded-full transition-all" style={{ width: `${score}%`, backgroundColor: color }} />
        </div>
        <span className="text-2xs text-muted-foreground tabular-nums">{score}%</span>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">Documentação</span>
        <span className="text-xs font-medium tabular-nums" style={{ color }}>{score}%</span>
      </div>
      <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${score}%`, backgroundColor: color }} />
      </div>
    </div>
  );
}
