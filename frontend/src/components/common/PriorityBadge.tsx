import { Badge } from '@/components/ui/badge';
import type { Priority } from '@/types';
import { ArrowUp, ArrowRight, ArrowDown } from 'lucide-react';

const priorityConfig: Record<Priority, { label: string; className: string; icon: typeof ArrowUp }> = {
  alta: { label: 'Alta', className: 'bg-destructive/10 text-destructive border-destructive/30', icon: ArrowUp },
  media: { label: 'Média', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30', icon: ArrowRight },
  baixa: { label: 'Baixa', className: 'bg-muted text-muted-foreground border-muted-foreground/20', icon: ArrowDown },
};

export function PriorityBadge({ priority }: { priority: Priority }) {
  const config = priorityConfig[priority];
  const Icon = config.icon;
  return (
    <Badge variant="outline" className={`text-xs font-medium px-1.5 py-0 h-5 gap-0.5 ${config.className}`}>
      <Icon className="h-2.5 w-2.5" />
      {config.label}
    </Badge>
  );
}
