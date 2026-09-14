import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, CircleAlert, Clock3, Loader2, Pause, Play, Route, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { klmApi, type KlmOmnichannelChannel, type KlmOmnichannelScheduledMessage } from '@/klm/api';

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
  scheduled: 'Agendada',
  paused: 'Pausada',
  delivered: 'Entregue',
  cancelled: 'Cancelada',
  failed: 'Falhou',
  uncertain: 'Entrega incerta',
};

const supportedTimezones = [
  'America/Sao_Paulo',
  'America/Manaus',
  'America/Cuiaba',
  'America/Rio_Branco',
  'UTC',
] as const;

function localDateTime(hoursAhead = 1) {
  const date = new Date(Date.now() + hoursAhead * 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function routeKey(route: { endpointId: string; bindingId: string | null }) {
  return `${route.endpointId}:${route.bindingId ?? 'none'}`;
}

function transportLabel(mode: string | null) {
  if (mode === 'coexistence') return 'Cloud API + App';
  if (mode === 'cloud_api') return 'Cloud API';
  if (mode === 'official_api') return 'API oficial';
  if (mode === 'oauth') return 'OAuth';
  if (mode === 'webhook') return 'Webhook';
  return mode?.replaceAll('_', ' ') || 'Canal local';
}

function timezoneOffsetAt(timestamp: number, timezone: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(timestamp));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return Date.UTC(
    Number(values.year),
    Number(values.month) - 1,
    Number(values.day),
    Number(values.hour),
    Number(values.minute),
    Number(values.second),
  ) - timestamp;
}

function zonedLocalToIso(value: string, timezone: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new Error('Informe uma data e hora válidas.');
  const wallClockUtc = Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5]),
  );
  let instant = wallClockUtc - timezoneOffsetAt(wallClockUtc, timezone);
  instant = wallClockUtc - timezoneOffsetAt(instant, timezone);
  const result = new Date(instant);
  if (!Number.isFinite(result.getTime())) throw new Error('Não foi possível interpretar o horário escolhido.');
  return result.toISOString();
}

export function ConversationMessageSchedulingDialog({
  open,
  onOpenChange,
  threadId,
  initialBody,
  canEdit,
  onScheduled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  threadId: string | null | undefined;
  initialBody: string;
  canEdit: boolean;
  onScheduled: (message: KlmOmnichannelScheduledMessage, consumedBody: string) => void;
}) {
  const queryClient = useQueryClient();
  const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const defaultTimezone = supportedTimezones.includes(browserTimezone as typeof supportedTimezones[number])
    ? browserTimezone
    : 'America/Sao_Paulo';
  const [body, setBody] = useState(initialBody);
  const [scheduledAt, setScheduledAt] = useState(() => localDateTime());
  const [timezone, setTimezone] = useState(defaultTimezone);
  const [selectedRouteKey, setSelectedRouteKey] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const contextQuery = useQuery({
    queryKey: ['klm', 'omnichannel-context', threadId],
    queryFn: () => klmApi.omnichannelContext(threadId!),
    enabled: open && Boolean(threadId),
    retry: false,
  });
  const routes = useMemo(() => (contextQuery.data?.channels ?? []).filter((route) => (
    route.blockedAt === null
    && Boolean(route.connectionKey)
    && !['error', 'disconnected', 'revoked'].includes(route.connectionStatus ?? '')
  )), [contextQuery.data?.channels]);
  const defaultRoute = routes.find((route) => route.channel === contextQuery.data?.thread.primaryChannel) ?? routes[0];
  const selectedRoute = routes.find((route) => routeKey(route) === selectedRouteKey) ?? defaultRoute;

  useEffect(() => {
    if (!open) return;
    setBody(initialBody);
    setScheduledAt(localDateTime());
    setNotice(null);
  }, [initialBody, open, threadId]);

  useEffect(() => {
    if (!defaultRoute) return;
    setSelectedRouteKey((current) => routes.some((route) => routeKey(route) === current) ? current : routeKey(defaultRoute));
  }, [defaultRoute, routes]);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-context', threadId] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
  };

  const scheduleMutation = useMutation({
    mutationFn: () => {
      if (!threadId || !selectedRoute) throw new Error('Este atendimento ainda não possui uma rota oficial endereçável.');
      const cleanBody = body.trim();
      if (!cleanBody) throw new Error('Escreva a mensagem que será agendada.');
      const instant = zonedLocalToIso(scheduledAt, timezone);
      if (new Date(instant).getTime() <= Date.now()) throw new Error('Escolha um horário futuro.');
      return klmApi.scheduleOmnichannelMessage(threadId, {
        endpointId: selectedRoute.endpointId,
        connectionKey: selectedRoute.connectionKey,
        channel: selectedRoute.channel,
        body: cleanBody,
        scheduledAt: instant,
        timezone,
      });
    },
    onSuccess: (message) => {
      const consumedBody = body.trim();
      invalidate();
      onScheduled(message, consumedBody);
      setBody('');
      setScheduledAt(localDateTime());
      setNotice('Mensagem incluída na fila com chave única e trilha de auditoria.');
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível criar o agendamento.'),
  });

  const stateMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'cancel' }) => (
      klmApi.updateOmnichannelScheduledMessage(id, {
        action,
        reason: action === 'cancel' ? 'Cancelada manualmente na conversa' : undefined,
      })
    ),
    onSuccess: () => {
      invalidate();
      setNotice('Estado do agendamento atualizado e auditado.');
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível atualizar o agendamento.'),
  });

  const scheduledMessages = contextQuery.data?.scheduledMessages ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><CalendarClock className="h-4 w-4 text-[#0057d8]" />Agendar mensagem</DialogTitle>
          <DialogDescription>Escolha quando e por qual rota oficial a mensagem deve sair. O agendamento fica ligado a esta conversa única.</DialogDescription>
        </DialogHeader>

        {contextQuery.isPending ? <div className="flex min-h-40 items-center justify-center text-2xs text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando rotas e fila</div> : null}
        {contextQuery.isError ? <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-2xs text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100"><CircleAlert className="mb-2 h-4 w-4" />Não foi possível carregar as rotas persistidas desta conversa.</div> : null}

        {contextQuery.isSuccess ? (
          <div className="space-y-5">
            <section className="space-y-3 rounded-lg border border-border p-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="space-y-1.5 text-2xs font-semibold">Data e hora
                  <Input aria-label="Data e hora da mensagem agendada" type="datetime-local" min={localDateTime(0)} value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} disabled={!canEdit} />
                </label>
                <label className="space-y-1.5 text-2xs font-semibold">Fuso horário
                  <Select value={timezone} onValueChange={setTimezone} disabled={!canEdit}>
                    <SelectTrigger aria-label="Fuso horário da mensagem"><SelectValue /></SelectTrigger>
                    <SelectContent>{supportedTimezones.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent>
                  </Select>
                </label>
              </div>
              <label className="block space-y-1.5 text-2xs font-semibold">Rota de envio
                <Select value={selectedRoute ? routeKey(selectedRoute) : ''} onValueChange={setSelectedRouteKey} disabled={!canEdit || routes.length === 0}>
                  <SelectTrigger aria-label="Rota da mensagem agendada"><Route className="mr-2 h-3.5 w-3.5" /><SelectValue placeholder="Nenhuma rota disponível" /></SelectTrigger>
                  <SelectContent>{routes.map((route) => <SelectItem key={routeKey(route)} value={routeKey(route)}>{channelLabels[route.channel]} · {transportLabel(route.transportMode)} · {route.connectionName ?? route.connectionKey}</SelectItem>)}</SelectContent>
                </Select>
              </label>
              <label className="block space-y-1.5 text-2xs font-semibold">Mensagem
                <Textarea aria-label="Conteúdo da mensagem agendada" value={body} onChange={(event) => setBody(event.target.value)} className="min-h-28 resize-y text-xs" placeholder="Escreva a mensagem que será enviada depois" disabled={!canEdit} />
              </label>
              <div className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/10 p-2.5 text-2xs leading-4">
                <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{contextQuery.data.externalGateOpen ? 'A fila está apta à entrega pela conexão configurada.' : 'Modo local protegido: o agendamento será persistido e testável, mas a API externa continua bloqueada até aprovação.'}</span>
              </div>
              <Button className="w-full bg-[#031121] text-white hover:bg-[#031121]" disabled={!canEdit || !selectedRoute || scheduleMutation.isPending || !body.trim()} onClick={() => scheduleMutation.mutate()}>
                {scheduleMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CalendarClock className="mr-2 h-4 w-4" />}
                {scheduleMutation.isPending ? 'Agendando' : 'Confirmar agendamento'}
              </Button>
            </section>

            <section className="space-y-2">
              <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">Fila desta conversa</h3><KlmPill tone="muted">{scheduledMessages.length}</KlmPill></div>
              {scheduledMessages.map((item) => (
                <div key={item.id} className="flex items-start gap-2 rounded-lg border border-border p-3">
                  <CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8]" />
                  <div className="min-w-0 flex-1"><p className="truncate text-2xs font-semibold" title={item.body ?? undefined}>{item.body || item.templateId || 'Mensagem com anexo'}</p><p className="mt-1 text-2xs text-muted-foreground">{new Date(item.scheduledAt).toLocaleString('pt-BR')} · {item.timezone} · {stateLabels[item.state] ?? item.state}</p></div>
                  {item.state === 'scheduled' ? <Button aria-label="Pausar mensagem agendada" variant="ghost" size="icon" className="h-7 w-7" disabled={stateMutation.isPending} onClick={() => stateMutation.mutate({ id: item.id, action: 'pause' })}><Pause className="h-3.5 w-3.5" /></Button> : null}
                  {item.state === 'paused' ? <Button aria-label="Retomar mensagem agendada" variant="ghost" size="icon" className="h-7 w-7" disabled={stateMutation.isPending} onClick={() => stateMutation.mutate({ id: item.id, action: 'resume' })}><Play className="h-3.5 w-3.5" /></Button> : null}
                  {!['cancelled', 'delivered'].includes(item.state) ? <Button aria-label="Cancelar mensagem agendada" variant="ghost" size="icon" className="h-7 w-7 text-destructive" disabled={stateMutation.isPending} onClick={() => stateMutation.mutate({ id: item.id, action: 'cancel' })}><X className="h-3.5 w-3.5" /></Button> : null}
                </div>
              ))}
              {!scheduledMessages.length ? <p className="rounded-lg border border-dashed p-4 text-center text-2xs text-muted-foreground">Nenhuma mensagem agendada nesta conversa.</p> : null}
            </section>
          </div>
        ) : null}

        {notice ? <p aria-live="polite" className="rounded-md border border-primary/30 bg-primary/10 p-2.5 text-2xs leading-4">{notice}</p> : null}
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Fechar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
