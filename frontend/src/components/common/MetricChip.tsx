import { Badge } from '@/components/ui/badge';

export function MetricChip({ label, value, variant = 'default' }: { label: string; value: string | number; variant?: 'default' | 'success' | 'warning' | 'destructive' }) {
  const colorMap = {
    default: 'bg-muted text-foreground',
    success: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))]',
    warning: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))]',
    destructive: 'bg-destructive/10 text-destructive',
  };

  return (
    <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs ${colorMap[variant]}`}>
      <span className="font-medium">{label}:</span>
      <span className="font-bold tabular-nums">{value}</span>
    </div>
  );
}
