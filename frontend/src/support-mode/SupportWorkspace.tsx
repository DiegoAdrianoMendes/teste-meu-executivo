import { useEffect, useMemo, useState, type ElementType } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  BarChart3,
  Bot,
  Building2,
  CalendarDays,
  FileText,
  FileCog,
  Gavel,
  ListTodo,
  Loader2,
  PencilLine,
  RefreshCw,
  Scale,
  Settings2,
  UserPlus,
  Users,
  WalletCards,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { AgentPlatformOperationsPanel } from '@/klm/components/AgentPlatformOperationsPanel';
import { canPlatform } from '@/lib/platform-access';
import { apiFetch } from '@/services/http';
import type { AuthUser } from '@/types';
import type { SupportAccessScope } from '@/support-mode/scopes';
import type { SupportModeSession } from '@/support-mode/session';
import { SupportModeBanner } from '@/support-mode/SupportModeBanner';
import { SupportWriteSurface } from '@/support-mode/SupportWriteSurface';
import { SupportTasksSurface } from '@/support-mode/SupportTasksSurface';
import { SupportMeetingsSurface } from '@/support-mode/SupportMeetingsSurface';
import { supportSurfaceFor } from '@/support-mode/surfaces';

type SupportWorkspaceSurface = ReturnType<typeof supportSurfaceFor> | {
  key: 'agent-platform';
  scope: 'configurations:read';
  label: 'Agentes e chamadas humanas';
  mode: 'agents';
};

function surfaceKey(surface: SupportWorkspaceSurface): string {
  return surface.mode === 'agents' ? surface.key : surface.scope;
}

const SURFACE_ICONS: Record<SupportAccessScope, ElementType> = {
  'workspace:read': Building2,
  'workspace:write': Settings2,
  'members:read': Users,
  'members:write': Users,
  'clients:read': Scale,
  'clients:write': UserPlus,
  'matters:read': Gavel,
  'matters:write': PencilLine,
  'tasks:read': ListTodo,
  'tasks:write': ListTodo,
  'meetings:read': CalendarDays,
  'meetings:write': CalendarDays,
  'documents:read': FileText,
  'finance:read': WalletCards,
  'finance:write': WalletCards,
  'reports:read': BarChart3,
  'configurations:read': FileCog,
  'configurations:write': Settings2,
  'lead_data:read': Users,
};

const HIDDEN_FIELD = /(^|_)(id|token|secret|hash|jti)($|_)/i;
const TITLE_FIELDS = ['name', 'title', 'clientName', 'demandType', 'namespace', 'label', 'email'];
const DETAIL_FIELDS = ['status', 'role', 'priority', 'columnId', 'microStatus', 'updatedAt', 'createdAt'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function scalarText(value: unknown): string | null {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  return null;
}

function friendlyKey(key: string): string {
  return key
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replaceAll('_', ' ')
    .replace(/^./, (letter) => letter.toUpperCase());
}

function recordTitle(record: Record<string, unknown>, index: number): string {
  for (const field of TITLE_FIELDS) {
    const value = scalarText(record[field]);
    if (value) return value;
  }
  return `Registro ${index + 1}`;
}

function recordDetails(record: Record<string, unknown>): Array<[string, string]> {
  return DETAIL_FIELDS.flatMap((field) => {
    if (HIDDEN_FIELD.test(field)) return [];
    const value = scalarText(record[field]);
    return value ? [[friendlyKey(field), value] as [string, string]] : [];
  }).slice(0, 4);
}

function ResourceResult({ data }: { data: unknown }) {
  if (Array.isArray(data)) {
    if (data.length === 0) return <p className="py-10 text-center text-sm text-muted-foreground">Nenhum registro encontrado.</p>;
    return (
      <div className="divide-y rounded-md border">
        {data.map((value, index) => {
          const record = isRecord(value) ? value : { value };
          const details = recordDetails(record);
          return (
            <article key={index} className="px-4 py-3">
              <p className="text-sm font-medium">{recordTitle(record, index)}</p>
              {details.length > 0 && (
                <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
                  {details.map(([key, detail]) => (
                    <div key={key} className="flex gap-1">
                      <dt>{key}:</dt><dd className="text-foreground">{detail}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </article>
          );
        })}
      </div>
    );
  }

  if (isRecord(data)) {
    const entries = Object.entries(data)
      .filter(([key, value]) => !HIDDEN_FIELD.test(key) && scalarText(value) !== null)
      .slice(0, 18);
    return (
      <dl className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-2 xl:grid-cols-3">
        {entries.map(([key, value]) => (
          <div key={key} className="bg-background px-4 py-3">
            <dt className="text-xs text-muted-foreground">{friendlyKey(key)}</dt>
            <dd className="mt-1 break-words text-sm font-medium">{scalarText(value)}</dd>
          </div>
        ))}
      </dl>
    );
  }

  return <p className="py-10 text-center text-sm text-muted-foreground">Resposta sem dados exibíveis.</p>;
}

export function SupportWorkspace({
  session,
  onExit,
  actor = null,
}: {
  session: SupportModeSession;
  onExit: () => Promise<void>;
  actor?: AuthUser | null;
}) {
  const isRootDirect = session.sessionType === 'root_direct';
  const canViewAgents = session.scopes.includes('configurations:read')
    && (isRootDirect || canPlatform(actor, 'ai_operations', 'view'));
  const canManageAgents = session.scopes.includes('configurations:write')
    && (isRootDirect || canPlatform(actor, 'ai_operations', 'manage'));
  const availableSurfaces = useMemo<SupportWorkspaceSurface[]>(() => {
    const surfaces: SupportWorkspaceSurface[] = session.scopes.map(supportSurfaceFor);
    if (canViewAgents) {
      surfaces.push({
        key: 'agent-platform',
        scope: 'configurations:read',
        label: 'Agentes e chamadas humanas',
        mode: 'agents',
      });
    }
    return surfaces;
  }, [canViewAgents, session.scopes]);
  const [selectedSurfaceKey, setSelectedSurfaceKey] = useState(availableSurfaces[0] ? surfaceKey(availableSurfaces[0]) : '');

  useEffect(() => {
    if (!availableSurfaces.some((surface) => surfaceKey(surface) === selectedSurfaceKey)) {
      setSelectedSurfaceKey(availableSurfaces[0] ? surfaceKey(availableSurfaces[0]) : '');
    }
  }, [availableSurfaces, selectedSurfaceKey]);

  const selectedSurface = availableSurfaces.find((surface) => surfaceKey(surface) === selectedSurfaceKey) ?? null;
  const selectedReadPath = selectedSurface?.mode === 'read' ? selectedSurface.path : null;
  const resourceQuery = useQuery({
    queryKey: ['support-mode', session.workspaceId, session.grantId, 'read', selectedSurfaceKey],
    queryFn: () => apiFetch<unknown>(selectedReadPath!),
    enabled: Boolean(selectedReadPath),
    retry: false,
  });
  return (
    <div
      className="min-h-screen bg-background"
      data-support-session-type={session.sessionType ?? 'support_approved'}
      data-can-view-agents={canViewAgents ? 'true' : 'false'}
    >
      <SupportModeBanner session={session} onExit={onExit} />
      <main className="mx-auto grid max-w-[1480px] gap-0 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="border-b p-4 lg:min-h-[calc(100vh-100px)] lg:border-b-0 lg:border-r">
          <p className="mb-3 text-xs font-semibold uppercase text-muted-foreground">Dados autorizados</p>
          <nav className="grid gap-1" aria-label="Recursos do modo suporte">
            {availableSurfaces.map((surface) => {
              const key = surfaceKey(surface);
              const Icon = surface.mode === 'agents' ? Bot : SURFACE_ICONS[surface.scope];
              return (
                <Button
                  key={key}
                  type="button"
                  variant={selectedSurfaceKey === key ? 'secondary' : 'ghost'}
                  className="justify-start"
                  onClick={() => setSelectedSurfaceKey(key)}
                  data-scope={surface.scope}
                  data-support-surface={key}
                >
                  <Icon className="mr-2 h-4 w-4" />
                  <span className="min-w-0 flex-1 truncate text-left">{surface.label}</span>
                  {surface.mode === 'write' && <Badge variant="outline" className="ml-2">alterar</Badge>}
                </Button>
              );
            })}
          </nav>
          {availableSurfaces.length === 0 && (
            <p className="text-xs text-muted-foreground">O grant não contém um escopo reconhecido.</p>
          )}
        </aside>

        <div className="min-w-0 p-4 md:p-6">
          {selectedSurface ? (
            <section aria-labelledby="support-resource-heading">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h1 id="support-resource-heading" className="text-xl font-semibold">{selectedSurface.label}</h1>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Escopo ativo: <span className="font-medium text-foreground">{selectedSurface.scope}</span>
                  </p>
                </div>
                {selectedSurface.mode === 'read' && (
                  <Button
                    type="button"
                    size="icon"
                    variant="outline"
                    aria-label={`Atualizar ${selectedSurface.label}`}
                    title={`Atualizar ${selectedSurface.label}`}
                    onClick={() => { void resourceQuery.refetch(); }}
                    disabled={resourceQuery.isFetching}
                  >
                    <RefreshCw className={`h-4 w-4 ${resourceQuery.isFetching ? 'animate-spin' : ''}`} />
                  </Button>
                )}
              </div>

              {selectedSurface.mode === 'read' && (
                <>
                  {resourceQuery.isLoading && (
                    <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground" role="status">
                      <Loader2 className="h-4 w-4 animate-spin" />Carregando dados autorizados...
                    </div>
                  )}
                  {resourceQuery.isError && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
                      {resourceQuery.error instanceof Error ? resourceQuery.error.message : 'Não foi possível carregar este recurso.'}
                    </div>
                  )}
                  {resourceQuery.isSuccess && <ResourceResult data={resourceQuery.data} />}
                </>
              )}

              {selectedSurface.mode === 'write' && (
                <SupportWriteSurface action={selectedSurface.action} session={session} />
              )}

              {selectedSurface.mode === 'tasks' && (
                <SupportTasksSurface session={session} />
              )}

              {selectedSurface.mode === 'meetings' && (
                <SupportMeetingsSurface session={session} />
              )}

              {selectedSurface.mode === 'agents' && (
                <AgentPlatformOperationsPanel
                  canManage={canManageAgents}
                  currentUserId={actor?.id ?? null}
                  isPlatformRoot={isRootDirect}
                  workspaceId={session.workspaceId}
                />
              )}

              {selectedSurface.mode === 'blocked' && (
                <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100" role="status">
                  <div className="flex gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                      <p className="font-medium">Consulta bloqueada pelo contrato atual</p>
                      <p className="mt-1 text-xs opacity-80">{selectedSurface.reason}</p>
                      <Button type="button" size="sm" className="mt-3" disabled>Consultar dados</Button>
                    </div>
                  </div>
                </div>
              )}

              {selectedSurface.mode === 'augment' && (
                <div className="rounded-md border bg-muted/30 p-4 text-sm" role="status">
                  <p className="font-medium">Escopo complementar ativo</p>
                  <p className="mt-1 text-xs text-muted-foreground">{selectedSurface.reason}</p>
                </div>
              )}
            </section>
          ) : (
            <section>
              <h1 className="text-xl font-semibold">Acesso temporário ativo</h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Nenhuma superfície reconhecida foi autorizada. Nenhuma consulta tenant foi iniciada.
              </p>
            </section>
          )}

          <section className="mt-8 border-t pt-5" aria-labelledby="support-live-audit-heading">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 id="support-live-audit-heading" className="text-sm font-semibold">Trilha durante o uso</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Cada consulta e alteração é registrada automaticamente no servidor.
                </p>
              </div>
              <Badge variant="outline">Registro ativo</Badge>
            </div>
            <div className="mt-3 rounded-md border bg-muted/20 px-3 py-3 text-xs text-muted-foreground" role="status">
              A trilha detalhada fica disponível no painel da plataforma depois que você sair do modo suporte.
              Nenhuma credencial administrativa é usada nesta sessão temporária.
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
