import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  fetchPlatformGoogleBusinessConnection,
  fetchPlatformGoogleBusinessJobsPage,
  queuePlatformGoogleBusinessJob,
  savePlatformGoogleBusinessConnection,
} from '@/services/platform';
import type { GoogleBusinessConnectionUpdate, GoogleBusinessProfileConnection, GoogleBusinessPublishRequest } from '@/types';
import { MapPinned, Send, UploadCloud } from 'lucide-react';

export function PlatformGoogleBusinessPanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<GoogleBusinessConnectionUpdate | null>(null);
  const [operationReason, setOperationReason] = useState('');
  const [jobPage, setJobPage] = useState(0);
  const jobPageSize = 25;
  const normalizedReason = operationReason.trim();
  const reasonValid = normalizedReason.length >= 8;
  const [jobDraft, setJobDraft] = useState<GoogleBusinessPublishRequest>({
    targetType: 'business_post',
    title: '',
    payload: { summary: '', ctaUrl: '' },
    scheduledFor: null,
  });

  const connectionQuery = useQuery({
    queryKey: ['platform-blog', 'google-business', 'connection'],
    queryFn: fetchPlatformGoogleBusinessConnection,
    staleTime: 15_000,
  });
  const jobsQuery = useQuery({
    queryKey: ['platform-blog', 'google-business', 'jobs', jobPage],
    queryFn: () => fetchPlatformGoogleBusinessJobsPage(jobPageSize, jobPage * jobPageSize),
    staleTime: 15_000,
  });

  useEffect(() => {
    if (!connectionQuery.data) return;
    const connection: GoogleBusinessProfileConnection = connectionQuery.data;
    setDraft({
      businessName: connection.businessName,
      accountName: connection.accountName,
      locationId: connection.locationId,
      locationName: connection.locationName,
      profileUrl: connection.profileUrl,
      clientId: '',
      clientSecret: '',
      refreshToken: '',
      accessToken: '',
      settings: connection.settings ?? {},
    });
  }, [connectionQuery.data]);

  const saveMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: GoogleBusinessConnectionUpdate; reason: string }) =>
      savePlatformGoogleBusinessConnection(payload, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform-blog', 'google-business'] });
      toast({ title: 'Conexão Google Business atualizada.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar conexão', description: error.message }),
  });

  const queueMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: GoogleBusinessPublishRequest; reason: string }) =>
      queuePlatformGoogleBusinessJob(payload, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform-blog', 'google-business', 'jobs'] });
      setJobDraft({
        targetType: 'business_post',
        title: '',
        payload: { summary: '', ctaUrl: '' },
        scheduledFor: null,
      });
      toast({ title: 'Job adicionado à fila do Google Business Profile.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao criar job', description: error.message }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Google Business Profile</h2>
        <p className="text-sm text-muted-foreground">
          Controle da conexão, mídia, posts e fila de publicação para Google Maps e superfícies locais da marca.
        </p>
      </div>

      <div className="space-y-1.5 rounded-2xl border bg-muted/20 p-4">
        <Label htmlFor="google-business-operation-reason">Motivo da alteração</Label>
        <Input
          id="google-business-operation-reason"
          value={operationReason}
          onChange={(event) => setOperationReason(event.target.value)}
          maxLength={500}
          placeholder="Ex.: atualização aprovada da presença local da marca"
        />
        <p className="text-xs text-muted-foreground">
          A configuração e a intenção de publicação são auditadas. A entrega externa depende do provedor.
        </p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr,0.95fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <MapPinned className="h-4 w-4" />
              Conexão principal
            </CardTitle>
            <CardDescription>Modelo pronto para múltiplas localizações no futuro, com uma localização principal no MVP.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">status: {connectionQuery.data?.status ?? 'disconnected'}</Badge>
              {connectionQuery.data?.locationName ? <Badge variant="secondary">{connectionQuery.data.locationName}</Badge> : null}
            </div>
            {draft ? (
              <>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Business name</Label>
                    <Input value={draft.businessName} onChange={(event) => setDraft({ ...draft, businessName: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Account name</Label>
                    <Input value={draft.accountName ?? ''} onChange={(event) => setDraft({ ...draft, accountName: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Location ID</Label>
                    <Input value={draft.locationId ?? ''} onChange={(event) => setDraft({ ...draft, locationId: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Location name</Label>
                    <Input value={draft.locationName ?? ''} onChange={(event) => setDraft({ ...draft, locationName: event.target.value })} />
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <Label>Profile URL</Label>
                    <Input value={draft.profileUrl ?? ''} onChange={(event) => setDraft({ ...draft, profileUrl: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Client ID</Label>
                    <Input value={draft.clientId ?? ''} onChange={(event) => setDraft({ ...draft, clientId: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Client secret</Label>
                    <Input value={draft.clientSecret ?? ''} onChange={(event) => setDraft({ ...draft, clientSecret: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Refresh token</Label>
                    <Input value={draft.refreshToken ?? ''} onChange={(event) => setDraft({ ...draft, refreshToken: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Access token</Label>
                    <Input value={draft.accessToken ?? ''} onChange={(event) => setDraft({ ...draft, accessToken: event.target.value })} />
                  </div>
                </div>

                <div className="flex items-center justify-between rounded-2xl border px-4 py-3">
                  <div>
                    <p className="text-sm font-medium">Publicar no Google por padrão</p>
                    <p className="text-xs text-muted-foreground">Atalho para replicar posts/editoriais nessa superfície.</p>
                  </div>
                  <Switch
                    checked={Boolean(draft.settings?.publishToGoogleByDefault)}
                    onCheckedChange={(checked) =>
                      setDraft({
                        ...draft,
                        settings: { ...(draft.settings ?? {}), publishToGoogleByDefault: checked },
                      })
                    }
                  />
                </div>

                <Button onClick={() => saveMutation.mutate({ payload: draft, reason: normalizedReason })} disabled={!reasonValid || saveMutation.isPending}>
                  Salvar conexão
                </Button>
              </>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <UploadCloud className="h-4 w-4" />
              Fila de publicação
            </CardTitle>
            <CardDescription>Atualizações, fotos, vídeos e posts editoriais, com suporte a agendamento.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4">
              <div className="space-y-1.5">
                <Label>Tipo de publicação</Label>
                <Select value={jobDraft.targetType} onValueChange={(value) => setJobDraft({ ...jobDraft, targetType: value })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="business_post">Post/update</SelectItem>
                    <SelectItem value="photo">Foto</SelectItem>
                    <SelectItem value="video">Vídeo</SelectItem>
                    <SelectItem value="profile_update">Atualização de perfil</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Título</Label>
                <Input value={jobDraft.title} onChange={(event) => setJobDraft({ ...jobDraft, title: event.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label>Resumo / payload</Label>
                <Textarea
                  rows={4}
                  value={String(jobDraft.payload.summary ?? '')}
                  onChange={(event) => setJobDraft({ ...jobDraft, payload: { ...jobDraft.payload, summary: event.target.value } })}
                />
              </div>
              <div className="space-y-1.5">
                <Label>CTA URL</Label>
                <Input value={String(jobDraft.payload.ctaUrl ?? '')} onChange={(event) => setJobDraft({ ...jobDraft, payload: { ...jobDraft.payload, ctaUrl: event.target.value } })} />
              </div>
              <div className="space-y-1.5">
                <Label>Agendar para</Label>
                <Input
                  type="datetime-local"
                  value={jobDraft.scheduledFor ? jobDraft.scheduledFor.slice(0, 16) : ''}
                  onChange={(event) => setJobDraft({ ...jobDraft, scheduledFor: event.target.value ? new Date(event.target.value).toISOString() : null })}
                />
              </div>
              <Button onClick={() => queueMutation.mutate({ payload: jobDraft, reason: normalizedReason })} disabled={!jobDraft.title.trim() || !reasonValid || queueMutation.isPending}>
                <Send className="mr-2 h-4 w-4" />
                Enfileirar job
              </Button>
            </div>

            <div className="space-y-3">
              {(jobsQuery.data?.items ?? []).map((job) => (
                <div key={job.id} className="rounded-2xl border p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{job.title}</p>
                      <p className="mt-1 truncate text-xs text-muted-foreground">{job.targetType} · {new Date(job.createdAt).toLocaleString('pt-BR')}</p>
                    </div>
                    <Badge className="shrink-0" variant={job.status === 'published' ? 'secondary' : job.status === 'failed' ? 'destructive' : 'outline'}>
                      {job.status}
                    </Badge>
                  </div>
                  {job.errorMessage ? <p className="mt-2 text-xs text-destructive">{job.errorMessage}</p> : null}
                </div>
              ))}
              {jobsQuery.data ? (
                <div className="flex flex-col gap-2 border-t pt-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-xs text-muted-foreground">
                    {jobsQuery.data.total
                      ? `${jobPage * jobPageSize + 1}–${Math.min((jobPage + 1) * jobPageSize, jobsQuery.data.total)} de ${jobsQuery.data.total}`
                      : 'Nenhum job encontrado'}
                  </p>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" size="sm" onClick={() => setJobPage((current) => Math.max(0, current - 1))} disabled={jobPage === 0 || jobsQuery.isFetching}>
                      Anterior
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => setJobPage((current) => current + 1)} disabled={!jobsQuery.data.hasMore || jobsQuery.isFetching}>
                      Próxima
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
