import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CalendarDays, CheckCircle2, Clock3, ExternalLink, HeartPulse, Loader2, MessageCircle, ShieldCheck, Video } from 'lucide-react';
import { useParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { klmApi } from '@/klm/api';
import { cn } from '@/lib/utils';

function dateKey(value: string, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value));
  const record = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${record.year}-${record.month}-${record.day}`;
}

function dayLabel(key: string) {
  const [year, month, day] = key.split('-').map(Number);
  return new Intl.DateTimeFormat('pt-BR', { weekday: 'short', day: '2-digit', month: 'short' }).format(new Date(year, month - 1, day));
}

function timeLabel(value: string, timeZone: string) {
  return new Intl.DateTimeFormat('pt-BR', { timeZone, hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

export default function PublicBookingPage() {
  const { token = '' } = useParams();
  const pageQuery = useQuery({
    queryKey: ['public-booking', token],
    queryFn: () => klmApi.publicBookingPage(token),
    enabled: Boolean(token),
    retry: false,
  });
  const [selectedDay, setSelectedDay] = useState('');
  const [selectedSlot, setSelectedSlot] = useState('');
  const [form, setForm] = useState({ name: '', phone: '', email: '', notes: '' });

  const groupedSlots = useMemo(() => {
    const result = new Map<string, Array<{ startsAt: string; endsAt: string }>>();
    const page = pageQuery.data;
    if (!page) return result;
    page.slots.forEach((slot) => {
      const key = dateKey(slot.startsAt, page.timezone);
      result.set(key, [...(result.get(key) ?? []), slot]);
    });
    return result;
  }, [pageQuery.data]);

  useEffect(() => {
    const firstDay = groupedSlots.keys().next().value as string | undefined;
    if (firstDay && !groupedSlots.has(selectedDay)) {
      setSelectedDay(firstDay);
      setSelectedSlot('');
    }
  }, [groupedSlots, selectedDay]);

  const bookingMutation = useMutation({
    mutationFn: () => klmApi.createPublicBooking(token, {
      startsAt: selectedSlot,
      name: form.name.trim(),
      phone: form.phone.trim() || undefined,
      email: form.email.trim() || undefined,
      notes: form.notes.trim() || undefined,
    }),
  });

  if (pageQuery.isLoading) {
    return <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando horários...</div>;
  }

  if (pageQuery.isError || !pageQuery.data) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#f5f5f3] px-5 dark:bg-background">
        <section className="w-full max-w-lg border-y border-border bg-background py-10 text-center">
          <CalendarDays className="mx-auto h-9 w-9 text-muted-foreground" />
          <h1 className="mt-5 text-2xl font-semibold">Link indisponível</h1>
          <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-muted-foreground">Este endereço expirou, já foi utilizado ou foi revogado. Solicite um novo link pelo WhatsApp do escritório.</p>
        </section>
      </main>
    );
  }

  const page = pageQuery.data;
  const selected = page.slots.find((slot) => slot.startsAt === selectedSlot);

  if (bookingMutation.data) {
    const confirmation = bookingMutation.data;
    return (
      <main className="min-h-screen bg-[#f5f5f3] text-foreground dark:bg-background">
        <header className="border-b border-white/10 bg-[#050505] text-white">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-5 py-5 sm:px-8">
            <img src="/logo.svg" alt="Meu Executivo" className="h-8 w-auto brightness-0 invert" />
            <span className="text-2xs font-semibold uppercase text-primary">Reunião on-line</span>
          </div>
        </header>
        <section className="mx-auto max-w-3xl px-5 py-16 text-center sm:px-8 sm:py-24">
          <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-600 text-white"><CheckCircle2 className="h-7 w-7" /></span>
          <p className="mt-7 text-2xs font-semibold uppercase text-primary">Agendamento confirmado</p>
          <h1 className="mt-2 text-3xl font-medium sm:text-4xl">Sua reunião está reservada.</h1>
          <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-muted-foreground">{confirmation.message}</p>
          <div className="mx-auto mt-9 grid max-w-xl gap-px overflow-hidden border border-border bg-border text-left sm:grid-cols-2">
            <div className="bg-background p-5"><p className="text-2xs text-muted-foreground">Data e horário</p><p className="mt-1 text-sm font-semibold">{new Intl.DateTimeFormat('pt-BR', { timeZone: confirmation.timezone, dateStyle: 'long', timeStyle: 'short' }).format(new Date(confirmation.startsAt))}</p></div>
            <div className="bg-background p-5"><p className="text-2xs text-muted-foreground">Formato</p><p className="mt-1 text-sm font-semibold">{confirmation.location}</p></div>
          </div>
          <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
            {confirmation.meetingUrl && <Button asChild className="bg-[#050505] text-white hover:bg-[#1c3254]"><a href={confirmation.meetingUrl} target="_blank" rel="noreferrer"><Video className="mr-2 h-4 w-4" />Abrir sala da reunião</a></Button>}
            {page.showOfficePhone && page.officePhone && <Button variant="outline" asChild><a href={`https://wa.me/${page.officePhone.replace(/\D/g, '')}`} target="_blank" rel="noreferrer"><MessageCircle className="mr-2 h-4 w-4" />Falar no WhatsApp</a></Button>}
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#f5f5f3] text-foreground dark:bg-background">
      <header className="border-b border-white/10 bg-[#050505] text-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5 sm:px-8">
          <img src="/logo.svg" alt="Meu Executivo" className="h-8 w-auto brightness-0 invert" />
          <div className="flex items-center gap-2 text-2xs text-white/70"><ShieldCheck className="h-3.5 w-3.5 text-primary" />Ambiente de agendamento Meu Executivo</div>
        </div>
      </header>

      <section className="border-b border-border bg-background">
        <div className="mx-auto grid max-w-6xl gap-8 px-5 py-10 sm:px-8 lg:grid-cols-[0.72fr_1.28fr] lg:gap-14 lg:py-14">
          <div className="lg:sticky lg:top-8 lg:self-start">
            <div className="flex items-center gap-3 text-primary"><HeartPulse className="h-5 w-5" /><span className="text-2xs font-semibold uppercase">Atendimento nacional em saúde</span></div>
            <h1 className="mt-5 text-3xl font-medium leading-tight sm:text-4xl">{page.title}</h1>
            <p className="mt-4 max-w-lg text-sm leading-6 text-muted-foreground">{page.description}</p>
            <div className="mt-8 space-y-4 border-y border-border py-5 text-sm">
              <p className="flex items-center gap-3"><Video className="h-4 w-4 text-primary" />Primeira reunião sempre on-line</p>
              <p className="flex items-center gap-3"><Clock3 className="h-4 w-4 text-primary" />{page.durationMinutes} minutos reservados</p>
              <p className="flex items-center gap-3"><MessageCircle className="h-4 w-4 text-primary" />WhatsApp disponível durante o atendimento</p>
            </div>
          </div>

          <div className="min-w-0">
            <div className="flex items-center justify-between gap-3 border-b border-border pb-4">
              <div><p className="text-2xs font-semibold uppercase text-primary">1. Escolha o dia</p><h2 className="mt-1 text-lg font-semibold">Horários disponíveis</h2></div>
              <CalendarDays className="h-5 w-5 text-muted-foreground" />
            </div>

            {groupedSlots.size === 0 ? (
              <div className="border-b border-border py-12 text-center"><CalendarDays className="mx-auto h-7 w-7 text-muted-foreground" /><p className="mt-3 text-sm font-semibold">Sem horários livres neste período</p><p className="mt-1 text-xs text-muted-foreground">Solicite outra janela pelo WhatsApp do escritório.</p></div>
            ) : (
              <>
                <div className="flex gap-2 overflow-x-auto py-5">
                  {Array.from(groupedSlots.keys()).map((key) => (
                    <button key={key} type="button" onClick={() => { setSelectedDay(key); setSelectedSlot(''); }} className={cn('min-w-[116px] border px-3 py-3 text-left transition', selectedDay === key ? 'border-[#050505] bg-[#050505] text-white dark:border-primary dark:bg-primary dark:text-black' : 'border-border bg-background hover:border-primary')}>
                      <span className="block text-2xs font-semibold capitalize">{dayLabel(key)}</span>
                      <span className={cn('mt-1 block text-2xs', selectedDay === key ? 'text-white/65 dark:text-black/60' : 'text-muted-foreground')}>{groupedSlots.get(key)?.length} horários</span>
                    </button>
                  ))}
                </div>
                <div className="grid grid-cols-3 gap-2 border-b border-border pb-6 sm:grid-cols-4">
                  {(groupedSlots.get(selectedDay) ?? []).map((slot) => (
                    <button key={slot.startsAt} type="button" onClick={() => setSelectedSlot(slot.startsAt)} className={cn('h-11 border text-sm font-semibold transition', selectedSlot === slot.startsAt ? 'border-primary bg-primary/10 text-primary dark:bg-primary/15 dark:text-primary' : 'border-border bg-background hover:border-primary')}>
                      {timeLabel(slot.startsAt, page.timezone)}
                    </button>
                  ))}
                </div>
              </>
            )}

            <div className="pt-7">
              <p className="text-2xs font-semibold uppercase text-primary">2. Confirme seus dados</p>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="booking-name">Nome completo</Label><Input id="booking-name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
                <div className="space-y-1.5"><Label htmlFor="booking-phone">WhatsApp</Label><Input id="booking-phone" value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} placeholder="(61) 99999-9999" /></div>
                <div className="space-y-1.5"><Label htmlFor="booking-email">E-mail</Label><Input id="booking-email" type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="voce@exemplo.com" /></div>
                <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="booking-notes">Algo importante antes da reunião?</Label><Textarea id="booking-notes" rows={3} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} placeholder="Informe urgência, tratamento ou medicamento, se desejar." /></div>
              </div>
              {selected && <p className="mt-4 border-l-2 border-primary pl-3 text-xs text-muted-foreground">Selecionado: {new Intl.DateTimeFormat('pt-BR', { timeZone: page.timezone, dateStyle: 'full', timeStyle: 'short' }).format(new Date(selected.startsAt))}</p>}
              {bookingMutation.isError && <p className="mt-4 text-xs font-medium text-red-700">Não foi possível reservar este horário. Atualize a página e escolha outro.</p>}
              <Button className="mt-6 h-11 w-full bg-[#050505] text-white hover:bg-[#1c3254]" onClick={() => bookingMutation.mutate()} disabled={!selectedSlot || form.name.trim().length < 2 || (!form.phone.trim() && !form.email.trim()) || bookingMutation.isPending}>
                {bookingMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                Confirmar agendamento
              </Button>
            </div>
          </div>
        </div>
      </section>

      <footer className="bg-[#050505] text-white">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-5 py-6 text-2xs text-white/55 sm:flex-row sm:items-center sm:justify-between sm:px-8"><span>{page.officeName}</span><span className="flex items-center gap-1.5"><ExternalLink className="h-3 w-3" />Atendimento on-line em todo o Brasil</span></div>
      </footer>
    </main>
  );
}
