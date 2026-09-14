import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { getAgentById } from '@/lib/agent-display';

export function AgentAvatarGroup({ agentIds, max = 3 }: { agentIds: string[]; max?: number }) {
  const visible = agentIds.slice(0, max);
  const remaining = agentIds.length - max;

  return (
    <div className="flex items-center -space-x-1.5">
      {visible.map(id => {
        const agent = getAgentById(id);
        if (!agent) return null;
        return (
          <Tooltip key={id}>
            <TooltipTrigger asChild>
              <Avatar className="h-6 w-6 border-2 border-card cursor-default">
                <AvatarFallback
                  className="text-2xs font-semibold text-white"
                  style={{ backgroundColor: agent.color }}
                >
                  {agent.initials}
                </AvatarFallback>
              </Avatar>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
              <p className="font-medium">{agent.name}</p>
              <p className="text-muted-foreground text-xs">{agent.role} • {agent.type === 'ai' ? 'IA' : 'Humano'}</p>
            </TooltipContent>
          </Tooltip>
        );
      })}
      {remaining > 0 && (
        <Avatar className="h-6 w-6 border-2 border-card">
          <AvatarFallback className="text-2xs font-semibold bg-muted text-muted-foreground tabular-nums">
            +{remaining}
          </AvatarFallback>
        </Avatar>
      )}
    </div>
  );
}
