import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  ExternalLink,
  FileText,
  Hash,
  Loader2,
  MessageCircleMore,
  MessagesSquare,
  RefreshCw,
  SmilePlus,
  UsersRound,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { KlmPill } from '@/klm/components/Primitives';
import { klmApi } from '@/klm/api';

type CollaborationView = 'history' | 'discussions' | 'members' | 'files' | 'reactions';

const viewCapability: Record<CollaborationView, string> = {
  history: 'room-search',
  discussions: 'room-discussions',
  members: 'teams-membership',
  files: 'room-files',
  reactions: 'reactions-pins-stars',
};

function timestamp(value: unknown) {
  if (typeof value !== 'string') return 'sem horário';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'sem horário' : parsed.toLocaleString('pt-BR');
}

export function NativeCollaborationPanel({
  threadId,
  canSync = false,
  canNavigate = false,
}: {
  threadId?: string | null;
  canSync?: boolean;
  canNavigate?: boolean;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [view, setView] = useState<CollaborationView>('history');
  const [notice, setNotice] = useState<string | null>(null);
  const contextQuery = useQuery({
    queryKey: ['klm', 'collaboration-context', threadId],
    queryFn: () => klmApi.nativeCollaborationThreadContext(threadId!),
    enabled: Boolean(threadId),
    retry: false,
  });
  const context = contextQuery.data;
  const syncMutation = useMutation({
    mutationFn: () => klmApi.reconcileNativeCollaborationThread(threadId!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'collaboration-context', threadId] });
    },
  });
  const attachments = useMemo(() => (context?.messages ?? []).flatMap((message) => (
    message.attachments.map((attachment) => ({ message, attachment }))
  )), [context?.messages]);
  const reactions = useMemo(() => (context?.messages ?? []).flatMap((message) => (
    message.reactions.map((reaction) => ({ message, reaction }))
  )), [context?.messages]);

  const openCurrentView = () => {
    if (!threadId || !canNavigate) return;
    const capability = viewCapability[view];
    navigate(`/app/capacidades/collaboration/${capability}?thread=${threadId}`);
    setNotice('Visão nativa aberta dentro da plataforma.');
  };

  if (!threadId) return <p className="p-4 text-2xs text-muted-foreground">Selecione um caso para abrir sua sala interna.</p>;
  if (contextQuery.isPending) return <div className="flex items-center p-4 text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Lendo o workspace interno...</div>;
  if (contextQuery.isError) return <div className="p-4 text-2xs"><p className="text-red-700">A colaboração interna não respondeu.</p><Button variant="outline" size="sm" className="mt-3 h-7 text-2xs" onClick={() => void contextQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button></div>;
  if (!context?.linked) return <div className="p-4 text-2xs leading-4 text-muted-foreground"><KlmPill tone="gold">sala pendente</KlmPill><p className="mt-3">Reconcilie este caso para preparar sua sala privada na plataforma.</p>{canSync && <Button size="sm" className="mt-3 h-7 text-2xs" disabled={syncMutation.isPending} onClick={() => syncMutation.mutate()}><RefreshCw className={syncMutation.isPending ? 'mr-1.5 h-3 w-3 animate-spin' : 'mr-1.5 h-3 w-3'} />Reconciliar caso</Button>}{syncMutation.isError && <p className="mt-2 text-red-700">{syncMutation.error instanceof Error ? syncMutation.error.message : 'Falha ao reconciliar a sala.'}</p>}</div>;

  const roomName = context.room?.displayName || context.room?.name || context.roomId;
  const tabs: Array<{ value: CollaborationView; label: string; icon: typeof MessagesSquare; total: number }> = [
    { value: 'history', label: 'Histórico', icon: MessagesSquare, total: context.messages.length },
    { value: 'discussions', label: 'Discussões', icon: MessageCircleMore, total: context.discussions.length },
    { value: 'members', label: 'Equipe', icon: UsersRound, total: context.members.length },
    { value: 'files', label: 'Arquivos', icon: FileText, total: attachments.length },
    { value: 'reactions', label: 'Reações', icon: SmilePlus, total: reactions.length },
  ];

  return (
    <div data-testid="collaboration-context" className="space-y-4 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><p className="flex items-center gap-1.5 text-2xs font-semibold"><Hash className="h-3.5 w-3.5 shrink-0 text-[#0057d8]" /><span className="truncate">{roomName}</span></p><p className="mt-1 text-2xs text-muted-foreground">colaboração nativa · fonte canônica</p></div>
        <div className="flex shrink-0 flex-col items-end gap-1"><KlmPill tone="green">vinculada</KlmPill>{context.sensitiveDataHidden && <KlmPill tone="gold">dados ocultos</KlmPill>}</div>
      </div>

      <Tabs value={view} onValueChange={(value) => setView(value as CollaborationView)}>
        <div className="overflow-x-auto pb-1">
          <TabsList className="h-9 min-w-max rounded-none bg-secondary p-0.5">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return <TabsTrigger key={tab.value} value={tab.value} onClick={() => setView(tab.value)} className="h-8 rounded-none px-2 text-2xs"><Icon className="mr-1 h-3 w-3" />{tab.label}<span className="ml-1 text-muted-foreground">{tab.total}</span></TabsTrigger>;
            })}
          </TabsList>
        </div>

        <TabsContent value="history" className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
          {context.messages.slice().reverse().map((message) => <article key={message.id} className="border-l-2 border-primary/50 py-1 pl-2 text-2xs leading-4"><div className="flex items-center justify-between gap-2"><strong className="truncate">{message.author.name || message.author.username || 'Sistema'}</strong><time className="shrink-0 text-2xs text-muted-foreground">{timestamp(message.occurredAt)}</time></div><p className="mt-0.5 whitespace-pre-wrap text-foreground/80">{message.body || 'Evento sem texto'}</p>{(message.attachments.length > 0 || message.reactions.length > 0 || message.replyCount > 0) && <div className="mt-1 flex flex-wrap gap-1"><KlmPill tone="muted">{message.attachments.length} arquivo(s)</KlmPill><KlmPill tone="muted">{message.reactions.reduce((sum, reaction) => sum + reaction.count, 0)} reação(ões)</KlmPill>{message.replyCount > 0 && <KlmPill tone="gold">{message.replyCount} resposta(s)</KlmPill>}</div>}</article>)}
          {!context.messages.length && <p className="py-5 text-center text-2xs text-muted-foreground">Sala criada sem mensagens.</p>}
        </TabsContent>

        <TabsContent value="discussions" className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
          {context.discussions.map((discussion) => <article key={discussion.id} className="border-b pb-2 text-2xs last:border-0"><strong className="block truncate text-2xs">{discussion.name || 'Discussão sem título'}</strong><div className="mt-1 flex items-center justify-between gap-2 text-muted-foreground"><span>{discussion.messageCount} mensagem(ns)</span><span>{timestamp(discussion.updatedAt || discussion.createdAt)}</span></div></article>)}
          {!context.discussions.length && <p className="py-5 text-center text-2xs text-muted-foreground">Nenhuma discussão aberta nesta sala.</p>}
        </TabsContent>

        <TabsContent value="members" className="mt-3 max-h-72 divide-y overflow-y-auto pr-1">
          {context.members.map((member) => <div key={member.id} className="flex items-center justify-between gap-3 py-2 text-2xs"><span className="min-w-0"><strong className="block truncate text-2xs">{member.name || member.username || 'Membro'}</strong><span className="text-muted-foreground">@{member.username || 'sem usuário'}</span></span><KlmPill tone={member.status === 'online' ? 'green' : member.status === 'away' ? 'gold' : 'muted'}>{member.status || 'sem presença'}</KlmPill></div>)}
          {!context.members.length && <p className="py-5 text-center text-2xs text-muted-foreground">Nenhum membro ativo no workspace.</p>}
        </TabsContent>

        <TabsContent value="files" className="mt-3 max-h-72 divide-y overflow-y-auto pr-1">
          {attachments.map(({ message, attachment }, index) => <div key={`${message.id}-${index}`} className="flex items-start gap-2 py-2 text-2xs"><FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8]" /><span className="min-w-0"><strong className="block truncate text-2xs">{attachment.name || 'Arquivo da sala'}</strong><span className="text-muted-foreground">{attachment.mediaType || 'tipo não informado'} · {message.body.slice(0, 72) || 'mensagem sem texto'}</span></span></div>)}
          {!attachments.length && <p className="py-5 text-center text-2xs text-muted-foreground">Nenhum arquivo registrado nesta sala.</p>}
        </TabsContent>

        <TabsContent value="reactions" className="mt-3 max-h-72 divide-y overflow-y-auto pr-1">
          {reactions.map(({ message, reaction }, index) => <div key={`${message.id}-${reaction.emoji}-${index}`} className="flex items-center justify-between gap-3 py-2 text-2xs"><span className="min-w-0"><strong className="block truncate text-2xs">{reaction.emoji || 'reação'} · {reaction.count}</strong><span className="line-clamp-1 text-muted-foreground">{message.body || 'Mensagem sem texto'}</span></span><KlmPill tone="muted">{reaction.count} pessoa(s)</KlmPill></div>)}
          {!reactions.length && <p className="py-5 text-center text-2xs text-muted-foreground">Nenhuma reação registrada nesta sala.</p>}
        </TabsContent>
      </Tabs>

      <div className="grid gap-2 sm:grid-cols-2">
        {canSync && <Button variant="outline" size="sm" className="h-7 text-2xs" disabled={syncMutation.isPending} onClick={() => syncMutation.mutate()}><RefreshCw className={syncMutation.isPending ? 'mr-1.5 h-3 w-3 animate-spin' : 'mr-1.5 h-3 w-3'} />Reconciliar sala</Button>}
        <Button variant="outline" size="sm" className="h-7 text-2xs" disabled={!canNavigate} onClick={openCurrentView}><ExternalLink className="mr-1.5 h-3 w-3" />Abrir visão nativa</Button>
      </div>
      {syncMutation.isSuccess && <p aria-live="polite" className="text-2xs text-emerald-700">Sala reconciliada: {syncMutation.data.messagesSeen} mensagem(ns) verificadas.</p>}
      {syncMutation.isError && <p aria-live="polite" className="text-2xs text-red-700">{syncMutation.error instanceof Error ? syncMutation.error.message : 'Falha ao reconciliar a colaboração interna.'}</p>}
      {notice && <p aria-live="polite" className="text-2xs text-muted-foreground">{notice}</p>}
      <p className="border-t pt-3 text-2xs leading-4 text-muted-foreground">Histórico, presença, arquivos e discussões são lidos diretamente do workspace nativo, sem dependência auxiliar.</p>
    </div>
  );
}
