import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Inbox, Loader2, MessageSquarePlus, RefreshCw, Send } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { useToast } from '@/hooks/use-toast';
import { canPlatform } from '@/lib/platform-access';
import { apiFetch } from '@/services/http';
import { useAppStore } from '@/store/app-context';

type Channel = 'whatsapp' | 'instagram' | 'messenger' | 'email_sales' | 'email_support' | 'webchat';
type Queue = 'sales' | 'support';
type Status = 'open' | 'pending' | 'resolved';

interface InboxMessage {
  id: string;
  direction: 'inbound' | 'outbound' | 'internal';
  body: string;
  deliveryStatus: string;
  actorLabel: string;
  occurredAt: string;
}

interface InboxConversation {
  id: string;
  channel: Channel;
  contactName: string;
  contactAddress: string | null;
  subject: string | null;
  queue: Queue;
  status: Status;
  assignedToUserId: string | null;
  prospectId: string | null;
  supportTicketId: string | null;
  workspaceId: string | null;
  lastMessagePreview: string | null;
  lastMessageAt: string | null;
  firstResponseDueAt: string | null;
  firstRespondedAt: string | null;
  resolutionDueAt: string | null;
  resolvedAt: string | null;
  unreadCount: number;
  messages?: InboxMessage[];
}

const CHANNEL_LABEL: Record<Channel, string> = {
  whatsapp: 'WhatsApp', instagram: 'Instagram', messenger: 'Messenger',
  email_sales: 'E-mail comercial', email_support: 'E-mail suporte', webchat: 'Webchat',
};

const initialDraft = {
  channel: 'webchat' as Channel, contactName: '', contactAddress: '', subject: '',
  queue: 'sales' as Queue, initialMessage: '',
};

export default function BlackboxInbox() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { state: appState } = useAppStore();
  const canManageSales = canPlatform(appState.currentUser, 'sales', 'manage');
  const canRespondSupport = canPlatform(appState.currentUser, 'support', 'respond');
  const canWriteInbox = canManageSales || canRespondSupport;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [channel, setChannel] = useState<string>('all');
  const [queue, setQueue] = useState<string>('all');
  const [status, setStatus] = useState<string>('all');
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState(initialDraft);
  const [message, setMessage] = useState('');
  const [internal, setInternal] = useState(false);
  const [linkDraft, setLinkDraft] = useState({ prospectId: '', supportTicketId: '', workspaceId: '' });

  const params = useMemo(() => {
    const query = new URLSearchParams();
    if (search.trim()) query.set('search', search.trim());
    if (channel !== 'all') query.set('channel', channel);
    if (queue !== 'all') query.set('queue', queue);
    if (status !== 'all') query.set('status', status);
    query.set('limit', '50');
    if (cursor) query.set('cursor', cursor);
    return query.toString();
  }, [channel, cursor, queue, search, status]);

  const listQuery = useQuery({
    queryKey: ['platform-inbox', 'conversations', params],
    queryFn: () => apiFetch<{ items: InboxConversation[]; count: number; nextCursor: string | null; hasMore: boolean }>(`/api/v1/platform/inbox/conversations?${params}`),
    staleTime: 10_000,
  });
  useEffect(() => {
    setCursor(null);
    setCursorHistory([]);
  }, [channel, queue, search, status]);
  const detailQuery = useQuery({
    queryKey: ['platform-inbox', 'conversation', selectedId],
    queryFn: () => apiFetch<InboxConversation>(`/api/v1/platform/inbox/conversations/${selectedId}`),
    enabled: Boolean(selectedId),
  });
  const metricsQuery = useQuery({
    queryKey: ['platform-inbox', 'metrics'],
    queryFn: () => apiFetch<{ total: number; open: number; unread: number; firstResponseOverdue: number; resolutionOverdue: number }>('/api/v1/platform/inbox/metrics'),
  });
  const channelsQuery = useQuery({
    queryKey: ['platform-inbox', 'channels'],
    queryFn: () => apiFetch<Array<{ channel: Channel; state: string; inbound: boolean; outbound: boolean; detail: string }>>('/api/v1/platform/inbox/channels'),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['platform-inbox'] });
  };
  const createMutation = useMutation({
    mutationFn: () => apiFetch<InboxConversation>('/api/v1/platform/inbox/conversations', {
      method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ ...draft, contactAddress: draft.contactAddress || null, subject: draft.subject || null }),
    }),
    onSuccess: (created) => {
      setCreateOpen(false); setDraft(initialDraft); setSelectedId(created.id); refresh();
      toast({ title: 'Conversa registrada na inbox.' });
    },
    onError: (error: Error) => toast({ title: 'Falha ao registrar conversa', description: error.message, variant: 'destructive' }),
  });
  const patchMutation = useMutation({
    mutationFn: (changes: Partial<Pick<InboxConversation, 'status' | 'queue' | 'assignedToUserId' | 'prospectId' | 'supportTicketId' | 'workspaceId'>>) => apiFetch<InboxConversation>(`/api/v1/platform/inbox/conversations/${selectedId}`, { method: 'PATCH', body: JSON.stringify(changes) }),
    onSuccess: () => refresh(),
    onError: (error: Error) => toast({ title: 'Falha ao atualizar conversa', description: error.message, variant: 'destructive' }),
  });
  const markReadMutation = useMutation({
    mutationFn: (conversationId: string) => apiFetch<InboxConversation>(
      `/api/v1/platform/inbox/conversations/${conversationId}/read`,
      { method: 'POST' },
    ),
    onSuccess: (updated) => {
      queryClient.setQueryData<InboxConversation>(
        ['platform-inbox', 'conversation', updated.id],
        (current) => current ? { ...current, unreadCount: 0 } : current,
      );
      void queryClient.invalidateQueries({ queryKey: ['platform-inbox', 'conversations'] });
      void queryClient.invalidateQueries({ queryKey: ['platform-inbox', 'metrics'] });
    },
  });
  const messageMutation = useMutation({
    mutationFn: () => apiFetch<InboxMessage>(`/api/v1/platform/inbox/conversations/${selectedId}/messages`, {
      method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ body: message, visibility: internal ? 'internal' : 'public' }),
    }),
    onSuccess: (created) => {
      setMessage(''); refresh();
      toast({
        title: internal ? 'Nota interna registrada.' : 'Resposta registrada.',
        description: created.deliveryStatus === 'not_configured'
          ? 'O transporte externo deste canal não está configurado; a mensagem não foi entregue.'
          : undefined,
      });
    },
  });
  const createLeadMutation = useMutation({
    mutationFn: () => apiFetch<InboxConversation>(`/api/v1/platform/inbox/conversations/${selectedId}/create-lead`, {
      method: 'POST', body: JSON.stringify({}),
    }),
    onSuccess: () => { refresh(); toast({ title: 'Lead criado e vinculado ao CRM.' }); },
    onError: (error: Error) => toast({ title: 'Falha ao criar lead', description: error.message, variant: 'destructive' }),
  });
  const createTicketMutation = useMutation({
    mutationFn: () => apiFetch<InboxConversation>(`/api/v1/platform/inbox/conversations/${selectedId}/create-ticket`, {
      method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({}),
    }),
    onSuccess: () => { refresh(); toast({ title: 'Ticket criado e vinculado ao SAC.' }); },
    onError: (error: Error) => toast({ title: 'Falha ao criar ticket', description: error.message, variant: 'destructive' }),
  });

  const selected = detailQuery.data;
  useEffect(() => {
    if (!selected || selected.unreadCount === 0 || markReadMutation.isPending) return;
    markReadMutation.mutate(selected.id);
  }, [markReadMutation, selected]);
  useEffect(() => {
    if (!selected) return;
    setLinkDraft({
      prospectId: selected.prospectId ?? '',
      supportTicketId: selected.supportTicketId ?? '',
      workspaceId: selected.workspaceId ?? '',
    });
  }, [selected]);
  return (
    <main className="mx-auto max-w-[1500px] space-y-5 p-6">
      <header className="flex flex-col gap-3 border-b pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div><h1 className="text-2xl font-semibold">Inbox do SaaS</h1><p className="text-sm text-muted-foreground">Conversas dos canais oficiais da Meu Executivo, separadas das conversas privadas das contas.</p></div>
        <div className="flex gap-2"><Button variant="outline" size="icon" aria-label="Atualizar inbox" onClick={refresh}><RefreshCw className="h-4 w-4" /></Button>{canWriteInbox && <Button className="gap-2" onClick={() => setCreateOpen(true)}><MessageSquarePlus className="h-4 w-4" />Registrar conversa</Button>}</div>
      </header>

      <section aria-label="Estado dos canais" className="flex gap-2 overflow-x-auto pb-1">{channelsQuery.isLoading ? <span className="text-sm text-muted-foreground">Consultando canais…</span> : (channelsQuery.data ?? []).map((item) => <div key={item.channel} className="min-w-48 rounded-lg border bg-card p-3"><div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{CHANNEL_LABEL[item.channel]}</span><Badge variant={item.state === 'connected' || item.state === 'available' ? 'default' : 'secondary'}>{item.state === 'connected' ? 'Conectado' : item.state === 'available' ? 'Disponível' : item.state === 'credentials_configured' ? 'Credencial presente' : 'Não configurado'}</Badge></div><p className="mt-1 text-xs text-muted-foreground">Entrada {item.inbound ? 'ativa' : 'inativa'} · saída {item.outbound ? 'ativa' : 'inativa'}</p><p className="mt-1 text-xs text-muted-foreground">{item.detail}</p></div>)}</section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Card><CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Conversas</CardTitle></CardHeader><CardContent className="text-2xl font-bold">{metricsQuery.data?.total ?? '—'}</CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Em aberto</CardTitle></CardHeader><CardContent className="text-2xl font-bold">{metricsQuery.data?.open ?? '—'}</CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Não lidas</CardTitle></CardHeader><CardContent className="text-2xl font-bold">{metricsQuery.data?.unread ?? '—'}</CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">1ª resposta vencida</CardTitle></CardHeader><CardContent className="text-2xl font-bold">{metricsQuery.data?.firstResponseOverdue ?? '—'}</CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Resolução vencida</CardTitle></CardHeader><CardContent className="text-2xl font-bold">{metricsQuery.data?.resolutionOverdue ?? '—'}</CardContent></Card>
      </section>

      <div className="grid gap-3 md:grid-cols-4"><Input aria-label="Buscar conversas" placeholder="Buscar contato, endereço ou assunto" value={search} onChange={(event) => setSearch(event.target.value)} /><Select value={channel} onValueChange={setChannel}><SelectTrigger aria-label="Filtrar canal"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Todos os canais</SelectItem>{Object.entries(CHANNEL_LABEL).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select><Select value={queue} onValueChange={setQueue}><SelectTrigger aria-label="Filtrar fila"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Todas as filas</SelectItem><SelectItem value="sales">Comercial</SelectItem><SelectItem value="support">Suporte</SelectItem></SelectContent></Select><Select value={status} onValueChange={setStatus}><SelectTrigger aria-label="Filtrar status"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Todos os status</SelectItem><SelectItem value="open">Aberta</SelectItem><SelectItem value="pending">Pendente</SelectItem><SelectItem value="resolved">Resolvida</SelectItem></SelectContent></Select></div>

      {selected && <section className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 p-3"><span className="mr-auto text-sm text-muted-foreground">Transforme a conversa em trabalho operacional sem copiar IDs.</span>{canManageSales && <Button size="sm" variant="outline" disabled={Boolean(selected.prospectId) || createLeadMutation.isPending} onClick={() => createLeadMutation.mutate()}>{selected.prospectId ? 'Lead vinculado' : 'Criar lead no CRM'}</Button>}{canRespondSupport && <Button size="sm" variant="outline" disabled={Boolean(selected.supportTicketId) || !selected.workspaceId || createTicketMutation.isPending} onClick={() => createTicketMutation.mutate()}>{selected.supportTicketId ? 'Ticket vinculado' : selected.workspaceId ? 'Criar ticket no SAC' : 'Vincule a conta para criar ticket'}</Button>}</section>}

      <section className="grid min-h-[620px] gap-4 lg:grid-cols-[380px_minmax(0,1fr)]">
        <div className="overflow-hidden rounded-lg border bg-card">
          {listQuery.isLoading ? <div className="grid h-40 place-items-center"><Loader2 className="h-5 w-5 animate-spin" /></div> : listQuery.isError ? <div role="alert" className="grid h-40 place-items-center px-6 text-center text-sm text-destructive">Não foi possível carregar as conversas.</div> : (listQuery.data?.items ?? []).length === 0 ? <div className="grid h-40 place-items-center px-6 text-center text-sm text-muted-foreground">Nenhuma conversa corresponde aos filtros.</div> : (listQuery.data?.items ?? []).map((item) => <button key={item.id} type="button" onClick={() => setSelectedId(item.id)} className={`block w-full border-b p-4 text-left hover:bg-muted/50 ${selectedId === item.id ? 'bg-muted' : ''}`}><div className="flex items-center justify-between gap-2"><span className="font-medium">{item.contactName}</span>{item.unreadCount > 0 && <Badge>{item.unreadCount}</Badge>}</div><div className="mt-1 flex gap-2 text-xs text-muted-foreground"><span>{CHANNEL_LABEL[item.channel]}</span><span>·</span><span>{item.queue === 'sales' ? 'Comercial' : 'Suporte'}</span></div><p className="mt-2 truncate text-sm text-muted-foreground">{item.lastMessagePreview ?? 'Sem prévia'}</p></button>)}
          <div className="flex items-center justify-between gap-2 border-t p-3"><span className="text-xs text-muted-foreground">{listQuery.data ? `${listQuery.data.count} conversa(s) nesta página` : 'Página indisponível'}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={cursorHistory.length === 0 || listQuery.isFetching} onClick={() => { const history = [...cursorHistory]; const previous = history.pop() ?? ''; setCursorHistory(history); setCursor(previous || null); }}>Anterior</Button><Button size="sm" variant="outline" disabled={!listQuery.data?.hasMore || !listQuery.data.nextCursor || listQuery.isFetching} onClick={() => { if (!listQuery.data?.nextCursor) return; setCursorHistory((history) => [...history, cursor ?? '']); setCursor(listQuery.data.nextCursor); }}>Próxima</Button></div></div>
        </div>
        <div className="rounded-lg border bg-card p-5">
          {!selectedId ? <div className="grid h-full place-items-center text-center text-sm text-muted-foreground"><div><Inbox className="mx-auto mb-3 h-8 w-8" />Selecione uma conversa.</div></div> : detailQuery.isLoading ? <div className="grid h-full place-items-center"><Loader2 className="h-5 w-5 animate-spin" /></div> : selected ? <div className="flex h-full flex-col"><div className="flex flex-wrap items-start justify-between gap-3 border-b pb-4"><div><h2 className="font-semibold">{selected.contactName}</h2><p className="text-sm text-muted-foreground">{selected.contactAddress ?? 'Endereço não informado'} · {CHANNEL_LABEL[selected.channel]}</p></div><div className="flex flex-wrap gap-2">{canWriteInbox ? <PlatformEntityCombobox className="w-44" entityType="staff" purpose="inbox_assignee" label="Responsável da conversa" placeholder="Sem responsável" value={selected.assignedToUserId} onChange={(value) => patchMutation.mutate({ assignedToUserId: value })} allowClear /> : <Badge variant="outline">{selected.assignedToUserId ? 'Responsável atribuído' : 'Sem responsável'}</Badge>}<Select disabled={!canWriteInbox} value={selected.queue} onValueChange={(value: Queue) => patchMutation.mutate({ queue: value })}><SelectTrigger className="w-32" aria-label="Fila da conversa"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="sales">Comercial</SelectItem><SelectItem value="support">Suporte</SelectItem></SelectContent></Select><Select disabled={!canWriteInbox} value={selected.status} onValueChange={(value: Status) => patchMutation.mutate({ status: value })}><SelectTrigger className="w-32" aria-label="Status da conversa"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="open">Aberta</SelectItem><SelectItem value="pending">Pendente</SelectItem><SelectItem value="resolved">Resolvida</SelectItem></SelectContent></Select></div></div>{canWriteInbox && <div className="grid gap-3 border-b py-3 sm:grid-cols-3"><div><Label className="text-xs">Conta</Label><PlatformEntityCombobox className="mt-1" entityType="accounts" purpose="inbox_link_workspace" label="Selecionar conta" placeholder="Buscar conta" value={linkDraft.workspaceId || null} onChange={(value) => setLinkDraft((current) => ({ ...current, workspaceId: value ?? '', prospectId: '', supportTicketId: '' }))} /></div><div><Label className="text-xs">Lead</Label><PlatformEntityCombobox className="mt-1" entityType="leads" purpose="inbox_link_lead" label="Selecionar lead" placeholder="Buscar lead" workspaceId={linkDraft.workspaceId || null} value={linkDraft.prospectId || null} onChange={(value) => setLinkDraft((current) => ({ ...current, prospectId: value ?? '' }))} /></div><div><Label className="text-xs">Ticket</Label><div className="mt-1 flex gap-1"><PlatformEntityCombobox className="flex-1" entityType="tickets" purpose="inbox_link_ticket" label="Selecionar ticket" placeholder="Buscar ticket" workspaceId={linkDraft.workspaceId || null} value={linkDraft.supportTicketId || null} onChange={(value) => setLinkDraft((current) => ({ ...current, supportTicketId: value ?? '' }))} /><Button size="sm" variant="outline" disabled={patchMutation.isPending} onClick={() => patchMutation.mutate({ prospectId: linkDraft.prospectId || null, supportTicketId: linkDraft.supportTicketId || null, workspaceId: linkDraft.workspaceId || null })}>Vincular</Button></div></div></div>}<div className="flex-1 space-y-3 overflow-y-auto py-4">{(selected.messages ?? []).map((item) => <div key={item.id} className={`max-w-[82%] rounded-lg border p-3 text-sm ${item.direction === 'inbound' ? 'mr-auto bg-muted/50' : 'ml-auto'} ${item.direction === 'internal' ? 'border-amber-300 bg-amber-50' : ''}`}><div className="mb-1 flex items-center justify-between gap-3 text-[11px] text-muted-foreground"><span>{item.actorLabel}</span><span>{item.deliveryStatus === 'not_configured' ? 'Não entregue · canal não configurado' : item.deliveryStatus}</span></div><p className="whitespace-pre-wrap">{item.body}</p></div>)}</div>{canWriteInbox && <div className="border-t pt-4"><div className="mb-2 flex items-center gap-2"><Button size="sm" variant={!internal ? 'secondary' : 'ghost'} onClick={() => setInternal(false)}>Resposta pública</Button><Button size="sm" variant={internal ? 'secondary' : 'ghost'} onClick={() => setInternal(true)}>Nota interna</Button></div><div className="flex gap-2"><Textarea aria-label="Mensagem da conversa" rows={3} value={message} onChange={(event) => setMessage(event.target.value)} placeholder={internal ? 'Nota visível somente à equipe' : 'Escreva a resposta'} /><Button size="icon" aria-label="Enviar mensagem" disabled={!message.trim() || messageMutation.isPending} onClick={() => messageMutation.mutate()}><Send className="h-4 w-4" /></Button></div>{!internal && <p className="mt-2 text-xs text-amber-700">A entrega externa só ocorre quando o adaptador oficial do canal estiver configurado. Caso contrário, a mensagem fica registrada como não entregue.</p>}</div>}</div> : <p className="text-sm text-destructive">Não foi possível carregar a conversa.</p>}
        </div>
      </section>

      <Dialog open={createOpen && canWriteInbox} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Registrar conversa recebida</DialogTitle><DialogDescription>Use para entrada manual ou diagnóstico. Webhooks oficiais poderão usar o mesmo núcleo persistido.</DialogDescription></DialogHeader><div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label>Canal</Label><Select value={draft.channel} onValueChange={(value: Channel) => setDraft((current) => ({ ...current, channel: value }))}><SelectTrigger aria-label="Canal da conversa"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(CHANNEL_LABEL).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div><div className="space-y-2"><Label>Fila</Label><Select value={draft.queue} onValueChange={(value: Queue) => setDraft((current) => ({ ...current, queue: value }))}><SelectTrigger aria-label="Fila inicial"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="sales">Comercial</SelectItem><SelectItem value="support">Suporte</SelectItem></SelectContent></Select></div><div className="space-y-2"><Label htmlFor="inbox-contact">Contato</Label><Input id="inbox-contact" value={draft.contactName} onChange={(event) => setDraft((current) => ({ ...current, contactName: event.target.value }))} /></div><div className="space-y-2"><Label htmlFor="inbox-address">Telefone, e-mail ou @perfil</Label><Input id="inbox-address" value={draft.contactAddress} onChange={(event) => setDraft((current) => ({ ...current, contactAddress: event.target.value }))} /></div><div className="space-y-2 sm:col-span-2"><Label htmlFor="inbox-subject">Assunto</Label><Input id="inbox-subject" value={draft.subject} onChange={(event) => setDraft((current) => ({ ...current, subject: event.target.value }))} /></div><div className="space-y-2 sm:col-span-2"><Label htmlFor="inbox-initial">Mensagem recebida</Label><Textarea id="inbox-initial" rows={5} value={draft.initialMessage} onChange={(event) => setDraft((current) => ({ ...current, initialMessage: event.target.value }))} /></div></div><DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancelar</Button><Button disabled={!draft.contactName.trim() || !draft.initialMessage.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>{createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Registrar</Button></DialogFooter></DialogContent></Dialog>
    </main>
  );
}
