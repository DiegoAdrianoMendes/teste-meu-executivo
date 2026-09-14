import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { History, RotateCcw, Save } from 'lucide-react';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';

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
import { Switch } from '@/components/ui/switch';
import { useToast } from '@/hooks/use-toast';
import { PlatformReleasesPanel } from '@/components/platform/PlatformReleasesPanel';
import { PlatformIncidentPanel } from '@/components/platform/PlatformIncidentPanel';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { canPlatform } from '@/lib/platform-access';
import {
  fetchPlatformAuditEvents,
  fetchPlatformFeatureFlags,
  fetchPlatformIntegrationsOverview,
  fetchPlatformOperationalLogs,
  fetchPlatformSecurityOverview,
  fetchPlatformGovernanceDocument,
  fetchPlatformGovernanceOperationalOverview,
  fetchPlatformGovernanceVersionPage,
  rollbackPlatformGovernanceDocument,
  revokePlatformSecuritySession,
  savePlatformGovernanceDocument,
  updatePlatformFeatureFlag,
  type PlatformGovernanceModule,
} from '@/services/platform';
import {
  createPlatformRestoration,
  createRestorationPreview,
  fetchPlatformRestoration,
  type PlatformRestorationPreview,
} from '@/services/platform-restorations';

const MODULES: Record<PlatformGovernanceModule, { title: string; description: string }> = {
  integrations: { title: 'Integrações', description: 'Política global, responsáveis, gates e condições operacionais das integrações.' },
  logs: { title: 'Logs', description: 'Retenção, mascaramento, exportação e níveis de observabilidade.' },
  audit: { title: 'Auditoria', description: 'Cobertura obrigatória, retenção e revisão das trilhas administrativas.' },
  security: { title: 'Segurança', description: 'Controles de identidade, sessão, incidentes e resposta operacional.' },
  compliance: { title: 'Compliance', description: 'Obrigações, evidências, responsáveis e ciclos de revisão.' },
  data_governance: { title: 'Governança de dados', description: 'Classificação, retenção, descarte e responsáveis pelos dados.' },
  releases: { title: 'Releases', description: 'Versão ativa, critérios de liberação, observação e rollback.' },
  feature_flags: { title: 'Feature flags', description: 'Catálogo global de flags, estado, responsável e justificativa.' },
};
const VERSION_PAGE_SIZE = 10;

function isGovernanceModule(value: string | undefined): value is PlatformGovernanceModule {
  return Boolean(value && value in MODULES);
}

function AuditEventContext({ event, canRestore, onRestore }: {
  event: import('@/services/platform').PlatformAuditEventRecord;
  canRestore: boolean;
  onRestore: () => void;
}) {
  const hasDiff = event.before != null || event.after != null;
  const fieldDiffs = event.fieldDiffs ?? [];
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap gap-1.5">
        {event.module && <Badge variant="outline">{event.module}</Badge>}
        <Badge variant="secondary">{event.action}</Badge>
        {event.workspaceId && (
          <Badge variant="outline">
            {event.workspaceLabelSnapshot ?? 'Conta vinculada'}
          </Badge>
        )}
      </div>
      {event.reason && <p><span className="font-medium">Motivo:</span> {event.reason}</p>}
      {event.correlationId && <p className="font-mono text-muted-foreground">correlationId: {event.correlationId}</p>}
      {fieldDiffs.length > 0 ? (
        <details className="rounded-md border p-2">
          <summary className="cursor-pointer font-medium">{fieldDiffs.length} campo(s) alterado(s)</summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-xs">
              <thead><tr><th className="p-2">Campo</th><th className="p-2">Antes</th><th className="p-2">Depois</th></tr></thead>
              <tbody>{fieldDiffs.map((item) => <tr key={item.path} className="border-t"><td className="p-2"><span className="font-medium">{item.label}</span><span className="block font-mono text-[10px] text-muted-foreground">{item.path}</span></td><td className="p-2"><pre className="max-w-48 whitespace-pre-wrap break-all">{JSON.stringify(item.before)}</pre></td><td className="p-2"><pre className="max-w-48 whitespace-pre-wrap break-all">{JSON.stringify(item.after)}</pre></td></tr>)}</tbody>
            </table>
          </div>
        </details>
      ) : hasDiff ? (
        <details className="rounded-md border p-2">
          <summary className="cursor-pointer font-medium">Ver alteração antes/depois</summary>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/60 p-2">{JSON.stringify(event.before, null, 2)}</pre>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/60 p-2">{JSON.stringify(event.after, null, 2)}</pre>
          </div>
        </details>
      ) : null}
      {canRestore ? <Button size="sm" variant="outline" onClick={onRestore}><RotateCcw className="mr-2 h-3.5 w-3.5" />Prévia de restauração</Button> : null}
      <details>
        <summary className="cursor-pointer text-muted-foreground">Contexto técnico completo</summary>
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-muted-foreground">{JSON.stringify(event.details, null, 2)}</pre>
      </details>
    </div>
  );
}

export default function BlackboxGovernanceCenter() {
  const { module: rawModule } = useParams();
  const [sourceSearchParams] = useSearchParams();
  const { effectiveUser } = useBlackboxManifest();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const module = isGovernanceModule(rawModule) ? rawModule : null;
  const [editor, setEditor] = useState('{}');
  const [reason, setReason] = useState('');
  const [parseError, setParseError] = useState<string | null>(null);
  const [restoreVersion, setRestoreVersion] = useState<number | null>(null);
  const [versionPage, setVersionPage] = useState(0);
  const [auditSearch, setAuditSearch] = useState('');
  const [auditOutcome, setAuditOutcome] = useState('');
  const [auditModule, setAuditModule] = useState('');
  const [auditResourceType, setAuditResourceType] = useState('');
  const [auditWorkspaceId, setAuditWorkspaceId] = useState<string | null>(null);
  const [auditRestorable, setAuditRestorable] = useState('');
  const [auditCursor, setAuditCursor] = useState<string | null>(null);
  const [auditCursorHistory, setAuditCursorHistory] = useState<string[]>([]);
  const [logSearch, setLogSearch] = useState('');
  const [logSeverity, setLogSeverity] = useState('');
  const [logCursor, setLogCursor] = useState<string | null>(null);
  const [logCursorHistory, setLogCursorHistory] = useState<string[]>([]);
  const [securityCursor, setSecurityCursor] = useState<string | null>(null);
  const [securityCursorHistory, setSecurityCursorHistory] = useState<string[]>([]);
  const [flagReason, setFlagReason] = useState('');
  const [restorationPreview, setRestorationPreview] = useState<PlatformRestorationPreview | null>(null);
  const [restorationReason, setRestorationReason] = useState('');
  const [restorationConfirmation, setRestorationConfirmation] = useState('');
  const [restorationJobId, setRestorationJobId] = useState<string | null>(null);

  const documentQuery = useQuery({
    queryKey: ['platform-governance', module],
    queryFn: () => fetchPlatformGovernanceDocument(module as PlatformGovernanceModule),
    enabled: Boolean(module),
    retry: false,
  });
  const versionsQuery = useQuery({
    queryKey: ['platform-governance', module, 'versions', versionPage],
    queryFn: () => fetchPlatformGovernanceVersionPage(module as PlatformGovernanceModule, {
      limit: VERSION_PAGE_SIZE,
      offset: versionPage * VERSION_PAGE_SIZE,
    }),
    enabled: Boolean(module),
    retry: false,
  });
  const auditParams = useMemo(() => {
    const params = new URLSearchParams({ limit: '100' });
    if (auditSearch.trim()) params.set('search', auditSearch.trim());
    if (auditOutcome) params.set('outcome', auditOutcome);
    if (auditModule.trim()) params.set('module', auditModule.trim());
    if (auditResourceType.trim()) params.set('resourceType', auditResourceType.trim());
    if (auditWorkspaceId) params.set('workspaceId', auditWorkspaceId);
    if (auditRestorable) params.set('restorable', auditRestorable);
    if (auditCursor) params.set('cursor', auditCursor);
    return params;
  }, [auditCursor, auditModule, auditOutcome, auditResourceType, auditRestorable, auditSearch, auditWorkspaceId]);
  const auditEventsQuery = useQuery({
    queryKey: ['platform-governance', 'audit-events', auditParams.toString()],
    queryFn: () => fetchPlatformAuditEvents(auditParams),
    enabled: module === 'audit',
    retry: false,
  });
  const logParams = useMemo(() => {
    const params = new URLSearchParams({ limit: '50' });
    if (logSearch.trim()) params.set('search', logSearch.trim());
    if (logSeverity) params.set('severity', logSeverity);
    if (logCursor) params.set('cursor', logCursor);
    return params;
  }, [logCursor, logSearch, logSeverity]);
  const logsQuery = useQuery({
    queryKey: ['platform-governance', 'operational-logs', logParams.toString()],
    queryFn: () => fetchPlatformOperationalLogs(logParams),
    enabled: module === 'logs',
    retry: false,
  });
  const flagsQuery = useQuery({
    queryKey: ['platform-governance', 'feature-flags', 'catalog'],
    queryFn: fetchPlatformFeatureFlags,
    enabled: module === 'feature_flags',
    retry: false,
  });
  const securityQuery = useQuery({
    queryKey: ['platform-governance', 'security', 'overview', securityCursor],
    queryFn: () => fetchPlatformSecurityOverview({ limit: 50, cursor: securityCursor }),
    enabled: module === 'security',
    retry: false,
  });
  const integrationsQuery = useQuery({
    queryKey: ['platform-governance', 'integrations', 'overview'],
    queryFn: fetchPlatformIntegrationsOverview,
    enabled: module === 'integrations',
    retry: false,
  });
  const operationalModule = module && ['compliance', 'data_governance', 'releases'].includes(module)
    ? module as 'compliance' | 'data_governance' | 'releases'
    : null;
  const operationalQuery = useQuery({
    queryKey: ['platform-governance', operationalModule, 'operational-overview'],
    queryFn: () => fetchPlatformGovernanceOperationalOverview(operationalModule as 'compliance' | 'data_governance' | 'releases'),
    enabled: Boolean(operationalModule),
    retry: false,
  });

  useEffect(() => {
    setEditor(JSON.stringify(documentQuery.data?.payload ?? {}, null, 2));
    setParseError(null);
    setVersionPage(0);
  }, [documentQuery.data, module]);

  useEffect(() => {
    setAuditCursor(null);
    setAuditCursorHistory([]);
  }, [auditModule, auditOutcome, auditResourceType, auditRestorable, auditSearch, auditWorkspaceId]);

  useEffect(() => {
    setLogCursor(null);
    setLogCursorHistory([]);
  }, [logSearch, logSeverity]);

  const canManage = module ? canPlatform(effectiveUser, module, 'manage') : false;
  const metadata = module ? MODULES[module] : null;
  const versions = versionsQuery.data?.items ?? [];
  const hasIncidentSourceContext = (
    module === 'security'
    && sourceSearchParams.get('sourceType') === 'incident'
    && Boolean(sourceSearchParams.get('sourceId'))
    && sourceSearchParams.get('tab') === 'incidents'
  );

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!module) throw new Error('Módulo inválido.');
      let payload: unknown;
      try {
        payload = JSON.parse(editor);
      } catch {
        setParseError('O controle deve ser um JSON válido.');
        throw new Error('Corrija o JSON antes de publicar.');
      }
      if (!payload || Array.isArray(payload) || typeof payload !== 'object') {
        setParseError('A raiz deve ser um objeto JSON.');
        throw new Error('A raiz deve ser um objeto JSON.');
      }
      if (reason.trim().length < 3) throw new Error('Informe o motivo da alteração.');
      setParseError(null);
      return savePlatformGovernanceDocument(module, payload as Record<string, unknown>, {
        reason: reason.trim(),
        etag: documentQuery.data?.etag ?? null,
      });
    },
    onSuccess: async () => {
      setVersionPage(0);
      await queryClient.invalidateQueries({ queryKey: ['platform-governance', module] });
      setReason('');
      toast({ title: 'Controle publicado', description: 'A nova versão foi registrada com autoria e motivo.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Publicação bloqueada', description: error.message }),
  });

  const rollbackMutation = useMutation({
    mutationFn: async (version: number) => {
      if (!module || !documentQuery.data) throw new Error('Documento atual indisponível.');
      return rollbackPlatformGovernanceDocument(module, version, documentQuery.data.etag);
    },
    onSuccess: async () => {
      setRestoreVersion(null);
      setVersionPage(0);
      await queryClient.invalidateQueries({ queryKey: ['platform-governance', module] });
      toast({ title: 'Versão restaurada', description: 'A restauração foi registrada como uma nova revisão.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Restauração bloqueada', description: error.message }),
  });

  const restorationPermission: Record<string, [string, string]> = {
    workspace: ['customers', 'manage'], staff: ['staff', 'manage'], user: ['customers', 'manage'],
    customer_membership: ['customers', 'manage'],
    plan: ['plans', 'manage'], subscription: ['billing', 'manage'],
    platform_configuration: ['settings', 'manage'],
  };
  const canRestoreEvent = (event: import('@/services/platform').PlatformAuditEventRecord) => {
    const permission = event.resourceType ? restorationPermission[event.resourceType] : undefined;
    return Boolean(
      event.restorable && permission && event.resourceId
      && canPlatform(effectiveUser, permission[0], permission[1]),
    );
  };
  const previewRestorationMutation = useMutation({
    mutationFn: createRestorationPreview,
    onSuccess: (preview) => {
      setRestorationPreview(preview);
      setRestorationReason('');
      setRestorationConfirmation('');
      setRestorationJobId(null);
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Prévia bloqueada', description: error.message }),
  });
  const restorationMutation = useMutation({
    mutationFn: () => {
      if (!restorationPreview) throw new Error('Prévia indisponível.');
      return createPlatformRestoration({
        preview: restorationPreview,
        reason: restorationReason.trim(),
        confirmation: restorationConfirmation.trim(),
        idempotencyKey: `restore:${restorationPreview.id}:${crypto.randomUUID()}`,
      });
    },
    onSuccess: (job) => setRestorationJobId(job.id),
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Restauração bloqueada', description: error.message }),
  });
  const restorationJobQuery = useQuery({
    queryKey: ['platform-restoration', restorationJobId],
    queryFn: () => fetchPlatformRestoration(restorationJobId as string),
    enabled: Boolean(restorationJobId),
    refetchInterval: (query) => query.state.data && ['queued', 'running'].includes(query.state.data.status) ? 1_500 : false,
  });

  const flagMutation = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) => {
      if (flagReason.trim().length < 3) throw new Error('Informe o motivo antes de alterar uma flag.');
      return updatePlatformFeatureFlag(key, enabled, flagReason.trim(), flagsQuery.data?.etag ?? null);
    },
    onSuccess: async () => {
      setFlagReason('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['platform-governance', 'feature-flags'] }),
        queryClient.invalidateQueries({ queryKey: ['blackbox-session-manifest'] }),
      ]);
      toast({ title: 'Feature flag atualizada', description: 'A alteração já consta no manifesto e no histórico.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Alteração bloqueada', description: error.message }),
  });
  const revokeSessionMutation = useMutation({
    mutationFn: revokePlatformSecuritySession,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['platform-governance', 'security', 'overview'] });
      toast({ title: result.revoked ? 'Sessão encerrada' : 'Sessão já estava encerrada', description: 'A decisão foi registrada na trilha administrativa.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Revogação bloqueada', description: error.message }),
  });

  if (!module || !metadata) return <Navigate to="/blackbox" replace />;

  return (
    <div className="mx-auto grid w-full max-w-[1500px] gap-6 p-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      {module === 'releases' ? <PlatformReleasesPanel canManage={canManage} /> : null}
      {operationalModule ? <Card className="xl:col-span-2"><CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>Estado operacional observado</CardTitle><CardDescription>Fatos consultados no banco e no runtime, separados da política declarada no editor abaixo.</CardDescription></div>{operationalQuery.data ? <Badge variant={operationalQuery.data.state === 'observed' || operationalQuery.data.state === 'configured' ? 'default' : 'secondary'}>{operationalQuery.data.state === 'insufficient_data' ? 'Dados insuficientes' : operationalQuery.data.state === 'not_configured' ? 'Não configurado' : 'Observado'}</Badge> : null}</div></CardHeader><CardContent className="space-y-4">{operationalQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Consultando evidências…</p> : operationalQuery.isError ? <Alert variant="destructive"><AlertTitle>Evidências indisponíveis</AlertTitle><AlertDescription>A política continua acessível, mas os fatos operacionais não puderam ser consultados.</AlertDescription></Alert> : operationalQuery.data ? <><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{operationalQuery.data.metrics.map((metric) => <div key={metric.key} className="rounded-lg border p-3"><div className="flex items-start justify-between gap-2"><p className="text-xs text-muted-foreground">{metric.label}</p><Badge variant="outline" className="text-[10px]">{metric.quality}</Badge></div><p className="mt-2 text-2xl font-semibold">{metric.value}</p></div>)}</div><div className="grid gap-2 md:grid-cols-2">{operationalQuery.data.facts.map((fact) => <div key={fact.label} className="rounded-md bg-muted/50 p-3"><p className="text-xs font-medium">{fact.label}</p><p className="mt-1 text-xs text-muted-foreground">{fact.value}</p></div>)}</div><p className="text-xs text-muted-foreground">Política: {operationalQuery.data.policy.configured ? `versão ${operationalQuery.data.policy.currentVersion}` : 'não configurada'} · leitura em {new Date(operationalQuery.data.generatedAt).toLocaleString('pt-BR')}</p></> : null}</CardContent></Card> : null}
      {module === 'integrations' ? <Card className="xl:col-span-2"><CardHeader><CardTitle>Evidências das integrações globais</CardTitle><CardDescription>“Credencial configurada” não é exibida como “conectado”; cada provedor informa o nível real de evidência disponível.</CardDescription></CardHeader><CardContent className="space-y-4">{integrationsQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Consultando integrações…</p> : integrationsQuery.isError ? <Alert variant="destructive"><AlertTitle>Estado indisponível</AlertTitle><AlertDescription>Não foi possível consultar as evidências das integrações.</AlertDescription></Alert> : integrationsQuery.data ? <><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[['Provedores', integrationsQuery.data.summary.total], ['Conectados/disponíveis', integrationsQuery.data.summary.connected], ['Somente credencial', integrationsQuery.data.summary.credentialsOnly], ['Não configurados', integrationsQuery.data.summary.notConfigured]].map(([label, value]) => <div key={String(label)} className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p></div>)}</div><div className="grid gap-3 md:grid-cols-2">{integrationsQuery.data.providers.map((provider) => <div key={provider.key} className="rounded-lg border p-4"><div className="flex items-start justify-between gap-3"><div><p className="font-medium">{provider.label}</p><p className="font-mono text-xs text-muted-foreground">{provider.key}</p></div><Badge variant={provider.state === 'connected' || provider.state === 'available' ? 'default' : provider.state === 'credentials_configured' ? 'secondary' : 'outline'}>{provider.state === 'connected' ? 'Conectado' : provider.state === 'available' ? 'Disponível' : provider.state === 'credentials_configured' ? 'Credencial presente' : 'Não configurado'}</Badge></div><p className="mt-3 text-sm text-muted-foreground">{provider.detail}</p><p className="mt-2 text-xs text-muted-foreground">Evidência: {provider.evidenceLevel}</p></div>)}</div></> : null}</CardContent></Card> : null}
      {module === 'security' ? <>{hasIncidentSourceContext ? <Alert className="xl:col-span-2"><AlertTitle>Contexto da Central de Tarefas</AlertTitle><AlertDescription>A gestão de incidentes foi aberta na fonte correta. Esta lista é paginada e ainda não localiza automaticamente um incidente fora da página atual; use a paginação sem presumir que o primeiro registro é o solicitado.</AlertDescription></Alert> : null}<Card className="xl:col-span-2"><CardHeader><CardTitle>Sessões ativas e postura de identidade</CardTitle><CardDescription>Sessões persistidas da plataforma e das contas. E-mails são mascarados para funcionários não root.</CardDescription></CardHeader><CardContent className="space-y-4">{securityQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando segurança…</p> : securityQuery.isError ? <Alert variant="destructive"><AlertTitle>Postura indisponível</AlertTitle><AlertDescription>Não foi possível consultar sessões e indicadores de identidade.</AlertDescription></Alert> : securityQuery.data ? <><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[['Sessões ativas', securityQuery.data.summary.activeUserSessions], ['Sessões administrativas', securityQuery.data.summary.activeAdministrativeSessions], ['Usuários com MFA', securityQuery.data.summary.mfaEnabledUsers], ['Eventos negados/falhos', securityQuery.data.summary.deniedOrFailedEvents]].map(([label, value]) => <div key={String(label)} className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p></div>)}</div>{securityQuery.data.sessions.length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma sessão ativa.</p> : <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[980px] text-left text-sm"><thead className="bg-muted/60 text-xs text-muted-foreground"><tr><th className="p-3">Pessoa</th><th className="p-3">Dispositivo</th><th className="p-3">Última atividade</th><th className="p-3">Expira</th><th className="p-3">Ação</th></tr></thead><tbody>{securityQuery.data.sessions.map((session) => <tr key={session.id} className="border-t"><td className="p-3"><p className="font-medium">{session.userName}</p><p className="text-xs text-muted-foreground">{session.userEmail ?? 'E-mail indisponível'}</p><Badge variant="outline" className="mt-1">{session.isPlatformStaff ? 'Equipe SaaS' : 'Conta cliente'}</Badge></td><td className="p-3"><p>{session.deviceLabel}</p><p className="text-xs text-muted-foreground">{session.browser} · {session.operatingSystem}</p></td><td className="p-3 text-xs">{new Date(session.lastSeenAt).toLocaleString('pt-BR')}</td><td className="p-3 text-xs">{new Date(session.expiresAt).toLocaleString('pt-BR')}</td><td className="p-3"><Button size="sm" variant="destructive" disabled={!canManage || revokeSessionMutation.isPending} onClick={() => revokeSessionMutation.mutate(session.id)}>Encerrar</Button></td></tr>)}</tbody></table></div>}<div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">{securityQuery.data.sessions.length} de {securityQuery.data.summary.activeUserSessions} sessão(ões) ativa(s) nesta navegação.</p><div className="flex gap-2"><Button variant="outline" size="sm" disabled={securityCursorHistory.length === 0 || securityQuery.isFetching} onClick={() => { const history = [...securityCursorHistory]; const previous = history.pop() ?? ''; setSecurityCursorHistory(history); setSecurityCursor(previous || null); }}>Anterior</Button><Button variant="outline" size="sm" disabled={!securityQuery.data.hasMore || !securityQuery.data.nextCursor || securityQuery.isFetching} onClick={() => { if (!securityQuery.data?.nextCursor) return; setSecurityCursorHistory((history) => [...history, securityCursor ?? '']); setSecurityCursor(securityQuery.data.nextCursor); }}>Próxima</Button></div></div></> : null}</CardContent></Card><div className="xl:col-span-2"><PlatformIncidentPanel /></div></> : null}
      {module === 'feature_flags' ? <Card className="xl:col-span-2"><CardHeader><CardTitle>Catálogo efetivo de feature flags</CardTitle><CardDescription>Alterações individuais entram no manifesto de sessão e criam uma nova versão auditável. O editor avançado permanece abaixo.</CardDescription></CardHeader><CardContent className="space-y-4"><div className="space-y-2"><Label htmlFor="flag-reason">Motivo da alteração</Label><Input id="flag-reason" value={flagReason} onChange={(event) => setFlagReason(event.target.value)} disabled={!canManage} maxLength={240} placeholder="Ex.: liberação gradual após validação" /></div>{flagsQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando flags…</p> : flagsQuery.isError ? <Alert variant="destructive"><AlertTitle>Catálogo indisponível</AlertTitle><AlertDescription>Não foi possível consultar as flags efetivas.</AlertDescription></Alert> : Object.keys(flagsQuery.data?.flags ?? {}).length === 0 ? <Alert><AlertTitle>Nenhuma flag cadastrada</AlertTitle><AlertDescription>Cadastre a primeira flag pelo editor avançado, usando o objeto <code>{'{ "flags": { "minha.flag": false } }'}</code>.</AlertDescription></Alert> : <div className="divide-y rounded-lg border">{Object.entries(flagsQuery.data?.flags ?? {}).sort(([left], [right]) => left.localeCompare(right)).map(([key, enabled]) => <div key={key} className="flex items-center justify-between gap-4 p-4"><div><p className="font-mono text-sm font-semibold">{key}</p><p className="text-xs text-muted-foreground">{enabled ? 'Ativa no manifesto' : 'Inativa no manifesto'}</p></div><Switch aria-label={`${enabled ? 'Desativar' : 'Ativar'} ${key}`} checked={enabled} disabled={!canManage || flagMutation.isPending || flagReason.trim().length < 3} onCheckedChange={(checked) => flagMutation.mutate({ key, enabled: checked })} /></div>)}</div>}<p className="text-xs text-muted-foreground">{flagsQuery.data ? `Versão ${flagsQuery.data.currentVersion}${flagsQuery.data.updatedAt ? ` · atualizada em ${new Date(flagsQuery.data.updatedAt).toLocaleString('pt-BR')}` : ''}` : 'Versão indisponível.'}</p></CardContent></Card> : null}
      {module === 'logs' ? <Card className="xl:col-span-2"><CardHeader><CardTitle>Logs operacionais persistidos</CardTitle><CardDescription>Falhas capturadas pela aplicação, com recorrência, endpoint, request ID e estado de tratamento.</CardDescription></CardHeader><CardContent className="space-y-4"><div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_220px]"><Input aria-label="Buscar logs operacionais" value={logSearch} onChange={(event) => setLogSearch(event.target.value)} placeholder="Código, exceção, endpoint ou request ID" /><select aria-label="Filtrar severidade dos logs" className="h-10 rounded-md border bg-background px-3 text-sm" value={logSeverity} onChange={(event) => setLogSeverity(event.target.value)}><option value="">Todas as severidades</option><option value="critical">Crítica</option><option value="high">Alta</option><option value="medium">Média</option><option value="low">Baixa</option><option value="info">Informação</option></select></div>{logsQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando logs…</p> : logsQuery.isError ? <Alert variant="destructive"><AlertTitle>Logs indisponíveis</AlertTitle><AlertDescription>Não foi possível consultar o armazenamento operacional.</AlertDescription></Alert> : logsQuery.data?.items.length === 0 ? <p className="text-sm text-muted-foreground">Nenhum log corresponde aos filtros.</p> : <div className="space-y-2">{logsQuery.data?.items.map((item) => <div key={item.id} className="rounded-lg border p-3"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-mono text-xs font-semibold">{item.errorCode} · {item.exceptionType}</p><p className="mt-1 text-sm">{item.message}</p></div><div className="flex gap-1"><Badge variant={item.severity === 'critical' || item.severity === 'high' ? 'destructive' : 'secondary'}>{item.severity}</Badge><Badge variant="outline">{item.status}</Badge></div></div><div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"><span>{item.method ?? '—'} {item.endpoint ?? 'Endpoint não informado'}</span><span>{item.occurrences} ocorrência(s)</span><span>Última: {new Date(item.lastSeenAt).toLocaleString('pt-BR')}</span>{item.requestId ? <span>Request: {item.requestId}</span> : null}</div></div>)}</div>}<div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">{logsQuery.data ? `${logsQuery.data.count} registro(s) nesta página.` : 'Contagem indisponível.'}</p><div className="flex gap-2"><Button variant="outline" size="sm" disabled={logCursorHistory.length === 0 || logsQuery.isFetching} onClick={() => { const history = [...logCursorHistory]; const previous = history.pop() ?? ''; setLogCursorHistory(history); setLogCursor(previous || null); }}>Anterior</Button><Button variant="outline" size="sm" disabled={!logsQuery.data?.hasMore || !logsQuery.data.nextCursor || logsQuery.isFetching} onClick={() => { if (!logsQuery.data?.nextCursor) return; setLogCursorHistory((history) => [...history, logCursor ?? '']); setLogCursor(logsQuery.data.nextCursor); }}>Próxima</Button></div></div></CardContent></Card> : null}
      {module === 'audit' ? <Card className="xl:col-span-2"><CardHeader><CardTitle>Eventos administrativos reais</CardTitle><CardDescription>Autoria, resultado, justificativa, correlação e diff efetivamente persistidos pelas operações do Blackbox. A política versionada permanece abaixo.</CardDescription></CardHeader><CardContent className="space-y-4"><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"><Input aria-label="Buscar eventos de auditoria" value={auditSearch} onChange={(event) => setAuditSearch(event.target.value)} placeholder="Evento, ator, ID ou correlação" /><select aria-label="Filtrar resultado da auditoria" className="h-10 rounded-md border bg-background px-3 text-sm" value={auditOutcome} onChange={(event) => setAuditOutcome(event.target.value)}><option value="">Todos os resultados</option><option value="success">Sucesso</option><option value="partial">Parcial</option><option value="denied">Negado</option><option value="failed">Falha</option></select><Input aria-label="Filtrar módulo da auditoria" value={auditModule} onChange={(event) => setAuditModule(event.target.value)} placeholder="Módulo exato, ex.: customers" /><Input aria-label="Filtrar tipo de recurso" value={auditResourceType} onChange={(event) => setAuditResourceType(event.target.value)} placeholder="Tipo, ex.: customer_membership" /><PlatformEntityCombobox entityType="accounts" purpose="audit_filter" value={auditWorkspaceId} onChange={(value) => setAuditWorkspaceId(value)} label="Filtrar conta da auditoria" placeholder="Todas as contas" /><select aria-label="Filtrar restaurabilidade" className="h-10 rounded-md border bg-background px-3 text-sm" value={auditRestorable} onChange={(event) => setAuditRestorable(event.target.value)}><option value="">Restauráveis e não restauráveis</option><option value="true">Somente restauráveis</option><option value="false">Somente não restauráveis</option></select></div>{auditEventsQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando trilha…</p> : auditEventsQuery.isError ? <Alert variant="destructive"><AlertTitle>Trilha indisponível</AlertTitle><AlertDescription>Não foi possível consultar os eventos persistidos.</AlertDescription></Alert> : auditEventsQuery.data?.items.length === 0 ? <p className="text-sm text-muted-foreground">Nenhum evento corresponde aos filtros.</p> : <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[900px] text-left text-sm"><thead className="bg-muted/60 text-xs text-muted-foreground"><tr><th className="p-3">Quando</th><th className="p-3">Evento</th><th className="p-3">Ator</th><th className="p-3">Resultado</th><th className="p-3">Contexto e diff</th></tr></thead><tbody>{auditEventsQuery.data?.items.map((event) => <tr key={event.id} className="border-t align-top"><td className="whitespace-nowrap p-3">{new Date(event.occurredAt).toLocaleString('pt-BR')}</td><td className="p-3 font-mono text-xs">{event.eventType}</td><td className="p-3"><span className="font-medium">{event.actorName}</span><div className="mt-1 flex gap-1"><Badge variant={event.isPlatformRoot ? 'default' : 'secondary'}>{event.isPlatformRoot ? 'Super Admin' : event.actorRole ?? 'Sistema'}</Badge></div></td><td className="p-3"><Badge variant={event.outcome === 'success' ? 'secondary' : 'destructive'}>{event.outcome}</Badge></td><td className="max-w-[560px] p-3"><AuditEventContext event={event} canRestore={canRestoreEvent(event)} onRestore={() => previewRestorationMutation.mutate(event.id)} /></td></tr>)}</tbody></table></div>}<div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">{auditEventsQuery.data ? `${auditEventsQuery.data.count} evento(s) nesta página.` : 'Contagem indisponível.'}</p><div className="flex gap-2"><Button variant="outline" size="sm" disabled={auditCursorHistory.length === 0 || auditEventsQuery.isFetching} onClick={() => { const history = [...auditCursorHistory]; const previous = history.pop() ?? ''; setAuditCursorHistory(history); setAuditCursor(previous || null); }}>Anterior</Button><Button variant="outline" size="sm" disabled={!auditEventsQuery.data?.hasMore || !auditEventsQuery.data.nextCursor || auditEventsQuery.isFetching} onClick={() => { if (!auditEventsQuery.data?.nextCursor) return; setAuditCursorHistory((history) => [...history, auditCursor ?? '']); setAuditCursor(auditEventsQuery.data.nextCursor); }}>Próxima</Button></div></div></CardContent></Card> : null}
      {module === 'audit' && restorationPreview ? <Card className="xl:col-span-2" role="dialog" aria-modal="true" aria-labelledby="restore-preview-title"><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle id="restore-preview-title">Prévia de restauração</CardTitle><CardDescription>{restorationPreview.resourceType} · {restorationPreview.resourceId}. A execução cria uma nova versão e nunca sobrescreve mudança posterior.</CardDescription></div><Button variant="ghost" onClick={() => setRestorationPreview(null)}>Fechar</Button></div></CardHeader><CardContent className="space-y-4">{restorationPreview.warnings.map((warning) => <Alert key={warning}><AlertTitle>Atenção</AlertTitle><AlertDescription>{warning}</AlertDescription></Alert>)}{restorationPreview.ineligibleReasons.map((reason) => <Alert key={reason} variant="destructive"><AlertTitle>Restauração indisponível</AlertTitle><AlertDescription>{reason}</AlertDescription></Alert>)}<div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[680px] text-left text-sm"><thead className="bg-muted/60"><tr><th className="p-3">Campo</th><th className="p-3">Atual</th><th className="p-3">Alvo</th></tr></thead><tbody>{restorationPreview.diff.map((item) => <tr key={item.path} className="border-t"><td className="p-3 font-mono text-xs">{item.label}</td><td className="p-3"><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(item.before)}</pre></td><td className="p-3"><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(item.after)}</pre></td></tr>)}</tbody></table></div><div className="grid gap-3 md:grid-cols-2"><div><Label htmlFor="restoration-reason">Motivo</Label><Textarea id="restoration-reason" value={restorationReason} onChange={(event) => setRestorationReason(event.target.value)} placeholder="Explique por que este estado deve ser restaurado" /></div><div><Label htmlFor="restoration-confirmation">Confirmação</Label><Input id="restoration-confirmation" value={restorationConfirmation} onChange={(event) => setRestorationConfirmation(event.target.value)} placeholder={`RESTAURAR ${restorationPreview.resourceId}`} /><p className="mt-1 text-xs text-muted-foreground">Digite exatamente: RESTAURAR {restorationPreview.resourceId}</p></div></div>{restorationJobQuery.data ? <Alert variant={restorationJobQuery.data.status === 'failed' ? 'destructive' : 'default'}><AlertTitle>Job: {restorationJobQuery.data.status}</AlertTitle><AlertDescription>{restorationJobQuery.data.errorCode ? `${restorationJobQuery.data.errorCode}: ${restorationJobQuery.data.errorDetail ?? 'falha sanitizada'}` : `correlationId ${restorationJobQuery.data.correlationId}`}</AlertDescription></Alert> : null}<div className="flex justify-end"><Button onClick={() => restorationMutation.mutate()} disabled={!restorationPreview.eligible || restorationReason.trim().length < 8 || restorationConfirmation !== `RESTAURAR ${restorationPreview.resourceId}` || restorationMutation.isPending || Boolean(restorationJobId)}><RotateCcw className="mr-2 h-4 w-4" />Criar restauração</Button></div></CardContent></Card> : null}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><CardTitle>{metadata.title}</CardTitle><CardDescription>{metadata.description}</CardDescription></div>
            <Badge variant={documentQuery.data ? 'default' : 'secondary'}>
              {documentQuery.data ? `Versão ${documentQuery.data.currentVersion}` : 'Não configurado'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {documentQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando controle…</p> : null}
          {documentQuery.isError ? (
            <Alert><AlertTitle>Controle ainda não configurado</AlertTitle><AlertDescription>Crie a primeira versão abaixo. Nenhum valor padrão fictício será presumido.</AlertDescription></Alert>
          ) : null}
          <div className="space-y-2">
            <Label htmlFor="governance-json">Controle JSON</Label>
            <Textarea id="governance-json" className="min-h-[520px] font-mono text-xs" value={editor} onChange={(event) => setEditor(event.target.value)} disabled={!canManage} aria-invalid={Boolean(parseError)} />
            {parseError ? <p role="alert" className="text-sm text-destructive">{parseError}</p> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor="governance-reason">Motivo obrigatório</Label>
            <Input id="governance-reason" value={reason} onChange={(event) => setReason(event.target.value)} disabled={!canManage} maxLength={240} />
          </div>
          <div className="flex justify-end">
            <Button disabled={!canManage || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
              <Save className="mr-2 h-4 w-4" />{saveMutation.isPending ? 'Publicando…' : 'Publicar versão'}
            </Button>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><History className="h-4 w-4" /> Histórico</CardTitle><CardDescription>Revisões imutáveis do controle.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {versionsQuery.isPending ? <p className="text-sm text-muted-foreground">Carregando histórico…</p> : null}
          {versionsQuery.isError ? <p className="text-sm text-destructive">Histórico indisponível.</p> : null}
          {versionsQuery.isSuccess && versions.length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma versão registrada.</p> : null}
          {versions.map((item) => (
            <div key={item.id} className="rounded-lg border p-3">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium">Versão {item.version}</p>
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Restaurar versão ${item.version}`}
                  disabled={!canManage || item.version === documentQuery.data?.currentVersion}
                  onClick={() => setRestoreVersion(item.version)}
                ><RotateCcw className="h-3.5 w-3.5" /></Button>
              </div>
              <p className="text-xs text-muted-foreground">{new Date(item.createdAt).toLocaleString('pt-BR')}</p>
              <p className="mt-2 text-xs text-muted-foreground">{item.reason || 'Sem motivo registrado.'}</p>
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
            <AlertDialogDescription>O conteúdo será publicado como uma nova revisão auditável. Nenhuma versão será apagada.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction disabled={rollbackMutation.isPending} onClick={() => { if (restoreVersion !== null) rollbackMutation.mutate(restoreVersion); }}>
              Confirmar restauração
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
