import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Braces,
  CheckCircle2,
  History,
  Loader2,
  RotateCcw,
  Save,
  ShieldCheck,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  fetchConfigurationDocument,
  fetchConfigurationVersions,
  fetchEffectiveConfiguration,
  rollbackConfigurationDocument,
  saveConfigurationDocument,
  type ConfigurationScope,
} from '@/services/configurations';
import { ApiError } from '@/services/http';
import type { AdminRole, AdminUser } from '@/types';

const namespaceOptions = [
  { value: 'brand.identity', label: 'Identidade da marca' },
  { value: 'sales.playbook', label: 'Playbook comercial' },
  { value: 'automation.policy', label: 'Política de automação' },
  { value: 'proposals.defaults', label: 'Padrões de proposta' },
  { value: 'agenda.policy', label: 'Política de agenda' },
  { value: 'channels.policy', label: 'Política de canais' },
  { value: 'notifications.delivery', label: 'Entrega de notificações' },
] as const;

function prettyJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function parseObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('A camada precisa ser um objeto JSON.');
  }
  return parsed as Record<string, unknown>;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

interface Props {
  roles: AdminRole[];
  users: AdminUser[];
  readOnly?: boolean;
}

export function ConfigurationGovernancePanel({ roles, users, readOnly = false }: Props) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [namespace, setNamespace] = useState('brand.identity');
  const [customNamespace, setCustomNamespace] = useState('');
  const [scope, setScope] = useState<ConfigurationScope>('workspace');
  const [roleKey, setRoleKey] = useState(roles[0]?.id ?? '');
  const [userId, setUserId] = useState(users[0]?.id ?? '');
  const [draft, setDraft] = useState('{}');
  const [reason, setReason] = useState('');
  const [parseError, setParseError] = useState<string | null>(null);

  useEffect(() => {
    if (!roleKey && roles[0]?.id) setRoleKey(roles[0].id);
  }, [roleKey, roles]);

  useEffect(() => {
    if (!userId && users[0]?.id) setUserId(users[0].id);
  }, [userId, users]);

  const selectedNamespace = customNamespace.trim() || namespace;
  const targetReady = Boolean(
    selectedNamespace
      && (scope !== 'role' || roleKey)
      && (scope !== 'user' || userId),
  );
  const target = useMemo(() => ({
    namespace: selectedNamespace,
    scope,
    roleKey: scope === 'role' ? roleKey : null,
    userId: scope === 'user' ? userId : null,
  }), [roleKey, scope, selectedNamespace, userId]);
  const queryKey = ['configuration-document', target] as const;

  const effectiveQuery = useQuery({
    queryKey: ['configuration-effective', selectedNamespace],
    queryFn: () => fetchEffectiveConfiguration(selectedNamespace),
    enabled: Boolean(selectedNamespace),
  });

  const documentQuery = useQuery({
    queryKey,
    queryFn: async () => {
      try {
        return await fetchConfigurationDocument(target);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: targetReady,
  });

  const versionsQuery = useQuery({
    queryKey: ['configuration-versions', target],
    queryFn: () => fetchConfigurationVersions(target),
    enabled: targetReady && Boolean(documentQuery.data),
  });

  useEffect(() => {
    if (documentQuery.isSuccess) {
      setDraft(prettyJson(documentQuery.data?.payload ?? {}));
      setParseError(null);
    }
  }, [documentQuery.data, documentQuery.isSuccess]);

  const invalidateTarget = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['configuration-document'] }),
      queryClient.invalidateQueries({ queryKey: ['configuration-versions'] }),
      queryClient.invalidateQueries({ queryKey: ['configuration-effective', selectedNamespace] }),
    ]);
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = parseObject(draft);
      return saveConfigurationDocument(target, payload, {
        etag: documentQuery.data?.etag,
        reason: reason.trim() || 'Atualização pelo centro de configurações',
        schemaVersion: documentQuery.data?.schemaVersion ?? '1.0',
      });
    },
    onSuccess: async () => {
      setReason('');
      setParseError(null);
      await invalidateTarget();
      toast({ title: 'Nova versão publicada.' });
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Não foi possível publicar a versão.';
      setParseError(message);
      toast({ variant: 'destructive', title: 'Configuração não salva', description: message });
    },
  });

  const rollbackMutation = useMutation({
    mutationFn: (version: number) => {
      if (!documentQuery.data) throw new Error('Documento de configuração não encontrado.');
      return rollbackConfigurationDocument(target, version, documentQuery.data.etag);
    },
    onSuccess: async () => {
      await invalidateTarget();
      toast({ title: 'Versão restaurada como uma nova revisão.' });
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Não foi possível restaurar a versão.';
      toast({ variant: 'destructive', title: 'Rollback não concluído', description: message });
    },
  });

  const validateDraft = () => {
    try {
      parseObject(draft);
      setParseError(null);
      toast({ title: 'JSON válido.' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'JSON inválido.';
      setParseError(message);
    }
  };

  return (
    <Card>
      <CardHeader className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="h-4 w-4 text-primary" />
              Governança de configurações
            </CardTitle>
            <CardDescription>
              Camadas versionadas com herança, auditoria e restauração.
            </CardDescription>
          </div>
          <Badge variant="outline" className="font-mono text-xs tabular-nums">
            {documentQuery.data ? `v${documentQuery.data.currentVersion}` : 'nova camada'}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 md:grid-cols-3">
          <div className="space-y-1.5">
            <Label>Configuração</Label>
            <Select value={namespace} onValueChange={(value) => { setNamespace(value); setCustomNamespace(''); }}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {namespaceOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Escopo</Label>
            <Select value={scope} onValueChange={(value) => setScope(value as ConfigurationScope)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="workspace">Escritório</SelectItem>
                <SelectItem value="role">Papel</SelectItem>
                <SelectItem value="user">Usuário</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Namespace técnico</Label>
            <Input
              value={customNamespace}
              onChange={(event) => setCustomNamespace(event.target.value)}
              placeholder={namespace}
              spellCheck={false}
            />
          </div>
        </div>

        {scope === 'role' && (
          <div className="max-w-sm space-y-1.5">
            <Label>Papel</Label>
            <Select value={roleKey} onValueChange={setRoleKey}>
              <SelectTrigger><SelectValue placeholder="Selecione o papel" /></SelectTrigger>
              <SelectContent>
                {roles.map((role) => <SelectItem key={role.id} value={role.id}>{role.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        )}

        {scope === 'user' && (
          <div className="max-w-sm space-y-1.5">
            <Label>Usuário</Label>
            <Select value={userId} onValueChange={setUserId}>
              <SelectTrigger><SelectValue placeholder="Selecione o usuário" /></SelectTrigger>
              <SelectContent>
                {users.map((user) => (
                  <SelectItem key={user.id} value={user.id}>{user.name} · {user.email}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <Separator />

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="space-y-3" aria-label="Camada em edição">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="flex items-center gap-2 text-sm font-semibold">
                  <Braces className="h-4 w-4" /> Camada em edição
                </p>
                <p className="text-xs text-muted-foreground">Somente os valores deste escopo.</p>
              </div>
              <Button type="button" size="sm" variant="outline" onClick={validateDraft}>
                <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />Validar
              </Button>
            </div>
            <Textarea
              aria-label="JSON da camada"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="min-h-[320px] resize-y font-mono text-xs leading-5"
              spellCheck={false}
              readOnly={readOnly}
              aria-invalid={Boolean(parseError)}
            />
            {parseError && <p className="text-xs text-destructive" role="alert">{parseError}</p>}
          </section>

          <section className="space-y-3" aria-label="Configuração efetiva">
            <div>
              <p className="text-sm font-semibold">Configuração efetiva da sessão</p>
              <p className="text-xs text-muted-foreground">
                Resultado após plataforma, escritório, papel e usuário.
              </p>
            </div>
            <pre className="min-h-[320px] max-h-[420px] overflow-auto rounded-md border bg-muted/30 p-3 font-mono text-xs leading-5">
              {effectiveQuery.isLoading
                ? 'Carregando...'
                : prettyJson(effectiveQuery.data?.effective ?? {})}
            </pre>
            <div className="flex flex-wrap gap-1.5">
              {(effectiveQuery.data?.layers ?? []).map((layer) => (
                <Badge key={`${layer.scope}:${layer.scopeKey}`} variant="secondary" className="text-xs tabular-nums">
                  {layer.scope} · v{layer.version}
                </Badge>
              ))}
            </div>
          </section>
        </div>

        <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
          <div className="space-y-1.5">
            <Label>Motivo da alteração</Label>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Ex.: ajuste aprovado para o atendimento comercial"
              maxLength={240}
              readOnly={readOnly}
            />
          </div>
          <Button
            type="button"
            disabled={readOnly || !targetReady || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending
              ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              : <Save className="mr-1.5 h-4 w-4" />}
            Publicar versão
          </Button>
        </div>

        <Separator />

        <section className="space-y-3" aria-label="Histórico de versões">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4" />
            <p className="text-sm font-semibold">Histórico</p>
          </div>
          {versionsQuery.isLoading && <p className="text-xs text-muted-foreground">Carregando versões...</p>}
          {!versionsQuery.isLoading && !(versionsQuery.data?.length) && (
            <p className="text-xs text-muted-foreground">Nenhuma versão publicada neste escopo.</p>
          )}
          <div className="divide-y rounded-md border">
            {(versionsQuery.data ?? []).map((version) => (
              <div key={version.id} className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={version.version === documentQuery.data?.currentVersion ? 'default' : 'outline'}>
                      v{version.version}
                    </Badge>
                    <span className="text-xs text-muted-foreground">{formatDate(version.createdAt)}</span>
                    <code className="truncate text-2xs text-muted-foreground">{version.payloadHash.slice(0, 12)}</code>
                  </div>
                  <p className="mt-1 truncate text-xs">{version.reason || 'Sem motivo registrado'}</p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={readOnly || rollbackMutation.isPending || version.version === documentQuery.data?.currentVersion}
                  onClick={() => rollbackMutation.mutate(version.version)}
                >
                  {rollbackMutation.isPending
                    ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    : <RotateCcw className="mr-1.5 h-3.5 w-3.5" />}
                  Restaurar
                </Button>
              </div>
            ))}
          </div>
        </section>
      </CardContent>
    </Card>
  );
}
