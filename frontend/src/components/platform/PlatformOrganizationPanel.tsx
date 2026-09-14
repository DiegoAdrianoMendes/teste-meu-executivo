import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MailPlus, Network, Plus, UserPlus, XCircle } from 'lucide-react';
import { useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { PlatformTeamCombobox } from '@/components/platform/PlatformTeamCombobox';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import {
  addPlatformTeamMember,
  createPlatformStaffInvitation,
  createPlatformTeam,
  fetchPlatformStaffInvitationsPage,
  fetchPlatformTeamsPage,
  revokePlatformStaffInvitation,
} from '@/services/platform-organization';
import { fetchPlatformStaffPackages } from '@/services/platform';

function slugify(value: string) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 100);
}

export function PlatformOrganizationPanel({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<'team' | 'member' | 'invite' | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [rolePackage, setRolePackage] = useState('');
  const [teamId, setTeamId] = useState('');
  const [userId, setUserId] = useState('');
  const [primary, setPrimary] = useState(false);
  const [reason, setReason] = useState('');
  const [invitationPage, setInvitationPage] = useState(0);
  const invitationPageSize = 5;
  const [teamPage, setTeamPage] = useState(0);
  const teamPageSize = 5;

  const teamPageQuery = useQuery({
    queryKey: ['platform', 'organization', 'teams', 'page', teamPage],
    queryFn: () => fetchPlatformTeamsPage({ active: true, limit: teamPageSize, offset: teamPage * teamPageSize }),
  });
  const invitations = useQuery({
    queryKey: ['platform', 'organization', 'staff-invitations', invitationPage],
    queryFn: () => fetchPlatformStaffInvitationsPage({
      status: 'pending',
      limit: invitationPageSize,
      offset: invitationPage * invitationPageSize,
    }),
  });
  const packages = useQuery({ queryKey: ['platform', 'staff', 'packages'], queryFn: fetchPlatformStaffPackages });
  const refresh = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['platform', 'organization'] }),
    queryClient.invalidateQueries({ queryKey: ['blackbox', 'work-items'] }),
  ]);
  const reset = () => {
    setDialog(null); setName(''); setEmail(''); setRolePackage(''); setTeamId('');
    setUserId(''); setPrimary(false); setReason('');
  };
  const mutation = useMutation({
    mutationFn: async () => {
      if (dialog === 'team') return createPlatformTeam({
        name: name.trim(), slug: slugify(name), description: null,
        managerUserId: userId || null, reason: reason.trim(),
      });
      if (dialog === 'member') return addPlatformTeamMember({
        teamId, userId, isPrimary: primary, reason: reason.trim(),
      });
      return createPlatformStaffInvitation({
        name: name.trim(), email: email.trim(), rolePackage,
        expiresInDays: 7, reason: reason.trim(),
      });
    },
    onSuccess: async () => { reset(); await refresh(); toast({ title: 'Operação da equipe concluída' }); },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Operação não concluída', description: error.message }),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => revokePlatformStaffInvitation(id, 'Convite revogado pelo painel da equipe'),
    onSuccess: refresh,
  });
  const pending = invitations.data?.items ?? [];
  const valid = reason.trim().length >= 8 && (
    dialog === 'team' ? name.trim().length >= 2 && slugify(name).length >= 2
      : dialog === 'member' ? Boolean(teamId && userId)
        : dialog === 'invite' ? Boolean(name.trim().length >= 2 && email.includes('@') && rolePackage)
          : false
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h3 className="flex items-center gap-2 font-semibold"><Network className="h-4 w-4 text-primary" />Organização e convites</h3><p className="text-xs text-muted-foreground">Equipes governam responsabilidade operacional; não concedem permissão.</p></div>
        {canManage ? <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => setDialog('team')}><Plus className="mr-1 h-4 w-4" />Equipe</Button><Button size="sm" variant="outline" onClick={() => setDialog('member')}><UserPlus className="mr-1 h-4 w-4" />Membro</Button><Button size="sm" onClick={() => setDialog('invite')}><MailPlus className="mr-1 h-4 w-4" />Convidar</Button></div> : null}
      </div>
      {(teamPageQuery.isError || invitations.isError) ? <Alert variant="destructive"><XCircle className="h-4 w-4" /><AlertTitle>Organização indisponível</AlertTitle><AlertDescription>A falha não foi tratada como lista vazia.</AlertDescription></Alert> : null}
      <div className="grid gap-3 lg:grid-cols-2">
        <Card><CardHeader><CardTitle className="text-sm">Equipes ativas</CardTitle></CardHeader><CardContent className="space-y-2">{teamPageQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando equipes…</p> : (teamPageQuery.data?.items ?? []).length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma equipe configurada.</p> : teamPageQuery.data?.items.map((team) => <div key={team.id} className="flex items-center justify-between rounded-md border p-3"><span><span className="block text-sm font-medium">{team.name}</span><span className="text-xs text-muted-foreground">{team.managerUserId ? 'Gestor definido' : 'Sem gestor'}</span></span><Badge variant="outline">v{team.version}</Badge></div>)}{teamPageQuery.data && teamPageQuery.data.total > teamPageSize ? <div className="flex items-center justify-between gap-2 pt-2"><span className="text-xs text-muted-foreground">{teamPageQuery.data.offset + 1}–{Math.min(teamPageQuery.data.offset + teamPageQuery.data.items.length, teamPageQuery.data.total)} de {teamPageQuery.data.total}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={teamPage === 0 || teamPageQuery.isFetching} onClick={() => setTeamPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!teamPageQuery.data.hasMore || teamPageQuery.isFetching} onClick={() => setTeamPage((value) => value + 1)}>Próxima</Button></span></div> : null}</CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm">Convites pendentes</CardTitle></CardHeader><CardContent className="space-y-2">{invitations.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando convites…</p> : pending.length === 0 ? <p className="text-sm text-muted-foreground">Nenhum convite pendente.</p> : pending.map((invite) => <div key={invite.id} className="flex items-center justify-between gap-3 rounded-md border p-3"><span className="min-w-0"><span className="block truncate text-sm font-medium">{invite.name}</span><span className="block truncate text-xs text-muted-foreground">{invite.email} · {invite.rolePackage} · expira {new Date(invite.expiresAt).toLocaleDateString('pt-BR')}</span></span>{canManage ? <Button size="sm" variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate(invite.id)}>Revogar</Button> : null}</div>)}{invitations.data && invitations.data.total > invitationPageSize ? <div className="flex items-center justify-between gap-2 pt-2"><span className="text-xs text-muted-foreground">{invitations.data.offset + 1}–{Math.min(invitations.data.offset + invitations.data.items.length, invitations.data.total)} de {invitations.data.total}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={invitationPage === 0 || invitations.isFetching} onClick={() => setInvitationPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!invitations.data.hasMore || invitations.isFetching} onClick={() => setInvitationPage((value) => value + 1)}>Próxima</Button></span></div> : null}</CardContent></Card>
      </div>
      <Dialog open={dialog !== null} onOpenChange={(open) => { if (!open && !mutation.isPending) reset(); }}><DialogContent><DialogHeader><DialogTitle>{dialog === 'team' ? 'Nova equipe' : dialog === 'member' ? 'Adicionar membro' : 'Convidar funcionário'}</DialogTitle><DialogDescription>Esta alteração é versionada e registrada na auditoria administrativa.</DialogDescription></DialogHeader><div className="space-y-4">{dialog !== 'member' ? <div><Label htmlFor="organization-name">Nome</Label><Input id="organization-name" value={name} onChange={(event) => setName(event.target.value)} /></div> : null}{dialog === 'invite' ? <><div><Label htmlFor="organization-email">E-mail</Label><Input id="organization-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></div><div><Label>Função</Label><Select value={rolePackage} onValueChange={setRolePackage}><SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{packages.data?.filter((item) => item.assignable).map((item) => <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>)}</SelectContent></Select></div></> : null}{dialog === 'team' || dialog === 'member' ? <><div>{dialog === 'member' ? <><Label>Equipe</Label><PlatformTeamCombobox className="mt-1" value={teamId || null} onChange={(value) => setTeamId(value ?? '')} label="Selecionar equipe do funcionário" placeholder="Pesquisar equipe" allowClear={false} /></> : null}</div><div><Label>{dialog === 'team' ? 'Gestor' : 'Funcionário'}</Label><PlatformEntityCombobox className="mt-1" entityType="staff" purpose={dialog === 'team' ? 'team_manager' : 'team_member'} value={userId || null} onChange={(value) => setUserId(value ?? '')} label={dialog === 'team' ? 'Selecionar gestor da equipe' : 'Selecionar funcionário da equipe'} placeholder={dialog === 'team' ? 'Gestor opcional' : 'Pesquisar funcionário'} /></div>{dialog === 'member' ? <label className="flex items-center gap-2 text-sm"><Checkbox checked={primary} onCheckedChange={(value) => setPrimary(value === true)} />Equipe principal deste funcionário</label> : null}</> : null}<div><Label htmlFor="organization-reason">Motivo</Label><Input id="organization-reason" value={reason} onChange={(event) => setReason(event.target.value)} /></div><div className="flex justify-end gap-2"><Button variant="outline" onClick={reset}>Cancelar</Button><Button disabled={!valid || mutation.isPending} onClick={() => mutation.mutate()}>Confirmar</Button></div></div></DialogContent></Dialog>
    </div>
  );
}
