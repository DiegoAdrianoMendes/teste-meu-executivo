import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  Clock3,
  Copy,
  KeyRound,
  Loader2,
  LockKeyhole,
  MonitorSmartphone,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  Trash2,
} from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { saveAdministrativeSessionToken } from '@/services/auth';
import {
  listUserSessions,
  revokeOtherUserSessions,
  revokeUserSession,
} from '@/services/identity';
import {
  beginAdministrativeSession,
  beginMfaEnrollment,
  fetchCurrentAdministrativeSession,
  fetchMfaStatus,
  listAdministrativeSessions,
  listAuthSecurityEvents,
  revokeAdministrativeSession,
  revokeAllAdministrativeSessions,
  verifyAdministrativeSession,
  verifyMfaEnrollment,
  type MfaChallenge,
  type MfaVerifier,
} from '@/services/auth-security';

function formatDate(value?: string | null): string {
  if (!value) return 'Não informado';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

function verifier(code: string, recovery: boolean): MfaVerifier {
  return recovery ? { recoveryCode: code.trim() } : { code: code.trim() };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'A operação de segurança não foi concluída.';
}

const eventLabels: Record<string, string> = {
  mfa_enrollment_started: 'Habilitação de MFA iniciada',
  mfa_enrollment_completed: 'MFA habilitado',
  mfa_login_started: 'Login com MFA iniciado',
  mfa_login_completed: 'Login com MFA concluído',
  admin_session_started: 'Elevação administrativa iniciada',
  admin_session_created: 'Sessão administrativa criada',
  admin_session_revoked: 'Sessão administrativa revogada',
};

interface AuthSecurityPanelProps {
  canAdminister?: boolean;
  mfaOnly?: boolean;
  onMfaEnabled?: () => void;
}

export function AuthSecurityPanel({
  canAdminister = true,
  mfaOnly = false,
  onMfaEnabled,
}: AuthSecurityPanelProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [enrollmentChallenge, setEnrollmentChallenge] = useState<MfaChallenge | null>(null);
  const [enrollmentCode, setEnrollmentCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [adminChallenge, setAdminChallenge] = useState<MfaChallenge | null>(null);
  const [adminCode, setAdminCode] = useState('');
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);

  const statusQuery = useQuery({ queryKey: ['auth-security', 'mfa'], queryFn: fetchMfaStatus });
  const deviceSessionsQuery = useQuery({
    queryKey: ['auth-security', 'device-sessions'],
    queryFn: listUserSessions,
    enabled: !mfaOnly,
  });
  const sessionsQuery = useQuery({
    queryKey: ['auth-security', 'sessions'],
    queryFn: listAdministrativeSessions,
    enabled: canAdminister,
  });
  const currentSessionQuery = useQuery({
    queryKey: ['auth-security', 'current-session'],
    queryFn: fetchCurrentAdministrativeSession,
    enabled: canAdminister,
    retry: false,
  });
  const eventsQuery = useQuery({
    queryKey: ['auth-security', 'events'],
    queryFn: () => listAuthSecurityEvents(30),
    enabled: canAdminister,
  });

  const refreshSecurity = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['auth-security', 'mfa'] }),
      queryClient.invalidateQueries({ queryKey: ['auth-security', 'device-sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['auth-security', 'sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['auth-security', 'current-session'] }),
      queryClient.invalidateQueries({ queryKey: ['auth-security', 'events'] }),
    ]);
  };

  const beginEnrollmentMutation = useMutation({
    mutationFn: beginMfaEnrollment,
    onSuccess: (challenge) => {
      setEnrollmentChallenge(challenge);
      setEnrollmentCode(challenge.simulatedCode ?? '');
      setRecoveryCodes([]);
    },
    onError: (error) => toast({ variant: 'destructive', title: 'MFA não iniciado', description: errorMessage(error) }),
  });
  const verifyEnrollmentMutation = useMutation({
    mutationFn: ({ challengeId, code }: { challengeId: string; code: string }) =>
      verifyMfaEnrollment(challengeId, { code }),
    onSuccess: async (result) => {
      setRecoveryCodes(result.recoveryCodes);
      setEnrollmentChallenge(null);
      setEnrollmentCode('');
      await refreshSecurity();
      toast({ title: 'MFA habilitado.', description: 'Guarde os códigos de recuperação antes de sair.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Código não confirmado', description: errorMessage(error) }),
  });

  const beginAdminMutation = useMutation({
    mutationFn: beginAdministrativeSession,
    onSuccess: (challenge) => {
      setAdminChallenge(challenge);
      setAdminCode(challenge.simulatedCode ?? '');
      setUseRecoveryCode(false);
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Elevação não iniciada', description: errorMessage(error) }),
  });
  const verifyAdminMutation = useMutation({
    mutationFn: ({ challengeId, code, recovery }: { challengeId: string; code: string; recovery: boolean }) =>
      verifyAdministrativeSession(challengeId, verifier(code, recovery)),
    onSuccess: async (result) => {
      setAdminChallenge(null);
      setAdminCode('');
      await refreshSecurity();
      toast({
        title: 'Sessão administrativa ativa.',
        description: `Expira em ${Math.ceil(result.expiresIn / 60)} minutos.`,
      });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Elevação recusada', description: errorMessage(error) }),
  });
  const revokeMutation = useMutation({
    mutationFn: (sessionId: string) => revokeAdministrativeSession(sessionId),
    onSuccess: async (_value, sessionId) => {
      if (currentSessionQuery.data?.session.id === sessionId) saveAdministrativeSessionToken(null);
      await refreshSecurity();
      toast({ title: 'Sessão administrativa revogada.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Revogação não concluída', description: errorMessage(error) }),
  });
  const revokeAllMutation = useMutation({
    mutationFn: () => revokeAllAdministrativeSessions(),
    onSuccess: async () => {
      await refreshSecurity();
      toast({ title: 'Todas as sessões administrativas foram revogadas.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Revogação não concluída', description: errorMessage(error) }),
  });
  const revokeDeviceMutation = useMutation({
    mutationFn: (sessionId: string) => revokeUserSession(sessionId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['auth-security', 'device-sessions'] });
      toast({ title: 'Dispositivo desconectado.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Revogação não concluída', description: errorMessage(error) }),
  });
  const revokeOtherDevicesMutation = useMutation({
    mutationFn: () => revokeOtherUserSessions(),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['auth-security', 'device-sessions'] });
      toast({ title: `${result.revokedCount} outra(s) sessão(ões) revogada(s).` });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Revogação não concluída', description: errorMessage(error) }),
  });

  const copyRecoveryCodes = async () => {
    try {
      await navigator.clipboard.writeText(recoveryCodes.join('\n'));
      toast({ title: 'Códigos copiados.' });
    } catch {
      toast({ variant: 'destructive', title: 'Não foi possível copiar automaticamente.' });
    }
  };

  const status = statusQuery.data;

  useEffect(() => {
    if (status?.enabled) onMfaEnabled?.();
  }, [onMfaEnabled, status?.enabled]);
  const activeSessions = (sessionsQuery.data ?? []).filter((session) => !session.revokedAt);
  const deviceSessions = deviceSessionsQuery.data ?? [];
  const otherActiveDevices = deviceSessions.filter((session) => !session.current && !session.revokedAt);
  const busy = beginEnrollmentMutation.isPending || verifyEnrollmentMutation.isPending;
  const adminBusy = beginAdminMutation.isPending || verifyAdminMutation.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <LockKeyhole className="h-4 w-4 text-primary" />
              Identidade e sessões privilegiadas
            </CardTitle>
            <CardDescription>
              Segundo fator, elevação temporária e eventos de acesso do usuário atual.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {status && <Badge variant="outline">{status.simulated ? 'simulação local' : 'TOTP de produção'}</Badge>}
            <Badge variant={status?.enabled ? 'secondary' : 'destructive'}>
              {status?.enabled ? 'MFA ativo' : 'MFA inativo'}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {statusQuery.isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando proteção de identidade...
          </div>
        )}

        <section className="space-y-4" aria-labelledby="mfa-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 id="mfa-heading" className="flex items-center gap-2 text-sm font-semibold">
                <KeyRound className="h-4 w-4" />Autenticação em duas etapas
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {status?.enabled
                  ? `${status.recoveryCodesRemaining} código(s) de recuperação disponível(is).`
                  : 'Proteja o login e as ações administrativas com um segundo fator.'}
              </p>
            </div>
            {!statusQuery.isLoading && !status?.enabled && !enrollmentChallenge && (
              <Button
                type="button"
                onClick={() => beginEnrollmentMutation.mutate()}
                disabled={statusQuery.isLoading || busy}
              >
                {beginEnrollmentMutation.isPending
                  ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  : <ShieldCheck className="mr-1.5 h-4 w-4" />}
                Habilitar MFA
              </Button>
            )}
          </div>

          {enrollmentChallenge && (
            <div className="grid gap-3 rounded-md border bg-muted/20 p-4 sm:grid-cols-[1fr_auto] sm:items-end">
              <div className="space-y-1.5">
                {enrollmentChallenge.manualEntryKey && (
                  <div className="mb-3 space-y-2 rounded-md border bg-background p-3">
                    <p className="text-xs font-medium">Adicione Meu Executivo no seu aplicativo autenticador</p>
                    <p className="text-xs text-muted-foreground">Chave exibida somente durante esta habilitação:</p>
                    <code className="block break-all rounded bg-muted p-2 text-xs" data-testid="mfa-manual-key">
                      {enrollmentChallenge.manualEntryKey}
                    </code>
                    {enrollmentChallenge.provisioningUri && (
                      <a className="text-xs font-medium text-primary underline" href={enrollmentChallenge.provisioningUri}>
                        Abrir no autenticador deste dispositivo
                      </a>
                    )}
                  </div>
                )}
                <Label htmlFor="mfa-enrollment-code">Código de verificação</Label>
                <Input
                  id="mfa-enrollment-code"
                  value={enrollmentCode}
                  onChange={(event) => setEnrollmentCode(event.target.value)}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  className="font-mono"
                />
                <p className="text-xs text-muted-foreground">
                  {enrollmentChallenge.simulatedCode ? <>Simulador local: <code className="font-semibold">{enrollmentChallenge.simulatedCode}</code> · </> : null}
                  {enrollmentChallenge.attemptsRemaining} tentativa(s)
                </p>
              </div>
              <Button
                type="button"
                onClick={() => verifyEnrollmentMutation.mutate({
                  challengeId: enrollmentChallenge.challengeId,
                  code: enrollmentCode.trim(),
                })}
                disabled={!enrollmentCode.trim() || busy}
              >
                {verifyEnrollmentMutation.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                Confirmar habilitação
              </Button>
            </div>
          )}

          {recoveryCodes.length > 0 && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold">Códigos de recuperação, exibição única</p>
                  <p className="mt-1 text-xs opacity-80">Cada código funciona uma vez. Guarde-os fora do Meu Executivo.</p>
                </div>
                <Button type="button" size="sm" variant="outline" onClick={copyRecoveryCodes}>
                  <Copy className="mr-1.5 h-3.5 w-3.5" />Copiar códigos
                </Button>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {recoveryCodes.map((code) => <code key={code} className="rounded border bg-background px-2 py-1.5 text-xs">{code}</code>)}
              </div>
            </div>
          )}
        </section>

        {!mfaOnly && <section className="space-y-4 border-t pt-5" aria-labelledby="device-sessions-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 id="device-sessions-heading" className="flex items-center gap-2 text-sm font-semibold">
                <MonitorSmartphone className="h-4 w-4" />Dispositivos conectados
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Sessões normais persistidas da sua identidade.
              </p>
            </div>
            {otherActiveDevices.length > 0 && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button type="button" size="sm" variant="outline" disabled={revokeOtherDevicesMutation.isPending}>
                    <ShieldX className="mr-1.5 h-3.5 w-3.5" />Revogar outros
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Desconectar os outros dispositivos?</AlertDialogTitle>
                    <AlertDialogDescription>
                      A sessão atual continuará ativa. Os demais dispositivos precisarão entrar novamente.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancelar</AlertDialogCancel>
                    <AlertDialogAction onClick={() => revokeOtherDevicesMutation.mutate()}>
                      Desconectar outros
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </div>

          <div className="divide-y overflow-hidden rounded-md border">
            {deviceSessionsQuery.isLoading && (
              <p className="px-3 py-4 text-xs text-muted-foreground">Carregando dispositivos...</p>
            )}
            {deviceSessionsQuery.isError && (
              <p className="px-3 py-4 text-xs text-destructive" role="alert">
                Não foi possível carregar os dispositivos.{' '}
                <button type="button" className="underline" onClick={() => { void deviceSessionsQuery.refetch(); }}>
                  Tentar novamente
                </button>
              </p>
            )}
            {!deviceSessionsQuery.isLoading && !deviceSessionsQuery.isError && deviceSessions.length === 0 && (
              <p className="px-3 py-4 text-xs text-muted-foreground">Nenhuma sessão persistida.</p>
            )}
            {deviceSessions.map((session) => (
              <div key={session.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-3">
                <div className="min-w-0 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{session.deviceLabel}</p>
                    {session.current && <Badge variant="secondary">Atual</Badge>}
                    {session.revokedAt && <Badge variant="outline">Revogada</Badge>}
                  </div>
                  <p className="mt-1 text-muted-foreground">
                    {[session.browser, session.operatingSystem].filter(Boolean).join(' · ')}
                  </p>
                  <p className="mt-1 flex items-center gap-1 text-muted-foreground">
                    <Clock3 className="h-3 w-3" />Última atividade em {formatDate(session.lastSeenAt)}
                  </p>
                </div>
                {!session.current && !session.revokedAt && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label={`Desconectar ${session.deviceLabel}`}
                    title={`Desconectar ${session.deviceLabel}`}
                    onClick={() => revokeDeviceMutation.mutate(session.id)}
                    disabled={revokeDeviceMutation.isPending}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        </section>}

        {canAdminister && (
          <section className="space-y-4 border-t pt-5" aria-labelledby="admin-session-heading">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 id="admin-session-heading" className="flex items-center gap-2 text-sm font-semibold">
                  <ShieldCheck className="h-4 w-4" />Sessão administrativa
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {currentSessionQuery.data?.active
                    ? `Elevada até ${formatDate(currentSessionQuery.data.session.expiresAt)}.`
                    : 'Confirme o MFA antes de executar comandos privilegiados.'}
                </p>
              </div>
              {!statusQuery.isLoading && !adminChallenge && (
                <Button
                  type="button"
                  variant={currentSessionQuery.data?.active ? 'outline' : 'default'}
                  onClick={() => beginAdminMutation.mutate()}
                  disabled={!status?.enabled || adminBusy}
                >
                  {beginAdminMutation.isPending
                    ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    : <LockKeyhole className="mr-1.5 h-4 w-4" />}
                  {currentSessionQuery.data?.active ? 'Renovar elevação' : 'Elevar sessão'}
                </Button>
              )}
            </div>

            {adminChallenge && (
              <div className="grid gap-3 rounded-md border bg-muted/20 p-4 sm:grid-cols-[1fr_auto] sm:items-end">
                <div className="space-y-1.5">
                  <Label htmlFor="admin-session-code">
                    {useRecoveryCode ? 'Código de recuperação' : 'Código de verificação'}
                  </Label>
                  <Input
                    id="admin-session-code"
                    value={adminCode}
                    onChange={(event) => setAdminCode(event.target.value)}
                    className="font-mono"
                    autoComplete="one-time-code"
                    inputMode={useRecoveryCode ? 'text' : 'numeric'}
                  />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    {!useRecoveryCode && adminChallenge.simulatedCode && (
                      <span>Simulador: <code className="font-semibold">{adminChallenge.simulatedCode}</code></span>
                    )}
                    {adminChallenge.recoveryAvailable && (
                      <button
                        type="button"
                        className="font-medium text-primary hover:underline"
                        onClick={() => { setUseRecoveryCode((current) => !current); setAdminCode(''); }}
                      >
                        {useRecoveryCode ? 'Usar código temporário' : 'Usar recuperação'}
                      </button>
                    )}
                  </div>
                </div>
                <Button
                  type="button"
                  onClick={() => verifyAdminMutation.mutate({
                    challengeId: adminChallenge.challengeId,
                    code: adminCode,
                    recovery: useRecoveryCode,
                  })}
                  disabled={!adminCode.trim() || adminBusy}
                >
                  {verifyAdminMutation.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                  Confirmar elevação
                </Button>
              </div>
            )}

            <div className="overflow-hidden rounded-md border">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/30 px-3 py-2">
                <span className="text-xs font-medium">Sessões ativas ({activeSessions.length})</span>
                {activeSessions.length > 0 && (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button type="button" size="sm" variant="ghost" disabled={revokeAllMutation.isPending}>
                        <ShieldX className="mr-1.5 h-3.5 w-3.5" />Revogar todas
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Revogar todas as sessões elevadas?</AlertDialogTitle>
                        <AlertDialogDescription>
                          As próximas ações privilegiadas exigirão uma nova confirmação MFA.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Voltar</AlertDialogCancel>
                        <AlertDialogAction onClick={() => revokeAllMutation.mutate()}>Revogar sessões</AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
              </div>
              <div className="divide-y">
                {sessionsQuery.isLoading && <p className="px-3 py-4 text-xs text-muted-foreground">Carregando sessões...</p>}
                {sessionsQuery.isError && (
                  <p className="px-3 py-4 text-xs text-destructive" role="alert">
                    Não foi possível carregar as sessões administrativas.{' '}
                    <button type="button" className="underline" onClick={() => { void sessionsQuery.refetch(); }}>
                      Tentar novamente
                    </button>
                  </p>
                )}
                {!sessionsQuery.isLoading && !sessionsQuery.isError && activeSessions.length === 0 && (
                  <p className="px-3 py-4 text-xs text-muted-foreground">Nenhuma sessão administrativa ativa.</p>
                )}
                {activeSessions.map((session) => (
                  <div key={session.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-3">
                    <div className="min-w-0 text-xs">
                      <p className="font-medium">
                        {session.mfaMethod === 'recovery' ? 'Código de recuperação' : 'Código temporário'}
                        {currentSessionQuery.data?.session.id === session.id && <Badge className="ml-2" variant="secondary">Atual</Badge>}
                      </p>
                      <p className="mt-1 flex items-center gap-1 text-muted-foreground">
                        <Clock3 className="h-3 w-3" />Expira em {formatDate(session.expiresAt)}
                      </p>
                    </div>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      aria-label="Revogar sessão administrativa"
                      title="Revogar sessão administrativa"
                      onClick={() => revokeMutation.mutate(session.id)}
                      disabled={revokeMutation.isPending}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          </section>
        )}

        {canAdminister && (
          <section className="space-y-3 border-t pt-5" aria-labelledby="identity-audit-heading">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 id="identity-audit-heading" className="text-sm font-semibold">Eventos de identidade</h3>
                <p className="mt-1 text-xs text-muted-foreground">Últimos 30 eventos do usuário atual.</p>
              </div>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label="Atualizar eventos de identidade"
                title="Atualizar eventos de identidade"
                onClick={() => eventsQuery.refetch()}
                disabled={eventsQuery.isFetching}
              >
                <RefreshCw className={`h-4 w-4 ${eventsQuery.isFetching ? 'animate-spin' : ''}`} />
              </Button>
            </div>
            <div className="divide-y overflow-hidden rounded-md border">
              {eventsQuery.isLoading && <p className="px-3 py-4 text-xs text-muted-foreground">Carregando eventos...</p>}
              {eventsQuery.isError && (
                <p className="px-3 py-4 text-xs text-destructive" role="alert">
                  Não foi possível carregar os eventos de segurança.{' '}
                  <button type="button" className="underline" onClick={() => { void eventsQuery.refetch(); }}>
                    Tentar novamente
                  </button>
                </p>
              )}
              {!eventsQuery.isLoading && !eventsQuery.isError && (eventsQuery.data ?? []).length === 0 && (
                <p className="px-3 py-4 text-xs text-muted-foreground">Nenhum evento registrado.</p>
              )}
              {(eventsQuery.data ?? []).map((event) => (
                <div key={event.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-3 text-xs">
                  <div className="flex min-w-0 items-center gap-2">
                    {event.outcome === 'success'
                      ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                      : <ShieldX className="h-4 w-4 shrink-0 text-destructive" />}
                    <div className="min-w-0">
                      <p className="truncate font-medium">{eventLabels[event.eventType] ?? event.eventType.replaceAll('_', ' ')}</p>
                      <p className="mt-0.5 text-muted-foreground">{formatDate(event.occurredAt)}</p>
                    </div>
                  </div>
                  <Badge variant={event.outcome === 'success' ? 'secondary' : 'destructive'}>{event.outcome}</Badge>
                </div>
              ))}
            </div>
          </section>
        )}
      </CardContent>
    </Card>
  );
}
