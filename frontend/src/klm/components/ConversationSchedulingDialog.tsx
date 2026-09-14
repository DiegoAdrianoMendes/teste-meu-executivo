import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, CheckCircle2, ExternalLink, Link2, LoaderCircle, RefreshCw, Video } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmBookingLink, type KlmMeeting } from '@/klm/api';
import { BookingLinkDialog } from '@/klm/components/BookingLinkDialog';
import { KlmPill } from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';

type ConversationSchedulingDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  matterId: string;
  contactName: string;
  onBooked: (meeting: KlmMeeting) => void;
  onLinkCreated: (link: KlmBookingLink, absoluteUrl: string) => void;
};

const providerLabel = {
  manual: 'Sala on-line manual',
  google_meet: 'Google Meet',
  microsoft_teams: 'Microsoft Teams',
};

export function ConversationSchedulingDialog({
  open,
  onOpenChange,
  matterId,
  contactName,
  onBooked,
  onLinkCreated,
}: ConversationSchedulingDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [durationMinutes, setDurationMinutes] = useState(45);
  const [selectedStart, setSelectedStart] = useState('');
  const [linkOpen, setLinkOpen] = useState(false);
  const availabilityQuery = useQuery({
    queryKey: ['klm', 'agenda-availability', durationMinutes],
    queryFn: () => klmApi.availability(durationMinutes),
    enabled: open,
    staleTime: 15_000,
  });

  useEffect(() => {
    if (!open) return;
    setSelectedStart('');
  }, [durationMinutes, open]);

  const groupedSlots = useMemo(() => {
    const timezone = availabilityQuery.data?.timezone ?? 'America/Sao_Paulo';
    const groups = new Map<string, Array<{ startsAt: string; endsAt: string }>>();
    for (const slot of (availabilityQuery.data?.slots ?? []).slice(0, 42)) {
      const label = new Intl.DateTimeFormat('pt-BR', {
        weekday: 'short',
        day: '2-digit',
        month: 'short',
        timeZone: timezone,
      }).format(new Date(slot.startsAt));
      groups.set(label, [...(groups.get(label) ?? []), slot]);
    }
    return [...groups.entries()].slice(0, 7);
  }, [availabilityQuery.data]);

  const bookingMutation = useMutation({
    mutationFn: () => klmApi.createInternalBooking({
      matterId,
      title: `Reunião online: ${contactName}`,
      startsAt: selectedStart,
      durationMinutes,
      notes: 'Criada pela conversa após seleção de disponibilidade; convite externo bloqueado no ambiente local.',
    }),
    onSuccess: (meeting) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'meetings'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'agenda-availability'] });
      onBooked(meeting);
      onOpenChange(false);
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Horário não confirmado', description: error.message });
      setSelectedStart('');
      void availabilityQuery.refetch();
    },
  });

  const openBookingPage = () => {
    onOpenChange(false);
    setLinkOpen(true);
  };

  const timezone = availabilityQuery.data?.timezone ?? 'America/Sao_Paulo';
  const selectedSlot = availabilityQuery.data?.slots.find((slot) => slot.startsAt === selectedStart);

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex max-h-[92vh] flex-col sm:max-w-[720px]">
          <DialogHeader>
            <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]"><CalendarClock className="h-4 w-4" /></div>
            <DialogTitle>Agendar reunião com {contactName}</DialogTitle>
            <DialogDescription>Escolha um horário realmente livre na agenda ou gere a página para o lead escolher. A primeira reunião permanece on-line.</DialogDescription>
          </DialogHeader>

          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto py-1 scrollbar-thin">
            <div className="grid gap-3 border-y border-border py-3 sm:grid-cols-[170px_minmax(0,1fr)] sm:items-end">
              <div className="space-y-1.5">
                <Label>Duração da reunião</Label>
                <Select value={String(durationMinutes)} onValueChange={(value) => setDurationMinutes(Number(value))}>
                  <SelectTrigger aria-label="Duração da reunião"><SelectValue /></SelectTrigger>
                  <SelectContent>{[30, 45, 60, 90].map((duration) => <SelectItem key={duration} value={String(duration)}>{duration} minutos</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-2 text-2xs">
                {availabilityQuery.data ? <>
                  <KlmPill tone={availabilityQuery.data.connectionStatus === 'connected' || availabilityQuery.data.connectionStatus === 'manual_ready' ? 'green' : 'gold'}>{providerLabel[availabilityQuery.data.meetingProvider]}</KlmPill>
                  <span className="text-muted-foreground">{timezone} · {availabilityQuery.data.slots.length} horários livres</span>
                  {availabilityQuery.data.meetingUrl ? <a href={availabilityQuery.data.meetingUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-[#0b7435] hover:underline dark:text-emerald-400"><Video className="h-3 w-3" />Sala configurada</a> : <span className="text-[#a53e3c]">Sala fixa não configurada</span>}
                </> : <span className="text-muted-foreground">Consultando regras, intervalos e conflitos da agenda.</span>}
              </div>
            </div>

            {availabilityQuery.isPending ? <div className="flex min-h-[220px] items-center justify-center gap-2 text-2xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Calculando disponibilidades reais</div> : null}
            {availabilityQuery.isError ? <div className="flex min-h-[200px] flex-col items-center justify-center text-center"><CalendarClock className="h-5 w-5 text-[#a53e3c]" /><p className="mt-3 text-2xs font-semibold">Não foi possível consultar a agenda</p><p className="mt-1 max-w-sm text-2xs leading-4 text-muted-foreground">Nenhum horário foi criado por aproximação. Tente novamente ou abra as configurações da agenda.</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void availabilityQuery.refetch()}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Consultar novamente</Button></div> : null}
            {!availabilityQuery.isPending && !availabilityQuery.isError && groupedSlots.length === 0 ? <div className="flex min-h-[200px] flex-col items-center justify-center text-center"><CalendarClock className="h-5 w-5 text-[#0057d8]" /><p className="mt-3 text-2xs font-semibold">Nenhum horário livre no horizonte configurado</p><p className="mt-1 max-w-sm text-2xs leading-4 text-muted-foreground">Revise disponibilidade, antecedência e bloqueios em Configurações da Agenda, ou envie uma página quando houver novos horários.</p></div> : null}

            {groupedSlots.length ? <div className="space-y-4" aria-label="Horários disponíveis">{groupedSlots.map(([day, slots]) => (
              <section key={day}>
                <p className="mb-2 text-2xs font-semibold capitalize text-muted-foreground">{day}</p>
                <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">{slots.map((slot) => {
                  const label = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: timezone }).format(new Date(slot.startsAt));
                  const selected = selectedStart === slot.startsAt;
                  return <button key={slot.startsAt} type="button" aria-pressed={selected} onClick={() => setSelectedStart(slot.startsAt)} className={cn('h-9 rounded-md border text-2xs font-semibold transition', selected ? 'border-[#1a9948] bg-[#1a9948] text-white ring-2 ring-[#1a9948]/20' : 'border-border bg-card hover:border-primary hover:bg-primary/10')}>{label}</button>;
                })}</div>
              </section>
            ))}</div> : null}

            {selectedSlot ? <div className="flex items-start gap-2 border-l-2 border-[#1a9948] bg-[#1a9948]/5 px-3 py-2.5 text-2xs"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#1a9948]" /><div><p className="font-semibold">Horário selecionado</p><p className="mt-0.5 text-muted-foreground">{new Date(selectedSlot.startsAt).toLocaleString('pt-BR', { dateStyle: 'full', timeStyle: 'short', timeZone: timezone })} · {durationMinutes} min</p></div></div> : null}
          </div>

          <DialogFooter className="gap-2 border-t border-border pt-4 sm:justify-between">
            <Button type="button" variant="outline" onClick={openBookingPage}><Link2 className="mr-2 h-4 w-4" />Enviar página para escolher</Button>
            <div className="flex gap-2"><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button><Button type="button" onClick={() => bookingMutation.mutate()} disabled={!selectedStart || bookingMutation.isPending}>{bookingMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <CalendarClock className="mr-2 h-4 w-4" />}{bookingMutation.isPending ? 'Confirmando' : 'Confirmar horário'}</Button></div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <BookingLinkDialog
        open={linkOpen}
        onOpenChange={setLinkOpen}
        lockedMatterId={matterId}
        lockedMatterLabel={contactName}
        initialTitle={`Reunião inicial · ${contactName}`}
        onCreated={(link, absoluteUrl) => {
          onLinkCreated(link, absoluteUrl);
          setLinkOpen(false);
        }}
      />
    </>
  );
}

export default ConversationSchedulingDialog;
