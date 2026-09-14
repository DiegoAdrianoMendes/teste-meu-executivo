import { Card, CardContent } from '@/components/ui/card';
import type { MatterCard as MatterCardType } from '@/types';
import { StatusBadge } from '@/components/common/StatusBadge';
import { StarsRating } from '@/components/common/StarsRating';
import { ClassificationChip } from '@/components/common/ClassificationChip';
import { Calendar, FileText, MessageSquare, CheckSquare, Lock, AlertTriangle, Clock, User, Bot } from 'lucide-react';
import { useAppStore } from '@/store/app-context';
import { useToast } from '@/hooks/use-toast';
import { getAgentById } from '@/lib/agent-display';

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

export function MatterCard({ card }: { card: MatterCardType }) {
  const { selectCard, updateCardField, can } = useAppStore();
  const { toast } = useToast();
  const canEditKanban = can('kanban', 'edit');

  const daysUntilDeadline = card.nextDeadline
    ? Math.ceil((new Date(card.nextDeadline).getTime() - Date.now()) / (1000 * 60 * 60 * 24))
    : null;

  const deadlineUrgent = daysUntilDeadline !== null && daysUntilDeadline <= 3;
  const responsible = getAgentById(card.responsibleHuman);
  const agent = getAgentById(card.currentAgent);

  return (
    <Card
      className={`cursor-pointer border bg-card hover:border-primary/30 transition-all hover:shadow-md group ${
        card.isBlocked ? 'border-l-2 border-l-[hsl(var(--destructive))]' : ''
      } ${card.needsApproval ? 'border-l-2 border-l-[hsl(var(--warning))]' : ''}`}
      style={{ boxShadow: 'var(--shadow-card)' }}
      onClick={() => selectCard(card.id)}
    >
      <CardContent className="p-2.5 space-y-1.5">
        {/* Row 1: Stars + Classification + Flags */}
        <div className="flex items-center justify-between gap-1">
          <div className="flex items-center gap-1.5">
            <span className="inline-flex" title={canEditKanban ? undefined : 'Permissão kanban.edit necessária'}>
            <StarsRating
              rating={card.starsRating}
              interactive={canEditKanban}
              onRate={(r) => {
                void Promise.resolve(updateCardField(card.id, 'starsRating', r)).catch((error: unknown) => {
                  toast({
                    title: 'A avaliação não foi salva.',
                    description: error instanceof Error && error.message ? error.message : 'Tente novamente em instantes.',
                    variant: 'destructive',
                  });
                });
              }}
            />
            </span>
            <ClassificationChip classification={card.classification} />
          </div>
          <div className="flex items-center gap-1">
            {card.isBlocked && (
              <div className="h-4 w-4 rounded flex items-center justify-center bg-destructive/10">
                <Lock className="h-2.5 w-2.5 text-destructive" />
              </div>
            )}
            {card.needsApproval && (
              <div className="h-4 w-4 rounded flex items-center justify-center bg-[hsl(var(--warning))]/10">
                <AlertTriangle className="h-2.5 w-2.5 text-[hsl(var(--warning))]" />
              </div>
            )}
          </div>
        </div>

        {/* Row 2: Client name */}
        <p className="text-sm font-semibold truncate group-hover:text-primary transition-colors leading-tight">
          {card.client.name}
        </p>

        {/* Row 3: Demand type */}
        <p className="text-xs text-muted-foreground truncate leading-tight">{card.demandType}</p>

        {/* Row 4: Status + Next action */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <StatusBadge status={card.microStatus} />
        </div>
        <p className="text-xs text-muted-foreground truncate">
          <span className="font-medium text-foreground/70">Próx:</span> {card.nextAction}
        </p>

        {/* Row 5: Responsible + Agent */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {responsible && (
            <span className="flex items-center gap-0.5 truncate">
              <User className="h-2.5 w-2.5 shrink-0" />
              <span className="truncate">{responsible.name.split(' ').slice(0, 2).join(' ')}</span>
            </span>
          )}
          {agent && (
            <span className="flex items-center gap-0.5 truncate">
              <Bot className="h-2.5 w-2.5 shrink-0" />
              <span className="truncate">{agent.name.split(' ').slice(0, 1)}</span>
            </span>
          )}
        </div>

        {/* Row 6: Indicators row */}
        <div className="flex items-center justify-between pt-1 border-t border-border/50">
          <div className="flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
            {/* Doc score */}
            <span className="flex items-center gap-0.5" title="Score documental">
              <FileText className="h-2.5 w-2.5" />
              {card.docScore}%
            </span>
            {/* Production score */}
            <span className="flex items-center gap-0.5" title="Score de produção">
              ⚙ {card.productionScore}%
            </span>
            {/* Unread messages */}
            {card.unreadMessages > 0 && (
              <span className="flex items-center gap-0.5 text-[hsl(var(--info))] font-semibold">
                <MessageSquare className="h-2.5 w-2.5" />
                {card.unreadMessages}
              </span>
            )}
            {/* Open tasks */}
            {card.openTasks > 0 && (
              <span className="flex items-center gap-0.5">
                <CheckSquare className="h-2.5 w-2.5" />
                {card.openTasks}
              </span>
            )}
          </div>
        </div>

        {/* Row 7: Deadline + Last interaction */}
        <div className="flex items-center justify-between text-2xs text-muted-foreground tabular-nums">
          {daysUntilDeadline !== null && (
            <span className={`flex items-center gap-0.5 ${deadlineUrgent ? 'text-destructive font-semibold' : ''}`}>
              <Calendar className="h-2.5 w-2.5" />
              {daysUntilDeadline}d
            </span>
          )}
          <span className="flex items-center gap-0.5">
            <Clock className="h-2.5 w-2.5" />
            há {timeAgo(card.lastInteraction)}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
