import { Badge } from '@/components/ui/badge';
import type { ClientClassification } from '@/types';

const classConfig: Record<ClientClassification, { className: string }> = {
  Premium: { className: 'bg-[hsl(var(--warning))]/15 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/40 font-semibold' },
  Forte: { className: 'bg-[hsl(var(--info))]/15 text-[hsl(var(--info))] border-[hsl(var(--info))]/40 font-semibold' },
  Bom: { className: 'bg-[hsl(var(--success))]/15 text-[hsl(var(--success))] border-[hsl(var(--success))]/40' },
  Frio: { className: 'bg-muted text-muted-foreground border-border' },
};

export function ClassificationChip({ classification }: { classification: ClientClassification }) {
  const config = classConfig[classification];
  return (
    <Badge variant="outline" className={`text-xs px-1.5 py-0 h-5 ${config.className}`}>
      {classification}
    </Badge>
  );
}
