import { useState } from 'react';
import { Search, Bot, Pause, UserRound, Clock3 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import type { WhatsAppConversation } from '@/types';

interface ContactListProps {
  conversations: WhatsAppConversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

type ConversationFilter = 'all' | 'unread' | 'human' | 'ai' | 'follow_up' | 'unassigned';

function timeAgo(ts: string) {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

export function ContactList({ conversations, selectedId, onSelect }: ContactListProps) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<ConversationFilter>('all');

  const filterOptions: Array<{ key: ConversationFilter; label: string; count: number }> = [
    { key: 'all', label: 'Todas', count: conversations.length },
    { key: 'unread', label: 'Não lidas', count: conversations.filter((conversation) => conversation.unreadCount > 0).length },
    { key: 'human', label: 'Humano', count: conversations.filter((conversation) => conversation.queueStatus === 'human').length },
    { key: 'ai', label: 'IA', count: conversations.filter((conversation) => conversation.queueStatus === 'ai').length },
    { key: 'follow_up', label: 'Follow-up', count: conversations.filter((conversation) => conversation.queueStatus === 'follow_up').length },
    { key: 'unassigned', label: 'Sem resp.', count: conversations.filter((conversation) => !conversation.assignedUserId).length },
  ];

  const filtered = conversations.filter((conversation) => {
    const matchesSearch =
      conversation.clientName.toLowerCase().includes(search.toLowerCase()) ||
      conversation.clientPhone.includes(search);
    if (!matchesSearch) return false;
    if (filter === 'all') return true;
    if (filter === 'unread') return conversation.unreadCount > 0;
    if (filter === 'unassigned') return !conversation.assignedUserId;
    return conversation.queueStatus === filter;
  });

  return (
    <div className="flex flex-col h-full border-r bg-card">
      <div className="p-3 border-b space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="min-w-0 truncate text-lg font-semibold">Mensagens</h2>
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs font-medium tabular-nums text-muted-foreground">
            {filtered.length}
          </span>
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            placeholder="Buscar contato..."
            className="h-8 pl-8 text-sm bg-muted/50 border-0"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="flex gap-1 overflow-x-auto pb-1">
          {filterOptions.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setFilter(option.key)}
              className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                filter === option.key
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-background text-muted-foreground hover:bg-muted'
              }`}
            >
              {option.label}
              {option.count > 0 && <span className="ml-1 tabular-nums opacity-75">{option.count}</span>}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {filtered.map(conv => (
          <button
            key={conv.id}
            onClick={() => onSelect(conv.id)}
            className={`w-full text-left px-3 py-2.5 flex items-start gap-2.5 hover:bg-muted/50 transition-colors border-b ${
              selectedId === conv.id ? 'bg-primary/5 border-l-2 border-l-primary' : ''
            }`}
          >
            <div className="relative shrink-0">
              <Avatar className="h-10 w-10">
                <AvatarFallback className="text-xs font-semibold bg-muted">
                  {conv.clientName.split(' ').map(n => n[0]).slice(0, 2).join('')}
                </AvatarFallback>
              </Avatar>
              {conv.isOnline && (
                <div className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full bg-[hsl(var(--success))] border-2 border-card" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-1">
                <span className="min-w-0 text-sm font-semibold truncate">{conv.clientName}</span>
                <span className={`text-2xs tabular-nums shrink-0 ${conv.unreadCount > 0 ? 'text-[hsl(var(--success))] font-semibold' : 'text-muted-foreground'}`}>
                  {timeAgo(conv.lastMessageTime)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-1 mt-0.5">
                <p className="text-xs text-muted-foreground truncate min-w-0 flex-1">{conv.lastMessage}</p>
                <div className="flex items-center gap-1 shrink-0">
                  {conv.aiMode === 'active' && <Bot className="h-3 w-3 text-[hsl(var(--success))]" />}
                  {conv.aiMode === 'paused' && <Pause className="h-3 w-3 text-[hsl(var(--warning))]" />}
                  {conv.unreadCount > 0 && (
                    <Badge className="h-4 min-w-[16px] text-2xs tabular-nums px-1 justify-center bg-[hsl(var(--success))] text-[hsl(var(--success-foreground))] hover:bg-[hsl(var(--success))]">
                      {conv.unreadCount}
                    </Badge>
                  )}
                </div>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1">
                <Badge variant="outline" className="h-4 px-1 text-2xs">
                  {conv.queueStatus === 'ai' ? 'IA' : conv.queueStatus === 'follow_up' ? 'Follow-up' : conv.queueStatus === 'closed' ? 'Encerrada' : 'Humano'}
                </Badge>
                {conv.assignedUserName && (
                  <Badge variant="secondary" className="h-4 px-1 text-2xs gap-1">
                    <UserRound className="h-2.5 w-2.5 shrink-0" />
                    {conv.assignedUserName.split(' ')[0]}
                  </Badge>
                )}
                {conv.nextFollowUpAt && (
                  <Badge variant="outline" className="h-4 px-1 text-2xs gap-1">
                    <Clock3 className="h-2.5 w-2.5 shrink-0" />
                    Follow-up
                  </Badge>
                )}
              </div>
            </div>
          </button>
        ))}
        {filtered.length === 0 && (
          <p className="text-xs text-muted-foreground text-center py-8">Nenhum contato encontrado</p>
        )}
      </div>
    </div>
  );
}
