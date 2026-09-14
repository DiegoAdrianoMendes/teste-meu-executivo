import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowUpRight,
  Bot,
  Clock3,
  ExternalLink,
  Hash,
  Inbox,
  Link2,
  Loader2,
  MessageCircleMore,
  Plus,
  Send,
  ShieldCheck,
  UserPlus,
  WandSparkles,
} from 'lucide-react';

import { CardContextPanel } from '@/components/mensagens/CardContextPanel';
import { KlmPill } from '@/klm/components/Primitives';
import { SectionHeader } from '@/components/common/SectionHeader';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  createApprovedMessage,
  createAutomationRule,
  createMetaCampaign,
  createMetaConnection,
  createMetaConversation,
  createMetaNote,
  fetchApprovedMessages,
  fetchAutomationRules,
  fetchMetaCampaigns,
  fetchMetaComments,
  fetchMetaConnections,
  fetchMetaConversation,
  fetchMetaConversations,
  fetchMetaDrawer,
  generateMetaCopilot,
  moveMetaConversationStage,
  refreshMetaFollowStatus,
  sendMetaMessage,
  updateMetaMatter,
} from '@/services/metaChannels';
import type {
  FollowRelationshipStatus,
  LeadDrawerData,
  MetaCopilotResult,
  MetaLeadDrawerData,
} from '@/types';

type ChannelKey = 'instagram' | 'messenger';
type StudioMode = 'inbox' | 'comments' | 'automation' | 'approved' | 'connections' | 'entrypoints';
type SocialQueueFilter = 'all' | 'unread' | 'human' | 'ai' | 'follow_up';
type SocialFollowFilter = 'all' | FollowRelationshipStatus;

const FOLLOW_STATUS_LABELS: Record<FollowRelationshipStatus, string> = {
  following: 'Segue',
  not_following: 'Não segue',
  unknown: 'Desconhecido',
};

interface MetaChannelStudioProps {
  channel: ChannelKey;
  mode: StudioMode;
  initialConversationId?: string | null;
  onOpenConversation?: (conversationId: string) => void;
}

const CHANNEL_META = {
  instagram: {
    label: 'Instagram',
    surface: 'Caixa social',
    accentClass: 'border-rose-200/70 bg-rose-50/40 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/20 dark:text-rose-200',
    chipClass: 'border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-50 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200',
    description: 'Mensagens privadas, comentários, respostas a stories, status de seguimento e CTAs oficiais em uma operação social com cara de Instagram.',
    commentsLabel: 'Comentários em postagens',
    entryLabel: 'Entradas e links oficiais',
  },
  messenger: {
    label: 'Messenger',
    surface: 'Caixa da página',
    accentClass: 'border-sky-200/70 bg-sky-50/40 text-sky-700 dark:border-sky-900/50 dark:bg-sky-950/20 dark:text-sky-200',
    chipClass: 'border-sky-200 bg-sky-50 text-sky-700 hover:bg-sky-50 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-200',
    description: 'Mensagens da Page, comentários de Facebook e automações de entrada em uma operação própria de Messenger.',
    commentsLabel: 'Comentários da página',
    entryLabel: 'Entradas, menus e postbacks',
  },
} as const;

// Capability matrix honesta (PLANO_META_MAXIMO seção 5 item 5): o backend
// devolve `{ supported, reason }` por capability, nunca mais um booleano cru
// "all-true" decorativo. Aceita o formato legado (booleano) só como
// fallback defensivo, sem quebrar se algum registro antigo ainda não migrou.
function isCapabilitySupported(value: unknown): boolean {
  if (value && typeof value === 'object' && 'supported' in (value as Record<string, unknown>)) {
    return Boolean((value as { supported?: unknown }).supported);
  }
  return Boolean(value);
}

function capabilityReason(value: unknown): string | undefined {
  if (value && typeof value === 'object' && 'reason' in (value as Record<string, unknown>)) {
    return (value as { reason?: string }).reason;
  }
  return undefined;
}

function toDrawerData(drawer: MetaLeadDrawerData): LeadDrawerData {
  const aiMode = drawer.conversation.queueStatus === 'ai'
    ? 'active'
    : drawer.conversation.humanControlState === 'manual_off'
      ? 'off'
      : 'paused';
  return {
    contact: {
      id: drawer.conversation.id,
      clientId: drawer.conversation.id,
      clientName: drawer.conversation.participantName,
      clientPhone: drawer.conversation.participantHandle || drawer.conversation.externalParticipantId || '',
      clientAvatar: drawer.conversation.participantAvatarUrl || undefined,
      cardId: drawer.conversation.matterId || undefined,
      messages: [],
      lastMessage: drawer.conversation.lastMessagePreview || '',
      lastMessageTime: drawer.conversation.lastMessageAt || new Date().toISOString(),
      unreadCount: drawer.conversation.unreadCount,
      aiMode,
      isOnline: false,
      assignedUserId: drawer.conversation.assignedUserId,
      assignedUserName: drawer.conversation.assignedUserName,
      queueStatus: drawer.conversation.queueStatus,
      humanControlState: drawer.conversation.humanControlState,
      activeFlowTemplateId: null,
      activeFlowLabel: drawer.conversation.activeFlowLabel,
      nextFollowUpAt: drawer.conversation.nextFollowUpAt,
    },
    matter: drawer.matter ?? null,
    notes: drawer.notes.map((note) => ({
      id: note.id,
      contactId: note.conversationId,
      matterId: note.matterId,
      authorUserId: note.authorUserId,
      authorName: note.authorName,
      body: note.body,
      mentions: note.mentions,
      noteMetadata: note.noteMetadata,
      createdAt: note.createdAt,
    })),
    recentMeetings: drawer.recentMeetings,
    canEditMatter: drawer.canEditMatter,
    canManageConversation: drawer.canManageConversation,
  };
}

function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex min-h-[220px] flex-col items-center justify-center gap-3 text-center">
        <Inbox className="h-8 w-8 text-muted-foreground" />
        <div className="space-y-1">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {action}
      </CardContent>
    </Card>
  );
}

export function MetaChannelStudio({
  channel,
  mode,
  initialConversationId = null,
  onOpenConversation,
}: MetaChannelStudioProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const copy = CHANNEL_META[channel];
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(
    initialConversationId,
  );
  const [showCreateConversation, setShowCreateConversation] = useState(false);
  const [queueFilter, setQueueFilter] = useState<SocialQueueFilter>('all');
  const [followFilter, setFollowFilter] = useState<SocialFollowFilter>('all');
  const [eventFilter, setEventFilter] = useState('all');
  const [conversationSearch, setConversationSearch] = useState('');
  const [composer, setComposer] = useState('');
  const [conversationDraft, setConversationDraft] = useState({
    connectionId: '',
    participantName: '',
    participantHandle: '',
    externalThreadId: '',
    eventType: channel === 'instagram' ? 'direct_message' : 'page_message',
    sourceTitle: '',
    initialMessage: '',
    isPublicThread: false,
  });
  const [connectionDraft, setConnectionDraft] = useState({
    accountName: '',
    username: '',
    externalAccountId: '',
    pageId: '',
    instagramAccountId: '',
    scopes: '',
  });
  const [approvedDraft, setApprovedDraft] = useState({
    title: '',
    body: '',
    messageKind: mode === 'comments' ? 'comment_reply' : 'direct_message',
    status: 'approved',
    ctaLabel: '',
    ctaUrl: '',
    requiresFollowing: false,
  });
  const [ruleDraft, setRuleDraft] = useState({
    title: '',
    eventType: channel === 'instagram' ? 'keyword_comment' : 'page_comment',
    keyword: '',
    approvedMessageId: '',
    targetQueueStatus: 'human',
    targetColumnId: 'qualificacao',
    requiresFollowing: false,
    aiEnabled: true,
    aiTone: 'clear',
    aiGoal: '',
    publicReply: false,
  });
  const [campaignDraft, setCampaignDraft] = useState({
    connectionId: '',
    approvedMessageId: '',
    title: '',
    description: '',
    estimatedRecipients: '0',
    eligibleRecipients: '0',
    blockedRecipients: '0',
  });
  const [copilotTone, setCopilotTone] = useState('clear');
  const [copilotGoal, setCopilotGoal] = useState('');

  const connectionsQuery = useQuery({
    queryKey: ['meta-connections', channel],
    queryFn: () => fetchMetaConnections(channel),
  });
  const conversationsQuery = useQuery({
    queryKey: ['meta-conversations', channel],
    queryFn: () => fetchMetaConversations(channel),
  });
  const commentsQuery = useQuery({
    queryKey: ['meta-comments', channel],
    queryFn: () => fetchMetaComments(channel),
    enabled: mode === 'comments',
  });
  const approvedQuery = useQuery({
    queryKey: ['meta-approved', channel],
    queryFn: () => fetchApprovedMessages(channel),
    enabled: mode === 'approved' || mode === 'automation' || mode === 'entrypoints',
  });
  const automationQuery = useQuery({
    queryKey: ['meta-automation', channel],
    queryFn: () => fetchAutomationRules(channel),
    enabled: mode === 'automation',
  });
  const campaignsQuery = useQuery({
    queryKey: ['meta-campaigns', channel],
    queryFn: () => fetchMetaCampaigns(channel),
    enabled: mode === 'entrypoints',
  });
  const selectedConversationQuery = useQuery({
    queryKey: ['meta-conversation', channel, selectedConversationId],
    queryFn: () => fetchMetaConversation(channel, selectedConversationId!),
    enabled: Boolean(selectedConversationId),
  });
  const drawerQuery = useQuery({
    queryKey: ['meta-drawer', channel, selectedConversationId],
    queryFn: () => fetchMetaDrawer(channel, selectedConversationId!),
    enabled: Boolean(selectedConversationId) && (mode === 'inbox' || mode === 'comments'),
  });

  useEffect(() => {
    const first = conversationsQuery.data?.[0];
    if (!selectedConversationId && first) {
      setSelectedConversationId(first.id);
    }
  }, [conversationsQuery.data, selectedConversationId]);

  useEffect(() => {
    setComposer('');
    setCopilotGoal('');
  }, [selectedConversationId]);

  useEffect(() => {
    const firstConnection = connectionsQuery.data?.[0];
    if (firstConnection && !conversationDraft.connectionId) {
      setConversationDraft((current) => ({ ...current, connectionId: firstConnection.id }));
      setCampaignDraft((current) => ({ ...current, connectionId: firstConnection.id }));
    }
  }, [connectionsQuery.data, conversationDraft.connectionId]);

  const selectedConversation = selectedConversationQuery.data;
  const drawerData = drawerQuery.data;
  const asLegacyDrawer = drawerData ? toDrawerData(drawerData) : null;
  const approvedMessages = approvedQuery.data ?? [];
  const conversations = useMemo(() => conversationsQuery.data ?? [], [conversationsQuery.data]);
  const eventOptions = useMemo(
    () => Array.from(new Set(conversations.map((conversation) => conversation.eventType))).filter(Boolean),
    [conversations],
  );
  const filteredConversations = useMemo(() => {
    const search = conversationSearch.trim().toLowerCase();
    return conversations.filter((conversation) => {
      const haystack = [
        conversation.participantName,
        conversation.participantHandle,
        conversation.externalParticipantId,
        conversation.sourceTitle,
        conversation.lastMessagePreview,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (search && !haystack.includes(search)) return false;
      if (queueFilter === 'unread' && conversation.unreadCount <= 0) return false;
      if (queueFilter !== 'all' && queueFilter !== 'unread' && conversation.queueStatus !== queueFilter) return false;
      if (followFilter !== 'all' && conversation.followStatus !== followFilter) return false;
      if (eventFilter !== 'all' && conversation.eventType !== eventFilter) return false;
      return true;
    });
  }, [conversationSearch, conversations, eventFilter, followFilter, queueFilter]);
  const queueFilters: Array<{ key: SocialQueueFilter; label: string; count: number }> = [
    { key: 'all', label: 'Todas', count: conversations.length },
    { key: 'unread', label: 'Não lidas', count: conversations.filter((conversation) => conversation.unreadCount > 0).length },
    { key: 'human', label: 'Humano', count: conversations.filter((conversation) => conversation.queueStatus === 'human').length },
    { key: 'ai', label: 'IA', count: conversations.filter((conversation) => conversation.queueStatus === 'ai').length },
    { key: 'follow_up', label: 'Acompanhamento', count: conversations.filter((conversation) => conversation.queueStatus === 'follow_up').length },
  ];

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['meta-connections', channel] }),
      queryClient.invalidateQueries({ queryKey: ['meta-conversations', channel] }),
      queryClient.invalidateQueries({ queryKey: ['meta-comments', channel] }),
      queryClient.invalidateQueries({ queryKey: ['meta-approved', channel] }),
      queryClient.invalidateQueries({ queryKey: ['meta-automation', channel] }),
      queryClient.invalidateQueries({ queryKey: ['meta-campaigns', channel] }),
      queryClient.invalidateQueries({ queryKey: ['meta-conversation', channel, selectedConversationId] }),
      queryClient.invalidateQueries({ queryKey: ['meta-drawer', channel, selectedConversationId] }),
    ]);
  };

  const createConnectionMutation = useMutation({
    mutationFn: () => createMetaConnection(channel, {
      accountName: connectionDraft.accountName,
      username: connectionDraft.username,
      externalAccountId: connectionDraft.externalAccountId,
      pageId: connectionDraft.pageId || null,
      instagramAccountId: connectionDraft.instagramAccountId || null,
      scopes: connectionDraft.scopes.split(',').map((item) => item.trim()).filter(Boolean),
      status: 'pending',
      webhookRegistered: false,
    }),
    onSuccess: async () => {
      toast({ title: `${copy.label} cadastrado localmente.`, description: 'Autenticação e webhook externos continuam pendentes.' });
      setConnectionDraft({ accountName: '', username: '', externalAccountId: '', pageId: '', instagramAccountId: '', scopes: '' });
      await invalidateAll();
    },
  });

  const createConversationMutation = useMutation({
    mutationFn: () => createMetaConversation(channel, {
      connectionId: conversationDraft.connectionId,
      participantName: conversationDraft.participantName,
      participantHandle: conversationDraft.participantHandle || null,
      externalThreadId: conversationDraft.externalThreadId,
      eventType: conversationDraft.eventType,
      sourceTitle: conversationDraft.sourceTitle || null,
      initialMessage: conversationDraft.initialMessage || null,
      isPublicThread: conversationDraft.isPublicThread,
      followStatus: 'unknown',
    }),
    onSuccess: async (data) => {
      setSelectedConversationId(data.id);
      setShowCreateConversation(false);
      toast({ title: 'Conversa criada no inbox social.' });
      setConversationDraft((current) => ({
        ...current,
        participantName: '',
        participantHandle: '',
        externalThreadId: '',
        sourceTitle: '',
        initialMessage: '',
        isPublicThread: false,
      }));
      await invalidateAll();
    },
  });

  const sendMessageMutation = useMutation({
    mutationFn: () => sendMetaMessage(channel, selectedConversationId!, composer, mode === 'comments'),
    onSuccess: async (conversation) => {
      setComposer('');
      const message = conversation.messages.at(-1);
      toast({
        title: message?.status === 'pending' ? 'Resposta salva como rascunho local.' : 'Resposta registrada.',
        description: message?.status === 'pending' ? 'Nenhuma chamada à Meta foi executada.' : undefined,
      });
      await invalidateAll();
    },
  });

  const followMutation = useMutation({
    mutationFn: (status: FollowRelationshipStatus) => refreshMetaFollowStatus(channel, selectedConversationId!, status),
    onSuccess: async () => {
      await invalidateAll();
    },
  });

  const createApprovedMutation = useMutation({
    mutationFn: () => createApprovedMessage(channel, approvedDraft),
    onSuccess: async () => {
      toast({ title: 'Mensagem validada adicionada à biblioteca.' });
      setApprovedDraft({ title: '', body: '', messageKind: mode === 'comments' ? 'comment_reply' : 'direct_message', status: 'approved', ctaLabel: '', ctaUrl: '', requiresFollowing: false });
      await invalidateAll();
    },
  });

  const createRuleMutation = useMutation({
    mutationFn: () => createAutomationRule(channel, {
      title: ruleDraft.title,
      eventType: ruleDraft.eventType,
      keyword: ruleDraft.keyword || null,
      approvedMessageId: ruleDraft.approvedMessageId || null,
      actionConfig: {
        targetQueueStatus: ruleDraft.targetQueueStatus,
        targetColumnId: ruleDraft.targetColumnId,
        requiresFollowing: ruleDraft.requiresFollowing,
        aiEnabled: ruleDraft.aiEnabled,
        aiTone: ruleDraft.aiTone,
        aiGoal: ruleDraft.aiGoal || null,
        publicReply: ruleDraft.publicReply,
      },
    }),
    onSuccess: async () => {
      toast({ title: 'Regra de automação salva.' });
      setRuleDraft({
        title: '',
        eventType: channel === 'instagram' ? 'keyword_comment' : 'page_comment',
        keyword: '',
        approvedMessageId: '',
        targetQueueStatus: 'human',
        targetColumnId: 'qualificacao',
        requiresFollowing: false,
        aiEnabled: true,
        aiTone: 'clear',
        aiGoal: '',
        publicReply: false,
      });
      await invalidateAll();
    },
  });

  const generateCopilotMutation = useMutation({
    mutationFn: () => generateMetaCopilot(channel, selectedConversationId!, {
      tone: copilotTone,
      goal: copilotGoal.trim() || null,
    }),
    onError: () => {
      toast({
        title: 'Co-piloto indisponível agora',
        description: 'A geração falhou nesta tentativa. Você pode tentar novamente em instantes.',
        variant: 'destructive',
      });
    },
  });
  const copilotData: MetaCopilotResult | undefined = generateCopilotMutation.data;

  const createCampaignMutation = useMutation({
    mutationFn: () => createMetaCampaign(channel, {
      connectionId: campaignDraft.connectionId,
      approvedMessageId: campaignDraft.approvedMessageId || null,
      title: campaignDraft.title,
      description: campaignDraft.description || null,
      audienceFilter: {
        estimatedRecipients: Number(campaignDraft.estimatedRecipients),
      },
      // Elegíveis/bloqueados não são enviados: o backend sempre recalcula
      // server-side a partir da janela de 24h do último inbound real e
      // ignora qualquer valor digitado (achado da auditoria: "compliance
      // autodeclarado"). Os campos na UI são só leitura do que voltou da API.
    }),
    onSuccess: async (campaign) => {
      toast({ title: 'Campanha de reengajamento criada.' });
      setCampaignDraft((current) => ({
        ...current,
        title: '',
        description: '',
        estimatedRecipients: '0',
        eligibleRecipients: String(campaign.eligibleRecipients ?? 0),
        blockedRecipients: String(campaign.blockedRecipients ?? 0),
      }));
      await invalidateAll();
    },
  });

  const activeCapabilities = useMemo(() => {
    const first = connectionsQuery.data?.[0];
    return Object.entries(first?.capabilityMatrix ?? {}).filter(([, value]) => isCapabilitySupported(value));
  }, [connectionsQuery.data]);

  const renderInbox = () => (
    <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)_420px]">
      <Card className="border-0 shadow-sm">
        <CardHeader className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">Fila ativa</CardTitle>
              <CardDescription>{filteredConversations.length} de {conversations.length} threads visíveis.</CardDescription>
            </div>
            <Button size="sm" variant="outline" className="shrink-0" onClick={() => setShowCreateConversation(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Nova
            </Button>
          </div>
          <div className="space-y-2">
            <Input
              value={conversationSearch}
              onChange={(event) => setConversationSearch(event.target.value)}
              placeholder={channel === 'instagram' ? 'Buscar perfil, @usuário ou postagem...' : 'Buscar contato, Página ou conversa...'}
              className="h-8 text-xs"
            />
            <div className="flex gap-1 overflow-x-auto pb-1">
              {queueFilters.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setQueueFilter(option.key)}
                  className={`shrink-0 rounded-full border px-2.5 py-1 text-[10px] font-medium transition-colors ${
                    queueFilter === option.key
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-background text-muted-foreground hover:bg-muted'
                  }`}
                >
                  {option.label}
                  {option.count > 0 && <span className="ml-1 opacity-75">{option.count}</span>}
                </button>
              ))}
            </div>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
              <Select value={followFilter} onValueChange={(value) => setFollowFilter(value as SocialFollowFilter)}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Status de seguimento" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todos os status</SelectItem>
                  <SelectItem value="following">Segue</SelectItem>
                  <SelectItem value="not_following">Não segue</SelectItem>
                  <SelectItem value="unknown">Desconhecido</SelectItem>
                </SelectContent>
              </Select>
              <Select value={eventFilter} onValueChange={setEventFilter}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Origem" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todas as origens</SelectItem>
                  {eventOptions.map((eventType) => (
                    <SelectItem key={eventType} value={eventType}>{eventType}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[520px] pr-2">
            <div className="space-y-2">
              {filteredConversations.map((conversation) => (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => setSelectedConversationId(conversation.id)}
                  className={`w-full rounded-lg border p-3 text-left transition-colors ${
                    selectedConversationId === conversation.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-medium">{conversation.participantName}</p>
                    {conversation.unreadCount > 0 && (
                      <Badge className="h-5 min-w-5 justify-center px-1 text-[10px]">{conversation.unreadCount}</Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{conversation.participantHandle || conversation.externalParticipantId || 'Sem identificador'}</p>
                  <p className="mt-2 line-clamp-2 text-sm text-foreground/80">{conversation.lastMessagePreview || 'Sem mensagens ainda.'}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-1">
                    <Badge variant="outline" className="h-5 px-1.5 text-[10px]">{conversation.eventType}</Badge>
                    <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">{conversation.queueStatus}</Badge>
                    <Badge variant="outline" className="h-5 px-1.5 text-[10px]">{FOLLOW_STATUS_LABELS[conversation.followStatus]}</Badge>
                    {conversation.lastMessageAt && (
                      <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                        <Clock3 className="h-3 w-3" />
                        {new Date(conversation.lastMessageAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                      </span>
                    )}
                  </div>
                </button>
              ))}
              {conversations.length === 0 && (
                <p className="text-sm text-muted-foreground">Conecte o canal e crie sua primeira conversa social.</p>
              )}
              {conversations.length > 0 && filteredConversations.length === 0 && (
                <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">Nenhuma thread bate com os filtros atuais.</p>
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">{selectedConversation?.participantName || 'Selecione uma conversa'}</CardTitle>
              <CardDescription>
                {selectedConversation?.sourceTitle || selectedConversation?.participantHandle || 'O contexto de origem aparece aqui por rede.'}
              </CardDescription>
            </div>
            {selectedConversation && (
              <div className="flex flex-wrap items-center justify-end gap-2">
                <Badge variant="outline">{selectedConversation.eventType}</Badge>
                <Select value={selectedConversation.followStatus} onValueChange={(value) => followMutation.mutate(value as FollowRelationshipStatus)}>
                  <SelectTrigger className="h-8 w-[150px] text-xs">
                    <SelectValue placeholder="Seguimento" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="following">Segue</SelectItem>
                    <SelectItem value="not_following">Não segue</SelectItem>
                    <SelectItem value="unknown">Desconhecido</SelectItem>
                  </SelectContent>
                </Select>
                {selectedConversation.sourceUrl && (
                  <Button asChild variant="ghost" size="sm">
                    <a href={selectedConversation.sourceUrl} target="_blank" rel="noreferrer">
                      <ExternalLink className="mr-2 h-4 w-4" />
                      Origem
                    </a>
                  </Button>
                )}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!selectedConversation ? (
            <EmptyState title={`Nenhuma conversa ${copy.label.toLowerCase()} selecionada`} description="Escolha uma conversa na coluna da esquerda para abrir o canal com contexto social, status de seguimento e linha do tempo." />
          ) : (
            <>
              <ScrollArea className="h-[430px] rounded-lg border bg-muted/20 p-4">
                <div className="space-y-3">
                  {selectedConversation.messages.map((message) => (
                    <div key={message.id} className={`flex ${message.direction === 'outbound' ? 'justify-end' : 'justify-start'}`}>
                      <div className={`max-w-[80%] rounded-lg px-4 py-3 text-sm shadow-sm ${message.direction === 'outbound' ? 'bg-primary text-primary-foreground' : 'bg-background border'}`}>
                        {message.isPublicReply && (
                          <Badge className="mb-2 bg-background/30 text-current hover:bg-background/30">resposta pública</Badge>
                        )}
                        <p className="whitespace-pre-wrap">{message.body}</p>
                        <p className={`mt-2 text-[11px] ${message.direction === 'outbound' ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>
                          {new Date(message.timestamp).toLocaleString('pt-BR')}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </ScrollArea>

              <div className="rounded-lg border bg-muted/20 p-3 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <WandSparkles className="h-4 w-4 text-primary" />
                    <p className="text-sm font-medium">Co-piloto</p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => generateCopilotMutation.mutate()}
                    disabled={generateCopilotMutation.isPending}
                  >
                    {generateCopilotMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <WandSparkles className="mr-2 h-4 w-4" />}
                    Gerar
                  </Button>
                </div>
                <div className="grid gap-2 md:grid-cols-[170px_minmax(0,1fr)]">
                <Select value={copilotTone} onValueChange={setCopilotTone}>
                  <SelectTrigger className="h-8 w-full">
                    <SelectValue placeholder="Tom do co-piloto" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="clear">Claro e objetivo</SelectItem>
                    <SelectItem value="commercial">Comercial</SelectItem>
                    <SelectItem value="empathetic">Empático</SelectItem>
                    <SelectItem value="formal">Formal</SelectItem>
                  </SelectContent>
                </Select>
                <Input
                  value={copilotGoal}
                  onChange={(event) => setCopilotGoal(event.target.value)}
                  placeholder="Objetivo do co-piloto"
                  className="h-8"
                />
                </div>
                {copilotData && (
                  <div className="space-y-4 border-t pt-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={copy.chipClass}>IA ativa</Badge>
                    {copilotData.provider && <Badge variant="outline">{copilotData.provider}</Badge>}
                    {copilotData.modelUsed && <Badge variant="outline">{copilotData.modelUsed}</Badge>}
                    {copilotData.automationEnabled && (
                      <Badge variant="secondary">
                        automação {copilotData.automationTitle || 'vinculada'}
                      </Badge>
                    )}
                  </div>
                  <div className="grid gap-4 md:grid-cols-[0.9fr_1.1fr]">
                    <div className="space-y-2">
                      <div>
                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Resumo</p>
                        <p className="mt-1 text-sm">{copilotData.summary}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Próxima ação</p>
                        <p className="mt-1 text-sm">{copilotData.recommendedAction}</p>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Sugestões prontas para enviar</p>
                      {copilotData.replySuggestions.map((suggestion) => (
                        <button
                          key={suggestion}
                          type="button"
                          onClick={() => setComposer(suggestion)}
                          className="w-full rounded-lg border bg-background p-3 text-left text-sm transition-colors hover:border-primary hover:bg-primary/5"
                        >
                          {suggestion}
                        </button>
                      ))}
                      {copilotData.replySuggestions.length === 0 && (
                        <p className="text-sm text-muted-foreground">Nenhuma sugestão pronta foi gerada nesta rodada.</p>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Sugestão de saída: {copilotData.suggestedPublicReply ? 'resposta pública' : 'mensagem privada/DM'}.
                  </p>
                  </div>
                )}
              </div>

              <div className="rounded-lg border p-4 space-y-3">
                <Textarea
                  value={composer}
                  onChange={(event) => setComposer(event.target.value)}
                  placeholder={mode === 'comments' ? 'Escreva uma resposta pública ou privada para este evento social...' : 'Escreva uma mensagem para este thread social...'}
                  className="min-h-[120px]"
                />
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs text-muted-foreground">
                    Política do canal: {selectedConversation.policyState || 'controlada pela rede e pelas mensagens aprovadas.'}
                  </p>
                  <Button onClick={() => sendMessageMutation.mutate()} disabled={sendMessageMutation.isPending || !composer.trim()}>
                    {sendMessageMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
                    Salvar resposta
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <CardContextPanel
        contactId={selectedConversationId || 'meta'}
        drawerData={asLegacyDrawer}
        loading={drawerQuery.isLoading}
        onAddNote={selectedConversationId ? async (body) => {
          await createMetaNote(channel, selectedConversationId, body);
          await invalidateAll();
        } : undefined}
        onMoveStage={selectedConversationId ? async (columnId, microStatus) => {
          await moveMetaConversationStage(channel, selectedConversationId, { columnId, microStatus });
          await invalidateAll();
        } : undefined}
        onSaveMatter={selectedConversationId ? async (payload) => {
          await updateMetaMatter(channel, selectedConversationId, {
            ...payload,
            nextDeadline: payload.nextDeadline || null,
          });
          await invalidateAll();
        } : undefined}
      />
    </div>
  );

  const renderComments = () => (
    <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">{copy.commentsLabel}</CardTitle>
          <CardDescription>Comentários públicos com keyword, origem da postagem e elegibilidade para DM derivada.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {(commentsQuery.data ?? []).map((comment) => (
            <div key={comment.id} className="rounded-lg border p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-medium">{comment.participantName}</p>
                  <p className="text-xs text-muted-foreground">{comment.sourceTitle || 'Comentário social'}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{comment.eventType}</Badge>
                  <Badge variant="secondary">{comment.followStatus}</Badge>
                </div>
              </div>
              <p className="mt-3 text-sm text-foreground/80">{comment.lastMessagePreview || 'Sem texto de comentário registrado.'}</p>
              <div className="mt-3 flex items-center gap-2">
                {comment.sourceUrl && (
                  <Button asChild variant="outline" size="sm">
                    <a href={comment.sourceUrl} target="_blank" rel="noreferrer">
                      <ArrowUpRight className="mr-2 h-4 w-4" />
                      Abrir post
                    </a>
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSelectedConversationId(comment.conversationId);
                    onOpenConversation?.(comment.conversationId);
                  }}
                >
                  <MessageCircleMore className="mr-2 h-4 w-4" />
                  Abrir thread
                </Button>
              </div>
            </div>
          ))}
          {(commentsQuery.data ?? []).length === 0 && (
            <EmptyState title="Nenhum comentário ingerido ainda" description="Quando o canal começar a receber comentários elegíveis, eles aparecem aqui com post de origem, follow signal e operação humana." />
          )}
        </CardContent>
      </Card>
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Playbook de resposta</CardTitle>
          <CardDescription>Use comentários para qualificar, puxar para DM e decidir quando escalar para humano.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border bg-muted/30 p-4 space-y-2 text-sm">
            <p className="font-medium">Checklist recomendado</p>
            <ul className="space-y-2 text-muted-foreground">
              <li>1. Detectar keyword e enquadrar a origem do comentário.</li>
              <li>2. Validar se a mensagem aprovada permite resposta pública.</li>
              <li>3. Checar follow signal antes de links ou DM derivada.</li>
              <li>4. Mover para o CRM ou escalar para humano quando houver intenção comercial real.</li>
            </ul>
          </div>
          {renderEntryPointsSummary()}
        </CardContent>
      </Card>
    </div>
  );

  const renderApproved = () => (
    <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Nova mensagem validada</CardTitle>
          <CardDescription>Biblioteca aprovada por canal para DM, comentário público e CTAs oficiais.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input placeholder="Título interno" value={approvedDraft.title} onChange={(event) => setApprovedDraft((current) => ({ ...current, title: event.target.value }))} />
          <Select value={approvedDraft.messageKind} onValueChange={(value) => setApprovedDraft((current) => ({ ...current, messageKind: value }))}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="direct_message">DM</SelectItem>
              <SelectItem value="comment_reply">Comentário público</SelectItem>
              <SelectItem value="entry_point">Entrada guiada</SelectItem>
            </SelectContent>
          </Select>
          <Textarea placeholder="Texto validado para automação ou operação humana assistida..." value={approvedDraft.body} onChange={(event) => setApprovedDraft((current) => ({ ...current, body: event.target.value }))} className="min-h-[180px]" />
          <div className="grid gap-3 md:grid-cols-2">
            <Input placeholder="Label do CTA" value={approvedDraft.ctaLabel} onChange={(event) => setApprovedDraft((current) => ({ ...current, ctaLabel: event.target.value }))} />
            <Input placeholder="URL do CTA" value={approvedDraft.ctaUrl} onChange={(event) => setApprovedDraft((current) => ({ ...current, ctaUrl: event.target.value }))} />
          </div>
          <label className="flex items-center justify-between rounded-lg border p-3 text-sm">
            Exigir following
            <Switch checked={approvedDraft.requiresFollowing} onCheckedChange={(checked) => setApprovedDraft((current) => ({ ...current, requiresFollowing: checked }))} />
          </label>
          <Button onClick={() => createApprovedMutation.mutate()} disabled={createApprovedMutation.isPending || !approvedDraft.title || !approvedDraft.body}>
            {createApprovedMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
            Validar mensagem
          </Button>
        </CardContent>
      </Card>
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Biblioteca aprovada</CardTitle>
          <CardDescription>Separada por rede, com restrições do canal e disponibilidade para automação.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {approvedMessages.map((message) => (
            <div key={message.id} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge className={copy.chipClass}>{message.status}</Badge>
                <Badge variant="outline">{message.messageKind}</Badge>
                {message.requiresFollowing && <Badge variant="secondary">follow gate</Badge>}
              </div>
              <p className="mt-3 font-medium">{message.title}</p>
              <p className="mt-2 whitespace-pre-wrap text-sm text-foreground/80">{message.body}</p>
              {(message.ctaLabel || message.ctaUrl) && (
                <div className="mt-3 rounded-lg bg-muted/30 p-3 text-sm">
                  CTA: {message.ctaLabel || 'Link'} {message.ctaUrl && <span className="text-muted-foreground">· {message.ctaUrl}</span>}
                </div>
              )}
            </div>
          ))}
          {approvedMessages.length === 0 && <EmptyState title="Sem mensagens aprovadas ainda" description="Crie uma biblioteca validada por canal para destravar automações, respostas públicas e campanhas." />}
        </CardContent>
      </Card>
    </div>
  );

  const renderAutomation = () => (
    <div className="grid gap-6 xl:grid-cols-[0.85fr_1.15fr]">
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Nova automação {copy.label}</CardTitle>
          <CardDescription>Regras separadas por rede, com keyword, evento social, gate de follow e fluxo operacional.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input placeholder="Título da regra" value={ruleDraft.title} onChange={(event) => setRuleDraft((current) => ({ ...current, title: event.target.value }))} />
          <Select value={ruleDraft.eventType} onValueChange={(value) => setRuleDraft((current) => ({ ...current, eventType: value }))}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {channel === 'instagram' ? (
                <>
                  <SelectItem value="direct_message">Primeira DM</SelectItem>
                  <SelectItem value="story_reply">Story reply</SelectItem>
                  <SelectItem value="story_mention">Mention</SelectItem>
                  <SelectItem value="keyword_comment">Comentário com keyword</SelectItem>
                </>
              ) : (
                <>
                  <SelectItem value="page_message">Mensagem da Page</SelectItem>
                  <SelectItem value="page_comment">Comentário na Page</SelectItem>
                  <SelectItem value="entry_point">Entry point</SelectItem>
                </>
              )}
            </SelectContent>
          </Select>
          <Input placeholder="Keyword opcional" value={ruleDraft.keyword} onChange={(event) => setRuleDraft((current) => ({ ...current, keyword: event.target.value }))} />
          <Select value={ruleDraft.approvedMessageId || 'none'} onValueChange={(value) => setRuleDraft((current) => ({ ...current, approvedMessageId: value === 'none' ? '' : value }))}>
            <SelectTrigger><SelectValue placeholder="Mensagem aprovada" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="none">Sem mensagem vinculada</SelectItem>
              {approvedMessages.map((message) => (
                <SelectItem key={message.id} value={message.id}>{message.title}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="grid gap-3 md:grid-cols-2">
            <Select value={ruleDraft.targetQueueStatus} onValueChange={(value) => setRuleDraft((current) => ({ ...current, targetQueueStatus: value }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ai">AI</SelectItem>
                <SelectItem value="human">Humano</SelectItem>
                <SelectItem value="follow_up">Follow-up</SelectItem>
                <SelectItem value="closed">Fechado</SelectItem>
              </SelectContent>
            </Select>
            <Select value={ruleDraft.targetColumnId} onValueChange={(value) => setRuleDraft((current) => ({ ...current, targetColumnId: value }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="entrada">Entrada</SelectItem>
                <SelectItem value="qualificacao">Qualificação</SelectItem>
                <SelectItem value="proposta_fechamento">Proposta & fechamento</SelectItem>
                <SelectItem value="coleta_dados">Coleta de dados</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center justify-between rounded-lg border p-3 text-sm">
            Exigir following
            <Switch checked={ruleDraft.requiresFollowing} onCheckedChange={(checked) => setRuleDraft((current) => ({ ...current, requiresFollowing: checked }))} />
          </label>
          <label className="flex items-center justify-between rounded-lg border p-3 text-sm">
            Resposta generativa por IA
            <Switch checked={ruleDraft.aiEnabled} onCheckedChange={(checked) => setRuleDraft((current) => ({ ...current, aiEnabled: checked }))} />
          </label>
          <label className="flex items-center justify-between rounded-lg border p-3 text-sm">
            Preferir resposta pública
            <Switch checked={ruleDraft.publicReply} onCheckedChange={(checked) => setRuleDraft((current) => ({ ...current, publicReply: checked }))} />
          </label>
          <div className="grid gap-3 md:grid-cols-2">
            <Select value={ruleDraft.aiTone} onValueChange={(value) => setRuleDraft((current) => ({ ...current, aiTone: value }))}>
              <SelectTrigger><SelectValue placeholder="Tom da IA" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="clear">Claro</SelectItem>
                <SelectItem value="commercial">Comercial</SelectItem>
                <SelectItem value="empathetic">Empático</SelectItem>
                <SelectItem value="formal">Formal</SelectItem>
              </SelectContent>
            </Select>
            <Input placeholder="Objetivo da IA" value={ruleDraft.aiGoal} onChange={(event) => setRuleDraft((current) => ({ ...current, aiGoal: event.target.value }))} />
          </div>
          <Button onClick={() => createRuleMutation.mutate()} disabled={createRuleMutation.isPending || !ruleDraft.title}>
            {createRuleMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Bot className="mr-2 h-4 w-4" />}
            Salvar automação
          </Button>
        </CardContent>
      </Card>
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Regras ativas</CardTitle>
          <CardDescription>Separadas por rede, sem misturar a operação social com o inbox do WhatsApp.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {(automationQuery.data ?? []).map((rule) => (
            <div key={rule.id} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge className={copy.chipClass}>{rule.eventType}</Badge>
                {rule.keyword && <Badge variant="outline">#{rule.keyword}</Badge>}
                {rule.approvedMessageId && <Badge variant="secondary">mensagem validada</Badge>}
                {(rule.actionConfig.aiEnabled as boolean | undefined) && <Badge variant="secondary">IA generativa</Badge>}
                {(rule.actionConfig.publicReply as boolean | undefined) && <Badge variant="outline">resposta pública</Badge>}
              </div>
              <p className="mt-3 font-medium">{rule.title}</p>
              <p className="mt-2 text-sm text-muted-foreground">
                Queue alvo: {String(rule.actionConfig.targetQueueStatus ?? 'human')} · Etapa: {String(rule.actionConfig.targetColumnId ?? 'qualificacao')}
              </p>
              {(rule.actionConfig.aiGoal as string | undefined) && (
                <p className="mt-2 text-sm text-muted-foreground">
                  Objetivo IA: {String(rule.actionConfig.aiGoal)}
                </p>
              )}
            </div>
          ))}
          {(automationQuery.data ?? []).length === 0 && <EmptyState title="Nenhuma automação ativa" description="Salve uma regra simples para inbound, comentário com keyword, story reply ou entry point deste canal." />}
        </CardContent>
      </Card>
    </div>
  );

  const renderConnections = () => (
    <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Conectar {copy.label}</CardTitle>
          <CardDescription>Ativos oficiais do workspace, separados do WhatsApp e com matriz própria de capabilities.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input placeholder={channel === 'instagram' ? 'Nome da conta profissional' : 'Nome da Page'} value={connectionDraft.accountName} onChange={(event) => setConnectionDraft((current) => ({ ...current, accountName: event.target.value }))} />
          <Input placeholder={channel === 'instagram' ? '@username' : 'Username da Page'} value={connectionDraft.username} onChange={(event) => setConnectionDraft((current) => ({ ...current, username: event.target.value }))} />
          <Input placeholder="External account id" value={connectionDraft.externalAccountId} onChange={(event) => setConnectionDraft((current) => ({ ...current, externalAccountId: event.target.value }))} />
          {channel === 'instagram' ? (
            <Input placeholder="Instagram account id" value={connectionDraft.instagramAccountId} onChange={(event) => setConnectionDraft((current) => ({ ...current, instagramAccountId: event.target.value }))} />
          ) : (
            <Input placeholder="Page id" value={connectionDraft.pageId} onChange={(event) => setConnectionDraft((current) => ({ ...current, pageId: event.target.value }))} />
          )}
          <Input placeholder="Scopes separados por vírgula" value={connectionDraft.scopes} onChange={(event) => setConnectionDraft((current) => ({ ...current, scopes: event.target.value }))} />
          <Button onClick={() => createConnectionMutation.mutate()} disabled={createConnectionMutation.isPending || !connectionDraft.accountName || !connectionDraft.externalAccountId}>
            {createConnectionMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UserPlus className="mr-2 h-4 w-4" />}
            Registrar conexão
          </Button>
        </CardContent>
      </Card>
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Conexões ativas</CardTitle>
          <CardDescription>Cada rede com seu próprio estado, scopes e matriz de superfícies permitidas.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {(connectionsQuery.data ?? []).map((connection) => (
            <div key={connection.id} className="rounded-lg border p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-medium">{connection.accountName || connection.username || connection.externalAccountId}</p>
                  <p className="text-xs text-muted-foreground">{connection.username || connection.externalAccountId}</p>
                </div>
                <Badge className={copy.chipClass}>{connection.status}</Badge>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(connection.capabilityMatrix ?? {}).map(([key, value]) => (
                  <Badge
                    key={key}
                    variant={isCapabilitySupported(value) ? 'secondary' : 'outline'}
                    title={capabilityReason(value) ?? (isCapabilitySupported(value) ? 'Suportado.' : 'Não suportado ainda.')}
                  >
                    {key}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
          {(connectionsQuery.data ?? []).length === 0 && <EmptyState title="Nenhuma conexão cadastrada" description={`Conecte a primeira conta oficial de ${copy.label} para liberar inbox, automações e mensagens validadas.`} />}
        </CardContent>
      </Card>
    </div>
  );

  const renderEntryPointsSummary = () => (
    <div className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Link2 className="h-4 w-4 text-primary" />
        <p className="font-medium">{copy.entryLabel}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {activeCapabilities.map(([key]) => (
          <Badge key={key} variant="outline">{key}</Badge>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        A plataforma só habilita CTAs, links, icebreakers, menus e entry points quando a surface oficial existe para a rede conectada.
      </p>
    </div>
  );

  const renderEntryPoints = () => (
    <div className="grid gap-6 xl:grid-cols-[0.85fr_1.15fr]">
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">{copy.entryLabel}</CardTitle>
          <CardDescription>Ferramentas oficiais do canal para entrada guiada, CTA e governança de outbound.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {renderEntryPointsSummary()}
          <div className="rounded-lg border bg-muted/30 p-4 space-y-2 text-sm">
            <p className="font-medium">Como isso entra no produto</p>
            <p className="text-muted-foreground">
              Cada CTA fica condicionado à capability matrix da conexão. Quando a rede permitir, a UI libera link, entry point, welcome flow, icebreaker, menu ou postback.
            </p>
          </div>
        </CardContent>
      </Card>
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Reengajamento (janela de 24h) e mídia paga</CardTitle>
          <CardDescription>
            Não existe envio em massa orgânico em {copy.label} no Brasil pós-2026. O que dá para fazer sem custo de
            mídia é responder quem já escreveu, dentro da janela de 24h aberta pelo último inbound real. Fora da
            janela, o caminho é mídia paga (Sponsored Messages / Click-to-Messenger).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-start gap-2 rounded-lg border border-amber-300/70 bg-amber-50/60 p-3 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="space-y-1">
              <p className="font-medium">Regras do BR pós-2026 para Messenger/Instagram</p>
              <p>
                Tags de reengajamento (mortas 27/04/2026) e recurring notifications (mortas 10/02/2026 no BR) não
                existem mais. Sobram: (1) janela de 24h desde o último inbound, (2) suporte humano
                (HUMAN_AGENT, restrito a atendimento por pessoa e sujeito a App Review próprio da Meta — ainda não
                habilitado neste produto) e (3) mídia paga via Sponsored Messages / Click-to-Messenger.
              </p>
              <a
                href="https://developers.facebook.com/docs/messenger-platform/policy/policy-overview"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 font-medium underline underline-offset-2"
              >
                Saiba mais na documentação oficial da Meta <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          </div>
          <Input placeholder="Título da campanha" value={campaignDraft.title} onChange={(event) => setCampaignDraft((current) => ({ ...current, title: event.target.value }))} />
          <Textarea placeholder="Resumo operacional" value={campaignDraft.description} onChange={(event) => setCampaignDraft((current) => ({ ...current, description: event.target.value }))} />
          <div className="grid gap-3 md:grid-cols-2">
            <Select value={campaignDraft.connectionId} onValueChange={(value) => setCampaignDraft((current) => ({ ...current, connectionId: value }))}>
              <SelectTrigger><SelectValue placeholder="Conexão" /></SelectTrigger>
              <SelectContent>
                {(connectionsQuery.data ?? []).map((connection) => (
                  <SelectItem key={connection.id} value={connection.id}>{connection.accountName || connection.username || connection.externalAccountId}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={campaignDraft.approvedMessageId || 'none'} onValueChange={(value) => setCampaignDraft((current) => ({ ...current, approvedMessageId: value === 'none' ? '' : value }))}>
              <SelectTrigger><SelectValue placeholder="Mensagem aprovada" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Sem mensagem vinculada</SelectItem>
                {approvedMessages.map((message) => (
                  <SelectItem key={message.id} value={message.id}>{message.title}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="grid gap-1.5">
              <Label htmlFor="campaign-estimated-recipients" className="text-xs text-muted-foreground">Estimados</Label>
              <Input
                id="campaign-estimated-recipients"
                placeholder="Estimados"
                value={campaignDraft.estimatedRecipients}
                onChange={(event) => setCampaignDraft((current) => ({ ...current, estimatedRecipients: event.target.value }))}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="campaign-eligible-recipients" className="text-xs text-muted-foreground">
                Elegíveis (calculado pelo backend)
              </Label>
              <Input
                id="campaign-eligible-recipients"
                value={campaignDraft.eligibleRecipients}
                disabled
                title="Calculado pelo backend a partir da janela de 24h do último inbound real; não é editável."
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="campaign-blocked-recipients" className="text-xs text-muted-foreground">
                Bloqueados (calculado pelo backend)
              </Label>
              <Input
                id="campaign-blocked-recipients"
                value={campaignDraft.blockedRecipients}
                disabled
                title="Calculado pelo backend a partir da janela de 24h do último inbound real; não é editável."
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Elegíveis/bloqueados são somente leitura: o backend recalcula a cada campanha criada ou atualizada a partir
            do último inbound real de cada conversa. Nenhum número digitado aqui é usado para decidir elegibilidade.
          </p>
          <Button onClick={() => createCampaignMutation.mutate()} disabled={createCampaignMutation.isPending || !campaignDraft.title || !campaignDraft.connectionId}>
            {createCampaignMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Hash className="mr-2 h-4 w-4" />}
            Criar campanha
          </Button>
          <Separator />
          {(campaignsQuery.data ?? []).map((campaign) => (
            <div key={campaign.id} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge className={copy.chipClass}>{campaign.status}</Badge>
                <Badge variant="outline">{campaign.totalRecipients} estimados</Badge>
                <Badge variant="secondary">{campaign.eligibleRecipients} elegíveis</Badge>
              </div>
              <p className="mt-3 font-medium">{campaign.title}</p>
              <p className="mt-2 text-sm text-muted-foreground">{campaign.description || 'Sem descrição operacional.'}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );

  const titleMap = {
    inbox: `${copy.surface} ${copy.label}`,
    comments: copy.commentsLabel,
    automation: `Automações ${copy.label}`,
    approved: `Mensagens validadas ${copy.label}`,
    connections: `Conexões ${copy.label}`,
    entrypoints: copy.entryLabel,
  } as const;

  return (
    <div className="space-y-6">
      <div className={`rounded-lg border bg-card p-5 shadow-sm ${copy.accentClass}`}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-3">
            <Badge variant="outline" className={copy.chipClass}>{copy.label}</Badge>
            <SectionHeader
              title={titleMap[mode]}
              description={copy.description}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {activeCapabilities.slice(0, 6).map(([key]) => (
              <Badge key={key} variant="outline" className="bg-background/80 text-foreground">{key}</Badge>
            ))}
            <KlmPill tone="gold">
              <ShieldCheck className="mr-1 h-3 w-3" />
              APIs externas bloqueadas
            </KlmPill>
          </div>
        </div>
      </div>

      <Dialog open={showCreateConversation} onOpenChange={setShowCreateConversation}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Nova conversa {copy.label}</DialogTitle>
            <DialogDescription>
              Cadastro manual para teste, migração ou atendimento iniciado fora do webhook.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="grid gap-1.5">
              <Label>Conexão do canal</Label>
              <Select value={conversationDraft.connectionId} onValueChange={(value) => setConversationDraft((current) => ({ ...current, connectionId: value }))}>
                <SelectTrigger><SelectValue placeholder="Selecione uma conexão" /></SelectTrigger>
                <SelectContent>
                  {(connectionsQuery.data ?? []).map((connection) => (
                    <SelectItem key={connection.id} value={connection.id}>
                      {connection.accountName || connection.username || connection.externalAccountId || connection.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <Input placeholder={channel === 'instagram' ? 'Nome do perfil/lead' : 'Nome do contato da Page'} value={conversationDraft.participantName} onChange={(event) => setConversationDraft((current) => ({ ...current, participantName: event.target.value }))} />
              <Input placeholder={channel === 'instagram' ? '@handle' : 'PSID / handle'} value={conversationDraft.participantHandle} onChange={(event) => setConversationDraft((current) => ({ ...current, participantHandle: event.target.value }))} />
            </div>
            <Input placeholder="Thread externa" value={conversationDraft.externalThreadId} onChange={(event) => setConversationDraft((current) => ({ ...current, externalThreadId: event.target.value }))} />
            <Textarea placeholder={channel === 'instagram' ? 'Mensagem inicial, comentário ou reply...' : 'Mensagem da Page, comentário ou entrada...'} value={conversationDraft.initialMessage} onChange={(event) => setConversationDraft((current) => ({ ...current, initialMessage: event.target.value }))} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCreateConversation(false)}>Cancelar</Button>
            <Button onClick={() => createConversationMutation.mutate()} disabled={createConversationMutation.isPending || !conversationDraft.connectionId || !conversationDraft.participantName || !conversationDraft.externalThreadId}>
              {createConversationMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
              Criar conversa
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {mode === 'inbox' && renderInbox()}
      {mode === 'comments' && renderComments()}
      {mode === 'approved' && renderApproved()}
      {mode === 'automation' && renderAutomation()}
      {mode === 'connections' && renderConnections()}
      {mode === 'entrypoints' && renderEntryPoints()}
    </div>
  );
}
