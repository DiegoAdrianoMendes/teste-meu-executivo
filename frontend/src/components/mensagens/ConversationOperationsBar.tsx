import {
  ArrowRightLeft,
  Bot,
  CalendarClock,
  ClipboardList,
  FileSignature,
  FileText,
  MoreHorizontal,
  PauseCircle,
  Send,
  UserCheck,
  UserRoundPlus,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { ConversationAssignee, FlowTemplateReference, WhatsAppConversation } from '@/types';

interface ConversationOperationsBarProps {
  conversation: WhatsAppConversation;
  currentUserId?: string | null;
  assignees: ConversationAssignee[];
  flowOptions: FlowTemplateReference[];
  onClaim: () => void | Promise<void>;
  onAssign: (userId?: string | null) => void | Promise<void>;
  onMoveToHuman: () => void | Promise<void>;
  onMoveToAi: () => void | Promise<void>;
  onPauseAi: () => void | Promise<void>;
  onMoveToFollowUp: () => void | Promise<void>;
  onApplyFlow: (templateId: string) => void | Promise<void>;
  onOpenScheduleMessage: () => void;
  onOpenMeeting: () => void;
  onOpenMedia: () => void;
  onOpenProposal: () => void;
  onOpenContract: () => void;
}

const queueLabel: Record<string, string> = {
  ai: 'IA no fluxo',
  human: 'Humano no controle',
  follow_up: 'Em follow-up',
  closed: 'Encerrada',
};

const controlLabel: Record<string, string> = {
  copilot: 'IA em co-piloto',
  manual_pause: 'IA pausada',
  manual_off: 'IA desligada',
};

export function ConversationOperationsBar({
  conversation,
  currentUserId,
  assignees,
  flowOptions,
  onClaim,
  onAssign,
  onMoveToHuman,
  onMoveToAi,
  onPauseAi,
  onMoveToFollowUp,
  onApplyFlow,
  onOpenScheduleMessage,
  onOpenMeeting,
  onOpenMedia,
  onOpenProposal,
  onOpenContract,
}: ConversationOperationsBarProps) {
  const isClaimedByCurrentUser = conversation.assignedUserId && conversation.assignedUserId === currentUserId;
  const isHumanQueue = conversation.queueStatus === 'human';
  const isAiQueue = conversation.queueStatus === 'ai';
  const isAiPaused = conversation.humanControlState === 'manual_pause';

  return (
    <div className="border-t border-b bg-background/95 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant={isHumanQueue ? 'secondary' : 'outline'} className="gap-1">
            <ArrowRightLeft className="h-3 w-3" />
            {queueLabel[conversation.queueStatus] ?? 'Fluxo ativo'}
          </Badge>
          <Badge variant={isAiPaused ? 'destructive' : 'outline'} className="gap-1">
            <Bot className="h-3 w-3" />
            {controlLabel[conversation.humanControlState] ?? 'Co-piloto'}
          </Badge>
          <Badge variant="outline" className="max-w-[220px] gap-1 truncate">
            <UserCheck className="h-3 w-3 shrink-0" />
            <span className="truncate">{conversation.assignedUserName || 'Sem responsável'}</span>
          </Badge>
          {conversation.activeFlowLabel && (
            <Badge variant="outline" className="max-w-[220px] gap-1 truncate">
              <ClipboardList className="h-3 w-3 shrink-0" />
              <span className="truncate">{conversation.activeFlowLabel}</span>
            </Badge>
          )}
          {conversation.nextFollowUpAt && (
            <Badge variant="outline" className="gap-1 tabular-nums">
              <CalendarClock className="h-3 w-3 shrink-0" />
              {new Date(conversation.nextFollowUpAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
            </Badge>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
        <Button variant={isClaimedByCurrentUser ? 'secondary' : 'outline'} size="sm" className="h-8" onClick={onClaim}>
          <UserCheck className="mr-1.5 h-3.5 w-3.5" />
          {isClaimedByCurrentUser ? 'Atribuído a mim' : 'Assumir para mim'}
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant={isHumanQueue ? 'secondary' : 'outline'} size="sm" className="h-8">
              {isAiQueue ? <Bot className="mr-1.5 h-3.5 w-3.5" /> : <UserRoundPlus className="mr-1.5 h-3.5 w-3.5" />}
              Atendimento
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64">
            <DropdownMenuLabel>Controle do atendimento</DropdownMenuLabel>
            <DropdownMenuItem onClick={onMoveToHuman}>
              <UserRoundPlus className="mr-2 h-4 w-4" />
              Assumir humano
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onMoveToAi}>
              <Bot className="mr-2 h-4 w-4" />
              Voltar para IA
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onPauseAi}>
              <PauseCircle className="mr-2 h-4 w-4" />
              Pausar IA
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onMoveToFollowUp}>
              <CalendarClock className="mr-2 h-4 w-4" />
              Mover para follow-up
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8">
              <UserRoundPlus className="mr-1.5 h-3.5 w-3.5" />
              Atribuir
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64">
            <DropdownMenuLabel>Responsável do atendimento</DropdownMenuLabel>
            <DropdownMenuItem onClick={() => onAssign(null)}>Desatribuir conversa</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              {assignees.map((assignee) => (
                <DropdownMenuItem key={assignee.id} onClick={() => onAssign(assignee.id)}>
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate text-sm font-medium">{assignee.name}</span>
                    <span className="truncate text-xs text-muted-foreground">{assignee.role}</span>
                  </div>
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8">
              <ClipboardList className="mr-1.5 h-3.5 w-3.5" />
              Fluxos
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-72">
            <DropdownMenuLabel>Aplicar fluxo</DropdownMenuLabel>
            {flowOptions.length === 0 ? (
              <DropdownMenuItem disabled>Nenhum flow disponível neste workspace</DropdownMenuItem>
            ) : (
              flowOptions.map((flow) => (
                <DropdownMenuItem key={flow.id} onClick={() => onApplyFlow(flow.id)}>
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate text-sm font-medium">{flow.name}</span>
                    <span className="truncate text-xs text-muted-foreground">{flow.practiceArea}</span>
                  </div>
                </DropdownMenuItem>
              ))
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="default" size="sm" className="h-8 bg-brand-gradient text-white hover:opacity-90">
              <MoreHorizontal className="mr-1.5 h-3.5 w-3.5" />
              Ações
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72">
            <DropdownMenuLabel>Operações do atendimento</DropdownMenuLabel>
            <DropdownMenuItem onClick={onOpenScheduleMessage}>
              <CalendarClock className="mr-2 h-4 w-4" />
              Agendar mensagem
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onOpenMeeting}>
              <CalendarClock className="mr-2 h-4 w-4" />
              Criar reunião
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onOpenMedia}>
              <Send className="mr-2 h-4 w-4" />
              Enviar mídia
            </DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <FileText className="mr-2 h-4 w-4" />
                Propostas e contratos
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-64">
                <DropdownMenuItem onClick={onOpenProposal}>
                  <FileText className="mr-2 h-4 w-4" />
                  Enviar proposta
                </DropdownMenuItem>
                <DropdownMenuItem onClick={onOpenContract}>
                  <FileSignature className="mr-2 h-4 w-4" />
                  Enviar contrato
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          </DropdownMenuContent>
        </DropdownMenu>
        </div>
      </div>
    </div>
  );
}
