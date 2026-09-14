import { useEffect, useMemo, useState } from 'react';
import { Info, MailPlus, ShieldCheck, ShieldOff, TriangleAlert, UserRoundCog } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { PermissionMatrix } from './PermissionMatrix';
import type { AdminRole, AdminUser, Permission, WorkspaceAccessPackage } from '@/types';

interface UserEditDialogProps {
  user: AdminUser | null;
  roles: AdminRole[];
  accessPackages?: WorkspaceAccessPackage[];
  open: boolean;
  onClose: () => void;
  onSave: (user: AdminUser) => Promise<void>;
}

const EMPTY_ACCESS_PACKAGES: WorkspaceAccessPackage[] = [];

const ACCESS_PACKAGE_GROUPS = [
  { label: 'Gestão do escritório', keys: ['office_owner', 'office_admin', 'managing_partner', 'equity_partner', 'legal_manager', 'practice_coordinator'] },
  { label: 'Equipe jurídica', keys: ['senior_lawyer', 'lawyer', 'associate_lawyer', 'junior_lawyer', 'legal_intern', 'paralegal'] },
  { label: 'Setores da advocacia', keys: ['litigation_lawyer', 'advisory_lawyer', 'contracts_lawyer'] },
  { label: 'Operação jurídica', keys: ['case_controller', 'legal_operations', 'intake_sdr', 'court_correspondent'] },
  { label: 'Administrativo e crescimento', keys: ['secretary', 'finance', 'accounting', 'people_hr', 'client_success', 'business_development', 'marketing', 'it_integrations', 'auditor'] },
] as const;

const permissionKey = (permission: Permission) => `${permission.module}:${permission.action}`;
const isAssignableScope = (permission: Permission) => (
  permission.scope === 'workspace' || permission.scope === 'all'
);

function uniquePermissions(permissions: Permission[]): Permission[] {
  return Array.from(new Map(permissions.map((permission) => [permissionKey(permission), permission])).values());
}

function buildEffectivePermissions(
  base: Permission[],
  grants: Permission[],
  denials: Permission[],
): Permission[] {
  const denied = new Set([
    ...denials.map(permissionKey),
    ...grants.filter((permission) => !isAssignableScope(permission)).map(permissionKey),
  ]);
  const effective = uniquePermissions([
    ...base.filter((permission) => !denied.has(permissionKey(permission))),
    ...grants.filter(isAssignableScope),
  ]);
  if (!effective.some((permission) => permissionKey(permission) === 'lead_data:view')) {
    return effective.filter((permission) => permissionKey(permission) !== 'lead_data:edit');
  }
  return effective;
}

function createEmptyUser(
  roles: AdminRole[],
  accessPackages: WorkspaceAccessPackage[],
): AdminUser {
  const defaultPackage = accessPackages.find((item) => item.key === 'lawyer') ?? accessPackages[0];
  const selectedRole = roles.find((role) => role.id === defaultPackage?.workspaceRole)
    ?? roles.find((role) => role.id === 'operator')
    ?? roles[0];
  const rolePermissions = selectedRole?.permissions ?? [];
  const directPermissions = defaultPackage?.permissionGrants ?? [];
  const deniedPermissions = defaultPackage?.permissionDenials ?? [];
  const effectivePermissions = defaultPackage?.permissions
    ?? buildEffectivePermissions(rolePermissions, directPermissions, deniedPermissions);
  return {
    id: '',
    name: '',
    initials: '',
    email: '',
    phone: '',
    type: 'human',
    role: selectedRole?.id ?? 'operator',
    color: 'hsl(217 50% 40%)',
    isActive: false,
    lastLogin: '-',
    roles: selectedRole ? [selectedRole.id] : ['operator'],
    accessPackage: defaultPackage?.key ?? null,
    directPermissions,
    deniedPermissions,
    rolePermissions,
    effectivePermissions,
    canViewSensitiveLeadData: effectivePermissions.some((item) => permissionKey(item) === 'lead_data:view'),
    canEditSensitiveLeadData: effectivePermissions.some((item) => permissionKey(item) === 'lead_data:edit'),
    membershipStatus: 'invited',
  };
}

export function UserEditDialog({
  user,
  roles,
  accessPackages = EMPTY_ACCESS_PACKAGES,
  open,
  onClose,
  onSave,
}: UserEditDialogProps) {
  const isNew = !user?.membershipId;
  const [form, setForm] = useState<AdminUser>(() => user ?? createEmptyUser(roles, accessPackages));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const next = user
      ? (() => {
          const rolePermissions = user.rolePermissions ?? [];
          const directPermissions = (user.directPermissions ?? []).filter(isAssignableScope);
          const legacyRestrictions = (user.directPermissions ?? [])
            .filter((permission) => !isAssignableScope(permission))
            .map((permission) => ({ ...permission, scope: 'workspace' as const }));
          const deniedPermissions = uniquePermissions([
            ...(user.deniedPermissions ?? []).map((permission) => (
              isAssignableScope(permission) ? permission : { ...permission, scope: 'workspace' as const }
            )),
            ...legacyRestrictions,
          ]);
          return {
            ...user,
            directPermissions,
            deniedPermissions,
            rolePermissions,
            effectivePermissions: buildEffectivePermissions(rolePermissions, directPermissions, deniedPermissions),
          };
        })()
      : createEmptyUser(roles, accessPackages);
    setForm(next);
    setSaving(false);
    setError(null);
  }, [accessPackages, open, roles, user]);

  const selectedPackage = accessPackages.find((item) => item.key === form.accessPackage);
  const selectedRole = roles.find((role) => role.id === form.roles[0]);
  const rolePermissions = useMemo(
    () => selectedRole?.permissions ?? form.rolePermissions ?? [],
    [form.rolePermissions, selectedRole],
  );
  const effectivePermissions = useMemo(
    () => buildEffectivePermissions(
      rolePermissions,
      form.directPermissions ?? [],
      form.deniedPermissions ?? [],
    ),
    [form.deniedPermissions, form.directPermissions, rolePermissions],
  );
  const availablePermissions = useMemo(
    () => uniquePermissions(roles.flatMap((role) => role.permissions)),
    [roles],
  );
  const canViewSensitive = effectivePermissions.some((item) => permissionKey(item) === 'lead_data:view');
  const canEditSensitive = effectivePermissions.some((item) => permissionKey(item) === 'lead_data:edit');
  const isOwner = form.roles[0] === 'workspace_owner';

  const update = (patch: Partial<AdminUser>) => setForm((previous) => ({ ...previous, ...patch }));

  const applyEffectivePermissions = (nextPermissions: Permission[]) => {
    if (!nextPermissions.some((permission) => permissionKey(permission) === 'lead_data:view')) {
      nextPermissions = nextPermissions.filter((permission) => permissionKey(permission) !== 'lead_data:edit');
    }
    const nextByKey = new Map(nextPermissions.map((permission) => [permissionKey(permission), permission]));
    const baseByKey = new Map(rolePermissions.map((permission) => [permissionKey(permission), permission]));
    const grants = nextPermissions.filter((permission) => {
      const inherited = baseByKey.get(permissionKey(permission));
      return !inherited || inherited.scope !== permission.scope;
    });
    const denials = rolePermissions.filter((permission) => !nextByKey.has(permissionKey(permission)));
    update({
      directPermissions: grants,
      deniedPermissions: denials,
      effectivePermissions: nextPermissions,
      canViewSensitiveLeadData: nextByKey.has('lead_data:view'),
      canEditSensitiveLeadData: nextByKey.has('lead_data:edit'),
    });
  };

  const toggleSensitiveData = (checked: boolean) => {
    const withoutSensitive = effectivePermissions.filter(
      (item) => !['lead_data:view', 'lead_data:edit'].includes(permissionKey(item)),
    );
    if (!checked) {
      applyEffectivePermissions(withoutSensitive);
      return;
    }
    const catalogPermission = availablePermissions.find((item) => permissionKey(item) === 'lead_data:view') ?? {
      module: 'lead_data' as const,
      action: 'view' as const,
      scope: 'workspace' as const,
    };
    const restored = [...withoutSensitive, catalogPermission];
    const inheritedEdit = rolePermissions.find((item) => permissionKey(item) === 'lead_data:edit');
    if (inheritedEdit) restored.push(inheritedEdit);
    applyEffectivePermissions(restored);
  };

  const changeRole = (roleId: string) => {
    const nextRole = roles.find((role) => role.id === roleId);
    const inherited = nextRole?.permissions ?? [];
    update({
      role: roleId,
      roles: [roleId],
      accessPackage: null,
      rolePermissions: inherited,
      directPermissions: [],
      deniedPermissions: [],
      effectivePermissions: inherited,
      canViewSensitiveLeadData: inherited.some((item) => permissionKey(item) === 'lead_data:view'),
      canEditSensitiveLeadData: inherited.some((item) => permissionKey(item) === 'lead_data:edit'),
    });
  };

  const changeAccessPackage = (packageKey: string) => {
    if (packageKey === 'custom') {
      update({ accessPackage: null });
      return;
    }
    const nextPackage = accessPackages.find((item) => item.key === packageKey);
    if (!nextPackage) return;
    const nextRole = roles.find((role) => role.id === nextPackage.workspaceRole);
    const inherited = nextRole?.permissions ?? [];
    update({
      role: nextPackage.workspaceRole,
      roles: [nextPackage.workspaceRole],
      accessPackage: nextPackage.key,
      rolePermissions: inherited,
      directPermissions: nextPackage.permissionGrants,
      deniedPermissions: nextPackage.permissionDenials,
      effectivePermissions: nextPackage.permissions,
      canViewSensitiveLeadData: nextPackage.permissions.some((item) => permissionKey(item) === 'lead_data:view'),
      canEditSensitiveLeadData: nextPackage.permissions.some((item) => permissionKey(item) === 'lead_data:edit'),
    });
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.email.trim() || !form.roles[0]) {
      setError('Preencha nome, e-mail e papel.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({
        ...form,
        name: form.name.trim(),
        email: form.email.trim(),
        rolePermissions,
        effectivePermissions,
        canViewSensitiveLeadData: canViewSensitive,
        canEditSensitiveLeadData: canEditSensitive,
        membershipStatus: isNew ? 'invited' : form.membershipStatus,
      });
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar o usuário.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen && !saving) onClose(); }}>
      <DialogContent className="flex max-h-[92vh] max-w-5xl flex-col overflow-hidden p-0">
        <div className="border-b px-5 py-4 pr-12">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <UserRoundCog className="h-4 w-4 text-[#006FE8]" />
            {isNew ? 'Convidar pessoa' : `Acesso de ${form.name}`}
          </DialogTitle>
          <DialogDescription className="sr-only">
            Configure o convite, o papel e as permissões da membership neste workspace.
          </DialogDescription>
        </DialogHeader>
        </div>

        <Tabs defaultValue="dados" className="flex min-h-0 flex-1 flex-col px-5 pb-5">
          <TabsList className="mt-4 grid h-10 w-full shrink-0 grid-cols-2 bg-muted/60">
            <TabsTrigger value="dados">Dados e acesso</TabsTrigger>
            <TabsTrigger value="permissoes">Permissões</TabsTrigger>
          </TabsList>

          <TabsContent value="dados" className="mt-4 min-h-0 space-y-4 overflow-y-auto pr-1">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="team-name">Nome</Label>
                <Input
                  id="team-name"
                  value={form.name}
                  disabled={!isNew}
                  onChange={(event) => {
                    const name = event.target.value;
                    update({
                      name,
                      initials: name.split(' ').filter(Boolean).map((word) => word[0]).join('').slice(0, 2).toUpperCase(),
                    });
                  }}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="team-email">E-mail</Label>
                <Input id="team-email" type="email" value={form.email} disabled={!isNew} onChange={(event) => update({ email: event.target.value })} />
              </div>
              {accessPackages.length > 0 && (
                <div className="space-y-1.5 sm:col-span-2">
                  <Label>Pacote de função</Label>
                  <Select value={form.accessPackage ?? 'custom'} onValueChange={changeAccessPackage}>
                    <SelectTrigger><SelectValue placeholder="Selecione a função" /></SelectTrigger>
                    <SelectContent>
                      {ACCESS_PACKAGE_GROUPS.map((group) => {
                        const items = group.keys.map((key) => accessPackages.find((item) => item.key === key)).filter((item): item is WorkspaceAccessPackage => Boolean(item));
                        return items.length ? <SelectGroup key={group.label}><SelectLabel>{group.label}</SelectLabel>{items.map((item) => <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>)}</SelectGroup> : null;
                      })}
                      <SelectItem value="custom">Acesso personalizado</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {selectedPackage?.description ?? 'Defina manualmente o papel e as permissões desta pessoa.'}
                  </p>
                </div>
              )}
              <div className="space-y-1.5 sm:col-span-2">
                <Label>Papel técnico do workspace</Label>
                <Select value={form.roles[0] ?? ''} onValueChange={changeRole}>
                  <SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger>
                  <SelectContent>
                    {roles.map((role) => <SelectItem key={role.id} value={role.id}>{role.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                {selectedPackage && (
                  <p className="text-xs text-muted-foreground">
                    Definido pelo pacote. Escolher outro papel muda o acesso para personalizado.
                  </p>
                )}
              </div>
            </div>

            {(isNew || form.membershipStatus === 'invited') ? (
              <div className="flex items-start gap-3 rounded-md border bg-muted/20 p-3">
                <MailPlus className="mt-0.5 h-4 w-4 shrink-0 text-[#006FE8]" />
                <div>
                  <p className="text-sm font-medium">Aceite pendente</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    A pessoa define a própria senha ao aceitar. O administrador não altera credenciais ou identidade global.
                  </p>
                </div>
              </div>
            ) : (
              <div className={`flex items-center justify-between gap-4 rounded-md border p-3 ${form.isActive ? 'border-emerald-600/25 bg-emerald-500/[0.06]' : 'border-border bg-muted/20'}`}>
                <div>
                  <p className="text-sm font-medium">Acesso ao workspace</p>
                  <p className="text-xs text-muted-foreground">{form.isActive ? 'Membership ativa' : 'Membership suspensa'}</p>
                </div>
                <Switch aria-label="Acesso ao workspace" className="data-[state=checked]:bg-emerald-700 dark:data-[state=checked]:bg-emerald-600" checked={form.isActive} onCheckedChange={(isActive) => update({ isActive })} />
              </div>
            )}

            <div className={`flex items-start justify-between gap-4 rounded-md border p-4 ${canViewSensitive ? 'border-emerald-600/25 bg-emerald-500/[0.06]' : 'border-amber-600/25 bg-amber-500/[0.06]'}`}>
              <div className="flex min-w-0 gap-3">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-background">
                  {canViewSensitive ? <ShieldCheck className="h-4 w-4 text-emerald-700" /> : <ShieldOff className="h-4 w-4 text-amber-700" />}
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold">Dados sensíveis dos leads</p>
                    <Badge variant={canViewSensitive ? 'default' : 'secondary'}>
                      {canViewSensitive ? (canEditSensitive ? 'Visíveis e editáveis' : 'Somente leitura') : 'Ocultos'}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    Nome, telefone, e-mail, CPF e metadados de contato. A edição possui permissão própria.
                  </p>
                </div>
              </div>
              <Switch
                aria-label="Permitir acesso aos dados sensíveis dos leads"
                className="data-[state=checked]:bg-emerald-700 dark:data-[state=checked]:bg-emerald-600"
                checked={canViewSensitive}
                onCheckedChange={toggleSensitiveData}
              />
            </div>
          </TabsContent>

          <TabsContent value="permissoes" className="mt-4 min-h-0 space-y-3 overflow-y-auto pr-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="flex items-center gap-1.5">
                  <p className="text-sm font-semibold">Permissões efetivas</p>
                  <TooltipProvider delayDuration={200}><Tooltip><TooltipTrigger asChild><button type="button" className="text-muted-foreground hover:text-foreground" aria-label="Como funcionam as permissões"><Info className="h-3.5 w-3.5" /></button></TooltipTrigger><TooltipContent className="max-w-72 text-xs">As caixas marcadas permitem a ação. Desmarcar uma permissão herdada cria um bloqueio individual; marcar uma ação ausente cria uma concessão individual.</TooltipContent></Tooltip></TooltipProvider>
                </div>
                <div className="flex items-center gap-1.5">
                  <p className="text-xs text-muted-foreground">Papel base com concessões e bloqueios individuais.</p>
                  <TooltipProvider delayDuration={200}><Tooltip><TooltipTrigger asChild><button type="button" className="text-muted-foreground hover:text-foreground" aria-label="O que são exceções individuais"><Info className="h-3.5 w-3.5" /></button></TooltipTrigger><TooltipContent className="max-w-72 text-xs">Exceções alteram somente esta pessoa. O papel base e os demais membros permanecem inalterados.</TooltipContent></Tooltip></TooltipProvider>
                </div>
              </div>
              <Badge variant="outline">
                {(form.directPermissions?.length ?? 0) + (form.deniedPermissions?.length ?? 0)} exceções
              </Badge>
            </div>
            {isOwner && (
              <div className="flex items-start gap-3 rounded-md border border-amber-600/30 bg-amber-500/10 p-3 text-xs">
                <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                <div><strong>Edição das permissões do titular</strong><p className="mt-1 text-muted-foreground">Você pode editar as caixas abaixo. Bloquear gestão de usuários, permissões ou configurações pode limitar a administração da conta; a alteração será auditada.</p></div>
              </div>
            )}
            <PermissionMatrix
              permissions={effectivePermissions}
              availablePermissions={availablePermissions}
              onChange={applyEffectivePermissions}
              readOnly={false}
              inheritedPermissions={rolePermissions}
              directPermissions={form.directPermissions ?? []}
              deniedPermissions={form.deniedPermissions ?? []}
            />
          </TabsContent>
        </Tabs>

        {error && <p className="mx-5 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">{error}</p>}

        <div className="flex shrink-0 flex-col-reverse gap-2 border-t bg-background px-5 py-4 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onClose} disabled={saving}>Cancelar</Button>
          <Button className="bg-[#006FE8] text-white hover:bg-[#005BC4]" onClick={handleSave} disabled={saving}>
            {saving ? 'Salvando...' : isNew ? 'Enviar convite' : 'Salvar acesso'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
