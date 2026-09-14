import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Check, Copy, ExternalLink, Link2, Loader2, Send, UserRound } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { KlmPill } from '@/klm/components/Primitives';
import { klmApi, type KlmBookingLink, type KlmCalendarProvider } from '@/klm/api';

type MatterOption = { id: string; label: string };

type BookingLinkDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  matterOptions?: MatterOption[];
  lockedMatterId?: string;
  lockedMatterLabel?: string;
  initialTitle?: string;
  defaultMaxUses?: number;
  onCreated?: (link: KlmBookingLink, absoluteUrl: string) => void;
};

const providerLabel: Record<KlmCalendarProvider, string> = {
  manual: 'Link manual',
  google_meet: 'Google Meet',
  microsoft_teams: 'Microsoft Teams',
};

export function BookingLinkDialog({
  open,
  onOpenChange,
  matterOptions = [],
  lockedMatterId,
  lockedMatterLabel,
  initialTitle = 'Reunião inicial com a equipe',
  defaultMaxUses = 1,
  onCreated,
}: BookingLinkDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({
    queryKey: ['klm', 'calendar-settings'],
    queryFn: klmApi.calendarSettings,
    enabled: open,
  });
  const [matterId, setMatterId] = useState(lockedMatterId ?? '');
  const [title, setTitle] = useState(initialTitle);
  const [description, setDescription] = useState('Escolha o melhor horário para conversarmos on-line.');
  const [durationMinutes, setDurationMinutes] = useState(45);
  const [maxUses, setMaxUses] = useState(defaultMaxUses);
  const [provider, setProvider] = useState<KlmCalendarProvider>('manual');
  const [meetingUrl, setMeetingUrl] = useState('');
  const [created, setCreated] = useState<KlmBookingLink | null>(null);

  useEffect(() => {
    if (!open) return;
    setMatterId(lockedMatterId ?? '');
    setTitle(initialTitle);
    setDescription('Escolha o melhor horário para conversarmos on-line.');
    setDurationMinutes(settingsQuery.data?.defaultDurationMinutes ?? 45);
    setMaxUses(defaultMaxUses);
    setProvider(settingsQuery.data?.provider ?? 'manual');
    setMeetingUrl(settingsQuery.data?.defaultMeetingUrl ?? '');
    setCreated(null);
  }, [defaultMaxUses, initialTitle, lockedMatterId, open, settingsQuery.data]);

  const mutation = useMutation({
    mutationFn: () => klmApi.createBookingLink({
      matterId: (lockedMatterId ?? matterId) || null,
      title: title.trim(),
      description: description.trim() || null,
      durationMinutes,
      maxUses,
      meetingProvider: provider,
      meetingUrl: meetingUrl.trim() || null,
    }),
    onSuccess: (link) => {
      setCreated(link);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'booking-links'] });
      onCreated?.(link, `${window.location.origin}${link.bookingPath}`);
    },
    onError: (error: Error) => toast({
      variant: 'destructive',
      title: 'Não foi possível criar o link',
      description: error.message,
    }),
  });

  const absoluteUrl = useMemo(
    () => created ? `${window.location.origin}${created.bookingPath}` : '',
    [created],
  );

  const copyLink = async () => {
    if (!absoluteUrl) return;
    await navigator.clipboard.writeText(absoluteUrl);
    toast({ title: 'Link copiado para enviar ao lead.' });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>{created ? 'Link pronto para envio' : 'Criar página de agendamento'}</DialogTitle>
          <DialogDescription>
            {created
              ? 'O lead escolhe um horário livre e a reunião entra na Agenda em tempo real.'
              : 'Links vinculados já alimentam o card. Links gerais podem ser conectados ao caso depois.'}
          </DialogDescription>
        </DialogHeader>

        {created ? (
          <div className="space-y-4 py-2">
            <div className="flex items-start gap-3 border-y border-border py-4">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-emerald-500/12 text-emerald-700 dark:text-emerald-400"><Check className="h-5 w-5" /></span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold">{created.title}</p>
                  <KlmPill tone={created.matterId ? 'green' : 'gold'}>{created.matterId ? 'Vinculado ao card' : 'Link geral'}</KlmPill>
                </div>
                <p className="mt-1 text-2xs text-muted-foreground">{created.durationMinutes} min · {providerLabel[created.meetingProvider]} · {created.maxUses ?? 'Ilimitados'} uso(s)</p>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="booking-created-url">Endereço público</Label>
              <div className="flex gap-2">
                <Input id="booking-created-url" readOnly value={absoluteUrl} className="min-w-0 font-mono text-2xs" />
                <Button type="button" size="icon" variant="outline" onClick={copyLink} aria-label="Copiar link de agendamento"><Copy className="h-4 w-4" /></Button>
              </div>
            </div>
            <p className="flex items-start gap-2 text-2xs leading-4 text-muted-foreground">
              <Send className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Envie este endereço na conversa. A integração externa continua bloqueada localmente, então nenhuma mensagem foi disparada automaticamente.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 py-2 sm:grid-cols-2">
            {lockedMatterId ? (
              <div className="flex items-center gap-3 border-y border-border py-3 sm:col-span-2">
                <UserRound className="h-4 w-4 text-muted-foreground" />
                <div><p className="text-2xs text-muted-foreground">Card vinculado</p><p className="text-xs font-semibold">{lockedMatterLabel ?? lockedMatterId}</p></div>
              </div>
            ) : (
              <div className="space-y-1.5 sm:col-span-2">
                <Label>Card do lead</Label>
                <Select value={matterId || '__none__'} onValueChange={(value) => setMatterId(value === '__none__' ? '' : value)}>
                  <SelectTrigger aria-label="Card do link de agendamento"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Link geral · vincular depois</SelectItem>
                    {matterOptions.map((matter) => <SelectItem key={matter.id} value={matter.id}>{matter.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="booking-title">Título visto pelo lead</Label>
              <Input id="booking-title" value={title} onChange={(event) => setTitle(event.target.value)} />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="booking-description">Mensagem da página</Label>
              <Textarea id="booking-description" rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>Duração</Label>
              <Select value={String(durationMinutes)} onValueChange={(value) => setDurationMinutes(Number(value))}>
                <SelectTrigger aria-label="Duração do agendamento"><SelectValue /></SelectTrigger>
                <SelectContent>{[30, 45, 60, 90].map((value) => <SelectItem key={value} value={String(value)}>{value} minutos</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="booking-max-uses">Quantidade de reservas</Label>
              <Input id="booking-max-uses" type="number" min={1} max={1000} value={maxUses} onChange={(event) => setMaxUses(Math.max(1, Number(event.target.value) || 1))} />
            </div>
            <div className="space-y-1.5">
              <Label>Tipo de sala</Label>
              <Select value={provider} onValueChange={(value: KlmCalendarProvider) => setProvider(value)}>
                <SelectTrigger aria-label="Tipo de sala do agendamento"><SelectValue /></SelectTrigger>
                <SelectContent>{Object.entries(providerLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="booking-room-url">Link fixo da sala</Label>
              <Input id="booking-room-url" value={meetingUrl} onChange={(event) => setMeetingUrl(event.target.value)} placeholder="https://meet.google.com/..." />
            </div>
            <div className="flex items-start gap-2 border-t border-border pt-3 text-2xs text-muted-foreground sm:col-span-2">
              <CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Os horários disponíveis respeitam antecedência, duração, intervalos e reuniões já existentes.
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{created ? 'Fechar' : 'Cancelar'}</Button>
          {created ? (
            <>
              <Button variant="outline" onClick={copyLink}><Copy className="mr-2 h-4 w-4" />Copiar link</Button>
              <Button asChild><a href={absoluteUrl} target="_blank" rel="noreferrer"><ExternalLink className="mr-2 h-4 w-4" />Visualizar página</a></Button>
            </>
          ) : (
            <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || title.trim().length < 3}>
              {mutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Link2 className="mr-2 h-4 w-4" />}
              Gerar link
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default BookingLinkDialog;
