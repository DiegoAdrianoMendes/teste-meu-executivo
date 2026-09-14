import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, History, RotateCcw, Save } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { canPlatform } from '@/lib/platform-access';
import {
  fetchPlatformConfiguration,
  fetchPlatformConfigurationVersionPage,
  rollbackPlatformConfiguration,
  savePlatformConfiguration,
} from '@/services/platform';

const DEFAULT_NAMESPACES = ['brand.identity', 'workspace.experience'];
const VERSION_PAGE_SIZE = 10;

function prettyJson(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

export default function BlackboxGlobalSettings() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { effectiveUser } = useBlackboxManifest();
  const canManage = canPlatform(effectiveUser, 'settings', 'manage');
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const requestedNamespace = searchParams.get('namespace')?.trim() ?? '';
  const initialNamespace = requestedNamespace.length > 0 && requestedNamespace.length <= 160
    ? requestedNamespace
    : DEFAULT_NAMESPACES[0];
  const [namespace, setNamespace] = useState(initialNamespace);
  const [namespaceDraft, setNamespaceDraft] = useState(namespace);
  const [editor, setEditor] = useState('{}');
  const [schemaVersion, setSchemaVersion] = useState('1.0');
  const [reason, setReason] = useState('');
  const [parseError, setParseError] = useState<string | null>(null);
  const [restoreVersion, setRestoreVersion] = useState<number | null>(null);
  const [versionPage, setVersionPage] = useState(0);

  const documentQuery = useQuery({
    queryKey: ['platform-configuration', namespace],
    queryFn: () => fetchPlatformConfiguration(namespace),
    retry: false,
  });
  const versionsQuery = useQuery({
    queryKey: ['platform-configuration-versions', namespace, versionPage],
    queryFn: () => fetchPlatformConfigurationVersionPage(namespace, {
      limit: VERSION_PAGE_SIZE,
      offset: versionPage * VERSION_PAGE_SIZE,
    }),
    enabled: documentQuery.isSuccess,
    retry: false,
  });

  useEffect(() => {
    if (!documentQuery.data) return;
    setEditor(prettyJson(documentQuery.data.payload));
    setSchemaVersion(documentQuery.data.schemaVersion);
    setReason('');
    setParseError(null);
  }, [documentQuery.data]);

  useEffect(() => {
    if (
      requestedNamespace.length === 0
      || requestedNamespace.length > 160
      || requestedNamespace === namespace
    ) return;
    setNamespace(requestedNamespace);
    setNamespaceDraft(requestedNamespace);
    setVersionPage(0);
  }, [namespace, requestedNamespace]);

  const currentVersion = documentQuery.data?.currentVersion ?? null;
  const versions = versionsQuery.data?.items ?? [];

  const saveMutation = useMutation({
    mutationFn: async () => {
      let payload: unknown;
      try {
        payload = JSON.parse(editor);
      } catch {
        setParseError('O conteúdo não é um JSON válido.');
        throw new Error('Corrija o JSON antes de publicar.');
      }
      if (!payload || Array.isArray(payload) || typeof payload !== 'object') {
        setParseError('A raiz do documento deve ser um objeto JSON.');
        throw new Error('A raiz do documento deve ser um objeto JSON.');
      }
      setParseError(null);
      return savePlatformConfiguration(namespace, payload as Record<string, unknown>, {
        schemaVersion,
        reason: reason.trim() || null,
        etag: documentQuery.data?.etag ?? null,
      });
    },
    onSuccess: async () => {
      setRestoreVersion(null);
      setVersionPage(0);
      await queryClient.invalidateQueries({ queryKey: ['platform-configuration', namespace] });
      await queryClient.invalidateQueries({ queryKey: ['platform-configuration-versions', namespace] });
      toast({ title: 'Configuração global publicada', description: 'Uma nova versão auditável foi criada.' });
    },
    onError: (error: Error) => toast({
      variant: 'destructive',
      title: 'Não foi possível publicar',
      description: error.message,
    }),
  });

  const rollbackMutation = useMutation({
    mutationFn: (version: number) => rollbackPlatformConfiguration(
      namespace,
      version,
      documentQuery.data?.etag ?? '',
    ),
    onSuccess: async () => {
      setVersionPage(0);
      await queryClient.invalidateQueries({ queryKey: ['platform-configuration', namespace] });
      await queryClient.invalidateQueries({ queryKey: ['platform-configuration-versions', namespace] });
      toast({ title: 'Versão restaurada', description: 'A restauração foi registrada como uma nova revisão.' });
    },
    onError: (error: Error) => toast({
      variant: 'destructive',
      title: 'Não foi possível restaurar',
      description: error.message,
    }),
  });

  const openNamespace = () => {
    const next = namespaceDraft.trim();
    if (next && next.length <= 160) {
      setVersionPage(0);
      setNamespace(next);
      const updated = new URLSearchParams(searchParams);
      updated.set('namespace', next);
      setSearchParams(updated, { replace: true });
    }
  };

  return (
    <div className="mx-auto grid w-full max-w-[1500px] gap-6 p-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Configurações globais</CardTitle>
              <CardDescription>
                Documento versionado com concorrência otimista, autoria e restauração.
              </CardDescription>
            </div>
            <Badge variant={documentQuery.data ? 'default' : 'secondary'}>
              {currentVersion ? `Versão ${currentVersion}` : 'Não carregado'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
            <div className="space-y-2">
              <Label htmlFor="global-namespace">Namespace</Label>
              <Input
                id="global-namespace"
                list="global-namespace-options"
                value={namespaceDraft}
                onChange={(event) => setNamespaceDraft(event.target.value)}
              />
              <datalist id="global-namespace-options">
                {DEFAULT_NAMESPACES.map((item) => <option key={item} value={item} />)}
              </datalist>
            </div>
            <Button className="self-end" variant="outline" onClick={openNamespace}>Abrir</Button>
          </div>

          {documentQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando documento…</p> : null}
          {documentQuery.isError ? (
            <Alert>
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Documento indisponível ou ainda não criado</AlertTitle>
              <AlertDescription>
                Confira o namespace. Usuários com permissão de gestão podem criar a primeira versão abaixo.
              </AlertDescription>
            </Alert>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="global-schema-version">Versão do schema</Label>
              <Input
                id="global-schema-version"
                value={schemaVersion}
                onChange={(event) => setSchemaVersion(event.target.value)}
                disabled={!canManage}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="global-reason">Motivo da alteração</Label>
              <Input
                id="global-reason"
                value={reason}
                maxLength={240}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Contexto para a auditoria"
                disabled={!canManage}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="global-payload">Documento JSON</Label>
            <Textarea
              id="global-payload"
              className="min-h-[480px] font-mono text-xs"
              value={editor}
              onChange={(event) => setEditor(event.target.value)}
              aria-invalid={Boolean(parseError)}
              disabled={!canManage}
            />
            {parseError ? <p className="text-sm text-destructive" role="alert">{parseError}</p> : null}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {documentQuery.data ? `ETag ${documentQuery.data.etag}` : 'A primeira gravação não exige ETag.'}
            </p>
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={!canManage || saveMutation.isPending || !schemaVersion.trim()}
            >
              <Save className="mr-2 h-4 w-4" />
              {saveMutation.isPending ? 'Publicando…' : 'Publicar nova versão'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><History className="h-4 w-4" /> Histórico</CardTitle>
          <CardDescription>Restaurar cria nova versão; não apaga o histórico.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {versionsQuery.isPending && documentQuery.isSuccess ? <p className="text-sm text-muted-foreground">Carregando histórico…</p> : null}
          {versionsQuery.isError ? <p className="text-sm text-destructive">Histórico indisponível.</p> : null}
          {versionsQuery.isSuccess && versions.length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma versão registrada.</p> : null}
          {versions.map((item) => (
            <div key={item.id} className="rounded-lg border p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">Versão {item.version}</p>
                  <p className="text-xs text-muted-foreground">
                    {new Date(item.createdAt).toLocaleString('pt-BR')} · schema {item.schemaVersion}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!canManage || item.version === currentVersion || rollbackMutation.isPending}
                  onClick={() => setRestoreVersion(item.version)}
                  aria-label={`Restaurar versão ${item.version}`}
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                </Button>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">{item.reason || 'Sem motivo informado.'}</p>
            </div>
          ))}
          {versionsQuery.isSuccess ? (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
              <p className="text-xs text-muted-foreground">
                {versionsQuery.data.total === 0
                  ? '0 versões'
                  : `${versionsQuery.data.offset + 1}–${versionsQuery.data.offset + versions.length} de ${versionsQuery.data.total}`}
              </p>
              <div className="flex gap-2">
                <Button type="button" size="sm" variant="outline" disabled={versionPage === 0 || versionsQuery.isFetching} onClick={() => setVersionPage((page) => Math.max(0, page - 1))}>Anterior</Button>
                <Button type="button" size="sm" variant="outline" disabled={!versionsQuery.data.hasMore || versionsQuery.isFetching} onClick={() => setVersionPage((page) => page + 1)}>Próxima</Button>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
      <AlertDialog open={restoreVersion !== null} onOpenChange={(open) => { if (!open) setRestoreVersion(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restaurar a versão {restoreVersion}?</AlertDialogTitle>
            <AlertDialogDescription>
              O conteúdo selecionado será publicado como uma nova revisão. O histórico existente será preservado.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => { if (restoreVersion !== null) rollbackMutation.mutate(restoreVersion); }}
              disabled={rollbackMutation.isPending}
            >
              Confirmar restauração
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
