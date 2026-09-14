import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { canPlatform } from '@/lib/platform-access';
import { createPlatformIncident, fetchPlatformIncidents, updatePlatformIncident, type PlatformManagedIncident } from '@/services/platform';

const statusLabels: Record<PlatformManagedIncident['status'], string> = {
  investigating: 'Investigando', identified: 'Causa identificada', monitoring: 'Monitorando', resolved: 'Resolvido',
};

export function PlatformIncidentPanel() {
  const { effectiveUser } = useBlackboxManifest();
  const canManage = canPlatform(effectiveUser, 'security', 'manage');
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [severity, setSeverity] = useState<PlatformManagedIncident['severity']>('sev3');
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [searchParams] = useSearchParams();
  const focusedIncidentId = searchParams.get('incident');
  const incidents = useQuery({
    queryKey: ['blackbox', 'incidents', cursor],
    queryFn: () => fetchPlatformIncidents({ cursor: cursor ?? undefined, limit: 25 }),
  });
  useEffect(() => {
    if (!focusedIncidentId || !incidents.data) return;
    document.getElementById(`platform-incident-${focusedIncidentId}`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    });
  }, [focusedIncidentId, incidents.data]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['blackbox', 'incidents'] });
  const create = useMutation({
    mutationFn: createPlatformIncident,
    onSuccess: async () => { setTitle(''); setSummary(''); await refresh(); toast({ title: 'Incidente aberto e auditado.' }); },
    onError: () => toast({ variant: 'destructive', title: 'Incidente não aberto', description: 'Revise os dados e tente novamente.' }),
  });
  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: PlatformManagedIncident['status'] }) => updatePlatformIncident(id, { status, note: `Status alterado para ${statusLabels[status]}.` }),
    onSuccess: async () => { await refresh(); toast({ title: 'Incidente atualizado.' }); },
  });

  return <Card><CardHeader><CardTitle>Gestão de incidentes</CardTitle><CardDescription>Abertura, acompanhamento e resolução com autoria e histórico. Não confundir com incidentes inferidos pela telemetria.</CardDescription></CardHeader><CardContent className="space-y-5">
    {canManage && <div className="grid gap-3 rounded-lg border p-4 lg:grid-cols-[1fr_160px_auto] lg:items-end"><div className="space-y-3"><div><Label htmlFor="incident-title">Título</Label><Input id="incident-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Ex.: fila de mensagens acumulando" /></div><div><Label htmlFor="incident-summary">Resumo inicial</Label><Textarea id="incident-summary" value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="Impacto observado, escopo e primeira medida" /></div></div><div><Label>Severidade</Label><Select value={severity} onValueChange={(value) => setSeverity(value as PlatformManagedIncident['severity'])}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{['sev1', 'sev2', 'sev3', 'sev4'].map((value) => <SelectItem key={value} value={value}>{value.toUpperCase()}</SelectItem>)}</SelectContent></Select></div><Button disabled={!title.trim() || !summary.trim() || create.isPending} onClick={() => create.mutate({ title, summary, severity, components: [] })}>Abrir incidente</Button></div>}
    {incidents.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando incidentes administrados…</p> : incidents.isError ? <Alert variant="destructive"><AlertTitle>Incidentes indisponíveis</AlertTitle><AlertDescription>A telemetria permanece separada; esta falha não significa que não existam incidentes.</AlertDescription></Alert> : incidents.data?.items.length ? <div className="space-y-3">{incidents.data.items.map((incident) => <article id={`platform-incident-${incident.id}`} key={incident.id} aria-current={focusedIncidentId === incident.id ? 'true' : undefined} className={`rounded-lg border p-4 ${focusedIncidentId === incident.id ? 'ring-2 ring-primary ring-offset-2' : ''}`}><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap gap-2"><Badge variant={incident.severity === 'sev1' || incident.severity === 'sev2' ? 'destructive' : 'outline'}>{incident.severity.toUpperCase()}</Badge><Badge variant={incident.status === 'resolved' ? 'secondary' : 'default'}>{statusLabels[incident.status]}</Badge></div><h3 className="mt-2 font-semibold">{incident.title}</h3><p className="mt-1 text-sm text-muted-foreground">{incident.summary}</p><p className="mt-2 text-xs text-muted-foreground">Aberto em {new Date(incident.startedAt).toLocaleString('pt-BR')} · {incident.updates.length} atualização(ões)</p></div>{canManage && <Select value={incident.status} onValueChange={(value) => update.mutate({ id: incident.id, status: value as PlatformManagedIncident['status'] })}><SelectTrigger className="w-48" aria-label={`Status de ${incident.title}`}><SelectValue /></SelectTrigger><SelectContent>{Object.entries(statusLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>}</div></article>)}</div> : <p className="text-sm text-muted-foreground">Nenhum incidente administrado registrado.</p>}
    {incidents.data && <div className="flex items-center justify-between gap-3"><p className="text-xs text-muted-foreground">{incidents.data.items.length} incidente(s) nesta página · {incidents.data.openCount} aberto(s) na plataforma.</p><div className="flex gap-2"><Button size="sm" variant="outline" disabled={cursorHistory.length === 0 || incidents.isFetching} onClick={() => { const history = [...cursorHistory]; const previous = history.pop() ?? ''; setCursorHistory(history); setCursor(previous || null); }}>Anterior</Button><Button size="sm" variant="outline" disabled={!incidents.data.hasMore || !incidents.data.nextCursor || incidents.isFetching} onClick={() => { if (!incidents.data?.nextCursor) return; setCursorHistory((history) => [...history, cursor ?? '']); setCursor(incidents.data.nextCursor); }}>Próxima</Button></div></div>}
  </CardContent></Card>;
}
