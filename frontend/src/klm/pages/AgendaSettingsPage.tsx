import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Blocks, CalendarCheck2, CalendarClock, Check, CircleAlert, Clock3, Copy, ExternalLink, Link2, Loader2, Plus, RefreshCw, Save, Settings2, Trash2, Video } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { klmApi, type KlmCalendarProvider, type KlmCalendarSettings } from '@/klm/api';
import { cn } from '@/lib/utils';

const WEEKDAYS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];
const PROVIDERS: Array<{ value: KlmCalendarProvider; label: string; description: string; icon: typeof Video }> = [
  { value: 'google_meet', label: 'Google Meet', description: 'OAuth e sincronização quando a API for aprovada.', icon: CalendarCheck2 },
  { value: 'microsoft_teams', label: 'Microsoft Teams', description: 'Conta Microsoft e sala Teams quando conectada.', icon: Blocks },
  { value: 'manual', label: 'Link manual', description: 'Use uma sala fixa sem chamar API externa.', icon: Link2 },
];

function cloneSettings(settings: KlmCalendarSettings): KlmCalendarSettings {
  return JSON.parse(JSON.stringify(settings)) as KlmCalendarSettings;
}

export default function AgendaSettingsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ['klm', 'calendar-settings'], queryFn: klmApi.calendarSettings });
  const linksQuery = useQuery({ queryKey: ['klm', 'booking-links'], queryFn: klmApi.bookingLinks });
  const [draft, setDraft] = useState<KlmCalendarSettings | null>(null);

  useEffect(() => {
    if (settingsQuery.data) setDraft(cloneSettings(settingsQuery.data));
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error('Configuração indisponível');
      const { connectionStatus: _, ...payload } = draft;
      return klmApi.updateCalendarSettings(payload);
    },
    onSuccess: (saved) => {
      setDraft(cloneSettings(saved));
      void queryClient.invalidateQueries({ queryKey: ['klm', 'calendar-settings'] });
      toast({ title: 'Disponibilidade e página de agendamento salvas.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar a agenda', description: error.message }),
  });
  const revokeMutation = useMutation({
    mutationFn: klmApi.revokeBookingLink,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'booking-links'] });
      toast({ title: 'Link revogado.' });
    },
  });

  const updateDay = (weekday: number, updater: (day: KlmCalendarSettings['weeklyAvailability'][number]) => KlmCalendarSettings['weeklyAvailability'][number]) => {
    if (!draft) return;
    setDraft({ ...draft, weeklyAvailability: draft.weeklyAvailability.map((day) => day.weekday === weekday ? updater(day) : day) });
  };

  const agendaContext = new URLSearchParams();
  const matterId = searchParams.get('matter');
  const threadId = searchParams.get('thread');
  if (matterId) agendaContext.set('matter', matterId);
  if (threadId) agendaContext.set('thread', threadId);
  const agendaQuery = agendaContext.toString();
  const agendaPath = `/app/agenda${agendaQuery ? `?${agendaQuery}` : ''}`;

  if (settingsQuery.isError || (!settingsQuery.isPending && !draft)) {
    return (
      <KlmPanel className="mx-auto max-w-[720px] p-8 text-center">
        <CircleAlert className="mx-auto h-6 w-6 text-[#a53e3c]" />
        <h2 className="mt-4 text-lg font-semibold">Não foi possível carregar a configuração da agenda</h2>
        <p className="mt-2 text-xs text-muted-foreground">
          {settingsQuery.error instanceof Error ? settingsQuery.error.message : 'Não foi possível carregar uma configuração válida.'}
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Button variant="outline" onClick={() => navigate(agendaPath)}><ArrowLeft className="mr-2 h-4 w-4" />Voltar à Agenda</Button>
          <Button onClick={() => void settingsQuery.refetch()} disabled={settingsQuery.isFetching}>
            {settingsQuery.isFetching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            Tentar novamente
          </Button>
        </div>
      </KlmPanel>
    );
  }

  if (!draft) {
    return <div className="flex min-h-[60vh] items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando configuração da agenda...</div>;
  }

  const selectedProvider = PROVIDERS.find((item) => item.value === draft.provider) ?? PROVIDERS[2];
  const ProviderIcon = selectedProvider.icon;

  return (
    <div className="mx-auto w-full max-w-[1380px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Button variant="ghost" className="-ml-3 h-8 px-3 text-2xs" onClick={() => navigate(agendaPath)}><ArrowLeft className="mr-2 h-3.5 w-3.5" />Voltar à Agenda</Button>
          <p className="mt-2 text-2xs font-semibold uppercase text-primary">Agenda / Configuração</p>
          <h1 className="mt-1 text-2xl font-semibold">Disponibilidade e booking</h1>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">Defina quando o escritório recebe reuniões, como a sala é criada e o que o lead vê no link público.</p>
        </div>
        <Button className="h-9 bg-[#031121] text-white hover:bg-[#031121]" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Salvar configuração
        </Button>
      </header>

      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.65fr)]">
        <div className="min-w-0 space-y-5">
          <KlmPanel className="overflow-hidden">
            <div className="flex items-start justify-between gap-4 border-b border-border p-5">
              <div><p className="text-sm font-semibold">Conexão e sala on-line</p><p className="mt-1 text-2xs text-muted-foreground">A configuração é local; OAuth só será ativado depois da aprovação das APIs.</p></div>
              <KlmPill tone={draft.connectionStatus === 'connected' || draft.connectionStatus === 'manual_ready' ? 'green' : 'gold'}>{draft.connectionStatus === 'connected' ? 'Conectado' : draft.connectionStatus === 'manual_ready' ? 'Link pronto' : draft.connectionStatus === 'awaiting_api' ? 'Aguardando API' : 'Offline'}</KlmPill>
            </div>
            <div className="p-5">
              <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Provedor da agenda">
                {PROVIDERS.map((provider) => {
                  const Icon = provider.icon;
                  const active = draft.provider === provider.value;
                  return (
                    <button key={provider.value} type="button" role="radio" aria-checked={active} onClick={() => setDraft({ ...draft, provider: provider.value })} className={cn('min-h-[96px] border p-3 text-left transition', active ? 'border-primary bg-primary/10 dark:bg-primary/15' : 'border-border hover:border-primary/60')}>
                      <div className="flex items-center justify-between"><Icon className={cn('h-4 w-4', active ? 'text-[#0057d8]' : 'text-muted-foreground')} />{active && <Check className="h-3.5 w-3.5 text-[#0057d8]" />}</div>
                      <p className="mt-3 text-xs font-semibold">{provider.label}</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{provider.description}</p>
                    </button>
                  );
                })}
              </div>
              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                {draft.provider !== 'manual' && <><div className="space-y-1.5"><Label htmlFor="calendar-account">Conta da agenda</Label><Input id="calendar-account" type="email" value={draft.accountEmail ?? ''} onChange={(event) => setDraft({ ...draft, accountEmail: event.target.value || null })} placeholder="conta@klmadvogados.com" /></div><div className="space-y-1.5"><Label htmlFor="calendar-id">Calendário</Label><Input id="calendar-id" value={draft.calendarId ?? ''} onChange={(event) => setDraft({ ...draft, calendarId: event.target.value || null })} placeholder="Agenda comercial" /></div></>}
                <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="default-room">Link padrão da sala</Label><div className="relative"><ProviderIcon className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input id="default-room" className="pl-9" value={draft.defaultMeetingUrl ?? ''} onChange={(event) => setDraft({ ...draft, defaultMeetingUrl: event.target.value || null })} placeholder="https://meet.google.com/..." /></div><p className="text-2xs text-muted-foreground">Enquanto OAuth estiver desligado, informe uma sala fixa ou adicione o link em cada reunião.</p></div>
              </div>
            </div>
          </KlmPanel>

          <KlmPanel className="overflow-hidden">
            <div className="flex items-start justify-between gap-4 border-b border-border p-5"><div><p className="text-sm font-semibold">Disponibilidade semanal</p><p className="mt-1 text-2xs text-muted-foreground">Horários no fuso {draft.timezone}. Reservas existentes são removidas dos slots automaticamente.</p></div><Clock3 className="h-5 w-5 text-muted-foreground" /></div>
            <div className="divide-y divide-border">
              {draft.weeklyAvailability.slice().sort((left, right) => left.weekday - right.weekday).map((day) => (
                <div key={day.weekday} className="grid gap-3 p-4 sm:grid-cols-[128px_minmax(0,1fr)]">
                  <div className="flex items-center gap-3"><Switch checked={day.enabled} onCheckedChange={(enabled) => updateDay(day.weekday, (current) => ({ ...current, enabled: Boolean(enabled), windows: enabled && current.windows.length === 0 ? [{ start: '09:00', end: '18:00' }] : current.windows }))} aria-label={`Disponibilidade de ${WEEKDAYS[day.weekday]}`} /><span className="text-xs font-semibold">{WEEKDAYS[day.weekday]}</span></div>
                  {day.enabled ? (
                    <div className="space-y-2">
                      {day.windows.map((window, index) => (
                        <div key={`${day.weekday}-${index}`} className="flex items-center gap-2"><Input type="time" value={window.start} onChange={(event) => updateDay(day.weekday, (current) => ({ ...current, windows: current.windows.map((item, itemIndex) => itemIndex === index ? { ...item, start: event.target.value } : item) }))} aria-label={`Início ${WEEKDAYS[day.weekday]} ${index + 1}`} /><span className="text-2xs text-muted-foreground">até</span><Input type="time" value={window.end} onChange={(event) => updateDay(day.weekday, (current) => ({ ...current, windows: current.windows.map((item, itemIndex) => itemIndex === index ? { ...item, end: event.target.value } : item) }))} aria-label={`Fim ${WEEKDAYS[day.weekday]} ${index + 1}`} /><Button variant="ghost" size="icon" className="shrink-0 text-red-700" onClick={() => updateDay(day.weekday, (current) => ({ ...current, windows: current.windows.filter((_, itemIndex) => itemIndex !== index) }))} aria-label={`Remover faixa ${index + 1} de ${WEEKDAYS[day.weekday]}`}><Trash2 className="h-3.5 w-3.5" /></Button></div>
                      ))}
                      <Button variant="ghost" className="h-7 px-2 text-2xs" onClick={() => updateDay(day.weekday, (current) => ({ ...current, windows: [...current.windows, { start: '14:00', end: '18:00' }] }))}><Plus className="mr-1.5 h-3 w-3" />Adicionar faixa</Button>
                    </div>
                  ) : <p className="self-center text-2xs text-muted-foreground">Sem atendimento neste dia.</p>}
                </div>
              ))}
            </div>
          </KlmPanel>

          <KlmPanel className="overflow-hidden">
            <div className="border-b border-border p-5"><p className="text-sm font-semibold">Regras dos horários</p><p className="mt-1 text-2xs text-muted-foreground">Controle duração, antecedência e espaço entre compromissos.</p></div>
            <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-1.5"><Label>Duração padrão</Label><Select value={String(draft.defaultDurationMinutes)} onValueChange={(value) => setDraft({ ...draft, defaultDurationMinutes: Number(value) })}><SelectTrigger aria-label="Duração padrão"><SelectValue /></SelectTrigger><SelectContent>{[30, 45, 60, 90].map((value) => <SelectItem key={value} value={String(value)}>{value} minutos</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-1.5"><Label>Intervalo dos slots</Label><Select value={String(draft.slotIntervalMinutes)} onValueChange={(value) => setDraft({ ...draft, slotIntervalMinutes: Number(value) })}><SelectTrigger aria-label="Intervalo dos slots"><SelectValue /></SelectTrigger><SelectContent>{[15, 30, 45, 60].map((value) => <SelectItem key={value} value={String(value)}>{value} minutos</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-1.5"><Label htmlFor="notice-hours">Antecedência mínima</Label><Input id="notice-hours" type="number" min={0} max={720} value={draft.minimumNoticeHours} onChange={(event) => setDraft({ ...draft, minimumNoticeHours: Number(event.target.value) })} /><p className="text-2xs text-muted-foreground">Em horas</p></div>
              <div className="space-y-1.5"><Label htmlFor="horizon-days">Janela futura</Label><Input id="horizon-days" type="number" min={1} max={180} value={draft.bookingHorizonDays} onChange={(event) => setDraft({ ...draft, bookingHorizonDays: Number(event.target.value) })} /><p className="text-2xs text-muted-foreground">Em dias</p></div>
              <div className="space-y-1.5"><Label htmlFor="buffer-before">Respiro antes</Label><Input id="buffer-before" type="number" min={0} max={120} value={draft.bufferBeforeMinutes} onChange={(event) => setDraft({ ...draft, bufferBeforeMinutes: Number(event.target.value) })} /><p className="text-2xs text-muted-foreground">Em minutos</p></div>
              <div className="space-y-1.5"><Label htmlFor="buffer-after">Respiro depois</Label><Input id="buffer-after" type="number" min={0} max={120} value={draft.bufferAfterMinutes} onChange={(event) => setDraft({ ...draft, bufferAfterMinutes: Number(event.target.value) })} /><p className="text-2xs text-muted-foreground">Em minutos</p></div>
            </div>
          </KlmPanel>
        </div>

        <aside className="min-w-0 space-y-5 xl:sticky xl:top-5">
          <KlmPanel className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-border p-5"><div><p className="text-sm font-semibold">Página enviada ao lead</p><p className="mt-1 text-2xs text-muted-foreground">Texto e contato do booking público.</p></div><Switch checked={draft.bookingPage.enabled} onCheckedChange={(enabled) => setDraft({ ...draft, bookingPage: { ...draft.bookingPage, enabled: Boolean(enabled) } })} aria-label="Ativar página de agendamento" /></div>
            <div className="space-y-4 p-5">
              <div className="space-y-1.5"><Label htmlFor="page-title">Título</Label><Input id="page-title" value={draft.bookingPage.title} onChange={(event) => setDraft({ ...draft, bookingPage: { ...draft.bookingPage, title: event.target.value } })} /></div>
              <div className="space-y-1.5"><Label htmlFor="page-description">Descrição</Label><Textarea id="page-description" rows={4} value={draft.bookingPage.description} onChange={(event) => setDraft({ ...draft, bookingPage: { ...draft.bookingPage, description: event.target.value } })} /></div>
              <div className="space-y-1.5"><Label htmlFor="page-confirmation">Confirmação</Label><Textarea id="page-confirmation" rows={3} value={draft.bookingPage.confirmationMessage} onChange={(event) => setDraft({ ...draft, bookingPage: { ...draft.bookingPage, confirmationMessage: event.target.value } })} /></div>
              <div className="flex items-center justify-between gap-3 border-y border-border py-3"><div><p className="text-xs font-semibold">Mostrar WhatsApp</p><p className="text-2xs text-muted-foreground">Contato visível após a confirmação.</p></div><Switch checked={draft.bookingPage.showOfficePhone} onCheckedChange={(enabled) => setDraft({ ...draft, bookingPage: { ...draft.bookingPage, showOfficePhone: Boolean(enabled) } })} aria-label="Mostrar WhatsApp após a confirmação" /></div>
              {draft.bookingPage.showOfficePhone && <div className="space-y-1.5"><Label htmlFor="office-phone">Número do escritório</Label><Input id="office-phone" value={draft.bookingPage.officePhone ?? ''} onChange={(event) => setDraft({ ...draft, bookingPage: { ...draft.bookingPage, officePhone: event.target.value || null } })} placeholder="+55 61 99999-9999" /></div>}
              <div className="border-l-2 border-[#0057d8] bg-secondary/35 p-4"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Prévia</p><p className="mt-2 text-lg font-medium">{draft.bookingPage.title || 'Título da página'}</p><p className="mt-2 text-2xs leading-4 text-muted-foreground">{draft.bookingPage.description || 'Descrição do atendimento.'}</p><p className="mt-4 flex items-center gap-2 text-2xs font-semibold"><CalendarClock className="h-3.5 w-3.5" />{draft.defaultDurationMinutes} min · primeira reunião on-line</p></div>
            </div>
          </KlmPanel>

          <KlmPanel className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-border p-5"><div><p className="text-sm font-semibold">Links ativos</p><p className="mt-1 text-2xs text-muted-foreground">Criados na Agenda ou dentro de um card.</p></div><KlmPill>{linksQuery.data?.filter((link) => link.isActive).length ?? 0}</KlmPill></div>
            <div className="max-h-[420px] divide-y divide-border overflow-y-auto">
              {linksQuery.isLoading ? <p className="p-5 text-2xs text-muted-foreground">Carregando links...</p> : linksQuery.data?.length ? linksQuery.data.map((link) => {
                const absoluteUrl = `${window.location.origin}${link.bookingPath}`;
                return <div key={link.id} className="p-4"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-xs font-semibold">{link.title}</p><p className="mt-1 truncate text-2xs text-muted-foreground">{link.matterLabel ?? 'Link geral'} · {link.useCount}/{link.maxUses ?? '∞'} reservas</p></div><KlmPill tone={link.isActive ? 'green' : 'muted'}>{link.isActive ? 'Ativo' : 'Encerrado'}</KlmPill></div><div className="mt-3 flex gap-2"><Button variant="outline" size="sm" className="h-7 flex-1 text-2xs" onClick={async () => { await navigator.clipboard.writeText(absoluteUrl); toast({ title: 'Link copiado.' }); }} disabled={!link.isActive}><Copy className="mr-1.5 h-3 w-3" />Copiar</Button><Button variant="outline" size="icon" className="h-7 w-7" asChild><a href={absoluteUrl} target="_blank" rel="noreferrer" aria-label="Abrir link"><ExternalLink className="h-3 w-3" /></a></Button>{link.isActive && <Button variant="ghost" size="icon" className="h-7 w-7 text-red-700" onClick={() => revokeMutation.mutate(link.id)} aria-label="Revogar link"><Trash2 className="h-3 w-3" /></Button>}</div></div>;
              }) : <p className="p-5 text-center text-2xs text-muted-foreground">Nenhum link criado ainda.</p>}
            </div>
          </KlmPanel>

          <KlmPanel className="flex items-start gap-3 p-4"><Settings2 className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" /><p className="text-2xs leading-4 text-muted-foreground">Alterações ficam registradas na auditoria. A sincronização externa seguirá desligada até a etapa de APIs.</p></KlmPanel>
        </aside>
      </div>
    </div>
  );
}
