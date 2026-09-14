import { useMemo, useState } from 'react';
import { CalendarClock, Plus, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import type { Meeting, WorkspaceNotificationRecipient } from '@/types';

interface MeetingsAgendaPanelProps {
  meetings: Meeting[];
  loading?: boolean;
  readOnly?: boolean;
  defaultRecipients?: WorkspaceNotificationRecipient[];
  onSave: (meeting: Partial<Meeting> & { id?: string }) => Promise<void>;
  onDelete: (meetingId: string, expectedVersion: number) => Promise<void>;
}

function toDateTimeLocal(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  const pad = (item: number) => `${item}`.padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function MeetingsAgendaPanel({
  meetings,
  loading,
  readOnly = false,
  defaultRecipients = [],
  onSave,
  onDelete,
}: MeetingsAgendaPanelProps) {
  const [draft, setDraft] = useState({
    title: '',
    startsAt: '',
    endsAt: '',
    location: '',
    meetingUrl: '',
    notes: '',
  });
  const [saving, setSaving] = useState(false);
  const upcoming = useMemo(() => [...meetings].sort((a, b) => a.startsAt.localeCompare(b.startsAt)), [meetings]);

  const handleCreate = async () => {
    if (!draft.title || !draft.startsAt) return;
    setSaving(true);
    try {
      await onSave({
        title: draft.title,
        startsAt: new Date(draft.startsAt).toISOString(),
        endsAt: draft.endsAt ? new Date(draft.endsAt).toISOString() : null,
        location: draft.location || null,
        meetingUrl: draft.meetingUrl || null,
        notes: draft.notes || null,
        recipientOverrides: [],
      });
      setDraft({
        title: '',
        startsAt: '',
        endsAt: '',
        location: '',
        meetingUrl: '',
        notes: '',
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <CalendarClock className="h-4 w-4" />
          Agenda interna e lembretes
        </CardTitle>
        <CardDescription>
          A reunião nasce dentro do sistema e usa as mesmas regras de alerta do workspace. Google Calendar pode entrar depois como sincronização.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="rounded-xl border p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Título da reunião</Label>
              <Input value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} disabled={readOnly} placeholder="Ex.: Reunião de alinhamento com lead qualificado" />
            </div>
            <div className="space-y-1.5">
              <Label>Início</Label>
              <Input type="datetime-local" value={draft.startsAt} onChange={(event) => setDraft((current) => ({ ...current, startsAt: event.target.value }))} disabled={readOnly} />
            </div>
            <div className="space-y-1.5">
              <Label>Fim</Label>
              <Input type="datetime-local" value={draft.endsAt} onChange={(event) => setDraft((current) => ({ ...current, endsAt: event.target.value }))} disabled={readOnly} />
            </div>
            <div className="space-y-1.5">
              <Label>Local ou contexto</Label>
              <Input value={draft.location} onChange={(event) => setDraft((current) => ({ ...current, location: event.target.value }))} disabled={readOnly} placeholder="Escritório, Meet ou telefone" />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <Label>Link da reunião</Label>
              <Input value={draft.meetingUrl} onChange={(event) => setDraft((current) => ({ ...current, meetingUrl: event.target.value }))} disabled={readOnly} placeholder="https://meet.google.com/..." />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <Label>Observações</Label>
              <Input value={draft.notes} onChange={(event) => setDraft((current) => ({ ...current, notes: event.target.value }))} disabled={readOnly} placeholder="Objetivo, pauta e contexto operacional" />
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              Destinatários padrão: {defaultRecipients.map((item) => item.name).join(', ') || 'gestor e responsável do matter'}
            </p>
            <Button onClick={handleCreate} disabled={saving || readOnly || !draft.title || !draft.startsAt}>
              <Plus className="mr-2 h-4 w-4" />
              {saving ? 'Salvando...' : 'Criar reunião'}
            </Button>
          </div>
        </div>

        <div className="space-y-3">
          {loading ? (
            <div className="rounded-xl border p-4 text-sm text-muted-foreground">Carregando agenda...</div>
          ) : upcoming.length === 0 ? (
            <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
              Nenhuma reunião criada ainda. Quando você agendar aqui, o sistema já prepara lembrete e visibilidade para o time.
            </div>
          ) : (
            upcoming.map((meeting) => (
              <div key={meeting.id} className="rounded-xl border p-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium">{meeting.title}</p>
                      <Badge variant={meeting.status === 'scheduled' ? 'outline' : meeting.status === 'completed' ? 'secondary' : 'destructive'}>
                        {meeting.status === 'scheduled' ? 'Agendada' : meeting.status === 'completed' ? 'Concluída' : 'Cancelada'}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {new Date(meeting.startsAt).toLocaleString('pt-BR')}
                      {meeting.endsAt ? ` · até ${new Date(meeting.endsAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}` : ''}
                    </p>
                    {meeting.location && <p className="text-xs text-muted-foreground">Local: {meeting.location}</p>}
                    {meeting.notes && <p className="text-sm text-muted-foreground">{meeting.notes}</p>}
                  </div>
                  <Button variant="ghost" size="icon" disabled={readOnly} onClick={() => onDelete(meeting.id, meeting.version)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}
