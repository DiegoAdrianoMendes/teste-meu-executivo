import { Badge } from '@/components/ui/badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Bot, Zap } from 'lucide-react';
import type { AdminUser } from '@/types';

interface BotManagementProps {
  users: AdminUser[];
}

export function BotManagement({ users }: BotManagementProps) {
  const bots = users.filter(u => u.type === 'ai');

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-muted/20 p-3 text-sm text-muted-foreground">
        Os agentes abaixo refletem as políticas de automação configuradas no workspace. O cadastro individual de agentes ainda não é persistido como entidade própria.
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {bots.map(bot => (
          <div key={bot.id} className="rounded-lg border bg-card p-4 space-y-3">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <Avatar className="h-10 w-10">
                  <AvatarFallback style={{ backgroundColor: bot.color, color: '#fff' }}>
                    <Bot className="h-5 w-5" />
                  </AvatarFallback>
                </Avatar>
                <div>
                  <p className="text-sm font-semibold">{bot.name}</p>
                  <p className="text-xs text-muted-foreground">{bot.role}</p>
                </div>
              </div>
              <Badge variant={bot.isActive ? 'default' : 'secondary'} className="text-xs shrink-0">
                {bot.isActive ? 'Ativo' : 'Inativo'}
              </Badge>
            </div>
            {bot.description && (
              <p className="text-xs text-muted-foreground">{bot.description}</p>
            )}
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge variant="outline" className="gap-1"><Zap className="h-2.5 w-2.5" /> {bot.aiModel}</Badge>
              <Badge variant="outline">{bot.maxActionsPerDay} ações/dia</Badge>
              {bot.requiresHumanApproval && <Badge variant="outline" className="text-amber-600 border-amber-300">Aprovação humana</Badge>}
            </div>
          </div>
        ))}
      </div>
      {bots.length === 0 && (
        <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          Nenhum agente automatizado está ativo nas políticas atuais do workspace.
        </div>
      )}
    </div>
  );
}
