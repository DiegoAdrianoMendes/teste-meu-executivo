import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, KeyRound, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { saveAdministrativeSessionToken } from '@/services/auth';
import {
  beginAdministrativeSession,
  beginMfaEnrollment,
  fetchCurrentAdministrativeSession,
  fetchMfaStatus,
  verifyAdministrativeSession,
  verifyMfaEnrollment,
  type MfaChallenge,
  type MfaVerifier,
} from '@/services/auth-security';

export const SUPPORT_STEP_UP_QUERY_KEY = ['support-access', 'step-up'] as const;

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'A confirmação de identidade não foi concluída.';
}

function verifier(value: string, recovery: boolean): MfaVerifier {
  return recovery ? { recoveryCode: value } : { code: value };
}

export function SupportStepUpPanel({
  onAdministrativeSessionChange,
}: {
  onAdministrativeSessionChange: (active: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [enrollmentChallenge, setEnrollmentChallenge] = useState<MfaChallenge | null>(null);
  const [administrativeChallenge, setAdministrativeChallenge] = useState<MfaChallenge | null>(null);
  const [verificationCode, setVerificationCode] = useState('');
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const mfaQuery = useQuery({
    queryKey: [...SUPPORT_STEP_UP_QUERY_KEY, 'mfa'],
    queryFn: fetchMfaStatus,
    retry: false,
  });
  const administrativeSessionQuery = useQuery({
    queryKey: [...SUPPORT_STEP_UP_QUERY_KEY, 'administrative-session'],
    queryFn: fetchCurrentAdministrativeSession,
    enabled: mfaQuery.data?.enabled === true,
    retry: false,
  });

  const administrativeExpiry = administrativeSessionQuery.data?.session.expiresAt ?? null;
  const administrativeExpiryMs = administrativeExpiry ? new Date(administrativeExpiry).getTime() : Number.NaN;
  const hasActiveAdministrativeSession = Boolean(
    administrativeSessionQuery.data?.active
    && !administrativeSessionQuery.data.session.revokedAt
    && Number.isFinite(administrativeExpiryMs)
    && administrativeExpiryMs > now,
  );

  useEffect(() => {
    onAdministrativeSessionChange(hasActiveAdministrativeSession);
  }, [hasActiveAdministrativeSession, onAdministrativeSessionChange]);

  useEffect(() => {
    if (!administrativeExpiry || !Number.isFinite(administrativeExpiryMs)) return undefined;
    const remaining = administrativeExpiryMs - Date.now();
    if (remaining <= 0) {
      saveAdministrativeSessionToken(null);
      setNow(Date.now());
      queryClient.setQueryData([...SUPPORT_STEP_UP_QUERY_KEY, 'administrative-session'], null);
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      saveAdministrativeSessionToken(null);
      setNow(Date.now());
      queryClient.setQueryData([...SUPPORT_STEP_UP_QUERY_KEY, 'administrative-session'], null);
    }, remaining);
    return () => window.clearTimeout(timeout);
  }, [administrativeExpiry, administrativeExpiryMs, queryClient]);

  const run = async (action: string, callback: () => Promise<void>) => {
    setPendingAction(action);
    setActionError(null);
    try {
      await callback();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setPendingAction(null);
    }
  };

  const startEnrollment = () => run('enrollment-start', async () => {
    setEnrollmentChallenge(await beginMfaEnrollment());
    setVerificationCode('');
    setUseRecoveryCode(false);
  });

  const completeEnrollment = () => {
    if (!enrollmentChallenge || !verificationCode.trim()) return Promise.resolve();
    return run('enrollment-verify', async () => {
      const completed = await verifyMfaEnrollment(
        enrollmentChallenge.challengeId,
        { code: verificationCode.trim() },
      );
      setRecoveryCodes(completed.recoveryCodes);
      setEnrollmentChallenge(null);
      setVerificationCode('');
      await queryClient.invalidateQueries({ queryKey: SUPPORT_STEP_UP_QUERY_KEY });
    });
  };

  const startAdministrativeSession = () => run('admin-start', async () => {
    setAdministrativeChallenge(await beginAdministrativeSession());
    setVerificationCode('');
    setUseRecoveryCode(false);
  });

  const completeAdministrativeSession = () => {
    if (!administrativeChallenge || !verificationCode.trim()) return Promise.resolve();
    return run('admin-verify', async () => {
      await verifyAdministrativeSession(
        administrativeChallenge.challengeId,
        verifier(verificationCode.trim(), useRecoveryCode),
      );
      setAdministrativeChallenge(null);
      setVerificationCode('');
      setUseRecoveryCode(false);
      setNow(Date.now());
      await administrativeSessionQuery.refetch();
    });
  };

  const busy = pendingAction !== null;

  return (
    <section className="border-b pb-5" aria-labelledby="support-step-up-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 id="support-step-up-heading" className="flex items-center gap-2 text-sm font-semibold">
            <KeyRound className="h-4 w-4" />Confirmação administrativa
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            O MFA e a sessão elevada são vinculados à sua sessão normal de platform admin.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={hasActiveAdministrativeSession ? 'secondary' : 'outline'}>
            {hasActiveAdministrativeSession ? 'MFA confirmado' : 'Confirmação necessária'}
          </Badge>
          <Button
            type="button"
            size="icon"
            variant="outline"
            aria-label="Atualizar estado do MFA"
            title="Atualizar estado do MFA"
            onClick={() => {
              void mfaQuery.refetch();
              void administrativeSessionQuery.refetch();
            }}
            disabled={mfaQuery.isFetching || administrativeSessionQuery.isFetching}
          >
            <RefreshCw className={`h-4 w-4 ${mfaQuery.isFetching || administrativeSessionQuery.isFetching ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {actionError && (
        <p className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive" role="alert">
          {actionError}
        </p>
      )}

      {mfaQuery.isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-xs text-muted-foreground" role="status">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />Verificando MFA...
        </div>
      ) : !mfaQuery.data?.enabled ? (
        <div className="mt-4 space-y-3">
          {!enrollmentChallenge ? (
            <Button type="button" size="sm" onClick={startEnrollment} disabled={busy}>
              {pendingAction === 'enrollment-start' && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              Habilitar MFA
            </Button>
          ) : (
            <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
              <div className="space-y-1.5">
                <Label htmlFor="support-enrollment-code">Código de verificação</Label>
                <Input
                  id="support-enrollment-code"
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  value={verificationCode}
                  onChange={(event) => setVerificationCode(event.target.value)}
                />
                {enrollmentChallenge.simulatedCode && (
                  <p className="text-xs text-muted-foreground">Código do simulador local: {enrollmentChallenge.simulatedCode}</p>
                )}
              </div>
              <Button type="button" size="sm" onClick={completeEnrollment} disabled={busy || !verificationCode.trim()}>
                {pendingAction === 'enrollment-verify' && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                Confirmar habilitação
              </Button>
            </div>
          )}
        </div>
      ) : hasActiveAdministrativeSession && administrativeExpiry ? (
        <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />Sessão administrativa válida até {formatDate(administrativeExpiry)}.
        </p>
      ) : (
        <div className="mt-4 space-y-3">
          {!administrativeChallenge ? (
            <Button type="button" size="sm" onClick={startAdministrativeSession} disabled={busy}>
              {pendingAction === 'admin-start' && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              Confirmar MFA para ativar
            </Button>
          ) : (
            <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
              <div className="space-y-2">
                <Label htmlFor="support-administrative-code">
                  {useRecoveryCode ? 'Código de recuperação' : 'Código de verificação'}
                </Label>
                <Input
                  id="support-administrative-code"
                  autoComplete="one-time-code"
                  value={verificationCode}
                  onChange={(event) => setVerificationCode(event.target.value)}
                />
                {administrativeChallenge.simulatedCode && !useRecoveryCode && (
                  <p className="text-xs text-muted-foreground">Código do simulador local: {administrativeChallenge.simulatedCode}</p>
                )}
                {administrativeChallenge.recoveryAvailable && (
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Checkbox checked={useRecoveryCode} onCheckedChange={(checked) => setUseRecoveryCode(checked === true)} />
                    Usar código de recuperação
                  </label>
                )}
              </div>
              <Button type="button" size="sm" onClick={completeAdministrativeSession} disabled={busy || !verificationCode.trim()}>
                {pendingAction === 'admin-verify'
                  ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  : <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />}
                Elevar sessão
              </Button>
            </div>
          )}
        </div>
      )}

      {recoveryCodes.length > 0 && (
        <div className="mt-4 rounded-md border bg-muted/30 p-3">
          <p className="text-xs font-medium">Códigos de recuperação gerados uma única vez</p>
          <div className="mt-2 grid gap-1 sm:grid-cols-2">
            {recoveryCodes.map((code) => <code key={code} className="text-xs">{code}</code>)}
          </div>
        </div>
      )}
    </section>
  );
}
