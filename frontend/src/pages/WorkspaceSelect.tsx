import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Building2, Check, ChevronRight } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useAppStore } from '@/store/app-context';
import { getToken, saveSession, saveToken, saveUser, selectWorkspace as selectWorkspaceRequest } from '@/services/auth';
import { roleLabel } from '@/lib/role-labels';

export default function WorkspaceSelect() {
  const { state, selectWorkspace, login } = useAppStore();
  const navigate = useNavigate();
  const location = useLocation();
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const workspaces = state.session?.availableWorkspaces ?? [];
  const from = (location.state as { from?: string })?.from || '/app';

  const handleSelect = async (workspaceId: string) => {
    if (!state.currentUser) return;
    const token = getToken();
    if (!token) return;

    setError(null);
    setLoadingId(workspaceId);
    try {
      const response = await selectWorkspaceRequest(token, workspaceId);
      saveToken(response.accessToken || response.access_token || '');
      saveUser(response.user);
      saveSession(response.session);
      login({
        id: response.user.id,
        name: response.user.name,
        email: response.user.email,
        role: response.user.role,
        initials: response.user.initials,
        isInternalStaff: response.user.isInternalStaff,
        isPlatformAdmin: response.user.isPlatformAdmin,
        platformRolePackage: response.user.platformRolePackage,
        platformAccessActive: response.user.platformAccessActive,
        platformPermissions: response.user.platformPermissions,
      }, response.session);
      selectWorkspace(response.session);
      navigate(from, { replace: true });
    } catch (error) {
      console.error(error);
      setError(error instanceof Error ? error.message : 'Não foi possível trocar de workspace.');
    } finally {
      setLoadingId(null);
    }
  };

  return (
    <div className="min-h-screen bg-background p-6 md:p-10">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">Escolha o workspace</h1>
          <p className="text-sm text-muted-foreground">
            O Meu Executivo usa contexto ativo por workspace para garantir isolamento de dados, permissões e configurações.
          </p>
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          {workspaces.map(membership => {
            const isActive = state.activeWorkspace?.id === membership.workspace.id;
            const isLoading = loadingId === membership.workspace.id;
            return (
              <Card key={membership.id} className={isActive ? 'border-primary shadow-sm' : ''}>
                <CardHeader className="space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                        <Building2 className="h-5 w-5" />
                      </div>
                      <div>
                        <CardTitle className="text-lg">{membership.workspace.name}</CardTitle>
                        <CardDescription>{membership.workspace.slug}</CardDescription>
                      </div>
                    </div>
                    {isActive && <Badge>Ativo</Badge>}
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-1 text-sm text-muted-foreground">
                    <p>Função: <span className="font-medium text-foreground">{roleLabel(membership.role)}</span></p>
                    <p>Status: <span className="font-medium text-foreground">{membership.status}</span></p>
                    <p>Permissões visíveis: <span className="font-medium text-foreground">{membership.permissions.length}</span></p>
                  </div>
                  <Button
                    className="w-full justify-between"
                    onClick={() => handleSelect(membership.workspace.id)}
                    disabled={isLoading}
                  >
                    <span>{isActive ? 'Continuar neste workspace' : 'Entrar neste workspace'}</span>
                    {isActive ? <Check className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>
    </div>
  );
}
