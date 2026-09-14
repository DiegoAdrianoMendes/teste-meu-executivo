import { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, KeyRound, Loader2, MailCheck } from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAppStore } from '@/store/app-context';
import {
  extractRefreshToken,
  extractToken,
  saveRefreshToken,
  saveSession,
  saveToken,
  saveUser,
} from '@/services/auth';
import {
  readLocalOutbox,
  createIdempotencyKey,
  resendSignupVerification,
  verifySignup,
  type LocalOutboxMessage,
} from '@/services/identity';

export default function VerifyAccount() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { login } = useAppStore();
  const deliveryId = params.get('delivery') ?? params.get('deliveryId') ?? '';
  const deliveryMode = params.get('mode') ?? 'transactional_email';
  const token = params.get('token') ?? '';
  const [code, setCode] = useState('');
  const [delivery, setDelivery] = useState<LocalOutboxMessage | null>(null);
  const isPrivilegedLocalDelivery = deliveryMode === 'privileged_local_simulation';
  const [loadingDelivery, setLoadingDelivery] = useState(Boolean(deliveryId && isPrivilegedLocalDelivery));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resendEmail, setResendEmail] = useState('');
  const [resending, setResending] = useState(false);
  const [resendAccepted, setResendAccepted] = useState(false);
  const resendKey = useRef(createIdempotencyKey());

  useEffect(() => {
    let active = true;
    if (!deliveryId || !isPrivilegedLocalDelivery) {
      setLoadingDelivery(false);
      return () => { active = false; };
    }
    readLocalOutbox(deliveryId)
      .then((message) => {
        if (!active) return;
        setDelivery(message);
        setCode(message.code ?? '');
      })
      .catch(() => {
        if (active) setDelivery(null);
      })
      .finally(() => {
        if (active) setLoadingDelivery(false);
      });
    return () => { active = false; };
  }, [deliveryId, isPrivilegedLocalDelivery]);

  const canVerify = useMemo(() => Boolean(token || (deliveryId && code.trim())), [code, deliveryId, token]);

  const handleVerify = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canVerify) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await verifySignup(token
        ? { token }
        : { deliveryId, code: code.trim() });
      const accessToken = extractToken(result);
      if (!accessToken) throw new Error('A verificação não retornou uma sessão válida.');
      saveToken(accessToken);
      saveRefreshToken(extractRefreshToken(result));
      saveUser(result.user);
      saveSession(result.session);
      login({
        id: result.user.id,
        name: result.user.name,
        email: result.user.email,
        role: result.user.role,
        initials: result.user.initials,
        isInternalStaff: result.user.isInternalStaff,
        isPlatformAdmin: result.user.isPlatformAdmin,
        platformRolePackage: result.user.platformRolePackage,
        platformAccessActive: result.user.platformAccessActive,
        platformPermissions: result.user.platformPermissions,
      }, result.session);
      navigate(result.session.activeWorkspace ? '/obrigado' : '/workspace/select', { replace: true });
    } catch (verificationError) {
      setError(verificationError instanceof Error
        ? verificationError.message
        : 'Não foi possível verificar a conta.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleResend = async () => {
    if (!resendEmail.trim()) return;
    setResending(true);
    setError(null);
    try {
      await resendSignupVerification(resendEmail.trim(), resendKey.current);
      setResendAccepted(true);
    } catch (resendError) {
      setError(resendError instanceof Error
        ? resendError.message
        : 'Não foi possível registrar o reenvio.');
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <main className="w-full max-w-md rounded-lg border bg-card p-6 shadow-sm sm:p-8">
        <DesbLogo variant="full" size={44} className="mb-7" />
        <div className="mb-6 flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10">
            <MailCheck className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold">Verificar conta</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              A identidade, o workspace e o trial permanecem pendentes até esta confirmação.
            </p>
          </div>
        </div>

        {loadingDelivery ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando entrega local...
          </div>
        ) : (
          <form className="space-y-5" onSubmit={handleVerify}>
            {delivery && (
              <div className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-950">
                <p className="flex items-center gap-2 font-medium">
                  <CheckCircle2 className="h-4 w-4" />Solicitação aceita
                </p>
                <p className="mt-1 text-xs opacity-80">
                  Entrega simulada local para {delivery.recipientHint}. Nenhuma mensagem externa foi enviada.
                </p>
              </div>
            )}

            {!token && isPrivilegedLocalDelivery && (
              <div className="space-y-1.5">
                <Label htmlFor="signup-code">Código de verificação</Label>
                <div className="relative">
                  <KeyRound className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="signup-code"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    className="pl-9 font-mono"
                    placeholder="000000"
                    maxLength={12}
                  />
                </div>
              </div>
            )}

            {!token && !isPrivilegedLocalDelivery && (
              <div className="space-y-3 rounded-md border border-primary/20 bg-primary/5 p-4 text-sm">
                <p className="font-medium text-foreground">Confira seu e-mail para ativar a conta</p>
                <p className="mt-1 text-muted-foreground">
                  A solicitação foi registrada. Use o link de verificação enviado ao endereço informado e confira também a pasta de spam.
                </p>
                <div className="space-y-1.5 pt-1">
                  <Label htmlFor="signup-resend-email">Não recebeu? Informe novamente o e-mail</Label>
                  <Input
                    id="signup-resend-email"
                    type="email"
                    autoComplete="email"
                    value={resendEmail}
                    onChange={(event) => setResendEmail(event.target.value)}
                    disabled={resending || resendAccepted}
                  />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  className="w-full"
                  onClick={() => { void handleResend(); }}
                  disabled={!resendEmail.trim() || resending || resendAccepted}
                >
                  {resending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {resendAccepted ? 'Reenvio registrado' : 'Reenviar verificação'}
                </Button>
              </div>
            )}

            {token && (
              <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">
                Link de verificação reconhecido. Confirme para ativar sua identidade.
              </p>
            )}

            {error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}

            {(token || isPrivilegedLocalDelivery) && (
              <Button className="w-full" type="submit" disabled={!canVerify || submitting}>
                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Verificar e entrar
              </Button>
            )}
            <Button asChild className="w-full" type="button" variant="ghost">
              <Link to="/login">Voltar ao login</Link>
            </Button>
          </form>
        )}
      </main>
    </div>
  );
}
