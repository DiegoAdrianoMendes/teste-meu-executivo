import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import {
  Bot,
  CheckCircle2,
  Clock3,
  ExternalLink,
  EyeOff,
  Facebook,
  Flame,
  Heart,
  Instagram,
  Loader2,
  MessageCircleMore,
  MessageSquareReply,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  Trash2,
  Workflow,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { OperationalCopilot } from '@/klm/components/OperationalCopilot';
import {
  klmApi,
  type KlmOmnichannelActionIntent,
  type KlmChannelConnection,
  type KlmOmnichannelChannel,
  type KlmOmnichannelChannelEvent,
  type KlmOperationalCopilotAction,
} from '@/klm/api';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import { PermissionGate } from '@/klm/components/PermissionGate';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';

type SocialChannel = 'all' | 'instagram' | 'facebook' | 'messenger' | 'tiktok';
type SocialView = 'queue' | 'hot_leads' | 'automation' | 'connections';
type ReplyMode = 'public_reply' | 'private_reply' | 'combo';
type KeywordMode = 'contains' | 'exact' | 'starts_with' | 'ends_with';
type KeywordMatch = 'any' | 'all';
type FollowerStatus = 'any' | 'follows' | 'not_follows';
type VerifiedStatus = 'any' | 'verified' | 'not_verified';
type MessagingWindow = 'any' | 'open' | 'closed';

const channelLabels: Record<Exclude<SocialChannel, 'all'>, string> = {
  instagram: 'Instagram',
  facebook: 'Facebook',
  messenger: 'Messenger',
  tiktok: 'TikTok',
};

const eventLabels: Record<string, string> = {
  post_comment: 'Comentário em publicação',
  page_comment: 'Comentário na página',
  keyword_comment: 'Comentário com palavra-chave',
  story_reply: 'Resposta ao story',
  story_mention: 'Menção no story',
  direct_message: 'Mensagem direta',
  'message.received': 'Mensagem recebida',
};

const actionabilityLabels: Record<string, string> = {
  reply_required: 'Requer resposta',
  urgent: 'Urgente',
  informational: 'Informativo',
};

const capabilityLabels: Record<string, string> = {
  public_reply: 'Resposta pública',
  private_reply: 'Mensagem privada',
  comment_like: 'Curtir comentário',
  comment_hide: 'Ocultar comentário',
  comment_delete: 'Excluir comentário',
};

const visibilityLabels: Record<string, string> = { public: 'público', private: 'privado' };
const transportLabels: Record<string, string> = { official_api: 'API oficial', webhook: 'Integração do site', local: 'Conexão local' };

function actionabilityLabel(value: string) {
  return actionabilityLabels[value] ?? value.replaceAll('_', ' ');
}

function eventBody(item: KlmOmnichannelChannelEvent) {
  if (item.message?.body) return item.message.body;
  const payload = item.event.eventPayload;
  return String(payload.body ?? payload.text ?? payload.comment ?? payload.message ?? 'Evento sem texto');
}

function capabilitySupported(item: KlmOmnichannelChannelEvent, action: string) {
  const capability = item.connection?.capabilities[action];
  return Boolean(item.connection && capability?.supported !== false);
}

function connectionProvider(channel: Exclude<SocialChannel, 'all'>) {
  if (channel === 'instagram') return 'meta_instagram';
  if (channel === 'tiktok') return 'tiktok_business';
  return 'meta_messenger';
}

function connectionChannel(channel: Exclude<SocialChannel, 'all'>): KlmOmnichannelChannel {
  return channel;
}

function isSocialConversationConnection(connection: KlmChannelConnection) {
  if (connection.provider === 'meta_instagram') return connection.channel === 'instagram';
  if (connection.provider === 'meta_messenger') return ['facebook', 'messenger'].includes(connection.channel);
  if (connection.provider === 'tiktok_business') return connection.channel === 'tiktok';
  return false;
}

export default function SocialCommentsPage() {
  const { can, state } = useAppStore();
  const canSend = can('mensagens', 'send_message');
  const canManageFlows = can('automations', 'manage');
  const canManageIntegrations = can('integrations', 'manage');
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [channel, setChannel] = useState<SocialChannel>('all');
  const [view, setView] = useState<SocialView>('queue');
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [replyMode, setReplyMode] = useState<ReplyMode>('combo');
  const [reply, setReply] = useState('');
  const [comboPublicReply, setComboPublicReply] = useState('Enviei uma mensagem privada para entender seu caso com segurança.');
  const [comboLikeComment, setComboLikeComment] = useState(true);
  const [comboPrivateSteps, setComboPrivateSteps] = useState([
    { body: 'Olá. Vi seu comentário e posso entender melhor o que aconteceu. Pode me contar mais detalhes?', delayMinutes: 0 },
    { body: 'Se preferir, envie o documento ou a negativa para analisarmos os próximos passos.', delayMinutes: 10 },
  ]);
  const [notice, setNotice] = useState<string | null>(null);
  const [keyword, setKeyword] = useState('medicamento, negativa, cirurgia');
  const [excludedKeyword, setExcludedKeyword] = useState('emprego, currículo');
  const [keywordMode, setKeywordMode] = useState<KeywordMode>('contains');
  const [keywordMatch, setKeywordMatch] = useState<KeywordMatch>('any');
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [firstLevelOnly, setFirstLevelOnly] = useState(true);
  const [likeComment, setLikeComment] = useState(true);
  const [sourceIds, setSourceIds] = useState('');
  const [followerStatus, setFollowerStatus] = useState<FollowerStatus>('any');
  const [verifiedStatus, setVerifiedStatus] = useState<VerifiedStatus>('any');
  const [messagingWindow, setMessagingWindow] = useState<MessagingWindow>('any');
  const [publicReply, setPublicReply] = useState('Enviei uma mensagem para entender seu caso com privacidade.');
  const [privateReply, setPrivateReply] = useState('Olá. Pode me contar o que foi negado e quando você recebeu a negativa?');
  const [coexistWithAi, setCoexistWithAi] = useState(true);
  const [connectionDraft, setConnectionDraft] = useState({ displayName: '', externalAccountId: '' });

  const eventsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-events', channel],
    queryFn: () => klmApi.omnichannelEvents({
      channel: channel === 'all' ? undefined : channel,
      visibility: 'public',
      limit: 300,
    }),
    retry: false,
  });
  const rulesQuery = useQuery({
    queryKey: ['klm', 'omnichannel-automation-rules'],
    queryFn: klmApi.omnichannelAutomationRules,
    enabled: view === 'automation' && canManageFlows,
    retry: false,
  });
  const connectionsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-connections'],
    queryFn: () => klmApi.omnichannelConnections(),
    enabled: view === 'connections' || view === 'queue',
    retry: false,
  });
  // Leads sociais nascem de DMs (client_private), não do feed público de comentários —
  // por isso a busca é separada, sem o filtro visibility: 'public' da fila principal.
  const socialLeadsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-events', 'social-leads', channel],
    queryFn: () => klmApi.omnichannelEvents({
      channel: channel === 'all' ? undefined : channel,
      limit: 300,
    }),
    enabled: view === 'hot_leads',
    retry: false,
  });

  const allEvents = useMemo(() => eventsQuery.data?.items ?? [], [eventsQuery.data?.items]);
  const socialLeads = useMemo(() => {
    const seenThreads = new Set<string>();
    const leads: typeof allEvents = [];
    for (const item of socialLeadsQuery.data?.items ?? []) {
      const socialLead = item.thread.threadMetadata?.socialLead as { origin?: string; hot?: boolean } | undefined;
      if (!item.thread.matterId || !socialLead) continue;
      if (seenThreads.has(item.thread.id)) continue;
      seenThreads.add(item.thread.id);
      leads.push(item);
    }
    return leads.sort((a, b) => (b.thread.priority ?? 0) - (a.thread.priority ?? 0));
  }, [socialLeadsQuery.data?.items]);
  const hotSocialLeads = socialLeads.filter((item) => (item.thread.threadMetadata?.socialLead as { hot?: boolean } | undefined)?.hot);
  const personalizedFields = useMemo<PersonalizedFilterField<KlmOmnichannelChannelEvent>[]>(() => [
    { key: 'identity.displayName', label: 'Lead', type: 'text' },
    { key: 'channel', label: 'Canal', type: 'select', options: Object.entries(channelLabels).map(([value, label]) => ({ value, label })) },
    { key: 'event.eventType', label: 'Tipo de evento', type: 'select', options: [...new Set(allEvents.map((item) => item.event.eventType))].map((value) => ({ value, label: eventLabels[value] ?? value })) },
    { key: 'event.actionability', label: 'Ação necessária', type: 'select', options: [...new Set(allEvents.map((item) => item.event.actionability))].map((value) => ({ value, label: actionabilityLabel(value) })) },
    { key: 'body', label: 'Texto do comentário', type: 'text', getValue: eventBody },
    { key: 'event.occurredAt', label: 'Data do comentário', type: 'date' },
    { key: 'connection.id', label: 'Conexão configurada', type: 'boolean', getValue: (item) => Boolean(item.connection?.id) },
    { key: 'thread.matterId', label: 'Vinculado a caso', type: 'boolean', getValue: (item) => Boolean(item.thread.matterId) },
  ], [allEvents]);
  const visibleEvents = useMemo(() => {
    const query = search.trim().toLocaleLowerCase('pt-BR');
    return allEvents.filter((item) => (!query || `${item.identity?.displayName ?? ''} ${eventBody(item)} ${item.event.eventType}`.toLocaleLowerCase('pt-BR').includes(query))
      && matchesPersonalizedFilter(item, personalizedFilter, personalizedFields));
  }, [allEvents, personalizedFields, personalizedFilter, search]);
  const selectedEvent = visibleEvents.find((item) => item.event.id === selectedEventId)
    ?? visibleEvents[0]
    ?? null;

  useEffect(() => {
    if (!selectedEventId && visibleEvents[0]) setSelectedEventId(visibleEvents[0].event.id);
    if (selectedEventId && !visibleEvents.some((item) => item.event.id === selectedEventId)) {
      setSelectedEventId(visibleEvents[0]?.event.id ?? null);
    }
  }, [selectedEventId, visibleEvents]);

  useEffect(() => {
    setReply('');
    setNotice(null);
  }, [selectedEvent?.event.id]);

  const socialConnections = (connectionsQuery.data ?? []).filter(isSocialConversationConnection);
  const unanswered = allEvents.filter((item) => item.event.actionability === 'reply_required').length;
  const urgent = allEvents.filter((item) => item.event.actionability === 'urgent').length;
  const configuredChannels = new Set(socialConnections.map((item) => item.channel)).size;
  const copilotContext = useMemo(() => ({
    channel,
    view,
    counts: {
      visible_events: visibleEvents.length,
      unanswered,
      urgent,
      hot_leads: hotSocialLeads.length,
      configured_channels: configuredChannels,
    },
    selected_event: selectedEvent ? {
      id: selectedEvent.event.id,
      channel: selectedEvent.channel,
      event_type: selectedEvent.event.eventType,
      actionability: selectedEvent.event.actionability,
      has_connection: Boolean(selectedEvent.connection?.id),
    } : null,
    suggested_reply: publicReply,
  }), [
    channel,
    configuredChannels,
    hotSocialLeads.length,
    publicReply,
    selectedEvent,
    unanswered,
    urgent,
    view,
    visibleEvents.length,
  ]);

  const invalidateEvents = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-events'] });
    if (selectedEvent) void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-context', selectedEvent.thread.id] });
  };

  const actionMutation = useMutation({
    mutationFn: ({ actionType, body }: { actionType: KlmOmnichannelActionIntent['actionType']; body?: string }) => {
      if (!selectedEvent) throw new Error('Selecione um comentário.');
      if (['public_reply', 'private_reply'].includes(actionType) && !body?.trim()) throw new Error('Escreva a resposta.');
      return klmApi.createOmnichannelAction(selectedEvent.thread.id, {
        eventId: selectedEvent.event.id,
        connectionId: selectedEvent.connection?.id ?? null,
        actionType: actionType as 'public_reply' | 'private_reply' | 'comment_like' | 'comment_hide' | 'comment_delete',
        targetExternalId: selectedEvent.event.externalEventId,
        body: body?.trim() || null,
        payload: { source: 'social_center', externalExecution: false },
      });
    },
    onSuccess: (result) => {
      setReply('');
      setNotice(result.state === 'blocked_external' ? 'Ação preparada e auditada. A chamada oficial continua bloqueada até a etapa de APIs.' : 'Ação registrada.');
      invalidateEvents();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao preparar a ação.'),
  });

  const comboMutation = useMutation({
    mutationFn: async () => {
      if (!selectedEvent) throw new Error('Selecione um comentário.');
      if (!comboPublicReply.trim() || !comboPrivateSteps.some((step) => step.body.trim())) throw new Error('Preencha o comentário e ao menos uma mensagem privada.');
      const createAction = (actionType: 'public_reply' | 'private_reply' | 'comment_like', body: string | null, payload: Record<string, unknown>) => klmApi.createOmnichannelAction(selectedEvent.thread.id, {
        eventId: selectedEvent.event.id,
        connectionId: selectedEvent.connection?.id ?? null,
        actionType,
        targetExternalId: selectedEvent.event.externalEventId,
        body,
        payload: { source: 'social_center_combo', externalExecution: false, ...payload },
      });
      if (comboLikeComment && capabilitySupported(selectedEvent, 'comment_like')) await createAction('comment_like', null, { sequenceIndex: 0 });
      await createAction('public_reply', comboPublicReply.trim(), { sequenceIndex: 1 });
      for (const [index, step] of comboPrivateSteps.entries()) {
        if (step.body.trim()) await createAction('private_reply', step.body.trim(), { sequenceIndex: index + 2, delayMinutes: Math.max(0, step.delayMinutes) });
      }
    },
    onSuccess: () => {
      setNotice('Combo preparado e auditado na ordem definida. Nenhuma mensagem externa foi enviada.');
      invalidateEvents();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao preparar o combo.'),
  });

  const createRuleMutation = useMutation({
    mutationFn: () => {
      const ruleChannel = channel === 'all' ? 'instagram' : channel;
      const keywords = keyword.split(',').map((item) => item.trim()).filter(Boolean);
      const excludedKeywords = excludedKeyword.split(',').map((item) => item.trim()).filter(Boolean);
      const selectedSourceIds = sourceIds.split(',').map((item) => item.trim()).filter(Boolean);
      if (!keywords.length) throw new Error('Informe ao menos uma palavra-chave.');
      const actions: Array<Record<string, unknown>> = [];
      if (likeComment) actions.push({ key: 'curtir-comentario', type: 'comment_like' });
      if (publicReply.trim()) actions.push({ key: 'resposta-publica', type: 'public_reply', body: publicReply.trim() });
      if (privateReply.trim()) actions.push({ key: 'resposta-privada', type: 'private_reply', body: privateReply.trim(), delaySeconds: 3 });
      actions.push({ key: 'etiquetar-social', type: 'add_tag', value: 'origem_social' });
      actions.push({ key: 'tarefa-triagem', type: 'create_task', title: 'Revisar lead originado em comentário' });
      return klmApi.createOmnichannelAutomationRule({
        name: `Comentários ${channelLabels[ruleChannel]}`,
        slug: `comentarios-${ruleChannel}-${Date.now()}`,
        description: 'Regra criada na Central Social e executada no runtime canônico.',
        channel: connectionChannel(ruleChannel),
        triggerType: 'keyword_comment',
        status: 'active',
        priority: 80,
        coexistWithAi,
        stopOnMatch: false,
        triggerConfig: {
          keywords,
          excludedKeywords,
          keywordMode,
          match: keywordMatch,
          caseSensitive,
          firstLevelOnly,
          sourceIds: selectedSourceIds,
          followerStatus,
          verifiedStatus,
          messagingWindow,
        },
        conditions: [{ field: 'visibility', operator: 'equals', value: 'public' }],
        actions,
        rateLimits: { perIdentityPerHour: 3, globalPerMinute: 30 },
      });
    },
    onSuccess: () => {
      setNotice('Automação ativa, versionada e pronta para simulação local.');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-automation-rules'] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao criar automação.'),
  });

  const ruleStateMutation = useMutation({
    mutationFn: ({ id, version, active }: { id: string; version: number; active: boolean }) => klmApi.updateOmnichannelAutomationRule(id, { expectedVersion: version, status: active ? 'active' : 'paused' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-automation-rules'] }),
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao atualizar automação.'),
  });

  const validateConnectionMutation = useMutation({
    mutationFn: (connectionId: string) => klmApi.validateOmnichannelConnection(connectionId),
    onSuccess: (result) => setNotice(result.readyForExternalSetup ? 'Conexão íntegra localmente e pronta para receber credenciais depois da aprovação.' : `Faltam: ${result.missingConfiguration.join(', ') || 'configurações do provedor'}.`),
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha na validação local.'),
  });

  const createConnectionMutation = useMutation({
    mutationFn: () => {
      const draftChannel = channel === 'all' ? 'instagram' : channel;
      if (!connectionDraft.displayName.trim()) throw new Error('Informe um nome para a conexão.');
      const key = `${draftChannel}:${connectionDraft.displayName.toLocaleLowerCase('pt-BR').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`;
      return klmApi.createOmnichannelConnection({
        channel: connectionChannel(draftChannel),
        provider: connectionProvider(draftChannel),
        connectionKey: key,
        displayName: connectionDraft.displayName.trim(),
        externalAccountId: connectionDraft.externalAccountId.trim() || null,
        transportMode: 'official_api',
        credentialReference: `vault://pending/${key}`,
        configuration: {},
      });
    },
    onSuccess: () => {
      setConnectionDraft({ displayName: '', externalAccountId: '' });
      setNotice('Conexão cadastrada localmente. Nenhum login externo foi iniciado.');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao cadastrar conexão.'),
  });

  const applyCopilotActions = async (actions: KlmOperationalCopilotAction[]) => {
    for (const action of actions) {
      const actionType = typeof action.payload.type === 'string' ? action.payload.type : '';
      if (actionType === 'social.select_view') {
        const nextView = typeof action.payload.view === 'string'
          && ['queue', 'hot_leads', 'automation', 'connections'].includes(action.payload.view)
          ? action.payload.view as SocialView
          : 'queue';
        setView(nextView);
        continue;
      }
      if (actionType === 'social.select_channel') {
        const nextChannel = typeof action.payload.channel === 'string'
          && ['all', 'instagram', 'facebook', 'messenger', 'tiktok'].includes(action.payload.channel)
          ? action.payload.channel as SocialChannel
          : 'all';
        setChannel(nextChannel);
        setSelectedEventId(null);
        continue;
      }
      if (actionType === 'social.prepare_reply') {
        if (!canSend) throw new Error('Seu perfil não pode preparar respostas.');
        if (!selectedEvent) throw new Error('Selecione uma interação antes de preparar a resposta.');
        const nextMode: ReplyMode = action.payload.mode === 'private'
          ? 'private_reply'
          : 'public_reply';
        setReplyMode(nextMode);
        setReply(nextMode === 'private_reply' ? privateReply : publicReply);
        setView('queue');
        continue;
      }
      if (actionType === 'social.prepare_rule') {
        if (!canManageFlows) throw new Error('Seu perfil não pode preparar automações.');
        if (typeof action.payload.keyword === 'string' && action.payload.keyword.trim()) {
          setKeyword(action.payload.keyword.trim().slice(0, 500));
        }
        setCaseSensitive(action.payload.case_sensitive === true);
        setView('automation');
      }
    }
    return 'A proposta foi aplicada aos editores. Nenhuma resposta foi enviada e nenhuma regra foi ativada.';
  };

  return (
    <div className="mx-auto max-w-[1280px] space-y-4">
      <header className="flex flex-col gap-3 border-b border-border pb-4 xl:flex-row xl:items-end xl:justify-between">
        <div><p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Social</p><h1 className="mt-1 text-2xl font-semibold">Comentários e mensagens sociais</h1><p className="mt-1 text-2xs text-muted-foreground">Cada interação entra na mesma conversa e no mesmo card do lead, independentemente do canal.</p></div>
        <div className="flex flex-wrap gap-2">
          {(['all', 'instagram', 'facebook', 'messenger', 'tiktok'] as SocialChannel[]).map((item) => <Button key={item} variant="outline" size="sm" disabled={item === 'tiktok'} title={item === 'tiktok' ? 'TikTok ficará disponível em uma próxima etapa' : undefined} className={cn('h-8 px-2.5 text-2xs', channel === item && 'border-[#031121] bg-[#031121] text-white hover:bg-[#031121] hover:text-white dark:border-primary dark:bg-primary dark:text-black')} onClick={() => { setChannel(item); setSelectedEventId(null); }}>{item === 'tiktok' && <Clock3 className="mr-1.5 h-3.5 w-3.5" />}{item === 'all' ? 'Todos' : channelLabels[item]}{item === 'tiktok' ? ' · em breve' : ''}</Button>)}
          <PersonalizedFilterBuilder scope="social-comments" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} />
        </div>
      </header>

      <div className="grid gap-2 sm:grid-cols-4">
        {[['Interações públicas', allEvents.length], ['Pedem resposta', unanswered], ['Urgentes', urgent], ['Canais configurados', configuredChannels]].map(([label, value]) => <KlmPanel key={String(label)} className="p-3"><p className="text-xs uppercase text-muted-foreground">{label}</p><strong className="mt-1 block text-2xl font-bold tabular-nums">{value}</strong></KlmPanel>)}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {([['queue', 'Fila social', MessageCircleMore], ['hot_leads', 'Leads quentes', Flame], ['automation', 'Automações', Workflow], ['connections', 'Conexões', Settings2]] as const).map(([key, label, Icon]) => <Button key={key} variant={view === key ? 'default' : 'outline'} size="sm" className={cn('h-8 text-2xs', view === key && 'bg-[#031121] text-white hover:bg-[#031121]')} onClick={() => setView(key)}><Icon className="mr-1.5 h-3.5 w-3.5" />{label}{key === 'hot_leads' && hotSocialLeads.length > 0 && <KlmPill tone="red" className="ml-1.5">{hotSocialLeads.length}</KlmPill>}</Button>)}
        <KlmPill tone="gold" className="ml-auto"><ShieldCheck className="mr-1 h-3 w-3" />APIs externas bloqueadas</KlmPill>
      </div>

      {notice && <div className="flex items-center justify-between gap-3 rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      {view === 'queue' && (
        <div className="grid min-h-[640px] gap-3 lg:grid-cols-[320px_minmax(0,1fr)]">
          <KlmPanel className="flex min-h-0 flex-col overflow-hidden">
            <div className="border-b p-3"><Input value={search} onChange={(event) => setSearch(event.target.value)} className="h-8 bg-secondary text-2xs" placeholder="Buscar pessoa, comentário ou palavra-chave" /></div>
            <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
              {eventsQuery.isPending && <p className="flex items-center justify-center gap-2 py-10 text-2xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Carregando eventos</p>}
              {eventsQuery.isError && <div className="py-8 text-center"><p className="text-2xs text-red-700">A fila social não respondeu.</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void eventsQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button></div>}
              {eventsQuery.isSuccess && !visibleEvents.length && <p className="px-3 py-10 text-center text-2xs text-muted-foreground">Nenhum comentário canônico neste filtro.</p>}
              {visibleEvents.map((item) => <button key={item.event.id} type="button" onClick={() => setSelectedEventId(item.event.id)} className={cn('w-full rounded-md border p-3 text-left', selectedEvent?.event.id === item.event.id ? 'border-[#031121] bg-[#031121] text-white' : 'border-border bg-card hover:border-primary')}><div className="flex items-center justify-between gap-2"><strong className="truncate text-2xs">{item.identity?.displayName ?? 'Lead sem identificação'}</strong><span className="shrink-0 text-2xs opacity-70">{new Date(item.event.occurredAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</span></div><p className="mt-1 line-clamp-2 text-2xs leading-4 opacity-85">{eventBody(item)}</p><div className="mt-2 flex items-center gap-1.5"><KlmPill tone={item.event.actionability === 'urgent' ? 'red' : item.event.actionability === 'reply_required' ? 'gold' : 'muted'}>{eventLabels[item.event.eventType] ?? item.event.eventType}</KlmPill><span className="ml-auto text-2xs opacity-75">{channelLabels[item.channel as Exclude<SocialChannel, 'all'>] ?? item.channel}</span></div></button>)}
            </div>
          </KlmPanel>

          <KlmPanel className="min-h-0 overflow-y-auto">
            {selectedEvent ? <div className="divide-y divide-border">
              <section className="space-y-3 p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><p className="text-2xs text-muted-foreground">{eventLabels[selectedEvent.event.eventType] ?? selectedEvent.event.eventType}</p><h2 className="mt-1 text-lg font-semibold">{selectedEvent.identity?.displayName ?? 'Lead sem identificação'}</h2><p className="mt-1 text-2xs text-muted-foreground">{selectedEvent.connection?.displayName ?? 'Canal social'} · {transportLabels[selectedEvent.connection?.transportMode ?? ''] ?? 'conexão configurada'}</p></div><div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => navigate(`/app/conversas?thread=${selectedEvent.thread.id}${selectedEvent.thread.matterId ? `&matter=${selectedEvent.thread.matterId}` : ''}`)}><MessageSquareReply className="mr-1.5 h-3 w-3" />Abrir atendimento</Button>{selectedEvent.event.publicUrl && <Button asChild variant="outline" size="icon" className="h-8 w-8"><a href={selectedEvent.event.publicUrl} target="_blank" rel="noreferrer" title="Abrir publicação" aria-label="Abrir publicação"><ExternalLink className="h-3.5 w-3.5" /></a></Button>}</div></div>
                <div className="border-l-2 border-primary pl-3"><p className="text-xs leading-5">{eventBody(selectedEvent)}</p><p className="mt-2 text-2xs text-muted-foreground">Recebido em {new Date(selectedEvent.event.receivedAt).toLocaleString('pt-BR')} · {visibilityLabels[selectedEvent.event.visibility] ?? 'visibilidade definida'} · {actionabilityLabel(selectedEvent.event.actionability)}</p></div>
                <div className="flex flex-wrap gap-1.5">{Object.entries(selectedEvent.connection?.capabilities ?? {}).filter(([key]) => capabilityLabels[key]).map(([key, value]) => <KlmPill key={key} tone={value.supported === false ? 'muted' : 'green'}>{capabilityLabels[key]}: {value.supported === false ? 'indisponível' : 'disponível'}</KlmPill>)}</div>
              </section>
              <section className="space-y-3 p-4">
                <div role="group" aria-label="Tipo de resposta" className="inline-flex max-w-full flex-wrap rounded-md border bg-secondary p-0.5">
                  <button type="button" className={cn('h-7 rounded px-3 text-2xs font-semibold', replyMode === 'combo' && 'bg-card shadow-sm')} onClick={() => setReplyMode('combo')}>Comentário + sequência</button>
                  <button type="button" className={cn('h-7 rounded px-3 text-2xs font-semibold', replyMode === 'public_reply' && 'bg-card shadow-sm')} onClick={() => setReplyMode('public_reply')}>Só comentário</button>
                  <button type="button" className={cn('h-7 rounded px-3 text-2xs font-semibold', replyMode === 'private_reply' && 'bg-card shadow-sm')} onClick={() => setReplyMode('private_reply')}>Só mensagem privada</button>
                </div>
                {replyMode === 'combo' ? (
                  <div className="space-y-3 rounded-md border bg-secondary/50 p-3">
                    <div><label className="text-2xs font-semibold">Resposta ao comentário</label><Textarea value={comboPublicReply} onChange={(event) => setComboPublicReply(event.target.value)} className="mt-1.5 min-h-[72px] resize-none bg-card text-2xs" /></div>
                    <div className="flex items-center justify-between gap-3 rounded-md border bg-card px-3 py-2"><span className="text-2xs">Curtir o comentário antes de responder</span><Switch aria-label="Curtir o comentário antes de responder" checked={comboLikeComment} onCheckedChange={setComboLikeComment} /></div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between"><label className="text-2xs font-semibold">Sequência privada</label><Button type="button" variant="outline" size="sm" className="h-7 text-2xs" onClick={() => setComboPrivateSteps((steps) => [...steps, { body: '', delayMinutes: 30 }])}><Plus className="mr-1 h-3 w-3" />Mensagem</Button></div>
                      {comboPrivateSteps.map((step, index) => <div key={index} className="grid gap-2 rounded-md border bg-card p-2 sm:grid-cols-[minmax(0,1fr)_100px_28px]"><Textarea aria-label={`Mensagem privada ${index + 1}`} value={step.body} onChange={(event) => setComboPrivateSteps((steps) => steps.map((item, itemIndex) => itemIndex === index ? { ...item, body: event.target.value } : item))} className="min-h-[68px] resize-none text-2xs" placeholder={`Mensagem privada ${index + 1}`} /><label className="text-2xs text-muted-foreground">Espera (min)<Input type="number" min={0} value={step.delayMinutes} onChange={(event) => setComboPrivateSteps((steps) => steps.map((item, itemIndex) => itemIndex === index ? { ...item, delayMinutes: Number(event.target.value) || 0 } : item))} className="mt-1 h-8 text-2xs" /></label><Button type="button" variant="ghost" size="icon" className="h-8 w-8 text-red-700" disabled={comboPrivateSteps.length === 1} onClick={() => setComboPrivateSteps((steps) => steps.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remover mensagem ${index + 1}`}><Trash2 className="h-3.5 w-3.5" /></Button></div>)}
                    </div>
                  </div>
                ) : <Textarea value={reply} onChange={(event) => setReply(event.target.value)} disabled={!canSend || !capabilitySupported(selectedEvent, replyMode)} className="min-h-[108px] resize-none bg-secondary text-2xs" placeholder={capabilitySupported(selectedEvent, replyMode) ? 'Escreva a resposta que será preparada...' : 'Esta ação não está disponível na conexão selecionada.'} />}
                <div className="flex flex-wrap gap-2"><Button size="sm" className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={replyMode === 'combo' ? !canSend || !comboPublicReply.trim() || !comboPrivateSteps.some((step) => step.body.trim()) || !capabilitySupported(selectedEvent, 'public_reply') || !capabilitySupported(selectedEvent, 'private_reply') || comboMutation.isPending : !canSend || !reply.trim() || !capabilitySupported(selectedEvent, replyMode) || actionMutation.isPending} onClick={() => replyMode === 'combo' ? comboMutation.mutate() : actionMutation.mutate({ actionType: replyMode, body: reply })}><Send className="mr-1.5 h-3 w-3" />{replyMode === 'combo' ? 'Preparar combo' : 'Preparar resposta'}</Button><Button variant="outline" size="sm" className="h-8 text-2xs" disabled={!canSend || !capabilitySupported(selectedEvent, 'comment_like') || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'comment_like' })}><Heart className="mr-1.5 h-3 w-3" />Curtir</Button><Button variant="outline" size="sm" className="h-8 text-2xs" disabled={!canSend || !capabilitySupported(selectedEvent, 'comment_hide') || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'comment_hide' })}><EyeOff className="mr-1.5 h-3 w-3" />Ocultar</Button><Button variant="outline" size="sm" className="h-8 text-2xs text-red-700" disabled={!canSend || !capabilitySupported(selectedEvent, 'comment_delete') || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'comment_delete' })}><Trash2 className="mr-1.5 h-3 w-3" />Excluir</Button></div>
                <p className="text-2xs leading-4 text-muted-foreground">As ações permanecem em estado local auditável até as credenciais oficiais e a liberação do ambiente externo.</p>
              </section>
            </div> : <div className="flex min-h-[500px] items-center justify-center p-8 text-center text-2xs text-muted-foreground">Selecione uma interação na fila.</div>}
          </KlmPanel>
        </div>
      )}

      {view === 'hot_leads' && (
        <KlmPanel className="overflow-hidden">
          <div className="border-b p-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-medium">Leads quentes de story</h2>
                <p className="mt-1 text-2xs leading-4 text-muted-foreground">Threads com caso já aberto a partir de resposta ou menção em story, comentário ou mensagem direta.</p>
              </div>
              <KlmPill tone="muted">{socialLeads.length}</KlmPill>
            </div>
          </div>
          <div className="divide-y divide-border">
            {socialLeadsQuery.isPending && <p className="p-8 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando leads sociais</p>}
            {socialLeadsQuery.isError && <div className="p-8 text-center"><p className="text-2xs text-red-700">Os leads sociais não responderam.</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void socialLeadsQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button></div>}
            {socialLeadsQuery.isSuccess && !socialLeads.length && <p className="p-8 text-center text-2xs text-muted-foreground">Nenhum lead social com caso aberto ainda.</p>}
            {socialLeads.map((item) => {
              const socialLead = item.thread.threadMetadata?.socialLead as {
                origin?: string;
                hot?: boolean;
                qualification?: {
                  step?: string;
                  completed?: boolean;
                  collectedFields?: string[];
                  missingFields?: string[];
                  score?: number | null;
                  classification?: string | null;
                };
                lastDraft?: {
                  body?: string;
                  audience?: string;
                  createdAt?: string;
                  policyGate?: { allowed?: boolean; reasonCode?: string };
                };
              } | undefined;
              const originLabel = socialLead?.origin === 'story' ? 'Story' : socialLead?.origin === 'comment' ? 'Comentário' : 'Mensagem direta';
              const qualification = socialLead?.qualification;
              const draft = socialLead?.lastDraft;
              return (
                <div key={item.thread.id} className="flex items-start gap-3 p-4">
                  <div className={cn('mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md', socialLead?.hot ? 'bg-red-100 text-red-700' : 'bg-secondary')}>
                    {item.channel === 'instagram' ? <Instagram className="h-4 w-4" /> : <Facebook className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="truncate text-2xs">{item.identity?.displayName ?? 'Lead sem identificação'}</strong>
                      {socialLead?.hot && <KlmPill tone="red"><Flame className="mr-1 h-3 w-3" />Lead quente</KlmPill>}
                      <KlmPill tone="blue">{originLabel}</KlmPill>
                      <span className="text-2xs text-muted-foreground">{channelLabels[item.channel as Exclude<SocialChannel, 'all'>] ?? item.channel} · prioridade {item.thread.priority ?? 0}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-2xs leading-4 text-muted-foreground">{eventBody(item)}</p>
                    {qualification && (
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <KlmPill tone={qualification.completed ? 'green' : 'gold'}>
                          {qualification.completed ? 'Qualificação concluída' : `Qualificando · ${qualification.collectedFields?.length ?? 0}/${(qualification.collectedFields?.length ?? 0) + (qualification.missingFields?.length ?? 0)} campos`}
                        </KlmPill>
                        {!qualification.completed && qualification.missingFields && qualification.missingFields.length > 0 && (
                          <span className="text-2xs text-muted-foreground">Faltam: {qualification.missingFields.join(', ')}</span>
                        )}
                      </div>
                    )}
                    {draft?.body && (
                      <div className="mt-2 rounded-md border border-dashed border-border bg-secondary/40 p-2">
                        <p className="text-2xs font-semibold uppercase text-muted-foreground">
                          {draft.policyGate?.allowed ? 'Rascunho preparado pelo SDR (IA)' : 'Nota interna — janela fechada'}
                        </p>
                        <p className="mt-1 line-clamp-2 text-2xs leading-4">{draft.body}</p>
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col gap-1.5">
                    <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => navigate(`/app/conversas?thread=${item.thread.id}${item.thread.matterId ? `&matter=${item.thread.matterId}` : ''}`)}><MessageSquareReply className="mr-1.5 h-3 w-3" />Abrir conversa</Button>
                    {draft?.body && (
                      <Button size="sm" className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" onClick={() => navigate(`/app/conversas?thread=${item.thread.id}${item.thread.matterId ? `&matter=${item.thread.matterId}` : ''}&review=draft`)}><CheckCircle2 className="mr-1.5 h-3 w-3" />Revisar e aprovar</Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </KlmPanel>
      )}

      {view === 'automation' && (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
          <KlmPanel className="p-4">
            <div className="flex items-start justify-between"><div><h2 className="text-base font-medium">Automação por comentário</h2><p className="mt-1 text-2xs leading-4 text-muted-foreground">Responde, chama no privado, etiqueta e cria tarefa no mesmo atendimento.</p></div><Bot className="h-4 w-4 text-[#0057d8]" /></div>
            <div className="mt-4 space-y-3">
              <div><label className="text-2xs font-semibold uppercase">Palavras-chave incluídas</label><Input value={keyword} onChange={(event) => setKeyword(event.target.value)} className="mt-1.5 h-9 text-2xs" placeholder="medicamento, cirurgia, home care" /></div>
              <div><label className="text-2xs font-semibold uppercase">Ignorar quando contiver</label><Input value={excludedKeyword} onChange={(event) => setExcludedKeyword(event.target.value)} className="mt-1.5 h-9 text-2xs" placeholder="emprego, currículo" /></div>
              <div className="grid grid-cols-2 gap-2">
                <div><label className="text-2xs font-semibold uppercase">Comparação</label><select aria-label="Modo de comparação" value={keywordMode} onChange={(event) => setKeywordMode(event.target.value as KeywordMode)} className="mt-1.5 h-9 w-full rounded-md border border-input bg-background px-2 text-2xs"><option value="contains">Contém</option><option value="exact">É exatamente</option><option value="starts_with">Começa com</option><option value="ends_with">Termina com</option></select></div>
                <div><label className="text-2xs font-semibold uppercase">Regra</label><select aria-label="Combinação das palavras" value={keywordMatch} onChange={(event) => setKeywordMatch(event.target.value as KeywordMatch)} className="mt-1.5 h-9 w-full rounded-md border border-input bg-background px-2 text-2xs"><option value="any">Qualquer palavra</option><option value="all">Todas as palavras</option></select></div>
              </div>
              <div><label className="text-2xs font-semibold uppercase">Publicações específicas</label><Input value={sourceIds} onChange={(event) => setSourceIds(event.target.value)} className="mt-1.5 h-9 text-2xs" placeholder="IDs separados por vírgula; vazio = todas" /></div>
              <div className="grid gap-2 rounded-md border p-3">
                <p className="text-2xs font-semibold uppercase">Condições do perfil Meta</p>
                <div className="grid grid-cols-3 gap-2">
                  <div><label className="text-2xs text-muted-foreground">Segue a conta</label><select aria-label="Condição de seguidor" value={followerStatus} onChange={(event) => setFollowerStatus(event.target.value as FollowerStatus)} className="mt-1 h-9 w-full rounded-md border border-input bg-background px-2 text-2xs"><option value="any">Qualquer</option><option value="follows">Segue</option><option value="not_follows">Não segue</option></select></div>
                  <div><label className="text-2xs text-muted-foreground">Perfil verificado</label><select aria-label="Condição de verificação" value={verifiedStatus} onChange={(event) => setVerifiedStatus(event.target.value as VerifiedStatus)} className="mt-1 h-9 w-full rounded-md border border-input bg-background px-2 text-2xs"><option value="any">Qualquer</option><option value="verified">Verificado</option><option value="not_verified">Não verificado</option></select></div>
                  <div><label className="text-2xs text-muted-foreground">Janela de DM</label><select aria-label="Janela de mensagem" value={messagingWindow} onChange={(event) => setMessagingWindow(event.target.value as MessagingWindow)} className="mt-1 h-9 w-full rounded-md border border-input bg-background px-2 text-2xs"><option value="any">Qualquer</option><option value="open">Aberta</option><option value="closed">Fechada</option></select></div>
                </div>
                <p className="text-2xs leading-4 text-muted-foreground">Condições restritivas só passam quando a conexão oficial entregou o dado. “Desconhecido” nunca é tratado como “não segue”.</p>
              </div>
              <div className="grid gap-2 rounded-md border p-3">
                <label className="flex items-center justify-between gap-3"><span><span className="block text-2xs font-semibold">Diferenciar maiúsculas</span><span className="block text-2xs text-muted-foreground">“Câncer” e “câncer” deixam de ser iguais.</span></span><Switch aria-label="Diferenciar maiúsculas" checked={caseSensitive} onCheckedChange={setCaseSensitive} /></label>
                <label className="flex items-center justify-between gap-3"><span><span className="block text-2xs font-semibold">Somente comentário principal</span><span className="block text-2xs text-muted-foreground">Ignora respostas encadeadas a outro comentário.</span></span><Switch aria-label="Somente comentário principal" checked={firstLevelOnly} onCheckedChange={setFirstLevelOnly} /></label>
                <label className="flex items-center justify-between gap-3"><span><span className="block text-2xs font-semibold">Curtir antes de responder</span><span className="block text-2xs text-muted-foreground">Prepara a curtida no mesmo run auditável.</span></span><Switch aria-label="Curtir antes de responder" checked={likeComment} onCheckedChange={setLikeComment} /></label>
              </div>
              <div><label className="text-2xs font-semibold uppercase">Resposta pública</label><Textarea value={publicReply} onChange={(event) => setPublicReply(event.target.value)} className="mt-1.5 min-h-[72px] resize-none text-2xs" /></div>
              <div><label className="text-2xs font-semibold uppercase">Mensagem privada</label><Textarea value={privateReply} onChange={(event) => setPrivateReply(event.target.value)} className="mt-1.5 min-h-[88px] resize-none text-2xs" /></div>
              <div className="flex items-center justify-between border-y border-border py-3"><div><p className="text-2xs font-semibold">Executar junto com a IA</p><p className="mt-0.5 text-2xs text-muted-foreground">O flow não interrompe o agente.</p></div><Switch aria-label="Executar junto com a IA" checked={coexistWithAi} onCheckedChange={setCoexistWithAi} /></div>
              <PermissionGate module="automations" action="manage" actionLabel="criar flows sociais" className="block w-full">
                <Button className="h-9 w-full bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={createRuleMutation.isPending} onClick={() => createRuleMutation.mutate()}><Plus className="mr-1.5 h-3.5 w-3.5" />Criar e ativar</Button>
              </PermissionGate>
              <Link to="/app/flows" className="flex h-9 items-center justify-center rounded-md border border-border text-2xs font-semibold hover:border-primary"><Workflow className="mr-1.5 h-3.5 w-3.5" />Abrir editor visual em tempo real</Link>
            </div>
          </KlmPanel>
          <KlmPanel className="overflow-hidden"><div className="border-b p-4"><div className="flex items-center justify-between"><div><h2 className="text-base font-medium">Flows sociais</h2><p className="mt-1 text-2xs text-muted-foreground">Ative e pause sem remover a configuração.</p></div><KlmPill tone="blue">{rulesQuery.data?.length ?? 0}</KlmPill></div></div><div className="divide-y divide-border">{rulesQuery.isPending && <p className="p-6 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando flows</p>}{(rulesQuery.data ?? []).filter((rule) => ['keyword_comment', 'post_comment', 'page_comment'].includes(rule.triggerType)).map((rule) => <div key={rule.id} className="flex items-start gap-3 p-4"><div className={cn('mt-0.5 h-2.5 w-2.5 rounded-full', rule.status === 'active' ? 'bg-[#1a9948]' : 'bg-amber-500')} /><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><strong className="truncate text-2xs">{rule.name}</strong><KlmPill tone={rule.coexistWithAi ? 'blue' : 'gold'}>{rule.coexistWithAi ? 'com IA' : 'flow assume'}</KlmPill></div><p className="mt-1 text-2xs text-muted-foreground">{rule.channel ? channelLabels[rule.channel as Exclude<SocialChannel, 'all'>] ?? rule.channel : 'Todos os canais'} · {rule.actions.length} ações · v{rule.version}</p></div><Button variant="outline" size="icon" className="h-8 w-8" disabled={!canManageFlows || ruleStateMutation.isPending} onClick={() => ruleStateMutation.mutate({ id: rule.id, version: rule.version, active: rule.status !== 'active' })} title={rule.status === 'active' ? 'Pausar' : 'Ativar'}>{rule.status === 'active' ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}</Button></div>)}{rulesQuery.isSuccess && !(rulesQuery.data ?? []).some((rule) => ['keyword_comment', 'post_comment', 'page_comment'].includes(rule.triggerType)) && <p className="p-8 text-center text-2xs text-muted-foreground">Nenhum flow social cadastrado.</p>}</div></KlmPanel>
        </div>
      )}

      {view === 'connections' && (
        <div className="grid gap-3 lg:grid-cols-[360px_minmax(0,1fr)]">
              <KlmPanel className="p-4"><h2 className="text-base font-medium">Cadastrar conexão local</h2><p className="mt-1 text-2xs leading-4 text-muted-foreground">Prepare a conta agora; OAuth, tokens e webhooks entram somente na etapa aprovada.</p><div className="mt-4 space-y-3"><Input value={connectionDraft.displayName} onChange={(event) => setConnectionDraft((current) => ({ ...current, displayName: event.target.value }))} className="h-9 text-2xs" placeholder="Nome da conta ou página" /><Input value={connectionDraft.externalAccountId} onChange={(event) => setConnectionDraft((current) => ({ ...current, externalAccountId: event.target.value }))} className="h-9 text-2xs" placeholder="ID externo, se já souber" /><PermissionGate module="integrations" action="manage" actionLabel="cadastrar conexões sociais" className="block w-full"><Button className="h-9 w-full bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={createConnectionMutation.isPending} onClick={() => createConnectionMutation.mutate()}><Plus className="mr-1.5 h-3.5 w-3.5" />Cadastrar {channel === 'all' ? 'Instagram' : channelLabels[channel]}</Button></PermissionGate><Button variant="outline" className="h-9 w-full text-2xs" onClick={() => navigate('/app/configuracoes/avancadas?tab=integracoes')}><Settings2 className="mr-1.5 h-3.5 w-3.5" />Configurar conta oficial</Button><Button variant="ghost" className="h-9 w-full text-2xs" onClick={() => navigate(`/app/integracoes?tab=assistente&provider=${connectionProvider(channel === 'all' ? 'instagram' : channel)}`)}><ShieldCheck className="mr-1.5 h-3.5 w-3.5" />Assinatura e webhooks</Button></div></KlmPanel>
          <KlmPanel className="overflow-hidden"><div className="border-b p-4"><div className="flex items-center justify-between"><div><h2 className="text-base font-medium">Contas sociais</h2><p className="mt-1 text-2xs text-muted-foreground">Estado, transporte e cobertura real por conexão.</p></div><KlmPill tone="muted">{socialConnections.length}</KlmPill></div></div><div className="divide-y divide-border">{connectionsQuery.isPending && <p className="p-8 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando conexões</p>}{socialConnections.map((connection) => <div key={connection.id} className="p-4"><div className="flex items-start gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary">{connection.channel === 'instagram' ? <Instagram className="h-4 w-4" /> : connection.channel === 'tiktok' ? <MessageCircleMore className="h-4 w-4" /> : <Facebook className="h-4 w-4" />}</div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><strong className="text-2xs">{connection.displayName}</strong>{connection.isDefault && <KlmPill tone="gold">padrão</KlmPill>}<KlmPill tone={connection.status === 'connected' ? 'green' : 'muted'}>{connection.status.replaceAll('_', ' ')}</KlmPill></div><p className="mt-1 text-2xs text-muted-foreground">{connection.channel} · {connection.transportMode.replaceAll('_', ' ')} · v{connection.version}</p><div className="mt-2 flex flex-wrap gap-1">{Object.entries(connection.capabilities).filter(([key]) => ['public_reply', 'private_reply', 'comment_like', 'comment_hide', 'comment_delete', 'lead_forms'].includes(key)).map(([key, value]) => <KlmPill key={key} tone={value.supported === false ? 'muted' : 'green'}>{key.replaceAll('_', ' ')}</KlmPill>)}</div></div><Button variant="outline" size="sm" className="h-8 shrink-0 text-2xs" disabled={!canManageIntegrations || validateConnectionMutation.isPending} onClick={() => validateConnectionMutation.mutate(connection.id)}><CheckCircle2 className="mr-1.5 h-3 w-3" />Validar</Button></div></div>)}{connectionsQuery.isSuccess && !socialConnections.length && <p className="p-8 text-center text-2xs text-muted-foreground">Nenhuma conta social cadastrada.</p>}</div></KlmPanel>
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-2 border-t border-border pt-3 text-2xs text-muted-foreground"><Clock3 className="h-3 w-3" />Eventos e ações são append-only e isolados por workspace.{eventsQuery.data?.hasMore && <KlmPill tone="gold">há mais eventos</KlmPill>}<span className="ml-auto">Última leitura: {eventsQuery.data ? new Date(eventsQuery.data.generatedAt).toLocaleString('pt-BR') : 'aguardando'}</span></footer>
      <OperationalCopilot
        domain="social_comments"
        title="Copiloto Social"
        subtitle="Fila, respostas e regras"
        welcome="Peça para priorizar a fila, preparar uma resposta ou montar uma regra. Eu preencho os editores; você continua responsável por enviar ou ativar."
        placeholder="Ex.: prepare uma DM para o comentário selecionado e abra a fila de leads quentes."
        contextKey={`social:${channel}:${view}`}
        contextSnapshot={copilotContext}
        workspaceId={state?.activeWorkspace?.id}
        userId={state?.currentUser?.id}
        canApply={can('mensagens', 'view')}
        onApply={applyCopilotActions}
      />
    </div>
  );
}
