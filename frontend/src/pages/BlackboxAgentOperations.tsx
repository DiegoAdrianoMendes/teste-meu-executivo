import { Link } from 'react-router-dom';

import { AgentPlatformOperationsPanel } from '@/klm/components/AgentPlatformOperationsPanel';
import { StudioWorkspaceCombobox } from '@/components/platform/StudioWorkspaceCombobox';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { canPlatform } from '@/lib/platform-access';
import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { getActiveSupportMode, subscribeToSupportMode, type SupportModeSession } from '@/support-mode/session';
import type { StudioWorkspaceOption } from '@/services/platform-configuration-studio';
import { useEffect, useState } from 'react';

function hasApprovedAgentReadScope(session: SupportModeSession | null): session is SupportModeSession {
  return Boolean(
    session
    && session.sessionType === 'support_approved'
    && Date.parse(session.expiresAt) > Date.now()
    && session.scopes.includes('configurations:read'),
  );
}

function hasApprovedAgentWriteScope(session: SupportModeSession | null): boolean {
  return hasApprovedAgentReadScope(session) && session.scopes.includes('configurations:write');
}

/** Runtime operations are exposed in Blackbox without duplicating the /app Agents surface. */
export default function BlackboxAgentOperations() {
  const { effectiveUser } = useBlackboxManifest();
  const [workspace, setWorkspace] = useState<StudioWorkspaceOption | null>(null);
  const [supportSession, setSupportSession] = useState<SupportModeSession | null>(() => getActiveSupportMode());
  const canView = canPlatform(effectiveUser, 'ai_operations', 'view') || effectiveUser.isPlatformRoot;
  const canManage = canPlatform(effectiveUser, 'ai_operations', 'manage') || effectiveUser.isPlatformRoot;
  const isRoot = Boolean(effectiveUser.isPlatformRoot);
  const approvedSupportSession = hasApprovedAgentReadScope(supportSession) ? supportSession : null;
  const workspaceId = isRoot ? workspace?.id ?? null : approvedSupportSession?.workspaceId ?? null;
  const canManageWorkspace = isRoot
    ? canManage && Boolean(workspaceId)
    : canManage && hasApprovedAgentWriteScope(supportSession);

  useEffect(() => subscribeToSupportMode(setSupportSession), []);

  if (!canView) {
    return <div className="p-6 text-sm text-muted-foreground">Operação de agentes não autorizada para esta função.</div>;
  }

  if (!isRoot && !approvedSupportSession) {
    return (
      <main className="space-y-4 p-4 sm:p-6" data-testid="blackbox-agent-operations-restricted">
        <Card><CardHeader><CardTitle>Operação contextual de agentes</CardTitle><CardDescription>O runtime por conta exige uma sessão de suporte aprovada, ativa e com escopo de configuração para agentes. Funcionários não podem escolher outro workspace diretamente nesta superfície.</CardDescription></CardHeader><CardContent><Button asChild variant="outline"><Link to="/blackbox/support-access">Solicitar ou acompanhar acesso aprovado</Link></Button></CardContent></Card>
      </main>
    );
  }

  return (
    <main className="space-y-4 p-4 sm:p-6" data-testid="blackbox-agent-operations">
      <Card>
        <CardHeader>
          <CardTitle>Agentes, flows e chamadas humanas</CardTitle>
          <CardDescription>
            Operação do runtime pela plataforma. O contexto da conta é selecionado por nome; nenhuma decisão humana copia conteúdo privado.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {isRoot ? <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <div>
              <label htmlFor="blackbox-agent-workspace" className="text-xs font-medium">Conta de referência (opcional)</label>
              <div id="blackbox-agent-workspace" className="mt-1"><StudioWorkspaceCombobox value={workspace?.id ?? ''} selected={workspace} onChange={setWorkspace} /></div>
            </div>
            {workspace ? <Button asChild variant="outline"><Link to={`/blackbox/workspaces?workspaceId=${encodeURIComponent(workspace.id)}`}>Abrir Conta 360</Link></Button> : null}
          </div> : <div className="flex flex-wrap items-end justify-between gap-3 rounded-md border bg-muted/30 p-3" data-testid="blackbox-agent-support-workspace">
            <div><p className="text-xs font-medium">Conta autorizada pela sessão aprovada</p><p className="mt-1 text-sm">{approvedSupportSession.workspaceName}</p><p className="mt-1 text-xs text-muted-foreground">Expira em {new Date(approvedSupportSession.expiresAt).toLocaleString('pt-BR')} · leitura de configurações autorizada{hasApprovedAgentWriteScope(supportSession) ? ' · alteração autorizada' : ''}.</p></div>
            <Button asChild variant="outline"><Link to={`/blackbox/workspaces?workspaceId=${encodeURIComponent(approvedSupportSession.workspaceId)}`}>Abrir Conta 360</Link></Button>
          </div>}
          <p className="text-xs text-muted-foreground">
            {isRoot ? 'Super Admin: deployments, decisões e rotas do runtime sem aprovação adicional, sempre auditados.' : 'As ações disponíveis seguem o RBAC, o escopo e a expiração da sessão aprovada atual.'}
          </p>
        </CardContent>
      </Card>
      <AgentPlatformOperationsPanel canManage={canManageWorkspace} isPlatformRoot={isRoot} currentUserId={effectiveUser.id} workspaceId={workspaceId} />
      <Card>
        <CardHeader><CardTitle className="text-base">Edição protegida de flows</CardTitle><CardDescription>Flows e artefatos do cliente não são abertos no `/app` apenas por query string. A edição de conteúdo tenant exige sessão administrativa explícita, com banner e auditoria.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {workspaceId ? <>
            <p className="text-sm text-muted-foreground">{isRoot ? 'Abra a Conta 360, inicie acesso direto root e siga para o editor dentro da sessão. O Super Admin continua identificado como ator da plataforma; a conta nunca é assumida silenciosamente.' : 'Abra a Conta 360 dentro da sessão aprovada atual. O funcionário continua identificado, limitado ao escopo concedido e não assume a identidade do cliente.'}</p>
            <Button asChild variant="outline"><Link to={`/blackbox/workspaces?workspaceId=${encodeURIComponent(workspaceId)}`}>{isRoot ? 'Abrir Conta 360 e iniciar sessão protegida' : 'Abrir Conta 360 da sessão aprovada'}</Link></Button>
          </> : <p className="text-sm text-muted-foreground">Selecione uma conta acima para abrir o gate de sessão administrativa. Sem conta e sem sessão, o editor não é disponibilizado.</p>}
        </CardContent>
      </Card>
    </main>
  );
}
