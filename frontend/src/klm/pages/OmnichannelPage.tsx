import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  AudioLines,
  Bot,
  CalendarDays,
  ChevronRight,
  CircleCheck,
  Clock3,
  FileCheck2,
  FileSearch,
  FileText,
  Headphones,
  Instagram,
  Mail,
  MessageCircle,
  MessagesSquare,
  MessageSquareText,
  MoreHorizontal,
  Paperclip,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  UserCheck,
  UserRoundCog,
  Workflow,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { NativeInboxRoutingPanel } from '@/klm/components/NativeInboxRoutingPanel';
import { cn } from '@/lib/utils';
import { klmApi, sha256File, type KlmThread } from '@/klm/api';
import { formatBRL } from '@/lib/formatters';
import { useAppStore } from '@/store/app-context';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';

type Channel = 'WhatsApp' | 'E-mail' | 'Formulário' | 'Instagram' | 'Facebook' | 'Messenger' | 'TikTok' | 'Chat do site' | 'Áudio' | 'Interno';
type Contact = {
  id: string;
  name: string;
  initials: string;
  subject: string;
  object: string;
  channels: Channel[];
  owner: string;
  unread: number;
  score: number;
  last: string;
  time: string;
};

type ChatMessage = { id: string; author: string; role: 'client' | 'agent' | 'human' | 'system'; time: string; text: string; channel: Channel };

const channelIcons: Record<Channel, typeof MessageCircle> = {
  WhatsApp: MessageCircle,
  'E-mail': Mail,
  Formulário: FileText,
  Instagram,
  Facebook: MessagesSquare,
  Messenger: MessageCircle,
  TikTok: MessagesSquare,
  'Chat do site': MessageSquareText,
  Áudio: AudioLines,
  Interno: Workflow,
};
const channelLabels: Record<KlmThread['primaryChannel'], Channel> = {
  whatsapp: 'WhatsApp',
  email: 'E-mail',
  form: 'Formulário',
  instagram: 'Instagram',
  facebook: 'Facebook',
  messenger: 'Messenger',
  tiktok: 'TikTok',
  webchat: 'Chat do site',
  audio: 'Áudio',
  internal: 'Interno',
};

function useCompactOmnichannel() {
  const [isCompact, setIsCompact] = useState(false);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 1279px)');
    const update = () => setIsCompact(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  return isCompact;
}

export default function OmnichannelPage() {
  const { can, state } = useAppStore();
  const canSend = can('mensagens', 'send_message');
  const canManageIntegrations = can('integrations', 'manage');
  const isCompact = useCompactOmnichannel();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const threadsQuery = useQuery({ queryKey: ['klm', 'threads'], queryFn: klmApi.threads });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const [selectedId, setSelectedId] = useState<string | null>(() => searchParams.get('thread'));
  const [channel, setChannel] = useState<'Todos' | Channel>('Todos');
  const [query, setQuery] = useState('');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [humanMode, setHumanMode] = useState(true);
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const requestedThreadId = searchParams.get('thread');
  const requestedMatterId = searchParams.get('matter');
  const contacts = useMemo(() => {
    if (!threadsQuery.data?.length) return [];
    const casesByMatter = new Map((funnelQuery.data?.cards ?? []).map((card) => [card.id, card]));
    const statusRank: Record<string, number> = { waiting_human: 0, human_active: 1, waiting_documents: 2, open: 3, blocked: 8, closed: 9 };
    return [...threadsQuery.data].sort((left, right) => (statusRank[left.status] ?? 5) - (statusRank[right.status] ?? 5) || right.score - left.score).map((thread) => {
      const matter = thread.matterId ? casesByMatter.get(thread.matterId) : undefined;
      const name = matter?.name ?? String(thread.threadMetadata.clientName ?? 'Cliente sem identificação');
      return {
        id: thread.id,
        name,
        initials: name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'CL',
        subject: matter?.subject.replaceAll('_', ' ') ?? thread.subject?.replaceAll('_', ' ') ?? 'Triagem em andamento',
        object: matter?.object ?? thread.objectName ?? 'Objeto a confirmar',
        channels: [channelLabels[thread.primaryChannel]],
        owner: thread.ownerAgentKey?.replaceAll('_', ' ') ?? 'responsável não atribuído',
        unread: thread.unreadCount,
        score: thread.score,
        last: thread.lastMessagePreview ?? 'Atendimento criado.',
        time: thread.lastMessageAt ? new Date(thread.lastMessageAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '--:--',
      } satisfies Contact;
    });
  }, [funnelQuery.data?.cards, threadsQuery.data]);
  const selected = contacts.find((contact) => contact.id === selectedId);
  const selectedThread = threadsQuery.data?.find((thread) => thread.id === selectedId);
  const selectedCase = selectedThread?.matterId ? funnelQuery.data?.cards.find((card) => card.id === selectedThread.matterId) : undefined;
  const contextualPath = (path: string, extras: Record<string, string> = {}) => {
    const params = new URLSearchParams();
    const matterId = selectedThread?.matterId ?? requestedMatterId;
    const threadId = selectedThread?.id ?? requestedThreadId;
    if (matterId) params.set('matter', matterId);
    if (threadId) params.set('thread', threadId);
    Object.entries(extras).forEach(([key, value]) => params.set(key, value));
    const contextQuery = params.toString();
    return `${path}${contextQuery ? `?${contextQuery}` : ''}`;
  };
  const matterQuery = useQuery({
    queryKey: ['klm', 'matter', selectedThread?.matterId],
    queryFn: () => klmApi.matter(selectedThread!.matterId!),
    enabled: Boolean(selectedThread?.matterId),
  });
  const messagesQuery = useQuery({
    queryKey: ['klm', 'messages', selectedThread?.id],
    queryFn: () => klmApi.messages(selectedThread!.id),
    enabled: Boolean(selectedThread),
  });
  const messages = useMemo<ChatMessage[]>(() => (messagesQuery.data ?? []).map((message) => ({
    id: message.id,
    author: message.authorLabel,
    role: message.direction === 'internal' || message.authorType === 'system' ? 'system' : message.authorType === 'client' ? 'client' : message.authorType === 'human' ? 'human' : 'agent',
    time: new Date(message.occurredAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
    channel: channelLabels[message.channel],
    text: message.body,
  })), [messagesQuery.data]);
  const internalLog = useMemo(() => (messagesQuery.data ?? [])
    .filter((message) => message.direction === 'internal')
    .map((message) => [message.id, message.authorLabel, message.body, new Date(message.occurredAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })] as const), [messagesQuery.data]);
  const caseRows = [
    ['Origem', matterQuery.data?.leadSource ?? selectedCase?.source ?? 'não informada'],
    ['Campanha', matterQuery.data?.campaignAttribution?.campaign ?? 'não informada'],
    ['Subtema', matterQuery.data?.healthSubtheme ?? selected?.subject ?? 'não informado'],
    ['Objeto', matterQuery.data?.healthObjectName ?? selected?.object ?? 'não informado'],
    ['Valor econômico', matterQuery.data?.economicValueAnnual != null ? `${formatBRL(matterQuery.data.economicValueAnnual)}/ano` : 'não estimado'],
    ['Prazo de resposta', selectedThread?.slaDueAt ? new Date(selectedThread.slaDueAt).toLocaleString('pt-BR') : 'sem prazo configurado'],
    ['Etapa', matterQuery.data?.columnId?.replaceAll('_', ' ') ?? selectedCase?.stage.replaceAll('_', ' ') ?? 'sem card vinculado'],
  ];
  const showList = !isCompact || !selectedId;
  const showChat = !isCompact || Boolean(selectedId);
  const personalizedFields = useMemo<PersonalizedFilterField<Contact>[]>(() => [
    { key: 'name', label: 'Lead', type: 'text' },
    { key: 'subject', label: 'Demanda ou subtema', type: 'text' },
    { key: 'object', label: 'Objeto do caso', type: 'text' },
    { key: 'channels', label: 'Canal', type: 'text' },
    { key: 'owner', label: 'Responsável', type: 'select', options: [...new Set(contacts.map((contact) => contact.owner))].map((value) => ({ value, label: value })) },
    { key: 'unread', label: 'Mensagens não lidas', type: 'number' },
    { key: 'score', label: 'Score', type: 'number' },
    { key: 'last', label: 'Última mensagem', type: 'text' },
  ], [contacts]);
  const filteredContacts = useMemo(() => contacts.filter((contact) => {
    const matchesChannel = channel === 'Todos' || contact.channels.includes(channel);
    const matchesQuery = `${contact.name} ${contact.subject} ${contact.object} ${contact.owner}`.toLowerCase().includes(query.toLowerCase());
    return matchesChannel && matchesQuery && matchesPersonalizedFilter(contact, personalizedFilter, personalizedFields);
  }), [channel, contacts, personalizedFields, personalizedFilter, query]);
  const oldestSla = useMemo(() => {
    const values = (threadsQuery.data ?? [])
      .filter((thread) => !['closed', 'blocked'].includes(thread.status) && thread.slaDueAt)
      .map((thread) => new Date(thread.slaDueAt!).getTime())
      .filter(Number.isFinite);
    if (!values.length) return 'sem prazo pendente';
    return `próximo prazo ${new Date(Math.min(...values)).toLocaleString('pt-BR', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}`;
  }, [threadsQuery.data]);

  useEffect(() => {
    if (threadsQuery.isPending || funnelQuery.isPending) return;

    setSelectedId((current) => {
      if (isCompact && current === null) return null;
      if (current && contacts.some((contact) => contact.id === current)) {
        const currentThread = threadsQuery.data?.find((thread) => thread.id === current);
        const isInitialRequestedThread = current === requestedThreadId;
        if (!isInitialRequestedThread || !requestedMatterId || currentThread?.matterId === requestedMatterId) return current;
        return null;
      }
      if (requestedThreadId) {
        const requestedThread = threadsQuery.data?.find((thread) => thread.id === requestedThreadId);
        if (requestedThread && (!requestedMatterId || requestedThread.matterId === requestedMatterId)) return requestedThread.id;
        return null;
      }
      if (requestedMatterId) {
        return threadsQuery.data?.find((thread) => thread.matterId === requestedMatterId)?.id ?? null;
      }
      return isCompact ? null : contacts[0]?.id ?? null;
    });
  }, [contacts, funnelQuery.isPending, isCompact, requestedMatterId, requestedThreadId, threadsQuery.data, threadsQuery.isPending]);

  useEffect(() => {
    if (!selectedThread?.id) return;
    const selectedMatterId = selectedThread.matterId ?? null;
    if (searchParams.get('thread') === selectedThread.id && searchParams.get('matter') === selectedMatterId) return;
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('thread', selectedThread.id);
    if (selectedMatterId) nextParams.set('matter', selectedMatterId);
    else nextParams.delete('matter');
    setSearchParams(nextParams, { replace: true });
  }, [searchParams, selectedThread?.id, selectedThread?.matterId, setSearchParams]);

  useEffect(() => {
    setHumanMode(selectedThread?.status === 'human_active');
  }, [selectedThread?.id, selectedThread?.status]);

  const sendMutation = useMutation({
    mutationFn: ({ threadId, body }: { threadId: string; body: string }) => klmApi.sendMessage(
      threadId,
      body,
      selectedThread?.primaryChannel ?? 'whatsapp',
      state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace',
    ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', selectedThread?.id] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      setNotice('Resposta salva como rascunho; publicação externa bloqueada.');
    },
    onError: () => setNotice('Falha ao salvar. Nenhum canal externo foi acionado.'),
  });

  const humanMutation = useMutation({
    mutationFn: ({ threadId, active }: { threadId: string; active: boolean }) => klmApi.updateThread(threadId, { status: active ? 'human_active' : 'open' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] }),
    onError: () => setNotice('O modo visual mudou, mas o sistema não confirmou a alteração.'),
  });

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedThread) throw new Error('Conversa local indisponível');
      const localDraftIds = (messagesQuery.data ?? [])
        .filter((message) => message.direction === 'outbound' && message.deliveryStatus === 'local_draft')
        .map((message) => message.id);
      const intent = await klmApi.createExternalIntent({
        matterId: selectedThread.matterId,
        actionType: 'omnichannel_publish_drafts',
        provider: selectedThread.primaryChannel,
        idempotencyKey: `omnichannel-publish:${selectedThread.id}:${localDraftIds.join(',') || 'empty'}`,
        payload: {
          threadId: selectedThread.id,
          channel: selectedThread.primaryChannel,
          messageIds: localDraftIds,
          externalActionsExecuted: 0,
        },
      });
      return klmApi.approveExternalIntentLocally(intent.id);
    },
    onSuccess: (intent) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(`Publicação aprovada somente no ambiente local (${intent.status}); o canal externo não foi acionado.`);
    },
    onError: () => setNotice('Não foi possível registrar a intenção de publicação local.'),
  });

  const attachmentMutation = useMutation({
    mutationFn: async (file: File) => {
      if (!selectedThread?.matterId) throw new Error('Conversa sem card vinculado');
      const checksumSha256 = await sha256File(file);
      const upload = await klmApi.createDocumentUpload(selectedThread.matterId, {
        name: file.name,
        checksumSha256,
        docType: 'outro',
        status: 'aguardando',
        fileSize: String(file.size),
        mimeType: file.type || 'application/octet-stream',
      });
      const document = await klmApi.completeDocumentUpload(selectedThread.matterId, upload, file);
      await klmApi.sendInternalMessage(
        selectedThread.id,
        `Arquivo “${document.name}” anexado ao card e disponível nas fontes do caso. Documento ${document.id}.`,
        state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace',
        { source: 'document_agent' },
      );
      return document;
    },
    onSuccess: (document) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', selectedThread?.matterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', selectedThread?.id] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      setNotice(`Arquivo “${document.name}” armazenado localmente, ligado ao card e registrado na conversa interna.`);
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível anexar o arquivo local.'),
  });

  const toggleHumanMode = () => {
    const next = !humanMode;
    setHumanMode(next);
    setNotice(next ? 'Atendimento assumido no ambiente local.' : 'Atendimento devolvido à IA no ambiente local.');
    if (selectedThread) humanMutation.mutate({ threadId: selectedThread.id, active: next });
  };

  const openTool = (label: string) => {
    if (label === 'Áudio') {
      navigate(contextualPath('/app/audio'));
      return;
    }
    if (label === 'Contrato') {
      if (selectedThread?.matterId) navigate(contextualPath('/app/contratos', { action: 'rascunho' }));
      else setNotice('Aguardando o vínculo local com o caso para preparar o contrato.');
      return;
    }
    if (label === 'Agenda') {
      if (selectedThread?.matterId) navigate(contextualPath('/app/agenda', { action: 'new' }));
      else setNotice('Aguardando o vínculo local com o caso para criar o agendamento.');
      return;
    }
    if (label === 'Docs' || label === 'Honorários') {
      if (selectedThread?.matterId) navigate(contextualPath('/app/leads', label === 'Docs' ? { panel: 'rag' } : {}));
      else setNotice('Aguardando o vínculo local com o caso para abrir esta ferramenta.');
    }
  };

  const sendLocalMessage = () => {
    if (!draft.trim()) return;
    const body = draft.trim();
    if (!selectedThread) {
      setNotice('Selecione uma conversa persistida antes de criar a resposta.');
      return;
    }
    setDraft('');
    sendMutation.mutate({ threadId: selectedThread.id, body });
  };

  return (
    <div className="mx-auto max-w-[1320px] space-y-4">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between"><div><p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Omnichannel</p><h1 className="mt-2 text-2xl font-semibold">Conversas unificadas</h1><p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">WhatsApp, e-mail, formulário, Instagram e áudio ligados ao mesmo lead e ao mesmo caso.</p></div><div className="flex flex-wrap gap-2"><Button variant="outline" className="h-9 text-xs" onClick={toggleHumanMode} disabled={!canSend || !selectedThread || humanMutation.isPending}>{humanMode ? <Bot className="mr-2 h-3.5 w-3.5" /> : <UserCheck className="mr-2 h-3.5 w-3.5" />}{humanMode ? 'Devolver à IA' : 'Assumir atendimento'}</Button><Button variant="outline" className="h-9 text-xs" onClick={() => navigate(contextualPath('/app/equipe'))}><MessagesSquare className="mr-2 h-3.5 w-3.5" />Equipe interna</Button><Button disabled={!canManageIntegrations || !selectedThread || publishMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Aprovar somente o registro local da intenção'} onClick={() => publishMutation.mutate()} className="h-9 text-xs"><LockExternalIcon />{publishMutation.isPending ? 'Registrando' : 'Aprovar publicação local'}</Button></div></header>

      <NativeModuleBar
        domain="communications"
        surface="Conversas unificadas"
        threadId={selectedThread?.id}
        matterId={selectedThread?.matterId}
        returnTo={contextualPath('/app/omnichannel')}
        capabilityIds={[
          'inboxes-routing',
          'contacts',
          'conversation-composer',
          'attachments-media',
          'private-notes',
          'labels-status-assignment',
          'canned-responses-macros',
        ]}
      />

      <KlmPanel className="overflow-hidden">
        <div className="grid sm:grid-cols-2 xl:grid-cols-4">
          {[
            { label: 'Registro nativo', value: threadsQuery.isSuccess ? 'saudável' : threadsQuery.isPending ? 'carregando' : 'indisponível', detail: 'caso, RAG, autoria e auditoria', tone: threadsQuery.isSuccess ? 'green' : 'gold' },
            { label: 'Atendimento nativo', value: threadsQuery.isSuccess ? 'operacional' : threadsQuery.isPending ? 'carregando' : 'indisponível', detail: 'fila, roteamento, respostas e prazos na plataforma', tone: threadsQuery.isSuccess ? 'green' : 'gold' },
            { label: 'Fonte da fila', value: threadsQuery.isSuccess ? 'conectado' : 'indisponível', detail: `${contacts.length} threads persistidas`, tone: threadsQuery.isSuccess ? 'green' : 'gold' },
            { label: 'Entrega externa', value: 'bloqueada', detail: '0 chamadas de canal nesta fase', tone: 'muted' },
          ].map((item) => <div key={item.label} className="border-b p-3 last:border-b-0 sm:border-r sm:[&:nth-child(2)]:border-r-0 xl:border-b-0 xl:[&:nth-child(2)]:border-r xl:last:border-r-0"><div className="flex items-center justify-between gap-2"><span className="text-2xs font-semibold uppercase text-muted-foreground">{item.label}</span><KlmPill tone={item.tone as 'green' | 'gold' | 'muted'}>{item.value}</KlmPill></div><p className="mt-2 text-2xs text-muted-foreground">{item.detail}</p></div>)}
        </div>
      </KlmPanel>

      {notice && <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <KlmPanel className="overflow-hidden">
        <div className="grid min-h-[720px] xl:grid-cols-[310px_minmax(440px,1fr)_320px]">
          {showList && <aside className={cn('border-r bg-card', isCompact && 'col-span-full border-r-0')}>
            <div className="border-b p-3"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">Fila unificada</h2><p className="mt-0.5 text-2xs text-muted-foreground">{oldestSla}</p></div><KlmPill tone="green">{contacts.length} abertas</KlmPill></div><div className="relative mt-3"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} className="h-9 bg-secondary pl-9 text-xs max-sm:text-base" placeholder="Nome, canal, medicamento" /></div><div className="mt-3 flex gap-1.5 overflow-x-auto">{(['Todos', 'WhatsApp', 'E-mail', 'Formulário', 'Instagram', 'Áudio'] as const).map((item) => <button key={item} onClick={() => setChannel(item)} className={cn('shrink-0 rounded-full border px-2.5 py-1 text-2xs font-semibold', channel === item ? 'border-[#031121] bg-[#031121] text-white' : 'border-border bg-card text-muted-foreground')}>{item === 'Formulário' ? 'Form' : item}</button>)}<PersonalizedFilterBuilder scope="omnichannel-inbox" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} compact /></div></div>
            <div className="divide-y">{filteredContacts.map((contact) => <button key={contact.id} onClick={() => setSelectedId(contact.id)} className={cn('grid w-full grid-cols-[40px_1fr_auto] gap-3 p-3 text-left transition hover:bg-secondary', selectedId === contact.id && !isCompact && 'bg-primary/10')}><div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#031121] text-2xs font-semibold text-primary">{contact.initials}</div><div className="min-w-0"><div className="flex items-center gap-2"><strong className="truncate text-xs">{contact.name}</strong>{contact.unread > 0 && <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-2xs font-bold text-[#031121]">{contact.unread}</span>}</div><p className="mt-0.5 truncate text-2xs text-muted-foreground">{contact.subject} · {contact.object}</p><p className="mt-1 truncate text-2xs text-foreground/70">{contact.last}</p><div className="mt-2 flex items-center gap-1.5">{contact.channels.map((item) => { const Icon = channelIcons[item]; return <span key={item} title={item} className="flex h-5 w-5 items-center justify-center rounded bg-secondary text-muted-foreground"><Icon className="h-3 w-3" /></span>; })}<KlmPill tone="muted">{contact.owner}</KlmPill></div></div><span className="text-2xs text-muted-foreground">{contact.time}</span></button>)}{!threadsQuery.isPending && !filteredContacts.length && <div className="p-6 text-center text-2xs text-muted-foreground">Nenhuma conversa persistida corresponde aos filtros.</div>}</div>
          </aside>}

          {showChat && <section aria-label="Conversa omnichannel" className="flex min-h-[720px] min-w-0 flex-col bg-[#f7f5f1] dark:bg-[#111214]">
            <div className="flex items-center justify-between border-b bg-card p-3"> <div className="flex min-w-0 items-center gap-3">{isCompact && <Button size="icon" variant="ghost" className="h-11 w-11 shrink-0" onClick={() => setSelectedId(null)} aria-label="Voltar à fila" title="Voltar à fila"><ArrowLeft className="h-4 w-4" /></Button>}<div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#031121] text-2xs font-semibold text-primary">{selected?.initials ?? '—'}</div><div className="min-w-0"><h2 className="truncate text-sm font-semibold">{selected?.name ?? 'Nenhuma conversa selecionada'}</h2><p className="truncate text-2xs text-muted-foreground">{selected ? `${selected.subject} · ${selected.object}` : 'Selecione uma thread persistida na fila'}</p></div></div><div className="flex items-center gap-2"><KlmPill tone={humanMode ? 'black' : 'green'}>{humanMode ? 'Humano' : 'IA'}</KlmPill><DropdownMenu><DropdownMenuTrigger asChild><Button size="icon" variant="ghost" className={cn('h-8 w-8', isCompact && 'h-11 w-11')} aria-label="Mais ações" title="Mais ações"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end" className="w-52"><DropdownMenuLabel>Atendimento atual</DropdownMenuLabel><DropdownMenuSeparator /><DropdownMenuItem disabled={!selectedThread?.matterId} onSelect={() => selectedThread?.matterId && navigate(contextualPath('/app/leads'))}>Abrir Cliente 360</DropdownMenuItem><DropdownMenuItem disabled={!selectedThread?.matterId} onSelect={() => selectedThread?.matterId && navigate(contextualPath('/app/flows'))}>Fluxos do caso</DropdownMenuItem><DropdownMenuItem disabled={!selectedThread?.matterId} onSelect={() => selectedThread?.matterId && navigate(contextualPath('/app/atividade'))}>Auditoria do caso</DropdownMenuItem></DropdownMenuContent></DropdownMenu></div></div>
            <div className="flex-1 space-y-3 overflow-y-auto p-3 sm:p-5">{messagesQuery.isPending && <div className="text-center text-2xs text-muted-foreground">Carregando histórico persistido...</div>}{messages.map((message) => { const isClient = message.role === 'client'; const isSystem = message.role === 'system'; const ChannelIcon = channelIcons[message.channel]; if (isSystem) return <div key={message.id} className="mx-auto flex max-w-[88%] items-start gap-2 rounded-lg border border-primary/25 bg-primary/10 p-3 text-2xs leading-4"><Mail className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8]" /><span><strong className="block text-2xs">{message.author} · {message.time}</strong>{message.text}</span></div>; return <div key={message.id} className={cn('flex', isClient ? 'justify-start' : 'justify-end')}><div className={cn('max-w-[86%] rounded-lg border p-3 shadow-sm sm:max-w-[72%]', isClient ? 'border-border bg-card' : message.role === 'human' ? 'border-[#031121] bg-[#031121] text-white' : 'border-emerald-200 bg-[#dcf8c6] text-[#102116] dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-50')}><div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-2xs"><strong>{message.author}</strong><span className={cn(message.role === 'human' ? 'text-white/50' : 'text-muted-foreground')}>{message.time}</span><span className="flex items-center gap-1"><ChannelIcon className="h-2.5 w-2.5" />{message.channel}</span></div><p className="whitespace-pre-wrap text-xs leading-5">{message.text}</p></div></div>; })}{!messagesQuery.isPending && !messages.length && <div className="rounded-lg border border-dashed p-6 text-center text-2xs text-muted-foreground">Nenhuma mensagem persistida nesta conversa.</div>}</div>
            <div className="border-t bg-card p-3"><div className="mb-2 flex gap-1.5 overflow-x-auto">{[{ label: 'Docs', icon: FileSearch }, { label: 'Honorários', icon: Sparkles }, { label: 'Contrato', icon: FileCheck2 }, { label: 'Agenda', icon: CalendarDays }, { label: 'Áudio', icon: AudioLines }].map((tool) => <Button key={tool.label} variant="outline" size="sm" className={cn('h-7 shrink-0 px-2 text-2xs', isCompact && 'h-11')} onClick={() => openTool(tool.label)}><tool.icon className="mr-1.5 h-3 w-3" />{tool.label}</Button>)}</div><input ref={attachmentInputRef} type="file" className="sr-only" aria-label="Selecionar anexo para o card" accept=".pdf,image/*,.doc,.docx,.txt" onChange={(event) => { const file = event.target.files?.[0]; if (file) attachmentMutation.mutate(file); event.currentTarget.value = ''; }} /><div className="flex gap-2"><Textarea aria-label="Mensagem da conversa" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={!canSend || !selectedThread} className="min-h-[44px] resize-none text-xs max-sm:text-base" placeholder={!canSend ? 'Seu perfil possui acesso somente de leitura.' : humanMode ? 'Responder como humano em rascunho local...' : 'Criar resposta assistida em rascunho local...'} /><div className="flex flex-col gap-1"><Button size="icon" variant="outline" className={cn('h-8 w-9', isCompact && 'h-11 w-11')} aria-label="Anexar arquivo ao card" title="Anexar arquivo ao card" disabled={!canSend || !selectedThread?.matterId || attachmentMutation.isPending} onClick={() => attachmentInputRef.current?.click()}><Paperclip className="h-3.5 w-3.5" /></Button><Button size="icon" className={cn('h-8 w-9 bg-[#031121] text-white hover:bg-[#031121]', isCompact && 'h-11 w-11')} aria-label="Registrar resposta local" title="Registrar resposta local" onClick={sendLocalMessage} disabled={!canSend || !selectedThread || !draft.trim() || sendMutation.isPending}><Send className="h-3.5 w-3.5" /></Button></div></div><p className="mt-2 text-2xs text-muted-foreground">Mensagem e anexos ficam ligados ao card; a entrega ao canal externo exige aprovação separada.</p></div>
          </section>}

          {!isCompact && <aside className="border-l bg-card">
            <Tabs defaultValue="caso" className="h-full">
              <div className="border-b p-3"><TabsList className="grid h-8 w-full grid-cols-4 bg-secondary p-1"><TabsTrigger value="caso" className="text-2xs">Caso</TabsTrigger><TabsTrigger value="interno" className="text-2xs">Interno</TabsTrigger><TabsTrigger value="fontes" className="text-2xs">Fontes</TabsTrigger><TabsTrigger value="operacao" className="text-2xs">Operação</TabsTrigger></TabsList></div>
              <TabsContent value="caso" className="m-0 space-y-4 p-4">
                <div className="flex items-center justify-between"><div><h3 className="text-sm font-semibold">Dados do caso</h3><p className="mt-1 text-2xs text-muted-foreground">Cliente 360</p></div><KlmPill tone="green">score {selected?.score ?? 0}</KlmPill></div>
                {caseRows.map(([label, value]) => <div key={label} className="border-b pb-2"><span className="block text-2xs text-muted-foreground">{label}</span><strong className="mt-1 block text-2xs">{value}</strong></div>)}
                <div><div className="mb-2 flex justify-between text-2xs"><span>Qualificação</span><strong>{selected?.score ?? 0}%</strong></div><KlmProgress value={selected?.score ?? 0} tone="green" /></div>
              </TabsContent>
              <TabsContent value="interno" className="m-0 space-y-1 p-3">
                {internalLog.map(([id, author, text, time]) => <div key={id} className="rounded-lg p-2.5 hover:bg-secondary"><div className="flex items-center justify-between"><strong className="text-2xs">{author}</strong><span className="text-2xs text-muted-foreground">{time}</span></div><p className="mt-1 whitespace-pre-wrap text-2xs leading-4 text-muted-foreground">{text}</p></div>)}
                {!internalLog.length && <p className="p-3 text-center text-2xs text-muted-foreground">Nenhuma nota interna nesta conversa.</p>}
              </TabsContent>
              <TabsContent value="fontes" className="m-0 space-y-2 p-3">
                {(matterQuery.data?.documents ?? []).map((document) => <button key={document.id} type="button" onClick={() => selectedThread?.matterId ? navigate(contextualPath('/app/leads', { panel: 'rag' })) : setNotice('Esta fonte ainda não está vinculada a um card local.')} aria-label={`Abrir ${document.name} no RAG do caso`} className="flex w-full items-center gap-3 rounded-lg border p-3 text-left hover:border-primary"><FileText className="h-4 w-4 shrink-0 text-[#0057d8]" /><span className="min-w-0 flex-1"><strong className="block truncate text-2xs">{document.name}</strong><span className="mt-1 block text-2xs text-muted-foreground">{document.type ?? 'documento'} · {new Date(document.createdAt).toLocaleDateString('pt-BR')}</span></span><KlmPill tone={document.status === 'completo' ? 'green' : 'gold'}>{document.status}</KlmPill></button>)}
                {!matterQuery.isPending && !(matterQuery.data?.documents.length) && <p className="p-3 text-center text-2xs text-muted-foreground">Nenhum documento vinculado ao card.</p>}
              </TabsContent>
              <TabsContent value="operacao" className="m-0"><NativeInboxRoutingPanel threadId={selectedThread?.id} canEdit={canSend} onUseResponse={(content) => setDraft(content)} /></TabsContent>
            </Tabs>
          </aside>}
        </div>
      </KlmPanel>
      {isCompact && selectedThread && (
        <KlmPanel className="overflow-hidden">
          <div className="border-b px-3 py-2"><p className="text-2xs font-semibold">Operação do atendimento</p><p className="mt-0.5 text-2xs text-muted-foreground">Roteamento e respostas prontas da conversa selecionada.</p></div>
          <NativeInboxRoutingPanel threadId={selectedThread.id} canEdit={canSend} onUseResponse={(content) => setDraft(content)} />
          <div className="border-t p-3" data-testid="omnichannel-mobile-details">
            <p className="text-2xs font-semibold">Detalhes do caso</p>
            <div className="mt-2 grid grid-cols-2 gap-2 text-2xs">
              {caseRows.slice(0, 4).map(([label, value]) => <div key={label} className="rounded-md border bg-secondary/40 p-2"><span className="block text-muted-foreground">{label}</span><strong className="mt-0.5 block truncate">{value}</strong></div>)}
            </div>
          </div>
        </KlmPanel>
      )}
    </div>
  );
}

function LockExternalIcon() {
  return <ShieldCheck className="mr-2 h-3.5 w-3.5" />;
}
