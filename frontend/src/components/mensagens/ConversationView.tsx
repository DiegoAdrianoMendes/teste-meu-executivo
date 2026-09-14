import { useEffect, useRef } from 'react';
import { Phone, Video, MoreVertical, ArrowLeft, PanelRight, CalendarClock, UserRound } from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { MessageBubble } from './MessageBubble';
import { AIControlBar } from './AIControlBar';
import { MessageInput } from './MessageInput';
import type { WhatsAppConversation, AiMode, MessageOptimizationResult, MessageTemplate } from '@/types';
import { formatWhatsAppPhoneDisplay } from '@/lib/whatsapp-phone';

interface ConversationViewProps {
  conversation: WhatsAppConversation;
  onSend: (text: string) => void | Promise<void>;
  onToggleAi: (mode: AiMode) => void;
  onBack?: () => void;
  onShowContext?: () => void;
  onCall?: () => void;
  onVideoCall?: () => void;
  onOpenMenu?: () => void;
  mobile?: boolean;
  messagingEnabled?: boolean;
  statusHint?: string | null;
  templates?: MessageTemplate[];
  onOpenMedia?: () => void;
  onOptimize?: (body: string, tone: string) => Promise<MessageOptimizationResult>;
  operationsBar?: React.ReactNode;
}

function formatDateSeparator(ts: string) {
  const d = new Date(ts);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return 'Hoje';
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'Ontem';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'long' });
}

export function ConversationView({
  conversation,
  onSend,
  onToggleAi,
  onBack,
  onShowContext,
  onCall,
  onVideoCall,
  onOpenMenu,
  mobile,
  messagingEnabled = true,
  statusHint,
  templates = [],
  onOpenMedia,
  onOptimize,
  operationsBar,
}: ConversationViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [conversation.messages.length]);

  // Group messages by date
  const groupedMessages: { date: string; messages: typeof conversation.messages }[] = [];
  let currentDate = '';
  conversation.messages.forEach(msg => {
    const msgDate = new Date(msg.timestamp).toDateString();
    if (msgDate !== currentDate) {
      currentDate = msgDate;
      groupedMessages.push({ date: msg.timestamp, messages: [msg] });
    } else {
      groupedMessages[groupedMessages.length - 1].messages.push(msg);
    }
  });

  const initials = conversation.clientName.split(' ').map(n => n[0]).slice(0, 2).join('');

  return (
    <div className="flex flex-col h-full flex-1 min-w-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b bg-card shrink-0">
        {onBack && (
          <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground shrink-0" onClick={onBack}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
        )}
        <button
          type="button"
          className={`flex min-w-0 flex-1 items-center gap-2 text-left rounded-lg transition-colors ${
            onShowContext ? 'hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-card px-1.5 py-1' : ''
          }`}
          onClick={onShowContext}
          disabled={!onShowContext}
        >
          <div className="relative">
            <Avatar className="h-9 w-9">
              <AvatarFallback className="text-xs font-semibold bg-muted">{initials}</AvatarFallback>
            </Avatar>
            {conversation.isOnline && (
              <div className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-[hsl(var(--success))] border-2 border-card" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold truncate">{conversation.clientName}</h3>
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span>{conversation.isOnline ? 'Online' : formatWhatsAppPhoneDisplay(conversation.clientPhone)}</span>
              {conversation.assignedUserName && (
                <span className="inline-flex items-center gap-1">
                  <UserRound className="h-3 w-3 shrink-0" />
                  {conversation.assignedUserName}
                </span>
              )}
              {conversation.nextFollowUpAt && (
                <span className="inline-flex items-center gap-1 tabular-nums">
                  <CalendarClock className="h-3 w-3 shrink-0" />
                  {new Date(conversation.nextFollowUpAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                </span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <Badge variant="outline" className="text-2xs">{conversation.queueStatus}</Badge>
              {conversation.activeFlowLabel && (
                <Badge variant="secondary" className="max-w-[200px] text-2xs"><span className="truncate">{conversation.activeFlowLabel}</span></Badge>
              )}
            </div>
          </div>
        </button>
        <div className="flex items-center gap-1">
          {onShowContext && (
            <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" onClick={onShowContext}>
              <PanelRight className="h-4 w-4" />
            </Button>
          )}
          {!mobile && (onCall || onVideoCall) && (
            <>
              {onCall && <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" onClick={onCall} aria-label="Ligar para o cliente" title="Ligar para o cliente"><Phone className="h-4 w-4" /></Button>}
              {onVideoCall && <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" onClick={onVideoCall} aria-label="Agendar reunião online" title="Agendar reunião online"><Video className="h-4 w-4" /></Button>}
            </>
          )}
          <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" onClick={onOpenMenu ?? onShowContext} disabled={!onOpenMenu && !onShowContext} aria-label="Abrir dados e ferramentas" title="Abrir dados e ferramentas">
            <MoreVertical className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {operationsBar}

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-3 space-y-1 scrollbar-thin"
        style={{ backgroundImage: 'radial-gradient(circle at 50% 50%, hsl(var(--muted)) 1px, transparent 1px)', backgroundSize: '24px 24px' }}
      >
        {groupedMessages.map((group, gi) => (
          <div key={gi}>
            <div className="flex justify-center my-3">
              <span className="text-xs bg-muted/80 text-muted-foreground px-3 py-0.5 rounded-full font-medium">
                {formatDateSeparator(group.date)}
              </span>
            </div>
            {group.messages.map(msg => (
              <MessageBubble key={msg.id} message={msg} />
            ))}
          </div>
        ))}
      </div>

      {/* AI Controls */}
      {!operationsBar && <AIControlBar aiMode={conversation.aiMode} onToggle={onToggleAi} />}

      {/* Input */}
      <MessageInput
        onSend={onSend}
        disabled={!messagingEnabled}
        helperText={statusHint}
        templates={templates}
        onOpenMedia={onOpenMedia}
        onOptimize={onOptimize}
      />
    </div>
  );
}
