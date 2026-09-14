import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import type { ReportMetric } from '@/types';
import { formatBRL, formatDecimal } from '@/lib/formatters';

export function KPIStatCard({ metric }: { metric: ReportMetric }) {
  const formatValue = () => {
    if (metric.unit === 'BRL') return formatBRL(metric.value);
    if (metric.unit === '%') return `${formatDecimal(metric.value)}%`;
    return formatDecimal(metric.value);
  };

  const TrendIcon = metric.trend === 'up' ? TrendingUp : metric.trend === 'down' ? TrendingDown : Minus;
  const trendColor = metric.trend === 'up' ? 'text-[hsl(var(--success))]' : metric.trend === 'down' ? 'text-destructive' : 'text-muted-foreground';

  return (
    <Card className="border bg-card" style={{ boxShadow: 'var(--shadow-card)' }}>
      <CardContent className="p-3 sm:p-4">
        <div className="flex items-start justify-between">
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide truncate">{metric.label}</p>
            <p className="text-2xl font-bold mt-0.5 sm:mt-1 tracking-tight tabular-nums">{formatValue()}</p>
          </div>
          {metric.trend && (
            <div className={`flex items-center gap-0.5 ${trendColor}`}>
              <TrendIcon className="h-3.5 w-3.5" />
            </div>
          )}
        </div>
        {metric.previousValue !== undefined && (
          <p className="text-xs text-muted-foreground mt-1.5 tabular-nums">
            Anterior: {metric.unit === 'BRL' ? formatBRL(metric.previousValue) : formatDecimal(metric.previousValue)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
