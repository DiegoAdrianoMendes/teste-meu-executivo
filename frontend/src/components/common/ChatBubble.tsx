import { getAgentById } from '@/lib/agent-display';
import type { ConversationItem } from '@/types';

export function ChatBubble({ item }: { item: ConversationItem }) {
  const agent = getAgentById(item.agentId);
  const isAi = agent?.type === 'ai';

  return (
    <div className={`flex gap-2 ${item.isInternal ? '' : 'flex-row-reverse'}`}>
      <div
        className="h-6 w-6 rounded-full flex items-center justify-center text-2xs font-semibold shrink-0 text-primary-foreground"
        style={{ backgroundColor: agent?.color ?? 'hsl(var(--muted))' }}
      >
        {agent?.initials ?? '?'}
      </div>
      <div className={`max-w-[80%] rounded-lg px-3 py-2 ${
        item.isInternal
          ? 'bg-muted text-foreground'
          : 'bg-primary/10 text-foreground'
      }`}>
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="text-sm font-semibold truncate">{agent?.name}</span>
          {isAi && <span className="text-2xs bg-[hsl(var(--info))]/20 text-[hsl(var(--info))] px-1 rounded shrink-0">IA</span>}
          {!item.isInternal && <span className="text-2xs bg-[hsl(var(--warning))]/20 text-[hsl(var(--warning))] px-1 rounded shrink-0">Externo</span>}
        </div>
        <p className="text-sm leading-relaxed">{item.message}</p>
        <span className="text-2xs text-muted-foreground mt-1 block tabular-nums">
          {new Date(item.timestamp).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
    </div>
  );
}
