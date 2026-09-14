/**
 * Painel Pessoas do Centro de Comando (Sprint B1): todos os usuários de
 * todos os workspaces, com busca global e ações rápidas (bloquear/desbloquear,
 * trocar pacote de acesso).
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { useToast } from '@/hooks/use-toast';
import {
  fetchPlatformPeople,
  fetchPlatformPeopleAccessPackages,
  invitePlatformPerson,
  resendPlatformPersonInvitation,
  revokePlatformPersonInvitation,
  revokePlatformPersonSessions,
  setPlatformPersonAccessPackage,
  setPlatformPersonBlocked,
  setPlatformPersonTemporaryPassword,
  type PlatformPerson,
} from '@/services/platform';

const PEOPLE_PAGE_SIZE = 50;

export function PlatformPeoplePanel({ enabled, canManage = false }: { enabled: boolean; canManage?: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [pendingBlock, setPendingBlock] = useState<PlatformPerson | null>(null);
  const [pendingInviteRevoke, setPendingInviteRevoke] = useState<PlatformPerson | null>(null);
  const [pendingSessionRevoke, setPendingSessionRevoke] = useState<PlatformPerson | null>(null);
  const [pendingPackage, setPendingPackage] = useState<{ person: PlatformPerson; accessPackage: string } | null>(null);
  const [pendingPassword, setPendingPassword] = useState<PlatformPerson | null>(null);
  const [temporaryPassword, setTemporaryPassword] = useState('');
  const [pendingReason, setPendingReason] = useState('');
  const [showInvite, setShowInvite] = useState(false);
  const [inviteDraft, setInviteDraft] = useState({ workspaceId: '', name: '', email: '', accessPackage: '' });

  const peopleQuery = useQuery({
    queryKey: ['platform', 'people', search, page],
    queryFn: () => fetchPlatformPeople({
      search: search || undefined,
      limit: PEOPLE_PAGE_SIZE,
      offset: page * PEOPLE_PAGE_SIZE,
    }),
    staleTime: 15_000,
    enabled,
  });

  useEffect(() => {
    setPage(0);
  }, [search]);

  const packagesQuery = useQuery({
    queryKey: ['platform', 'people', 'access-packages'],
    queryFn: fetchPlatformPeopleAccessPackages,
    staleTime: 60_000,
    enabled,
  });
  useEffect(() => {
    if (!showInvite) return;
    setInviteDraft((current) => ({
      ...current,
      accessPackage: current.accessPackage || packagesQuery.data?.[0]?.key || '',
    }));
  }, [showInvite, packagesQuery.data]);

  const blockMutation = useMutation({
    mutationFn: ({ workspaceId, userId, blocked, reason }: { workspaceId: string; userId: string; blocked: boolean; reason: string }) =>
      setPlatformPersonBlocked(workspaceId, userId, blocked, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'people'] });
      toast({ title: 'Acesso atualizado.' });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Não foi possível atualizar o acesso', description: error.message });
    },
  });

  const packageMutation = useMutation({
    mutationFn: ({ workspaceId, userId, accessPackage, reason }: { workspaceId: string; userId: string; accessPackage: string; reason: string }) =>
      setPlatformPersonAccessPackage(workspaceId, userId, accessPackage, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'people'] });
      toast({ title: 'Pacote de acesso atualizado.' });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Não foi possível trocar o pacote', description: error.message });
    },
  });
  const inviteMutation = useMutation({
    mutationFn: invitePlatformPerson,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'people'] });
      setInviteDraft({ workspaceId: '', name: '', email: '', accessPackage: '' });
      setShowInvite(false);
      toast({ title: 'Convite criado e entrega registrada.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível convidar', description: error.message }),
  });
  const resendMutation = useMutation({
    mutationFn: ({ workspaceId, invitationId }: { workspaceId: string; invitationId: string }) =>
      resendPlatformPersonInvitation(workspaceId, invitationId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'people'] });
      toast({ title: 'Convite reenviado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível reenviar', description: error.message }),
  });
  const revokeInviteMutation = useMutation({
    mutationFn: ({ workspaceId, invitationId }: { workspaceId: string; invitationId: string }) =>
      revokePlatformPersonInvitation(workspaceId, invitationId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'people'] });
      toast({ title: 'Convite cancelado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível cancelar', description: error.message }),
  });
  const revokeSessionsMutation = useMutation({
    mutationFn: ({ workspaceId, userId, reason }: { workspaceId: string; userId: string; reason: string }) =>
      revokePlatformPersonSessions(workspaceId, userId, reason),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'people'] });
      toast({ title: `${result.revoked_count} sessão(ões) encerrada(s).` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível encerrar as sessões', description: error.message }),
  });
  const temporaryPasswordMutation = useMutation({
    mutationFn: ({ workspaceId, userId, password, reason }: { workspaceId: string; userId: string; password: string; reason: string }) =>
      setPlatformPersonTemporaryPassword(workspaceId, userId, password, reason),
    onSuccess: ({ revokedSessionCount }) => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'people'] });
      setPendingPassword(null);
      setTemporaryPassword('');
      setPendingReason('');
      toast({ title: 'Senha provisória definida.', description: `Troca obrigatória ativada e ${revokedSessionCount} sessão(ões) encerrada(s).` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível definir a senha provisória', description: error.message }),
  });

  const items = peopleQuery.data?.items ?? [];
  const total = peopleQuery.data?.total ?? peopleQuery.data?.count ?? 0;
  const hasMore = peopleQuery.data?.hasMore
    ?? Boolean(peopleQuery.data && peopleQuery.data.offset + peopleQuery.data.items.length < total);

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle className="text-base">Todos os usuários</CardTitle>
          <CardDescription>
            {peopleQuery.isError ? 'Total indisponível.' : `${peopleQuery.data ? total : '—'} vínculos encontrados.`} Sessões e acessos são dados reais do backend.
          </CardDescription>
        </div>
        <div className="flex flex-wrap gap-2">
          <Input placeholder="Buscar por nome ou e-mail..." value={search} onChange={(e) => setSearch(e.target.value)} className="w-64" />
          {canManage && <Button onClick={() => setShowInvite((value) => !value)}>{showInvite ? 'Fechar convite' : 'Convidar usuário'}</Button>}
        </div>
      </CardHeader>
      <CardContent>
        {showInvite && canManage && (
          <div className="mb-5 grid gap-3 rounded-lg border bg-muted/20 p-4 md:grid-cols-2">
            <div className="space-y-1.5 md:col-span-2">
              <Label>Conta</Label>
              <PlatformEntityCombobox entityType="accounts" purpose="customer_user_invitation" value={inviteDraft.workspaceId || null} onChange={(value) => setInviteDraft((current) => ({ ...current, workspaceId: value ?? '' }))} label="Conta do convite" placeholder="Pesquisar conta" allowClear />
            </div>
            <div className="space-y-1.5"><Label htmlFor="platform-invite-name">Nome</Label><Input id="platform-invite-name" value={inviteDraft.name} onChange={(event) => setInviteDraft((current) => ({ ...current, name: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="platform-invite-email">E-mail</Label><Input id="platform-invite-email" type="email" value={inviteDraft.email} onChange={(event) => setInviteDraft((current) => ({ ...current, email: event.target.value }))} /></div>
            <div className="space-y-1.5 md:col-span-2">
              <Label>Pacote de acesso</Label>
              <Select value={inviteDraft.accessPackage} onValueChange={(accessPackage) => setInviteDraft((current) => ({ ...current, accessPackage }))}>
                <SelectTrigger aria-label="Pacote do convite"><SelectValue placeholder="Selecione o pacote" /></SelectTrigger>
                <SelectContent>{(packagesQuery.data ?? []).map((item) => <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="flex justify-end md:col-span-2">
              <Button
                disabled={inviteMutation.isPending || !inviteDraft.workspaceId || inviteDraft.name.trim().length < 2 || !inviteDraft.email.includes('@') || !inviteDraft.accessPackage}
                onClick={() => inviteMutation.mutate({ ...inviteDraft, name: inviteDraft.name.trim(), email: inviteDraft.email.trim() })}
              >{inviteMutation.isPending ? 'Convidando...' : 'Enviar convite'}</Button>
            </div>
          </div>
        )}
        {peopleQuery.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : peopleQuery.isError ? (
          <div className="rounded-lg border border-destructive/30 p-8 text-center text-sm text-destructive">
            Não foi possível carregar os usuários. Nenhum total foi presumido.
            <div className="mt-3"><Button size="sm" variant="outline" onClick={() => void peopleQuery.refetch()}>Tentar novamente</Button></div>
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            {search ? 'Nenhum usuário encontrado para essa busca.' : 'Nenhum usuário cadastrado ainda.'}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nome</TableHead>
                  <TableHead>E-mail</TableHead>
                  <TableHead>Workspace</TableHead>
                  <TableHead>Papel</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Último acesso</TableHead>
                  <TableHead>Sessões</TableHead>
                  <TableHead>Pacote</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((person) => (
                  <TableRow key={`${person.workspace_id}-${person.user_id}`}>
                    <TableCell className="text-sm font-medium">{person.name}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{person.email ?? '—'}</TableCell>
                    <TableCell className="text-sm">{person.workspace_name}</TableCell>
                    <TableCell className="text-sm">{person.role}</TableCell>
                    <TableCell>
                      <Badge variant={person.membership_status === 'suspended' ? 'destructive' : 'secondary'}>
                        {person.membership_status === 'suspended' ? 'Bloqueado' : 'Ativo'}
                      </Badge>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {person.last_seen_at ? new Date(person.last_seen_at).toLocaleString('pt-BR') : 'Sem acesso registrado'}
                    </TableCell>
                    <TableCell className="text-sm tabular-nums">{person.active_session_count}</TableCell>
                    <TableCell className="min-w-52">
                      {canManage ? (
                        <Select
                          value={person.access_package ?? undefined}
                          disabled={packageMutation.isPending || packagesQuery.isLoading}
                          onValueChange={(accessPackage) => { setPendingReason(''); setPendingPackage({ person, accessPackage }); }}
                        >
                          <SelectTrigger aria-label={`Pacote de acesso de ${person.name}`}>
                            <SelectValue placeholder="Selecionar pacote" />
                          </SelectTrigger>
                          <SelectContent>
                            {(packagesQuery.data ?? []).map((item) => (
                              <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <span className="text-sm text-muted-foreground">{person.access_package ?? 'Personalizado'}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {canManage && person.invitation_status === 'pending' && person.invitation_id ? (
                        <div className="flex justify-end gap-1">
                          <Button size="sm" variant="outline" disabled={resendMutation.isPending} onClick={() => resendMutation.mutate({ workspaceId: person.workspace_id, invitationId: person.invitation_id as string })}>Reenviar</Button>
                          <Button size="sm" variant="destructive" onClick={() => setPendingInviteRevoke(person)}>Cancelar</Button>
                        </div>
                      ) : canManage ? <div className="flex justify-end gap-1">
                        <Button size="sm" variant="outline" onClick={() => { setPendingReason(''); setTemporaryPassword(''); setPendingPassword(person); }}>Senha provisória</Button>
                        {person.active_session_count > 0 && <Button size="sm" variant="outline" onClick={() => { setPendingReason(''); setPendingSessionRevoke(person); }}>Encerrar sessões</Button>}
                        <Button
                          size="sm"
                          variant={person.membership_status === 'suspended' ? 'outline' : 'destructive'}
                          disabled={blockMutation.isPending}
                          onClick={() => { setPendingReason(''); setPendingBlock(person); }}
                        >
                          {person.membership_status === 'suspended' ? 'Desbloquear' : 'Bloquear'}
                        </Button>
                      </div> : <span className="text-xs text-muted-foreground">Somente leitura</span>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            </div>
            {peopleQuery.data && (
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {total === 0
                    ? 'Sem usuários.'
                    : `${peopleQuery.data.offset + 1}–${Math.min(peopleQuery.data.offset + peopleQuery.data.items.length, total)} de ${total}`}
                </span>
                <span className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={page === 0 || peopleQuery.isFetching} onClick={() => setPage((value) => Math.max(0, value - 1))}>Anterior</Button>
                  <Button size="sm" variant="outline" disabled={!hasMore || peopleQuery.isFetching} onClick={() => setPage((value) => value + 1)}>Próxima</Button>
                </span>
              </div>
            )}
          </div>
        )}
        {pendingBlock && (
          <div role="alertdialog" aria-modal="true" className="mt-4 rounded-lg border border-destructive/40 bg-destructive/5 p-4">
            <p className="font-medium">
              {pendingBlock.membership_status === 'suspended' ? 'Desbloquear' : 'Bloquear'} {pendingBlock.name}?
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              {pendingBlock.membership_status === 'suspended'
                ? 'O usuário voltará a acessar este workspace.'
                : 'O vínculo será suspenso e as sessões ativas deste workspace serão encerradas.'}
            </p>
            <div className="mt-3 space-y-1.5"><Label htmlFor="person-block-reason">Motivo</Label><Textarea id="person-block-reason" value={pendingReason} onChange={(event) => setPendingReason(event.target.value)} /></div>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setPendingBlock(null); setPendingReason(''); }}>Cancelar</Button>
              <Button
                variant={pendingBlock.membership_status === 'suspended' ? 'default' : 'destructive'}
                size="sm"
                disabled={blockMutation.isPending || pendingReason.trim().length < 8}
                onClick={() => {
                  blockMutation.mutate({
                    workspaceId: pendingBlock.workspace_id,
                    userId: pendingBlock.user_id,
                    blocked: pendingBlock.membership_status !== 'suspended',
                    reason: pendingReason.trim(),
                  });
                  setPendingBlock(null);
                  setPendingReason('');
                }}
              >Confirmar</Button>
            </div>
          </div>
        )}
        {pendingInviteRevoke?.invitation_id && (
          <div role="alertdialog" aria-modal="true" className="mt-4 rounded-lg border border-destructive/40 bg-destructive/5 p-4">
            <p className="font-medium">Cancelar o convite de {pendingInviteRevoke.name}?</p>
            <p className="mt-1 text-sm text-muted-foreground">O link pendente será invalidado e o vínculo permanecerá suspenso para preservar o histórico.</p>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setPendingInviteRevoke(null)}>Voltar</Button>
              <Button variant="destructive" size="sm" disabled={revokeInviteMutation.isPending} onClick={() => {
                revokeInviteMutation.mutate({ workspaceId: pendingInviteRevoke.workspace_id, invitationId: pendingInviteRevoke.invitation_id as string });
                setPendingInviteRevoke(null);
              }}>Cancelar convite</Button>
            </div>
          </div>
        )}
        {pendingSessionRevoke && (
          <div role="alertdialog" aria-modal="true" className="mt-4 rounded-lg border border-destructive/40 bg-destructive/5 p-4">
            <p className="font-medium">Encerrar as sessões de {pendingSessionRevoke.name}?</p>
            <p className="mt-1 text-sm text-muted-foreground">Todos os dispositivos serão desconectados. O vínculo com a conta permanecerá ativo.</p>
            <div className="mt-3 space-y-1.5"><Label htmlFor="person-sessions-reason">Motivo</Label><Textarea id="person-sessions-reason" value={pendingReason} onChange={(event) => setPendingReason(event.target.value)} /></div>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setPendingSessionRevoke(null); setPendingReason(''); }}>Cancelar</Button>
              <Button variant="destructive" size="sm" disabled={revokeSessionsMutation.isPending || pendingReason.trim().length < 8} onClick={() => {
                revokeSessionsMutation.mutate({ workspaceId: pendingSessionRevoke.workspace_id, userId: pendingSessionRevoke.user_id, reason: pendingReason.trim() });
                setPendingSessionRevoke(null);
                setPendingReason('');
              }}>Encerrar todas</Button>
            </div>
          </div>
        )}
        {pendingPackage && (
          <div role="alertdialog" aria-modal="true" className="mt-4 rounded-lg border border-primary/30 bg-primary/5 p-4">
            <p className="font-medium">Alterar o pacote de {pendingPackage.person.name}?</p>
            <p className="mt-1 text-sm text-muted-foreground">Novo pacote: {pendingPackage.accessPackage}. A alteração de autoridade será auditada.</p>
            <div className="mt-3 space-y-1.5"><Label htmlFor="person-package-reason">Motivo</Label><Textarea id="person-package-reason" value={pendingReason} onChange={(event) => setPendingReason(event.target.value)} /></div>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setPendingPackage(null); setPendingReason(''); }}>Cancelar</Button>
              <Button size="sm" disabled={packageMutation.isPending || pendingReason.trim().length < 8} onClick={() => {
                packageMutation.mutate({
                  workspaceId: pendingPackage.person.workspace_id,
                  userId: pendingPackage.person.user_id,
                  accessPackage: pendingPackage.accessPackage,
                  reason: pendingReason.trim(),
                });
                setPendingPackage(null);
                setPendingReason('');
              }}>Confirmar pacote</Button>
            </div>
          </div>
        )}
        {pendingPassword && (
          <div role="alertdialog" aria-modal="true" className="mt-4 rounded-lg border border-amber-400/50 bg-amber-50/50 p-4">
            <p className="font-medium">Definir senha provisória para {pendingPassword.name}?</p>
            <p className="mt-1 text-sm text-muted-foreground">Todas as sessões serão encerradas. A pessoa deverá trocar a senha no primeiro acesso e ela não será exibida novamente.</p>
            <div className="mt-3 space-y-1.5"><Label htmlFor="person-temporary-password">Senha provisória</Label><Input id="person-temporary-password" type="password" autoComplete="new-password" minLength={5} maxLength={72} value={temporaryPassword} onChange={(event) => setTemporaryPassword(event.target.value)} /></div>
            <div className="mt-3 space-y-1.5"><Label htmlFor="person-temporary-password-reason">Motivo auditável</Label><Textarea id="person-temporary-password-reason" value={pendingReason} onChange={(event) => setPendingReason(event.target.value)} /></div>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => { setPendingPassword(null); setTemporaryPassword(''); setPendingReason(''); }}>Cancelar</Button>
              <Button size="sm" disabled={temporaryPasswordMutation.isPending || temporaryPassword.length < 5 || pendingReason.trim().length < 8} onClick={() => {
                temporaryPasswordMutation.mutate({ workspaceId: pendingPassword.workspace_id, userId: pendingPassword.user_id, password: temporaryPassword, reason: pendingReason.trim() });
              }}>Definir e exigir troca</Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
