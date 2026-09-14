import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
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
import { UserEditDialog } from './UserEditDialog';
import { Ban, CalendarClock, Check, Copy, Pencil, Plus, RefreshCw, ShieldCheck, ShieldOff, UserCheck, UserX } from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';
import type { AdminUser, AdminRole, WorkspaceAccessPackage } from '@/types';

interface UserManagementProps {
  users: AdminUser[];
  roles: AdminRole[];
  accessPackages?: WorkspaceAccessPackage[];
  onSave: (user: AdminUser) => Promise<void>;
  onResendInvitation?: (invitationId: string) => Promise<void>;
  onRevokeInvitation?: (invitationId: string) => Promise<void>;
  onGetInvitationLink?: (invitationId: string) => Promise<string>;
  onManageAvailability?: (user: AdminUser) => void;
  onOpenProfile?: (user: AdminUser) => void;
}

export function UserManagement({
  users,
  roles,
  accessPackages = [],
  onSave,
  onResendInvitation,
  onRevokeInvitation,
  onGetInvitationLink,
  onManageAvailability,
  onOpenProfile,
}: UserManagementProps) {
  const isMobile = useIsMobile();
  const [editUser, setEditUser] = useState<AdminUser | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmUser, setConfirmUser] = useState<AdminUser | null>(null);
  const [suspendingUserId, setSuspendingUserId] = useState<string | null>(null);
  const [confirmInvitation, setConfirmInvitation] = useState<AdminUser | null>(null);
  const [pendingInvitationAction, setPendingInvitationAction] = useState<string | null>(null);
  const [copiedInvitationId, setCopiedInvitationId] = useState<string | null>(null);
  const [invitationFeedback, setInvitationFeedback] = useState<string | null>(null);
  const humanUsers = users.filter(u => u.type === 'human');

  const getRoleName = (roleIds: string[]) =>
    roleIds.map(id => roles.find(r => r.id === id)?.name ?? id).join(', ');
  const getAccessName = (user: AdminUser) => (
    accessPackages.find((item) => item.key === user.accessPackage)?.label
    ?? getRoleName(user.roles)
  );

  const sensitiveAccessLabel = (user: AdminUser) => {
    if (!user.canViewSensitiveLeadData) return 'Dados do lead ocultos';
    return user.canEditSensitiveLeadData ? 'Dados visíveis e editáveis' : 'Dados visíveis em leitura';
  };

  const handleToggleActive = (u: AdminUser) => {
    if (u.membershipStatus === 'invited') return;
    if (u.isActive) {
      setConfirmUser(u);
    } else {
      void onSave({ ...u, isActive: true });
    }
  };

  const suspendUser = async () => {
    if (!confirmUser) return;
    setSuspendingUserId(confirmUser.id);
    try {
      await onSave({ ...confirmUser, isActive: false });
      setConfirmUser(null);
    } catch {
      // The parent surface reports the API error and the dialog remains open.
    } finally {
      setSuspendingUserId(null);
    }
  };

  const openAccessEditor = (user: AdminUser) => {
    setEditUser(user);
    setDialogOpen(true);
  };

  const statusLabel = (user: AdminUser) => {
    if (user.membershipStatus === 'invited') {
      if (user.invitationStatus === 'revoked') return 'Convite revogado';
      if (user.invitationStatus === 'expired') return 'Convite expirado';
      return 'Convite pendente';
    }
    return user.isActive ? 'Ativo' : 'Suspenso';
  };

  const statusVariant = (user: AdminUser): 'default' | 'secondary' | 'destructive' | 'outline' => {
    if (user.membershipStatus === 'invited') {
      return user.invitationStatus === 'pending' ? 'outline' : 'secondary';
    }
    return user.isActive ? 'default' : 'secondary';
  };

  const invitationDeliveryLabel = (user: AdminUser) => {
    switch (user.invitationDeliveryStatus) {
      case 'sent': return 'E-mail enviado';
      case 'sending': return 'E-mail em envio';
      case 'failed': return 'Falha no envio por e-mail — use Copiar link';
      case 'blocked': return 'E-mail indisponível — use Copiar link';
      case 'pending': return 'E-mail aguardando envio';
      default: return 'Entrega por e-mail ainda não confirmada';
    }
  };

  const resendInvitation = async (user: AdminUser) => {
    if (!user.invitationId || !onResendInvitation) return;
    setPendingInvitationAction(user.invitationId);
    try {
      await onResendInvitation(user.invitationId);
    } catch {
      // The parent surface reports the API error.
    } finally {
      setPendingInvitationAction(null);
    }
  };

  const revokeInvitation = async (user: AdminUser) => {
    if (!user.invitationId || !onRevokeInvitation) return;
    setPendingInvitationAction(user.invitationId);
    try {
      await onRevokeInvitation(user.invitationId);
      setConfirmInvitation(null);
    } catch {
      // The parent surface reports the API error.
    } finally {
      setPendingInvitationAction(null);
    }
  };

  const copyInvitation = async (user: AdminUser) => {
    if (!user.invitationId || !onGetInvitationLink) return;
    setPendingInvitationAction(user.invitationId);
    setInvitationFeedback(null);
    try {
      const relativeLink = await onGetInvitationLink(user.invitationId);
      const link = new URL(relativeLink, window.location.origin).toString();
      if (!navigator.clipboard?.writeText) {
        throw new Error('Área de transferência indisponível neste navegador.');
      }
      await navigator.clipboard.writeText(link);
      setCopiedInvitationId(user.invitationId);
      setInvitationFeedback(`Link de convite de ${user.name} copiado.`);
    } catch (error) {
      setInvitationFeedback(
        error instanceof Error ? error.message : 'Não foi possível copiar o link de convite.',
      );
    } finally {
      setPendingInvitationAction(null);
    }
  };

  if (isMobile) {
    return (
      <div className="space-y-3">
        <Button size="sm" className="w-full" onClick={() => { setEditUser(null); setDialogOpen(true); }}>
          <Plus className="h-4 w-4 mr-1" /> Convidar pessoa
        </Button>
        {humanUsers.map(u => (
          <div key={u.id} className="rounded-lg border bg-card p-3 space-y-2">
            <div className="flex items-center justify-between">
              <button type="button" className="flex min-w-0 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onOpenProfile?.(u)} disabled={!onOpenProfile} aria-label={`Abrir indicadores de ${u.name}`}>
                <Avatar className="h-8 w-8">
                  <AvatarFallback className="text-xs" style={{ backgroundColor: u.color, color: '#fff' }}>{u.initials}</AvatarFallback>
                </Avatar>
                <div>
                  <p className="text-sm font-semibold">{u.name}</p>
                  <p className="text-xs text-muted-foreground">{u.email}</p>
                </div>
              </button>
              <Badge variant={statusVariant(u)} className="text-xs shrink-0">
                {statusLabel(u)}
              </Badge>
            </div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{getAccessName(u)}</span>
              <div className="flex items-center gap-1">
                {onManageAvailability && u.membershipStatus !== 'invited' ? (
                  <Button variant="ghost" size="sm" className="h-7" onClick={() => onManageAvailability(u)}>
                    <CalendarClock className="mr-1 h-3 w-3" />Escala
                  </Button>
                ) : null}
                <Button variant="ghost" size="sm" className="h-7" onClick={() => { setEditUser(u); setDialogOpen(true); }}>
                  <Pencil className="h-3 w-3 mr-1" /> Editar
                </Button>
              </div>
            </div>
            {u.membershipStatus === 'invited' && (
              <p className="text-xs text-muted-foreground">{invitationDeliveryLabel(u)}</p>
            )}
            {u.membershipStatus === 'invited' && (u.invitationStatus === 'pending' || u.invitationStatus === 'expired') && u.invitationId && (
              <div className="flex flex-wrap gap-2 border-t pt-2">
                {onGetInvitationLink && u.invitationStatus === 'pending' && <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="flex-1"
                  disabled={pendingInvitationAction === u.invitationId}
                  onClick={() => { void copyInvitation(u); }}
                >
                  {copiedInvitationId === u.invitationId
                    ? <Check className="mr-1 h-3.5 w-3.5" />
                    : <Copy className="mr-1 h-3.5 w-3.5" />}
                  {copiedInvitationId === u.invitationId ? 'Copiado' : 'Copiar link'}
                </Button>}
                {onResendInvitation && <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="flex-1"
                  disabled={pendingInvitationAction === u.invitationId}
                  onClick={() => { void resendInvitation(u); }}
                >
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />Reenviar
                </Button>}
                {onRevokeInvitation && <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="flex-1"
                  disabled={pendingInvitationAction === u.invitationId}
                  onClick={() => { void revokeInvitation(u); }}
                >
                  <Ban className="mr-1 h-3.5 w-3.5" />Revogar
                </Button>}
              </div>
            )}
            <button type="button" className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Editar acesso aos dados do lead de ${u.name}`} onClick={() => openAccessEditor(u)}>
              {u.canViewSensitiveLeadData ? <ShieldCheck className="h-3.5 w-3.5 text-emerald-700" /> : <ShieldOff className="h-3.5 w-3.5 text-amber-700" />}
              {sensitiveAccessLabel(u)}
            </button>
          </div>
        ))}
        {invitationFeedback && (
          <p role="status" className="text-xs text-muted-foreground">{invitationFeedback}</p>
        )}
        <UserEditDialog user={editUser} roles={roles} accessPackages={accessPackages} open={dialogOpen} onClose={() => setDialogOpen(false)} onSave={onSave} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button size="sm" onClick={() => { setEditUser(null); setDialogOpen(true); }}>
          <Plus className="h-4 w-4 mr-1" /> Convidar pessoa
        </Button>
      </div>
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50 text-muted-foreground text-xs">
              <th className="text-left p-3">Usuário</th>
              <th className="text-left p-3">Função</th>
              <th className="text-left p-3">Status</th>
              <th className="text-left p-3">Dados do lead</th>
              <th className="text-left p-3">Último acesso</th>
              <th className="text-right p-3">Ações</th>
            </tr>
          </thead>
          <tbody>
            {humanUsers.map(u => (
              <tr key={u.id} className="border-b last:border-b-0 hover:bg-muted/30">
                <td className="p-3">
                  <button type="button" className="flex min-w-0 items-center gap-2 text-left hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onOpenProfile?.(u)} disabled={!onOpenProfile} aria-label={`Abrir indicadores de ${u.name}`}>
                    <Avatar className="h-8 w-8">
                      <AvatarFallback className="text-xs" style={{ backgroundColor: u.color, color: '#fff' }}>{u.initials}</AvatarFallback>
                    </Avatar>
                    <div>
                      <p className="text-sm font-semibold">{u.name}</p>
                      <p className="text-xs text-muted-foreground">{u.email}</p>
                    </div>
                  </button>
                </td>
                <td className="p-3 text-xs">{getAccessName(u)}</td>
                <td className="p-3">
                  <Badge variant={statusVariant(u)} className="text-xs">
                    {statusLabel(u)}
                  </Badge>
                  {u.membershipStatus === 'invited' && (
                    <p className="mt-1 text-[11px] text-muted-foreground">{invitationDeliveryLabel(u)}</p>
                  )}
                </td>
                <td className="p-3">
                  <button type="button" className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Editar acesso aos dados do lead de ${u.name}`} onClick={() => openAccessEditor(u)}>
                  <Badge variant="outline" className="cursor-pointer gap-1 text-xs hover:bg-muted">
                    {u.canViewSensitiveLeadData ? <ShieldCheck className="h-3 w-3 text-emerald-700" /> : <ShieldOff className="h-3 w-3 text-amber-700" />}
                    {u.canViewSensitiveLeadData
                      ? (u.canEditSensitiveLeadData ? 'Visíveis + edição' : 'Somente leitura')
                      : 'Ocultos'}
                  </Badge>
                  </button>
                </td>
                <td className="p-3 text-xs text-muted-foreground">
                  {u.lastLogin === '-' ? '-' : new Date(u.lastLogin).toLocaleDateString('pt-BR')}
                </td>
                <td className="p-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    {onManageAvailability && u.membershipStatus !== 'invited' ? (
                      <Button variant="ghost" size="icon" className="h-7 w-7" title="Escala, férias e ausência" aria-label={`Gerenciar escala de ${u.name}`} onClick={() => onManageAvailability(u)}>
                        <CalendarClock className="h-3.5 w-3.5" />
                      </Button>
                    ) : null}
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Editar acesso" onClick={() => { setEditUser(u); setDialogOpen(true); }}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    {u.membershipStatus === 'invited' && (u.invitationStatus === 'pending' || u.invitationStatus === 'expired') && u.invitationId ? (
                      <>
                        {onGetInvitationLink && u.invitationStatus === 'pending' && <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          title={copiedInvitationId === u.invitationId ? 'Link copiado' : 'Copiar link de convite'}
                          aria-label={copiedInvitationId === u.invitationId ? 'Link copiado' : 'Copiar link de convite'}
                          disabled={pendingInvitationAction === u.invitationId}
                          onClick={() => { void copyInvitation(u); }}
                        >
                          {copiedInvitationId === u.invitationId
                            ? <Check className="h-3.5 w-3.5" />
                            : <Copy className="h-3.5 w-3.5" />}
                        </Button>}
                        {onResendInvitation && <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          title="Reenviar convite"
                          aria-label="Reenviar convite"
                          disabled={pendingInvitationAction === u.invitationId}
                          onClick={() => { void resendInvitation(u); }}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </Button>}
                        {onRevokeInvitation && <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          title="Revogar convite"
                          aria-label="Revogar convite"
                          disabled={pendingInvitationAction === u.invitationId}
                          onClick={() => setConfirmInvitation(u)}
                        >
                          <Ban className="h-3.5 w-3.5" />
                        </Button>}
                      </>
                    ) : u.membershipStatus !== 'invited' ? (
                      <Button variant="ghost" size="icon" className="h-7 w-7" title={u.isActive ? 'Suspender acesso ao workspace' : 'Reativar acesso ao workspace'} aria-label={`${u.isActive ? 'Suspender' : 'Reativar'} acesso de ${u.name}`} onClick={() => handleToggleActive(u)}>
                        {u.isActive ? <UserX className="h-3.5 w-3.5" /> : <UserCheck className="h-3.5 w-3.5" />}
                      </Button>
                    ) : null}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {invitationFeedback && (
        <p role="status" className="text-xs text-muted-foreground">{invitationFeedback}</p>
      )}
      <UserEditDialog user={editUser} roles={roles} accessPackages={accessPackages} open={dialogOpen} onClose={() => setDialogOpen(false)} onSave={onSave} />

      <AlertDialog open={!!confirmUser} onOpenChange={(open) => { if (!open) setConfirmUser(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Suspender acesso ao workspace</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <span className="block">Confirme a suspensão do acesso de <strong>{confirmUser?.name}</strong> a este workspace. A identidade global e outros workspaces não serão alterados.</span>
              <span className="block">A ação será registrada nos logs de auditoria com responsável, estado anterior e estado posterior. A reversão poderá ser solicitada por ticket à administração.</span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={suspendingUserId === confirmUser?.id}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              disabled={suspendingUserId === confirmUser?.id}
              onClick={(event) => {
                event.preventDefault();
                void suspendUser();
              }}
            >
              {suspendingUserId === confirmUser?.id ? 'Suspendendo...' : 'Confirmar suspensão'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={!!confirmInvitation} onOpenChange={(open) => { if (!open) setConfirmInvitation(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revogar convite pendente</AlertDialogTitle>
            <AlertDialogDescription>
              O link atual de <strong>{confirmInvitation?.name}</strong> deixará de funcionar. Esta operação não altera a identidade global.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              disabled={Boolean(confirmInvitation?.invitationId && pendingInvitationAction === confirmInvitation.invitationId)}
              onClick={(event) => {
                event.preventDefault();
                if (confirmInvitation) void revokeInvitation(confirmInvitation);
              }}
            >
              Revogar convite
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
