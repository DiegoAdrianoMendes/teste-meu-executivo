import { Clock3, Users, UserCog } from 'lucide-react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { UserManagement } from '@/components/admin/UserManagement';
import { RolesPermissions } from '@/components/admin/RolesPermissions';
import type { AdminRole, AdminUser, WorkspaceAccessPackage, WorkspaceSettingsProfile } from '@/types';

interface WorkspaceTeamPanelProps {
  workspace: WorkspaceSettingsProfile | null;
  users: AdminUser[];
  roles: AdminRole[];
  accessPackages: WorkspaceAccessPackage[];
  loading?: boolean;
  canManageUsers: boolean;
  canViewRoles: boolean;
  onSaveUser: (user: AdminUser) => Promise<void>;
  onResendInvitation: (invitationId: string) => Promise<void>;
  onRevokeInvitation: (invitationId: string) => Promise<void>;
  onGetInvitationLink?: (invitationId: string) => Promise<string>;
}

export function WorkspaceTeamPanel({
  workspace,
  users,
  roles,
  accessPackages,
  loading,
  canManageUsers,
  canViewRoles,
  onSaveUser,
  onResendInvitation,
  onRevokeInvitation,
  onGetInvitationLink,
}: WorkspaceTeamPanelProps) {
  const planSummary = workspace?.planSummary ?? null;
  const humanUsers = users.filter((user) => user.type === 'human');
  const activeUsers = humanUsers.filter((user) => user.membershipStatus === 'active');
  const invitedUsers = humanUsers.filter((user) => user.membershipStatus === 'invited' && user.invitationStatus === 'pending');
  const maxUsers = planSummary?.maxUsers ?? null;
  const seatsUsed = planSummary?.seatsUsed ?? humanUsers.length;
  const seatsRemaining = planSummary?.seatsRemaining ?? null;
  const usagePct = maxUsers ? Math.min(Math.round((seatsUsed / maxUsers) * 100), 100) : 0;

  if (loading) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando equipe e permissões...
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <Users className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Equipe ativa</p>
              <p className="text-2xl font-bold tabular-nums">{activeUsers.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10">
              <Clock3 className="h-4 w-4 text-amber-700" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Convites pendentes</p>
              <p className="text-2xl font-bold tabular-nums">{invitedUsers.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <UserCog className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Plano atual</p>
              <p className="truncate text-2xl font-bold">{planSummary?.name ?? 'Workspace ativo'}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-2 py-5">
            <div className="flex items-center justify-between">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Limite de usuários</p>
              <Badge variant={planSummary?.isAtLimit ? 'destructive' : 'secondary'}>
                {maxUsers ? `${seatsUsed}/${maxUsers}` : `${seatsUsed}/ilimitado`}
              </Badge>
            </div>
            {maxUsers ? <Progress value={usagePct} className="h-2" /> : <div className="h-2 rounded-full bg-muted" />}
            <p className="text-xs text-muted-foreground">
              {maxUsers
                ? `${seatsRemaining ?? 0} vagas restantes para advogados, estagiários e gestores do workspace.`
                : 'Seu plano atual não limita o tamanho da equipe.'}
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Equipe & permissões</CardTitle>
          <CardDescription>
            Pacotes por função, exceções individuais, suspensão por membership e visibilidade dos dados dos leads.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {canManageUsers ? (
            <UserManagement
              users={users}
              roles={roles}
              accessPackages={accessPackages}
              onSave={onSaveUser}
              onResendInvitation={onResendInvitation}
              onRevokeInvitation={onRevokeInvitation}
              onGetInvitationLink={onGetInvitationLink}
            />
          ) : (
            <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
              Seu papel atual pode acompanhar a equipe, mas não pode adicionar ou editar usuários.
            </div>
          )}

          {canViewRoles && <RolesPermissions roles={roles} users={users} />}
        </CardContent>
      </Card>
    </div>
  );
}
