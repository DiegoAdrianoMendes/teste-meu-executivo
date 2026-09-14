import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bot,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Loader2,
  MessageCircle,
  Pause,
  PhoneCall,
  Play,
  RefreshCw,
  Route,
  Send,
  ShieldCheck,
  Smartphone,
  UsersRound,
  Workflow,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmOmnichannelChannel,
  type KlmOmnichannelContext,
} from '@/klm/api';
import { fetchMessageTemplates } from '@/services/whatsapp';

const channelLabels: Record<KlmOmnichannelChannel, string> = {
  whatsapp: 'WhatsApp',
  instagram: 'Instagram',
  messenger: 'Messenger',
  facebook: 'Facebook',
  tiktok: 'TikTok',
  email: 'E-mail',
  form: 'Formulário',
  webchat: 'Chat do site',
  audio: 'Áudio',
  internal: 'Interno',
};

const stateLabels: Record<string, string> = {
  open: 'Aberta',
  human_active: 'Humano atendendo',
  waiting_human: 'Aguardando humano',
  waiting_documents: 'Aguardando documentos',
  closed: 'Concluída',
  blocked: 'Bloqueada',
  scheduled: 'Agendada',
  paused: 'Pausada',
  cancelled: 'Cancelada',
  active: 'Ativa',
  draft: 'Rascunho',
  prepared_local: 'Preparada localmente',
};

function localDateTime(hoursAhead = 1) {
  const date = new Date(Date.now() + hoursAhead * 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function transportLabel(mode: string | null) {
  if (mode === 'coexistence') return 'Cloud API + App';
  if (mode === 'cloud_api') return 'Cloud API';
  if (mode === 'official_api') return 'API oficial';
  if (mode === 'oauth') return 'OAuth';
  if (mode === 'webhook') return 'Webhook';
  return mode?.replaceAll('_', ' ') || 'Canal local';
}

function channelTone(channel: KlmOmnichannelChannel): 'green' | 'gold' | 'blue' | 'muted' {
  if (channel === 'whatsapp') return 'green';
  if (['instagram', 'messenger', 'facebook'].includes(channel)) return 'blue';
  if (channel === 'tiktok') return 'gold';
  return 'muted';
}

export function NativeInboxRoutingPanel({
  threadId,
  canEdit,
  canManageFlows = false,
  onUseResponse,
}: {
  threadId?: string | null;
  canEdit: boolean;
  canManageFlows?: boolean;
  onUseResponse: (content: string) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedTeamId, setSelectedTeamId] = useState('none');
  const [selectedUserId, setSelectedUserId] = useState('none');
  const [queue, setQueue] = useState('atendimento');
  const [selectedRouteKey, setSelectedRouteKey] = useState('auto');
  const [scheduleAt, setScheduleAt] = useState(() => localDateTime());
  const [scheduleBody, setScheduleBody] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const contextQuery = useQuery({
    queryKey: ['klm', 'omnichannel-context', threadId],
    queryFn: () => klmApi.omnichannelContext(threadId!),
    enabled: Boolean(threadId),
    retry: false,
  });
  const teamsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-teams'],
    queryFn: klmApi.omnichannelTeams,
    enabled: Boolean(threadId),
    retry: false,
  });
  const membersQuery = useQuery({
    queryKey: ['klm', 'omnichannel-team-members', selectedTeamId],
    queryFn: () => klmApi.omnichannelTeamMembers(selectedTeamId),
    enabled: selectedTeamId !== 'none',
    retry: false,
  });
  const templatesQuery = useQuery({
    queryKey: ['klm', 'message-templates'],
    queryFn: fetchMessageTemplates,
    enabled: Boolean(threadId),
    retry: false,
  });
  const rulesQuery = useQuery({
    queryKey: ['klm', 'omnichannel-automation-rules'],
    queryFn: klmApi.omnichannelAutomationRules,
    enabled: Boolean(threadId && canManageFlows),
    retry: false,
  });

  const context = contextQuery.data;
  useEffect(() => {
    if (!context) return;
    setSelectedTeamId(context.thread.assignedTeamId ?? 'none');
    setSelectedUserId(context.thread.assignedHumanId ?? 'none');
    setQueue(context.thread.queue ?? 'atendimento');
  }, [context]);

  const routableChannels = useMemo(
    () => (context?.channels ?? []).filter((item) => (
      item.blockedAt === null
      && item.connectionKey
      && !['error', 'disconnected', 'revoked'].includes(item.connectionStatus ?? '')
    )),
    [context?.channels],
  );
  const routeKey = (item: KlmOmnichannelContext['channels'][number]) => `${item.endpointId}:${item.bindingId ?? 'none'}`;
  const defaultEndpoint = routableChannels.find((item) => item.channel === context?.thread.primaryChannel)
    ?? routableChannels[0];
  const preferredEndpoint = routableChannels.find((item) => routeKey(item) === selectedRouteKey)
    ?? defaultEndpoint;

  useEffect(() => {
    if (!defaultEndpoint) {
      setSelectedRouteKey('auto');
      return;
    }
    setSelectedRouteKey((current) => (
      routableChannels.some((item) => routeKey(item) === current)
        ? current
        : routeKey(defaultEndpoint)
    ));
  }, [context?.thread.id, defaultEndpoint, routableChannels]);

  const invalidateContext = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-context', threadId] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
  };

  const transferMutation = useMutation({
    mutationFn: () => {
      if (!threadId || !context) throw new Error('Selecione um atendimento válido.');
      if (selectedTeamId === 'none' && selectedUserId === 'none' && !queue.trim()) {
        throw new Error('Escolha uma equipe, pessoa ou fila.');
      }
      return klmApi.transferOmnichannelThread(threadId, {
        expectedVersion: context.thread.version ?? 1,
        toTeamId: selectedTeamId === 'none' ? null : selectedTeamId,
        toUserId: selectedUserId === 'none' ? null : selectedUserId,
        queue: queue.trim() || null,
        reason: 'Transferência manual pela Central de atendimento',
      });
    },
    onSuccess: (result) => {
      setNotice(result.deduplicated ? 'Transferência já registrada.' : 'Atendimento transferido e auditado.');
      invalidateContext();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao transferir.'),
  });

  const statusMutation = useMutation({
    mutationFn: (status: 'open' | 'closed') => {
      if (!threadId || !context) throw new Error('Selecione um atendimento válido.');
      return klmApi.updateThread(threadId, {
        status,
        expectedVersion: context.thread.version ?? 1,
        closedReason: status === 'closed' ? 'Concluído manualmente na Central de atendimento' : null,
      });
    },
    onSuccess: () => {
      setNotice('Status atualizado com controle de versão.');
      invalidateContext();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao atualizar status.'),
  });

  const scheduleMutation = useMutation({
    mutationFn: () => {
      if (!threadId || !preferredEndpoint) throw new Error('Este lead ainda não possui canal endereçável.');
      if (!scheduleBody.trim()) throw new Error('Escreva a mensagem que será agendada.');
      return klmApi.scheduleOmnichannelMessage(threadId, {
        endpointId: preferredEndpoint.endpointId,
        connectionKey: preferredEndpoint.connectionKey,
        channel: preferredEndpoint.channel,
        body: scheduleBody.trim(),
        scheduledAt: new Date(scheduleAt).toISOString(),
        timezone: 'America/Sao_Paulo',
      });
    },
    onSuccess: () => {
      setScheduleBody('');
      setScheduleAt(localDateTime());
      setNotice('Mensagem agendada localmente. A entrega externa continua bloqueada até sua aprovação.');
      invalidateContext();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao agendar.'),
  });

  const scheduleStateMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'cancel' }) =>
      klmApi.updateOmnichannelScheduledMessage(id, {
        action,
        reason: action === 'cancel' ? 'Cancelada manualmente na Central de atendimento' : undefined,
      }),
    onSuccess: () => {
      setNotice('Agendamento atualizado.');
      invalidateContext();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao atualizar o agendamento.'),
  });

  const callMutation = useMutation({
    mutationFn: () => {
      if (!threadId || !preferredEndpoint) throw new Error('Este lead ainda não possui canal compatível.');
      return klmApi.createOmnichannelCall(threadId, {
        endpointId: preferredEndpoint.endpointId,
        connectionKey: preferredEndpoint.connectionKey,
        channel: preferredEndpoint.channel,
        direction: 'outbound',
        permissionState: 'requested',
        metadata: { source: 'conversation_one_click', externalExecution: false },
      });
    },
    onSuccess: () => {
      setNotice('Solicitação de chamada registrada. Nenhuma ligação externa foi iniciada.');
      invalidateContext();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao preparar chamada.'),
  });

  const ruleMutation = useMutation({
    mutationFn: ({ id, version, action }: { id: string; version: number; action: 'pause' | 'resume' }) =>
      klmApi.updateOmnichannelAutomationRule(id, {
        expectedVersion: version,
        status: action === 'pause' ? 'paused' : 'active',
      }),
    onSuccess: () => {
      setNotice('Flow atualizado. A execução continua rastreável na linha do tempo.');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-automation-rules'] });
      invalidateContext();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Falha ao atualizar o flow.'),
  });

  if (!threadId) {
    return <p className="p-4 text-2xs text-muted-foreground">Selecione uma conversa para carregar a operação.</p>;
  }
  if (contextQuery.isPending) {
    return <div className="flex items-center p-4 text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Montando Atendimento 360...</div>;
  }
  if (contextQuery.isError || !context) {
    return <div className="p-4 text-2xs"><p className="text-red-700">O Atendimento 360 não respondeu.</p><Button variant="outline" size="sm" className="mt-3 h-7 text-2xs" onClick={() => void contextQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button></div>;
  }

  return (
    <div className="divide-y divide-border">
      <section className="space-y-3 p-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-2xs font-semibold">Atendimento unificado</p>
            <p className="mt-0.5 text-2xs text-muted-foreground">{context.channels.length} canal(is) · 1 histórico · versão {context.thread.version ?? 1}</p>
          </div>
          <KlmPill tone={context.externalGateOpen ? 'green' : 'gold'}>{context.externalGateOpen ? 'entrega ativa' : 'somente local'}</KlmPill>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {context.channels.map((item) => (
            <div key={`${item.channel}:${item.connectionKey}:${item.externalUserId}`} className="inline-flex min-w-0 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 py-1.5" title={`${item.connectionName ?? item.connectionKey} · ${transportLabel(item.transportMode)}`}>
              <MessageCircle className="h-3 w-3 shrink-0" />
              <span className="text-2xs font-semibold">{channelLabels[item.channel]}</span>
              <KlmPill tone={channelTone(item.channel)} className="max-w-[110px] truncate">{transportLabel(item.transportMode)}</KlmPill>
            </div>
          ))}
          {!context.channels.length && <p className="text-2xs text-muted-foreground">Nenhum endereço de canal vinculado.</p>}
        </div>
        {routableChannels.length > 0 && (
          <Select value={preferredEndpoint ? routeKey(preferredEndpoint) : 'auto'} onValueChange={setSelectedRouteKey} disabled={!canEdit}>
            <SelectTrigger className="h-8 text-2xs" aria-label="Rota oficial deste atendimento">
              <Route className="mr-2 h-3 w-3" />
              <SelectValue placeholder="Escolha a rota" />
            </SelectTrigger>
            <SelectContent>
              {routableChannels.map((item) => (
                <SelectItem key={routeKey(item)} value={routeKey(item)} className="text-2xs">
                  {channelLabels[item.channel]} · {transportLabel(item.transportMode)} · {item.connectionName ?? item.connectionKey}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="h-8 flex-1 text-2xs" disabled={!canEdit || context.thread.status === 'open' || statusMutation.isPending} onClick={() => statusMutation.mutate('open')}><Play className="mr-1.5 h-3 w-3" />Reabrir</Button>
          <Button variant="outline" size="sm" className="h-8 flex-1 text-2xs" disabled={!canEdit || context.thread.status === 'closed' || statusMutation.isPending} onClick={() => statusMutation.mutate('closed')}><CheckCircle2 className="mr-1.5 h-3 w-3" />Concluir</Button>
        </div>
      </section>

      <section className="space-y-2.5 p-3">
        <div className="flex items-center justify-between"><p className="flex items-center gap-1.5 text-2xs font-semibold uppercase text-[#0057d8]"><Route className="h-3 w-3" />Transferir</p><span className="text-2xs text-muted-foreground">{stateLabels[context.thread.status] ?? context.thread.status}</span></div>
        <Select value={selectedTeamId} onValueChange={(value) => { setSelectedTeamId(value); setSelectedUserId('none'); }} disabled={!canEdit || teamsQuery.isPending}>
          <SelectTrigger className="h-8 text-2xs" aria-label="Equipe de destino"><UsersRound className="mr-2 h-3 w-3" /><SelectValue placeholder="Equipe" /></SelectTrigger>
          <SelectContent><SelectItem value="none" className="text-2xs">Sem equipe</SelectItem>{(teamsQuery.data ?? []).map((team) => <SelectItem key={team.id} value={team.id} className="text-2xs">{team.name} · {team.openThreadCount} em atendimento</SelectItem>)}</SelectContent>
        </Select>
        <Select value={selectedUserId} onValueChange={setSelectedUserId} disabled={!canEdit || selectedTeamId === 'none' || membersQuery.isPending}>
          <SelectTrigger className="h-8 text-2xs" aria-label="Pessoa responsável"><SelectValue placeholder="Pessoa responsável" /></SelectTrigger>
          <SelectContent><SelectItem value="none" className="text-2xs">Distribuição da equipe</SelectItem>{(membersQuery.data ?? []).map((member) => <SelectItem key={member.id} value={member.userId} className="text-2xs">{member.userName}{member.isAvailable ? ' · disponível' : ' · indisponível'}</SelectItem>)}</SelectContent>
        </Select>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <Select value={queue} onValueChange={setQueue} disabled={!canEdit}>
            <SelectTrigger className="h-8 text-2xs" aria-label="Fila de destino"><SelectValue /></SelectTrigger>
            <SelectContent>{['atendimento', 'sdr', 'juridico', 'closer', 'secretaria', 'documentos', 'suporte'].map((value) => <SelectItem key={value} value={value} className="text-2xs">{value.replaceAll('_', ' ')}</SelectItem>)}</SelectContent>
          </Select>
          <Button size="sm" className="h-8 bg-[#031121] px-3 text-2xs text-white hover:bg-[#031121]" disabled={!canEdit || transferMutation.isPending} onClick={() => transferMutation.mutate()}>{transferMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Transferir'}</Button>
        </div>
      </section>

      <section className="space-y-2.5 p-3">
        <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase text-[#0057d8]"><MessageCircle className="h-3 w-3" />Respostas prontas</p>
        <div className="space-y-1.5">
          {(templatesQuery.data ?? []).filter((item) => item.kind === 'message').slice(0, 5).map((item) => (
            <button key={item.id} type="button" disabled={!canEdit} onClick={() => onUseResponse(item.body)} className="w-full rounded-md border border-border p-2 text-left text-2xs leading-4 hover:border-primary disabled:opacity-50"><strong>{item.label}</strong><span className="mt-0.5 block line-clamp-2 text-muted-foreground">{item.body}</span></button>
          ))}
          {templatesQuery.isPending && <p className="text-2xs text-muted-foreground">Carregando biblioteca...</p>}
          {templatesQuery.isError && <p className="text-2xs text-red-700">A biblioteca de respostas não respondeu.</p>}
          {templatesQuery.isSuccess && !(templatesQuery.data ?? []).some((item) => item.kind === 'message') && <p className="text-2xs text-muted-foreground">Nenhuma resposta pronta cadastrada.</p>}
        </div>
      </section>

      <section className="space-y-2.5 p-3">
        <div className="flex items-center justify-between"><p className="flex items-center gap-1.5 text-2xs font-semibold uppercase text-[#0057d8]"><CalendarClock className="h-3 w-3" />Mensagens agendadas</p><KlmPill tone="muted">{context.scheduledMessages.length}</KlmPill></div>
        {context.scheduledMessages.map((item) => (
          <div key={item.id} className="flex items-start gap-2 border-l-2 border-primary pl-2">
            <div className="min-w-0 flex-1"><p className="truncate text-2xs font-semibold">{item.body || item.templateId || 'Mensagem com anexo'}</p><p className="mt-0.5 text-2xs text-muted-foreground">{new Date(item.scheduledAt).toLocaleString('pt-BR')} · {stateLabels[item.state] ?? item.state}</p></div>
            {item.state === 'scheduled' && <Button variant="ghost" size="icon" className="h-6 w-6" title="Pausar" onClick={() => scheduleStateMutation.mutate({ id: item.id, action: 'pause' })}><Pause className="h-3 w-3" /></Button>}
            {item.state === 'paused' && <Button variant="ghost" size="icon" className="h-6 w-6" title="Retomar" onClick={() => scheduleStateMutation.mutate({ id: item.id, action: 'resume' })}><Play className="h-3 w-3" /></Button>}
            {!['cancelled', 'delivered'].includes(item.state) && <Button variant="ghost" size="icon" className="h-6 w-6" title="Cancelar" onClick={() => scheduleStateMutation.mutate({ id: item.id, action: 'cancel' })}><X className="h-3 w-3" /></Button>}
          </div>
        ))}
        <Input type="datetime-local" value={scheduleAt} min={localDateTime(0)} onChange={(event) => setScheduleAt(event.target.value)} className="h-8 text-2xs" disabled={!canEdit} aria-label="Data da mensagem agendada" />
        <Textarea value={scheduleBody} onChange={(event) => setScheduleBody(event.target.value)} className="min-h-[58px] resize-none text-2xs" placeholder="Mensagem para enviar depois" disabled={!canEdit} />
        <Button variant="outline" size="sm" className="h-8 w-full text-2xs" disabled={!canEdit || !preferredEndpoint || scheduleMutation.isPending} onClick={() => scheduleMutation.mutate()}><Clock3 className="mr-1.5 h-3 w-3" />Agendar via {preferredEndpoint ? `${channelLabels[preferredEndpoint.channel]} · ${transportLabel(preferredEndpoint.transportMode)}` : 'canal'}</Button>
      </section>

      <section className="space-y-2.5 p-3">
        <div className="flex items-center justify-between"><p className="flex items-center gap-1.5 text-2xs font-semibold uppercase text-[#0057d8]"><PhoneCall className="h-3 w-3" />Chamadas</p><KlmPill tone="muted">{context.calls.length}</KlmPill></div>
        {context.calls.slice(0, 3).map((call) => <div key={call.id} className="flex items-center justify-between gap-2 text-2xs"><span>{channelLabels[call.channel]} · {call.direction === 'outbound' ? 'saída' : 'entrada'}</span><span className="text-muted-foreground">{stateLabels[call.state] ?? call.state} · {call.permissionState}</span></div>)}
        <Button variant="outline" size="sm" className="h-8 w-full text-2xs" disabled={!canEdit || !preferredEndpoint || callMutation.isPending} onClick={() => callMutation.mutate()}><PhoneCall className="mr-1.5 h-3 w-3" />Preparar chamada · {preferredEndpoint ? transportLabel(preferredEndpoint.transportMode) : 'rota oficial'}</Button>
        <p className="text-2xs leading-3 text-muted-foreground">A permissão e a intenção ficam registradas agora; a conexão externa será habilitada somente após aprovação.</p>
      </section>

      <section className="space-y-2.5 p-3">
        <div className="flex items-center justify-between"><p className="flex items-center gap-1.5 text-2xs font-semibold uppercase text-[#0057d8]"><Workflow className="h-3 w-3" />Flows + IA</p><KlmPill tone="blue">simultâneos</KlmPill></div>
        {context.automationRuns.slice(0, 4).map((run) => <div key={run.id} className="flex items-center justify-between gap-2 text-2xs"><span className="truncate font-semibold">{run.ruleName}</span><span className="shrink-0 text-muted-foreground">{stateLabels[run.state] ?? run.state} · {run.preparedActions} ações</span></div>)}
        {canManageFlows && (rulesQuery.data ?? []).filter((rule) => rule.status !== 'draft').slice(0, 5).map((rule) => (
          <div key={rule.id} className="flex items-center gap-2 border-t border-border/70 pt-2">
            <Bot className="h-3 w-3 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1"><p className="truncate text-2xs font-semibold">{rule.name}</p><p className="text-2xs text-muted-foreground">{rule.coexistWithAi ? 'roda com a IA' : 'assume o atendimento'} · {rule.triggerType.replaceAll('_', ' ')}</p></div>
            <Button variant="ghost" size="icon" className="h-7 w-7" title={rule.status === 'active' ? 'Pausar flow' : 'Ativar flow'} disabled={ruleMutation.isPending} onClick={() => ruleMutation.mutate({ id: rule.id, version: rule.version, action: rule.status === 'active' ? 'pause' : 'resume' })}>{rule.status === 'active' ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}</Button>
          </div>
        ))}
        {!context.automationRuns.length && !rulesQuery.data?.length && <p className="text-2xs text-muted-foreground">Nenhum flow ativo neste atendimento.</p>}
      </section>

      <section className="space-y-2 p-3">
        <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase text-[#0057d8]"><ShieldCheck className="h-3 w-3" />Auditoria de ações</p>
        {context.actionIntents.slice(0, 5).map((intent) => <div key={intent.id} className="flex items-center justify-between gap-2 text-2xs"><span className="truncate">{intent.actionType.replaceAll('_', ' ')}</span><KlmPill tone={intent.state === 'blocked_external' ? 'gold' : 'muted'}>{stateLabels[intent.state] ?? intent.state}</KlmPill></div>)}
        {!context.actionIntents.length && <p className="text-2xs text-muted-foreground">Nenhuma ação de canal preparada.</p>}
      </section>

      <section className="p-3">
        <div className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/10 p-2 text-2xs leading-4">
          {context.externalGateOpen ? <Send className="mt-0.5 h-3 w-3 shrink-0" /> : <Smartphone className="mt-0.5 h-3 w-3 shrink-0" />}
          <span>{context.externalGateOpen ? 'A entrega externa está habilitada para este workspace.' : 'Modo local protegido: comandos são persistidos e auditados, mas nenhuma API externa é chamada.'}</span>
        </div>
        {notice && <p aria-live="polite" className="mt-2 rounded-md border border-primary/30 bg-primary/10 p-2 text-2xs leading-4">{notice}</p>}
      </section>
    </div>
  );
}
