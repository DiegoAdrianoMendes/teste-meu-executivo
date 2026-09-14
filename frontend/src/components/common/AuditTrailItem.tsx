import { getAgentById } from '@/lib/agent-display';
import type { AuditLog } from '@/types';
import { Bot, User } from 'lucide-react';

export function AuditTrailItem({ log }: { log: AuditLog }) {
  const agent = getAgentById(log.agentId);
  const isAi = agent?.type === 'ai';

  return (
    <div className="flex gap-3 group">
      <div className="flex flex-col items-center">
        <div className={`h-6 w-6 rounded-full flex items-center justify-center shrink-0 ${
          isAi ? 'bg-[hsl(var(--info))]/15 text-[hsl(var(--info))]' : 'bg-primary/10 text-primary'
        }`}>
          {isAi ? <Bot className="h-3 w-3" /> : <User className="h-3 w-3" />}
        </div>
        <div className="w-px flex-1 bg-border group-last:hidden" />
      </div>
      <div className="pb-4 min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold truncate">{agent?.name ?? 'Sistema'}</span>
          <span className="text-2xs text-muted-foreground shrink-0 tabular-nums">
            {new Date(log.timestamp).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">{log.description ?? log.action}</p>
      </div>
    </div>
  );
}
