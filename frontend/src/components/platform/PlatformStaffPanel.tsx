import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, KeyRound, LogOut, Pencil, Plus, UserRoundCog, UsersRound } from 'lucide-react';

import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { canPlatform, isPlatformRoot } from '@/lib/platform-access';
import { PlatformOrganizationPanel } from '@/components/platform/PlatformOrganizationPanel';
import {
  createPlatformStaff,
  fetchPlatformStaffPage,
  fetchPlatformStaffPackages,
  revokePlatformStaffSessions,
  setPlatformStaffTemporaryPassword,
  updatePlatformStaff,
} from '@/services/platform';
import type { AuthUser, PlatformPermission, PlatformRolePackage, PlatformStaffUser } from '@/types';

const permissionKey = (permission: PlatformPermission) => `${permission.module}:${permission.action}`;
const EMPTY_PLATFORM_ROLE_PACKAGES: PlatformRolePackage[] = [];

const moduleLabels: Record<string, string> = {
  dashboard: 'Visão geral',
  finance: 'Financeiro',
  customers: 'Clientes SaaS',
  plans: 'Planos',
  billing: 'Assinaturas e cobrança',
  sales: 'Vendas',
  onboarding: 'Onboarding',
  success: 'Customer Success',
  support: 'Suporte',
  support_access: 'Acesso temporário',
  staff: 'Equipe SaaS',
  content: 'Site e conteúdo',
  marketing: 'Marketing',
  community: 'Comunidade',
  whatsapp: 'WhatsApp da plataforma',
  integrations: 'Integrações',
  ai_operations: 'Operação de IA',
  errors: 'Erros',
  logs: 'Logs',
  audit: 'Auditoria',
  compliance: 'Compliance',
  security: 'Segurança',
  releases: 'Releases',
  feature_flags: 'Feature flags',
  infrastructure: 'Infraestrutura',
  data_governance: 'Governança de dados',
  settings: 'Configurações globais',
};

const actionLabels: Record<string, string> = {
  view: 'Ver',
  manage: 'Gerenciar',
  export: 'Exportar',
  publish: 'Publicar',
  request: 'Solicitar',
};

interface StaffForm {
  id?: string;
  name: string;
  email: string;
  rolePackage: string;
  accessActive: boolean;
  permissionGrants: PlatformPermission[];
  permissionDenials: PlatformPermission[];
  isPlatformRoot: boolean;
  reason: string;
}

function emptyForm(packages: PlatformRolePackage[]): StaffForm {
  return {
    name: '',
    email: '',
    rolePackage: packages.find((item) => item.assignable)?.key ?? '',
    accessActive: true,
    permissionGrants: [],
    permissionDenials: [],
    isPlatformRoot: false,
    reason: '',
  };
}

function formFromStaff(staff: PlatformStaffUser): StaffForm {
  return {
    id: staff.id,
    name: staff.name,
    email: staff.email,
    rolePackage: staff.rolePackage,
    accessActive: staff.accessActive,
    permissionGrants: staff.permissionGrants,
    permissionDenials: staff.permissionDenials,
    isPlatformRoot: staff.isPlatformRoot,
    reason: '',
  };
}

function effectivePermissionKeys(form: StaffForm, selectedPackage?: PlatformRolePackage): Set<string> {
  const values = new Set((selectedPackage?.permissions ?? []).map(permissionKey));
  form.permissionDenials.forEach((item) => values.delete(permissionKey(item)));
  form.permissionGrants.forEach((item) => values.add(permissionKey(item)));
  return values;
}

export function PlatformStaffPanel({ currentUser }: { currentUser: AuthUser | null }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<StaffForm | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<PlatformStaffUser | null>(null);
  const [revokeReason, setRevokeReason] = useState('');
  const [passwordTarget, setPasswordTarget] = useState<PlatformStaffUser | null>(null);
  const [temporaryPassword, setTemporaryPassword] = useState('');
  const [passwordReason, setPasswordReason] = useState('');
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search);
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const canManage = canPlatform(currentUser, 'staff', 'manage');
  const currentIsRoot = isPlatformRoot(currentUser);
  const packagesQuery = useQuery({
    queryKey: ['platform', 'staff', 'packages'],
    queryFn: fetchPlatformStaffPackages,
    staleTime: 5 * 60_000,
  });
  const staffQuery = useQuery({
    queryKey: ['platform', 'staff', 'page', page, deferredSearch],
    queryFn: () => fetchPlatformStaffPage({
      search: deferredSearch,
      limit: pageSize,
      offset: page * pageSize,
    }),
    staleTime: 30_000,
  });
  const packages = packagesQuery.data ?? EMPTY_PLATFORM_ROLE_PACKAGES;
  const assignablePackages = packages.filter((item) => item.assignable);

  useEffect(() => {
    if (open && form === null && packages.length) setForm(emptyForm(packages));
  }, [form, open, packages]);

  const allPermissions = useMemo(() => {
    const byKey = new Map<string, PlatformPermission>();
    packages.forEach((rolePackage) => rolePackage.permissions.forEach((permission) => {
      byKey.set(permissionKey(permission), permission);
    }));
    return Array.from(byKey.values()).sort((a, b) => (
      `${moduleLabels[a.module] ?? a.module}:${a.action}`
        .localeCompare(`${moduleLabels[b.module] ?? b.module}:${b.action}`, 'pt-BR')
    ));
  }, [packages]);

  const mutation = useMutation({
    mutationFn: async (value: StaffForm) => {
      if (value.id) {
        return updatePlatformStaff(value.id, {
          reason: value.reason.trim(),
          name: value.name.trim(),
          rolePackage: value.rolePackage,
          accessActive: value.accessActive,
          permissionGrants: value.permissionGrants,
          permissionDenials: value.permissionDenials,
        });
      }
      return createPlatformStaff({
        name: value.name.trim(),
        email: value.email.trim(),
        rolePackage: value.rolePackage,
        permissionGrants: value.permissionGrants,
        permissionDenials: value.permissionDenials,
      });
    },
    onSuccess: (staff) => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'staff'] });
      setOpen(false);
      setForm(null);
      toast({
        title: staff.requiresPasswordSetup ? 'Funcionário criado e ativação preparada.' : 'Acesso da equipe atualizado.',
        description: staff.requiresPasswordSetup
          ? 'O usuário deverá definir a senha pelo fluxo de recuperação antes do primeiro acesso.'
          : undefined,
      });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Não foi possível salvar o acesso', description: error.message });
    },
  });
  const revokeSessionsMutation = useMutation({
    mutationFn: ({ userId, reason }: { userId: string; reason: string }) => revokePlatformStaffSessions(userId, reason),
    onSuccess: ({ revokedCount }) => {
      setRevokeTarget(null);
      setRevokeReason('');
      void queryClient.invalidateQueries({ queryKey: ['platform', 'staff'] });
      toast({ title: 'Sessões encerradas', description: `${revokedCount} sessão(ões) revogada(s).` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível encerrar as sessões', description: error.message }),
  });
  const temporaryPasswordMutation = useMutation({
    mutationFn: ({ userId, password, reason }: { userId: string; password: string; reason: string }) =>
      setPlatformStaffTemporaryPassword(userId, password, reason),
    onSuccess: ({ revokedSessionCount }) => {
      setPasswordTarget(null);
      setTemporaryPassword('');
      setPasswordReason('');
      void queryClient.invalidateQueries({ queryKey: ['platform', 'staff'] });
      toast({
        title: 'Senha provisória definida',
        description: `Troca obrigatória ativada e ${revokedSessionCount} sessão(ões) encerrada(s).`,
      });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível definir a senha provisória', description: error.message }),
  });

  const openCreate = () => {
    setForm(emptyForm(packages));
    setOpen(true);
  };
  const openEdit = (staff: PlatformStaffUser) => {
    setForm(formFromStaff(staff));
    setOpen(true);
  };
  const selectedPackage = packages.find((item) => item.key === form?.rolePackage);
  const effective = form ? effectivePermissionKeys(form, selectedPackage) : new Set<string>();

  const togglePermission = (permission: PlatformPermission, checked: boolean) => {
    if (!form || form.isPlatformRoot) return;
    const key = permissionKey(permission);
    const inherited = Boolean(selectedPackage?.permissions.some((item) => permissionKey(item) === key));
    setForm((current) => {
      if (!current) return current;
      const grants = current.permissionGrants.filter((item) => permissionKey(item) !== key);
      const denials = current.permissionDenials.filter((item) => permissionKey(item) !== key);
      if (checked && !inherited) grants.push(permission);
      if (!checked && inherited) denials.push(permission);
      return { ...current, permissionGrants: grants, permissionDenials: denials };
    });
  };

  if (packagesQuery.isLoading || staffQuery.isLoading) {
    return <div className="space-y-3"><Skeleton className="h-12 w-full" /><Skeleton className="h-64 w-full" /></div>;
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <UsersRound className="h-4 w-4 text-primary" /> Equipe interna do SaaS
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Acessos da operação Meu Executivo separados dos usuários pertencentes aos escritórios clientes.
          </p>
        </div>
        {canManage && (
          <Button size="sm" onClick={openCreate}><Plus className="mr-1.5 h-4 w-4" />Adicionar funcionário</Button>
        )}
      </div>

      {(packagesQuery.error || staffQuery.error) && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          Não foi possível carregar a equipe e os pacotes da plataforma.
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {assignablePackages.map((rolePackage) => (
          <div key={rolePackage.key} className="rounded-md border bg-card p-3">
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm font-medium">{rolePackage.label}</p>
              <Badge variant="outline" className="shrink-0 text-2xs tabular-nums">{rolePackage.permissions.length}</Badge>
            </div>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{rolePackage.description}</p>
          </div>
        ))}
      </div>

      <PlatformOrganizationPanel canManage={canManage} />

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <Input
          value={search}
          onChange={(event) => { setSearch(event.target.value); setPage(0); }}
          placeholder="Buscar funcionário por nome ou e-mail"
          aria-label="Buscar funcionários"
          className="sm:max-w-sm"
        />
        <p className="text-xs text-muted-foreground">
          {staffQuery.data ? `${staffQuery.data.total} funcionário(s)` : 'Total indisponível'}
        </p>
      </div>

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
            <tr>
              <th className="p-3">Funcionário</th>
              <th className="p-3">Pacote</th>
              <th className="p-3">Permissões</th>
              <th className="p-3">Status</th>
              <th className="p-3">Último acesso</th>
              <th className="p-3 text-right">Ações</th>
            </tr>
          </thead>
          <tbody>
            {(staffQuery.data?.items ?? []).map((staff) => (
              <tr key={staff.id} className="border-b last:border-0 hover:bg-muted/20">
                <td className="p-3">
                  <div className="flex items-center gap-2.5">
                    <Avatar className="h-8 w-8"><AvatarFallback>{staff.initials}</AvatarFallback></Avatar>
                    <div><p className="font-medium">{staff.name}</p><p className="text-xs text-muted-foreground">{staff.email}</p></div>
                  </div>
                </td>
                <td className="p-3"><Badge variant={staff.isPlatformRoot ? 'default' : 'outline'}>{staff.roleLabel}</Badge></td>
                <td className="p-3 text-xs text-muted-foreground">{staff.permissions.length} efetivas</td>
                <td className="p-3">
                  <Badge variant={staff.accessActive ? 'secondary' : 'destructive'}>
                    {staff.requiresPasswordSetup ? 'Aguardando senha' : staff.requiresPasswordChange ? 'Troca de senha obrigatória' : staff.accessActive ? 'Ativo' : 'Suspenso'}
                  </Badge>
                </td>
                <td className="p-3 text-xs text-muted-foreground">
                  <p>{staff.lastLoginAt ? new Date(staff.lastLoginAt).toLocaleString('pt-BR') : 'Nunca entrou'}</p>
                  <p>{staff.activeSessionCount} sessão(ões) ativa(s)</p>
                </td>
                <td className="p-3 text-right">
                  <Button
                    variant="ghost"
                    size="icon"
                    title={staff.isPlatformRoot ? 'Visualizar titular' : 'Editar acesso'}
                    disabled={!currentIsRoot && staff.rolePackage === 'platform_admin'}
                    onClick={() => openEdit(staff)}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  {currentIsRoot ? (
                    <Button variant="ghost" size="icon" title="Definir senha provisória" onClick={() => setPasswordTarget(staff)}>
                      <KeyRound className="h-4 w-4" />
                    </Button>
                  ) : null}
                  {canManage && !staff.isPlatformRoot && staff.id !== currentUser?.id && (currentIsRoot || staff.rolePackage !== 'platform_admin') ? (
                    <Button variant="ghost" size="icon" title="Encerrar sessões" onClick={() => setRevokeTarget(staff)}>
                      <LogOut className="h-4 w-4" />
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {staffQuery.data ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {staffQuery.data.total === 0
              ? 'Nenhum funcionário encontrado.'
              : `${staffQuery.data.offset + 1}–${Math.min(staffQuery.data.offset + staffQuery.data.items.length, staffQuery.data.total)} de ${staffQuery.data.total}`}
          </p>
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="outline" disabled={page === 0 || staffQuery.isFetching} onClick={() => setPage((value) => Math.max(0, value - 1))}>Anterior</Button>
            <Button type="button" size="sm" variant="outline" disabled={!staffQuery.data.hasMore || staffQuery.isFetching} onClick={() => setPage((value) => value + 1)}>Próxima</Button>
          </div>
        </div>
      ) : null}

      <Dialog open={open} onOpenChange={(next) => { if (!next && !mutation.isPending) { setOpen(false); setForm(null); } }}>
        <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base"><UserRoundCog className="h-4 w-4 text-primary" />{form?.id ? 'Acesso do funcionário' : 'Novo funcionário do SaaS'}</DialogTitle>
            <DialogDescription>O pacote define a base; as exceções abaixo valem apenas para esta identidade.</DialogDescription>
          </DialogHeader>
          {form && (
            <div className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5"><Label htmlFor="staff-name">Nome</Label><Input id="staff-name" value={form.name} disabled={form.isPlatformRoot || !canManage} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
                <div className="space-y-1.5"><Label htmlFor="staff-email">E-mail</Label><Input id="staff-email" type="email" value={form.email} disabled={Boolean(form.id) || !canManage} onChange={(event) => setForm({ ...form, email: event.target.value })} /></div>
                <div className="space-y-1.5 sm:col-span-2">
                  <Label>Pacote funcional</Label>
                  <Select
                    value={form.rolePackage}
                    disabled={form.isPlatformRoot || !canManage}
                    onValueChange={(rolePackage) => setForm({ ...form, rolePackage, permissionGrants: [], permissionDenials: [] })}
                  >
                    <SelectTrigger><SelectValue placeholder="Selecione o pacote" /></SelectTrigger>
                    <SelectContent>{(form.isPlatformRoot ? packages : assignablePackages).map((item) => <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>)}</SelectContent>
                  </Select>
                  {selectedPackage && <p className="text-xs text-muted-foreground">{selectedPackage.description}</p>}
                </div>
              </div>

              {form.id && !form.isPlatformRoot && (
                <div className="flex items-center justify-between rounded-md border p-3">
                  <div><p className="text-sm font-medium">Acesso ativo</p><p className="text-xs text-muted-foreground">Suspender bloqueia apenas a operação do SaaS.</p></div>
                  <Switch checked={form.accessActive} disabled={!canManage} onCheckedChange={(accessActive) => setForm({ ...form, accessActive })} />
                </div>
              )}

              {form.id && !form.isPlatformRoot ? (
                <div className="space-y-1.5">
                  <Label htmlFor="staff-change-reason">Motivo da alteração</Label>
                  <Textarea id="staff-change-reason" value={form.reason} maxLength={500} onChange={(event) => setForm({ ...form, reason: event.target.value })} placeholder="Explique a necessidade operacional desta mudança de autoridade." />
                </div>
              ) : null}

              <div>
                <div className="mb-3 flex items-center justify-between"><div><p className="text-sm font-medium">Permissões efetivas</p><p className="text-xs text-muted-foreground">Privilégio mínimo por função, com concessões e bloqueios auditáveis.</p></div><Badge variant="outline">{effective.size}</Badge></div>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {allPermissions.map((permission) => {
                    const key = permissionKey(permission);
                    return (
                      <label key={key} className="flex min-h-12 items-center gap-2 rounded-md border px-3 py-2 text-sm">
                        <Checkbox checked={effective.has(key)} disabled={form.isPlatformRoot || !canManage} onCheckedChange={(checked) => togglePermission(permission, checked === true)} />
                        <span className="min-w-0"><span className="block truncate font-medium">{moduleLabels[permission.module] ?? permission.module}</span><span className="text-xs text-muted-foreground">{actionLabels[permission.action] ?? permission.action}</span></span>
                      </label>
                    );
                  })}
                </div>
              </div>

              <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <Button variant="outline" onClick={() => { setOpen(false); setForm(null); }} disabled={mutation.isPending}>Fechar</Button>
                {canManage && !form.isPlatformRoot && (
                  <Button
                    onClick={() => mutation.mutate(form)}
                    disabled={mutation.isPending || !form.name.trim() || !form.email.trim() || !form.rolePackage || (Boolean(form.id) && form.reason.trim().length < 8)}
                  >
                    {mutation.isPending ? 'Salvando...' : <><Check className="mr-1.5 h-4 w-4" />Salvar acesso</>}
                  </Button>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog open={Boolean(revokeTarget)} onOpenChange={(next) => { if (!next) { setRevokeTarget(null); setRevokeReason(''); } }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Encerrar todas as sessões?</AlertDialogTitle>
            <AlertDialogDescription>
              {revokeTarget?.name} será desconectado em todos os dispositivos. A autoria histórica será preservada.
            </AlertDialogDescription>
            <div className="space-y-1.5 pt-2">
              <Label htmlFor="staff-revoke-reason">Motivo do encerramento</Label>
              <Textarea
                id="staff-revoke-reason"
                value={revokeReason}
                maxLength={500}
                onChange={(event) => setRevokeReason(event.target.value)}
                placeholder="Explique a necessidade operacional ou de segurança."
              />
            </div>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              disabled={!revokeTarget || revokeReason.trim().length < 8 || revokeSessionsMutation.isPending}
              onClick={() => {
                if (revokeTarget) revokeSessionsMutation.mutate({ userId: revokeTarget.id, reason: revokeReason.trim() });
              }}
            >Encerrar sessões</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={Boolean(passwordTarget)} onOpenChange={(next) => { if (!next) { setPasswordTarget(null); setTemporaryPassword(''); setPasswordReason(''); } }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Definir senha provisória?</AlertDialogTitle>
            <AlertDialogDescription>
              {passwordTarget?.name} terá todas as sessões encerradas e deverá trocar esta senha no primeiro acesso. A senha não será exibida novamente.
            </AlertDialogDescription>
            <div className="space-y-1.5 pt-2">
              <Label htmlFor="staff-temporary-password">Senha provisória</Label>
              <Input id="staff-temporary-password" type="password" autoComplete="new-password" value={temporaryPassword} minLength={5} maxLength={72} onChange={(event) => setTemporaryPassword(event.target.value)} />
            </div>
            <div className="space-y-1.5 pt-2">
              <Label htmlFor="staff-temporary-password-reason">Motivo auditável</Label>
              <Textarea id="staff-temporary-password-reason" value={passwordReason} maxLength={500} onChange={(event) => setPasswordReason(event.target.value)} />
            </div>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              disabled={!passwordTarget || temporaryPassword.length < 5 || passwordReason.trim().length < 8 || temporaryPasswordMutation.isPending}
              onClick={() => {
                if (passwordTarget) temporaryPasswordMutation.mutate({ userId: passwordTarget.id, password: temporaryPassword, reason: passwordReason.trim() });
              }}
            >Definir e exigir troca</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
