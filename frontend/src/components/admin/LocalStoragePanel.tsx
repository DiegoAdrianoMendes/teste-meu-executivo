import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArchiveRestore,
  CheckCircle2,
  Clock3,
  Download,
  FileArchive,
  FileClock,
  FilePlus2,
  FileText,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Switch } from '@/components/ui/switch';
import { useToast } from '@/hooks/use-toast';
import {
  deleteLocalStorageObject,
  downloadLocalStorageObject,
  exportLocalStorageObject,
  extendLocalStorageRetention,
  fetchLocalStorageObjects,
  fetchLocalStorageVersions,
  restoreLocalStorageObject,
  saveBlob,
  uploadLocalStorageObject,
  type LocalStorageObject,
} from '@/services/local-storage';

const stateLabels: Record<string, string> = {
  pending_upload: 'Aguardando upload',
  quarantined: 'Em quarentena',
  available: 'Disponível',
  rejected: 'Rejeitado',
  deleted: 'Excluído',
};

const avLabels: Record<string, string> = {
  pending: 'Análise pendente',
  clean: 'Arquivo limpo',
  infected: 'Ameaça detectada',
  error: 'Falha na análise',
};

function formatBytes(value?: number | null): string {
  const bytes = value ?? 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(2)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function formatDate(value?: string | null): string {
  if (!value) return 'Sem retenção definida';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

function mutationError(error: unknown): string {
  return error instanceof Error ? error.message : 'A operação local não foi concluída.';
}

export function LocalStoragePanel({ readOnly = false }: { readOnly?: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const versionInput = useRef<HTMLInputElement>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [selectedObject, setSelectedObject] = useState<LocalStorageObject | null>(null);
  const [retentionUntil, setRetentionUntil] = useState('');

  const objectsQuery = useQuery({
    queryKey: ['local-storage-objects', includeDeleted],
    queryFn: () => fetchLocalStorageObjects(includeDeleted),
  });
  const versionsQuery = useQuery({
    queryKey: ['local-storage-versions', selectedObject?.objectId],
    queryFn: () => fetchLocalStorageVersions(selectedObject!.objectId),
    enabled: Boolean(selectedObject),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['local-storage-objects'] }),
      queryClient.invalidateQueries({ queryKey: ['local-storage-versions'] }),
      queryClient.invalidateQueries({ queryKey: ['workspace-plan-summary'] }),
    ]);
  };

  const uploadMutation = useMutation({
    mutationFn: ({ file, objectId }: { file: File; objectId?: string }) => uploadLocalStorageObject(file, {
      objectId,
      retentionUntil: retentionUntil ? new Date(retentionUntil).toISOString() : null,
    }),
    onSuccess: async (object) => {
      setSelectedObject(object);
      await refresh();
      toast({ title: `Arquivo ${object.originalFilename} armazenado e verificado.` });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Upload não concluído', description: mutationError(error) }),
  });
  const deleteMutation = useMutation({
    mutationFn: (objectId: string) => deleteLocalStorageObject(objectId),
    onSuccess: async () => { await refresh(); toast({ title: 'Objeto excluído logicamente.' }); },
    onError: (error) => toast({ variant: 'destructive', title: 'Exclusão não concluída', description: mutationError(error) }),
  });
  const restoreMutation = useMutation({
    mutationFn: (objectId: string) => restoreLocalStorageObject(objectId),
    onSuccess: async (object) => { setSelectedObject(object); await refresh(); toast({ title: 'Objeto restaurado.' }); },
    onError: (error) => toast({ variant: 'destructive', title: 'Restauração não concluída', description: mutationError(error) }),
  });
  const retentionMutation = useMutation({
    mutationFn: ({ objectId, value }: { objectId: string; value: string }) => (
      extendLocalStorageRetention(objectId, new Date(value).toISOString())
    ),
    onSuccess: async (object) => { setSelectedObject(object); await refresh(); toast({ title: 'Retenção estendida.' }); },
    onError: (error) => toast({ variant: 'destructive', title: 'Retenção não atualizada', description: mutationError(error) }),
  });
  const downloadMutation = useMutation({
    mutationFn: async ({ object, version }: { object: LocalStorageObject; version?: number }) => ({
      object,
      blob: await downloadLocalStorageObject(object.objectId, version),
    }),
    onSuccess: ({ object, blob }) => saveBlob(blob, object.originalFilename),
    onError: (error) => toast({ variant: 'destructive', title: 'Download não concluído', description: mutationError(error) }),
  });
  const exportMutation = useMutation({
    mutationFn: async (object: LocalStorageObject) => ({
      object,
      blob: await exportLocalStorageObject(object.objectId),
    }),
    onSuccess: ({ object, blob }) => saveBlob(blob, `${object.originalFilename}-versoes.zip`),
    onError: (error) => toast({ variant: 'destructive', title: 'Exportação não concluída', description: mutationError(error) }),
  });

  const objects = objectsQuery.data ?? [];
  const totalBytes = objects.reduce((total, object) => total + (object.byteSize ?? 0), 0);
  const quarantined = objects.filter((object) => object.state === 'quarantined' || object.state === 'rejected').length;
  const retained = objects.filter((object) => object.retentionUntil).length;
  const busy = uploadMutation.isPending || deleteMutation.isPending || restoreMutation.isPending
    || retentionMutation.isPending || downloadMutation.isPending || exportMutation.isPending;

  const handleFile = (file: File | undefined, objectId?: string) => {
    if (file) uploadMutation.mutate({ file, objectId });
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="h-4 w-4 text-primary" />
              Arquivos do workspace
            </CardTitle>
            <CardDescription>Objetos locais versionados, verificados e isolados por escritório.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">S3-compatible local</Badge>
            <Badge variant={quarantined ? 'destructive' : 'secondary'}>{quarantined} em quarentena</Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 md:grid-cols-4">
          {[
            ['Objetos', String(objects.length), FileText],
            ['Armazenado', formatBytes(totalBytes), FileArchive],
            ['Com retenção', String(retained), Clock3],
            ['Quarentena', String(quarantined), ShieldAlert],
          ].map(([label, value, Icon]) => (
            <div key={String(label)} className="rounded-md border p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">{String(label)}</p>
                <Icon className="h-4 w-4 text-muted-foreground" />
              </div>
              <p className="mt-1 text-2xl font-bold tabular-nums">{String(value)}</p>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-3 border-y py-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="grid gap-3 sm:grid-cols-[220px_auto] sm:items-end">
            <div className="space-y-1.5">
              <Label htmlFor="storage-retention">Retenção inicial</Label>
              <Input
                id="storage-retention"
                type="datetime-local"
                value={retentionUntil}
                onChange={(event) => setRetentionUntil(event.target.value)}
                readOnly={readOnly}
              />
            </div>
            <div className="flex items-center gap-2 pb-2">
              <Switch id="storage-deleted" checked={includeDeleted} onCheckedChange={setIncludeDeleted} />
              <Label htmlFor="storage-deleted" className="text-xs">Mostrar excluídos</Label>
            </div>
          </div>
          <input
            ref={fileInput}
            type="file"
            className="sr-only"
            aria-label="Selecionar arquivo para upload"
            onChange={(event) => { handleFile(event.target.files?.[0]); event.currentTarget.value = ''; }}
          />
          <Button type="button" disabled={readOnly || busy} onClick={() => fileInput.current?.click()}>
            {uploadMutation.isPending
              ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              : <UploadCloud className="mr-1.5 h-4 w-4" />}
            Enviar arquivo
          </Button>
        </div>

        {objectsQuery.isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando objetos...
          </div>
        )}
        {!objectsQuery.isLoading && objects.length === 0 && (
          <div className="rounded-md border border-dashed p-8 text-center">
            <FilePlus2 className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium">Nenhum arquivo neste workspace</p>
          </div>
        )}

        <div className="divide-y rounded-md border">
          {objects.map((object) => (
            <div key={object.id} className="space-y-3 p-3">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <button
                  type="button"
                  className="min-w-0 text-left"
                  onClick={() => setSelectedObject((current) => current?.objectId === object.objectId ? null : object)}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-semibold">{object.originalFilename}</span>
                    <Badge variant={object.state === 'available' ? 'secondary' : object.state === 'deleted' ? 'outline' : 'destructive'}>
                      {stateLabels[object.state] ?? object.state}
                    </Badge>
                    <Badge variant="outline">v{object.version}</Badge>
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {formatBytes(object.byteSize)} · {avLabels[object.avStatus] ?? object.avStatus} · {formatDate(object.retentionUntil)}
                  </p>
                  {object.checksumSha256 && (
                    <code className="mt-1 block truncate text-2xs text-muted-foreground">sha256:{object.checksumSha256}</code>
                  )}
                </button>
                <div className="flex flex-wrap gap-2">
                  {object.state === 'available' && (
                    <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => downloadMutation.mutate({ object })}>
                      <Download className="mr-1.5 h-3.5 w-3.5" />Baixar
                    </Button>
                  )}
                  <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => exportMutation.mutate(object)}>
                    <FileArchive className="mr-1.5 h-3.5 w-3.5" />Exportar
                  </Button>
                  {object.state === 'deleted' ? (
                    <Button type="button" size="sm" variant="outline" disabled={readOnly || busy} onClick={() => restoreMutation.mutate(object.objectId)}>
                      <ArchiveRestore className="mr-1.5 h-3.5 w-3.5" />Restaurar
                    </Button>
                  ) : (
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button type="button" size="icon" variant="outline" disabled={readOnly || busy} title="Excluir objeto">
                          <Trash2 className="h-3.5 w-3.5" /><span className="sr-only">Excluir objeto</span>
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Excluir {object.originalFilename}?</AlertDialogTitle>
                          <AlertDialogDescription>
                            A exclusão será lógica e reversível, respeitando a retenção configurada.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Voltar</AlertDialogCancel>
                          <AlertDialogAction onClick={() => deleteMutation.mutate(object.objectId)}>Excluir</AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  )}
                </div>
              </div>

              {selectedObject?.objectId === object.objectId && (
                <div className="space-y-3 border-t pt-3">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <div className="space-y-1.5">
                      <Label htmlFor={`retention-${object.objectId}`}>Estender retenção</Label>
                      <Input
                        id={`retention-${object.objectId}`}
                        className="w-[220px]"
                        type="datetime-local"
                        value={retentionUntil}
                        onChange={(event) => setRetentionUntil(event.target.value)}
                        readOnly={readOnly}
                      />
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={readOnly || busy || !retentionUntil}
                        onClick={() => retentionMutation.mutate({ objectId: object.objectId, value: retentionUntil })}
                      >
                        <FileClock className="mr-1.5 h-3.5 w-3.5" />Atualizar retenção
                      </Button>
                      <input
                        ref={versionInput}
                        type="file"
                        className="sr-only"
                        aria-label="Selecionar nova versão"
                        onChange={(event) => { handleFile(event.target.files?.[0], object.objectId); event.currentTarget.value = ''; }}
                      />
                      <Button type="button" size="sm" disabled={readOnly || busy} onClick={() => versionInput.current?.click()}>
                        <FilePlus2 className="mr-1.5 h-3.5 w-3.5" />Nova versão
                      </Button>
                    </div>
                  </div>

                  {versionsQuery.isLoading && <Progress value={40} className="h-1" />}
                  <div className="grid gap-2 md:grid-cols-2">
                    {(versionsQuery.data ?? []).map((version) => (
                      <div key={version.id} className="flex items-center justify-between gap-3 rounded-md border p-3">
                        <div className="min-w-0">
                          <p className="flex items-center gap-1.5 text-xs font-medium">
                            {version.avStatus === 'clean' ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> : <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />}
                            Versão {version.version}
                          </p>
                          <p className="mt-1 truncate text-xs text-muted-foreground tabular-nums">{formatDate(version.uploadedAt ?? version.createdAt)} · {formatBytes(version.byteSize)}</p>
                        </div>
                        {version.state === 'available' && (
                          <Button type="button" size="icon" variant="ghost" disabled={busy} onClick={() => downloadMutation.mutate({ object: version, version: version.version })} title={`Baixar versão ${version.version}`}>
                            <Download className="h-3.5 w-3.5" /><span className="sr-only">Baixar versão {version.version}</span>
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
