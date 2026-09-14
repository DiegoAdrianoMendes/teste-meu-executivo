import { BriefcaseBusiness, KanbanSquare, List } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';

type CrmView = 'case' | 'kanban' | 'list';

export function CrmViewNav({ active, matterId }: { active: CrmView; matterId?: string | null }) {
  const matterQuery = matterId ? `?matter=${encodeURIComponent(matterId)}` : '';
  const items = [
    { id: 'kanban' as const, label: 'Kanban', href: `/app/funil?view=kanban${matterId ? `&matter=${encodeURIComponent(matterId)}` : ''}`, icon: KanbanSquare },
    { id: 'list' as const, label: 'Lista', href: `/app/funil?view=tabela${matterId ? `&matter=${encodeURIComponent(matterId)}` : ''}`, icon: List },
    { id: 'case' as const, label: 'Caso 360', href: `/app/leads${matterQuery}`, icon: BriefcaseBusiness },
  ];

  return (
    <nav aria-label="Visões do CRM" className="flex w-fit max-w-full gap-1 overflow-x-auto rounded-md border border-border bg-secondary p-1">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <Link
            key={item.id}
            to={item.href}
            aria-current={active === item.id ? 'page' : undefined}
            className={cn(
              'inline-flex h-8 shrink-0 items-center gap-2 rounded px-3 text-xs font-medium transition-colors',
              active === item.id ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:bg-card/70 hover:text-foreground',
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
