import { useCallback, useState } from 'react';
import { KeyRound, LogOut, ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { AuthSecurityPanel } from '@/components/admin/AuthSecurityPanel';
import { Input } from '@/components/ui/input';
import { apiFetch, ApiError } from '@/services/http';
import { passwordPolicyError } from '@/services/identity';
import { useAppStore } from '@/store/app-context';

/**
 * Deliberately rendered by the route guard, rather than as a normal
 * navigable page. Until this succeeds no protected app or Blackbox route is
 * mounted. The current password is required because the backend contract is
 * an authenticated change, including provisional credentials.
 */
export default function ForcePasswordChange() {
  const { state, logout } = useAppStore();
  const platformFirstAccess = state.session?.surface === 'platform';
  const [platformMfaReady, setPlatformMfaReady] = useState(!platformFirstAccess);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const confirmPlatformMfa = useCallback(() => setPlatformMfaReady(true), []);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    const policyError = passwordPolicyError(newPassword);
    if (!currentPassword) {
      setError('Informe a senha provisória atual.');
      return;
    }
    if (policyError) {
      setError(policyError);
      return;
    }
    if (newPassword !== confirmation) {
      setError('As senhas novas não coincidem.');
      return;
    }
    if (currentPassword === newPassword) {
      setError('A nova senha precisa ser diferente da senha provisória.');
      return;
    }

    setSubmitting(true);
    try {
      await apiFetch<{ message: string }>('/api/v1/auth/password', {
        method: 'POST',
        body: JSON.stringify({ currentPassword, newPassword }),
      });
      setCurrentPassword('');
      setNewPassword('');
      setConfirmation('');
      setSuccess(true);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : requestError instanceof Error
            ? requestError.message
            : 'Não foi possível alterar a senha. Tente novamente.',
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="grid min-h-screen place-items-center bg-background px-4 py-8 sm:px-6">
      <section
        aria-labelledby="force-password-title"
        className="w-full max-w-lg rounded-2xl border bg-card p-5 shadow-sm sm:p-8"
      >
        <div className="flex items-start gap-3">
          <div className="rounded-full bg-primary/10 p-2.5 text-primary" aria-hidden="true">
            <KeyRound className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h1 id="force-password-title" className="text-lg font-semibold sm:text-xl">
              Troque sua senha provisória
            </h1>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Por segurança, a primeira entrada exige uma senha pessoal antes de liberar o sistema.
            </p>
          </div>
        </div>

        {platformFirstAccess && !platformMfaReady ? (
          <div className="mt-6 space-y-4">
            <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-4 text-sm">
              <p className="font-medium">Primeiro, proteja o acesso à Blackbox.</p>
              <p className="mt-1 text-muted-foreground">
                Ative o código de segurança no autenticador. Depois, esta tela libera a troca da senha provisória.
              </p>
            </div>
            <AuthSecurityPanel
              canAdminister={false}
              mfaOnly
              onMfaEnabled={confirmPlatformMfa}
            />
          </div>
        ) : success ? (
          <div className="mt-6 space-y-4" role="status" aria-live="polite">
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-800 dark:text-emerald-200">
              <p className="font-medium">Senha alterada com sucesso.</p>
              <p className="mt-1">Entre novamente com a senha pessoal para iniciar uma sessão segura.</p>
            </div>
            <Button type="button" className="w-full" onClick={() => void logout()}>
              Entrar novamente
            </Button>
          </div>
        ) : (
          <form className="mt-6 space-y-4" onSubmit={submit} noValidate>
            {error && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">
                {error}
              </div>
            )}
            <div className="space-y-2">
              <label htmlFor="current-provisional-password" className="text-sm font-medium">Senha provisória atual</label>
              <Input
                id="current-provisional-password"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                disabled={submitting}
                required
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="new-account-password" className="text-sm font-medium">Nova senha pessoal</label>
              <Input
                id="new-account-password"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                disabled={submitting}
                required
                aria-describedby="password-policy"
              />
              <p id="password-policy" className="text-xs text-muted-foreground">Entre 10 e 72 bytes em UTF-8.</p>
            </div>
            <div className="space-y-2">
              <label htmlFor="confirm-account-password" className="text-sm font-medium">Confirmar nova senha</label>
              <Input
                id="confirm-account-password"
                type="password"
                autoComplete="new-password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                disabled={submitting}
                required
              />
            </div>
            <div className="flex items-start gap-2 rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
              <span>A senha nunca é exibida nem armazenada no histórico da plataforma.</span>
            </div>
            <Button type="submit" className="h-11 w-full" disabled={submitting}>
              {submitting ? 'Alterando senha…' : 'Alterar senha e continuar'}
            </Button>
          </form>
        )}

        <Button type="button" variant="ghost" className="mt-4 w-full" onClick={() => void logout()} disabled={submitting}>
          <LogOut className="mr-2 h-4 w-4" aria-hidden="true" />
          Sair
        </Button>
      </section>
    </main>
  );
}
